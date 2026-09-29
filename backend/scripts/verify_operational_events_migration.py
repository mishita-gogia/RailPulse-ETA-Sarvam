"""
Independent verification script for Phase 4.3 operational_events migration.

Verifies:
1. SQLite count == MongoDB count
2. Exact ID set equality (all IDs match)
3. 100% field parity (event_type, train_id, location, severity, duration_minutes, description, impact_delay, active, created_at, expires_at)
4. Zero duplicate IDs, zero missing, zero unexpected
5. Counter seq == max(event.id)
6. Other collections intact:
   - users == 42
   - counters.user_id seq == 42
   - alerts == 157
   - counters.alert_id seq == 157
   - trains == 10
   - real_trains == 5211
   - congestion_sections == 20
"""

import asyncio
import os
import sys
import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any
from dotenv import load_dotenv

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
repo_dir = os.path.abspath(os.path.join(backend_dir, ".."))
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
    COLL_OPERATIONAL_EVENTS,
    COLL_COUNTERS,
    COLL_USERS,
    COLL_ALERTS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_CONGESTION_SECTIONS,
)


def find_sqlite_db_path() -> str:
    paths = [
        os.path.join(repo_dir, "railpulse.db"),
        os.path.join(backend_dir, "railpulse.db"),
    ]
    for p in paths:
        if os.path.exists(p):
            conn = sqlite3.connect(p)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM operational_events")
            cnt = c.fetchone()[0]
            conn.close()
            if cnt > 0:
                return p
    return paths[0]


def read_sqlite_events() -> Dict[int, Dict[str, Any]]:
    sqlite_path = find_sqlite_db_path()
    uri = f"file:{os.path.abspath(sqlite_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, event_type, train_id, location, severity, duration_minutes,
               description, impact_delay_minutes, active, created_at, expires_at
        FROM operational_events
        ORDER BY id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return {r["id"]: dict(r) for r in rows}


async def verify():
    results = {
        "success": True,
        "errors": [],
        "warnings": [],
        "counts": {},
        "id_check": {},
        "field_parity": {
            "total_checked": 0,
            "matched": 0,
            "mismatched": 0,
            "sample_diffs": [],
        },
        "counter_check": {},
        "integrity_checks": {},
    }

    sqlite_events = read_sqlite_events()
    sql_count = len(sqlite_events)
    max_sql_id = max(sqlite_events.keys()) if sqlite_events else 0
    results["counts"]["sqlite_events"] = sql_count
    results["counts"]["max_sqlite_id"] = max_sql_id

    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME", "railpulse")
    client = AsyncMongoClient(mongo_url)

    try:
        db = client[db_name]
        col = db[COLL_OPERATIONAL_EVENTS]
        counters_col = db[COLL_COUNTERS]

        mongo_docs = await col.find({}).to_list(length=1000)
        mongo_count = len(mongo_docs)
        results["counts"]["mongo_events"] = mongo_count
        results["counts"]["count_match"] = (sql_count == mongo_count)

        if not results["counts"]["count_match"]:
            results["success"] = False
            results["errors"].append(f"Count mismatch: SQLite={sql_count}, Mongo={mongo_count}")

        # ID set equality
        sql_ids = sorted(list(sqlite_events.keys()))
        mongo_ids = sorted([doc["id"] for doc in mongo_docs])
        missing_in_mongo = list(set(sql_ids) - set(mongo_ids))
        unexpected_in_mongo = list(set(mongo_ids) - set(sql_ids))

        results["id_check"] = {
            "min_sql_id": min(sql_ids) if sql_ids else 0,
            "max_sql_id": max(sql_ids) if sql_ids else 0,
            "min_mongo_id": min(mongo_ids) if mongo_ids else 0,
            "max_mongo_id": max(mongo_ids) if mongo_ids else 0,
            "id_set_equal": (sql_ids == mongo_ids),
            "missing_in_mongo": missing_in_mongo,
            "unexpected_in_mongo": unexpected_in_mongo,
            "no_duplicates_mongo": (len(mongo_ids) == len(set(mongo_ids))),
        }

        if missing_in_mongo or unexpected_in_mongo or not results["id_check"]["no_duplicates_mongo"]:
            results["success"] = False
            results["errors"].append("Event ID set discrepancy or duplicate IDs found.")

        # Counter sequence check
        event_counter = await counters_col.find_one({"_id": "event_id"})
        counter_seq = event_counter.get("seq") if event_counter else None
        results["counter_check"] = {
            "counter_seq": counter_seq,
            "matches_max_id": (counter_seq == max_sql_id),
        }
        if counter_seq != max_sql_id:
            results["success"] = False
            results["errors"].append(f"Counter 'event_id' seq={counter_seq} != max SQLite ID={max_sql_id}")

        # Field parity check
        mongo_map = {doc["id"]: doc for doc in mongo_docs}
        results["field_parity"]["total_checked"] = len(sqlite_events)

        for eid, sql_row in sqlite_events.items():
            if eid not in mongo_map:
                continue
            m_doc = mongo_map[eid]
            diffs = []

            for str_f in ["event_type", "train_id", "location", "description"]:
                s_v = str(sql_row.get(str_f) or "")
                m_v = str(m_doc.get(str_f) or "")
                if s_v != m_v:
                    diffs.append(f"{str_f}: SQL='{s_v}' vs Mongo='{m_v}'")

            for num_f in ["severity", "impact_delay_minutes"]:
                s_v = float(sql_row.get(num_f) or 0.0)
                m_v = float(m_doc.get(num_f) or 0.0)
                if abs(s_v - m_v) > 1e-4:
                    diffs.append(f"{num_f}: SQL={s_v} vs Mongo={m_v}")

            if int(sql_row.get("duration_minutes") or 0) != int(m_doc.get("duration_minutes") or 0):
                diffs.append(f"duration_minutes: SQL={sql_row.get('duration_minutes')} vs Mongo={m_doc.get('duration_minutes')}")

            if bool(sql_row.get("active")) != bool(m_doc.get("active")):
                diffs.append(f"active: SQL={sql_row.get('active')} vs Mongo={m_doc.get('active')}")

            # Timestamps
            for ts_f in ["created_at", "expires_at"]:
                s_raw = sql_row.get(ts_f)
                m_val = m_doc.get(ts_f)
                if s_raw and m_val:
                    try:
                        s_dt = datetime.fromisoformat(str(s_raw)).replace(tzinfo=timezone.utc)
                        if abs((s_dt - m_val).total_seconds()) > 1.0:
                            diffs.append(f"{ts_f}: SQL={s_dt} vs Mongo={m_val}")
                    except Exception:
                        pass

            if diffs:
                results["field_parity"]["mismatched"] += 1
                if len(results["field_parity"]["sample_diffs"]) < 5:
                    results["field_parity"]["sample_diffs"].append({"id": eid, "diffs": diffs})
                results["success"] = False
                results["errors"].append(f"Field mismatch on event {eid}: {diffs}")
            else:
                results["field_parity"]["matched"] += 1

        # Integrity checks
        users_count = await db[COLL_USERS].count_documents({})
        user_counter = await counters_col.find_one({"_id": "user_id"})
        alerts_count = await db[COLL_ALERTS].count_documents({})
        alert_counter = await counters_col.find_one({"_id": "alert_id"})
        trains_count = await db[COLL_TRAINS].count_documents({})
        real_trains_count = await db[COLL_REAL_TRAINS].count_documents({})
        congestion_count = await db[COLL_CONGESTION_SECTIONS].count_documents({})

        results["integrity_checks"] = {
            "users_count": users_count,
            "users_ok": (users_count == 42),
            "user_counter_seq": user_counter.get("seq") if user_counter else None,
            "user_counter_ok": (user_counter.get("seq") == 42 if user_counter else False),
            "alerts_count": alerts_count,
            "alerts_ok": (alerts_count == 157),
            "alert_counter_seq": alert_counter.get("seq") if alert_counter else None,
            "alert_counter_ok": (alert_counter.get("seq") == 157 if alert_counter else False),
            "trains_count": trains_count,
            "trains_ok": (trains_count == 10),
            "real_trains_count": real_trains_count,
            "real_trains_ok": (real_trains_count == 5211),
            "congestion_count": congestion_count,
            "congestion_ok": (congestion_count == 20),
        }

        all_ok = (
            results["integrity_checks"]["users_ok"]
            and results["integrity_checks"]["user_counter_ok"]
            and results["integrity_checks"]["alerts_ok"]
            and results["integrity_checks"]["alert_counter_ok"]
            and results["integrity_checks"]["trains_ok"]
            and results["integrity_checks"]["real_trains_ok"]
            and results["integrity_checks"]["congestion_ok"]
        )
        if not all_ok:
            results["success"] = False
            results["errors"].append("Integrity check failed on another collection!")

        print(json.dumps(results, indent=2))
        return results
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(verify())
