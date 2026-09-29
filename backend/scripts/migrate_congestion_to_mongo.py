"""
Phase 4.2A: Migrate SQLite congestion_sections to MongoDB Atlas.

Reads the 20 congestion sections from SQLite in read-only mode and upserts
them into the MongoDB 'congestion_sections' collection.

Safety:
- SQLite database is accessed read-only (URI mode=ro).
- Idempotent: uses bulk_write with UpdateOne(..., upsert=True).
- Preserves exact section_id, names, scores, speeds, active_trains, status, delay_impact_minutes.
- Does NOT modify any other collection.
"""

import asyncio
import os
import sys
import sqlite3
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
from app.database.mongodb import COLL_CONGESTION_SECTIONS


def read_sqlite_congestion_sections(sqlite_path: str) -> List[Dict[str, Any]]:
    """Read all congestion sections from SQLite read-only."""
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
    sections = [dict(r) for r in rows]
    conn.close()
    return sections


async def migrate_congestion_sections():
    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME", "railpulse")

    if not mongo_url:
        print("[Error] MONGODB_URL environment variable is missing.")
        sys.exit(1)

    sqlite_path = os.path.join(backend_dir, "railpulse.db")
    if not os.path.exists(sqlite_path):
        print(f"[Error] SQLite database not found at {sqlite_path}")
        sys.exit(1)

    sections = read_sqlite_congestion_sections(sqlite_path)
    print(f"[Migration] Read {len(sections)} congestion sections from SQLite.")

    client = AsyncMongoClient(mongo_url)
    try:
        db = client[db_name]
        col = db[COLL_CONGESTION_SECTIONS]

        # Verify/create index on section_id
        await col.create_index([("section_id", 1)], unique=True)

        operations = []
        for sec in sections:
            doc = {
                "_id": sec["section_id"],
                "section_id": sec["section_id"],
                "from_station": sec["from_station"],
                "to_station": sec["to_station"],
                "from_station_name": sec.get("from_station_name", ""),
                "to_station_name": sec.get("to_station_name", ""),
                "congestion_score": float(sec["congestion_score"]),
                "avg_speed_kmph": float(sec["avg_speed_kmph"]),
                "active_trains": int(sec["active_trains"]),
                "status": sec["status"],
                "delay_impact_minutes": float(sec["delay_impact_minutes"]),
            }
            operations.append(
                UpdateOne(
                    {"_id": sec["section_id"]},
                    {"$set": doc},
                    upsert=True,
                )
            )

        if operations:
            result = await col.bulk_write(operations, ordered=False)
            print(f"[Migration] bulk_write complete. Matched: {result.matched_count}, Upserted: {len(result.upserted_ids)}, Modified: {result.modified_count}")

        final_count = await col.count_documents({})
        print(f"[Migration] Current MongoDB '{COLL_CONGESTION_SECTIONS}' collection count: {final_count}")

        summary = {
            "sqlite_sections": len(sections),
            "mongo_sections": final_count,
            "upserted": len(result.upserted_ids) if operations else 0,
            "matched": result.matched_count if operations else 0,
            "modified": result.modified_count if operations else 0,
        }
        print(f"\nMigration Summary: {summary}")
        return summary
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(migrate_congestion_sections())
