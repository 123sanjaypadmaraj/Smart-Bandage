"""
Phase 4 settings (Blueprint §10 roadmap: "FastAPI + PostgreSQL").

Plain env-var driven config, not pydantic-settings, to keep the dependency
list small. Defaults to a local SQLite file so `pytest` / `uvicorn` work
with zero setup; point DATABASE_URL at Postgres for anything real:

    DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/smart_bandage
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Loads a local .env (gitignored -- see .env.example) into the process
# environment before any field default below reads it. Never overrides a
# variable already set in the real environment (Docker, CI, a shell
# export), and does nothing at all if no .env file is found -- so this is
# purely a local-dev convenience, not a new required dependency (uvicorn
# [standard] already pulls python-dotenv in transitively; requirements.txt
# lists it explicitly so that's not load-bearing).
load_dotenv()


class ConfigurationError(RuntimeError):
    """Raised at startup (backend/app/main.py:lifespan) when ENVIRONMENT=production
    is combined with a dev-only default -- JWT_SECRET, SQLite -- that would
    otherwise silently ship an unlocked/unscalable backend. See the "Ship what
    already works" lane in docs/architecture/overview.md."""


@dataclass(frozen=True)
class Settings:
    # "development" (default) or "production" -- gates Settings.validate()
    # below and whether main.py seeds the dev-only admin/admin login.
    environment: str = os.environ.get("ENVIRONMENT", "development")
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./smart_bandage.db")
    jwt_secret: str = os.environ.get("JWT_SECRET", "dev-only-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = int(os.environ.get("JWT_EXPIRES_MINUTES", "180"))
    # Refresh tokens are what let a device that's already signed in stay
    # signed in: the frontend swaps one for a fresh access token whenever
    # the short-lived one expires, without ever showing the login screen
    # again -- see POST /auth/refresh and frontend/src/api.ts.
    jwt_refresh_expires_days: int = int(os.environ.get("JWT_REFRESH_EXPIRES_DAYS", "30"))
    simulation_interval_seconds: float = float(os.environ.get("SIMULATION_INTERVAL_SECONDS", "1.0"))
    cors_origins: tuple[str, ...] = tuple(
        o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
    )
    # Phase 10 AI analysis (backend/app/gemini_client.py, backend/app/routers/ai.py).
    # Empty by default -- GET/POST /devices/{id}/ai/* return 503 until this is
    # set, everything else in the app works with zero setup regardless.
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def validate(self) -> None:
        """Fail fast at startup instead of silently running a production
        backend with defaults that were only ever meant for `pytest`/local
        `uvicorn --reload`. A no-op in development (the default) so this
        never affects local dev or the test suite."""
        if not self.is_production:
            return
        problems = []
        if self.jwt_secret == "dev-only-secret-change-me" or len(self.jwt_secret) < 16:
            problems.append("JWT_SECRET is unset/default or shorter than 16 chars -- set a long random secret")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL is SQLite -- point at Postgres (see docker-compose.yml's `db` service)")
        if problems:
            raise ConfigurationError(
                "ENVIRONMENT=production refuses to start with dev-only defaults: " + "; ".join(problems)
            )


settings = Settings()
