"""
Verification script for Phase 4.3 runtime operational events migration.
Tests event injection, active cache maintenance, deactivation on resolution, and expiration.
Cleans up after testing to preserve baseline 29 events and seq=29.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.mongodb import (
    init_mongo,
    close_mongo,
    get_mongo_db,
    COLL_OPERATIONAL_EVENTS,
    COLL_COUNTERS,
    COLL_ALERTS,
    COLL_USERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_CONGESTION_SECTIONS,
)
from app.simulation.engine import SimulationEngine
from app.config import settings


async def main():
    print("=== PHASE 4.3 RUNTIME OPERATIONAL EVENTS VERIFICATION ===")

    # Initialize MongoDB connection
    await init_mongo()
    db = get_mongo_db()
    if db is None:
        print("[FAIL] Could not connect to MongoDB Atlas.")
        return 1

    # 1. Baseline verification
    baseline_count = await db[COLL_OPERATIONAL_EVENTS].count_documents({})
    counter_doc = await db[COLL_COUNTERS].find_one({"_id": "event_id"})
    counter_seq = counter_doc["seq"] if counter_doc else None

    print(f"Baseline operational_events count: {baseline_count}")
    print(f"Baseline counters.event_id seq: {counter_seq}")

    assert baseline_count == 29, f"Expected 29 events, got {baseline_count}"
    assert counter_seq == 29, f"Expected counter seq=29, got {counter_seq}"

    # 2. Test SimulationEngine runtime event injection
    engine = SimulationEngine()
    test_train_id = "12002"  # Shatabdi Express
    test_event_data = {
        "train_id": test_train_id,
        "event_type": "speed_restriction",
        "severity": 0.4,
        "duration": 20,
        "location": "Mathura Junction",
        "description": "Verification speed restriction test",
    }

    print("\n--- Testing inject_event ---")
    inject_res = await engine.inject_event(test_event_data)
    injected_id = inject_res.get("event_id")
    print(f"Injected event response: {inject_res}")
    assert injected_id == 30, f"Expected injected event_id=30, got {injected_id}"

    # Check MongoDB document
    doc = await db[COLL_OPERATIONAL_EVENTS].find_one({"id": 30})
    assert doc is not None, "Injected event document with id=30 not found in MongoDB"
    assert doc["active"] is True, f"Expected active=True, got {doc.get('active')}"
    assert doc["train_id"] == test_train_id, f"Expected train_id={test_train_id}, got {doc.get('train_id')}"
    print("[PASS] MongoDB document inserted correctly with integer id=30 and active=True")

    # Check engine active events cache
    assert test_train_id in engine._active_events, f"Train {test_train_id} not in engine._active_events"
    assert "30" in engine._active_events[test_train_id], f"Event '30' not in engine._active_events[{test_train_id}]"
    print(f"[PASS] Engine in-memory cache synchronized: {engine._active_events[test_train_id]['30']}")

    # 3. Test resolve_train_issue
    print("\n--- Testing resolve_train_issue ---")
    resolve_res = await engine.resolve_train_issue(test_train_id)
    print(f"Resolve response success: {resolve_res.get('success')}")
    assert resolve_res.get("success") is True, f"Expected success=True, got {resolve_res}"

    # Verify MongoDB deactivation
    doc_resolved = await db[COLL_OPERATIONAL_EVENTS].find_one({"id": 30})
    assert doc_resolved["active"] is False, f"Expected active=False after resolution, got {doc_resolved.get('active')}"
    assert test_train_id not in engine._active_events or "30" not in engine._active_events.get(test_train_id, {}), \
        "Event '30' should have been removed from active cache"
    print("[PASS] Event 30 deactivated in MongoDB and cleared from engine cache")

    # 4. Test _expire_events
    print("\n--- Testing _expire_events ---")
    expired_id = 31
    now = datetime.now(timezone.utc)
    expired_doc = {
        "_id": expired_id,
        "id": expired_id,
        "event_type": "heavy_rain",
        "train_id": "12952",
        "location": "Surat",
        "severity": 0.5,
        "duration_minutes": 10,
        "description": "Expired event test",
        "impact_delay_minutes": 2.5,
        "active": True,
        "created_at": now - timedelta(minutes=20),
        "expires_at": now - timedelta(minutes=5),  # in the past
    }
    await db[COLL_OPERATIONAL_EVENTS].insert_one(expired_doc)
    engine._active_events["12952"] = {"31": {"event_type": "heavy_rain", "severity": 0.5}}

    # Run _expire_events
    await engine._expire_events(None)

    exp_res = await db[COLL_OPERATIONAL_EVENTS].find_one({"id": 31})
    assert exp_res["active"] is False, f"Expected active=False for expired event, got {exp_res.get('active')}"
    assert "12952" not in engine._active_events, "Expired event should be removed from cache"
    print("[PASS] Expired event correctly deactivated in MongoDB and cleared from cache")

    # 5. Clean up test records (id >= 30) and reset counter
    print("\n--- Cleaning up test records ---")
    await db[COLL_OPERATIONAL_EVENTS].delete_many({"id": {"$gt": 29}})
    # Also clean up any alerts created during inject_event / resolve_train_issue
    await db[COLL_ALERTS].delete_many({"id": {"$gt": 157}})
    await db[COLL_COUNTERS].update_one({"_id": "event_id"}, {"$set": {"seq": 29}})
    await db[COLL_COUNTERS].update_one({"_id": "alert_id"}, {"$set": {"seq": 157}})

    # 6. Final verification of database integrity
    print("\n--- Final Integrity Checks ---")
    final_events = await db[COLL_OPERATIONAL_EVENTS].count_documents({})
    final_event_counter = (await db[COLL_COUNTERS].find_one({"_id": "event_id"}))["seq"]
    final_users = await db[COLL_USERS].count_documents({})
    final_user_counter = (await db[COLL_COUNTERS].find_one({"_id": "user_id"}))["seq"]
    final_alerts = await db[COLL_ALERTS].count_documents({})
    final_alert_counter = (await db[COLL_COUNTERS].find_one({"_id": "alert_id"}))["seq"]
    final_trains = await db[COLL_TRAINS].count_documents({})
    final_real_trains = await db[COLL_REAL_TRAINS].count_documents({})
    final_congestion = await db[COLL_CONGESTION_SECTIONS].count_documents({})

    print(f"Final operational_events: {final_events} (seq: {final_event_counter})")
    print(f"Final users: {final_users} (seq: {final_user_counter})")
    print(f"Final alerts: {final_alerts} (seq: {final_alert_counter})")
    print(f"Final trains: {final_trains}")
    print(f"Final real_trains: {final_real_trains}")
    print(f"Final congestion_sections: {final_congestion}")

    assert final_events == 29, f"Expected 29 events, got {final_events}"
    assert final_event_counter == 29, f"Expected event seq=29, got {final_event_counter}"
    assert final_users == 42, f"Expected 42 users, got {final_users}"
    assert final_user_counter == 42, f"Expected user seq=42, got {final_user_counter}"
    assert final_alerts == 157, f"Expected 157 alerts, got {final_alerts}"
    assert final_alert_counter == 157, f"Expected alert seq=157, got {final_alert_counter}"
    assert final_trains == 10, f"Expected 10 trains, got {final_trains}"
    assert final_real_trains == 5211, f"Expected 5211 real trains, got {final_real_trains}"
    assert final_congestion == 20, f"Expected 20 congestion sections, got {final_congestion}"

    await close_mongo()
    print("\n[SUCCESS] Phase 4.3 runtime verification passed with 100% integrity!")
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
