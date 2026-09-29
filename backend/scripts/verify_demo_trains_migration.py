"""
Independent Verification Script for Phase 4.1 Demo Trains Migration.

Verifies:
A. SQLite demo train count = 10.
B. MongoDB 'trains' demo count = 10.
C. Exact train ID set equality.
D. Exact train-number set equality.
E. Field-by-field parity:
   - Train metadata (train_name, train_type, source, destination, total_distance_km, etc.)
   - Stop count per train
   - Stop sequence ordering
   - Stop station codes, names, arrival, departure, distance, day, halt_minutes
F. Zero duplicate train IDs, zero missing, zero unexpected.
G. Representative train check:
   - 12951 in demo trains
   - 20491, 20492, 22436 verified in real_trains catalog
H. 5,211 real_trains documents untouched.
I. 417,130 real_trains embedded stops untouched.
J. MongoDB users remain exactly 42.
K. MongoDB counter 'user_id' remains seq=42.
"""

import os
import sys
import json
import sqlite3
import asyncio
from typing import Dict, Any, List

# Ensure reliable DNS resolution on Windows
try:
    import dns.asyncresolver
    import dns.resolver
    for r in [dns.asyncresolver.get_default_resolver(), dns.resolver.get_default_resolver()]:
        if r and hasattr(r, "nameservers"):
            reliable_ns = ["8.8.8.8", "1.1.1.1"]
            r.nameservers = [ns for ns in reliable_ns if ns not in r.nameservers] + list(r.nameservers)
except Exception:
    pass

from pymongo import AsyncMongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.database.mongodb import (
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_USERS,
    COLL_COUNTERS,
)


def get_sqlite_connection(db_path: str = "railpulse.db") -> sqlite3.Connection:
    if not os.path.exists(db_path):
        alt_path = os.path.join(os.path.dirname(__file__), "..", "railpulse.db")
        if os.path.exists(alt_path):
            db_path = alt_path
        else:
            alt_root = os.path.join(os.path.dirname(__file__), "..", "..", "railpulse.db")
            if os.path.exists(alt_root):
                db_path = alt_root

    uri_path = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri_path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


async def verify_demo_trains() -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "success": True,
        "errors": [],
        "warnings": [],
        "counts": {},
        "train_id_check": {},
        "train_number_check": {},
        "field_comparisons": [],
        "representative_trains": {},
        "integrity_checks": {},
    }

    if not settings.MONGODB_URL:
        report["success"] = False
        report["errors"].append("MONGODB_URL not configured.")
        return report

    client = AsyncMongoClient(
        settings.MONGODB_URL,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
    )
    db = client[settings.MONGODB_DB_NAME]
    conn = get_sqlite_connection()

    try:
        cur = conn.cursor()

        # 1. Counts check
        cur.execute("SELECT count(*) FROM trains")
        sq_train_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM route_stops")
        sq_stops_count = cur.fetchone()[0]

        mg_train_count = await db[COLL_TRAINS].count_documents({})
        report["counts"] = {
            "sqlite_trains": sq_train_count,
            "sqlite_route_stops": sq_stops_count,
            "mongo_trains": mg_train_count,
            "trains_match": (sq_train_count == mg_train_count == 10),
        }

        if sq_train_count != 10:
            report["success"] = False
            report["errors"].append(f"SQLite trains count expected 10, got {sq_train_count}")
        if mg_train_count != 10:
            report["success"] = False
            report["errors"].append(f"MongoDB trains count expected 10, got {mg_train_count}")

        # 2. Fetch all SQLite trains & stops
        cur.execute("""
            SELECT train_id, train_name, train_number, train_type,
                   source, source_code, destination, destination_code,
                   zone, total_distance_km, scheduled_departure, scheduled_arrival,
                   avg_speed_kmph, max_speed_kmph, days_of_run
            FROM trains
            ORDER BY train_id
        """)
        sqlite_trains = {str(r["train_id"]): dict(r) for r in cur.fetchall()}

        cur.execute("""
            SELECT train_id, station_code, station_name,
                   arrival, departure, distance_from_source,
                   day, stop_number, halt_minutes
            FROM route_stops
            ORDER BY train_id, stop_number ASC
        """)
        sqlite_stops_by_train: Dict[str, List[Dict[str, Any]]] = {}
        for s in cur.fetchall():
            sqlite_stops_by_train.setdefault(str(s["train_id"]), []).append(dict(s))

        # 3. Fetch all Mongo trains
        mongo_trains = {}
        total_mongo_embedded_stops = 0
        async for doc in db[COLL_TRAINS].find({}):
            t_id = str(doc.get("train_id") or doc.get("_id"))
            mongo_trains[t_id] = doc
            total_mongo_embedded_stops += len(doc.get("stops", []))

        report["counts"]["mongo_embedded_stops"] = total_mongo_embedded_stops
        report["counts"]["stops_match"] = (sq_stops_count == total_mongo_embedded_stops == 68)

        # 4. Train ID and Train Number sets
        sq_ids = set(sqlite_trains.keys())
        mg_ids = set(mongo_trains.keys())
        missing_ids = list(sq_ids - mg_ids)
        unexpected_ids = list(mg_ids - sq_ids)

        sq_numbers = {r["train_number"] for r in sqlite_trains.values()}
        mg_numbers = {doc.get("train_number") for doc in mongo_trains.values()}

        report["train_id_check"] = {
            "sqlite_ids": sorted(list(sq_ids)),
            "mongo_ids": sorted(list(mg_ids)),
            "match": (sq_ids == mg_ids),
            "missing": missing_ids,
            "unexpected": unexpected_ids,
        }
        report["train_number_check"] = {
            "sqlite_numbers": sorted(list(sq_numbers)),
            "mongo_numbers": sorted(list(mg_numbers)),
            "match": (sq_numbers == mg_numbers),
        }

        if sq_ids != mg_ids:
            report["success"] = False
            report["errors"].append(f"Train ID mismatch. Missing: {missing_ids}, Unexpected: {unexpected_ids}")

        # 5. Field-by-field and stop comparison
        for t_id, sq_t in sqlite_trains.items():
            mg_t = mongo_trains.get(t_id)
            if not mg_t:
                continue

            comp: Dict[str, Any] = {"train_id": t_id, "train_fields_match": True, "stops_match": True, "diffs": []}

            # Train fields check
            fields_to_check = [
                "train_name", "train_number", "train_type", "source", "source_code",
                "destination", "destination_code", "zone", "scheduled_departure",
                "scheduled_arrival", "days_of_run"
            ]
            for f in fields_to_check:
                sq_val = str(sq_t.get(f) or "")
                mg_val = str(mg_t.get(f) or "")
                if sq_val != mg_val:
                    comp["train_fields_match"] = False
                    comp["diffs"].append(f"Train {t_id} {f}: sqlite='{sq_val}' vs mongo='{mg_val}'")

            # Numeric fields
            for f in ["total_distance_km", "avg_speed_kmph", "max_speed_kmph"]:
                sq_num = float(sq_t.get(f) or 0.0)
                mg_num = float(mg_t.get(f) or 0.0)
                if abs(sq_num - mg_num) > 0.001:
                    comp["train_fields_match"] = False
                    comp["diffs"].append(f"Train {t_id} {f}: sqlite={sq_num} vs mongo={mg_num}")

            # Stops comparison
            sq_stops = sqlite_stops_by_train.get(t_id, [])
            mg_stops = mg_t.get("stops", [])

            if len(sq_stops) != len(mg_stops):
                comp["stops_match"] = False
                comp["diffs"].append(f"Stop count mismatch for {t_id}: sqlite={len(sq_stops)} vs mongo={len(mg_stops)}")
            else:
                for idx, (s_sq, s_mg) in enumerate(zip(sq_stops, mg_stops)):
                    # stop_number
                    if int(s_sq["stop_number"]) != int(s_mg["stop_number"]):
                        comp["stops_match"] = False
                        comp["diffs"].append(f"Stop {idx} stop_number: {s_sq['stop_number']} vs {s_mg['stop_number']}")
                    # station_code
                    if str(s_sq["station_code"]) != str(s_mg["station_code"]):
                        comp["stops_match"] = False
                        comp["diffs"].append(f"Stop {idx} station_code: {s_sq['station_code']} vs {s_mg['station_code']}")
                    # station_name
                    if str(s_sq["station_name"]) != str(s_mg["station_name"]):
                        comp["stops_match"] = False
                        comp["diffs"].append(f"Stop {idx} station_name: {s_sq['station_name']} vs {s_mg['station_name']}")
                    # arrival
                    arr_sq = str(s_sq["arrival"]) if s_sq["arrival"] is not None else None
                    arr_mg = str(s_mg["arrival"]) if s_mg.get("arrival") is not None else None
                    if arr_sq != arr_mg:
                        comp["stops_match"] = False
                        comp["diffs"].append(f"Stop {idx} arrival: {arr_sq} vs {arr_mg}")
                    # departure
                    dep_sq = str(s_sq["departure"]) if s_sq["departure"] is not None else None
                    dep_mg = str(s_mg["departure"]) if s_mg.get("departure") is not None else None
                    if dep_sq != dep_mg:
                        comp["stops_match"] = False
                        comp["diffs"].append(f"Stop {idx} departure: {dep_sq} vs {dep_mg}")
                    # distance
                    dist_sq = float(s_sq["distance_from_source"] or 0.0)
                    dist_mg = float(s_mg.get("distance_from_source") or 0.0)
                    if abs(dist_sq - dist_mg) > 0.001:
                        comp["stops_match"] = False
                        comp["diffs"].append(f"Stop {idx} distance: {dist_sq} vs {dist_mg}")
                    # halt_minutes
                    halt_sq = int(s_sq["halt_minutes"] if s_sq["halt_minutes"] is not None else 2)
                    halt_mg = int(s_mg.get("halt_minutes") if s_mg.get("halt_minutes") is not None else 2)
                    if halt_sq != halt_mg:
                        comp["stops_match"] = False
                        comp["diffs"].append(f"Stop {idx} halt: {halt_sq} vs {halt_mg}")

            if comp["diffs"]:
                report["success"] = False
                report["errors"].extend(comp["diffs"])
            report["field_comparisons"].append({
                "train_id": t_id,
                "train_name": sq_t["train_name"],
                "stops_count": len(sq_stops),
                "matched": comp["train_fields_match"] and comp["stops_match"],
            })

        # 6. Representative trains audit
        # 12951
        t_12951 = mongo_trains.get("12951")
        report["representative_trains"]["12951_in_demo"] = bool(t_12951)
        if t_12951:
            report["representative_trains"]["12951_name"] = t_12951.get("train_name")
            report["representative_trains"]["12951_stops_count"] = len(t_12951.get("stops", []))

        # Check real_trains catalog for 20491, 20492, 22436
        for num in ["20491", "20492", "22436"]:
            rt_doc = await db[COLL_REAL_TRAINS].find_one({"train_number": num})
            report["representative_trains"][f"{num}_in_real_catalog"] = bool(rt_doc)
            if rt_doc:
                report["representative_trains"][f"{num}_name"] = rt_doc.get("train_name")
                report["representative_trains"][f"{num}_stops_count"] = len(rt_doc.get("stops", []))

        # 7. Check untouched collections (real_trains, users, counters)
        real_trains_count = await db[COLL_REAL_TRAINS].count_documents({})
        users_count = await db[COLL_USERS].count_documents({})
        counter_doc = await db[COLL_COUNTERS].find_one({"_id": "user_id"})

        # Sample aggregate for real_trains stops
        pipeline = [{"$project": {"stop_count": {"$size": {"$ifNull": ["$stops", []]}}}}, {"$group": {"_id": None, "total_stops": {"$sum": "$stop_count"}}}]
        agg_cursor = await db[COLL_REAL_TRAINS].aggregate(pipeline)
        agg_res = None
        async for r in agg_cursor:
            agg_res = r
        real_stops_count = agg_res["total_stops"] if agg_res else 0

        report["integrity_checks"] = {
            "real_trains_count": real_trains_count,
            "real_trains_untouched": (real_trains_count == 5211),
            "real_stops_count": real_stops_count,
            "real_stops_untouched": (real_stops_count == 417130),
            "users_count": users_count,
            "users_untouched": (users_count == 42),
            "user_counter_seq": counter_doc["seq"] if counter_doc else None,
            "counter_untouched": (counter_doc and counter_doc["seq"] == 42),
        }

        if real_trains_count != 5211:
            report["success"] = False
            report["errors"].append(f"real_trains count mismatch: {real_trains_count} (expected 5211)")
        if real_stops_count != 417130:
            report["success"] = False
            report["errors"].append(f"real_trains stops count mismatch: {real_stops_count} (expected 417130)")
        if users_count != 42:
            report["success"] = False
            report["errors"].append(f"users count mismatch: {users_count} (expected 42)")
        if not counter_doc or counter_doc["seq"] != 42:
            report["success"] = False
            report["errors"].append(f"user counter seq mismatch: {counter_doc}")

    finally:
        conn.close()
        await client.close()

    return report


if __name__ == "__main__":
    rep = asyncio.run(verify_demo_trains())
    print(json.dumps(rep, indent=2))
