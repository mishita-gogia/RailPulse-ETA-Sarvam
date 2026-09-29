"""
RailPulse In-Memory Runtime Models (Decoupled from SQLAlchemy).

These pure Python dataclasses represent train, station, position, and simulation entities
used in-memory during production runtime. They provide clean DTO containers without any
ORM database dependencies. All fields contain sensible defaults for kwargs flexibility.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Train:
    train_id: str = ""
    train_name: str = ""
    train_number: str = ""
    train_type: str = "express"  # rajdhani, shatabdi, express, superfast, mail
    source: str = ""
    source_code: str = ""
    destination: str = ""
    destination_code: str = ""
    zone: str = "NR"
    total_distance_km: float = 0.0
    scheduled_departure: str = "00:00"
    scheduled_arrival: str = "00:00"
    avg_speed_kmph: float = 60.0
    max_speed_kmph: float = 130.0
    days_of_run: str = "Mon,Tue,Wed,Thu,Fri,Sat,Sun"


@dataclass
class Station:
    station_code: str = ""
    station_name: str = ""
    city: Optional[str] = None
    state: Optional[str] = None
    zone: Optional[str] = None
    latitude: float = 0.0
    longitude: float = 0.0
    platform_count: int = 4
    is_junction: bool = False


@dataclass
class RouteStop:
    train_id: str = ""
    station_code: str = ""
    station_name: str = ""
    arrival: Optional[str] = None
    departure: Optional[str] = None
    distance_from_source: float = 0.0
    day: int = 1
    stop_number: int = 1
    halt_minutes: int = 2
    id: Optional[int] = None


@dataclass
class TrainPosition:
    train_id: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    speed_kmph: float = 0.0
    delay_minutes: float = 0.0
    status: str = "On Time"  # On Time, Slight Delay, Delayed, Critical Delay
    current_station_code: Optional[str] = None
    current_station_name: Optional[str] = None
    next_station_code: Optional[str] = None
    next_station_name: Optional[str] = None
    distance_covered_km: float = 0.0
    total_distance_km: float = 0.0
    last_updated: Optional[datetime] = None
    current_stop_index: int = 0
    at_station: bool = True
    dwell_remaining_seconds: float = 0.0


@dataclass
class ETAPrediction:
    train_id: Optional[str] = None
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    scheduled_arrival: Optional[str] = None
    predicted_arrival: Optional[str] = None
    predicted_delay_minutes: Optional[float] = None
    confidence: float = 80.0
    confidence_level: str = "Medium"
    factors_json: str = "[]"
    created_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class OperationalEvent:
    event_type: str = ""
    train_id: str = ""
    location: str = "En route"
    severity: float = 0.5
    duration_minutes: int = 30
    description: str = ""
    impact_delay_minutes: float = 0.0
    active: bool = True
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class Alert:
    train_id: Optional[str] = None
    train_name: str = ""
    severity: str = "info"  # critical, warning, info, success
    alert_type: str = "delay"
    message: str = ""
    location: str = ""
    eta_impact_minutes: float = 0.0
    created_at: Optional[datetime] = None
    acknowledged: bool = False
    id: Optional[int] = None


@dataclass
class CongestionSection:
    section_id: str = ""
    from_station: str = ""
    to_station: str = ""
    from_station_name: str = ""
    to_station_name: str = ""
    congestion_score: float = 0.0
    avg_speed_kmph: float = 80.0
    active_trains: int = 0
    status: str = "Normal"  # Normal, Moderate, High, Critical
    delay_impact_minutes: float = 0.0


@dataclass
class RealTrain:
    train_number: str = ""
    train_name: str = ""
    train_type: str = "Superfast"
    source_station: str = ""
    source_station_name: str = ""
    destination_station: str = ""
    destination_station_name: str = ""
    distance: float = 0.0
    running_days: str = "Daily"
    data_source: str = "NTES/DataMeet"
    last_updated: Optional[datetime] = None


@dataclass
class RealTrainStop:
    train_number: str = ""
    sequence: int = 1
    station_code: str = ""
    station_name: str = ""
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    halt_minutes: int = 2
    day_offset: int = 1
    distance: Optional[float] = None
    id: Optional[int] = None
