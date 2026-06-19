from datetime import datetime, timedelta, timezone

from distillation.faults import FaultManager
from distillation.process import ProcessState


def test_top_temperature_drift_detection_uses_inconsistency():
    manager = FaultManager()
    state = ProcessState(top_temperature=88.0, column_pressure=104.0, reflux_flow=6.0, purity_proxy=96.0)

    alarms = manager.detect(state.to_tags(), {"reflux_valve_feedback": 55.0}, datetime.now(timezone.utc))

    assert "DT101.ALARM.TOP_TEMP_SENSOR_DRIFT" in alarms


def test_reflux_valve_stuck_detection_uses_command_feedback_mismatch():
    manager = FaultManager()
    state = ProcessState(reflux_flow=1.2)

    alarms = manager.detect(
        state.to_tags(),
        {"reflux_valve": 70.0, "reflux_valve_feedback": 20.0},
        datetime.now(timezone.utc),
    )

    assert "DT101.ALARM.REFLUX_VALVE_STUCK" in alarms


def test_feed_composition_disturbance_detection_uses_purity_and_temperature():
    manager = FaultManager()
    state = ProcessState(feed_composition_light=0.64, top_temperature=87.0, purity_proxy=88.0)

    alarms = manager.detect(state.to_tags(), {}, datetime.now(timezone.utc))

    assert "DT101.ALARM.FEED_COMPOSITION_DISTURBANCE" in alarms


def test_data_staleness_detection_uses_old_heartbeat():
    manager = FaultManager()
    state = ProcessState()
    old = datetime.now(timezone.utc) - timedelta(seconds=70)

    alarms = manager.detect(state.to_tags(), {"last_heartbeat": old}, datetime.now(timezone.utc))

    assert "DT101.ALARM.DATA_STALE" in alarms
