"""
Independent verification script for Phase 4.2A congestion_sections migration.

Verifies:
1. SQLite congestion count == MongoDB congestion count (20 == 20)
2. Exact section ID set equality (all 20 IDs match)
3. 100% field parity (from_station, to_station, names, score, speed, active_trains, status, delay_impact)
4. Zero duplicate IDs, zero missing, zero unexpected
5. Other collections intact:
   - users == 42
   - counters.user_id seq == 42
   - trains == 10 (68 embedded stops)
   - real_trains == 5211 (417,130 embedded stops)
"""

import asyncio
import os
import sys
import json
import sqlite3
from typing import Dict, Any
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

from pymongo import AsyncMongoClient
from app.database.mongodb import (
    COLL_CONGESTION_SECTIONS,
    COLL_USERS,
    COLL_COUNTERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
)


def read_sqlite_congestion() -> Dict[str, Dict[str, Any]]:
    sqlite_path = os.path.join(backend_dir, "railpulse.db")
    uri = f"file:{os.path.abspath(sqlite_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT section_id, from_station, to_station, from_station_name, to_station_name,
               congestion_score, avg_speed_kmph, active_trains, status, delay_impact_minutes
        FROM congestion_sections
        ORDER BY section_id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return {r["section_id"]: dict(r) for r in rows}


async def verify():
    results = {
        "success": True,
        "errors": [],
        "warnings": [],
        "counts": {},
        "section_id_check": {},
        "field_comparisons": [],
        "integrity_checks": {},
    }

    sqlite_sections = read_sqlite_congestion()
    results["counts"]["sqlite_sections"] = len(sqlite_sections)

    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME", "railpulse")
    client = AsyncMongoClient(mongo_url)

    try:
        db = client[db_name]
        col = db[COLL_CONGESTION_SECTIONS]

        mongo_docs = await col.find({}).to_list(length=100)
        results["counts"]["mongo_sections"] = len(mongo_docs)
        results["counts"]["sections_match"] = (len(sqlite_sections) == len(mongo_docs))

        if not results["counts"]["sections_match"]:
            results["success"] = False
            results["errors"].append(
                f"Section count mismatch: SQLite={len(sqlite_sections)}, Mongo={len(mongo_docs)}"
            )

        # ID checks
        sqlite_ids = sorted(list(sqlite_sections.keys()))
        mongo_ids = sorted([doc["section_id"] for doc in mongo_docs])
        missing_in_mongo = list(set(sqlite_ids) - set(mongo_ids))
        unexpected_in_mongo = list(set(mongo_ids) - set(sqlite_ids))

        results["section_id_check"] = {
            "sqlite_ids": sqlite_ids,
            "mongo_ids": mongo_ids,
            "match": (sqlite_ids == mongo_ids),
            "missing": missing_in_mongo,
            "unexpected": unexpected_in_mongo,
        }

        if missing_in_mongo or unexpected_in_mongo:
            results["success"] = False
            results["errors"].append(f"Missing in Mongo: {missing_in_mongo}, Unexpected: {unexpected_in_mongo}")

        # Check duplicate _id or section_id
        if len(mongo_ids) != len(set(mongo_ids)):
            results["success"] = False
            results["errors"].append("Duplicate section_ids found in MongoDB collection")

        # Field-by-field check
        mongo_map = {doc["section_id"]: doc for doc in mongo_docs}
        float_tolerance = 1e-4

        for sec_id, sql_doc in sqlite_sections.items():
            if sec_id not in mongo_map:
                continue
            m_doc = mongo_map[sec_id]
            diffs = []

            for str_field in ["from_station", "to_station", "from_station_name", "to_station_name", "status"]:
                s_val = str(sql_doc.get(str_field, ""))
                m_val = str(m_doc.get(str_field, ""))
                if s_val != m_val:
                    diffs.append(f"{str_field}: SQL='{s_val}' vs Mongo='{m_val}'")

            for num_field in ["congestion_score", "avg_speed_kmph", "delay_impact_minutes"]:
                s_val = float(sql_doc[num_field])
                m_val = float(m_doc[num_field])
                if abs(s_val - m_val) > float_tolerance:
                    diffs.append(f"{num_field}: SQL={s_val} vs Mongo={m_val}")

            if int(sql_doc["active_trains"]) != int(m_doc["active_trains"]):
                diffs.append(f"active_trains: SQL={sql_doc['active_trains']} vs Mongo={m_doc['active_trains']}")

            results["field_comparisons"].append({
                "section_id": sec_id,
                "matched": (len(diffs) == 0),
                "diffs": diffs,
            })

            if diffs:
                results["success"] = False
                results["errors"].append(f"Field mismatch for section {sec_id}: {diffs}")

        # Integrity checks on other collections
        users_count = await db[COLL_USERS].count_documents({})
        results["integrity_checks"]["users_count"] = users_count
        results["integrity_checks"]["users_untouched"] = (users_count == 42)
        if users_count != 42:
            results["success"] = False
            results["errors"].append(f"Users collection count changed: {users_count} != 42")

        counter_doc = await db[COLL_COUNTERS].find_one({"_id": "user_id"})
        counter_seq = counter_doc.get("seq") if counter_doc else None
        results["integrity_checks"]["user_counter_seq"] = counter_seq
        results["integrity_checks"]["counter_untouched"] = (counter_seq == 42)
        if counter_seq != 42:
            results["success"] = False
            results["errors"].append(f"Counter sequence changed: {counter_seq} != 42")

        trains_count = await db[COLL_TRAINS].count_documents({})
        results["integrity_checks"]["trains_count"] = trains_count
        results["integrity_checks"]["trains_untouched"] = (trains_count == 10)
        if trains_count != 10:
            results["success"] = False
            results["errors"].append(f"Trains collection count changed: {trains_count} != 10")

        real_trains_count = await db[COLL_REAL_TRAINS].count_documents({})
        results["integrity_checks"]["real_trains_count"] = real_trains_count
        results["integrity_checks"]["real_trains_untouched"] = (real_trains_count == 5211)
        if real_trains_count != 5211:
            results["success"] = False
            results["errors"].append(f"Real trains count changed: {real_trains_count} != 5211")

        print(json.dumps(results, indent=2))
        return results
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(verify())
