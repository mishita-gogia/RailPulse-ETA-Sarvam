"""
Phase 4.2B: Migrate SQLite alerts to MongoDB Atlas.

Reads all alerts from SQLite in read-only mode, upserts them into
the MongoDB 'alerts' collection, and seeds the 'alert_id' counter.

Safety:
- SQLite database is accessed read-only (URI mode=ro).
- Idempotent: uses bulk_write with UpdateOne(..., upsert=True) on _id == id.
- Computes max alert ID dynamically (does NOT hard-code).
- Seeds counters with {"_id": "alert_id", "seq": max_id}.
- Does NOT modify any other collection.
"""

import asyncio
import os
import sys
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Any
from dotenv import load_dotenv

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

load_dotenv(os.path.join(backend_dir, ".env"))

# DNS fallback for Windows dnspython
import dns.asyncresolver
try:
    dns.asyncresolver.get_default_resolver().nameservers = [
        "8.8.8.8", "1.1.1.1"
    ] + dns.asyncresolver.get_default_resolver().nameservers
except Exception:
    pass

from pymongo import AsyncMongoClient, UpdateOne
from app.database.mongodb import COLL_ALERTS, COLL_COUNTERS


def read_sqlite_alerts(sqlite_path: str) -> List[Dict[str, Any]]:
    """Read all alerts from SQLite read-only."""
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
    alerts = [dict(r) for r in rows]
    conn.close()
    return alerts


async def migrate_alerts():
    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME", "railpulse")

    if not mongo_url:
        print("[Error] MONGODB_URL environment variable is missing.")
        sys.exit(1)

    sqlite_path = os.path.join(backend_dir, "railpulse.db")
    if not os.path.exists(sqlite_path):
        print(f"[Error] SQLite database not found at {sqlite_path}")
        sys.exit(1)

    alerts = read_sqlite_alerts(sqlite_path)
    count = len(alerts)
    max_id = max([a["id"] for a in alerts]) if alerts else 0
    print(f"[Migration] Read {count} alerts from SQLite. Maximum alert ID: {max_id}")

    client = AsyncMongoClient(mongo_url)
    try:
        db = client[db_name]
        col = db[COLL_ALERTS]
        counters_col = db[COLL_COUNTERS]

        # Verify/create indexes
        await col.create_index([("id", 1)], unique=True)
        await col.create_index([("created_at", -1)])
        await col.create_index([("acknowledged", 1), ("created_at", -1)])
        await col.create_index([("train_id", 1), ("created_at", -1)])

        operations = []
        for a in alerts:
            # Parse created_at timestamp
            raw_ts = a.get("created_at")
            if isinstance(raw_ts, str):
                try:
                    dt = datetime.fromisoformat(raw_ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)
            elif isinstance(raw_ts, datetime):
                dt = raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)
            else:
                dt = datetime.now(timezone.utc)

            doc = {
                "_id": int(a["id"]),
                "id": int(a["id"]),
                "train_id": str(a.get("train_id") or ""),
                "train_name": str(a.get("train_name") or ""),
                "severity": str(a.get("severity") or "info"),
                "alert_type": str(a.get("alert_type") or "delay"),
                "message": str(a.get("message") or ""),
                "location": str(a.get("location") or "En route"),
                "eta_impact_minutes": float(a.get("eta_impact_minutes") or 0.0),
                "created_at": dt,
                "acknowledged": bool(a.get("acknowledged", False)),
            }

            operations.append(
                UpdateOne(
                    {"_id": int(a["id"])},
                    {"$set": doc},
                    upsert=True,
                )
            )

        if operations:
            result = await col.bulk_write(operations, ordered=False)
            print(f"[Migration] bulk_write complete. Matched: {result.matched_count}, Upserted: {len(result.upserted_ids)}, Modified: {result.modified_count}")

        final_count = await col.count_documents({})
        print(f"[Migration] Current MongoDB '{COLL_ALERTS}' collection count: {final_count}")

        # Seed or update alert_id counter to max_id if current seq is lower
        existing_counter = await counters_col.find_one({"_id": "alert_id"})
        if not existing_counter or existing_counter.get("seq", 0) < max_id:
            await counters_col.update_one(
                {"_id": "alert_id"},
                {"$set": {"seq": max_id}},
                upsert=True,
            )
            print(f"[Migration] Initialized counter 'alert_id' to seq={max_id}")
        else:
            print(f"[Migration] Existing counter 'alert_id' retained at seq={existing_counter['seq']}")

        summary = {
            "sqlite_alerts": count,
            "max_id": max_id,
            "mongo_alerts": final_count,
            "upserted": len(result.upserted_ids) if operations else 0,
            "matched": result.matched_count if operations else 0,
            "modified": result.modified_count if operations else 0,
        }
        print(f"\nMigration Summary: {summary}")
        return summary
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(migrate_alerts())
