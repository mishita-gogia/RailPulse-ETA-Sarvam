"""
Unit tests for the MongoDB module (AsyncMongoClient) and document schemas.
"""

import pytest
from app.database.mongodb import (
    get_mongo_client,
    get_mongo_db,
    get_collection,
    init_mongo,
    close_mongo,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_STATIONS,
    COLL_USERS,
    COLL_COUNTERS,
)
from app.models.document_schemas import (
    TrainDocument,
    RouteStopEmbedded,
    RealTrainDocument,
    RealTrainStopEmbedded,
    StationDocument,
    TrainPositionDocument,
    ETAPredictionDocument,
    PredictionFactorEmbedded,
    AlertDocument,
    OperationalEventDocument,
    CongestionSectionDocument,
    UserDocument,
    CounterDocument,
)


@pytest.mark.asyncio
async def test_mongo_unconfigured_graceful_handling():
    """When MONGODB_URL is not configured, init_mongo returns False gracefully without crashing."""
    result = await init_mongo()
    # In test environment without MONGODB_URL set, it should return False
    assert isinstance(result, bool)
    await close_mongo()
    assert get_mongo_client() is None
    assert get_mongo_db() is None
    assert get_collection(COLL_TRAINS) is None


def test_train_document_schema_validation():
    """Verify TrainDocument schema with embedded route stops."""
    stop = RouteStopEmbedded(
        stop_number=1,
        station_code="NDLS",
        station_name="New Delhi",
        departure="06:00",
        distance_from_source=0.0,
    )
    doc = TrainDocument(
        _id="12951",
        train_name="Mumbai Rajdhani",
        train_number="12951",
        train_type="rajdhani",
        source="Mumbai Central",
        source_code="MMCT",
        destination="New Delhi",
        destination_code="NDLS",
        zone="WR",
        total_distance_km=1384.0,
        scheduled_departure="17:00",
        scheduled_arrival="08:35",
        stops=[stop],
    )
    assert doc.train_id == "12951"
    assert len(doc.stops) == 1
    assert doc.stops[0].station_code == "NDLS"


def test_real_train_document_schema_validation():
    """Verify RealTrainDocument schema with embedded timetable stops."""
    stop = RealTrainStopEmbedded(
        sequence=1,
        station_code="JSM",
        station_name="Jaisalmer",
        departure_time="15:30",
        halt_minutes=0,
        day_offset=1,
        distance=0.0,
    )
    doc = RealTrainDocument(
        _id="20491",
        train_name="Jaisalmer - Sabarmati SF Express",
        train_type="Superfast Express",
        source_station="JSM",
        destination_station="SBIB",
        distance=759.0,
        stops=[stop],
    )
    assert doc.train_number == "20491"
    assert len(doc.stops) == 1
    assert doc.stops[0].station_code == "JSM"


def test_user_document_schema_validation():
    """Verify UserDocument preserves integer ID and role validation."""
    user = UserDocument(
        id=1,
        name="Demo Passenger",
        phone="9876543210",
        password_hash="pbkdf2_hash_here",
        role="PASSENGER",
    )
    assert user.id == 1
    assert isinstance(user.id, int)
    assert user.phone == "9876543210"
    assert user.role == "PASSENGER"


def test_eta_prediction_document_schema_validation():
    """Verify ETAPredictionDocument with native embedded factor array."""
    factor = PredictionFactorEmbedded(
        factor_name="Current Delay",
        impact_minutes=5.0,
        description="Section congestion",
        severity="medium",
    )
    eta = ETAPredictionDocument(
        train_id="12951",
        station_code="BRC",
        station_name="Vadodara Jn",
        scheduled_arrival="21:40",
        predicted_arrival="21:45",
        predicted_delay_minutes=5.0,
        confidence=85.0,
        confidence_level="High",
        factors=[factor],
    )
    assert eta.train_id == "12951"
    assert len(eta.factors) == 1
    assert eta.factors[0].impact_minutes == 5.0


def test_counter_document_schema_validation():
    """Verify CounterDocument for atomic integer generation."""
    counter = CounterDocument(_id="user_id", seq=42)
    assert counter.sequence_name == "user_id"
    assert counter.seq == 42
