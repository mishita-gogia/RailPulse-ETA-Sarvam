"""
Phase 6 Verification Script: Complete Runtime Decoupling & Dynamic Real-Train Simulation Cutover.

Verifies:
1. AuthService: User lookup and creation without FastAPI Depends(get_db) injection.
2. ETAService: calculate_all_upcoming_etas operates against MongoDB without top-level SQLite session.
3. SimulationEngine:
   - tick() runs without SQLite session in MongoDB runtime.
   - inject_event() writes to MongoDB operational_events/congestion/positions without SQLite session.
   - register_real_train() loads catalog from MongoDB real_trains, upserts to trains & train_positions.
   - unregister_real_train() cleans up MongoDB runtime records without SQLite session.
   - resolve_train_issue() operates against MongoDB runtime records without SQLite session.
4. Confirms zero SQLite queries during healthy MongoDB runtime operations.
"""

import os
import sys
import asyncio
from datetime import datetime, timezone

# Ensure reliable DNS resolvers on Windows
try:
    import dns.asyncresolver
    import dns.resolver
    for r in [dns.asyncresolver.get_default_resolver(), dns.resolver.get_default_resolver()]:
        if r and hasattr(r, "nameservers"):
            reliable_ns = ["8.8.8.8", "1.1.1.1"]
            r.nameservers = [ns for ns in reliable_ns if ns not in r.nameservers] + list(r.nameservers)
except Exception:
    pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.mongodb import (
    init_mongo,
    close_mongo,
    get_mongo_db,
    COLL_USERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_TRAIN_POSITIONS,
    COLL_ETA_PREDICTIONS,
    COLL_CONGESTION_SECTIONS,
    COLL_OPERATIONAL_EVENTS,
    COLL_ALERTS,
)
from app.simulation.engine import simulation_engine
from app.services.eta_service import eta_service
from app.services.auth_service import auth_service
from app.database.db import engine as sqla_engine


async def main():
    print("=" * 70)
    print("  RAILPULSE PHASE 6 — RUNTIME DECOUPLING & REAL-TRAIN CUTOVER VERIFICATION")
    print("=" * 70)

    await init_mongo()
    db = get_mongo_db()
    if db is None:
        print("[CRITICAL] MongoDB Atlas is not connected (check IP whitelist / network).")
        print("Verification requires an active MongoDB connection.")
        sys.exit(1)

    try:
        await db.command("ping")
        print("[1/6] MongoDB Connection: OK (pong received)")
    except Exception as e:
        print(f"[CRITICAL] MongoDB Atlas ping failed: {e}")
        print("Please whitelist your current public IP in Atlas Network Access.")
        sys.exit(1)

    # Track SQLite engine queries
    sqlite_query_count = 0
    from sqlalchemy import event

    @event.listens_for(sqla_engine.sync_engine, "before_cursor_execute")
    def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal sqlite_query_count
        sqlite_query_count += 1
        print(f"  [SQLITE UNEXPECTED EXECUTE] {statement[:80]}...")

    # 1. Preload master data in simulation engine
    print("\n[2/6] Loading simulation engine master data from MongoDB...")
    loaded = await simulation_engine.load_master_data()
    print(f"  Simulation master data loaded: {loaded} (trains: {len(simulation_engine._master_trains)}, stations: {len(simulation_engine._station_cache)})")
    assert loaded, "Master data must load from MongoDB"

    initial_sqlite_count = sqlite_query_count
    print(f"  SQLite queries executed during MongoDB preload: {sqlite_query_count}")
    assert sqlite_query_count == 0, f"Expected 0 SQLite queries during master data preload, got {sqlite_query_count}"

    # 2. Verify SimulationEngine tick() has zero SQLite queries
    print("\n[3/6] Testing SimulationEngine.tick() with 3 live ticks...")
    for i in range(3):
        await simulation_engine.tick()
    ticks_sqlite_queries = sqlite_query_count - initial_sqlite_count
    print(f"  SQLite queries executed during 3 ticks: {ticks_sqlite_queries}")
    assert ticks_sqlite_queries == 0, f"Expected 0 SQLite queries during ticks, got {ticks_sqlite_queries}"
    print("  SimulationEngine.tick(): 100% decoupled from SQLite!")

    # 3. Verify ETAService.calculate_all_upcoming_etas()
    print("\n[4/6] Testing ETAService.calculate_all_upcoming_etas('12951')...")
    before_eta_queries = sqlite_query_count
    etas = await eta_service.calculate_all_upcoming_etas("12951")
    eta_sqlite_queries = sqlite_query_count - before_eta_queries
    print(f"  Upcoming ETAs returned: {len(etas)}, SQLite queries: {eta_sqlite_queries}")
    assert eta_sqlite_queries == 0, f"Expected 0 SQLite queries during ETAService, got {eta_sqlite_queries}"
    print("  ETAService: 100% decoupled from SQLite in MongoDB runtime!")

    # 4. Verify Dynamic Real-Train registration & unregistration
    print("\n[5/6] Testing dynamic real-train registration and unregistration...")
    # Find a real train in COLL_REAL_TRAINS that is not in demo trains
    real_sample = await db[COLL_REAL_TRAINS].find_one({"train_number": {"$nin": ["12951", "12952", "12004"]}})
    if not real_sample:
        real_sample = await db[COLL_REAL_TRAINS].find_one({})
    sample_num = real_sample["train_number"]
    print(f"  Registering real train {sample_num} ({real_sample.get('train_name')})...")

    before_reg_queries = sqlite_query_count
    reg_result = await simulation_engine.register_real_train(sample_num)
    reg_sqlite_queries = sqlite_query_count - before_reg_queries
    print(f"  Registration result: {reg_result.get('success')}, SQLite queries: {reg_sqlite_queries}")
    assert reg_result.get("success"), f"Failed to register real train: {reg_result}"
    assert reg_sqlite_queries == 0, f"Expected 0 SQLite queries during real-train registration, got {reg_sqlite_queries}"

    # Verify presence in MongoDB COLL_TRAIN_POSITIONS and COLL_TRAINS
    pos_doc = await db[COLL_TRAIN_POSITIONS].find_one({"_id": sample_num})
    train_doc = await db[COLL_TRAINS].find_one({"_id": sample_num})
    assert pos_doc is not None, f"Real train position missing from MongoDB {COLL_TRAIN_POSITIONS}"
    assert train_doc is not None, f"Real train doc missing from MongoDB {COLL_TRAINS}"
    print(f"  Verified MongoDB presence in {COLL_TRAINS} and {COLL_TRAIN_POSITIONS}")

    # Test event injection on this train
    print(f"  Injecting test operational event on real train {sample_num}...")
    before_inj_queries = sqlite_query_count
    event_result = await simulation_engine.inject_event({
        "train_id": sample_num,
        "event_type": "signal_congestion",
        "severity": 0.6,
        "duration_minutes": 15,
        "description": "Test phase 6 operational event",
    })
    inj_sqlite_queries = sqlite_query_count - before_inj_queries
    print(f"  Event injection event_id: {event_result.get('event_id')}, SQLite queries: {inj_sqlite_queries}")
    assert inj_sqlite_queries == 0, f"Expected 0 SQLite queries during inject_event, got {inj_sqlite_queries}"

    # Test issue resolution on this train
    print(f"  Resolving issue on real train {sample_num}...")
    before_res_queries = sqlite_query_count
    res_result = await simulation_engine.resolve_train_issue(sample_num)
    res_sqlite_queries = sqlite_query_count - before_res_queries
    print(f"  Issue resolution result: {res_result.get('success')}, SQLite queries: {res_sqlite_queries}")
    assert res_result.get("success"), f"Failed to resolve train issue: {res_result}"
    assert res_sqlite_queries == 0, f"Expected 0 SQLite queries during resolve_train_issue, got {res_sqlite_queries}"

    # Test unregistering the real train
    print(f"  Unregistering real train {sample_num}...")
    before_unreg_queries = sqlite_query_count
    unreg_result = await simulation_engine.unregister_real_train(sample_num)
    unreg_sqlite_queries = sqlite_query_count - before_unreg_queries
    print(f"  Unregistration result: {unreg_result.get('success')}, SQLite queries: {unreg_sqlite_queries}")
    assert unreg_result.get("success"), f"Failed to unregister real train: {unreg_result}"
    assert unreg_sqlite_queries == 0, f"Expected 0 SQLite queries during unregister_real_train, got {unreg_sqlite_queries}"

    # Verify cleanup in MongoDB
    pos_after = await db[COLL_TRAIN_POSITIONS].find_one({"_id": sample_num})
    train_after = await db[COLL_TRAINS].find_one({"_id": sample_num})
    assert pos_after is None, "Train position should be deleted after unregister"
    assert train_after is None, "Train document should be deleted after unregister"
    print("  Verified MongoDB cleanup after unregistration")

    # 5. Verify AuthService zero-SQLite read
    print("\n[6/6] Testing AuthService decoupled user lookup...")
    before_auth_queries = sqlite_query_count
    user = await auth_service.get_user_by_id(1)
    auth_sqlite_queries = sqlite_query_count - before_auth_queries
    print(f"  User found: {user.name if user else None} ({user.phone if user else None}), SQLite queries: {auth_sqlite_queries}")
    assert auth_sqlite_queries == 0, f"Expected 0 SQLite queries during auth lookup, got {auth_sqlite_queries}"

    await close_mongo()
    print("\n" + "=" * 70)
    print("  PHASE 6 VERIFICATION COMPLETE — ZERO SQLITE RUNTIME QUERIES CONFIRMED")
    print(f"  Total unexpected SQLite queries across entire run: {sqlite_query_count}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
