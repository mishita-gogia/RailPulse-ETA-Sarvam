"""
Phase 4.3: Migrate SQLite operational_events to MongoDB Atlas.

Reads all operational events from SQLite in read-only mode, upserts them into
the MongoDB 'operational_events' collection, and seeds the 'event_id' counter.

Safety:
- SQLite database is accessed read-only (URI mode=ro).
- Idempotent: uses bulk_write with UpdateOne(..., upsert=True) on _id == id.
- Computes max event ID dynamically (does NOT hard-code 22 or 28).
- Seeds counters with {"_id": "event_id", "seq": max_id}.
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
repo_dir = os.path.abspath(os.path.join(backend_dir, ".."))
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
from app.database.mongodb import COLL_OPERATIONAL_EVENTS, COLL_COUNTERS


def find_sqlite_db_path() -> str:
    """Find the authoritative railpulse.db path."""
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


def read_sqlite_events(sqlite_path: str) -> List[Dict[str, Any]]:
    """Read all operational events from SQLite read-only."""
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
    events = [dict(r) for r in rows]
    conn.close()
    return events


def parse_timestamp(raw_ts: Any) -> Any:
    if not raw_ts:
        return None
    if isinstance(raw_ts, str):
        try:
            dt = datetime.fromisoformat(raw_ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    if isinstance(raw_ts, datetime):
        return raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)
    return None


async def migrate_operational_events():
    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME", "railpulse")

    if not mongo_url:
        print("[Error] MONGODB_URL environment variable is missing.")
        sys.exit(1)

    sqlite_path = find_sqlite_db_path()
    if not os.path.exists(sqlite_path):
        print(f"[Error] SQLite database not found at {sqlite_path}")
        sys.exit(1)

    events = read_sqlite_events(sqlite_path)
    count = len(events)
    max_id = max([e["id"] for e in events]) if events else 0
    print(f"[Migration] Read {count} operational events from SQLite ({sqlite_path}). Maximum ID: {max_id}")

    client = AsyncMongoClient(mongo_url)
    try:
        db = client[db_name]
        col = db[COLL_OPERATIONAL_EVENTS]
        counters_col = db[COLL_COUNTERS]

        # Verify/create indexes
        await col.create_index([("id", 1)], unique=True)
        await col.create_index([("active", 1), ("train_id", 1)])
        await col.create_index([("expires_at", 1)])

        operations = []
        for e in events:
            dt_created = parse_timestamp(e.get("created_at")) or datetime.now(timezone.utc)
            dt_expires = parse_timestamp(e.get("expires_at"))

            doc = {
                "_id": int(e["id"]),
                "id": int(e["id"]),
                "event_type": str(e.get("event_type") or ""),
                "train_id": str(e.get("train_id") or ""),
                "location": str(e.get("location") or "En route"),
                "severity": float(e.get("severity") or 0.5),
                "duration_minutes": int(e.get("duration_minutes") or 30),
                "description": str(e.get("description") or ""),
                "impact_delay_minutes": float(e.get("impact_delay_minutes") or 0.0),
                "active": bool(e.get("active", True)),
                "created_at": dt_created,
                "expires_at": dt_expires,
            }

            operations.append(
                UpdateOne(
                    {"_id": int(e["id"])},
                    {"$set": doc},
                    upsert=True,
                )
            )

        if operations:
            result = await col.bulk_write(operations, ordered=False)
            print(f"[Migration] bulk_write complete. Matched: {result.matched_count}, Upserted: {len(result.upserted_ids)}, Modified: {result.modified_count}")

        final_count = await col.count_documents({})
        print(f"[Migration] Current MongoDB '{COLL_OPERATIONAL_EVENTS}' collection count: {final_count}")

        # Seed or update event_id counter to max_id if current seq is lower
        existing_counter = await counters_col.find_one({"_id": "event_id"})
        if not existing_counter or existing_counter.get("seq", 0) < max_id:
            await counters_col.update_one(
                {"_id": "event_id"},
                {"$set": {"seq": max_id}},
                upsert=True,
            )
            print(f"[Migration] Initialized counter 'event_id' to seq={max_id}")
        else:
            print(f"[Migration] Existing counter 'event_id' retained at seq={existing_counter['seq']}")

        summary = {
            "sqlite_events": count,
            "max_id": max_id,
            "mongo_events": final_count,
            "upserted": len(result.upserted_ids) if operations else 0,
            "matched": result.matched_count if operations else 0,
            "modified": result.modified_count if operations else 0,
        }
        print(f"\nMigration Summary: {summary}")
        return summary
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(migrate_operational_events())
