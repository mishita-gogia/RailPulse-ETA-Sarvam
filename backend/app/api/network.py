"""Network/congestion API endpoints."""

from fastapi import APIRouter, HTTPException, status
from app.database.mongodb import get_mongo_db, COLL_CONGESTION_SECTIONS

router = APIRouter()


@router.get("/congestion")
async def get_congestion():
    """Get all network section congestion data."""
    db = get_mongo_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB service unavailable",
        )

    docs = await db[COLL_CONGESTION_SECTIONS].find({}, {"_id": 0}).sort("section_id", 1).to_list(length=100)
    return [
        {
            "section_id": doc["section_id"],
            "from_station": doc["from_station"],
            "to_station": doc["to_station"],
            "from_station_name": doc.get("from_station_name", ""),
            "to_station_name": doc.get("to_station_name", ""),
            "congestion_score": doc["congestion_score"],
            "avg_speed_kmph": doc["avg_speed_kmph"],
            "active_trains": doc["active_trains"],
            "status": doc["status"],
            "delay_impact_minutes": doc["delay_impact_minutes"],
        }
        for doc in docs
    ]


@router.get("/congestion/{section_id}")
async def get_section_congestion(section_id: str):
    """Get congestion data for a specific section."""
    db = get_mongo_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB service unavailable",
        )

    doc = await db[COLL_CONGESTION_SECTIONS].find_one({"_id": section_id})
    if not doc:
        doc = await db[COLL_CONGESTION_SECTIONS].find_one({"section_id": section_id})

    if not doc:
        return {"error": "Section not found"}

    return {
        "section_id": doc["section_id"],
        "from_station": doc["from_station"],
        "to_station": doc["to_station"],
        "from_station_name": doc.get("from_station_name", ""),
        "to_station_name": doc.get("to_station_name", ""),
        "congestion_score": doc["congestion_score"],
        "avg_speed_kmph": doc["avg_speed_kmph"],
        "active_trains": doc["active_trains"],
        "status": doc["status"],
        "delay_impact_minutes": doc["delay_impact_minutes"],
    }
