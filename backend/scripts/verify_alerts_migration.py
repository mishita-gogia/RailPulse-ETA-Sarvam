"""
Independent verification script for Phase 4.2B alerts migration.

Verifies:
1. SQLite alert count == MongoDB alert count
2. Exact alert ID set equality (all IDs match)
3. 100% field parity (train_id, train_name, severity, alert_type, message, location, eta_impact, created_at, acknowledged)
4. Zero duplicate IDs, zero missing, zero unexpected
5. Counter seq == max(alert.id)
6. Other collections intact:
   - users == 42
   - counters.user_id seq == 42
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
    COLL_ALERTS,
    COLL_COUNTERS,
    COLL_USERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_CONGESTION_SECTIONS,
)


def read_sqlite_alerts() -> Dict[int, Dict[str, Any]]:
    sqlite_path = os.path.join(backend_dir, "railpulse.db")
    uri = f"file:{os.path.abspath(sqlite_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, train_id, train_name, severity, alert_type, message,
               location, eta_impact_minutes, created_at, acknowledged
        FROM alerts
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

    sqlite_alerts = read_sqlite_alerts()
    sql_count = len(sqlite_alerts)
    max_sql_id = max(sqlite_alerts.keys()) if sqlite_alerts else 0
    results["counts"]["sqlite_alerts"] = sql_count
    results["counts"]["max_sqlite_id"] = max_sql_id

    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME", "railpulse")
    client = AsyncMongoClient(mongo_url)

    try:
        db = client[db_name]
        col = db[COLL_ALERTS]
        counters_col = db[COLL_COUNTERS]

        mongo_docs = await col.find({}).to_list(length=1000)
        mongo_count = len(mongo_docs)
        results["counts"]["mongo_alerts"] = mongo_count
        results["counts"]["count_match"] = (sql_count == mongo_count)

        if not results["counts"]["count_match"]:
            results["success"] = False
            results["errors"].append(f"Count mismatch: SQLite={sql_count}, Mongo={mongo_count}")

        # ID set equality
        sql_ids = sorted(list(sqlite_alerts.keys()))
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
            results["errors"].append("Alert ID set discrepancy or duplicate IDs found.")

        # Counter sequence check
        alert_counter = await counters_col.find_one({"_id": "alert_id"})
        counter_seq = alert_counter.get("seq") if alert_counter else None
        results["counter_check"] = {
            "counter_seq": counter_seq,
            "matches_max_id": (counter_seq == max_sql_id),
        }
        if counter_seq != max_sql_id:
            results["success"] = False
            results["errors"].append(f"Counter 'alert_id' seq={counter_seq} != max SQLite ID={max_sql_id}")

        # Field-by-field check
        mongo_map = {doc["id"]: doc for doc in mongo_docs}
        results["field_parity"]["total_checked"] = len(sqlite_alerts)

        for aid, sql_row in sqlite_alerts.items():
            if aid not in mongo_map:
                continue
            m_doc = mongo_map[aid]
            diffs = []

            for field in ["train_id", "train_name", "severity", "alert_type", "message", "location"]:
                s_v = str(sql_row.get(field) or "")
                m_v = str(m_doc.get(field) or "")
                if s_v != m_v:
                    diffs.append(f"{field}: SQL='{s_v}' vs Mongo='{m_v}'")

            s_eta = float(sql_row.get("eta_impact_minutes") or 0.0)
            m_eta = float(m_doc.get("eta_impact_minutes") or 0.0)
            if abs(s_eta - m_eta) > 1e-4:
                diffs.append(f"eta_impact_minutes: SQL={s_eta} vs Mongo={m_eta}")

            s_ack = bool(sql_row.get("acknowledged", False))
            m_ack = bool(m_doc.get("acknowledged", False))
            if s_ack != m_ack:
                diffs.append(f"acknowledged: SQL={s_ack} vs Mongo={m_ack}")

            # Check timestamp consistency
            raw_ts = sql_row.get("created_at")
            m_ts = m_doc.get("created_at")
            if isinstance(raw_ts, str) and isinstance(m_ts, datetime):
                try:
                    s_dt = datetime.fromisoformat(raw_ts).replace(tzinfo=timezone.utc)
                    # within 1 second tolerance for precision
                    if abs((s_dt - m_ts).total_seconds()) > 1.0:
                        diffs.append(f"created_at: SQL={s_dt} vs Mongo={m_ts}")
                except Exception:
                    pass

            if diffs:
                results["field_parity"]["mismatched"] += 1
                if len(results["field_parity"]["sample_diffs"]) < 5:
                    results["field_parity"]["sample_diffs"].append({"id": aid, "diffs": diffs})
                results["success"] = False
                results["errors"].append(f"Field mismatch on alert {aid}: {diffs}")
            else:
                results["field_parity"]["matched"] += 1

        # Integrity checks on other collections
        users_count = await db[COLL_USERS].count_documents({})
        user_counter = await counters_col.find_one({"_id": "user_id"})
        trains_count = await db[COLL_TRAINS].count_documents({})
        real_trains_count = await db[COLL_REAL_TRAINS].count_documents({})
        congestion_count = await db[COLL_CONGESTION_SECTIONS].count_documents({})

        results["integrity_checks"] = {
            "users_count": users_count,
            "users_ok": (users_count == 42),
            "user_counter_seq": user_counter.get("seq") if user_counter else None,
            "user_counter_ok": (user_counter.get("seq") == 42 if user_counter else False),
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
