"""
MongoDB persistence module using the official PyMongo Async driver (AsyncMongoClient).

Supports MongoDB Atlas and local MongoDB configurations via environment variables:
- MONGODB_URL
- MONGODB_DB_NAME
"""

import logging
from typing import Optional
from pymongo import AsyncMongoClient, ASCENDING, DESCENDING, TEXT, GEOSPHERE
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.collection import AsyncCollection

from app.config import settings

logger = logging.getLogger(__name__)

# Singleton client and database instances
_mongo_client: Optional[AsyncMongoClient] = None
_mongo_db: Optional[AsyncDatabase] = None

# Canonical collection names
COLL_TRAINS = "trains"
COLL_REAL_TRAINS = "real_trains"
COLL_STATIONS = "stations"
COLL_TRAIN_POSITIONS = "train_positions"
COLL_ETA_PREDICTIONS = "eta_predictions"
COLL_ALERTS = "alerts"
COLL_OPERATIONAL_EVENTS = "operational_events"
COLL_CONGESTION_SECTIONS = "congestion_sections"
COLL_USERS = "users"
COLL_COUNTERS = "counters"


def get_mongo_client() -> Optional[AsyncMongoClient]:
    """Retrieve the active AsyncMongoClient singleton."""
    return _mongo_client


def get_mongo_db() -> Optional[AsyncDatabase]:
    """Retrieve the active AsyncDatabase instance."""
    return _mongo_db


def get_collection(name: str) -> Optional[AsyncCollection]:
    """
    Get an async collection by name from the configured MongoDB database.
    Returns None if MongoDB is not initialized.
    """
    if _mongo_db is None:
        return None
    return _mongo_db[name]


async def create_mongo_indexes(db: AsyncDatabase) -> None:
    """
    Create all required MongoDB indexes idempotently based on the architectural audit.
    """
    try:
        # 1. Trains collection (active simulated trains with embedded route stops)
        await db[COLL_TRAINS].create_index([("train_id", ASCENDING)], unique=True)
        await db[COLL_TRAINS].create_index([("train_number", ASCENDING)])
        await db[COLL_TRAINS].create_index([("stops.station_code", ASCENDING)])

        # 2. Real Trains collection (master catalog with embedded timetable stops)
        await db[COLL_REAL_TRAINS].create_index([("train_number", ASCENDING)], unique=True)
        await db[COLL_REAL_TRAINS].create_index([("train_name", ASCENDING)])
        await db[COLL_REAL_TRAINS].create_index([("source_station", ASCENDING)])
        await db[COLL_REAL_TRAINS].create_index([("destination_station", ASCENDING)])
        await db[COLL_REAL_TRAINS].create_index([("stops.station_code", ASCENDING)])

        # 3. Stations collection
        await db[COLL_STATIONS].create_index([("station_code", ASCENDING)], unique=True)
        await db[COLL_STATIONS].create_index([("station_name", ASCENDING)])
        await db[COLL_STATIONS].create_index([("location", GEOSPHERE)])

        # 4. Train Positions (telemetry state)
        await db[COLL_TRAIN_POSITIONS].create_index([("train_id", ASCENDING)], unique=True)
        await db[COLL_TRAIN_POSITIONS].create_index([("status", ASCENDING)])

        # 5. ETA Predictions
        await db[COLL_ETA_PREDICTIONS].create_index(
            [("train_id", ASCENDING), ("station_code", ASCENDING)],
            unique=True
        )
        await db[COLL_ETA_PREDICTIONS].create_index(
            [("train_id", ASCENDING), ("created_at", DESCENDING)]
        )

        # 6. Alerts
        await db[COLL_ALERTS].create_index([("id", ASCENDING)], unique=True)
        await db[COLL_ALERTS].create_index([("created_at", DESCENDING)])
        await db[COLL_ALERTS].create_index([("acknowledged", ASCENDING), ("created_at", DESCENDING)])
        await db[COLL_ALERTS].create_index([("train_id", ASCENDING), ("created_at", DESCENDING)])

        # 7. Operational Events
        await db[COLL_OPERATIONAL_EVENTS].create_index([("id", ASCENDING)], unique=True)
        await db[COLL_OPERATIONAL_EVENTS].create_index([("active", ASCENDING), ("train_id", ASCENDING)])
        await db[COLL_OPERATIONAL_EVENTS].create_index([("expires_at", ASCENDING)])

        # 8. Congestion Sections
        await db[COLL_CONGESTION_SECTIONS].create_index([("section_id", ASCENDING)], unique=True)

        # 9. Users
        await db[COLL_USERS].create_index([("phone", ASCENDING)], unique=True)
        await db[COLL_USERS].create_index([("id", ASCENDING)], unique=True)

        logger.info("[MongoDB] All collection indexes verified/created successfully.")
    except Exception as e:
        logger.warning(f"[MongoDB] Notice while creating indexes: {e}")


async def init_mongo() -> bool:
    """
    Initialize connection to MongoDB using AsyncMongoClient.
    Gracefully handles unconfigured environment or unreachable clusters.
    """
    global _mongo_client, _mongo_db

    if not settings.MONGODB_URL:
        logger.info("[MongoDB] MONGODB_URL not configured. Running without active MongoDB connection.")
        return False

    try:
        # Ensure reliable DNS resolvers for MongoDB Atlas SRV resolution
        try:
            import dns.asyncresolver
            import dns.resolver
            for r in [dns.asyncresolver.get_default_resolver(), dns.resolver.get_default_resolver()]:
                if r and hasattr(r, "nameservers"):
                    reliable_ns = ["8.8.8.8", "1.1.1.1"]
                    r.nameservers = [ns for ns in reliable_ns if ns not in r.nameservers] + list(r.nameservers)
        except Exception:
            pass

        # Create asynchronous client with sensible timeouts
        _mongo_client = AsyncMongoClient(
            settings.MONGODB_URL,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            maxPoolSize=100,
            minPoolSize=10,
        )
        # Verify connectivity via ping
        await _mongo_client.admin.command("ping")
        _mongo_db = _mongo_client[settings.MONGODB_DB_NAME]

        # Initialize schema indexes
        await create_mongo_indexes(_mongo_db)

        logger.info(f"[MongoDB] Connected successfully to database '{settings.MONGODB_DB_NAME}'.")
        return True
    except Exception as e:
        logger.error(f"[MongoDB] Failed to connect to MongoDB ({e}). Falling back.")
        _mongo_client = None
        _mongo_db = None
        return False


async def close_mongo() -> None:
    """Close the AsyncMongoClient connection pool cleanly."""
    global _mongo_client, _mongo_db

    if _mongo_client is not None:
        try:
            await _mongo_client.close()
            logger.info("[MongoDB] Connection closed cleanly.")
        except Exception as e:
            logger.warning(f"[MongoDB] Error during connection close: {e}")
        finally:
            _mongo_client = None
            _mongo_db = None
