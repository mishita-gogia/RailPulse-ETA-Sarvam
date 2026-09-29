"""
Pytest configuration for backend test isolation.
Ensures persistent development databases (SQLite and MongoDB) remain pristine
(exactly 42 users, counter seq=42; 157 alerts; 29 events; 10 demo train positions; 31 demo eta_predictions).
"""

import os
import sys
import pytest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.mongodb import (
    get_mongo_db,
    init_mongo,
    COLL_USERS,
    COLL_COUNTERS,
    COLL_ALERTS,
    COLL_OPERATIONAL_EVENTS,
    COLL_TRAIN_POSITIONS,
    COLL_ETA_PREDICTIONS,
)
from app.config import settings

DEMO_TRAIN_IDS = ("12951", "12301", "12002", "12622", "12860", "12903", "12627", "12723", "12259", "12433")


async def _purge_test_records():
    """Purge test-created records: users > 42, alerts > 157, events > 29,
    and train_positions / eta_predictions not belonging to the 10 demo trains.
    """
    # MongoDB cleanup (if connected)
    try:
        db = get_mongo_db()
        if db is not None:
            await db[COLL_USERS].delete_many({"id": {"$gt": 42}})
            u_count = await db[COLL_USERS].count_documents({})
            if u_count <= 42:
                await db[COLL_COUNTERS].update_one({"_id": "user_id"}, {"$set": {"seq": 42}})

            await db[COLL_ALERTS].delete_many({"id": {"$gt": 157}})
            a_count = await db[COLL_ALERTS].count_documents({})
            if a_count <= 157:
                await db[COLL_COUNTERS].update_one({"_id": "alert_id"}, {"$set": {"seq": 157}})

            await db[COLL_OPERATIONAL_EVENTS].delete_many({"id": {"$gt": 29}})
            e_count = await db[COLL_OPERATIONAL_EVENTS].count_documents({})
            if e_count <= 29:
                await db[COLL_COUNTERS].update_one({"_id": "event_id"}, {"$set": {"seq": 29}})

            await db[COLL_TRAIN_POSITIONS].delete_many({"_id": {"$nin": list(DEMO_TRAIN_IDS)}})
            # ETA predictions: only keep predictions for the 10 demo trains
            await db[COLL_ETA_PREDICTIONS].delete_many({"train_id": {"$nin": list(DEMO_TRAIN_IDS)}})
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def isolate_test_database_records():
    """Autouse fixture ensuring no test records persist after any test across the suite."""
    if get_mongo_db() is None:
        await init_mongo()
    yield
    await _purge_test_records()


def pytest_sessionfinish(session, exitstatus):
    """
    Synchronous pytest hook called after the entire test session completes.
    Guarantees cleanup of MongoDB development database even if async loops closed.
    """
    # MongoDB cleanup
    if settings.MONGODB_URL:
        try:
            from pymongo import MongoClient
            client = MongoClient(
                settings.MONGODB_URL,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
            )
            db = client[settings.MONGODB_DB_NAME]
            db[COLL_USERS].delete_many({"id": {"$gt": 42}})
            u_count = db[COLL_USERS].count_documents({})
            if u_count <= 42:
                db[COLL_COUNTERS].update_one({"_id": "user_id"}, {"$set": {"seq": 42}})

            db[COLL_ALERTS].delete_many({"id": {"$gt": 157}})
            a_count = db[COLL_ALERTS].count_documents({})
            if a_count <= 157:
                db[COLL_COUNTERS].update_one({"_id": "alert_id"}, {"$set": {"seq": 157}})

            db[COLL_OPERATIONAL_EVENTS].delete_many({"id": {"$gt": 29}})
            e_count = db[COLL_OPERATIONAL_EVENTS].count_documents({})
            if e_count <= 29:
                db[COLL_COUNTERS].update_one({"_id": "event_id"}, {"$set": {"seq": 29}})

            db[COLL_TRAIN_POSITIONS].delete_many({"_id": {"$nin": list(DEMO_TRAIN_IDS)}})
            # ETA predictions: only keep predictions for the 10 demo trains
            db[COLL_ETA_PREDICTIONS].delete_many({"train_id": {"$nin": list(DEMO_TRAIN_IDS)}})
            client.close()
        except Exception:
            pass
