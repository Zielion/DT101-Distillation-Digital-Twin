from distillation.plc import PLCController
from distillation.process import ProcessState


def test_high_high_pressure_forces_shutdown_actions():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState(column_pressure=145.0)

    output = controller.scan(state.to_tags(), 1.0)

    assert output.commands["reboiler_duty"] == 0.0
    assert output.commands["condenser_valve"] == 100.0
    assert output.commands["feed_pump"] is False
    assert output.mode == "SHUTDOWN"
    assert "DT101.ALARM.HIGH_HIGH_PRESSURE" in output.alarms


def test_low_feed_tank_level_stops_feed_pump():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState(feed_tank_level=5.0)

    output = controller.scan(state.to_tags(), 1.0)

    assert output.commands["feed_pump"] is False
    assert "DT101.ALARM.FEED_TANK_LOW_LOW" in output.alarms


def test_feed_valve_open_request_commands_normal_feed_valve_position():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState()

    output = controller.scan({**state.to_tags(), "DT101.HMI.FEED_VALVE_OPEN_REQUEST": True}, 1.0)

    assert output.commands["feed_valve"] == 50.0


def test_feed_valve_closed_request_commands_closed_feed_valve():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState()

    output = controller.scan({**state.to_tags(), "DT101.HMI.FEED_VALVE_OPEN_REQUEST": False}, 1.0)

    assert output.commands["feed_valve"] == 0.0


def test_low_feed_tank_level_closes_feed_valve_even_when_open_requested():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState(feed_tank_level=5.0)

    output = controller.scan({**state.to_tags(), "DT101.HMI.FEED_VALVE_OPEN_REQUEST": True}, 1.0)

    assert output.commands["feed_pump"] is False
    assert output.commands["feed_valve"] == 0.0


def test_idle_mode_closes_feed_valve_even_when_open_requested():
    controller = PLCController(mode="IDLE")
    state = ProcessState()

    output = controller.scan({**state.to_tags(), "DT101.HMI.FEED_VALVE_OPEN_REQUEST": True}, 1.0)

    assert output.commands["feed_valve"] == 0.0


def test_normal_operation_outputs_are_bounded():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState()

    output = controller.scan(state.to_tags(), 1.0)

    for key in [
        "feed_valve",
        "reboiler_duty",
        "condenser_valve",
        "reflux_valve",
        "distillate_valve",
        "bottoms_valve",
    ]:
        assert 0.0 <= output.commands[key] <= 100.0


def test_bottom_temperature_setpoint_changes_reboiler_duty():
    state = ProcessState(bottom_temperature=100.0)
    low_setpoint_controller = PLCController(mode="NORMAL_OPERATION")
    high_setpoint_controller = PLCController(mode="NORMAL_OPERATION")

    low_snapshot = {**state.to_tags(), "DT101.SP.BOTTOM_TEMP": 30.0}
    high_snapshot = {**state.to_tags(), "DT101.SP.BOTTOM_TEMP": 150.0}

    low_output = low_setpoint_controller.scan(low_snapshot, 1.0)
    high_output = high_setpoint_controller.scan(high_snapshot, 1.0)

    assert high_output.commands["reboiler_duty"] > low_output.commands["reboiler_duty"]


def test_top_temperature_setpoint_changes_reflux_valve_command():
    state = ProcessState(top_temperature=60.0)
    low_setpoint_controller = PLCController(mode="NORMAL_OPERATION")
    high_setpoint_controller = PLCController(mode="NORMAL_OPERATION")

    low_snapshot = {**state.to_tags(), "DT101.SP.TOP_TEMP": -20.0}
    high_snapshot = {**state.to_tags(), "DT101.SP.TOP_TEMP": 80.0}

    low_output = low_setpoint_controller.scan(low_snapshot, 1.0)
    high_output = high_setpoint_controller.scan(high_snapshot, 1.0)

    assert low_output.commands["reflux_valve"] > high_output.commands["reflux_valve"]
