"""Application configuration."""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = "RailPulse ETA"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "AI-Powered Dynamic Train Arrival Forecasting System"

    # MongoDB Atlas (AsyncMongoClient)
    MONGODB_URL: str | None = os.getenv("MONGODB_URL")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "railpulse")

    # Simulation
    SIMULATION_INTERVAL: int = int(os.getenv("SIMULATION_INTERVAL", "3"))

    # ML Model
    ML_MODEL_PATH: str = os.getenv(
        "ML_MODEL_PATH",
        os.path.join(os.path.dirname(__file__), "..", "..", "ml", "model", "eta_model.joblib")
    )

    # Demo mode
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() == "true"

    # Server
    HOST: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("BACKEND_PORT", "8000"))

    # JWT Authentication
    JWT_SECRET_KEY: str = os.getenv(
        "JWT_SECRET_KEY",
        # Auto-generate a dev-only secret if not set; MUST be set in production
        __import__("secrets").token_urlsafe(64)
    )
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    # Sarvam AI
    SARVAM_API_KEY: str | None = os.getenv("SARVAM_API_KEY")


settings = Settings()
