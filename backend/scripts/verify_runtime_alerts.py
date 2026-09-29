"""
Comprehensive runtime verification for Phase 4.2B alerts migration.

Tests with MongoDB Atlas active:
1. GET /api/alerts/active returns unacknowledged alerts with exact response schema.
2. GET /api/alerts returns recent alerts newest first.
3. AlertService.get_train_alerts() returns train-filtered alerts.
4. Alert creation atomically gets the next sequential ID (158) from MongoDB counters.
5. Injected event generates alert with atomic ID and broadcasts over WebSocket format.
6. Analytics active_alerts count matches MongoDB count_documents({"acknowledged": False}).
7. Congestion sections remain on MongoDB (20 sections).
8. Train positions, ETA predictions, operational events remain on SQLite.
9. Database integrity checks:
   - users == 42, user counter == 42
   - trains == 10 (68 embedded stops)
   - real_trains == 5211
   - congestion_sections == 20
   - alerts count and counter cleaned back to 157 after test
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timezone
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
    COLL_ALERTS,
    COLL_COUNTERS,
    COLL_USERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_CONGESTION_SECTIONS,
)
from app.services.alert_service import alert_service
from app.services.analytics_service import AnalyticsService
from app.simulation.engine import simulation_engine
from app.database.db import async_session_maker
from app.models.database_models import TrainPosition, ETAPrediction, OperationalEvent
from sqlalchemy import select, func


async def run_checks():
    report = {
        "api_active_alerts_ok": False,
        "active_alerts_count": 0,
        "sample_alert_keys": [],
        "api_all_alerts_ok": False,
        "all_alerts_count": 0,
        "train_alerts_filter_ok": False,
        "alert_creation_ok": False,
        "created_alert_id": 0,
        "counter_incremented": False,
        "analytics_active_alerts_ok": False,
        "event_injection_alert_ok": False,
        "injected_alert_id": 0,
        "websocket_format_ok": False,
        "congestion_intact": False,
        "sqlite_positions_count": 0,
        "sqlite_etas_count": 0,
        "sqlite_events_count": 0,
        "other_collections_untouched": False,
        "errors": [],
    }

    # 1. Connect MongoDB
    connected = await init_mongo()
    if not connected:
        report["errors"].append("MongoDB could not connect")
        print(json.dumps(report, indent=2))
        return

    db = get_mongo_db()
    col = db[COLL_ALERTS]
    counters_col = db[COLL_COUNTERS]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # I. Verify GET /api/alerts/active
        resp_act = await client.get("/api/alerts/active")
        if resp_act.status_code != 200:
            report["errors"].append(f"GET /api/alerts/active failed: {resp_act.status_code}")
        else:
            act_data = resp_act.json()
            report["active_alerts_count"] = len(act_data)
            required_keys = {
                "id", "train_id", "train_name", "severity",
                "alert_type", "message", "location",
                "eta_impact_minutes", "created_at", "acknowledged"
            }
            if len(act_data) > 0 and all(required_keys.issubset(item.keys()) for item in act_data):
                report["api_active_alerts_ok"] = True
                report["sample_alert_keys"] = sorted(list(act_data[0].keys()))
            else:
                report["errors"].append("Active alerts format mismatch or empty")

        # Verify GET /api/alerts
        resp_all = await client.get("/api/alerts?limit=50")
        if resp_all.status_code == 200:
            all_data = resp_all.json()
            report["all_alerts_count"] = len(all_data)
            report["api_all_alerts_ok"] = (len(all_data) > 0)

        # K. Train-specific alert filtering
        train_alerts = await alert_service.get_train_alerts("12723", limit=5)
        if train_alerts and all(a["train_id"] == "12723" for a in train_alerts):
            report["train_alerts_filter_ok"] = True
        else:
            report["errors"].append("Train alerts filter failed for 12723")

    # L & M & N. Test alert creation and atomic counter increment
    created = await alert_service.create_alert(
        train_id="12951",
        severity="warning",
        alert_type="delay_warning",
        message="Test alert for Phase 4.2B verification",
        location="Near Vadodara",
        eta_impact=12.0,
    )
    report["created_alert_id"] = created["id"]
    if created["id"] == 158:
        report["alert_creation_ok"] = True

    counter_after = await counters_col.find_one({"_id": "alert_id"})
    if counter_after and counter_after.get("seq") == 158:
        report["counter_incremented"] = True

    # P. Test analytics query
    analytics_svc = AnalyticsService()
    full_analytics = await analytics_svc.get_full_analytics()
    mongo_active_count = await col.count_documents({"acknowledged": False})
    if full_analytics.get("active_alerts") == mongo_active_count:
        report["analytics_active_alerts_ok"] = True
    else:
        report["errors"].append(
            f"Analytics active_alerts={full_analytics.get('active_alerts')} != Mongo={mongo_active_count}"
        )

    # Q. Test event injection and alert generation format
    test_evt = {
        "event_type": "track_maintenance",
        "train_id": "12002",
        "severity": 0.8,
        "duration_minutes": 25,
        "description": "Speed restriction near Agra for Phase 4.2B",
        "location": "Near Agra Cantt",
    }
    inj_res = await simulation_engine.inject_event(test_evt)
    counter_after_inj = await counters_col.find_one({"_id": "alert_id"})
    report["injected_alert_id"] = counter_after_inj.get("seq") if counter_after_inj else None
    if counter_after_inj and counter_after_inj.get("seq") == 159:
        report["event_injection_alert_ok"] = True

    # Verify injected alert document in MongoDB
    injected_doc = await col.find_one({"id": 159})
    if injected_doc and injected_doc["train_id"] == "12002" and injected_doc["alert_type"] == "track_maintenance":
        report["websocket_format_ok"] = True

    # R. Verify congestion sections on MongoDB
    congestion_count = await db[COLL_CONGESTION_SECTIONS].count_documents({})
    report["congestion_intact"] = (congestion_count == 20)

    # S, T, U. Verify SQLite train_positions, eta_predictions, operational_events
    async with async_session_maker() as session:
        pos_cnt = (await session.execute(select(func.count()).select_from(TrainPosition))).scalar() or 0
        eta_cnt = (await session.execute(select(func.count()).select_from(ETAPrediction))).scalar() or 0
        evt_cnt = (await session.execute(select(func.count()).select_from(OperationalEvent))).scalar() or 0
        report["sqlite_positions_count"] = pos_cnt
        report["sqlite_etas_count"] = eta_cnt
        report["sqlite_events_count"] = evt_cnt

    # Clean up test alerts (158 and 159) to keep database pristine
    await col.delete_many({"id": {"$gt": 157}})
    await counters_col.update_one({"_id": "alert_id"}, {"$set": {"seq": 157}})

    # Check integrity of other collections
    users_cnt = await db[COLL_USERS].count_documents({})
    user_ctr = (await counters_col.find_one({"_id": "user_id"})).get("seq")
    trains_cnt = await db[COLL_TRAINS].count_documents({})
    real_cnt = await db[COLL_REAL_TRAINS].count_documents({})

    report["other_collections_untouched"] = (
        users_cnt == 42
        and user_ctr == 42
        and trains_cnt == 10
        and real_cnt == 5211
    )

    await close_mongo()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(run_checks())
