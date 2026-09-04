"""scripts/generate_secret.py -- the production JWT_SECRET generator.
Companion to test_config_production_safety.py: that file proves
Settings.validate() rejects a short/default secret, this proves the
script that's supposed to produce a real one actually clears that bar.
"""
from __future__ import annotations

import pytest

from backend.app.config import Settings
from scripts.generate_secret import generate_secret


def test_default_secret_is_well_above_the_16_char_floor():
    secret = generate_secret(48)
    assert len(secret) >= 16


def test_secret_passes_settings_validate():
    settings = Settings(
        environment="production",
        jwt_secret=generate_secret(48),
        database_url="postgresql+psycopg2://user:pass@localhost:5432/smart_bandage",
    )
    settings.validate()  # must not raise


def test_two_calls_are_not_the_same_secret():
    # Sanity check that this is actually random and not a fixed string --
    # a real security bug (e.g. a hardcoded seed) would make this flaky/fail.
    assert generate_secret(48) != generate_secret(48)


def test_rejects_too_short_a_length():
    with pytest.raises(ValueError):
        generate_secret(8)
