"""RailPulse ETA - Main FastAPI Application."""

import asyncio
import sys
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Add project root to path for ML imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.config import settings
from app.database.mongodb import init_mongo, close_mongo
from app.simulation.engine import simulation_engine
from app.services.eta_service import eta_service
from app.api import trains, simulation, network, alerts, analytics, websocket, auth, sarvam


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    print(f"\n{'='*60}")
    print(f"  RailPulse ETA - {settings.VERSION}")
    print(f"  AI-Powered Dynamic Train Arrival Forecasting System")
    print(f"  DEMO MODE: {'ON' if settings.DEMO_MODE else 'OFF'}")
    print(f"{'='*60}\n")

    # Initialize MongoDB (AsyncMongoClient)
    mongo_ok = await init_mongo()
    if not mongo_ok:
        raise RuntimeError("MongoDB connection failed. RailPulse requires an active MongoDB connection.")
    print("[Startup] MongoDB connected successfully.")

    # Set ML predictor on simulation engine
    if eta_service.predictor:
        simulation_engine.set_predictor(eta_service.predictor)
        print("[Startup] ML predictor connected to simulation engine.")

    # Start simulation
    await simulation_engine.start()
    print("[Startup] Simulation engine started.")

    print(f"\n[Ready] API docs: http://localhost:8000/docs")
    print(f"[Ready] WebSocket: ws://localhost:8000/ws/live\n")

    yield

    # Shutdown
    await simulation_engine.stop()
    print("[Shutdown] Simulation engine stopped.")
    await close_mongo()
    print("[Shutdown] MongoDB connection closed.")


app = FastAPI(
    title=settings.APP_NAME,
    description="AI-Powered Dynamic Train Arrival Forecasting System for Indian Railways. "
                "This is a Smart India Hackathon 2026 prototype using simulated data.",
    version=settings.VERSION,
    lifespan=lifespan,
)

# CORS - explicit origins for cookie-based auth (credentials require non-wildcard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "https://railpulse-eta-sarvam-web.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(trains.router, prefix="/api/trains", tags=["Trains"])
app.include_router(simulation.router, prefix="/api", tags=["Simulation"])
app.include_router(network.router, prefix="/api/network", tags=["Network"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(sarvam.router, prefix="/api/sarvam", tags=["Sarvam AI"])
app.include_router(websocket.router, tags=["WebSocket"])


@app.get("/", tags=["System"])
async def root():
    """Root endpoint with application information."""
    return {
        "app_name": settings.APP_NAME,
        "version": settings.VERSION,
        "description": "AI-Powered Dynamic Train Arrival Forecasting System",
        "demo_mode": settings.DEMO_MODE,
        "docs_url": "/docs",
        "api_prefix": "/api",
        "websocket": "/ws/live",
        "status": "running",
        "note": "For SIH prototype, simulated data is used because Indian Railways operational APIs are not publicly available.",
    }


@app.get("/api/health", tags=["System"])
async def health_check():
    """System health check endpoint."""
    from datetime import datetime, timezone

    return {
        "status": "healthy",
        "version": settings.VERSION,
        "demo_mode": settings.DEMO_MODE,
        "simulation_running": simulation_engine.is_running,
        "ml_model_loaded": eta_service.is_ml_available(),
        "database_ok": True,
        "active_trains": 10,  # Will be updated when train count is dynamic
        "last_update": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/kpis", tags=["Dashboard"])
async def get_kpis():
    """Get dashboard KPI metrics."""
    from app.services.train_service import train_service
    return await train_service.get_kpis()
