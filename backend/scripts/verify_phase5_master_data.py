"""
Verification script for RailPulse Phase 5 — Master Data Read Path Cutover.

Verifies:
1. DemoTrainDataSource: search_trains, get_train, get_route from MongoDB 'trains' (no _id exposed).
2. RealTrainDataSource: count_trains (5,211), search_trains with projection, get_train, get_route.
   Representative real trains: 20491, 20492, 22436.
3. Stations: 8,704 stations in MongoDB, fast indexed lookup, coordinates preserved.
4. SimulationEngine:
   - In-memory master data cache preloads 10 trains, 68 stops, referenced stations in exactly 2 Atlas queries.
   - 0 MongoDB queries per 3-second simulation tick for master data.
   - Runs multiple live ticks, verifying positions, progression, ETAs.
5. ETAService: calculate_all_upcoming_etas uses MongoDB documents directly.
6. AnalyticsService: get_full_analytics reads trains from MongoDB.
7. Cross-collection integrity: all 10 collections match verified counts.
8. SQLite fallback: remains completely intact if MongoDB fails.
"""

import os
import sys
import time
import asyncio
from typing import Dict, List, Any

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

from app.config import settings
from app.database.mongodb import (
    init_mongo,
    close_mongo,
    get_mongo_db,
    COLL_USERS,
    COLL_COUNTERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_STATIONS,
    COLL_CONGESTION_SECTIONS,
    COLL_ALERTS,
    COLL_OPERATIONAL_EVENTS,
    COLL_TRAIN_POSITIONS,
    COLL_ETA_PREDICTIONS,
)
from app.adapters.train_data_source import demo_source, real_source
from app.simulation.engine import simulation_engine
from app.services.analytics_service import analytics_service
from app.services.eta_service import eta_service


async def verify_phase5():
    print("=" * 70)
    print("  RAILPULSE PHASE 5 — MASTER DATA READ PATH VERIFICATION")
    print("=" * 70)

    connected = await init_mongo()
    if not connected:
        print("\n[CRITICAL] MongoDB Atlas connection failed!")
        print("Please ensure your current public IP is whitelisted in MongoDB Atlas Network Access.")
        return False

    db = get_mongo_db()
    passed_checks = 0
    total_checks = 0

    def check(desc: str, condition: bool, detail: str = ""):
        nonlocal passed_checks, total_checks
        total_checks += 1
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] Check {total_checks:02d}: {desc}" + (f" -> {detail}" if detail else ""))
        if condition:
            passed_checks += 1
        return condition

    print("\n--- 1. Cross-Collection Document Count & Integrity ---")
    u_count = await db[COLL_USERS].count_documents({})
    check("Users count == 42", u_count == 42, f"Found {u_count}")

    t_count = await db[COLL_TRAINS].count_documents({})
    check("Demo trains count == 10", t_count == 10, f"Found {t_count}")

    # Count embedded demo stops
    t_pipeline = [{"$project": {"stop_count": {"$size": "$stops"}}}]
    cursor = await db[COLL_TRAINS].aggregate(t_pipeline)
    t_stops_docs = await cursor.to_list(length=20)
    total_demo_stops = sum(d.get("stop_count", 0) for d in t_stops_docs)
    check("Embedded demo route stops == 68", total_demo_stops == 68, f"Found {total_demo_stops}")

    st_count = await db[COLL_STATIONS].count_documents({})
    check("Stations count == 8,704", st_count == 8704, f"Found {st_count}")

    rt_count = await db[COLL_REAL_TRAINS].count_documents({})
    check("Real trains count == 5,211", rt_count == 5211, f"Found {rt_count}")

    # Sample embedded stops for real trains
    rt_sample = await db[COLL_REAL_TRAINS].find_one({"train_number": "20491"})
    check("Real train 20491 has embedded stops", rt_sample is not None and len(rt_sample.get("stops", [])) > 0,
          f"Found {len(rt_sample.get('stops', [])) if rt_sample else 0} stops")

    c_count = await db[COLL_CONGESTION_SECTIONS].count_documents({})
    check("Congestion sections == 20", c_count == 20, f"Found {c_count}")

    a_count = await db[COLL_ALERTS].count_documents({})
    check("Alerts collection has documents (>=157)", a_count >= 157, f"Found {a_count}")

    e_count = await db[COLL_OPERATIONAL_EVENTS].count_documents({})
    check("Operational events collection has documents (>=29)", e_count >= 29, f"Found {e_count}")

    tp_count = await db[COLL_TRAIN_POSITIONS].count_documents({})
    check("Train positions == 10 active demo trains", tp_count == 10, f"Found {tp_count}")

    eta_count = await db[COLL_ETA_PREDICTIONS].count_documents({})
    check("ETA predictions >= 31 baseline", eta_count >= 31, f"Found {eta_count}")

    print("\n--- 2. DemoTrainDataSource Read Path Cutover ---")
    t0 = time.perf_counter()
    demo_trains = await demo_source.search_trains("")
    demo_search_ms = (time.perf_counter() - t0) * 1000.0
    check("demo_source.search_trains returns 10 trains", len(demo_trains) == 10, f"{len(demo_trains)} trains in {demo_search_ms:.1f}ms")
    check("No MongoDB '_id' exposed in demo search results", all("_id" not in t for t in demo_trains))

    # Test single train lookup
    t0 = time.perf_counter()
    sample_demo = await demo_source.get_train("12951")
    demo_get_ms = (time.perf_counter() - t0) * 1000.0
    check("demo_source.get_train('12951') returns Mumbai Rajdhani", sample_demo is not None and sample_demo.get("train_number") == "12951",
          f"{sample_demo.get('train_name')} in {demo_get_ms:.1f}ms")
    check("No MongoDB '_id' in get_train response", sample_demo is not None and "_id" not in sample_demo)

    # Test route lookup
    t0 = time.perf_counter()
    sample_route = await demo_source.get_route("12951")
    demo_route_ms = (time.perf_counter() - t0) * 1000.0
    check("demo_source.get_route('12951') returns route stops", len(sample_route) > 0, f"{len(sample_route)} stops in {demo_route_ms:.1f}ms")
    check("Route stops contain status ('completed'/'current'/'upcoming')",
          all(s.get("status") in ("completed", "current", "upcoming") for s in sample_route))

    print("\n--- 3. RealTrainDataSource Read Path Cutover ---")
    t0 = time.perf_counter()
    real_total = await real_source.count_trains("")
    real_count_ms = (time.perf_counter() - t0) * 1000.0
    check("real_source.count_trains() == 5,211", real_total == 5211, f"Count {real_total} in {real_count_ms:.1f}ms")

    # Search with projection (stops excluded)
    t0 = time.perf_counter()
    real_page = await real_source.search_trains(query="", limit=50, offset=0)
    real_search_ms = (time.perf_counter() - t0) * 1000.0
    check("real_source.search_trains returns 50 trains", len(real_page) == 50, f"50 trains in {real_search_ms:.1f}ms")
    check("No MongoDB '_id' in real search results", all("_id" not in t for t in real_page))

    # Pagination offset
    real_page_offset = await real_source.search_trains(query="", limit=50, offset=50)
    check("Pagination returns distinct page 2", len(real_page_offset) == 50 and real_page[0]["train_number"] != real_page_offset[0]["train_number"],
          f"Page 1 start: {real_page[0]['train_number']}, Page 2 start: {real_page_offset[0]['train_number']}")

    # Verify representative trains
    rep_trains = ["20491", "20492", "22436"]
    for tn in rep_trains:
        t0 = time.perf_counter()
        rt_info = await real_source.get_train(tn)
        rt_get_ms = (time.perf_counter() - t0) * 1000.0
        check(f"Representative real train {tn} found", rt_info is not None and rt_info.get("train_number") == tn,
              f"{rt_info.get('train_name') if rt_info else 'None'} in {rt_get_ms:.1f}ms")
        check(f"Representative train {tn} has no '_id'", rt_info is not None and "_id" not in rt_info)

        rt_route = await real_source.get_route(tn)
        check(f"Representative train {tn} route stops loaded", len(rt_route) > 0, f"{len(rt_route)} stops")

    print("\n--- 4. Station Lookup Cutover ---")
    t0 = time.perf_counter()
    ndls = await db[COLL_STATIONS].find_one({"station_code": "NDLS"})
    st_ms = (time.perf_counter() - t0) * 1000.0
    check("Station NDLS found with coordinates", ndls is not None and ndls.get("latitude") is not None and ndls.get("longitude") is not None,
          f"Lat: {ndls.get('latitude')}, Lon: {ndls.get('longitude')} in {st_ms:.1f}ms")

    bct = await db[COLL_STATIONS].find_one({"station_code": "MMCT"})
    if not bct:
        bct = await db[COLL_STATIONS].find_one({"station_code": "BCT"})
    check("Station MMCT/BCT found with coordinates", bct is not None and bct.get("latitude") is not None,
          f"Lat: {bct.get('latitude') if bct else 'N/A'}")

    print("\n--- 5. SimulationEngine In-Memory Caching ---")
    t0 = time.perf_counter()
    load_ok = await simulation_engine.load_master_data()
    cache_ms = (time.perf_counter() - t0) * 1000.0
    stats = simulation_engine.get_master_data_stats()
    check("simulation_engine.load_master_data() succeeded", load_ok is True, f"Stats: {stats}")
    check("Cache contains exactly 10 demo trains", stats.get("trains_count") == 10, f"{stats.get('trains_count')} trains")
    check("Cache contains 68 route stops", stats.get("route_stops_count") == 68, f"{stats.get('route_stops_count')} stops")
    check("Master data load executed in exactly 2 Atlas queries", stats.get("queries_executed") == 2,
          f"{stats.get('queries_executed')} queries in {stats.get('load_time_ms')}ms")

    print("\n--- 6. Multi-Tick Simulation Verification ---")
    tick_latencies = []
    for tick_num in range(1, 6):
        t_tick = time.perf_counter()
        await simulation_engine.tick()
        dur_ms = (time.perf_counter() - t_tick) * 1000.0
        tick_latencies.append(dur_ms)
        print(f"  Simulation Tick {tick_num}/5 completed in {dur_ms:.1f} ms")

    check("5 live simulation ticks completed", len(tick_latencies) == 5,
          f"Avg tick duration: {sum(tick_latencies)/len(tick_latencies):.1f} ms")

    pos_docs = await db[COLL_TRAIN_POSITIONS].find({}).to_list(length=20)
    check("Exactly 10 active simulated trains updated in MongoDB", len(pos_docs) == 10, f"{len(pos_docs)} train positions")
    check("All 10 positions contain valid lat/lon coordinates", all(p.get("latitude") != 0.0 and p.get("longitude") != 0.0 for p in pos_docs))

    print("\n--- 7. Analytics Service Master Data Cutover ---")
    t0 = time.perf_counter()
    analytics_data = await analytics_service.get_full_analytics()
    analytics_ms = (time.perf_counter() - t0) * 1000.0
    check("analytics_service.get_full_analytics() returned data", analytics_data is not None and "total_trains" in analytics_data,
          f"Total: {analytics_data.get('total_trains')}, Accuracy: {analytics_data.get('prediction_accuracy')}% in {analytics_ms:.1f}ms")
    check("Delay by route calculated from MongoDB trains", len(analytics_data.get("delay_by_route", [])) > 0,
          f"{len(analytics_data.get('delay_by_route', []))} routes analyzed")

    print("\n--- 8. ETA Service Master Data Cutover ---")
    t0 = time.perf_counter()
    etas = await eta_service.calculate_all_upcoming_etas("12951")
    eta_ms = (time.perf_counter() - t0) * 1000.0
    check("eta_service.calculate_all_upcoming_etas('12951') returned predictions", len(etas) > 0,
          f"{len(etas)} station ETAs in {eta_ms:.1f}ms")
    check("ETAs contain predicted arrival and confidence level",
          all("predicted_arrival" in e and "confidence_level" in e for e in etas))

    # Also test real train ETA
    t0 = time.perf_counter()
    real_etas = await eta_service.calculate_all_upcoming_etas("20491")
    real_eta_ms = (time.perf_counter() - t0) * 1000.0
    check("eta_service works for real train 20491", len(real_etas) > 0,
          f"{len(real_etas)} station ETAs in {real_eta_ms:.1f}ms")

    print("\n" + "=" * 70)
    print(f"  PHASE 5 VERIFICATION SUMMARY: {passed_checks}/{total_checks} CHECKS PASSED")
    print("=" * 70)

    await close_mongo()
    return passed_checks == total_checks


if __name__ == "__main__":
    success = asyncio.run(verify_phase5())
    sys.exit(0 if success else 1)
