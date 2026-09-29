"""
Phase 4.4A: Migrate train_positions persistence from SQLite to MongoDB Atlas.
Reads current train_positions from SQLite in read-only mode and upserts into MongoDB.
Preserves all 16 fields, numeric precision, and timestamps.
Idempotent and safe to run.
"""

import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pymongo import UpdateOne
from app.database.mongodb import (
    init_mongo,
    close_mongo,
    get_mongo_db,
    COLL_TRAIN_POSITIONS,
)
from app.config import settings


def find_sqlite_db_path() -> str:
    """Find authoritative SQLite database file path."""
    possible_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "railpulse.db")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "railpulse.db")),
        os.path.abspath("railpulse.db"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            # Check if train_positions has rows in this db
            try:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                cnt = con.execute("SELECT COUNT(*) FROM train_positions").fetchone()[0]
                con.close()
                if cnt > 0:
                    return p
            except Exception:
                continue
    for p in possible_paths:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Could not find SQLite database railpulse.db")


def parse_datetime(dt_val: Any) -> datetime:
    """Safely parse SQLite datetime value into UTC datetime."""
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val
    if isinstance(dt_val, str):
        # Format usually 'YYYY-MM-DD HH:MM:SS.ffffff' or ISO
        dt_str = dt_val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def read_sqlite_train_positions(db_path: str) -> List[Dict[str, Any]]:
    """Read train_positions from SQLite database in read-only mode."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            train_id,
            latitude,
            longitude,
            speed_kmph,
            delay_minutes,
            status,
            current_station_code,
            current_station_name,
            next_station_code,
            next_station_name,
            distance_covered_km,
            total_distance_km,
            last_updated,
            current_stop_index,
            at_station,
            dwell_remaining_seconds
        FROM train_positions
        ORDER BY train_id ASC
    """)
    rows = cursor.fetchall()
    positions = []
    for r in rows:
        positions.append({
            "_id": str(r["train_id"]),
            "train_id": str(r["train_id"]),
            "latitude": float(r["latitude"]) if r["latitude"] is not None else 0.0,
            "longitude": float(r["longitude"]) if r["longitude"] is not None else 0.0,
            "speed_kmph": float(r["speed_kmph"]) if r["speed_kmph"] is not None else 0.0,
            "delay_minutes": float(r["delay_minutes"]) if r["delay_minutes"] is not None else 0.0,
            "status": str(r["status"]) if r["status"] is not None else "On Time",
            "current_station_code": str(r["current_station_code"]) if r["current_station_code"] is not None else None,
            "current_station_name": str(r["current_station_name"]) if r["current_station_name"] is not None else None,
            "next_station_code": str(r["next_station_code"]) if r["next_station_code"] is not None else None,
            "next_station_name": str(r["next_station_name"]) if r["next_station_name"] is not None else None,
            "distance_covered_km": float(r["distance_covered_km"]) if r["distance_covered_km"] is not None else 0.0,
            "total_distance_km": float(r["total_distance_km"]) if r["total_distance_km"] is not None else 0.0,
            "last_updated": parse_datetime(r["last_updated"]),
            "current_stop_index": int(r["current_stop_index"]) if r["current_stop_index"] is not None else 0,
            "at_station": bool(r["at_station"]),
            "dwell_remaining_seconds": float(r["dwell_remaining_seconds"]) if r["dwell_remaining_seconds"] is not None else 0.0,
        })
    conn.close()
    return positions


async def migrate_positions():
    """Migrate train positions from SQLite to MongoDB Atlas."""
    db_path = find_sqlite_db_path()
    print(f"Reading SQLite train positions from: {db_path}")
    positions = read_sqlite_train_positions(db_path)
    print(f"Found {len(positions)} train position records in SQLite.")

    if len(positions) != 10:
        print(f"[WARNING] Expected exactly 10 demo train positions, found {len(positions)}.")

    await init_mongo()
    db = get_mongo_db()
    if db is None:
        print("[ERROR] Failed to connect to MongoDB.")
        return 1

    coll = db[COLL_TRAIN_POSITIONS]

    bulk_ops = [
        UpdateOne(
            {"_id": pos["_id"]},
            {"$set": pos},
            upsert=True
        )
        for pos in positions
    ]

    result = await coll.bulk_write(bulk_ops, ordered=False)
    print(f"Bulk write completed: matched={result.matched_count}, upserted={len(result.upserted_ids)}, modified={result.modified_count}")

    mongo_count = await coll.count_documents({})
    print(f"Total documents in MongoDB '{COLL_TRAIN_POSITIONS}': {mongo_count}")

    await close_mongo()
    print("[SUCCESS] Train positions migration finished.")
    return 0


if __name__ == "__main__":
    code = asyncio.run(migrate_positions())
    sys.exit(code)
