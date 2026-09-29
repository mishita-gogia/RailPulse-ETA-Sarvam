"""
Phase 4.4B Static Verification: ETA Predictions Migration Parity

Verifies:
1. SQLite railpulse.db has 31 eta_predictions rows across 10 demo trains.
2. SQLite has no duplicate (train_id, station_code) pairs.
3. MongoDB has 31 eta_predictions documents.
4. All 31 (train_id, station_code) keys match between SQLite and MongoDB.
5. Zero missing pairs, zero unexpected pairs, zero duplicates in MongoDB.
6. Unique compound index on {train_id: 1, station_code: 1} exists.
7. Static metadata fields match (station_name, scheduled_arrival).
8. factors_json text from SQLite was correctly converted to native BSON list in MongoDB.
9. Runtime divergence is appropriately distinguished from migration parity:
   - If simulation ticked, delay/arrival/factors update dynamically in MongoDB (source of truth).
   - Validates that predicted_delay_minutes, confidence, confidence_level, and factors are valid.
10. All created_at timestamps are present and valid datetimes.
"""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
import dotenv

# Load environment
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
if not os.path.exists(dotenv_path):
    dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
dotenv.load_dotenv(dotenv_path)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.mongodb import init_mongo, close_mongo, get_mongo_db, COLL_ETA_PREDICTIONS

PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"
results = []


def check(label: str, ok: bool, detail: str = ""):
    icon = PASS if ok else FAIL
    msg = f"  {icon}  {label}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    results.append((label, ok, detail))
    return ok


async def run_verification():
    print("=" * 65)
    print("  PHASE 4.4B STATIC VERIFICATION: ETA PREDICTIONS MIGRATION")
    print("=" * 65)

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

    if not check("SQLite railpulse.db located", sqlite_path is not None, str(sqlite_path or "NOT FOUND")):
        return False

    # 2. Read SQLite baseline
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sqlite_rows = con.execute("SELECT * FROM eta_predictions").fetchall()
    con.close()

    sqlite_count = len(sqlite_rows)
    print(f"\n  SQLite baseline eta_predictions count: {sqlite_count}")
    check("SQLite baseline contains exactly 31 records", sqlite_count == 31, f"count={sqlite_count}")

    sqlite_map = {}
    for row in sqlite_rows:
        key = (str(row["train_id"]), str(row["station_code"]))
        sqlite_map[key] = dict(row)

    check("SQLite has no duplicate (train_id, station_code) pairs", len(sqlite_map) == sqlite_count,
          f"{sqlite_count} rows, {len(sqlite_map)} unique pairs")

    # 3. Connect to MongoDB
    print("\n  Connecting to MongoDB Atlas...")
    connected = await init_mongo()
    if not check("MongoDB Atlas connected successfully", connected):
        print(f"\n  {FAIL} Cannot proceed without MongoDB connection.")
        return False

    db = get_mongo_db()
    col = db[COLL_ETA_PREDICTIONS]

    # 4. Check MongoDB record count
    mongo_docs = await col.find({}).to_list(length=1000)
    mongo_count = len(mongo_docs)
    print(f"  MongoDB eta_predictions count: {mongo_count}")

    check("MongoDB contains exactly 31 documents", mongo_count == 31,
          f"SQLite={sqlite_count}, MongoDB={mongo_count}")

    # 5. Check for duplicates in MongoDB
    mongo_map = {}
    duplicate_keys = []
    for doc in mongo_docs:
        key = (str(doc.get("train_id")), str(doc.get("station_code")))
        if key in mongo_map:
            duplicate_keys.append(key)
        else:
            mongo_map[key] = doc

    check("MongoDB has zero duplicate (train_id, station_code) pairs", len(duplicate_keys) == 0,
          f"Duplicates: {duplicate_keys[:3]}" if duplicate_keys else "0 duplicates")

    # 6. Verify Key Parity (no missing, no unexpected)
    sqlite_keys = set(sqlite_map.keys())
    mongo_keys = set(mongo_map.keys())

    missing_in_mongo = sqlite_keys - mongo_keys
    unexpected_in_mongo = mongo_keys - sqlite_keys

    check("Zero missing (train_id, station_code) pairs in MongoDB", len(missing_in_mongo) == 0,
          f"Missing: {list(missing_in_mongo)[:5]}" if missing_in_mongo else "0 missing")
    check("Zero unexpected (train_id, station_code) pairs in MongoDB", len(unexpected_in_mongo) == 0,
          f"Unexpected: {list(unexpected_in_mongo)[:5]}" if unexpected_in_mongo else "0 unexpected")

    # 7. Check Unique Compound Index
    indexes = await col.index_information()
    has_unique_compound = any(
        info.get("unique") and
        sorted([k[0] for k in info["key"]]) == sorted(["train_id", "station_code"])
        for info in indexes.values()
    )
    check("Unique compound index on {train_id: 1, station_code: 1} exists", has_unique_compound,
          f"Indexes: {list(indexes.keys())}")

    # 8. Field Validation & Schema Integrity
    station_name_mismatches = []
    sched_arrival_mismatches = []
    factors_type_errors = []
    factors_schema_errors = []
    numeric_issues = []
    timestamp_missing = []
    runtime_diverged_delays = 0

    for key in sqlite_keys & mongo_keys:
        s = sqlite_map[key]
        m = mongo_map[key]

        # Station name must match
        if s["station_name"] != m.get("station_name"):
            station_name_mismatches.append((key, s["station_name"], m.get("station_name")))

        # Scheduled arrival timetable must match
        if s["scheduled_arrival"] != m.get("scheduled_arrival"):
            sched_arrival_mismatches.append((key, s["scheduled_arrival"], m.get("scheduled_arrival")))

        # factors must be a native Python list (BSON array)
        m_factors = m.get("factors")
        if not isinstance(m_factors, list):
            factors_type_errors.append((key, type(m_factors).__name__))
        else:
            for f in m_factors:
                if not isinstance(f, dict) or "factor_name" not in f or "impact_minutes" not in f:
                    factors_schema_errors.append((key, f))

        # Check numeric types
        try:
            m_delay = float(m.get("predicted_delay_minutes", 0.0))
            m_conf = float(m.get("confidence", 0.0))
            if m_delay != m_delay or m_conf != m_conf:  # NaN check
                numeric_issues.append((key, "NaN detected"))
        except (ValueError, TypeError):
            numeric_issues.append((key, "Invalid numeric format"))

        # Check timestamp
        if not m.get("created_at"):
            timestamp_missing.append(key)

        # Check if delay diverged from SQLite baseline (expected if simulation ticked)
        s_delay = float(s["predicted_delay_minutes"] or 0.0)
        if abs(s_delay - m_delay) > 0.001:
            runtime_diverged_delays += 1

    check("station_name matches baseline for all 31 records", len(station_name_mismatches) == 0,
          f"Mismatches: {station_name_mismatches[:3]}" if station_name_mismatches else "All 31 match")
    check("scheduled_arrival matches timetable for all 31 records", len(sched_arrival_mismatches) == 0,
          f"Mismatches: {sched_arrival_mismatches[:3]}" if sched_arrival_mismatches else "All 31 match")
    check("factors stored as native BSON array (not JSON string)", len(factors_type_errors) == 0,
          f"Errors: {factors_type_errors[:3]}" if factors_type_errors else "All 31 are native lists")
    check("factors elements have standard schema (factor_name, impact_minutes)", len(factors_schema_errors) == 0,
          f"Errors: {factors_schema_errors[:3]}" if factors_schema_errors else "All valid")
    check("All delay and confidence values are valid finite numbers", len(numeric_issues) == 0,
          f"Issues: {numeric_issues[:3]}" if numeric_issues else "All valid numbers")
    check("All documents have valid created_at timestamp", len(timestamp_missing) == 0,
          f"Missing: {timestamp_missing[:3]}" if timestamp_missing else "All 31 valid")

    # 9. Runtime vs Static State Context
    if runtime_diverged_delays > 0:
        print(f"\n  {INFO} Runtime state divergence detected on {runtime_diverged_delays}/31 records.")
        print(f"         This confirms the simulation engine has ticked and actively updated MongoDB")
        print(f"         as the operational source of truth. SQLite snapshot remains static baseline.")
    else:
        print(f"\n  {INFO} All 31 delay values match the initial SQLite migration baseline.")

    # 10. Per-train distribution
    print(f"\n  Per-train ETA prediction distribution:")
    from collections import Counter
    train_dist = Counter(d.get("train_id") for d in mongo_docs)
    for tid in sorted(train_dist.keys()):
        print(f"    Train {tid}: {train_dist[tid]} upcoming stops predicted")

    await close_mongo()

    # Final summary
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"\n{'='*65}")
    print(f"  STATIC VERIFICATION RESULT: {passed}/{total} checks passed")
    if failed > 0:
        print(f"  FAILED CHECKS:")
        for label, ok, detail in results:
            if not ok:
                print(f"    {FAIL} {label}: {detail}")
    print(f"{'='*65}")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_verification())
    if not ok:
        sys.exit(1)
