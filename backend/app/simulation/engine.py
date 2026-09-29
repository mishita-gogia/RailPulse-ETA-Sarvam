"""
RailPulse ETA - Simulation Engine

Simulates real-time train movement, speed changes, delays, and operational events.
Designed to be replaceable with real Indian Railways data feeds.
"""

import asyncio
import json
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

import time
from app.models.runtime_models import (
    Train, Station, RouteStop, TrainPosition,
    OperationalEvent, Alert, CongestionSection, ETAPrediction,
    RealTrain, RealTrainStop
)
from pymongo import UpdateOne, ReturnDocument
from app.database.mongodb import (
    get_mongo_db,
    COLL_CONGESTION_SECTIONS,
    COLL_ALERTS,
    COLL_OPERATIONAL_EVENTS,
    COLL_COUNTERS,
    COLL_TRAIN_POSITIONS,
    COLL_ETA_PREDICTIONS,
    COLL_TRAINS,
    COLL_REAL_TRAINS,
    COLL_STATIONS,
)
from app.services.alert_service import alert_service
from app.config import settings


def _doc_to_train_position(d: dict) -> TrainPosition:
    """Convert a MongoDB train_positions document into a TrainPosition model instance."""
    return TrainPosition(
        train_id=d.get("train_id", d.get("_id")),
        latitude=float(d.get("latitude", 0.0)),
        longitude=float(d.get("longitude", 0.0)),
        speed_kmph=float(d.get("speed_kmph", 0.0)),
        delay_minutes=float(d.get("delay_minutes", 0.0)),
        status=str(d.get("status", "On Time")),
        current_station_code=d.get("current_station_code"),
        current_station_name=d.get("current_station_name"),
        next_station_code=d.get("next_station_code"),
        next_station_name=d.get("next_station_name"),
        distance_covered_km=float(d.get("distance_covered_km", 0.0)),
        total_distance_km=float(d.get("total_distance_km", 0.0)),
        last_updated=d.get("last_updated"),
        current_stop_index=int(d.get("current_stop_index", 0)),
        at_station=bool(d.get("at_station", True)),
        dwell_remaining_seconds=float(d.get("dwell_remaining_seconds", 0.0)),
    )


class SimulationEngine:
    """
    Background simulation engine that mimics real-time train operations.

    For SIH prototype, simulated data is used because Indian Railways
    operational APIs are not publicly available to the development team.

    Architecture note: This class can be replaced by a RealDataAdapter
    that consumes actual GPS/AVL feeds, signal data, and NTES information.
    """

    def __init__(self):
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._tick_count = 0
        self._last_update = datetime.now(timezone.utc)
        self._ws_clients: List = []  # WebSocket connections
        self._active_events: Dict[str, dict] = {}  # train_id -> active events
        self._registered_real_trains: Set[str] = set()  # actively simulated real train numbers
        self._eta_predictor = None
        self._bulk_latencies: List[float] = []
        self._eta_latencies: List[float] = []
        # Phase 5: In-memory cache for master static railway data to avoid per-tick Atlas queries
        self._master_trains: Dict[str, Train] = {}
        self._master_route_stops: Dict[str, List[RouteStop]] = {}
        self._station_cache: Dict[str, Station] = {}
        self._master_data_loaded: bool = False
        self._master_load_time_ms: float = 0.0
        self._master_load_query_count: int = 0

    def _record_bulk_latency(self, latency_ms: float):
        self._bulk_latencies.append(latency_ms)
        if len(self._bulk_latencies) > 500:
            self._bulk_latencies.pop(0)

    def _record_eta_latency(self, latency_ms: float):
        self._eta_latencies.append(latency_ms)
        if len(self._eta_latencies) > 500:
            self._eta_latencies.pop(0)

    def get_latency_stats(self) -> dict:
        if not self._bulk_latencies:
            return {"count": 0, "avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0, "p95_ms": 0.0}
        sorted_l = sorted(self._bulk_latencies)
        p95_idx = int(len(sorted_l) * 0.95)
        return {
            "count": len(self._bulk_latencies),
            "avg_ms": round(sum(self._bulk_latencies) / len(self._bulk_latencies), 2),
            "min_ms": round(min(self._bulk_latencies), 2),
            "max_ms": round(max(self._bulk_latencies), 2),
            "p95_ms": round(sorted_l[min(p95_idx, len(sorted_l) - 1)], 2),
        }

    def get_eta_latency_stats(self) -> dict:
        if not self._eta_latencies:
            return {"count": 0, "avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0, "p95_ms": 0.0}
        sorted_l = sorted(self._eta_latencies)
        p95_idx = int(len(sorted_l) * 0.95)
        return {
            "count": len(self._eta_latencies),
            "avg_ms": round(sum(self._eta_latencies) / len(self._eta_latencies), 2),
            "min_ms": round(min(self._eta_latencies), 2),
            "max_ms": round(max(self._eta_latencies), 2),
            "p95_ms": round(sorted_l[min(p95_idx, len(sorted_l) - 1)], 2),
        }

    @property
    def is_running(self):
        return self._is_running

    @property
    def tick_count(self):
        return self._tick_count

    @property
    def last_update(self):
        return self._last_update

    def register_ws_client(self, ws):
        if ws not in self._ws_clients:
            self._ws_clients.append(ws)

    def unregister_ws_client(self, ws):
        if ws in self._ws_clients:
            self._ws_clients.remove(ws)

    def set_predictor(self, predictor):
        self._eta_predictor = predictor

    def get_master_data_stats(self) -> dict:
        return {
            "loaded": self._master_data_loaded,
            "trains_count": len(self._master_trains),
            "route_stops_count": sum(len(s) for s in self._master_route_stops.values()),
            "stations_cached": len(self._station_cache),
            "load_time_ms": self._master_load_time_ms,
            "queries_executed": self._master_load_query_count,
        }

    async def load_master_data(self, session=None) -> bool:
        """
        Preload and cache master train definitions, route stops, and referenced stations
        from MongoDB into memory to eliminate repeated database queries on every 3-second simulation tick.
        """
        t0 = time.perf_counter()
        query_count = 0
        db = get_mongo_db()

        if db is not None:
            try:
                # 1. Fetch demo trains with embedded route stops (Query 1)
                query_count += 1
                train_docs = await db[COLL_TRAINS].find({}).to_list(length=50)
                if train_docs:
                    station_codes = set()
                    for td in train_docs:
                        t_id = td.get("train_id", td.get("_id"))
                        self._master_trains[t_id] = Train(
                            train_id=t_id,
                            train_name=td.get("train_name", ""),
                            train_number=td.get("train_number", ""),
                            train_type=td.get("train_type", "Superfast Express"),
                            source=td.get("source", ""),
                            source_code=td.get("source_code", ""),
                            destination=td.get("destination", ""),
                            destination_code=td.get("destination_code", ""),
                            zone=td.get("zone", "IR"),
                            total_distance_km=float(td.get("total_distance_km") or 0.0),
                            scheduled_departure=td.get("scheduled_departure", "00:00"),
                            scheduled_arrival=td.get("scheduled_arrival", "00:00"),
                            avg_speed_kmph=float(td.get("avg_speed_kmph") or 60.0),
                            max_speed_kmph=float(td.get("max_speed_kmph") or 130.0),
                            days_of_run=td.get("days_of_run", "Daily"),
                        )
                        raw_stops = td.get("stops", [])
                        sorted_stops = sorted(raw_stops, key=lambda s: int(s.get("stop_number", 0)))
                        self._master_route_stops[t_id] = [
                            RouteStop(
                                train_id=t_id,
                                station_code=s.get("station_code"),
                                station_name=s.get("station_name"),
                                arrival=s.get("arrival"),
                                departure=s.get("departure"),
                                distance_from_source=float(s.get("distance_from_source") or 0.0),
                                day=int(s.get("day", 1)),
                                stop_number=int(s.get("stop_number", idx + 1)),
                                halt_minutes=int(s.get("halt_minutes", 2)),
                            )
                            for idx, s in enumerate(sorted_stops)
                        ]
                        for s in raw_stops:
                            st_c = s.get("station_code")
                            if st_c:
                                station_codes.add(st_c.strip().upper())

                    # 2. Fetch stations referenced by all demo trains in a single indexed query (Query 2)
                    if station_codes:
                        query_count += 1
                        st_docs = await db[COLL_STATIONS].find({"station_code": {"$in": list(station_codes)}}).to_list(length=200)
                        for st in st_docs:
                            c = st.get("station_code", st.get("_id"))
                            self._station_cache[c] = Station(
                                station_code=c,
                                station_name=st.get("station_name", ""),
                                latitude=float(st.get("latitude", 0.0)),
                                longitude=float(st.get("longitude", 0.0)),
                            )

                    self._master_data_loaded = True
                    self._master_load_query_count = query_count
                    self._master_load_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    print(f"[SimulationEngine] Master data preloaded from MongoDB: {len(self._master_trains)} trains, "
                          f"{sum(len(s) for s in self._master_route_stops.values())} route stops, "
                          f"{len(self._station_cache)} stations in {self._master_load_time_ms} ms ({query_count} queries).")
                    return True
            except Exception as e:
                print(f"[SimulationEngine] Failed to load master data from MongoDB: {e}")
                return False

        return False

    async def start(self):
        if self._is_running:
            return {"status": "already_running"}
        self._is_running = True

        # Preload master static railway data into in-memory cache
        await self.load_master_data()

        try:
            db = get_mongo_db()
            if db is not None:
                now = datetime.now(timezone.utc)
                cursor = db[COLL_OPERATIONAL_EVENTS].find({"active": True})
                async for evt in cursor:
                    exp = evt.get("expires_at")
                    if exp:
                        if exp.tzinfo is None:
                            exp = exp.replace(tzinfo=timezone.utc)
                        if exp <= now:
                            continue
                    t_id = evt.get("train_id")
                    if t_id:
                        if t_id not in self._active_events:
                            self._active_events[t_id] = {}
                        self._active_events[t_id][str(evt.get("id", evt.get("_id")))] = {
                            "event_type": evt.get("event_type"),
                            "severity": evt.get("severity", 0.3),
                            "description": evt.get("description", ""),
                            "impact_delay": evt.get("impact_delay_minutes", 0.0),
                        }
        except Exception as e:
            print(f"[Simulation] Warning: failed to hydrate active events: {e}")

        self._task = asyncio.create_task(self._run_loop())
        print(f"[Simulation] Started. Interval: {settings.SIMULATION_INTERVAL}s")
        return {"status": "started"}

    async def stop(self):
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        print("[Simulation] Stopped.")
        return {"status": "stopped"}

    async def _run_loop(self):
        while self._is_running:
            try:
                await self.tick()
            except Exception as e:
                print(f"[Simulation] Tick error: {e}")
            await asyncio.sleep(settings.SIMULATION_INTERVAL)

    async def tick(self):
        """One simulation step - updates all train states."""
        self._tick_count += 1
        self._last_update = datetime.now(timezone.utc)

        db = get_mongo_db()
        # Ensure master static railway data is loaded into memory cache
        if not self._master_data_loaded:
            await self.load_master_data()

        if db is not None:
            # 1. Get all train positions from MongoDB
            docs = await db[COLL_TRAIN_POSITIONS].find({}).to_list(length=100)
            positions = [_doc_to_train_position(d) for d in docs]
            if not positions:
                return

            updates = []
            for pos in positions:
                update_data = await self._update_train_position(pos)
                if update_data:
                    updates.append(update_data)

            # Persist train positions to MongoDB via bulk_write
            now_utc = datetime.now(timezone.utc)
            bulk_ops = [
                UpdateOne(
                    {"_id": p.train_id},
                    {"$set": {
                        "train_id": p.train_id,
                        "latitude": p.latitude,
                        "longitude": p.longitude,
                        "speed_kmph": p.speed_kmph,
                        "delay_minutes": p.delay_minutes,
                        "status": p.status,
                        "current_station_code": p.current_station_code,
                        "current_station_name": p.current_station_name,
                        "next_station_code": p.next_station_code,
                        "next_station_name": p.next_station_name,
                        "distance_covered_km": p.distance_covered_km,
                        "total_distance_km": p.total_distance_km,
                        "last_updated": now_utc,
                        "current_stop_index": p.current_stop_index,
                        "at_station": p.at_station,
                        "dwell_remaining_seconds": p.dwell_remaining_seconds,
                    }},
                    upsert=True
                )
                for p in positions
            ]
            if bulk_ops:
                t0 = time.perf_counter()
                await db[COLL_TRAIN_POSITIONS].bulk_write(bulk_ops, ordered=False)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                self._record_bulk_latency(latency_ms)

            # 2. Update congestion
            await self._update_congestion()

            # 3. Expire old events
            await self._expire_events()

            # 4. Calculate ETAs
            await self._calculate_all_etas()

            # 5. Check for alert-worthy conditions
            await self._check_alerts()

            # 6. Broadcast to WebSocket clients
            if updates:
                await self._broadcast_updates(updates)

        else:
            # MongoDB is disconnected
            logger.error("[SimulationEngine] MongoDB is disconnected during tick()")
            return

    async def _update_train_position(self, pos: TrainPosition, session=None) -> Optional[dict]:
        """Update a single train's position, speed, and delay."""
        # 1. Get route stops from in-memory cache
        stops = self._master_route_stops.get(pos.train_id)
        if not stops or pos.current_stop_index >= len(stops) - 1:
            return None

        current_stop = stops[pos.current_stop_index]
        next_idx = min(pos.current_stop_index + 1, len(stops) - 1)
        next_stop = stops[next_idx]

        # 2. Get train info for speed limits from in-memory cache
        train = self._master_trains.get(pos.train_id)
        if not train:
            return None

        # Simulation time step (accelerated: each tick = ~1 minute of real time)
        sim_minutes = 1.0

        if pos.at_station:
            # Handle station dwell
            pos.dwell_remaining_seconds -= settings.SIMULATION_INTERVAL
            if pos.dwell_remaining_seconds <= 0:
                pos.at_station = False
                pos.dwell_remaining_seconds = 0
                pos.current_stop_index = next_idx
                pos.speed_kmph = train.avg_speed_kmph * random.uniform(0.5, 0.8)
        else:
            # Calculate section distance
            section_distance = next_stop.distance_from_source - current_stop.distance_from_source
            if section_distance <= 0:
                section_distance = 20  # fallback

            distance_in_section = pos.distance_covered_km - current_stop.distance_from_source
            remaining_in_section = max(0, section_distance - distance_in_section)

            # Speed variation (realistic)
            target_speed = train.avg_speed_kmph
            # Check for active events affecting speed
            speed_factor = 1.0
            if pos.train_id in self._active_events:
                events = self._active_events[pos.train_id]
                for evt in events.values():
                    if evt.get("event_type") == "speed_restriction":
                        speed_factor *= (1.0 - evt.get("severity", 0.3) * 0.5)
                    elif evt.get("event_type") == "signal_congestion":
                        speed_factor *= (1.0 - evt.get("severity", 0.3) * 0.4)
                    elif evt.get("event_type") == "heavy_rain":
                        speed_factor *= (1.0 - evt.get("severity", 0.3) * 0.3)
                    elif evt.get("event_type") in ("unscheduled_halt", "level_crossing_delay"):
                        speed_factor *= 0.1

            target_speed *= speed_factor
            # Add random variation
            target_speed *= random.uniform(0.9, 1.1)
            target_speed = max(5, min(target_speed, train.max_speed_kmph))

            # Smooth speed change
            speed_change = (target_speed - pos.speed_kmph) * 0.3
            pos.speed_kmph = round(max(0, pos.speed_kmph + speed_change), 1)

            # Slow down near station
            if remaining_in_section < 5:
                pos.speed_kmph = min(pos.speed_kmph, 40)
            if remaining_in_section < 1:
                pos.speed_kmph = min(pos.speed_kmph, 15)

            # Move train
            distance_moved = (pos.speed_kmph / 60.0) * sim_minutes
            pos.distance_covered_km = round(pos.distance_covered_km + distance_moved, 2)

            # Check if reached next station
            if pos.distance_covered_km >= next_stop.distance_from_source:
                pos.distance_covered_km = next_stop.distance_from_source
                pos.at_station = True
                pos.dwell_remaining_seconds = (next_stop.halt_minutes or 2) * settings.SIMULATION_INTERVAL
                pos.current_station_code = next_stop.station_code
                pos.current_station_name = next_stop.station_name
                pos.speed_kmph = 0

                # Update next station
                if next_idx + 1 < len(stops):
                    pos.next_station_code = stops[next_idx + 1].station_code
                    pos.next_station_name = stops[next_idx + 1].station_name
                else:
                    pos.next_station_code = next_stop.station_code
                    pos.next_station_name = next_stop.station_name

            # Interpolate lat/lng
            if section_distance > 0:
                progress = min(1.0, max(0, distance_in_section / section_distance))
            else:
                progress = 0

            # Get station coordinates from in-memory cache
            curr_station = self._station_cache.get(current_stop.station_code)
            next_station = self._station_cache.get(next_stop.station_code)

            if curr_station and next_station:
                pos.latitude = round(
                    curr_station.latitude + (next_station.latitude - curr_station.latitude) * progress, 4
                )
                pos.longitude = round(
                    curr_station.longitude + (next_station.longitude - curr_station.longitude) * progress, 4
                )

        # Update delay - gradual changes with some recovery tendency
        delay_change = random.gauss(0, 0.5)
        # Events cause delay to increase
        if pos.train_id in self._active_events:
            for evt in self._active_events[pos.train_id].values():
                severity = evt.get("severity", 0.3)
                delay_change += severity * random.uniform(0.1, 0.5)
        else:
            # Natural recovery tendency
            if pos.delay_minutes > 5:
                delay_change -= random.uniform(0, 0.3)

        pos.delay_minutes = round(max(0, pos.delay_minutes + delay_change), 1)

        # Update status
        if pos.delay_minutes <= 2:
            pos.status = "On Time"
        elif pos.delay_minutes <= 10:
            pos.status = "Slight Delay"
        elif pos.delay_minutes <= 30:
            pos.status = "Delayed"
        else:
            pos.status = "Critical Delay"

        is_real = pos.train_id in self._registered_real_trains
        telemetry_source = (
            "Simulated Telemetry (No Authorized Live Feed)"
            if is_real
            else "Simulated Live Telemetry"
        )
        data_source = (
            "Real Train Master (NTES/DataMeet)"
            if is_real
            else "Demo Simulation Engine"
        )

        return {
            "train_id": pos.train_id,
            "latitude": pos.latitude,
            "longitude": pos.longitude,
            "speed_kmph": pos.speed_kmph,
            "delay_minutes": pos.delay_minutes,
            "status": pos.status,
            "current_station": pos.current_station_name,
            "next_station": pos.next_station_name,
            "distance_covered_km": pos.distance_covered_km,
            "journey_progress": round(
                (pos.distance_covered_km / pos.total_distance_km * 100)
                if pos.total_distance_km > 0 else 0, 1
            ),
            "telemetry_source": telemetry_source,
            "data_source": data_source,
            "is_simulated": True,
        }

    async def _update_congestion(self, session=None):
        """Update network congestion levels."""
        db = get_mongo_db()
        if db is not None:
            col = db[COLL_CONGESTION_SECTIONS]
            mongo_sections = await col.find({}).to_list(length=100)
            operations = []
            for sec in mongo_sections:
                current_score = float(sec.get("congestion_score", 0.0))
                change = random.gauss(0, 0.02)
                new_score = round(max(0.0, min(1.0, current_score + change)), 3)

                if new_score < 0.25:
                    status = "Normal"
                    delay_impact = 0.0
                elif new_score < 0.5:
                    status = "Moderate"
                    delay_impact = round(new_score * 5.0, 1)
                elif new_score < 0.75:
                    status = "High"
                    delay_impact = round(new_score * 10.0, 1)
                else:
                    status = "Critical"
                    delay_impact = round(new_score * 15.0, 1)

                avg_speed = round(max(20.0, 100.0 * (1.0 - new_score * 0.6)), 1)

                operations.append(
                    UpdateOne(
                        {"_id": sec["_id"]},
                        {
                            "$set": {
                                "congestion_score": new_score,
                                "status": status,
                                "delay_impact_minutes": delay_impact,
                                "avg_speed_kmph": avg_speed,
                            }
                        }
                    )
                )

            if operations:
                await col.bulk_write(operations, ordered=False)

    async def _expire_events(self, session=None):
        """Expire old operational events."""
        now = datetime.now(timezone.utc)
        db = get_mongo_db()
        if db is not None:
            cursor = db[COLL_OPERATIONAL_EVENTS].find({"active": True})
            async for event in cursor:
                exp = event.get("expires_at")
                if exp:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < now:
                        res = await db[COLL_OPERATIONAL_EVENTS].update_one(
                            {"_id": event["_id"], "active": True},
                            {"$set": {"active": False}}
                        )
                        evt_train_id = event.get("train_id")
                        evt_id_str = str(event.get("id", event["_id"]))
                        if res.modified_count > 0 and evt_train_id in self._active_events:
                            self._active_events[evt_train_id].pop(evt_id_str, None)
                            if not self._active_events[evt_train_id]:
                                del self._active_events[evt_train_id]
            return

    async def _calculate_all_etas(self, session=None):
        """Recalculate ETAs for all active trains."""
        db = get_mongo_db()
        if db is None:
            return

        docs = await db[COLL_TRAIN_POSITIONS].find({}).to_list(length=100)
        positions = [_doc_to_train_position(d) for d in docs]

        _eta_bulk_ops = []  # Collect MongoDB ETA upsert ops across all trains

        for pos in positions:
            all_stops = self._master_route_stops.get(pos.train_id)
            if all_stops:
                upcoming_stops = [s for s in all_stops if s.stop_number > pos.current_stop_index]
            else:
                upcoming_stops = []
                t_doc = await db[COLL_TRAINS].find_one({"_id": pos.train_id})
                if t_doc and "stops" in t_doc:
                    upcoming_stops = [
                        RouteStop(
                            train_id=pos.train_id,
                            station_code=s.get("station_code"),
                            station_name=s.get("station_name"),
                            arrival=s.get("arrival"),
                            departure=s.get("departure"),
                            distance_from_source=float(s.get("distance_from_source") or 0.0),
                            day=int(s.get("day", 1)),
                            stop_number=int(s.get("stop_number", idx + 1)),
                            halt_minutes=int(s.get("halt_minutes", 2)),
                        )
                        for idx, s in enumerate(t_doc["stops"])
                        if int(s.get("stop_number", idx + 1)) > pos.current_stop_index
                    ]

            cumulative_delay = pos.delay_minutes
            cumulative_distance = pos.distance_covered_km

            for stop in upcoming_stops[:10]:  # Limit to next 10 stops
                # Calculate running time to this stop
                distance = stop.distance_from_source - cumulative_distance
                if distance <= 0:
                    continue

                # Get train for avg speed
                train = self._master_trains.get(pos.train_id)
                if not train:
                    t_doc = await db[COLL_TRAINS].find_one({"_id": pos.train_id})
                    if t_doc:
                        train = Train(
                            train_id=t_doc.get("train_id", pos.train_id),
                            train_name=t_doc.get("train_name", pos.train_id),
                            avg_speed_kmph=float(t_doc.get("avg_speed_kmph", 60.0)),
                            max_speed_kmph=float(t_doc.get("max_speed_kmph", 130.0)),
                            total_distance_km=float(t_doc.get("total_distance_km", 0.0)),
                            train_type=t_doc.get("train_type", "express"),
                            zone=t_doc.get("zone", "NR"),
                        )
                if not train:
                    break

                avg_speed = max(30, pos.speed_kmph if pos.speed_kmph > 0 else train.avg_speed_kmph)
                running_time_minutes = (distance / avg_speed) * 60

                # ML prediction or fallback
                predicted_additional = await self._predict_additional_delay(
                    pos, train, stop, distance
                )
                total_predicted_delay = round(cumulative_delay + predicted_additional, 1)

                # Confidence calculation
                confidence = self._calculate_confidence(pos, distance, predicted_additional)
                if confidence >= 80:
                    confidence_level = "High"
                elif confidence >= 60:
                    confidence_level = "Medium"
                else:
                    confidence_level = "Low"

                # Prediction factors
                factors = self._get_prediction_factors(pos, train, distance, predicted_additional)

                # Calculate predicted arrival time
                scheduled = stop.arrival or stop.departure or "00:00"
                try:
                    sched_parts = scheduled.split(":")
                    sched_hour = int(sched_parts[0])
                    sched_min = int(sched_parts[1])
                    pred_min = sched_min + int(total_predicted_delay)
                    pred_hour = sched_hour + pred_min // 60
                    pred_min = pred_min % 60
                    pred_hour = pred_hour % 24
                    predicted_arrival = f"{pred_hour:02d}:{pred_min:02d}"
                except (ValueError, IndexError):
                    predicted_arrival = scheduled

                now_utc = datetime.now(timezone.utc)
                factors_list = [f.__dict__ if hasattr(f, '__dict__') else f for f in factors]

                eta_doc = {
                    "train_id": pos.train_id,
                    "station_code": stop.station_code,
                    "station_name": stop.station_name,
                    "scheduled_arrival": scheduled,
                    "predicted_arrival": predicted_arrival,
                    "predicted_delay_minutes": total_predicted_delay,
                    "confidence": confidence,
                    "confidence_level": confidence_level,
                    "factors": factors_list,
                    "created_at": now_utc,
                }

                _eta_bulk_ops.append(UpdateOne(
                    {"train_id": pos.train_id, "station_code": stop.station_code},
                    {"$set": eta_doc},
                    upsert=True
                ))

        # Flush MongoDB ETA bulk write
        if _eta_bulk_ops:
            try:
                _t0 = time.perf_counter()
                await db[COLL_ETA_PREDICTIONS].bulk_write(_eta_bulk_ops, ordered=False)
                _elapsed = (time.perf_counter() - _t0) * 1000
                self._record_eta_latency(_elapsed)
            except Exception as _e:
                import logging
                logging.getLogger(__name__).warning(f"[ETA bulk_write] Error: {_e}")

    async def _predict_additional_delay(self, pos, train, stop, distance) -> float:
        """Predict additional delay using ML model or fallback."""
        if self._eta_predictor and self._eta_predictor.is_model_loaded():
            try:
                features = {
                    "current_delay_minutes": pos.delay_minutes,
                    "current_speed_kmph": pos.speed_kmph,
                    "avg_speed_section_kmph": train.avg_speed_kmph,
                    "distance_to_next_station_km": distance,
                    "distance_to_destination_km": max(0, train.total_distance_km - pos.distance_covered_km),
                    "historical_avg_delay_minutes": pos.delay_minutes * 0.8,
                    "historical_section_delay_minutes": pos.delay_minutes * 0.5,
                    "station_dwell_minutes": stop.halt_minutes or 2,
                    "congestion_score": 0.3,
                    "weather_severity": 0.1,
                    "speed_restriction_active": 1 if pos.train_id in self._active_events else 0,
                    "speed_restriction_severity": 0.0,
                    "preceding_train_delay_minutes": random.uniform(0, 10),
                    "hour_of_day": datetime.now().hour,
                    "day_of_week": datetime.now().weekday(),
                    "number_of_stops_remaining": 5,
                    "train_type": train.train_type,
                    "zone": train.zone,
                    "is_holiday": 0,
                    "recent_speed_trend": 0.0,
                    "recent_delay_trend": 0.1,
                }

                # Check for active events
                if pos.train_id in self._active_events:
                    for evt in self._active_events[pos.train_id].values():
                        if evt.get("event_type") == "speed_restriction":
                            features["speed_restriction_active"] = 1
                            features["speed_restriction_severity"] = evt.get("severity", 0.5)
                        if evt.get("event_type") in ("signal_congestion", "station_overcrowding"):
                            features["congestion_score"] = max(features["congestion_score"], evt.get("severity", 0.5))
                        if evt.get("event_type") == "heavy_rain":
                            features["weather_severity"] = max(features["weather_severity"], evt.get("severity", 0.5))

                result = self._eta_predictor.predict(features)
                return max(0, result.get("predicted_additional_delay", 0))
            except Exception as e:
                print(f"[ML Prediction] Error: {e}")

        # Fallback: rule-based prediction
        return self._fallback_predict(pos, train, distance)

    def _fallback_predict(self, pos, train, distance) -> float:
        """Simple rule-based fallback when ML model is unavailable."""
        additional = 0.0

        # Longer distances = more chance of additional delay
        if distance > 200:
            additional += random.uniform(0, 3)
        elif distance > 100:
            additional += random.uniform(0, 2)

        # Currently delayed trains tend to accumulate more delay
        if pos.delay_minutes > 15:
            additional += random.uniform(0, 2)
        elif pos.delay_minutes > 5:
            additional += random.uniform(-1, 1)

        # Events cause additional delay
        if pos.train_id in self._active_events:
            for evt in self._active_events[pos.train_id].values():
                severity = evt.get("severity", 0.3)
                additional += severity * random.uniform(2, 8)

        # Slow trains accumulate more delay
        if pos.speed_kmph > 0 and pos.speed_kmph < train.avg_speed_kmph * 0.6:
            additional += random.uniform(1, 3)

        # Recovery tendency for moderate delays
        if pos.delay_minutes > 3 and pos.delay_minutes < 15:
            additional -= random.uniform(0, 1)

        return max(0, round(additional, 1))

    def _calculate_confidence(self, pos, distance, predicted_additional) -> float:
        """Calculate prediction confidence score."""
        confidence = 90.0

        # Distance reduces confidence
        if distance > 500:
            confidence -= 15
        elif distance > 200:
            confidence -= 10
        elif distance > 100:
            confidence -= 5

        # High delay reduces confidence
        if pos.delay_minutes > 30:
            confidence -= 15
        elif pos.delay_minutes > 15:
            confidence -= 10
        elif pos.delay_minutes > 5:
            confidence -= 5

        # Active events reduce confidence
        if pos.train_id in self._active_events:
            confidence -= len(self._active_events[pos.train_id]) * 5

        # Low speed = uncertain
        if pos.speed_kmph > 0 and pos.speed_kmph < 20:
            confidence -= 10

        # Recent data = higher confidence
        last_up = pos.last_updated
        if last_up.tzinfo is None:
            last_up = last_up.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last_up).total_seconds()
        if age > 60:
            confidence -= 5

        return round(max(30, min(98, confidence)), 1)

    def _get_prediction_factors(self, pos, train, distance, predicted_additional) -> list:
        """Get factors contributing to the prediction."""
        factors = []

        # Current delay factor
        if pos.delay_minutes > 0:
            factors.append({
                "factor_name": "Current Delay",
                "impact_minutes": round(pos.delay_minutes * 0.3, 1),
                "description": f"Train is currently {pos.delay_minutes:.0f} min behind schedule",
                "severity": "high" if pos.delay_minutes > 15 else "medium" if pos.delay_minutes > 5 else "low"
            })

        # Speed factor
        if pos.speed_kmph > 0 and pos.speed_kmph < train.avg_speed_kmph * 0.7:
            impact = round((1 - pos.speed_kmph / train.avg_speed_kmph) * 5, 1)
            factors.append({
                "factor_name": "Reduced Speed",
                "impact_minutes": impact,
                "description": f"Running at {pos.speed_kmph:.0f} km/h vs avg {train.avg_speed_kmph:.0f} km/h",
                "severity": "medium"
            })

        # Event factors
        if pos.train_id in self._active_events:
            for evt in self._active_events[pos.train_id].values():
                event_names = {
                    "signal_congestion": "Signal Congestion",
                    "speed_restriction": "Speed Restriction",
                    "unscheduled_halt": "Unscheduled Halt",
                    "track_maintenance": "Track Maintenance",
                    "heavy_rain": "Heavy Rain",
                    "station_overcrowding": "Station Overcrowding",
                    "preceding_train_delay": "Preceding Train Delay",
                    "level_crossing_delay": "Level Crossing Delay",
                }
                name = event_names.get(evt.get("event_type", ""), evt.get("event_type", "Unknown"))
                severity = evt.get("severity", 0.3)
                impact = round(severity * random.uniform(3, 8), 1)
                factors.append({
                    "factor_name": name,
                    "impact_minutes": impact,
                    "description": evt.get("description", f"{name} detected"),
                    "severity": "high" if severity > 0.7 else "medium" if severity > 0.3 else "low"
                })

        # Distance factor
        if distance > 200:
            factors.append({
                "factor_name": "Long Distance Remaining",
                "impact_minutes": round(distance * 0.005, 1),
                "description": f"{distance:.0f} km remaining - uncertainty increases with distance",
                "severity": "low"
            })

        # Historical pattern factor
        if pos.delay_minutes > 3:
            factors.append({
                "factor_name": "Historical Pattern",
                "impact_minutes": round(pos.delay_minutes * 0.15, 1),
                "description": "Similar delays observed in historical data for this route",
                "severity": "low"
            })

        # If no factors, add a baseline
        if not factors:
            factors.append({
                "factor_name": "Normal Operations",
                "impact_minutes": 0.0,
                "description": "Train running within normal parameters",
                "severity": "low"
            })

        return factors

    async def _check_alerts(self, session=None):
        """Generate alerts for significant conditions."""
        if self._tick_count % 5 != 0:  # Check every 5 ticks
            return

        db = get_mongo_db()
        if db is None:
            return

        docs = await db[COLL_TRAIN_POSITIONS].find({}).to_list(length=100)
        positions = [_doc_to_train_position(d) for d in docs]

        for pos in positions:
            train = self._master_trains.get(pos.train_id)
            if not train:
                t_doc = await db[COLL_TRAINS].find_one({"_id": pos.train_id})
                if t_doc:
                    train = Train(
                        train_id=t_doc.get("train_id", pos.train_id),
                        train_name=t_doc.get("train_name", pos.train_id),
                    )
            train_name = train.train_name if train else pos.train_id

            if pos.delay_minutes > 30 and random.random() < 0.3:
                await alert_service.create_alert(
                    train_id=pos.train_id,
                    severity="critical",
                    alert_type="critical_delay",
                    message=f"Critical delay of {pos.delay_minutes:.0f} minutes predicted for {train_name}",
                    location=pos.current_station_name or "En route",
                    eta_impact=pos.delay_minutes,
                )
            elif pos.delay_minutes > 15 and random.random() < 0.2:
                await alert_service.create_alert(
                    train_id=pos.train_id,
                    severity="warning",
                    alert_type="significant_delay",
                    message=f"ETA increased significantly for {train_name} (+{pos.delay_minutes:.0f} min)",
                    location=pos.current_station_name or "En route",
                    eta_impact=pos.delay_minutes,
                )
            elif pos.delay_minutes <= 2 and random.random() < 0.1:
                await alert_service.create_alert(
                    train_id=pos.train_id,
                    severity="success",
                    alert_type="on_schedule",
                    message=f"{train_name} is running on schedule",
                    location=pos.current_station_name or "En route",
                    eta_impact=0,
                )

    async def inject_event(self, event_data: dict) -> dict:
        """Inject an operational event into the simulation."""
        severity = float(event_data.get("severity", 0.5))
        event_type = event_data.get("event_type", "")
        duration = int(event_data.get("duration_minutes", 30))
        train_id = event_data.get("train_id", "")
        db = get_mongo_db()

        # Different event types have different delay impacts
        impact_multipliers = {
            "signal_congestion": 8,
            "speed_restriction": 6,
            "unscheduled_halt": 10,
            "track_maintenance": 12,
            "heavy_rain": 5,
            "station_overcrowding": 4,
            "preceding_train_delay": 7,
            "level_crossing_delay": 3,
        }
        multiplier = impact_multipliers.get(event_type, 5)
        impact_delay = round(severity * multiplier, 1)

        description = event_data.get("description", "")
        if not description:
            event_descriptions = {
                "signal_congestion": f"Signal congestion detected with severity {severity:.1f}",
                "speed_restriction": f"Temporary speed restriction imposed (severity: {severity:.1f})",
                "unscheduled_halt": f"Unscheduled halt for {duration} minutes",
                "track_maintenance": f"Track maintenance block for {duration} minutes",
                "heavy_rain": f"Heavy rain affecting operations (severity: {severity:.1f})",
                "station_overcrowding": f"Station overcrowding causing delays",
                "preceding_train_delay": f"Preceding train delayed, causing cascading effect",
                "level_crossing_delay": f"Level crossing delay for {duration} minutes",
            }
            description = event_descriptions.get(event_type, f"Operational event: {event_type}")

        now_utc = datetime.now(timezone.utc)

        if db is not None:
            # 1. Resolve random train_id via MongoDB
            if not train_id or train_id.lower() == "random":
                train_docs = await db[COLL_TRAIN_POSITIONS].find({}, {"_id": 1, "train_id": 1}).to_list(length=100)
                if train_docs:
                    train_id = random.choice(train_docs).get("train_id", train_docs[0]["_id"])
                    event_data["train_id"] = train_id
                else:
                    return {"error": "No active trains to apply event to"}

            counter = await db[COLL_COUNTERS].find_one_and_update(
                {"_id": "event_id"},
                {"$inc": {"seq": 1}},
                return_document=ReturnDocument.AFTER,
            )
            if not counter:
                max_evt = await db[COLL_OPERATIONAL_EVENTS].find_one(sort=[("id", -1)])
                seq = int(max_evt["id"]) + 1 if max_evt and "id" in max_evt else 1
                await db[COLL_COUNTERS].update_one(
                    {"_id": "event_id"},
                    {"$set": {"seq": seq}},
                    upsert=True,
                )
                event_id = seq
            else:
                event_id = int(counter["seq"])

            event_doc = {
                "_id": event_id,
                "id": event_id,
                "event_type": event_type,
                "train_id": event_data["train_id"],
                "location": event_data.get("location", "En route"),
                "severity": severity,
                "duration_minutes": duration,
                "description": description,
                "impact_delay_minutes": impact_delay,
                "active": True,
                "created_at": now_utc,
                "expires_at": now_utc + timedelta(minutes=duration),
            }
            await db[COLL_OPERATIONAL_EVENTS].insert_one(event_doc)

            # Apply immediate effects in MongoDB
            pos = None
            pos_doc = await db[COLL_TRAIN_POSITIONS].find_one({"_id": event_data["train_id"]})
            if pos_doc:
                new_delay = round(float(pos_doc.get("delay_minutes", 0.0)) + impact_delay, 1)
                if new_delay > 30:
                    new_status = "Critical Delay"
                elif new_delay > 10:
                    new_status = "Delayed"
                elif new_delay > 2:
                    new_status = "Slight Delay"
                else:
                    new_status = "On Time"

                await db[COLL_TRAIN_POSITIONS].update_one(
                    {"_id": event_data["train_id"]},
                    {"$set": {
                        "delay_minutes": new_delay,
                        "status": new_status,
                        "last_updated": datetime.now(timezone.utc),
                    }}
                )
                pos_doc["delay_minutes"] = new_delay
                pos_doc["status"] = new_status
                pos = _doc_to_train_position(pos_doc)

            if pos and pos.current_station_code and pos.next_station_code:
                sec_id_1 = f"{pos.current_station_code}-{pos.next_station_code}"
                sec_id_2 = f"{pos.next_station_code}-{pos.current_station_code}"
                col = db[COLL_CONGESTION_SECTIONS]
                affected_sec = await col.find_one({
                    "$or": [
                        {"_id": sec_id_1},
                        {"_id": sec_id_2},
                        {"section_id": sec_id_1},
                        {"section_id": sec_id_2},
                    ]
                })
                if affected_sec:
                    new_score = round(min(1.0, max(float(affected_sec.get("congestion_score", 0.0)), severity)), 3)
                    new_status = "Critical" if new_score >= 0.75 else "High" if new_score >= 0.5 else "Moderate"
                    new_delay = round(new_score * 15.0, 1)
                    new_speed = round(max(20.0, 100.0 * (1.0 - new_score * 0.6)), 1)
                    await col.update_one(
                        {"_id": affected_sec["_id"]},
                        {
                            "$set": {
                                "congestion_score": new_score,
                                "status": new_status,
                                "delay_impact_minutes": new_delay,
                                "avg_speed_kmph": new_speed,
                            }
                        }
                    )

            train = self._master_trains.get(event_data["train_id"])
            if not train:
                t_doc = await db[COLL_TRAINS].find_one({"_id": event_data["train_id"]})
                train_name = t_doc.get("train_name", event_data["train_id"]) if t_doc else event_data["train_id"]
            else:
                train_name = train.train_name

            alert_dict = await alert_service.create_alert(
                train_id=event_data["train_id"],
                severity="warning" if severity < 0.7 else "critical",
                alert_type=event_type,
                message=f"{description} - ETA impact: +{impact_delay:.0f} min for {train_name}",
                location=event_data.get("location", "En route"),
                eta_impact=impact_delay,
            )
            alert_id_str = str(alert_dict["id"])
            alert_created_at = alert_dict["created_at"]
            alert_severity = alert_dict["severity"]
            alert_message = alert_dict["message"]
            alert_location = alert_dict["location"]

            if event_data["train_id"] not in self._active_events:
                self._active_events[event_data["train_id"]] = {}
            self._active_events[event_data["train_id"]][str(event_id)] = {
                "event_type": event_type,
                "severity": severity,
                "description": description,
                "impact_delay": impact_delay,
            }

            await self._calculate_all_etas()

            await self._broadcast_event({
                "type": "alert",
                "alert": {
                    "id": alert_id_str,
                    "train_id": event_data["train_id"],
                    "train_name": train_name,
                    "severity": alert_severity,
                    "alert_type": event_type,
                    "message": alert_message,
                    "location": alert_location,
                    "eta_impact_minutes": impact_delay,
                    "created_at": alert_created_at,
                    "acknowledged": False,
                }
            })

            if pos:
                is_real = pos.train_id in self._registered_real_trains
                await self._broadcast_event({
                    "type": "train_update",
                    "position": {
                        "train_id": pos.train_id,
                        "latitude": pos.latitude,
                        "longitude": pos.longitude,
                        "speed_kmph": pos.speed_kmph,
                        "delay_minutes": pos.delay_minutes,
                        "status": pos.status,
                        "current_station": pos.current_station_name,
                        "next_station": pos.next_station_name,
                        "distance_covered_km": pos.distance_covered_km,
                        "journey_progress": round(
                            (pos.distance_covered_km / pos.total_distance_km * 100)
                            if pos.total_distance_km > 0 else 0, 1
                        ),
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                        "telemetry_source": "Simulated Telemetry (No Authorized Live Feed)" if is_real else "Simulated Live Telemetry",
                        "data_source": "Real Train Master (NTES/DataMeet)" if is_real else "Demo Simulation Engine",
                    }
                })

            return {
                "event_id": event_id,
                "impact_delay_minutes": impact_delay,
                "description": description,
                "message": f"Event applied. ETA for {train_name} increased by {impact_delay:.0f} minutes.",
            }

        raise RuntimeError("MongoDB is unavailable")

    async def _broadcast_event(self, event_payload: dict):
        """Broadcast a single structured message to all WebSocket clients."""
        if not self._ws_clients:
            return

        message = json.dumps(event_payload)
        disconnected = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self._ws_clients.remove(ws)

    async def _broadcast_updates(self, updates: list):
        """Broadcast updates to all connected WebSocket clients."""
        if not self._ws_clients:
            return

        # 1. Standard batch update
        message = json.dumps({
            "type": "simulation_update",
            "data": {
                "trains": updates,
                "tick": self._tick_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        })

        disconnected = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(message)
                # Also send individual train_update messages so hooks listening to onTrainUpdate receive them
                for u in updates:
                    await ws.send_text(json.dumps({
                        "type": "train_update",
                        "position": u
                    }))
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self._ws_clients.remove(ws)

    def get_state(self) -> dict:
        return {
            "running": self._is_running,
            "tick_count": self._tick_count,
            "interval_seconds": settings.SIMULATION_INTERVAL,
            "last_update": self._last_update.isoformat(),
            "active_events": len(self._active_events),
            "simulated_real_trains": list(self._registered_real_trains),
        }

    def is_train_simulated(self, train_id: str) -> bool:
        """Check if a real train is actively being simulated in the engine."""
        return train_id in self._registered_real_trains

    def get_registered_real_trains(self) -> List[str]:
        """Return list of actively simulated real train numbers."""
        return list(self._registered_real_trains)

    async def register_real_train(self, train_number: str) -> dict:
        """
        Dynamically register a real train from the real_trains catalog into the simulation engine.
        Binds its authentic timetable/station sequence into active simulation structures,
        allowing it to continuously tick, move along route, compute delay, feed ML features, and broadcast.
        """
        db = get_mongo_db()
        if db is not None:
            real_t = await db[COLL_REAL_TRAINS].find_one({"$or": [{"_id": train_number}, {"train_number": train_number}]})
            if not real_t:
                return {
                    "success": False,
                    "error": f"Real train {train_number} not found in master catalog."
                }

            real_stops = real_t.get("stops", [])
            if not real_stops:
                return {
                    "success": False,
                    "error": f"No timetable stops found for real train {train_number}."
                }

            real_stops = sorted(real_stops, key=lambda s: int(s.get("sequence", 0)))
            dep_time = real_stops[0].get("departure_time") or "00:00"
            arr_time = real_stops[-1].get("arrival_time") or "00:00"
            total_dist = float(real_t.get("distance") or real_stops[-1].get("distance") or 500.0)

            # Ensure station cache has coordinates for all stops
            needed_codes = [
                s.get("station_code") for s in real_stops
                if s.get("station_code") and s.get("station_code") not in self._station_cache
            ]
            if needed_codes:
                st_docs = await db[COLL_STATIONS].find({"station_code": {"$in": needed_codes}}).to_list(length=len(needed_codes) + 10)
                for st in st_docs:
                    c = st.get("station_code", st.get("_id"))
                    self._station_cache[c] = Station(
                        station_code=c,
                        station_name=st.get("station_name", ""),
                        latitude=float(st.get("latitude", 0.0)),
                        longitude=float(st.get("longitude", 0.0)),
                    )
                for s in real_stops:
                    c = s.get("station_code")
                    if c and c not in self._station_cache:
                        seq = int(s.get("sequence", 1))
                        base_lat = 26.9 + (seq * 0.15)
                        base_lon = 70.9 + (seq * 0.12)
                        self._station_cache[c] = Station(
                            station_code=c,
                            station_name=s.get("station_name", c),
                            latitude=round(base_lat, 4),
                            longitude=round(base_lon, 4),
                        )

            # Upsert Train with embedded stops to COLL_TRAINS
            route_stops_mongo = [
                {
                    "stop_number": int(s.get("sequence", idx + 1)),
                    "station_code": s.get("station_code"),
                    "station_name": s.get("station_name"),
                    "arrival": s.get("arrival_time"),
                    "departure": s.get("departure_time"),
                    "distance_from_source": float(s.get("distance") or 0.0),
                    "day": int(s.get("day_offset") or 1),
                    "halt_minutes": int(s.get("halt_minutes") or 2),
                }
                for idx, s in enumerate(real_stops)
            ]
            train_doc = {
                "_id": train_number,
                "train_id": train_number,
                "train_name": real_t.get("train_name"),
                "train_number": train_number,
                "train_type": real_t.get("train_type") or "superfast",
                "source": real_t.get("source_station_name") or real_t.get("source_station"),
                "source_code": real_t.get("source_station"),
                "destination": real_t.get("destination_station_name") or real_t.get("destination_station"),
                "destination_code": real_t.get("destination_station"),
                "zone": real_t.get("zone", "NWR"),
                "total_distance_km": total_dist,
                "scheduled_departure": dep_time,
                "scheduled_arrival": arr_time,
                "avg_speed_kmph": 65.0,
                "max_speed_kmph": 110.0,
                "days_of_run": real_t.get("running_days") or "Daily",
                "stops": route_stops_mongo,
            }
            await db[COLL_TRAINS].update_one({"_id": train_number}, {"$set": train_doc}, upsert=True)

            first_st_code = real_stops[0].get("station_code")
            origin_st = self._station_cache.get(first_st_code)
            init_lat = origin_st.latitude if origin_st else 26.9165
            init_lon = origin_st.longitude if origin_st else 70.9282

            next_name = real_stops[1].get("station_name") if len(real_stops) > 1 else real_stops[0].get("station_name")
            next_code = real_stops[1].get("station_code") if len(real_stops) > 1 else real_stops[0].get("station_code")
            halt_0 = int(real_stops[0].get("halt_minutes") or 2)

            pos_doc = {
                "_id": train_number,
                "train_id": train_number,
                "latitude": init_lat,
                "longitude": init_lon,
                "speed_kmph": 0.0,
                "delay_minutes": 0.0,
                "status": "On Time",
                "current_station_code": first_st_code,
                "current_station_name": real_stops[0].get("station_name"),
                "next_station_code": next_code,
                "next_station_name": next_name,
                "distance_covered_km": 0.0,
                "total_distance_km": total_dist,
                "last_updated": datetime.now(timezone.utc),
                "current_stop_index": 0,
                "at_station": True,
                "dwell_remaining_seconds": halt_0 * settings.SIMULATION_INTERVAL,
            }
            await db[COLL_TRAIN_POSITIONS].update_one({"_id": train_number}, {"$set": pos_doc}, upsert=True)

            t_obj = Train(
                train_id=train_number,
                train_name=real_t.get("train_name"),
                train_number=train_number,
                train_type=real_t.get("train_type") or "superfast",
                source=real_t.get("source_station_name") or real_t.get("source_station"),
                source_code=real_t.get("source_station"),
                destination=real_t.get("destination_station_name") or real_t.get("destination_station"),
                destination_code=real_t.get("destination_station"),
                zone=real_t.get("zone", "NWR"),
                total_distance_km=total_dist,
                scheduled_departure=dep_time,
                scheduled_arrival=arr_time,
                avg_speed_kmph=65.0,
                max_speed_kmph=110.0,
                days_of_run=real_t.get("running_days") or "Daily",
            )
            self._registered_real_trains.add(train_number)
            self._master_trains[train_number] = t_obj
            self._master_route_stops[train_number] = [
                RouteStop(
                    train_id=train_number,
                    station_code=s.get("station_code"),
                    station_name=s.get("station_name"),
                    arrival=s.get("arrival_time"),
                    departure=s.get("departure_time"),
                    distance_from_source=float(s.get("distance") or 0.0),
                    day=int(s.get("day_offset") or 1),
                    stop_number=int(s.get("sequence", idx + 1)),
                    halt_minutes=int(s.get("halt_minutes") or 2),
                )
                for idx, s in enumerate(real_stops)
            ]

            if not self._is_running:
                await self.start()

            return {
                "success": True,
                "train_number": train_number,
                "train_name": real_t.get("train_name"),
                "status": "registered",
                "message": f"Real train {train_number} ({real_t.get('train_name')}) successfully registered in dynamic simulation.",
                "telemetry_source": "Simulated Telemetry (No Authorized Live Feed)",
                "data_source": f"Real Train Master ({real_t.get('data_source', 'NTES/DataMeet')})",
            }

        raise RuntimeError("MongoDB is unavailable")

    async def unregister_real_train(self, train_number: str) -> dict:
        """
        Unregister a real train from active simulation.
        Removes its live TrainPosition and active operational events while preserving master timetable records.
        """
        db = get_mongo_db()
        if db is not None:
            await db[COLL_OPERATIONAL_EVENTS].delete_many({"train_id": train_number})
            await db[COLL_TRAIN_POSITIONS].delete_one({"_id": train_number})
            await db[COLL_ETA_PREDICTIONS].delete_many({"train_id": train_number})
            await db[COLL_TRAINS].delete_one({"_id": train_number})
        else:
            raise RuntimeError("MongoDB is unavailable")

        self._registered_real_trains.discard(train_number)
        self._active_events.pop(train_number, None)
        self._master_trains.pop(train_number, None)
        self._master_route_stops.pop(train_number, None)

        return {
            "success": True,
            "train_number": train_number,
            "status": "unregistered",
            "message": f"Real train {train_number} unregistered from dynamic simulation. Master schedule preserved.",
        }

    async def resolve_train_issue(self, train_id: str) -> dict:
        """
        Resolve active operational issues for a train.
        Deactivates associated operational events, removes them from active events cache,
        recovers the train delay out of critical status using operational delay rollback,
        recalculates ETAs, updates train status, and broadcasts live telemetry.
        """
        db = get_mongo_db()
        if db is not None:
            pos_doc = await db[COLL_TRAIN_POSITIONS].find_one({"_id": train_id})
            if not pos_doc:
                return {"success": False, "error": f"Train {train_id} not found in active simulation"}
            pos = _doc_to_train_position(pos_doc)

            cursor = db[COLL_OPERATIONAL_EVENTS].find({
                "train_id": train_id,
                "active": True
            })
            active_events = await cursor.to_list(length=100)
            total_impact = sum(float(e.get("impact_delay_minutes") or 0.0) for e in active_events)
            if active_events:
                await db[COLL_OPERATIONAL_EVENTS].update_many(
                    {"train_id": train_id, "active": True},
                    {"$set": {"active": False}}
                )

            self._active_events.pop(train_id, None)

            if total_impact > 0:
                pos.delay_minutes = max(0.0, pos.delay_minutes - total_impact)
            if pos.delay_minutes > 30.0:
                pos.delay_minutes = round(max(2.0, pos.delay_minutes - 25.0), 1)
            else:
                pos.delay_minutes = round(pos.delay_minutes, 1)

            if pos.delay_minutes <= 2.0:
                pos.status = "On Time"
            elif pos.delay_minutes <= 10.0:
                pos.status = "Slight Delay"
            elif pos.delay_minutes <= 30.0:
                pos.status = "Delayed"
            else:
                pos.status = "Critical Delay"

            await db[COLL_TRAIN_POSITIONS].update_one(
                {"_id": train_id},
                {"$set": {
                    "delay_minutes": pos.delay_minutes,
                    "status": pos.status,
                    "last_updated": datetime.now(timezone.utc),
                }}
            )

            train = self._master_trains.get(train_id)
            if not train:
                t_doc = await db[COLL_TRAINS].find_one({"_id": train_id})
                train_name = t_doc.get("train_name", train_id) if t_doc else train_id
            else:
                train_name = train.train_name

            res_alert = await alert_service.create_alert(
                train_id=train_id,
                severity="success",
                alert_type="issue_resolved",
                message=f"Operational issue resolved for {train_name}. Speed restored, delay reduced to {pos.delay_minutes} min.",
                location=pos.current_station_name or "En route",
                eta_impact=-round(total_impact, 1),
            )
            res_alert_id = str(res_alert["id"])
            res_alert_created_at = res_alert["created_at"]
            res_alert_msg = res_alert["message"]
            res_alert_loc = res_alert["location"]

            await self._calculate_all_etas()

            is_real = pos.train_id in self._registered_real_trains
            telemetry_source = (
                "Simulated Telemetry (No Authorized Live Feed)"
                if is_real
                else "Simulated Live Telemetry"
            )
            data_source = (
                "Real Train Master (NTES/DataMeet)"
                if is_real
                else "Demo Simulation Engine"
            )

            updated_pos = {
                "train_id": pos.train_id,
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                "speed_kmph": pos.speed_kmph,
                "delay_minutes": pos.delay_minutes,
                "status": pos.status,
                "current_station": pos.current_station_name,
                "next_station": pos.next_station_name,
                "distance_covered_km": pos.distance_covered_km,
                "journey_progress": round(
                    (pos.distance_covered_km / pos.total_distance_km * 100)
                    if pos.total_distance_km > 0 else 0, 1
                ),
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "telemetry_source": telemetry_source,
                "data_source": data_source,
                "is_simulated": True,
            }

            await self._broadcast_event({
                "type": "train_update",
                "position": updated_pos,
            })
            await self._broadcast_event({
                "type": "alert",
                "alert": {
                    "id": res_alert_id,
                    "train_id": train_id,
                    "train_name": train_name,
                    "severity": "success",
                    "alert_type": "issue_resolved",
                    "message": res_alert_msg,
                    "location": res_alert_loc,
                    "eta_impact_minutes": -round(total_impact, 1),
                    "created_at": res_alert_created_at,
                    "acknowledged": False,
                }
            })

            return {
                "success": True,
                "train_id": train_id,
                "train_name": train_name,
                "delay_minutes": pos.delay_minutes,
                "status": pos.status,
                "message": f"Issue resolved for Train {train_id} ({train_name}).",
                "position": updated_pos,
            }
        raise RuntimeError("MongoDB is unavailable")



# Singleton
simulation_engine = SimulationEngine()

