"""
Verification script for Phase 4.4A: train_positions migration from SQLite to MongoDB.
Verifies count, exact train_id set equality, 100% field parity across all 16 fields,
timestamps, numeric values, and cross-collection integrity.
"""

import asyncio
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, Any

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.mongodb import (
    init_mongo,
    close_mongo,
    get_mongo_db,
    COLL_TRAIN_POSITIONS,
    COLL_USERS,
    COLL_COUNTERS,
    COLL_ALERTS,
    COLL_OPERATIONAL_EVENTS,
    COLL_CONGESTION_SECTIONS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
)
from scripts.migrate_train_positions_to_mongo import find_sqlite_db_path, read_sqlite_train_positions


async def verify():
    results = {
        "success": False,
        "errors": [],
        "warnings": [],
        "counts": {},
        "id_check": {},
        "field_parity": {},
        "cross_collection_integrity": {},
    }

    sqlite_db_path = find_sqlite_db_path()
    sql_positions = read_sqlite_train_positions(sqlite_db_path)
    sql_count = len(sql_positions)
    results["counts"]["sqlite_positions"] = sql_count

    await init_mongo()
    db = get_mongo_db()
    if db is None:
        results["errors"].append("Failed to connect to MongoDB.")
        print(json.dumps(results, indent=2))
        return 1

    mongo_positions = await db[COLL_TRAIN_POSITIONS].find({}).to_list(length=100)
    mongo_count = len(mongo_positions)
    results["counts"]["mongo_positions"] = mongo_count
    results["counts"]["count_match"] = (sql_count == mongo_count == 10)

    if sql_count != 10:
        results["errors"].append(f"Expected 10 SQLite positions, found {sql_count}")
    if mongo_count != 10:
        results["errors"].append(f"Expected 10 MongoDB positions, found {mongo_count}")

    # ID set checks
    sql_ids = set(p["train_id"] for p in sql_positions)
    mongo_ids = set(p["train_id"] for p in mongo_positions)
    mongo_doc_ids = set(p["_id"] for p in mongo_positions)

    results["id_check"]["train_id_set_equal"] = (sql_ids == mongo_ids)
    results["id_check"]["_id_matches_train_id"] = (mongo_ids == mongo_doc_ids)
    results["id_check"]["missing_in_mongo"] = list(sql_ids - mongo_ids)
    results["id_check"]["unexpected_in_mongo"] = list(mongo_ids - sql_ids)
    results["id_check"]["no_duplicate_train_ids"] = (len(mongo_positions) == len(mongo_ids))

    if sql_ids != mongo_ids:
        results["errors"].append("Train ID set mismatch between SQLite and MongoDB.")
    if mongo_ids != mongo_doc_ids:
        results["errors"].append("MongoDB _id does not match train_id for some documents.")

    # Field-by-field parity check
    mongo_map = {p["train_id"]: p for p in mongo_positions}
    checked_fields = [
        "train_id",
        "latitude",
        "longitude",
        "speed_kmph",
        "delay_minutes",
        "status",
        "current_station_code",
        "current_station_name",
        "next_station_code",
        "next_station_name",
        "distance_covered_km",
        "total_distance_km",
        "current_stop_index",
        "at_station",
        "dwell_remaining_seconds",
    ]

    field_mismatches = []
    for sp in sql_positions:
        t_id = sp["train_id"]
        mp = mongo_map.get(t_id)
        if not mp:
            continue

        for f in checked_fields:
            s_val = sp.get(f)
            m_val = mp.get(f)
            if isinstance(s_val, float) or isinstance(m_val, float):
                if s_val is None or m_val is None:
                    if s_val != m_val:
                        field_mismatches.append(f"Train {t_id} field {f}: SQLite={s_val} != Mongo={m_val}")
                elif not math.isclose(float(s_val), float(m_val), rel_tol=1e-5, abs_tol=1e-4):
                    field_mismatches.append(f"Train {t_id} field {f}: SQLite={s_val} != Mongo={m_val}")
            else:
                if s_val != m_val:
                    field_mismatches.append(f"Train {t_id} field {f}: SQLite={s_val} != Mongo={m_val}")

        # Check timestamp
        s_dt = sp.get("last_updated")
        m_dt = mp.get("last_updated")
        if s_dt and m_dt:
            if isinstance(m_dt, str):
                m_dt = datetime.fromisoformat(m_dt.replace("Z", "+00:00"))
            if m_dt.tzinfo is None:
                m_dt = m_dt.replace(tzinfo=timezone.utc)
            if abs((s_dt - m_dt).total_seconds()) > 2.0:
                field_mismatches.append(f"Train {t_id} last_updated timestamp drift: SQLite={s_dt} vs Mongo={m_dt}")

    results["field_parity"]["total_trains_checked"] = len(sql_positions)
    results["field_parity"]["matched_trains"] = len(sql_positions) - len(set(m.split()[1] for m in field_mismatches))
    results["field_parity"]["mismatch_count"] = len(field_mismatches)
    results["field_parity"]["sample_mismatches"] = field_mismatches[:5]

    if field_mismatches:
        results["errors"].extend(field_mismatches[:5])

    # Cross-collection integrity
    u_count = await db[COLL_USERS].count_documents({})
    u_seq = (await db[COLL_COUNTERS].find_one({"_id": "user_id"}))["seq"]
    a_count = await db[COLL_ALERTS].count_documents({})
    a_seq = (await db[COLL_COUNTERS].find_one({"_id": "alert_id"}))["seq"]
    e_count = await db[COLL_OPERATIONAL_EVENTS].count_documents({})
    e_seq = (await db[COLL_COUNTERS].find_one({"_id": "event_id"}))["seq"]
    t_count = await db[COLL_TRAINS].count_documents({})
    rt_count = await db[COLL_REAL_TRAINS].count_documents({})
    c_count = await db[COLL_CONGESTION_SECTIONS].count_documents({})

    results["cross_collection_integrity"] = {
        "users": u_count, "users_ok": (u_count == 42 and u_seq == 42),
        "alerts": a_count, "alerts_ok": (a_count == 157 and a_seq == 157),
        "events": e_count, "events_ok": (e_count == 29 and e_seq == 29),
        "trains": t_count, "trains_ok": (t_count == 10),
        "real_trains": rt_count, "real_trains_ok": (rt_count == 5211),
        "congestion_sections": c_count, "congestion_ok": (c_count == 20),
    }

    if not all([
        results["cross_collection_integrity"]["users_ok"],
        results["cross_collection_integrity"]["alerts_ok"],
        results["cross_collection_integrity"]["events_ok"],
        results["cross_collection_integrity"]["trains_ok"],
        results["cross_collection_integrity"]["real_trains_ok"],
        results["cross_collection_integrity"]["congestion_ok"],
    ]):
        results["errors"].append("Cross-collection integrity check failed.")

    await close_mongo()

    results["success"] = (len(results["errors"]) == 0)
    print(json.dumps(results, indent=2))
    return 0 if results["success"] else 1


if __name__ == "__main__":
    code = asyncio.run(verify())
    sys.exit(code)
