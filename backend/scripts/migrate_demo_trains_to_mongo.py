"""
Migrate Demo Train Master Data from SQLite to MongoDB Atlas.

Phase 4.1:
- Migrates the 10 active/demo trains from 'trains' table in SQLite.
- Embeds their 68 route stops from 'route_stops' table under 'stops: [...]'.
- Uses train_id as explicit MongoDB '_id' and retains 'train_id' as field.
- Ensures unique indexes: train_id, train_number, stops.station_code.
- Uses official PyMongo Async (AsyncMongoClient).
- Completely idempotent and safe to re-run without duplicate documents.
"""

import os
import sys
import sqlite3
import asyncio
from typing import Dict, List, Any

# Ensure reliable DNS resolution on Windows environments before PyMongo connects
try:
    import dns.asyncresolver
    import dns.resolver
    for r in [dns.asyncresolver.get_default_resolver(), dns.resolver.get_default_resolver()]:
        if r and hasattr(r, "nameservers"):
            reliable_ns = ["8.8.8.8", "1.1.1.1"]
            r.nameservers = [ns for ns in reliable_ns if ns not in r.nameservers] + list(r.nameservers)
except Exception:
    pass

from pymongo import AsyncMongoClient, UpdateOne, ASCENDING

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.database.mongodb import COLL_TRAINS


def get_sqlite_connection(db_path: str = "railpulse.db") -> sqlite3.Connection:
    """Open SQLite in read-only mode."""
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


async def ensure_trains_indexes(db) -> None:
    """Ensure indexes on the 'trains' collection."""
    print("[Indexes] Creating/verifying indexes on 'trains'...")
    await db[COLL_TRAINS].create_index([("train_id", ASCENDING)], unique=True)
    await db[COLL_TRAINS].create_index([("train_number", ASCENDING)])
    await db[COLL_TRAINS].create_index([("stops.station_code", ASCENDING)])
    print("[Indexes] 'trains' indexes verified successfully.")


async def migrate_demo_trains() -> Dict[str, Any]:
    """Migrate 10 demo trains with embedded route stops into MongoDB."""
    if not settings.MONGODB_URL:
        raise ValueError("MONGODB_URL is not configured in settings/environment.")

    client = AsyncMongoClient(
        settings.MONGODB_URL,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
    )
    db = client[settings.MONGODB_DB_NAME]
    conn = get_sqlite_connection()

    try:
        await ensure_trains_indexes(db)

        cur = conn.cursor()
        cur.execute("""
            SELECT train_id, train_name, train_number, train_type,
                   source, source_code, destination, destination_code,
                   zone, total_distance_km, scheduled_departure, scheduled_arrival,
                   avg_speed_kmph, max_speed_kmph, days_of_run
            FROM trains
            ORDER BY train_id
        """)
        train_rows = cur.fetchall()
        print(f"\n[Migration] Found {len(train_rows)} demo trains in SQLite.")

        # Fetch route stops grouped by train_id
        cur.execute("""
            SELECT train_id, station_code, station_name,
                   arrival, departure, distance_from_source,
                   day, stop_number, halt_minutes
            FROM route_stops
            ORDER BY train_id, stop_number ASC
        """)
        stops_by_train: Dict[str, List[Dict[str, Any]]] = {}
        total_stops = 0
        for s in cur.fetchall():
            t_id = str(s["train_id"])
            stop_doc = {
                "stop_number": int(s["stop_number"]),
                "station_code": str(s["station_code"]),
                "station_name": str(s["station_name"]),
                "arrival": str(s["arrival"]) if s["arrival"] is not None else None,
                "departure": str(s["departure"]) if s["departure"] is not None else None,
                "distance_from_source": float(s["distance_from_source"] or 0.0),
                "day": int(s["day"] or 1),
                "halt_minutes": int(s["halt_minutes"] if s["halt_minutes"] is not None else 2),
            }
            stops_by_train.setdefault(t_id, []).append(stop_doc)
            total_stops += 1

        print(f"[Migration] Loaded {total_stops} route stops across {len(stops_by_train)} trains from SQLite.")

        ops = []
        for t in train_rows:
            t_id = str(t["train_id"])
            t_stops = stops_by_train.get(t_id, [])

            doc = {
                "_id": t_id,
                "train_id": t_id,
                "train_name": str(t["train_name"]),
                "train_number": str(t["train_number"]),
                "train_type": str(t["train_type"]),
                "source": str(t["source"]),
                "source_code": str(t["source_code"]),
                "destination": str(t["destination"]),
                "destination_code": str(t["destination_code"]),
                "zone": str(t["zone"]),
                "total_distance_km": float(t["total_distance_km"] or 0.0),
                "scheduled_departure": str(t["scheduled_departure"]),
                "scheduled_arrival": str(t["scheduled_arrival"]),
                "avg_speed_kmph": float(t["avg_speed_kmph"] or 60.0),
                "max_speed_kmph": float(t["max_speed_kmph"] or 130.0),
                "days_of_run": str(t["days_of_run"] or "Mon,Tue,Wed,Thu,Fri,Sat,Sun"),
                "stops": t_stops,
            }
            ops.append(UpdateOne({"_id": t_id}, {"$set": doc}, upsert=True))

        result = await db[COLL_TRAINS].bulk_write(ops, ordered=True)
        mongo_count = await db[COLL_TRAINS].count_documents({})
        print(f"[Migration] bulk_write complete. Matched: {result.matched_count}, Upserted: {len(result.upserted_ids)}, Modified: {result.modified_count}")
        print(f"[Migration] Current MongoDB '{COLL_TRAINS}' collection count: {mongo_count}")

        return {
            "sqlite_trains": len(train_rows),
            "sqlite_stops": total_stops,
            "mongo_trains": mongo_count,
            "upserted": len(result.upserted_ids),
            "matched": result.matched_count,
            "modified": result.modified_count,
        }

    finally:
        conn.close()
        await client.close()


if __name__ == "__main__":
    summary = asyncio.run(migrate_demo_trains())
    print(f"\nMigration Summary: {summary}")
