"""
Production-safety guard (backend/app/config.py:Settings.validate(),
wired into backend/app/main.py:lifespan). Pure dataclass tests -- no
TestClient/DB needed, unlike backend/tests/test_api.py.

ENVIRONMENT defaults to "development" everywhere else in the suite
(backend/tests/test_api.py, test_ai_analysis.py never set it), so
Settings.validate() is a no-op for every other test in this repo; these
are the only tests that ever construct an environment="production" Settings.
"""
from __future__ import annotations

import pytest

from backend.app.config import ConfigurationError, Settings


def test_development_never_validates_even_with_dev_defaults():
    settings = Settings(environment="development")
    settings.validate()  # must not raise


def test_production_rejects_default_jwt_secret():
    settings = Settings(environment="production", jwt_secret="dev-only-secret-change-me")
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        settings.validate()


def test_production_rejects_short_jwt_secret():
    settings = Settings(environment="production", jwt_secret="short")
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        settings.validate()


def test_production_rejects_sqlite():
    settings = Settings(
        environment="production",
        jwt_secret="a" * 32,
        database_url="sqlite:///./smart_bandage.db",
    )
    with pytest.raises(ConfigurationError, match="Postgres"):
        settings.validate()


def test_production_accepts_a_real_secret_and_postgres():
    settings = Settings(
        environment="production",
        jwt_secret="a" * 32,
        database_url="postgresql+psycopg2://user:pass@localhost:5432/smart_bandage",
    )
    settings.validate()  # must not raise


def test_environment_is_case_insensitive():
    assert Settings(environment="Production").is_production
    assert Settings(environment="PRODUCTION").is_production
    assert not Settings(environment="staging").is_production
