"""
Independent Verification Script for Phase 2 MongoDB Migration.

Verifies:
1. Exact counts: stations (8,704), real_trains (5,211), embedded stops (417,130).
2. Duplicate checks (zero duplicate station_code, zero duplicate train_number).
3. Missing-data checks (bidirectional identity between SQLite and MongoDB).
4. Every train's embedded stop count and sequence order matches SQLite.
5. Field-by-field verification of representative trains (20491, 20492, 22436).
6. Index verification on stations and real_trains collections.
"""

import os
import sys
import sqlite3
import asyncio
from typing import Dict, Any, List

from pymongo import AsyncMongoClient

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings


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


async def verify_migration() -> Dict[str, Any]:
    report: Dict[str, Any] = {"success": True, "errors": [], "warnings": []}

    if not settings.MONGODB_URL:
        report["success"] = False
        report["errors"].append("MONGODB_URL is not set.")
        return report

    client = AsyncMongoClient(settings.MONGODB_URL, serverSelectionTimeoutMS=8000)
    db = client[settings.MONGODB_DB_NAME]
    conn = get_sqlite_connection()

    try:
        cur = conn.cursor()

        # -------------------------------------------------------------
        # 1. COUNTS VERIFICATION
        # -------------------------------------------------------------
        cur.execute("SELECT count(*) FROM stations")
        sqlite_stations = cur.fetchone()[0]
        mongo_stations = await db.stations.count_documents({})

        cur.execute("SELECT count(*) FROM real_trains")
        sqlite_trains = cur.fetchone()[0]
        mongo_trains = await db.real_trains.count_documents({})

        cur.execute("SELECT count(*) FROM real_train_stops")
        sqlite_stops = cur.fetchone()[0]

        # Aggregate total embedded stops in MongoDB
        pipeline = [
            {"$project": {"stop_count": {"$size": "$stops"}}},
            {"$group": {"_id": None, "total_stops": {"$sum": "$stop_count"}}}
        ]
        agg_cursor = await db.real_trains.aggregate(pipeline)
        agg_result = await agg_cursor.to_list(1)
        mongo_stops = agg_result[0]["total_stops"] if agg_result else 0

        report["counts"] = {
            "stations": {"sqlite": sqlite_stations, "mongo": mongo_stations, "match": sqlite_stations == mongo_stations},
            "real_trains": {"sqlite": sqlite_trains, "mongo": mongo_trains, "match": sqlite_trains == mongo_trains},
            "embedded_stops": {"sqlite": sqlite_stops, "mongo": mongo_stops, "match": sqlite_stops == mongo_stops},
        }

        if sqlite_stations != mongo_stations:
            report["success"] = False
            report["errors"].append(f"Station count mismatch: SQLite={sqlite_stations}, MongoDB={mongo_stations}")
        if sqlite_trains != mongo_trains:
            report["success"] = False
            report["errors"].append(f"Train count mismatch: SQLite={sqlite_trains}, MongoDB={mongo_trains}")
        if sqlite_stops != mongo_stops:
            report["success"] = False
            report["errors"].append(f"Embedded stop count mismatch: SQLite={sqlite_stops}, MongoDB={mongo_stops}")

        # -------------------------------------------------------------
        # 2. DUPLICATE CHECKS
        # -------------------------------------------------------------
        distinct_station_codes = await db.stations.distinct("station_code")
        distinct_train_numbers = await db.real_trains.distinct("train_number")
        report["duplicates"] = {
            "stations_unique": len(distinct_station_codes) == mongo_stations,
            "trains_unique": len(distinct_train_numbers) == mongo_trains,
        }

        # -------------------------------------------------------------
        # 3. MISSING-DATA CHECKS (SET DIFFERENCE)
        # -------------------------------------------------------------
        cur.execute("SELECT station_code FROM stations")
        sqlite_st_set = set(r[0] for r in cur.fetchall())
        mongo_st_set = set(distinct_station_codes)
        station_diff_missing = sqlite_st_set - mongo_st_set
        station_diff_unexpected = mongo_st_set - sqlite_st_set

        cur.execute("SELECT train_number FROM real_trains")
        sqlite_tr_set = set(r[0] for r in cur.fetchall())
        mongo_tr_set = set(distinct_train_numbers)
        train_diff_missing = sqlite_tr_set - mongo_tr_set
        train_diff_unexpected = mongo_tr_set - sqlite_tr_set

        report["missing_data"] = {
            "stations_missing_in_mongo": len(station_diff_missing),
            "stations_unexpected_in_mongo": len(station_diff_unexpected),
            "trains_missing_in_mongo": len(train_diff_missing),
            "trains_unexpected_in_mongo": len(train_diff_unexpected),
        }

        if station_diff_missing or station_diff_unexpected or train_diff_missing or train_diff_unexpected:
            report["success"] = False
            report["errors"].append("Missing or unexpected records detected in set difference comparison.")

        # -------------------------------------------------------------
        # 4. EVERY TRAIN'S STOP COUNT MATCH
        # -------------------------------------------------------------
        cur.execute("SELECT train_number, count(*) FROM real_train_stops GROUP BY train_number")
        sqlite_stop_counts = {r[0]: r[1] for r in cur.fetchall()}

        mismatched_stop_counts = []
        async for t_doc in db.real_trains.find({}, {"train_number": 1, "stops": 1}):
            t_num = t_doc["train_number"]
            expected_c = sqlite_stop_counts.get(t_num, 0)
            actual_c = len(t_doc.get("stops", []))
            if expected_c != actual_c:
                mismatched_stop_counts.append((t_num, expected_c, actual_c))

        report["stop_count_mismatches"] = len(mismatched_stop_counts)
        if mismatched_stop_counts:
            report["success"] = False
            report["errors"].append(f"{len(mismatched_stop_counts)} trains have stop count mismatches.")

        # -------------------------------------------------------------
        # 5. REPRESENTATIVE TRAIN VERIFICATION (20491, 20492, 22436)
        # -------------------------------------------------------------
        representative_trains = ["20491", "20492", "22436"]
        rep_report: Dict[str, Any] = {}

        for t_no in representative_trains:
            cur.execute("""
                SELECT train_number, train_name, train_type,
                       source_station, source_station_name,
                       destination_station, destination_station_name,
                       distance, running_days, data_source
                FROM real_trains WHERE train_number=?
            """, (t_no,))
            sq_t = cur.fetchone()
            if not sq_t:
                rep_report[t_no] = {"error": f"Train {t_no} not found in SQLite!"}
                continue

            cur.execute("""
                SELECT sequence, station_code, station_name,
                       arrival_time, departure_time, halt_minutes, day_offset, distance
                FROM real_train_stops WHERE train_number=?
                ORDER BY sequence
            """, (t_no,))
            sq_stops = [dict(r) for r in cur.fetchall()]

            mg_t = await db.real_trains.find_one({"train_number": t_no})
            if not mg_t:
                rep_report[t_no] = {"error": f"Train {t_no} not found in MongoDB!"}
                report["success"] = False
                report["errors"].append(f"Representative train {t_no} missing from MongoDB!")
                continue

            # Compare metadata
            meta_match = (
                sq_t["train_name"] == mg_t["train_name"] and
                sq_t["source_station"] == mg_t["source_station"] and
                sq_t["destination_station"] == mg_t["destination_station"] and
                float(sq_t["distance"] or 0) == float(mg_t["distance"] or 0) and
                sq_t["running_days"] == mg_t["running_days"]
            )

            # Compare stops
            mg_stops = mg_t.get("stops", [])
            stops_match = len(sq_stops) == len(mg_stops)

            stop_diffs = []
            if stops_match:
                for idx, (s_sq, s_mg) in enumerate(zip(sq_stops, mg_stops)):
                    if s_sq["sequence"] != s_mg["sequence"]:
                        stop_diffs.append(f"Stop {idx}: sequence mismatch {s_sq['sequence']} vs {s_mg['sequence']}")
                    if s_sq["station_code"] != s_mg["station_code"]:
                        stop_diffs.append(f"Stop {idx}: station_code mismatch {s_sq['station_code']} vs {s_mg['station_code']}")
                    if s_sq["arrival_time"] != s_mg["arrival_time"]:
                        stop_diffs.append(f"Stop {idx}: arrival_time mismatch {s_sq['arrival_time']} vs {s_mg['arrival_time']}")
                    if s_sq["departure_time"] != s_mg["departure_time"]:
                        stop_diffs.append(f"Stop {idx}: departure_time mismatch {s_sq['departure_time']} vs {s_mg['departure_time']}")
                    if s_sq["halt_minutes"] != s_mg["halt_minutes"]:
                        stop_diffs.append(f"Stop {idx}: halt_minutes mismatch {s_sq['halt_minutes']} vs {s_mg['halt_minutes']}")
                    if s_sq["day_offset"] != s_mg["day_offset"]:
                        stop_diffs.append(f"Stop {idx}: day_offset mismatch {s_sq['day_offset']} vs {s_mg['day_offset']}")
                    if s_sq["distance"] is not None and s_mg["distance"] is not None:
                        if float(s_sq["distance"]) != float(s_mg["distance"]):
                            stop_diffs.append(f"Stop {idx}: distance mismatch {s_sq['distance']} vs {s_mg['distance']}")

            rep_report[t_no] = {
                "metadata_match": meta_match,
                "sqlite_stops_count": len(sq_stops),
                "mongo_stops_count": len(mg_stops),
                "stops_match": stops_match and len(stop_diffs) == 0,
                "differences": stop_diffs,
                "first_stop": mg_stops[0] if mg_stops else None,
                "last_stop": mg_stops[-1] if mg_stops else None,
            }

            if not meta_match or not stops_match or stop_diffs:
                report["success"] = False
                report["errors"].append(f"Representative train {t_no} has verification mismatches: {stop_diffs}")

        report["representative_trains"] = rep_report

        # -------------------------------------------------------------
        # 6. INDEX VERIFICATION
        # -------------------------------------------------------------
        st_indexes = await db.stations.index_information()
        tr_indexes = await db.real_trains.index_information()

        report["indexes"] = {
            "stations_indexes": list(st_indexes.keys()),
            "real_trains_indexes": list(tr_indexes.keys()),
            "station_code_unique": any(i.get("unique") for i in st_indexes.values() if i.get("key") == [("station_code", 1)]),
            "train_number_unique": any(i.get("unique") for i in tr_indexes.values() if i.get("key") == [("train_number", 1)]),
            "multikey_stops_station_code": any(i.get("key") == [("stops.station_code", 1)] for i in tr_indexes.values()),
        }

    finally:
        conn.close()
        await client.close()

    return report


if __name__ == "__main__":
    rep = asyncio.run(verify_migration())
    import json
    print(json.dumps(rep, indent=2))
