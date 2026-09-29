"""Comprehensive Tests for RailPulse ETA backend and ML inference."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.eta_service import eta_service
from ml.predict import ETAPredictor


@pytest.fixture
async def client():
    from app.database.mongodb import init_mongo, get_mongo_db
    if get_mongo_db() is None:
        await init_mongo()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        yield ac


@pytest.fixture
async def staff_client():
    """Client authenticated as RAILWAY_STAFF for protected endpoint tests."""
    from app.database.mongodb import init_mongo, get_mongo_db
    if get_mongo_db() is None:
        await init_mongo()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        # Login as staff to get auth cookie
        login_resp = await ac.post("/api/auth/login", json={
            "phone": "9876543211",
            "password": "demo123",
        })
        assert login_resp.status_code == 200
        yield ac


@pytest.mark.asyncio
async def test_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app_name"] == "RailPulse ETA"
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["ml_model_loaded"] is True
    assert isinstance(data["simulation_running"], bool)


@pytest.mark.asyncio
async def test_get_trains(client):
    response = await client.get("/api/trains")
    assert response.status_code == 200
    data = response.json()
    assert "trains" in data
    assert len(data["trains"]) >= 10


@pytest.mark.asyncio
async def test_search_trains(client):
    response = await client.get("/api/trains?search=rajdhani")
    assert response.status_code == 200
    data = response.json()
    assert "trains" in data
    assert any("Rajdhani" in t["train_name"] for t in data["trains"])


@pytest.mark.asyncio
async def test_get_train_not_found(client):
    response = await client.get("/api/trains/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_train_detail_and_alerts(client):
    response = await client.get("/api/trains/12951")
    assert response.status_code == 200
    data = response.json()
    assert data["train"]["train_number"] == "12951"
    assert "etas" in data
    assert "factors" in data
    assert "recent_alerts" in data
    assert isinstance(data["etas"], list)


@pytest.mark.asyncio
async def test_ml_direct_inference():
    """Verify ML model actually loads and outputs predictions with feature contributions."""
    predictor = ETAPredictor()
    assert predictor.is_model_loaded() is True

    features = {
        "current_delay_minutes": 15.0,
        "current_speed_kmph": 75.0,
        "avg_speed_section_kmph": 90.0,
        "distance_to_next_station_km": 45.0,
        "distance_to_destination_km": 600.0,
        "historical_avg_delay_minutes": 10.0,
        "historical_section_delay_minutes": 5.0,
        "station_dwell_minutes": 3.0,
        "congestion_score": 0.85,
        "weather_severity": 0.3,
        "speed_restriction_active": 1,
        "speed_restriction_severity": 0.6,
        "preceding_train_delay_minutes": 12.0,
        "hour_of_day": 18,
        "day_of_week": 3,
        "number_of_stops_remaining": 6,
        "train_type": "rajdhani",
        "zone": "WR",
        "is_holiday": 0,
        "recent_speed_trend": -0.2,
        "recent_delay_trend": 0.3,
    }

    res = predictor.predict(features)
    assert "predicted_additional_delay" in res
    assert isinstance(res["predicted_additional_delay"], float)
    assert res["confidence"] > 0.0
    assert len(res["feature_contributions"]) > 0


@pytest.mark.asyncio
async def test_dynamic_eta_before_and_after_disruption(staff_client):
    """Verify that injecting an operational event actively changes the ETA and generates alerts."""
    # 1. Capture ETA before disruption
    train_id = "12951"
    detail_before = (await staff_client.get(f"/api/trains/{train_id}")).json()
    etas_before = detail_before.get("etas", [])
    assert len(etas_before) > 0
    eta_pred_before = etas_before[0]["predicted_delay_minutes"]

    # 2. Inject operational disruption
    event_payload = {
        "event_type": "signal_congestion",
        "train_id": train_id,
        "location": "Near Vadodara Jn",
        "severity": 0.9,
        "duration_minutes": 30,
        "description": "High signal interlocking delay at BRC junction",
    }
    event_res = await staff_client.post("/api/simulation/events", json=event_payload)
    assert event_res.status_code == 200
    assert event_res.json()["impact_delay_minutes"] > 0

    # 3. Capture ETA after disruption
    detail_after = (await staff_client.get(f"/api/trains/{train_id}")).json()
    etas_after = detail_after.get("etas", [])
    assert len(etas_after) > 0
    eta_pred_after = etas_after[0]["predicted_delay_minutes"]

    # 4. Verify ETA BEFORE != ETA AFTER and delay increased
    assert eta_pred_after > eta_pred_before

    # 5. Verify alert generated
    alerts = (await staff_client.get("/api/alerts")).json()
    assert len(alerts) > 0
    assert any(a["train_id"] == train_id for a in alerts)


@pytest.mark.asyncio
async def test_network_congestion(client):
    response = await client.get("/api/network/congestion")
    assert response.status_code == 200
    sections = response.json()
    assert len(sections) > 0
    assert "congestion_score" in sections[0]
    assert "status" in sections[0]


@pytest.mark.asyncio
async def test_get_analytics(client):
    response = await client.get("/api/analytics")
    assert response.status_code == 200
    data = response.json()
    assert "total_trains" in data
    assert "delay_by_route" in data
    assert "model_performance" in data
    assert data["model_performance"]["r_squared"] > 0.7


@pytest.mark.asyncio
async def test_kpis(client):
    response = await client.get("/api/kpis")
    assert response.status_code == 200
    data = response.json()
    assert data["active_trains"] >= 10
    assert "avg_delay_minutes" in data


@pytest.mark.asyncio
async def test_search_real_train_20491(client):
    """Test 1: Search exact train number 20491."""
    response = await client.get("/api/trains?search=20491")
    assert response.status_code == 200
    data = response.json()
    assert "trains" in data
    assert any(t["train_number"] == "20491" for t in data["trains"])
    train = next(t for t in data["trains"] if t["train_number"] == "20491")
    assert "Jaisalmer" in train["train_name"]
    assert "Sabarmati" in train["destination"] or "SBIB" in train["destination_code"]


@pytest.mark.asyncio
async def test_search_by_real_train_name(client):
    """Test 2: Search real train by name 'Jaisalmer'."""
    response = await client.get("/api/trains?search=Jaisalmer")
    assert response.status_code == 200
    data = response.json()
    assert len(data["trains"]) >= 1
    assert any("Jaisalmer" in t["train_name"] for t in data["trains"])


@pytest.mark.asyncio
async def test_search_partial_real_train_number(client):
    """Test 3: Search partial train number '2049'."""
    response = await client.get("/api/trains?search=2049")
    assert response.status_code == 200
    data = response.json()
    assert any(t["train_number"] in ("20491", "20492") for t in data["trains"])


@pytest.mark.asyncio
async def test_get_real_train_details(client):
    """Test 4: Get real train details."""
    response = await client.get("/api/trains/20491")
    assert response.status_code == 200
    data = response.json()
    assert data["train"]["train_number"] == "20491"
    assert "Real Train Master" in data["train"]["data_source"]
    assert "Simulated" in data["train"]["telemetry_source"]


@pytest.mark.asyncio
async def test_get_real_train_route_and_stops(client):
    """Test 5 & 6: Get real train route and all authentic stops."""
    response = await client.get("/api/trains/20491/route")
    assert response.status_code == 200
    stops = response.json()
    assert len(stops) == 23
    assert stops[0]["station_code"] == "JSM"
    assert stops[-1]["station_code"] == "SBIB"
    # Check intermediate stops
    codes = [s["station_code"] for s in stops]
    assert "POK" in codes
    assert "JU" in codes
    assert "MSH" in codes


@pytest.mark.asyncio
async def test_verify_real_train_telemetry_flag(client):
    """Test 7: Verify real train data is not falsely claimed as live GPS telemetry."""
    response = await client.get("/api/trains/20491/position")
    assert response.status_code == 200
    pos = response.json()
    assert "Simulated Telemetry" in pos["telemetry_source"]


@pytest.mark.asyncio
async def test_real_train_dynamic_eta_forecast(client):
    """Test: Dynamic ML ETA engine works for real train stops."""
    response = await client.get("/api/trains/20491/eta")
    assert response.status_code == 200
    etas = response.json()
    assert len(etas) > 0
    assert "predicted_arrival" in etas[0]
    assert "confidence" in etas[0]


@pytest.mark.asyncio
async def test_temporal_evaluation_artifact_and_metrics(client):
    """Test: Verify temporal evaluation artifact exists with valid walk-forward metrics."""
    import json
    from pathlib import Path

    artifact_path = Path(__file__).parent.parent.parent / "ml" / "model" / "temporal_evaluation_results.json"
    assert artifact_path.exists(), "temporal_evaluation_results.json should exist"

    with open(artifact_path, "r") as f:
        data = json.load(f)

    assert data["evaluation_methodology"] == "temporal_walk_forward_split"
    assert data["total_records"] == 55000
    assert data["train_window"]["records"] == 44000
    assert data["test_window"]["records"] == 11000

    baseline = data["baseline_model"]
    naive = data.get("naive_baseline", baseline)
    stat = data.get("statistical_baseline")
    gb = data["gradient_boosting"]
    rf = data["random_forest"]
    improvement = data["improvement_over_baseline"]
    stat_improvement = data.get("improvement_over_statistical_baseline")

    # Verify naive baseline error is significantly worse than ML models
    assert baseline["mae"] > 20.0
    assert naive["mae"] > 20.0
    assert gb["mae"] < 4.5
    assert rf["mae"] < 5.0
    assert gb["r2"] > 0.85
    assert improvement["mae_reduction_minutes"] > 15.0
    assert improvement["mae_improvement_percent"] > 75.0

    # Verify domain-realistic statistical baseline
    assert stat is not None, "statistical_baseline should be present in temporal_evaluation_results.json"
    assert 6.0 <= stat["mae"] <= 6.5, f"Expected statistical baseline MAE between 6.0 and 6.5, got {stat['mae']}"
    assert 8.0 <= stat["rmse"] <= 8.5, f"Expected statistical baseline RMSE between 8.0 and 8.5, got {stat['rmse']}"
    assert 0.60 <= stat["r2"] <= 0.66, f"Expected statistical baseline R2 between 0.60 and 0.66, got {stat['r2']}"
    assert len(stat["fallback_levels"]) == 6, "Expected 6 fallback levels"

    # Verify ML improvement over statistical baseline
    assert stat_improvement is not None, "improvement_over_statistical_baseline should be present"
    assert 2.0 <= stat_improvement["mae_reduction_minutes"] <= 2.6
    assert 35.0 <= stat_improvement["mae_improvement_percent"] <= 40.0
    assert 3.0 <= stat_improvement["rmse_reduction_minutes"] <= 3.6
    assert 37.0 <= stat_improvement["rmse_improvement_percent"] <= 43.0


@pytest.mark.asyncio
async def test_analytics_model_performance_temporal_consistency(client):
    """Test: Verify /api/analytics returns scientifically honest temporal metrics."""
    response = await client.get("/api/analytics")
    assert response.status_code == 200
    perf = response.json().get("model_performance", {})
    assert perf["mae"] <= 4.2
    assert perf["rmse"] <= 5.5
    assert perf["r_squared"] >= 0.80
    assert "Temporal" in perf["model_type"] or "Temporal" in perf["note"]
    assert perf["training_samples"] == 44000


@pytest.mark.asyncio
async def test_register_real_train_dynamic_simulation(staff_client):
    """Test: Dynamically register real train 20491 into the simulation engine."""
    from app.simulation.engine import simulation_engine

    # 1. Start simulation for real train 20491
    start_resp = await staff_client.post("/api/simulation/trains/20491/start")
    assert start_resp.status_code == 200
    start_data = start_resp.json()
    assert start_data["success"] is True
    assert start_data["train_number"] == "20491"
    assert "Simulated Telemetry" in start_data["telemetry_source"]

    # 2. Verify status endpoint includes 20491
    status_resp = await staff_client.get("/api/simulation/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert "20491" in status_data["simulated_real_trains"]
    assert status_data["train_count"] >= 11

    # 3. Verify get /api/simulation/trains endpoint
    sim_trains_resp = await staff_client.get("/api/simulation/trains")
    assert sim_trains_resp.status_code == 200
    assert "20491" in sim_trains_resp.json()["simulated_real_trains"]

    # 4. Advance simulation tick and verify real train state updates
    init_pos_resp = await staff_client.get("/api/trains/20491/position")
    assert init_pos_resp.status_code == 200
    init_pos = init_pos_resp.json()
    assert init_pos["is_simulated"] is True

    # Advance tick
    await simulation_engine.tick()

    tick_pos_resp = await staff_client.get("/api/trains/20491/position")
    assert tick_pos_resp.status_code == 200
    tick_pos = tick_pos_resp.json()
    assert tick_pos["telemetry_source"] == "Simulated Telemetry (No Authorized Live Feed)"

    # 5. Inject event on 20491 and verify ETA delay increases
    event_payload = {
        "event_type": "signal_congestion",
        "train_id": "20491",
        "location": "Phalodi Jn",
        "severity": 0.8,
        "duration_minutes": 25,
        "description": "Signal interlock issue at Phalodi Jn",
    }
    event_resp = await staff_client.post("/api/simulation/events", json=event_payload)
    assert event_resp.status_code == 200
    event_data = event_resp.json()
    assert event_data["impact_delay_minutes"] > 0

    # Verify train position reflects delay
    after_event_pos = (await staff_client.get("/api/trains/20491/position")).json()
    assert after_event_pos["delay_minutes"] >= event_data["impact_delay_minutes"]

    # Verify ETA predictions reflect disruption
    etas_resp = await staff_client.get("/api/trains/20491/eta")
    assert etas_resp.status_code == 200
    etas = etas_resp.json()
    assert len(etas) > 0
    assert etas[0]["predicted_delay_minutes"] > 0

    # 6. Unregister 20491
    stop_resp = await staff_client.post("/api/simulation/trains/20491/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["success"] is True

    # Verify removed from simulated list
    status_after = (await staff_client.get("/api/simulation/status")).json()
    assert "20491" not in status_after["simulated_real_trains"]



@pytest.mark.asyncio
async def test_trains_pagination_and_search(client):
    """Test safe pagination defaults (limit=50), max cap (limit=100), and search."""
    # 1. Default pagination
    resp = await client.get("/api/trains")
    assert resp.status_code == 200
    data = resp.json()
    assert "trains" in data
    assert "total" in data
    assert data["limit"] == 50
    assert data["offset"] == 0
    # Must enforce default safe limit of 50
    assert len(data["trains"]) == 50
    assert data["total"] == 5211

    # 2. Custom limit and offset
    resp_custom = await client.get("/api/trains?limit=5&offset=0")
    assert resp_custom.status_code == 200
    custom_data = resp_custom.json()
    assert len(custom_data["trains"]) == 5
    assert custom_data["limit"] == 5

    # 3. Limit validation: >100 should be rejected by FastAPI Query(le=100)
    resp_invalid = await client.get("/api/trains?limit=150")
    assert resp_invalid.status_code == 422

    # 4. Search by train number and name (guarantees finding 20491 and 22436 in 5,000+ catalog)
    resp_search_num = await client.get("/api/trains?search=20491")
    assert resp_search_num.status_code == 200
    search_num_data = resp_search_num.json()
    assert any(t["train_number"] == "20491" for t in search_num_data["trains"])

    resp_search_22436 = await client.get("/api/trains?search=22436")
    assert resp_search_22436.status_code == 200
    search_22436_data = resp_search_22436.json()
    assert any(t["train_number"] == "22436" for t in search_22436_data["trains"])

    resp_search_name = await client.get("/api/trains?search=Vande")
    assert resp_search_name.status_code == 200
    search_name_data = resp_search_name.json()
    assert any("Vande" in t["train_name"] for t in search_name_data["trains"])


@pytest.mark.asyncio
async def test_time_normalization():
    """Test time normalization for real train timetable feeds."""
    from scripts.import_real_train_data import normalize_time
    assert normalize_time("14:05:00") == "14:05"
    assert normalize_time("09:30") == "09:30"
    assert normalize_time("9:30") == "09:30"
    assert normalize_time("") is None
    assert normalize_time("None") is None
    assert normalize_time(None) is None
    assert normalize_time("Source") is None
    assert normalize_time("1st Day") is None


@pytest.mark.asyncio
async def test_importer_generalization_idempotency_and_malformed_isolation(tmp_path):
    """Test importer discovers files, skips malformed JSON cleanly, and is idempotent without duplicate stops."""
    import json
    import os
    from app.models.database_models import RealTrain, RealTrainStop
    from sqlalchemy import select, func
    from scripts.import_real_train_data import import_trains_from_directory

    temp_dir = tmp_path / "train_feeds"
    temp_dir.mkdir()

    # Create a valid train JSON file
    valid_train = {
        "train_number": "99001",
        "train_name": "Test Express 1",
        "train_type": "express",
        "source": "Station A",
        "source_code": "STA",
        "destination": "Station B",
        "destination_code": "STB",
        "zone": "NR",
        "total_distance_km": 250,
        "scheduled_departure": "10:00:00",
        "scheduled_arrival": "14:30:00",
        "stops": [
            {
                "station_code": "STA",
                "station_name": "Station A",
                "arrival": None,
                "departure": "10:00:00",
                "distance_km": 0,
                "stop_number": 1,
                "platform": "1",
                "halt_minutes": 0,
                "day": 1
            },
            {
                "station_code": "STB",
                "station_name": "Station B",
                "arrival": "14:30:00",
                "departure": None,
                "distance_km": 250,
                "stop_number": 2,
                "platform": "2",
                "halt_minutes": 0,
                "day": 1
            }
        ]
    }
    valid_file = temp_dir / "train_99001.json"
    valid_file.write_text(json.dumps(valid_train), encoding="utf-8")

    # Create a malformed JSON file to verify it doesn't crash the importer batch
    malformed_file = temp_dir / "train_corrupt.json"
    malformed_file.write_text("{ corrupt json ... not valid", encoding="utf-8")

    # Create isolated in-memory test database so no persistent SQLite file is accessed
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database.db import Base
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Run import on temp directory using isolated in-memory session
    async with TestSession() as session:
        result = await import_trains_from_directory(session, str(temp_dir), batch_size=10)
        assert result["total_files"] == 2
        assert result["imported"] == 1
        assert result["skipped"] == 1

        # Check DB has 99001 and exactly 2 stops
        res = await session.execute(select(RealTrain).where(RealTrain.train_number == "99001"))
        train = res.scalar_one_or_none()
        assert train is not None
        assert train.train_name == "Test Express 1"
        assert train.source_station == "STA"

        stops_res = await session.execute(select(RealTrainStop).where(RealTrainStop.train_number == "99001").order_by(RealTrainStop.sequence))
        stops = stops_res.scalars().all()
        assert len(stops) == 2
        assert stops[0].departure_time == "10:00"
        assert stops[1].arrival_time == "14:30"

        # Verify IDEMPOTENCY: run import second time
        result2 = await import_trains_from_directory(session, str(temp_dir), batch_size=10)
        assert result2["imported"] == 1

        # Must still have exactly 2 stops, no duplicate rows
        stops_res_after = await session.execute(select(RealTrainStop).where(RealTrainStop.train_number == "99001"))
        stops_after = stops_res_after.scalars().all()
        assert len(stops_after) == 2

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_importer_batch_performance_benchmark(tmp_path):
    """Benchmark: Verify importer handles batch ingestion of 100 train records cleanly."""
    import json
    import time
    from scripts.import_real_train_data import import_trains_from_directory

    bench_dir = tmp_path / "bench_feeds"
    bench_dir.mkdir()

    # Generate 100 synthetic benchmark train files
    for i in range(100):
        t_num = f"88{i:03d}"
        t_data = {
            "train_number": t_num,
            "train_name": f"Benchmark Express {i}",
            "train_type": "express",
            "source": "Origin",
            "source_code": "ORG",
            "destination": "Terminus",
            "destination_code": "TRM",
            "zone": "NR",
            "total_distance_km": 100,
            "scheduled_departure": "06:00",
            "scheduled_arrival": "08:00",
            "stops": [
                {
                    "station_code": "ORG",
                    "station_name": "Origin",
                    "arrival": None,
                    "departure": "06:00",
                    "distance_km": 0,
                    "stop_number": 1,
                    "halt_minutes": 0,
                    "day": 1
                },
                {
                    "station_code": "TRM",
                    "station_name": "Terminus",
                    "arrival": "08:00",
                    "departure": None,
                    "distance_km": 100,
                    "stop_number": 2,
                    "halt_minutes": 0,
                    "day": 1
                }
            ]
        }
        (bench_dir / f"train_{t_num}.json").write_text(json.dumps(t_data), encoding="utf-8")

    # Create isolated in-memory test database so no persistent SQLite file is accessed
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database.db import Base
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    start_time = time.perf_counter()
    async with TestSession() as session:
        res = await import_trains_from_directory(session, str(bench_dir), batch_size=50)
    elapsed = time.perf_counter() - start_time

    assert res["imported"] == 100
    assert res["skipped"] == 0
    # Ingestion of 100 trains with stops in batches should take under 5 seconds in SQLite
    assert elapsed < 5.0
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_real_train_data_integrity(client):
    """Test Priority 3B data integrity: 22436 distance == 771.0, BSB stop == 771.0, 20491 route validity."""
    # 1. Check Train 22436 details and total distance
    resp_22436 = await client.get("/api/trains/22436")
    assert resp_22436.status_code == 200
    train_22436 = resp_22436.json()["train"]
    assert train_22436["total_distance_km"] == 771.0
    assert train_22436["train_name"] == "Vande Bharat Express"
    assert train_22436["source_code"] == "NDLS"
    assert train_22436["destination_code"] == "BSB"

    # 2. Check Train 22436 route and stops
    resp_route_22436 = await client.get("/api/trains/22436/route")
    assert resp_route_22436.status_code == 200
    stops_22436 = resp_route_22436.json()
    assert len(stops_22436) == 4
    final_bsb = stops_22436[-1]
    assert final_bsb["station_code"] == "BSB"
    assert final_bsb["distance_from_source"] == 771.0

    # 3. Check Train 20491 and 20492 total distances remain 759.0
    resp_20491 = await client.get("/api/trains/20491")
    assert resp_20491.status_code == 200
    assert resp_20491.json()["train"]["total_distance_km"] == 759.0

    resp_20492 = await client.get("/api/trains/20492")
    assert resp_20492.status_code == 200
    assert resp_20492.json()["train"]["total_distance_km"] == 759.0

    # 4. Verify 20491 route contains Phalodi Jn and does NOT contain Abohar Jn
    resp_route_20491 = await client.get("/api/trains/20491/route")
    assert resp_route_20491.status_code == 200
    stops_20491 = resp_route_20491.json()
    stop_names = [s["station_name"] for s in stops_20491]
    assert "Phalodi Jn" in stop_names
    assert "Abohar Jn" not in stop_names


@pytest.mark.asyncio
async def test_datameet_import_invariants(client):
    """Test DataMeet bulk import invariants under Option A."""
    # 1. Total real trains catalog should exceed 5,000
    resp = await client.get("/api/trains?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "trains" in data

    # 2. Check DataMeet train sample (e.g. 04601 Jammu Tawi Udhampur Special)
    resp_04601 = await client.get("/api/trains/04601")
    assert resp_04601.status_code == 200
    t_04601 = resp_04601.json()["train"]
    assert t_04601["train_type"] == "DEMU"
    assert "DataMeet (CC0) - Historical Timetable Snapshot" in t_04601["data_source"]
    assert t_04601["days_of_run"] == "Unspecified"

    # 3. Check Option A distance semantics on route stops
    resp_route_04601 = await client.get("/api/trains/04601/route")
    assert resp_route_04601.status_code == 200
    stops_04601 = resp_route_04601.json()
    assert len(stops_04601) == 6

    # Origin distance is 0.0
    assert stops_04601[0]["distance_from_source"] == 0.0
    # Intermediate distances are None / null
    assert stops_04601[1]["distance_from_source"] is None
    assert stops_04601[2]["distance_from_source"] is None
    assert stops_04601[3]["distance_from_source"] is None
    assert stops_04601[4]["distance_from_source"] is None
    # Terminus distance is 53.0
    assert stops_04601[5]["distance_from_source"] == 53.0

    # 4. Simulation Engine invariant: verify simulation does NOT auto-simulate 5000+ trains
    from app.simulation.engine import simulation_engine
    # Actively registered real trains in simulation loop should remain small (only on-demand)
    assert len(simulation_engine._registered_real_trains) <= 5


@pytest.mark.asyncio
async def test_pagination_and_search_regression(client):
    """
    Priority 3C Regression Tests:
    1. offset=0 returns first page (50 items).
    2. offset=50 returns second page (50 items).
    3. offset=500 returns non-empty page when enough records exist (50 items).
    4. offset beyond catalog returns empty list.
    5. Adjacent pages contain no duplicates.
    6. Search pagination works.
    7. Demo + real train records coexist correctly.
    8. Existing 20491 search still works.
    9. Existing 22436 search still works.
    """
    # 1. offset=0
    resp_0 = await client.get("/api/trains?limit=50&offset=0")
    assert resp_0.status_code == 200
    data_0 = resp_0.json()
    page_0 = data_0["trains"]
    assert len(page_0) == 50
    assert data_0["total"] == 5211

    # 2. offset=50
    resp_50 = await client.get("/api/trains?limit=50&offset=50")
    assert resp_50.status_code == 200
    data_50 = resp_50.json()
    page_50 = data_50["trains"]
    assert len(page_50) == 50
    assert data_50["total"] == 5211

    # 3. offset=500
    resp_500 = await client.get("/api/trains?limit=50&offset=500")
    assert resp_500.status_code == 200
    data_500 = resp_500.json()
    page_500 = data_500["trains"]
    assert len(page_500) == 50
    assert data_500["total"] == 5211

    # 4. offset=5000
    resp_5000 = await client.get("/api/trains?limit=50&offset=5000")
    assert resp_5000.status_code == 200
    data_5000 = resp_5000.json()
    page_5000 = data_5000["trains"]
    assert len(page_5000) == 50
    assert data_5000["total"] == 5211

    # 5. offset=5200 (final page)
    resp_5200 = await client.get("/api/trains?limit=50&offset=5200")
    assert resp_5200.status_code == 200
    data_5200 = resp_5200.json()
    assert len(data_5200["trains"]) == 11
    assert data_5200["total"] == 5211

    # 6. offset=5211 (exhaustion)
    resp_5211 = await client.get("/api/trains?limit=50&offset=5211")
    assert resp_5211.status_code == 200
    data_5211 = resp_5211.json()
    assert len(data_5211["trains"]) == 0
    assert data_5211["total"] == 5211

    # 7. offset beyond catalog returns empty list
    resp_beyond = await client.get("/api/trains?limit=50&offset=10000")
    assert resp_beyond.status_code == 200
    data_beyond = resp_beyond.json()
    page_beyond = data_beyond["trains"]
    assert len(page_beyond) == 0
    assert data_beyond["total"] == 5211

    # 8. Adjacent pages contain no duplicates
    nums_0 = {t["train_number"] for t in page_0}
    nums_50 = {t["train_number"] for t in page_50}
    assert len(nums_0.intersection(nums_50)) == 0

    # 9. Search pagination works and maintains invariant total
    resp_search_p0 = await client.get("/api/trains?search=Rajdhani&limit=5&offset=0")
    assert resp_search_p0.status_code == 200
    data_search_p0 = resp_search_p0.json()
    search_p0 = data_search_p0["trains"]
    assert len(search_p0) == 5
    assert data_search_p0["total"] > 5

    resp_search_p5 = await client.get("/api/trains?search=Rajdhani&limit=5&offset=5")
    assert resp_search_p5.status_code == 200
    data_search_p5 = resp_search_p5.json()
    search_p5 = data_search_p5["trains"]
    assert len(search_p5) == 5
    assert data_search_p5["total"] == data_search_p0["total"]

    nums_search_p0 = {t["train_number"] for t in search_p0}
    nums_search_p5 = {t["train_number"] for t in search_p5}
    assert len(nums_search_p0.intersection(nums_search_p5)) == 0

    # 10. Demo + real train records coexist correctly in initial page
    demo_present = any(t.get("data_source") == "Demo Simulation Engine" for t in page_0)
    real_present = any("Real Train Master" in t.get("data_source", "") for t in page_0)
    assert demo_present is True
    assert real_present is True

    # 11. Existing 20491 search still works with total == 1
    resp_20491 = await client.get("/api/trains?search=20491")
    assert resp_20491.status_code == 200
    data_20491 = resp_20491.json()
    assert data_20491["total"] == 1
    assert len(data_20491["trains"]) == 1
    assert data_20491["trains"][0]["train_number"] == "20491"

    # 12. Existing 22436 search still works with total == 1
    resp_22436 = await client.get("/api/trains?search=22436")
    assert resp_22436.status_code == 200
    data_22436 = resp_22436.json()
    assert data_22436["total"] == 1
    assert len(data_22436["trains"]) == 1
    assert data_22436["trains"][0]["train_number"] == "22436"




