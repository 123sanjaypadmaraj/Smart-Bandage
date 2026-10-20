"""
Calibration data contract (Blueprint §13).

Calibration parameters are *data*, never hard-coded constants in the
processing code — this is what gets replaced wholesale in Phase 9 once
real experimental calibration curves exist.
"""
from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

CalibrationModelType = Literal["linear", "polynomial"]


class CalibrationParameters(BaseModel):
    """
    y = slope * x + intercept                      (model_type="linear")
    y = coefficients[0]*x^n + ... + coefficients[n] (model_type="polynomial")
    """

    sensor_type: str = Field(..., examples=["pathogen_channel_1"])
    # Free-form, not semver -- "0.0-identity" (the pass-through calibration
    # used by tests/ml/evaluation) is a valid value alongside "1.0", "2.1".
    version: str = Field(..., min_length=1, max_length=32, examples=["1.0", "0.0-identity"])
    model_type: CalibrationModelType = "linear"
    slope: Optional[float] = Field(None, description="y = slope*x + intercept; units are (biomarker unit)/(raw_signal unit)")
    intercept: Optional[float] = Field(None, description="Same unit as the resulting estimated_value")
    coefficients: Optional[List[float]] = Field(
        None, min_length=1, max_length=8,
        description="Highest degree first; used when model_type='polynomial'. Capped at degree 7 "
        "(8 coefficients) -- real calibration curves this deep would indicate overfitting, not signal",
    )
    valid_from: date
    valid_to: Optional[date] = None

    @model_validator(mode="after")
    def _check_params_match_model(self) -> "CalibrationParameters":
        if self.model_type == "linear" and (self.slope is None or self.intercept is None):
            raise ValueError("linear calibration requires slope and intercept")
        if self.model_type == "polynomial" and not self.coefficients:
            raise ValueError("polynomial calibration requires coefficients")
        return self

    def apply(self, x: float) -> float:
        """Convert a processed signal value into an estimated concentration."""
        if self.model_type == "linear":
            return self.slope * x + self.intercept  # type: ignore[operator]
        # polynomial, highest degree first
        result = 0.0
        degree = len(self.coefficients) - 1  # type: ignore[arg-type]
        for i, c in enumerate(self.coefficients):  # type: ignore[union-attr]
            result += c * (x ** (degree - i))
        return result
