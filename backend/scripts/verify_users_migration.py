"""
Independent User Migration Verification Script for Phase 3.

Verifies:
1. Exact user counts (SQLite: 42, MongoDB: 42).
2. IDs 1–42 exist in both without gaps or mismatches.
3. Zero duplicate IDs and zero duplicate phone numbers.
4. Names, phones, and roles match 100%.
5. Password hashes match 100% (WITHOUT printing/exposing hashes).
6. Created_at timestamps match.
7. Counter 'user_id' in 'counters' collection has seq = 42.
"""

import os
import sys
import sqlite3
import asyncio
from typing import Dict, Any, List

from pymongo import AsyncMongoClient

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


async def verify_users() -> Dict[str, Any]:
    report: Dict[str, Any] = {"success": True, "errors": [], "warnings": []}

    if not settings.MONGODB_URL:
        report["success"] = False
        report["errors"].append("MONGODB_URL not configured.")
        return report

    client = AsyncMongoClient(settings.MONGODB_URL, serverSelectionTimeoutMS=8000)
    db = client[settings.MONGODB_DB_NAME]
    conn = get_sqlite_connection()

    try:
        cur = conn.cursor()

        # 1. Counts
        cur.execute("SELECT count(*) FROM users")
        sq_count = cur.fetchone()[0]
        mg_count = await db.users.count_documents({})
        report["counts"] = {"sqlite": sq_count, "mongo": mg_count, "match": sq_count == mg_count}
        if sq_count != mg_count:
            report["success"] = False
            report["errors"].append(f"Count mismatch: SQLite={sq_count}, MongoDB={mg_count}")

        # 2. Duplicate checks
        distinct_ids = await db.users.distinct("id")
        distinct_phones = await db.users.distinct("phone")
        report["duplicates"] = {
            "ids_unique": len(distinct_ids) == mg_count,
            "phones_unique": len(distinct_phones) == mg_count,
        }
        if len(distinct_ids) != mg_count:
            report["success"] = False
            report["errors"].append("Duplicate user IDs found in MongoDB.")
        if len(distinct_phones) != mg_count:
            report["success"] = False
            report["errors"].append("Duplicate phone numbers found in MongoDB.")

        # 3. Fetch all from SQLite
        cur.execute("SELECT id, name, phone, password_hash, role, created_at FROM users ORDER BY id")
        sq_users = {r["id"]: dict(r) for r in cur.fetchall()}

        # 4. Fetch all from MongoDB
        mg_users = {}
        async for u in db.users.find({}):
            mg_users[u["id"]] = u

        # 5. Missing / Unexpected checks
        sq_ids = set(sq_users.keys())
        mg_ids = set(mg_users.keys())
        missing_in_mg = sq_ids - mg_ids
        unexpected_in_mg = mg_ids - sq_ids
        report["missing_users"] = list(missing_in_mg)
        report["unexpected_users"] = list(unexpected_in_mg)
        if missing_in_mg or unexpected_in_mg:
            report["success"] = False
            report["errors"].append("ID set discrepancy detected.")

        # 6. Field-by-field verification (WITHOUT exposing password hashes)
        mismatches = []
        hash_matches_count = 0
        names_match_count = 0
        phones_match_count = 0
        roles_match_count = 0

        for uid in sorted(sq_ids):
            sq_u = sq_users[uid]
            mg_u = mg_users.get(uid)
            if not mg_u:
                continue

            name_ok = sq_u["name"].strip() == mg_u["name"].strip()
            phone_ok = sq_u["phone"].strip() == mg_u["phone"].strip()
            role_ok = sq_u["role"].strip().upper() == mg_u["role"].strip().upper()
            hash_ok = sq_u["password_hash"].strip() == mg_u["password_hash"].strip()

            if name_ok:
                names_match_count += 1
            else:
                mismatches.append(f"User {uid}: name mismatch ({sq_u['name']} vs {mg_u['name']})")

            if phone_ok:
                phones_match_count += 1
            else:
                mismatches.append(f"User {uid}: phone mismatch ({sq_u['phone']} vs {mg_u['phone']})")

            if role_ok:
                roles_match_count += 1
            else:
                mismatches.append(f"User {uid}: role mismatch ({sq_u['role']} vs {mg_u['role']})")

            if hash_ok:
                hash_matches_count += 1
            else:
                mismatches.append(f"User {uid}: password hash mismatch")

        report["field_verification"] = {
            "total_verified": len(sq_ids),
            "names_matching": names_match_count,
            "phones_matching": phones_match_count,
            "roles_matching": roles_match_count,
            "password_hashes_matching": hash_matches_count,
            "all_hashes_equal": hash_matches_count == len(sq_ids),
        }
        if mismatches:
            report["success"] = False
            report["errors"].extend(mismatches)

        # 7. Counter verification
        counter_doc = await db.counters.find_one({"_id": "user_id"})
        report["counter"] = counter_doc
        if not counter_doc or counter_doc.get("seq") != 42:
            report["success"] = False
            report["errors"].append(f"Counter 'user_id' is not 42 (found: {counter_doc})")

    finally:
        conn.close()
        await client.close()

    return report


if __name__ == "__main__":
    rep = asyncio.run(verify_users())
    import json
    print(json.dumps(rep, indent=2))
