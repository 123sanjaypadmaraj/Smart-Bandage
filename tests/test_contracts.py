"""
Phase 1 smoke tests — proves the data contracts and the SensorInterface
boundary actually hold together, before any later phase builds on them.
"""
from datetime import date, datetime, timezone

import pytest

from common.interfaces.sensor_interface import SensorInterface
from common.schemas.calibration import CalibrationParameters
from common.schemas.device import Device, DeviceStatus, SensorChannel
from common.schemas.measurement import (
    BiomarkerResult,
    MeasurementRecord,
    ProcessedMeasurement,
    RawMeasurement,
)
from simulator.sensors.simulated_sensor import SimulatedSensor


def test_sensor_interface_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        SensorInterface()  # type: ignore[abstract]


def test_raw_measurement_roundtrip():
    raw = RawMeasurement(
        device_id="SB-001",
        channel_id="CH-01",
        timestamp=datetime.now(timezone.utc),
        raw_signal=0.832,
        temperature=36.7,
        battery=87,
    )
    assert raw.device_id == "SB-001"
    assert 0 <= raw.battery <= 100


def test_measurement_record_from_pipeline():
    raw = RawMeasurement(
        device_id="SB-001",
        channel_id="CH-01",
        timestamp=datetime.now(timezone.utc),
        raw_signal=0.832,
        temperature=36.7,
        battery=87,
    )
    processed = ProcessedMeasurement(processed_signal=0.791, signal_quality=0.94)
    biomarker = BiomarkerResult(estimated_value=123.4, unit="ng/mL", confidence=0.9)

    record = MeasurementRecord.from_pipeline(raw, processed, biomarker)

    assert record.status == "valid"
    assert record.raw_signal == 0.832
    assert record.estimated_value == 123.4
    assert record.unit == "ng/mL"


def test_device_and_channel_schema():
    device = Device(
        device_id="SB-001",
        name="Prototype #1",
        firmware_version="0.1.0",
        channels=["CH-01"],
        status="online",
    )
    channel = SensorChannel(
        channel_id="CH-01",
        device_id="SB-001",
        sensor_type="pathogen_channel_1",
    )
    assert channel.device_id == device.device_id


def test_calibration_linear_apply():
    cal = CalibrationParameters(
        sensor_type="pathogen_channel_1",
        version="1.0",
        model_type="linear",
        slope=1.23,
        intercept=0.42,
        valid_from=date(2026, 8, 30),
    )
    assert cal.apply(1.0) == pytest.approx(1.65)


def test_calibration_requires_matching_params():
    with pytest.raises(ValueError):
        CalibrationParameters(
            sensor_type="x",
            version="1.0",
            model_type="linear",
            valid_from=date(2026, 8, 30),
        )


def test_simulated_sensor_satisfies_interface():
    sensor: SensorInterface = SimulatedSensor(device_id="SB-001", channel_id="CH-01")
    sensor.initialize()
    sensor.start_measurement()

    reading = sensor.read_measurement()
    assert isinstance(reading, RawMeasurement)
    assert reading.device_id == "SB-001"

    status: DeviceStatus = sensor.get_status()
    assert status.connected is True

    sensor.stop_measurement()


def test_simulated_sensor_rejects_read_before_start():
    sensor = SimulatedSensor(device_id="SB-001", channel_id="CH-01")
    sensor.initialize()
    with pytest.raises(RuntimeError):
        sensor.read_measurement()
