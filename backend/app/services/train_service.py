"""Train data service for RailPulse ETA."""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from app.adapters.train_data_source import DemoTrainDataSource, RealTrainDataSource
from app.database.mongodb import get_mongo_db, COLL_ALERTS, COLL_TRAIN_POSITIONS, COLL_ETA_PREDICTIONS

demo_source = DemoTrainDataSource()
real_source = RealTrainDataSource()


class TrainService:
    """Service for managing train data and queries across demo and real sources."""

    async def get_all_trains(self, limit: int = 50, offset: int = 0) -> List[dict]:
        """Get demo trains (with live simulation state) + paginated real trains up to limit without double offset."""
        safe_limit = max(1, min(100, limit if limit is not None else 50))
        safe_offset = max(0, offset if offset is not None else 0)

        demo_trains = await demo_source.search_trains("")
        demo_count = len(demo_trains)
        demo_numbers = {t["train_number"] for t in demo_trains}

        result = []
        if safe_offset < demo_count:
            demo_slice = demo_trains[safe_offset : safe_offset + safe_limit]
            result.extend(demo_slice)
            needed_from_real = safe_limit - len(result)
            real_offset = 0
        else:
            needed_from_real = safe_limit
            real_offset = safe_offset - demo_count

        if needed_from_real > 0:
            real_trains = await real_source.search_trains(
                "", limit=needed_from_real, offset=real_offset, exclude_numbers=demo_numbers
            )
            result.extend(real_trains)

        return result

    async def search_trains(self, query: str, limit: int = 50, offset: int = 0) -> List[dict]:
        """Search trains across both Demo simulation trains and Real railway master catalog without double offset."""
        safe_limit = max(1, min(100, limit if limit is not None else 50))
        safe_offset = max(0, offset if offset is not None else 0)

        demo_matching = await demo_source.search_trains(query)
        demo_count = len(demo_matching)
        demo_numbers = {t["train_number"] for t in demo_matching}

        result = []
        if safe_offset < demo_count:
            demo_slice = demo_matching[safe_offset : safe_offset + safe_limit]
            result.extend(demo_slice)
            needed_from_real = safe_limit - len(result)
            real_offset = 0
        else:
            needed_from_real = safe_limit
            real_offset = safe_offset - demo_count

        if needed_from_real > 0:
            real_trains = await real_source.search_trains(
                query, limit=needed_from_real, offset=real_offset, exclude_numbers=demo_numbers
            )
            result.extend(real_trains)

        return result

    async def count_all_trains(self) -> int:
        """Count total trains across demo trains and non-overlapping real trains."""
        demo_trains = await demo_source.search_trains("")
        demo_count = len(demo_trains)
        demo_numbers = {t["train_number"] for t in demo_trains}
        real_count = await real_source.count_trains("", exclude_numbers=demo_numbers)
        return demo_count + real_count

    async def count_search_trains(self, query: str) -> int:
        """Count total matching trains across demo trains and non-overlapping real trains."""
        demo_matching = await demo_source.search_trains(query)
        demo_count = len(demo_matching)
        demo_numbers = {t["train_number"] for t in demo_matching}
        real_count = await real_source.count_trains(query, exclude_numbers=demo_numbers)
        return demo_count + real_count

    async def get_train(self, train_id: str) -> Optional[dict]:
        """Get a single train by ID or train_number from demo source or real source."""
        # Check demo first
        t = await demo_source.get_train(train_id)
        if t:
            return t
        # Check real train catalog
        return await real_source.get_train(train_id)

    async def get_train_position(self, train_id: str) -> Optional[dict]:
        """Get position telemetry for a train."""
        p = await demo_source.get_position(train_id)
        if p:
            return p
        return await real_source.get_position(train_id)

    async def get_train_route(self, train_id: str) -> List[dict]:
        """Get route with stop statuses."""
        # If in demo trains
        demo_t = await demo_source.get_train(train_id)
        if demo_t:
            return await demo_source.get_route(train_id)
        # Else real train route
        return await real_source.get_route(train_id)

    async def get_train_history(self, train_id: str) -> List[dict]:
        """Get ETA prediction history for a train from MongoDB."""
        db = get_mongo_db()
        if db is None:
            raise RuntimeError("MongoDB is unavailable")

        cursor = db[COLL_ETA_PREDICTIONS].find(
            {"train_id": train_id}
        ).sort("created_at", -1).limit(50)
        docs = await cursor.to_list(length=50)
        return [
            {
                "station_code": d.get("station_code", ""),
                "station_name": d.get("station_name", ""),
                "scheduled_arrival": d.get("scheduled_arrival", ""),
                "predicted_arrival": d.get("predicted_arrival", ""),
                "predicted_delay_minutes": d.get("predicted_delay_minutes"),
                "confidence": d.get("confidence"),
                "confidence_level": d.get("confidence_level", ""),
                "created_at": d["created_at"].isoformat() if isinstance(d.get("created_at"), datetime) else str(d.get("created_at", "")),
            }
            for d in docs
        ]

    async def get_kpis(self) -> dict:
        """Get dashboard KPI metrics from MongoDB."""
        db = get_mongo_db()
        if db is None:
            raise RuntimeError("MongoDB is unavailable")

        mongo_positions = await db[COLL_TRAIN_POSITIONS].find({}).to_list(length=100)
        total = len(mongo_positions)
        on_time = sum(1 for p in mongo_positions if p.get("status") == "On Time")
        delayed = sum(1 for p in mongo_positions if p.get("status") in ("Delayed", "Slight Delay"))
        critical = sum(1 for p in mongo_positions if p.get("status") == "Critical Delay")
        avg_delay = sum(float(p.get("delay_minutes", 0.0)) for p in mongo_positions) / max(1, total)
        active_alerts = await db[COLL_ALERTS].count_documents({"acknowledged": False})
        accuracy = max(70, 95 - avg_delay * 0.5)

        return {
            "active_trains": total,
            "on_time": on_time,
            "delayed": delayed,
            "critical": critical,
            "avg_delay_minutes": round(avg_delay, 1),
            "prediction_accuracy": round(accuracy, 1),
            "active_alerts": active_alerts,
        }


# Singleton
train_service = TrainService()
