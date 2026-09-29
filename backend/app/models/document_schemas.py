"""
MongoDB Document Schemas for RailPulse ETA.

These models define the document structures for MongoDB collections,
supporting embedded stops and preserving complete compatibility with
FastAPI response schemas and business logic.
"""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class RouteStopEmbedded(BaseModel):
    """Embedded route stop within TrainDocument."""
    stop_number: int
    station_code: str
    station_name: str
    arrival: Optional[str] = None
    departure: Optional[str] = None
    distance_from_source: float = 0.0
    day: int = 1
    halt_minutes: int = 2


class TrainDocument(BaseModel):
    """Document model for 'trains' collection (10 simulated trains)."""
    train_id: str = Field(..., alias="_id")
    train_name: str
    train_number: str
    train_type: str
    source: str
    source_code: str
    destination: str
    destination_code: str
    zone: str
    total_distance_km: float
    scheduled_departure: str
    scheduled_arrival: str
    avg_speed_kmph: float = 60.0
    max_speed_kmph: float = 130.0
    days_of_run: str = "Mon,Tue,Wed,Thu,Fri,Sat,Sun"
    stops: List[RouteStopEmbedded] = []

    class Config:
        populate_by_name = True


class RealTrainStopEmbedded(BaseModel):
    """Embedded timetable stop within RealTrainDocument."""
    sequence: int
    station_code: str
    station_name: str
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    halt_minutes: int = 2
    day_offset: int = 1
    distance: Optional[float] = None


class RealTrainDocument(BaseModel):
    """Document model for 'real_trains' master catalog (5,211 trains)."""
    train_number: str = Field(..., alias="_id")
    train_name: str
    train_type: str = "Superfast Express"
    source_station: str
    source_station_name: str = ""
    destination_station: str
    destination_station_name: str = ""
    distance: float = 0.0
    running_days: str = "Daily"
    data_source: str = "NTES / DataMeet"
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stops: List[RealTrainStopEmbedded] = []

    class Config:
        populate_by_name = True


class GeoJSONPoint(BaseModel):
    """GeoJSON Point geometry for MongoDB 2dsphere indexing."""
    type: str = "Point"
    coordinates: List[float]  # [longitude, latitude]


class StationDocument(BaseModel):
    """Document model for 'stations' catalog (8,704 stations)."""
    station_code: str = Field(..., alias="_id")
    station_name: str
    city: Optional[str] = None
    state: Optional[str] = None
    zone: Optional[str] = None
    latitude: float
    longitude: float
    location: Optional[GeoJSONPoint] = None
    platform_count: int = 4
    is_junction: bool = False

    class Config:
        populate_by_name = True


class TrainPositionDocument(BaseModel):
    """Document model for 'train_positions' telemetry state."""
    train_id: str = Field(..., alias="_id")
    latitude: float
    longitude: float
    speed_kmph: float = 0.0
    delay_minutes: float = 0.0
    status: str = "On Time"
    current_station_code: Optional[str] = None
    current_station_name: Optional[str] = None
    next_station_code: Optional[str] = None
    next_station_name: Optional[str] = None
    distance_covered_km: float = 0.0
    total_distance_km: float = 0.0
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    current_stop_index: int = 0
    at_station: bool = True
    dwell_remaining_seconds: float = 0.0

    class Config:
        populate_by_name = True


class PredictionFactorEmbedded(BaseModel):
    """Embedded prediction factor."""
    factor_name: str
    impact_minutes: float
    description: str
    severity: str = "low"


class ETAPredictionDocument(BaseModel):
    """Document model for 'eta_predictions' collection."""
    train_id: str
    station_code: str
    station_name: str
    scheduled_arrival: str
    predicted_arrival: str
    predicted_delay_minutes: float
    confidence: float = 80.0
    confidence_level: str = "Medium"
    factors: List[PredictionFactorEmbedded] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlertDocument(BaseModel):
    """Document model for 'alerts' collection."""
    id: int  # Sequential integer matching User/Alert ID contracts
    train_id: str
    train_name: str = ""
    severity: str = "info"  # critical, warning, info, success
    alert_type: str = "delay"
    message: str
    location: str = ""
    eta_impact_minutes: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False


class OperationalEventDocument(BaseModel):
    """Document model for 'operational_events' collection."""
    id: int
    event_type: str
    train_id: str
    location: str = "En route"
    severity: float = 0.5
    duration_minutes: int = 30
    description: str = ""
    impact_delay_minutes: float = 0.0
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None


class CongestionSectionDocument(BaseModel):
    """Document model for 'congestion_sections' collection."""
    section_id: str = Field(..., alias="_id")
    from_station: str
    to_station: str
    from_station_name: str = ""
    to_station_name: str = ""
    congestion_score: float = 0.0
    avg_speed_kmph: float = 80.0
    active_trains: int = 0
    status: str = "Normal"
    delay_impact_minutes: float = 0.0

    class Config:
        populate_by_name = True


class UserDocument(BaseModel):
    """Document model for 'users' authentication collection."""
    id: int  # Continuous integer ID preserving AuthUser.id and JWT sub
    name: str
    phone: str
    password_hash: str
    role: str  # PASSENGER or RAILWAY_STAFF
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CounterDocument(BaseModel):
    """Atomic counter document for integer ID sequence generation."""
    sequence_name: str = Field(..., alias="_id")
    seq: int = 0

    class Config:
        populate_by_name = True
