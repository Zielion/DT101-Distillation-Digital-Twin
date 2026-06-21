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
    controller = PLCController(mode="NORMAL_OPERATION", feed_cycle_phase="FEEDING_COLUMN")
    state = ProcessState(feed_tank_level=80.0)

    output = controller.scan(
        {
            **state.to_tags(),
            "DT101.HMI.FEED_VALVE_OPEN_REQUEST": True,
            "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": False,
            "DT101.FB.FEED_SUPPLY_VALVE_OPEN": False,
        },
        1.0,
    )

    assert output.commands["feed_valve"] == 100.0


def test_feed_valve_closed_request_commands_closed_feed_valve():
    controller = PLCController(mode="NORMAL_OPERATION", feed_cycle_phase="FEEDING_COLUMN")
    state = ProcessState()

    output = controller.scan({**state.to_tags(), "DT101.HMI.FEED_VALVE_OPEN_REQUEST": False}, 1.0)

    assert output.commands["feed_valve"] == 0.0


def test_closed_feed_valve_stops_condenser_during_normal_pressure():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState(column_pressure=105.0)

    output = controller.scan(
        {**state.to_tags(), "DT101.HMI.FEED_VALVE_OPEN_REQUEST": False},
        1.0,
    )

    assert output.commands["feed_valve"] == 0.0
    assert output.commands["condenser_valve"] == 0.0


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


def automatic_feed_snapshot(level: float, **feedback: bool) -> dict[str, float | bool]:
    return {
        **ProcessState(feed_tank_level=level).to_tags(),
        "DT101.HMI.FEED_SUPPLY_RUN_REQUEST": True,
        "DT101.HMI.FEED_VALVE_OPEN_REQUEST": True,
        **feedback,
    }


def test_low_level_fill_phase_waits_for_v100_closed_feedback_before_starting_supply():
    controller = PLCController(mode="NORMAL_OPERATION")

    waiting = controller.scan(
        automatic_feed_snapshot(10.0, **{"DT101.FB.FEED_VALVE_OPEN": True}),
        1.0,
    )
    running = controller.scan(
        automatic_feed_snapshot(10.0, **{"DT101.FB.FEED_VALVE_OPEN": False}),
        1.0,
    )

    assert controller.feed_cycle_phase == "FILLING_FEED_TANK"
    assert waiting.commands["feed_valve"] == 0.0
    assert waiting.commands["feed_supply_pump"] is False
    assert waiting.commands["feed_supply_valve"] is False
    assert running.commands["feed_supply_pump"] is True
    assert running.commands["feed_supply_valve"] is True
    assert running.commands["feed_valve"] == 0.0


def test_fill_phase_latches_between_ten_and_ninety_five_percent():
    controller = PLCController(mode="NORMAL_OPERATION")
    controller.scan(
        automatic_feed_snapshot(10.0, **{"DT101.FB.FEED_VALVE_OPEN": False}),
        1.0,
    )

    output = controller.scan(
        automatic_feed_snapshot(80.0, **{"DT101.FB.FEED_VALVE_OPEN": False}),
        1.0,
    )

    assert controller.feed_cycle_phase == "FILLING_FEED_TANK"
    assert output.commands["feed_supply_pump"] is True
    assert output.commands["feed_supply_valve"] is True
    assert output.commands["feed_valve"] == 0.0


def test_high_level_feed_phase_waits_for_supply_stopped_feedback_before_opening_v100():
    controller = PLCController(mode="NORMAL_OPERATION")
    controller.scan(
        automatic_feed_snapshot(10.0, **{"DT101.FB.FEED_VALVE_OPEN": False}),
        1.0,
    )

    waiting = controller.scan(
        automatic_feed_snapshot(
            95.0,
            **{
                "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": True,
                "DT101.FB.FEED_SUPPLY_VALVE_OPEN": True,
            },
        ),
        1.0,
    )
    feeding = controller.scan(
        automatic_feed_snapshot(
            95.0,
            **{
                "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": False,
                "DT101.FB.FEED_SUPPLY_VALVE_OPEN": False,
            },
        ),
        1.0,
    )

    assert controller.feed_cycle_phase == "FEEDING_COLUMN"
    assert waiting.commands["feed_supply_pump"] is False
    assert waiting.commands["feed_supply_valve"] is False
    assert waiting.commands["feed_valve"] == 0.0
    assert feeding.commands["feed_supply_pump"] is False
    assert feeding.commands["feed_supply_valve"] is False
    assert feeding.commands["feed_valve"] == 100.0


def test_feed_phase_latches_below_ninety_five_until_level_reaches_ten():
    controller = PLCController(mode="NORMAL_OPERATION", feed_cycle_phase="FEEDING_COLUMN")

    feeding = controller.scan(
        automatic_feed_snapshot(
            40.0,
            **{
                "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": False,
                "DT101.FB.FEED_SUPPLY_VALVE_OPEN": False,
            },
        ),
        1.0,
    )
    low = controller.scan(
        automatic_feed_snapshot(10.0, **{"DT101.FB.FEED_VALVE_OPEN": True}),
        1.0,
    )

    assert feeding.commands["feed_valve"] == 100.0
    assert controller.feed_cycle_phase == "FILLING_FEED_TANK"
    assert low.commands["feed_valve"] == 0.0
    assert low.commands["feed_supply_pump"] is False
    assert low.commands["feed_supply_valve"] is False


def test_manual_allows_cannot_bypass_phase_or_feedback_interlocks():
    fill_controller = PLCController(mode="NORMAL_OPERATION")
    disabled_supply = fill_controller.scan(
        {
            **automatic_feed_snapshot(10.0, **{"DT101.FB.FEED_VALVE_OPEN": False}),
            "DT101.HMI.FEED_SUPPLY_RUN_REQUEST": False,
        },
        1.0,
    )
    feed_controller = PLCController(mode="NORMAL_OPERATION", feed_cycle_phase="FEEDING_COLUMN")
    disabled_v100 = feed_controller.scan(
        {
            **automatic_feed_snapshot(
                80.0,
                **{
                    "DT101.FB.FEED_SUPPLY_PUMP_RUNNING": False,
                    "DT101.FB.FEED_SUPPLY_VALVE_OPEN": False,
                },
            ),
            "DT101.HMI.FEED_VALVE_OPEN_REQUEST": False,
        },
        1.0,
    )

    assert disabled_supply.commands["feed_supply_pump"] is False
    assert disabled_supply.commands["feed_supply_valve"] is False
    assert disabled_v100.commands["feed_valve"] == 0.0


def test_high_level_alarm_clears_automatically_below_ninety_five_percent():
    controller = PLCController(mode="NORMAL_OPERATION")

    high = controller.scan(automatic_feed_snapshot(95.0), 1.0)
    cleared = controller.scan(automatic_feed_snapshot(94.9), 1.0)

    assert "DT101.ALARM.FEED_TANK_HIGH_HIGH" in high.alarms
    assert "DT101.ALARM.FEED_TANK_HIGH_HIGH" not in cleared.alarms


def test_product_export_units_start_above_sixty_percent():
    controller = PLCController(mode="NORMAL_OPERATION")
    snapshot = ProcessState(distillate_tank_level=61.0, bottoms_tank_level=61.0).to_tags()

    output = controller.scan(snapshot, 1.0)

    assert output.commands["distillate_export_pump"] is True
    assert output.commands["distillate_export_valve"] is True
    assert output.commands["bottoms_export_pump"] is True
    assert output.commands["bottoms_export_valve"] is True


def test_product_export_units_hold_state_between_thresholds():
    controller = PLCController(mode="NORMAL_OPERATION")
    controller.scan(ProcessState(distillate_tank_level=61.0, bottoms_tank_level=61.0).to_tags(), 1.0)

    output = controller.scan(
        ProcessState(distillate_tank_level=40.0, bottoms_tank_level=40.0).to_tags(),
        1.0,
    )

    assert output.commands["distillate_export_pump"] is True
    assert output.commands["distillate_export_valve"] is True
    assert output.commands["bottoms_export_pump"] is True
    assert output.commands["bottoms_export_valve"] is True


def test_product_export_units_stop_below_ten_percent():
    controller = PLCController(mode="NORMAL_OPERATION")
    controller.scan(ProcessState(distillate_tank_level=61.0, bottoms_tank_level=61.0).to_tags(), 1.0)

    output = controller.scan(
        ProcessState(distillate_tank_level=9.0, bottoms_tank_level=9.0).to_tags(),
        1.0,
    )

    assert output.commands["distillate_export_pump"] is False
    assert output.commands["distillate_export_valve"] is False
    assert output.commands["bottoms_export_pump"] is False
    assert output.commands["bottoms_export_valve"] is False
