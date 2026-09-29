"""
Migrate Static Railway Master Data from SQLite to MongoDB Atlas.

Migrates:
1. All 8,704 Stations (with exact coordinates and properties).
2. All 5,211 Real Trains (with master timetable attributes).
3. All 417,130 Real Train Stops (embedded inside parent train documents as stops: [...]).

Uses the official PyMongo Async driver (AsyncMongoClient) with chunked bulk_write.
Idempotent and safe to re-run.
"""

import os
import sys
import time
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from pymongo import AsyncMongoClient, UpdateOne, ASCENDING, GEOSPHERE

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings


def get_sqlite_connection(db_path: str = "railpulse.db") -> sqlite3.Connection:
    """Open SQLite in read-only mode to prevent any modification to the source database."""
    if not os.path.exists(db_path):
        # Fallback to backend/railpulse.db if run from backend directory
        alt_path = os.path.join(os.path.dirname(__file__), "..", "railpulse.db")
        if os.path.exists(alt_path):
            db_path = alt_path
        else:
            alt_root = os.path.join(os.path.dirname(__file__), "..", "..", "railpulse.db")
            if os.path.exists(alt_root):
                db_path = alt_root

    # Open read-only URI
    uri_path = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri_path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


async def ensure_indexes(db) -> None:
    """Create all required indexes for stations and real_trains."""
    print("[Indexes] Creating/verifying indexes on stations...")
    await db.stations.create_index([("station_code", ASCENDING)], unique=True)
    await db.stations.create_index([("station_name", ASCENDING)])
    try:
        await db.stations.create_index([("location", GEOSPHERE)])
    except Exception as e:
        print(f"[Indexes] Notice for 2dsphere index: {e}")

    print("[Indexes] Creating/verifying indexes on real_trains...")
    await db.real_trains.create_index([("train_number", ASCENDING)], unique=True)
    await db.real_trains.create_index([("train_name", ASCENDING)])
    await db.real_trains.create_index([("source_station", ASCENDING)])
    await db.real_trains.create_index([("destination_station", ASCENDING)])
    await db.real_trains.create_index([("stops.station_code", ASCENDING)])
    print("[Indexes] Index verification complete.")


async def migrate_stations(conn: sqlite3.Connection, db, batch_size: int = 1000) -> int:
    """Migrate all 8,704 stations into the stations collection using idempotent bulk upserts."""
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM stations")
    total_stations = cur.fetchone()[0]
    print(f"\n--- Migrating {total_stations} Stations ---")

    cur.execute("""
        SELECT station_code, station_name, city, state, zone,
               latitude, longitude, platform_count, is_junction
        FROM stations
        ORDER BY station_code
    """)

    ops = []
    migrated_count = 0

    for row in cur:
        lat = float(row["latitude"]) if row["latitude"] is not None else 0.0
        lon = float(row["longitude"]) if row["longitude"] is not None else 0.0

        doc = {
            "_id": str(row["station_code"]).strip().upper(),
            "station_code": str(row["station_code"]).strip().upper(),
            "station_name": str(row["station_name"]).strip(),
            "city": str(row["city"] or "").strip(),
            "state": str(row["state"] or "").strip(),
            "zone": str(row["zone"] or "").strip(),
            "latitude": lat,
            "longitude": lon,
            "platform_count": int(row["platform_count"]) if row["platform_count"] is not None else 4,
            "is_junction": bool(row["is_junction"]),
        }

        # Add GeoJSON point if coordinates are within valid range
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and (lat != 0.0 or lon != 0.0):
            doc["location"] = {
                "type": "Point",
                "coordinates": [lon, lat]
            }

        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True))

        if len(ops) >= batch_size:
            await db.stations.bulk_write(ops, ordered=False)
            migrated_count += len(ops)
            ops = []
            print(f"  Stations progress: {migrated_count}/{total_stations}...")

    if ops:
        await db.stations.bulk_write(ops, ordered=False)
        migrated_count += len(ops)

    print(f"  Stations migration complete: {migrated_count} stations migrated.")
    return migrated_count


async def migrate_real_trains_with_stops(conn: sqlite3.Connection, db, train_batch_size: int = 500) -> Dict[str, int]:
    """
    Migrate all 5,211 real trains with their 417,130 timetable stops embedded.
    Reads in batches of train_batch_size trains to maintain constant low memory usage.
    """
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM real_trains")
    total_trains = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM real_train_stops")
    total_stops = cur.fetchone()[0]

    print(f"\n--- Migrating {total_trains} Real Trains with {total_stops} Embedded Stops ---")

    # Get all distinct train_numbers ordered
    cur.execute("SELECT train_number FROM real_trains ORDER BY train_number")
    all_train_numbers = [r[0] for r in cur.fetchall()]

    migrated_trains = 0
    migrated_stops = 0

    for i in range(0, len(all_train_numbers), train_batch_size):
        chunk_train_numbers = all_train_numbers[i : i + train_batch_size]

        # 1. Fetch train records for this chunk
        placeholders = ",".join("?" for _ in chunk_train_numbers)
        cur.execute(f"""
            SELECT train_number, train_name, train_type,
                   source_station, source_station_name,
                   destination_station, destination_station_name,
                   distance, running_days, data_source, last_updated
            FROM real_trains
            WHERE train_number IN ({placeholders})
            ORDER BY train_number
        """, chunk_train_numbers)
        train_rows = cur.fetchall()

        # 2. Fetch all stops for this chunk of trains
        cur.execute(f"""
            SELECT train_number, sequence, station_code, station_name,
                   arrival_time, departure_time, halt_minutes, day_offset, distance
            FROM real_train_stops
            WHERE train_number IN ({placeholders})
            ORDER BY train_number, sequence
        """, chunk_train_numbers)
        stop_rows = cur.fetchall()

        # 3. Group stops by train_number in memory
        stops_by_train: Dict[str, List[dict]] = {}
        for s in stop_rows:
            t_num = str(s["train_number"]).strip()
            dist_val = float(s["distance"]) if s["distance"] is not None else None
            stop_doc = {
                "sequence": int(s["sequence"]),
                "station_code": str(s["station_code"]).strip().upper(),
                "station_name": str(s["station_name"]).strip(),
                "arrival_time": s["arrival_time"],
                "departure_time": s["departure_time"],
                "halt_minutes": int(s["halt_minutes"]) if s["halt_minutes"] is not None else 2,
                "day_offset": int(s["day_offset"]) if s["day_offset"] is not None else 1,
                "distance": dist_val,
            }
            stops_by_train.setdefault(t_num, []).append(stop_doc)

        # 4. Build bulk UpdateOne operations for this chunk
        ops = []
        for t in train_rows:
            t_num = str(t["train_number"]).strip()
            train_stops = stops_by_train.get(t_num, [])

            dist_val = float(t["distance"]) if t["distance"] is not None else 0.0

            doc = {
                "_id": t_num,
                "train_number": t_num,
                "train_name": str(t["train_name"]).strip(),
                "train_type": str(t["train_type"] or "Superfast").strip(),
                "source_station": str(t["source_station"]).strip().upper(),
                "source_station_name": str(t["source_station_name"] or "").strip(),
                "destination_station": str(t["destination_station"]).strip().upper(),
                "destination_station_name": str(t["destination_station_name"] or "").strip(),
                "distance": dist_val,
                "running_days": str(t["running_days"] or "Daily").strip(),
                "data_source": str(t["data_source"] or "NTES / DataMeet").strip(),
                "last_updated": t["last_updated"] or datetime.now(timezone.utc).isoformat(),
                "stops": train_stops,
            }
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True))
            migrated_stops += len(train_stops)

        if ops:
            await db.real_trains.bulk_write(ops, ordered=False)
            migrated_trains += len(ops)
            print(f"  Real trains progress: {migrated_trains}/{total_trains} trains, {migrated_stops} stops...")

    print(f"  Real trains migration complete: {migrated_trains} trains, {migrated_stops} embedded stops.")
    return {"trains": migrated_trains, "stops": migrated_stops}


async def run_migration() -> Dict[str, Any]:
    """Execute complete static data migration and return results."""
    start_time = time.time()

    # Verify MONGODB_URL
    if not settings.MONGODB_URL:
        raise ValueError("MONGODB_URL is not set in environment or backend/.env!")

    client = AsyncMongoClient(
        settings.MONGODB_URL,
        serverSelectionTimeoutMS=8000,
        maxPoolSize=50,
    )
    # Ping
    await client.admin.command("ping")
    db = client[settings.MONGODB_DB_NAME]

    # Open SQLite read-only
    conn = get_sqlite_connection()

    try:
        # Create indexes
        await ensure_indexes(db)

        # Migrate stations
        stations_migrated = await migrate_stations(conn, db)

        # Migrate real trains + embedded stops
        trains_result = await migrate_real_trains_with_stops(conn, db)

        duration = time.time() - start_time
        return {
            "success": True,
            "stations_migrated": stations_migrated,
            "trains_migrated": trains_result["trains"],
            "stops_migrated": trains_result["stops"],
            "duration_seconds": round(duration, 2),
        }
    finally:
        conn.close()
        await client.close()


if __name__ == "__main__":
    result = asyncio.run(run_migration())
    print("\nMigration summary:", result)
