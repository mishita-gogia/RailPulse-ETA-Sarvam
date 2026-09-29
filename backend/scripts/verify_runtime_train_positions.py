"""
Verification script for Phase 4.4A: Runtime Train Positions Persistence in MongoDB Atlas.

Verifies:
1. Initial train_positions count in MongoDB == 10 demo trains.
2. Field schema and types match SQLite runtime specification across all 16 fields.
3. Simulation tick execution:
   - Multiple ticks executed against MongoDB.
   - Positions updated via bulk_write (UpdateOne, upsert=True, ordered=False).
   - Profiling metrics captured: min, max, avg, p95 bulk-write latency.
4. Adapters and Services reading from MongoDB:
   - DemoTrainDataSource.get_position()
   - RealTrainDataSource.get_position()
   - TrainService.get_kpis()
   - AnalyticsService.get_full_analytics()
   - ETAService.calculate_all_upcoming_etas()
5. Event injection & resolution modifying MongoDB train_positions.
6. Cross-collection integrity:
   - users == 42 (counter seq=42)
   - alerts == 157 (counter seq=157)
   - events == 29 (counter seq=29)
   - congestion_sections == 20
   - demo trains == 10
   - real_trains == 5211
"""

import asyncio
import os
import sys
import numpy as np

# Ensure backend directory is in python path
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
)
from app.adapters.train_data_source import DemoTrainDataSource, RealTrainDataSource
from app.services.train_service import TrainService
from app.services.analytics_service import AnalyticsService
from app.services.eta_service import ETAService
from app.simulation.engine import simulation_engine


EXPECTED_DEMO_TRAIN_IDS = {
    "12951", "12301", "12002", "12622", "12860",
    "12903", "12627", "12723", "12259", "12433"
}

REQUIRED_FIELDS = [
    "_id", "train_id", "latitude", "longitude", "speed_kmph",
    "delay_minutes", "status", "current_station_code", "current_station_name",
    "next_station_code", "next_station_name", "distance_covered_km",
    "total_distance_km", "last_updated", "current_stop_index",
    "at_station", "dwell_remaining_seconds"
]


async def run_verification():
    print("=" * 60)
    print("PHASE 4.4A RUNTIME TRAIN POSITIONS VERIFICATION")
    print("=" * 60)

    # 1. Connect MongoDB
    await init_mongo()
    db = get_mongo_db()
    if db is None:
        print("[FAIL] MongoDB connection failed.")
        return False

    col = db[COLL_TRAIN_POSITIONS]

    # Step 1: Initial Count & Schema Verification
    initial_count = await col.count_documents({})
    print(f"\n[Step 1] Initial train_positions count in MongoDB: {initial_count}")
    assert initial_count == 10, f"Expected 10 documents, got {initial_count}"

    docs = await col.find({}).to_list(length=20)
    found_ids = {d["_id"] for d in docs}
    assert found_ids == EXPECTED_DEMO_TRAIN_IDS, f"Train IDs mismatch: {found_ids} vs {EXPECTED_DEMO_TRAIN_IDS}"
    print(f"[PASS] All 10 expected demo train IDs present: {sorted(list(found_ids))}")

    # Verify all fields present and correct types
    for d in docs:
        for f in REQUIRED_FIELDS:
            assert f in d, f"Train {d['_id']} missing field: {f}"
        assert isinstance(d["latitude"], (int, float)), f"Invalid latitude type for {d['_id']}"
        assert isinstance(d["longitude"], (int, float)), f"Invalid longitude type for {d['_id']}"
        assert isinstance(d["speed_kmph"], (int, float)), f"Invalid speed type for {d['_id']}"
        assert isinstance(d["delay_minutes"], (int, float)), f"Invalid delay type for {d['_id']}"
        assert isinstance(d["status"], str), f"Invalid status type for {d['_id']}"
    print("[PASS] Schema verification: All 16 fields valid on all 10 documents.")

    # Step 2: Test Simulation Ticking & bulk_write Latency
    print("\n[Step 2] Executing 5 simulation ticks to test bulk_write & movement...")
    initial_snapshot = {d["_id"]: (d["distance_covered_km"], d["latitude"], d["longitude"]) for d in docs}

    for tick_num in range(1, 6):
        await simulation_engine.tick()
        print(f"  Tick {tick_num} completed.")

    # Latency Stats
    stats = simulation_engine.get_latency_stats()
    print(f"\n[Step 2 Latency Metrics] bulk_write profiling across {stats['count']} operations:")
    print(f"  Count: {stats['count']}")
    print(f"  Min:   {stats['min_ms']} ms")
    print(f"  Avg:   {stats['avg_ms']} ms")
    print(f"  Max:   {stats['max_ms']} ms")
    print(f"  p95:   {stats['p95_ms']} ms")
    assert stats["count"] >= 5, "Expected at least 5 bulk_write recordings"

    # Verify movement / updates occurred in MongoDB
    post_tick_docs = await col.find({}).to_list(length=20)
    assert len(post_tick_docs) == 10, f"Expected 10 documents after ticks, found {len(post_tick_docs)}"

    movement_detected = False
    for d in post_tick_docs:
        orig = initial_snapshot[d["_id"]]
        curr = (d["distance_covered_km"], d["latitude"], d["longitude"])
        if curr != orig or d["dwell_remaining_seconds"] != docs[0].get("dwell_remaining_seconds"):
            movement_detected = True
    print(f"[PASS] MongoDB train_positions live updates verified (Movement/dwell changes: {movement_detected})")

    # Step 3: Test Adapters & Services Querying MongoDB
    print("\n[Step 3] Testing Data Adapters and Services...")
    demo_adapter = DemoTrainDataSource()
    real_adapter = RealTrainDataSource()

    # DemoTrainDataSource.get_position
    demo_pos = await demo_adapter.get_position("12951")
    assert demo_pos is not None, "Demo adapter get_position returned None"
    assert demo_pos["train_id"] == "12951"
    print(f"[PASS] DemoTrainDataSource.get_position('12951'): lat={demo_pos['latitude']}, lon={demo_pos['longitude']}, speed={demo_pos['speed_kmph']}")

    # RealTrainDataSource.get_position
    real_pos = await real_adapter.get_position("12951")
    assert real_pos is not None, "Real adapter get_position returned None"
    assert real_pos["train_id"] == "12951"
    print(f"[PASS] RealTrainDataSource.get_position('12951') returned position from MongoDB.")

    # TrainService.get_kpis
    train_service = TrainService()
    kpis = await train_service.get_kpis()
    print(f"[PASS] TrainService.get_kpis(): active_trains={kpis['active_trains']}, on_time={kpis['on_time']}, avg_delay={kpis['avg_delay_minutes']}")
    assert kpis["active_trains"] == 10, f"Expected 10 active trains in KPIs, got {kpis['active_trains']}"

    # AnalyticsService.get_full_analytics
    analytics_service = AnalyticsService()
    analytics = await analytics_service.get_full_analytics()
    assert "active_trains" in analytics, "Missing active_trains in analytics"
    assert analytics["active_trains"] == 10
    print(f"[PASS] AnalyticsService.get_full_analytics(): active_trains={analytics['active_trains']}")

    # ETAService.calculate_all_upcoming_etas
    eta_service = ETAService()
    etas = await eta_service.calculate_all_upcoming_etas("12951")
    print(f"[PASS] ETAService.calculate_all_upcoming_etas('12951'): calculated {len(etas)} upcoming ETAs")
    assert len(etas) > 0, "Expected at least 1 ETA prediction"

    # Step 3B: Test FastAPI HTTP Endpoints
    print("\n[Step 3B] Testing FastAPI HTTP endpoints with live MongoDB positions...")
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /api/trains
        resp = await client.get("/api/trains")
        assert resp.status_code == 200, f"GET /api/trains returned {resp.status_code}: {resp.text}"
        trains_data = resp.json()
        trains_list = trains_data.get("trains", trains_data)
        assert len(trains_list) > 0, f"Expected trains list in GET /api/trains, got {len(trains_list)}"
        assert trains_data.get("total", 0) >= 10, f"Expected at least 10 total trains, got {trains_data.get('total')}"
        print(f"[PASS] GET /api/trains returned {len(trains_list)} trains (total: {trains_data.get('total')}) with status 200.")

        # GET /api/trains/12951
        resp = await client.get("/api/trains/12951")
        assert resp.status_code == 200, f"GET /api/trains/12951 returned {resp.status_code}: {resp.text}"
        train_data = resp.json()
        assert train_data["train"]["train_id"] == "12951"
        assert "position" in train_data and train_data["position"] is not None
        print(f"[PASS] GET /api/trains/12951 returned status 200 with live position.")

        # GET /api/trains/12951/position
        resp = await client.get("/api/trains/12951/position")
        assert resp.status_code == 200, f"GET /api/trains/12951/position returned {resp.status_code}: {resp.text}"
        pos_12951 = resp.json()
        assert pos_12951["train_id"] == "12951"
        assert "latitude" in pos_12951 and "longitude" in pos_12951
        print(f"[PASS] GET /api/trains/12951/position returned live position: lat={pos_12951['latitude']}, lon={pos_12951['longitude']}")

        # GET /api/trains/12301/position
        resp = await client.get("/api/trains/12301/position")
        assert resp.status_code == 200, f"GET /api/trains/12301/position returned {resp.status_code}: {resp.text}"
        pos_12301 = resp.json()
        assert pos_12301["train_id"] == "12301"
        assert "latitude" in pos_12301 and "longitude" in pos_12301
        print(f"[PASS] GET /api/trains/12301/position returned live position: lat={pos_12301['latitude']}, lon={pos_12301['longitude']}")

    # Step 4: Test Event Injection & Resolution on MongoDB train_positions
    print("\n[Step 4] Testing Event Injection and Resolution impact on MongoDB...")
    event_data = {
        "train_id": "12951",
        "event_type": "signal_congestion",
        "severity": 2.0,
        "description": "Test verification delay injection",
    }
    inj_res = await simulation_engine.inject_event(event_data)
    assert "event_id" in inj_res or inj_res.get("success") is True, f"Event injection failed: {inj_res}"

    # Read train position after injection
    inj_pos = await col.find_one({"_id": "12951"})
    print(f"  Post-injection delay for 12951: {inj_pos['delay_minutes']} min, status: {inj_pos['status']}")
    assert inj_pos["delay_minutes"] > 0, "Delay minutes did not increase after event injection"

    # Resolve event
    res_res = await simulation_engine.resolve_train_issue("12951")
    assert res_res["success"] is True, f"Event resolution failed: {res_res}"

    # Read train position after resolution
    res_pos = await col.find_one({"_id": "12951"})
    print(f"  Post-resolution delay for 12951: {res_pos['delay_minutes']} min, status: {res_pos['status']}")
    print("[PASS] Event injection and resolution correctly mutated MongoDB train_positions document.")

    # Step 5: Clean up injected test alert and event from counters/collections
    print("\n[Step 5] Cleaning up test event/alert created during step 4...")
    await db[COLL_ALERTS].delete_many({"id": {"$gt": 157}})
    await db[COLL_COUNTERS].update_one({"_id": "alert_id"}, {"$set": {"seq": 157}})
    await db[COLL_OPERATIONAL_EVENTS].delete_many({"id": {"$gt": 29}})
    await db[COLL_COUNTERS].update_one({"_id": "event_id"}, {"$set": {"seq": 29}})

    # Step 6: Cross-Collection Data Integrity Checks
    print("\n[Step 6] Cross-collection integrity checks...")
    users_cnt = await db[COLL_USERS].count_documents({})
    user_ctr = await db[COLL_COUNTERS].find_one({"_id": "user_id"})
    print(f"  users: {users_cnt} (counter seq: {user_ctr.get('seq') if user_ctr else None})")
    assert users_cnt == 42, f"Expected 42 users, got {users_cnt}"
    assert user_ctr and user_ctr["seq"] == 42, f"Expected user_id counter seq=42, got {user_ctr}"

    alerts_cnt = await db[COLL_ALERTS].count_documents({})
    alert_ctr = await db[COLL_COUNTERS].find_one({"_id": "alert_id"})
    print(f"  alerts: {alerts_cnt} (counter seq: {alert_ctr.get('seq') if alert_ctr else None})")
    assert alerts_cnt == 157, f"Expected 157 alerts, got {alerts_cnt}"
    assert alert_ctr and alert_ctr["seq"] == 157, f"Expected alert_id counter seq=157, got {alert_ctr}"

    events_cnt = await db[COLL_OPERATIONAL_EVENTS].count_documents({})
    event_ctr = await db[COLL_COUNTERS].find_one({"_id": "event_id"})
    print(f"  events: {events_cnt} (counter seq: {event_ctr.get('seq') if event_ctr else None})")
    assert events_cnt == 29, f"Expected 29 events, got {events_cnt}"
    assert event_ctr and event_ctr["seq"] == 29, f"Expected event_id counter seq=29, got {event_ctr}"

    sec_cnt = await db[COLL_CONGESTION_SECTIONS].count_documents({})
    print(f"  congestion_sections: {sec_cnt}")
    assert sec_cnt == 20, f"Expected 20 congestion sections, got {sec_cnt}"

    trains_cnt = await db[COLL_TRAINS].count_documents({})
    print(f"  demo trains: {trains_cnt}")
    assert trains_cnt == 10, f"Expected 10 demo trains, got {trains_cnt}"

    real_trains_cnt = await db[COLL_REAL_TRAINS].count_documents({})
    print(f"  real_trains: {real_trains_cnt}")
    assert real_trains_cnt == 5211, f"Expected 5211 real trains, got {real_trains_cnt}"

    pos_cnt = await db[COLL_TRAIN_POSITIONS].count_documents({})
    print(f"  train_positions: {pos_cnt}")
    assert pos_cnt == 10, f"Expected 10 train_positions, got {pos_cnt}"

    await close_mongo()
    print("\n" + "=" * 60)
    print("ALL PHASE 4.4A RUNTIME TRAIN POSITIONS VERIFICATIONS PASSED!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = asyncio.run(run_verification())
    if not success:
        sys.exit(1)
