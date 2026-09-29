"""
Migrate Users and Initialize Counters from SQLite to MongoDB Atlas.

Migrates:
- All 42 existing users from railpulse.db (read-only) into MongoDB 'users' collection.
- Initializes atomic 'user_id' counter in 'counters' collection to seq = 42.
- Ensures unique indexes on 'phone' and 'id'.

Uses PyMongo Async (AsyncMongoClient).
Idempotent and safe to re-run.
"""

import os
import sys
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List

from pymongo import AsyncMongoClient, UpdateOne, ASCENDING

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


async def migrate_users() -> Dict[str, Any]:
    if not settings.MONGODB_URL:
        raise ValueError("MONGODB_URL is not set.")

    client = AsyncMongoClient(settings.MONGODB_URL, serverSelectionTimeoutMS=8000)
    db = client[settings.MONGODB_DB_NAME]
    conn = get_sqlite_connection()

    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM users")
        sqlite_count = cur.fetchone()[0]
        print(f"[Users Migration] Found {sqlite_count} users in SQLite.")

        cur.execute("""
            SELECT id, name, phone, password_hash, role, created_at
            FROM users
            ORDER BY id
        """)
        user_rows = cur.fetchall()

        # 1. Ensure indexes on MongoDB
        await db.users.create_index([("phone", ASCENDING)], unique=True)
        await db.users.create_index([("id", ASCENDING)], unique=True)
        print("[Users Migration] Unique indexes on phone and id verified.")

        # 2. Build bulk upsert operations
        ops = []
        max_id = 0
        for r in user_rows:
            uid = int(r["id"])
            if uid > max_id:
                max_id = uid

            # Parse created_at
            raw_ca = r["created_at"]
            if raw_ca:
                try:
                    if isinstance(raw_ca, str):
                        ca_dt = datetime.fromisoformat(raw_ca.replace("Z", "+00:00"))
                    else:
                        ca_dt = raw_ca
                except Exception:
                    ca_dt = datetime.now(timezone.utc)
            else:
                ca_dt = datetime.now(timezone.utc)

            doc = {
                "id": uid,
                "name": str(r["name"]).strip(),
                "phone": str(r["phone"]).strip(),
                "password_hash": str(r["password_hash"]).strip(),
                "role": str(r["role"]).strip().upper(),
                "created_at": ca_dt.isoformat() if hasattr(ca_dt, "isoformat") else str(ca_dt),
            }

            ops.append(UpdateOne({"id": uid}, {"$set": doc}, upsert=True))

        if ops:
            await db.users.bulk_write(ops, ordered=True)
            print(f"[Users Migration] Successfully upserted {len(ops)} users into MongoDB.")

        # 3. Initialize atomic counter
        print(f"[Users Migration] Initializing counter 'user_id' with seq = {max_id}...")
        await db.counters.update_one(
            {"_id": "user_id"},
            {"$set": {"seq": max_id}},
            upsert=True
        )
        counter_doc = await db.counters.find_one({"_id": "user_id"})
        print(f"[Users Migration] Counter 'user_id' initialized: {counter_doc}")

        mongo_count = await db.users.count_documents({})
        return {
            "success": True,
            "sqlite_users": sqlite_count,
            "mongo_users": mongo_count,
            "max_id": max_id,
            "counter_seq": counter_doc.get("seq") if counter_doc else None,
        }
    finally:
        conn.close()
        await client.close()


if __name__ == "__main__":
    res = asyncio.run(migrate_users())
    print("Result:", res)
