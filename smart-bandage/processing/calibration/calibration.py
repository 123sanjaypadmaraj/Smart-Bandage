"""
Phase 3 calibration lookup (Blueprint §13).

Calibration *parameters* are the schema in common/schemas/calibration.py
and never change here. This is just the registry that finds which
CalibrationParameters is active for a sensor_type on a given date — an
in-memory stand-in for the Phase 4 Postgres table it becomes.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from common.schemas.calibration import CalibrationParameters


class CalibrationStore:
    """One or more calibration curves per sensor_type, selected by date."""

    def __init__(self) -> None:
        self._by_sensor_type: Dict[str, List[CalibrationParameters]] = {}

    def register(self, params: CalibrationParameters) -> None:
        self._by_sensor_type.setdefault(params.sensor_type, []).append(params)

    def active(self, sensor_type: str, at: Optional[date] = None) -> CalibrationParameters:
        """Most recent calibration whose validity window covers `at` (default: today)."""
        at = at or date.today()
        candidates = [
            p
            for p in self._by_sensor_type.get(sensor_type, [])
            if p.valid_from <= at and (p.valid_to is None or at <= p.valid_to)
        ]
        if not candidates:
            raise KeyError(f"no active calibration for sensor_type={sensor_type!r} at {at}")
        return max(candidates, key=lambda p: p.valid_from)

    def all_for(self, sensor_type: str) -> List[CalibrationParameters]:
        return list(self._by_sensor_type.get(sensor_type, []))
