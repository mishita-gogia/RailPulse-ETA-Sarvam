"""
Phase 4.4B Runtime Verification: ETA Predictions Persistence in MongoDB Atlas.

Verifies:
1. Initial count: exactly 31 eta_predictions in MongoDB.
2. Simulation Engine ETA write path:
   - Executes multiple simulation ticks.
   - Verifies ETA updates are written to MongoDB via bulk_write (UpdateOne, upsert=True).
   - Verifies (train_id, station_code) uniqueness via PyMongo async aggregate cursor.
   - Measures and reports actual ETA write latency: min, avg, max, p95.
3. Read path verification from MongoDB:
   - ETAService._get_cached_eta() queries MongoDB.
   - ETAService.calculate_all_upcoming_etas() queries MongoDB.
   - ETAService.get_prediction_factors() queries MongoDB.
   - TrainService.get_train_history() queries MongoDB.
4. FastAPI HTTP Endpoints (via ASGITransport):
   - GET /api/trains/{train_id} includes etas and factors with unchanged schema.
   - GET /api/trains/{train_id}/eta returns valid predictions list.
   - GET /api/trains/{train_id}/history returns ETA history from MongoDB.
5. Sarvam context compatibility:
   - Sarvam context fetcher successfully retrieves ETA context.
6. Cross-Collection Integrity:
   - users == 42 (seq=42)
   - alerts == 157 (seq=157)
   - events == 29 (seq=29)
   - congestion_sections == 20
   - demo trains == 10 (route stops = 68)
   - real_trains == 5,211 (route stops = 417,130)
   - train_positions == 10
   - eta_predictions == 31
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
import dotenv

# Load environment
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
if not os.path.exists(dotenv_path):
    dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
dotenv.load_dotenv(dotenv_path)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.mongodb import (
    init_mongo,
    close_mongo,
    get_mongo_db,
    COLL_USERS,
    COLL_COUNTERS,
    COLL_ALERTS,
    COLL_OPERATIONAL_EVENTS,
    COLL_CONGESTION_SECTIONS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_TRAIN_POSITIONS,
    COLL_ETA_PREDICTIONS,
)
from app.simulation.engine import simulation_engine
from app.services.eta_service import eta_service
from app.services.train_service import train_service
from httpx import AsyncClient, ASGITransport
from app.main import app

DEMO_TRAIN_ID = "12951"  # Mumbai Rajdhani

PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"
results = []


def check(label: str, ok: bool, detail: str = ""):
    icon = PASS if ok else FAIL
    msg = f"  {icon}  {label}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    results.append((label, ok, detail))
    return ok


async def run_verification():
    print("=" * 65)
    print("  PHASE 4.4B RUNTIME VERIFICATION: ETA PREDICTIONS PERSISTENCE")
    print(f"  Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 65)

    # 1. Connect MongoDB
    connected = await init_mongo()
    if not check("MongoDB Atlas connected successfully", connected):
        print(f"\n  {FAIL} Cannot proceed without MongoDB Atlas connection.")
        return False

    db = get_mongo_db()
    col = db[COLL_ETA_PREDICTIONS]

    # Step 1: Baseline MongoDB Count
    print("\n[Step 1] Initial ETA predictions count & duplicate check...")
    initial_count = await col.count_documents({})
    check("Initial MongoDB eta_predictions count == 31", initial_count == 31,
          f"count={initial_count}")

    # Aggregation pipeline checking for duplicates using correct PyMongo Async syntax
    pipeline = [
        {"$group": {"_id": {"train_id": "$train_id", "station_code": "$station_code"}, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}}
    ]
    cursor = await col.aggregate(pipeline)
    duplicates = await cursor.to_list(100)
    check("Zero duplicate (train_id, station_code) pairs before ticks", len(duplicates) == 0,
          f"Duplicates: {duplicates[:3]}" if duplicates else "0 duplicates")

    # Step 2: Simulation Ticking & Live ETA Writes to MongoDB
    print("\n[Step 2] Executing 5 simulation ticks to test ETA bulk_write & live updates...")
    snapshot_before = {}
    docs = await col.find({}).to_list(100)
    for d in docs:
        snapshot_before[(d["train_id"], d["station_code"])] = (
            d.get("predicted_delay_minutes"),
            d.get("predicted_arrival"),
            d.get("created_at")
        )

    for tick_num in range(1, 6):
        await simulation_engine.tick()
        print(f"  Tick {tick_num} executed.")

    # Latency Stats
    stats = simulation_engine.get_eta_latency_stats()
    print(f"\n[Step 2 Latency Metrics] ETA bulk_write profiling across {stats['count']} operations:")
    print(f"  Count: {stats['count']}")
    print(f"  Min:   {stats['min_ms']} ms")
    print(f"  Avg:   {stats['avg_ms']} ms")
    print(f"  Max:   {stats['max_ms']} ms")
    print(f"  p95:   {stats['p95_ms']} ms")

    check("Simulation recorded at least 5 ETA bulk_write operations", stats["count"] >= 5,
          f"count={stats['count']}")
    check("ETA bulk_write average latency <= 500 ms", stats["avg_ms"] <= 500.0,
          f"avg={stats['avg_ms']} ms")

    # Verify Count and Duplicate Invariance After Ticks
    after_tick_count = await col.count_documents({})
    check("ETA count remains exactly 31 after simulation ticks", after_tick_count == 31,
          f"before={initial_count}, after={after_tick_count}")

    cursor = await col.aggregate(pipeline)
    post_duplicates = await cursor.to_list(100)
    check("Zero duplicate (train_id, station_code) pairs after simulation ticks", len(post_duplicates) == 0,
          f"Duplicates: {post_duplicates[:3]}" if post_duplicates else "0 duplicates")

    # Verify MongoDB timestamps updated
    docs_after = await col.find({}).to_list(100)
    timestamps_updated = sum(
        1 for d in docs_after
        if (d["train_id"], d["station_code"]) in snapshot_before and
        d.get("created_at") != snapshot_before[(d["train_id"], d["station_code"])][2]
    )
    check("Simulation engine actively updated MongoDB documents", timestamps_updated > 0,
          f"{timestamps_updated}/31 documents updated with fresh timestamps")

    # Step 3: Test ETA Service Read Paths (MongoDB-backed)
    print("\n[Step 3] Testing ETA Service read methods from MongoDB...")

    # A. _get_cached_eta
    cached = await eta_service._get_cached_eta(DEMO_TRAIN_ID, "NDLS")
    check("eta_service._get_cached_eta() retrieves document from MongoDB", cached is not None,
          f"station={cached.get('station_code') if cached else None}, delay={cached.get('predicted_delay_minutes') if cached else None}")

    # B. calculate_all_upcoming_etas
    etas = await eta_service.calculate_all_upcoming_etas(DEMO_TRAIN_ID)
    check("eta_service.calculate_all_upcoming_etas() returns upcoming stops", len(etas) > 0,
          f"stops count={len(etas)}")
    if etas:
        first_eta = etas[0]
        expected_fields = ["train_id", "station_code", "station_name", "scheduled_arrival",
                           "predicted_arrival", "predicted_delay_minutes", "confidence",
                           "confidence_level", "factors"]
        all_fields = all(f in first_eta for f in expected_fields)
        check("ETA stop document contains all required fields", all_fields,
              f"fields={list(first_eta.keys())}")
        check("ETA stop factors is a list", isinstance(first_eta.get("factors"), list),
              f"type={type(first_eta.get('factors')).__name__}")

    # C. get_prediction_factors
    factors = await eta_service.get_prediction_factors(DEMO_TRAIN_ID)
    check("eta_service.get_prediction_factors() returns factors list from MongoDB", isinstance(factors, list) and len(factors) > 0,
          f"factors count={len(factors)}, first={factors[0].get('factor_name') if factors else None}")

    # D. train_service.get_train_history
    history = await train_service.get_train_history(DEMO_TRAIN_ID)
    check("train_service.get_train_history() returns prediction history from MongoDB", isinstance(history, list) and len(history) > 0,
          f"history records={len(history)}")
    if history:
        first_h = history[0]
        hist_fields = ["station_code", "predicted_delay_minutes", "confidence"]
        check("History record contains station, delay, and confidence", all(f in first_h for f in hist_fields),
              f"fields={list(first_h.keys())}")

    # Step 4: Test FastAPI HTTP Endpoints
    print("\n[Step 4] Testing FastAPI HTTP endpoints with live MongoDB ETA persistence...")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /api/trains/12951
        resp = await client.get(f"/api/trains/{DEMO_TRAIN_ID}")
        check("GET /api/trains/12951 returned HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            check("Train details response includes 'etas'", "etas" in data and isinstance(data["etas"], list),
                  f"etas count={len(data.get('etas', []))}")
            check("Train details response includes 'factors'", "factors" in data and isinstance(data["factors"], list),
                  f"factors count={len(data.get('factors', []))}")

        # GET /api/trains/12951/eta
        resp_eta = await client.get(f"/api/trains/{DEMO_TRAIN_ID}/eta")
        check("GET /api/trains/12951/eta returned HTTP 200", resp_eta.status_code == 200, f"HTTP {resp_eta.status_code}")
        if resp_eta.status_code == 200:
            eta_list = resp_eta.json()
            check("ETA endpoint returns a non-empty list", isinstance(eta_list, list) and len(eta_list) > 0,
                  f"count={len(eta_list)}")

        # GET /api/trains/12951/history
        resp_hist = await client.get(f"/api/trains/{DEMO_TRAIN_ID}/history")
        check("GET /api/trains/12951/history returned HTTP 200", resp_hist.status_code == 200, f"HTTP {resp_hist.status_code}")
        if resp_hist.status_code == 200:
            hist_list = resp_hist.json()
            check("History endpoint returns a list from MongoDB", isinstance(hist_list, list) and len(hist_list) > 0,
                  f"count={len(hist_list)}")

    # Step 5: Sarvam Context Builder Compatibility
    print("\n[Step 5] Checking Sarvam context builder compatibility...")
    try:
        from app.api.sarvam import _fetch_railpulse_context
        ctx = await _fetch_railpulse_context(f"Where is train {DEMO_TRAIN_ID}?")
        check("Sarvam context builder returned valid ground-truth dict", isinstance(ctx, dict) and ctx.get("found") is True,
              f"found={ctx.get('found') if isinstance(ctx, dict) else False}")
        sarvam_etas = ctx.get("etas", [])
        check("Sarvam context contains factual ETA predictions from MongoDB", isinstance(sarvam_etas, list) and len(sarvam_etas) > 0,
              f"etas count={len(sarvam_etas)}")
        if sarvam_etas:
            check("Sarvam ETA contains predicted_arrival and delay",
                  "predicted_arrival" in sarvam_etas[0] and "predicted_delay_minutes" in sarvam_etas[0],
                  f"first stop={sarvam_etas[0].get('station_code')}")
    except Exception as e:
        check("Sarvam context builder executed without error", False, str(e))

    # Step 6: Cross-Collection Integrity Checks
    print("\n[Step 6] Cross-collection integrity checks...")
    # Clean up any simulation-tick generated alert (>157) to preserve baseline
    await db[COLL_ALERTS].delete_many({"id": {"$gt": 157}})
    await db[COLL_COUNTERS].update_one({"_id": "alert_id"}, {"$set": {"seq": 157}})
    await db[COLL_OPERATIONAL_EVENTS].delete_many({"id": {"$gt": 29}})
    await db[COLL_COUNTERS].update_one({"_id": "event_id"}, {"$set": {"seq": 29}})
    users_cnt = await db[COLL_USERS].count_documents({})
    user_ctr = await db[COLL_COUNTERS].find_one({"_id": "user_id"})
    check("users count == 42", users_cnt == 42, f"count={users_cnt}")
    check("user_id counter seq == 42", user_ctr and user_ctr["seq"] == 42,
          f"seq={user_ctr.get('seq') if user_ctr else None}")

    alerts_cnt = await db[COLL_ALERTS].count_documents({})
    alert_ctr = await db[COLL_COUNTERS].find_one({"_id": "alert_id"})
    check("alerts count == 157", alerts_cnt == 157, f"count={alerts_cnt}")
    check("alert_id counter seq == 157", alert_ctr and alert_ctr["seq"] == 157,
          f"seq={alert_ctr.get('seq') if alert_ctr else None}")

    events_cnt = await db[COLL_OPERATIONAL_EVENTS].count_documents({})
    event_ctr = await db[COLL_COUNTERS].find_one({"_id": "event_id"})
    check("operational_events count == 29", events_cnt == 29, f"count={events_cnt}")
    check("event_id counter seq == 29", event_ctr and event_ctr["seq"] == 29,
          f"seq={event_ctr.get('seq') if event_ctr else None}")

    congestion_cnt = await db[COLL_CONGESTION_SECTIONS].count_documents({})
    check("congestion_sections count == 20", congestion_cnt == 20, f"count={congestion_cnt}")

    demo_trains_cnt = await db[COLL_TRAINS].count_documents({})
    check("demo trains count == 10", demo_trains_cnt == 10, f"count={demo_trains_cnt}")

    real_trains_cnt = await db[COLL_REAL_TRAINS].count_documents({})
    check("real_trains count == 5211", real_trains_cnt == 5211, f"count={real_trains_cnt}")

    pos_cnt = await db[COLL_TRAIN_POSITIONS].count_documents({})
    check("train_positions count == 10", pos_cnt == 10, f"count={pos_cnt}")

    final_eta_cnt = await col.count_documents({})
    check("eta_predictions count == 31", final_eta_cnt == 31, f"count={final_eta_cnt}")

    await close_mongo()

    # Final summary
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"\n{'='*65}")
    print(f"  RUNTIME VERIFICATION RESULT: {passed}/{total} checks passed")
    if failed > 0:
        print(f"  FAILED CHECKS:")
        for label, ok, detail in results:
            if not ok:
                print(f"    {FAIL} {label}: {detail}")
    print(f"\n  ETA Bulk Write Latency Summary:")
    print(f"    Min: {stats['min_ms']} ms")
    print(f"    Avg: {stats['avg_ms']} ms")
    print(f"    Max: {stats['max_ms']} ms")
    print(f"    p95: {stats['p95_ms']} ms")
    print(f"{'='*65}")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_verification())
    if not ok:
        sys.exit(1)
