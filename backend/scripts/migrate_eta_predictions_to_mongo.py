"""
Phase 4.4B Migration: ETA Predictions (SQLite → MongoDB Atlas)

Reads all current eta_predictions from SQLite in read-only mode.
Migrates each row to MongoDB as a document keyed on (train_id, station_code)
using UpdateOne upserts so this script is idempotent.

MongoDB document fields preserved exactly:
  train_id, station_code, station_name, scheduled_arrival,
  predicted_arrival, predicted_delay_minutes, confidence,
  confidence_level, factors (native BSON array, semantically
  equivalent to factors_json), created_at

factors_json (text) is parsed into a native BSON array.
The public API never exposes the raw field name 'factors_json';
it always returns a parsed list as 'factors', so the storage
change is invisible to all API consumers.

WARNING: Do NOT re-run this script against live runtime data —
it will overwrite MongoDB predictions with stale SQLite snapshots.
Run only before runtime cutover or against a known-frozen baseline.
"""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pymongo import UpdateOne
from app.database.mongodb import init_mongo, close_mongo, get_mongo_db, COLL_ETA_PREDICTIONS


def _parse_factors(factors_json_str: str) -> list:
    """Parse factors_json text into native Python list."""
    if not factors_json_str:
        return []
    try:
        result = json.loads(factors_json_str)
        if isinstance(result, list):
            return result
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_dt(dt_str) -> datetime:
    """Parse SQLite datetime string to UTC-aware datetime."""
    if dt_str is None:
        return datetime.now(timezone.utc)
    if isinstance(dt_str, datetime):
        if dt_str.tzinfo is None:
            return dt_str.replace(tzinfo=timezone.utc)
        return dt_str
    try:
        dt = datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.now(timezone.utc)


async def run_migration():
    # 1. Locate SQLite
    db_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "railpulse.db")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "railpulse.db")),
    ]
    sqlite_path = None
    for p in db_paths:
        if os.path.exists(p):
            sqlite_path = p
            break
    if not sqlite_path:
        print("[ERROR] railpulse.db not found.")
        return False

    print(f"Reading SQLite eta_predictions from: {sqlite_path}")

    # 2. Read all rows from SQLite (read-only)
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM eta_predictions").fetchall()
    con.close()

    sqlite_count = len(rows)
    print(f"Found {sqlite_count} eta_prediction records in SQLite.")
    if sqlite_count == 0:
        print("[WARN] No records to migrate.")
        return True

    # 3. Connect MongoDB
    await init_mongo()
    db = get_mongo_db()
    if db is None:
        print("[ERROR] MongoDB not connected.")
        return False

    col = db[COLL_ETA_PREDICTIONS]

    # 4. Build bulk upsert operations
    ops = []
    for row in rows:
        factors = _parse_factors(row["factors_json"])
        doc = {
            "train_id": row["train_id"],
            "station_code": row["station_code"],
            "station_name": row["station_name"],
            "scheduled_arrival": row["scheduled_arrival"],
            "predicted_arrival": row["predicted_arrival"],
            "predicted_delay_minutes": float(row["predicted_delay_minutes"] or 0.0),
            "confidence": float(row["confidence"] or 80.0),
            "confidence_level": row["confidence_level"] or "Medium",
            "factors": factors,
            "created_at": _parse_dt(row["created_at"]),
        }
        ops.append(UpdateOne(
            {"train_id": doc["train_id"], "station_code": doc["station_code"]},
            {"$set": doc},
            upsert=True
        ))

    # 5. Execute bulk write
    result = await col.bulk_write(ops, ordered=False)
    print(f"Bulk write: matched={result.matched_count}, upserted={result.upserted_count}, modified={result.modified_count}")

    # 6. Verify final count
    mongo_count = await col.count_documents({})
    print(f"Total documents in MongoDB '{COLL_ETA_PREDICTIONS}': {mongo_count}")

    # 7. Idempotency check
    print("\nRunning idempotency check...")
    result2 = await col.bulk_write(ops, ordered=False)
    print(f"Idempotency: matched={result2.matched_count}, upserted={result2.upserted_count}, modified={result2.modified_count}")
    assert result2.upserted_count == 0, "Idempotency FAILED: unexpected upserts"

    await close_mongo()
    print(f"\n[SUCCESS] eta_predictions migration finished. SQLite={sqlite_count}, MongoDB={mongo_count}")
    return True


if __name__ == "__main__":
    success = asyncio.run(run_migration())
    if not success:
        sys.exit(1)
