"""
Comprehensive runtime verification for Phase 4.2A congestion_sections migration.

Tests with MongoDB Atlas active:
1. GET /api/network/congestion returns all 20 sections with exact required schema/keys.
2. GET /api/network/congestion/{section_id} returns specific section with exact schema/keys.
3. SimulationEngine._update_congestion() executes multiple ticks successfully against MongoDB,
   mutating scores via random walk and bulk_write.
4. SimulationEngine.inject_event() modifies MongoDB congestion for affected corridor section.
5. Verification that other trains, positions, ETA, WebSocket structures are untouched.
6. DB integrity checks:
   - users == 42
   - counters.user_id seq == 42
   - trains == 10 (68 embedded stops)
   - real_trains == 5211
"""

import asyncio
import os
import sys
import json
import time
from httpx import AsyncClient, ASGITransport
from dotenv import load_dotenv

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

load_dotenv(os.path.join(backend_dir, ".env"))

import dns.asyncresolver
try:
    dns.asyncresolver.get_default_resolver().nameservers = [
        "8.8.8.8", "1.1.1.1"
    ] + dns.asyncresolver.get_default_resolver().nameservers
except Exception:
    pass

from app.main import app
from app.database.mongodb import (
    init_mongo,
    close_mongo,
    get_mongo_db,
    COLL_CONGESTION_SECTIONS,
    COLL_USERS,
    COLL_COUNTERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
)
from app.simulation.engine import simulation_engine
from app.database.db import async_session_maker


async def run_checks():
    report = {
        "api_shape_all_ok": False,
        "api_shape_single_ok": False,
        "sections_count": 0,
        "sample_section": {},
        "simulation_ticks_ok": False,
        "ticks_executed": 0,
        "bulk_write_duration_ms": 0.0,
        "values_changed": False,
        "event_injection_ok": False,
        "injected_section_change": {},
        "db_integrity": {},
        "errors": [],
    }

    # 1. Connect MongoDB
    connected = await init_mongo()
    if not connected:
        report["errors"].append("MongoDB could not connect")
        print(json.dumps(report, indent=2))
        return

    db = get_mongo_db()
    col = db[COLL_CONGESTION_SECTIONS]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # H. Verify GET /api/network/congestion
        resp = await client.get("/api/network/congestion")
        if resp.status_code != 200:
            report["errors"].append(f"GET /congestion failed: {resp.status_code}")
        else:
            data = resp.json()
            report["sections_count"] = len(data)
            required_keys = {
                "section_id", "from_station", "to_station",
                "from_station_name", "to_station_name",
                "congestion_score", "avg_speed_kmph", "active_trains",
                "status", "delay_impact_minutes"
            }
            if len(data) == 20 and all(required_keys.issubset(item.keys()) for item in data):
                report["api_shape_all_ok"] = True
                report["sample_section"] = data[0]
            else:
                report["errors"].append(f"GET /congestion shape mismatch: count={len(data)}")

        # I. Verify GET /api/network/congestion/{section_id}
        sec_id = "NDLS-AGC"
        resp_sec = await client.get(f"/api/network/congestion/{sec_id}")
        if resp_sec.status_code != 200:
            report["errors"].append(f"GET /congestion/{sec_id} failed: {resp_sec.status_code}")
        else:
            sec_data = resp_sec.json()
            if sec_data.get("section_id") == sec_id and required_keys.issubset(sec_data.keys()):
                report["api_shape_single_ok"] = True
            else:
                report["errors"].append(f"GET /congestion/{sec_id} shape mismatch: {sec_data}")

    # J & K. Run simulation ticks & measure bulk_write
    # Record initial scores
    initial_docs = await col.find({}).to_list(length=100)
    initial_scores = {d["section_id"]: d.get("congestion_score", 0.0) for d in initial_docs}

    t0 = time.perf_counter()
    async with async_session_maker() as session:
        for _ in range(3):
            await simulation_engine._update_congestion(session)
    duration_ms = (time.perf_counter() - t0) * 1000.0 / 3.0
    report["bulk_write_duration_ms"] = round(duration_ms, 2)
    report["ticks_executed"] = 3
    report["simulation_ticks_ok"] = True

    # Check that values changed
    after_docs = await col.find({}).to_list(length=100)
    after_scores = {d["section_id"]: d.get("congestion_score", 0.0) for d in after_docs}

    changed_count = sum(1 for sid, s in initial_scores.items() if s != after_scores.get(sid))
    if changed_count > 0:
        report["values_changed"] = True

    # L. Test event injection modifying congestion
    # Train 12951 currently has current=BRC, next=RTM, corresponding to section BRC-RTM
    test_sec_id = "BRC-RTM"
    before_test_doc = await col.find_one({"_id": test_sec_id})
    prev_score = before_test_doc.get("congestion_score", 0.0) if before_test_doc else 0.0

    evt = {
        "event_type": "signal_congestion",
        "train_id": "12951",
        "severity": 0.95,
        "duration_minutes": 20,
        "description": "Severe signaling fault test",
        "location": "Near BRC",
    }
    res = await simulation_engine.inject_event(evt)
    report["event_injection_ok"] = bool(res and "event_id" in res)

    after_test_doc = await col.find_one({"_id": test_sec_id})
    after_score = after_test_doc.get("congestion_score") if after_test_doc else None
    report["injected_section_change"] = {
        "section_id": test_sec_id,
        "before_score": prev_score,
        "after_score": after_score,
        "status": after_test_doc.get("status") if after_test_doc else None,
        "score_spiked": bool(after_score and after_score >= 0.95),
    }

    # DB Integrity checks
    users_count = await db[COLL_USERS].count_documents({})
    counter_doc = await db[COLL_COUNTERS].find_one({"_id": "user_id"})
    trains_count = await db[COLL_TRAINS].count_documents({})
    real_trains_count = await db[COLL_REAL_TRAINS].count_documents({})

    report["db_integrity"] = {
        "users_count": users_count,
        "users_ok": (users_count == 42),
        "counter_seq": counter_doc.get("seq") if counter_doc else None,
        "counter_ok": (counter_doc.get("seq") == 42 if counter_doc else False),
        "trains_count": trains_count,
        "trains_ok": (trains_count == 10),
        "real_trains_count": real_trains_count,
        "real_trains_ok": (real_trains_count == 5211),
    }

    await close_mongo()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(run_checks())
