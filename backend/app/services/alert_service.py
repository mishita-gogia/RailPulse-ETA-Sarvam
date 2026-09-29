"""Alert management service for RailPulse ETA."""

from datetime import datetime, timezone
from typing import List
from pymongo import ReturnDocument

from app.database.mongodb import (
    get_mongo_db,
    COLL_ALERTS,
    COLL_COUNTERS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
)


class AlertService:
    """Service for managing system alerts."""

    async def get_active_alerts(self, limit: int = 50) -> List[dict]:
        """Get recent alerts, newest first."""
        db = get_mongo_db()
        if db is None:
            raise RuntimeError("MongoDB unavailable")

        col = db[COLL_ALERTS]
        cursor = col.find({}).sort("created_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [
            {
                "id": d["id"],
                "train_id": d["train_id"],
                "train_name": d.get("train_name") or d["train_id"],
                "severity": d["severity"],
                "alert_type": d["alert_type"],
                "message": d["message"],
                "location": d.get("location", ""),
                "eta_impact_minutes": d.get("eta_impact_minutes", 0.0),
                "created_at": d["created_at"].isoformat() if isinstance(d.get("created_at"), datetime) else str(d.get("created_at", "")),
                "acknowledged": bool(d.get("acknowledged", False)),
            }
            for d in docs
        ]

    async def get_train_alerts(self, train_id: str, limit: int = 10) -> List[dict]:
        """Get recent alerts for a specific train."""
        db = get_mongo_db()
        if db is None:
            raise RuntimeError("MongoDB unavailable")

        col = db[COLL_ALERTS]
        cursor = col.find({"train_id": train_id}).sort("created_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [
            {
                "id": d["id"],
                "train_id": d["train_id"],
                "train_name": d.get("train_name") or d["train_id"],
                "severity": d["severity"],
                "alert_type": d["alert_type"],
                "message": d["message"],
                "location": d.get("location", ""),
                "eta_impact_minutes": d.get("eta_impact_minutes", 0.0),
                "created_at": d["created_at"].isoformat() if isinstance(d.get("created_at"), datetime) else str(d.get("created_at", "")),
                "acknowledged": bool(d.get("acknowledged", False)),
            }
            for d in docs
        ]

    async def create_alert(
        self,
        train_id: str,
        severity: str,
        alert_type: str,
        message: str,
        location: str = "",
        eta_impact: float = 0,
    ) -> dict:
        """Create a new alert."""
        db = get_mongo_db()
        if db is None:
            raise RuntimeError("MongoDB unavailable")

        train_name = train_id
        train_doc = await db[COLL_TRAINS].find_one({"_id": train_id})
        if not train_doc:
            train_doc = await db[COLL_TRAINS].find_one({"train_id": train_id})
        if not train_doc:
            train_doc = await db[COLL_REAL_TRAINS].find_one({"_id": train_id})
        if train_doc:
            train_name = train_doc.get("train_name", train_id)

        counter = await db[COLL_COUNTERS].find_one_and_update(
            {"_id": "alert_id"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        new_id = int(counter["seq"])
        now = datetime.now(timezone.utc)

        doc = {
            "_id": new_id,
            "id": new_id,
            "train_id": train_id,
            "train_name": train_name,
            "severity": severity,
            "alert_type": alert_type,
            "message": message,
            "location": location,
            "eta_impact_minutes": float(eta_impact),
            "created_at": now,
            "acknowledged": False,
        }
        await db[COLL_ALERTS].insert_one(doc)

        return {
            "id": new_id,
            "train_id": train_id,
            "train_name": train_name,
            "severity": severity,
            "alert_type": alert_type,
            "message": message,
            "location": location,
            "eta_impact_minutes": float(eta_impact),
            "created_at": now.isoformat(),
            "acknowledged": False,
        }


# Singleton
alert_service = AlertService()
