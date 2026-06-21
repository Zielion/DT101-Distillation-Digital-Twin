from math import exp
from types import SimpleNamespace

import pytest

from distillation import process
from distillation.process import ProcessState, derive_column_layer_temperatures


def test_reflux_loss_raises_top_temperature_and_lowers_purity():
    baseline = ProcessState()
    low_reflux = ProcessState()

    for _ in range(80):
        baseline = baseline.step(
            {"reflux_valve": 70.0, "reboiler_duty": 55.0, "condenser_valve": 55.0},
            {},
            1.0,
        )
        low_reflux = low_reflux.step(
            {"reflux_valve": 5.0, "reboiler_duty": 55.0, "condenser_valve": 55.0},
            {},
            1.0,
        )

    assert low_reflux.top_temperature > baseline.top_temperature + 1.0
    assert low_reflux.purity_proxy < baseline.purity_proxy - 1.0


def test_higher_reboiler_duty_raises_bottom_temperature_and_pressure():
    low_duty = ProcessState()
    high_duty = ProcessState()

    for _ in range(80):
        low_duty = low_duty.step(
            {"reflux_valve": 50.0, "reboiler_duty": 25.0, "condenser_valve": 55.0},
            {},
            1.0,
        )
        high_duty = high_duty.step(
            {"reflux_valve": 50.0, "reboiler_duty": 85.0, "condenser_valve": 55.0},
            {},
            1.0,
        )

    assert high_duty.bottom_temperature > low_duty.bottom_temperature + 3.0
    assert high_duty.column_pressure > low_duty.column_pressure + 3.0


def test_manual_temperature_inputs_override_process_temperatures():
    state = ProcessState(top_temperature=79.0, mid_temperature=88.0, bottom_temperature=100.0)

    next_state = state.step(
        {
            "top_temperature": 37.5,
            "bottom_temperature": 123.0,
            "reflux_valve": 50.0,
            "reboiler_duty": 50.0,
            "condenser_valve": 55.0,
        },
        {},
        1.0,
    )

    assert next_state.top_temperature == 37.5
    assert next_state.bottom_temperature == 123.0
    assert next_state.mid_temperature == pytest.approx(next_state.middle_layer_temperatures[2])
    assert next_state.mid_temperature != (37.5 + 123.0) / 2.0


def test_column_layer_temperatures_are_seven_bottom_to_top_values():
    state = ProcessState(top_temperature=40.0, bottom_temperature=100.0)

    layers = state.column_layer_temperatures()

    assert len(layers) == 7
    assert layers == (100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0)


def test_column_layer_temperatures_are_published_as_tags():
    state = ProcessState(top_temperature=40.0, bottom_temperature=100.0)

    tags = state.to_tags()

    assert tags["DT101.PV.LAYER_01_TEMP"] == 100.0
    assert tags["DT101.PV.LAYER_04_TEMP"] == 70.0
    assert tags["DT101.PV.LAYER_07_TEMP"] == 40.0


def test_middle_layers_apply_exact_first_order_response_to_new_endpoints():
    state = ProcessState(top_temperature=40.0, bottom_temperature=100.0)
    initial_layers = state.column_layer_temperatures()

    next_state = state.step(
        {"top_temperature": 10.0, "bottom_temperature": 130.0},
        {},
        1.0,
    )

    targets = process.interpolate_column_layer_temperatures(130.0, 10.0)
    alpha = 1.0 - exp(-1.0 / 20.0)
    expected_middle = tuple(
        current + alpha * (target - current)
        for current, target in zip(initial_layers[1:-1], targets[1:-1])
    )

    assert next_state.bottom_temperature == 130.0
    assert next_state.top_temperature == 10.0
    assert next_state.middle_layer_temperatures == pytest.approx(expected_middle)
    assert next_state.mid_temperature == pytest.approx(expected_middle[2])


def test_middle_layers_converge_exponentially_for_heating_and_cooling():
    heating = ProcessState(top_temperature=40.0, bottom_temperature=100.0)
    cooling = ProcessState(top_temperature=80.0, bottom_temperature=140.0)
    heating_start = heating.column_layer_temperatures()[3]
    cooling_start = cooling.column_layer_temperatures()[3]
    heating_target = process.interpolate_column_layer_temperatures(140.0, 80.0)[3]
    cooling_target = process.interpolate_column_layer_temperatures(100.0, 40.0)[3]

    for _ in range(20):
        heating = heating.step({"top_temperature": 80.0, "bottom_temperature": 140.0}, {}, 1.0)
        cooling = cooling.step({"top_temperature": 40.0, "bottom_temperature": 100.0}, {}, 1.0)

    expected_fraction = 1.0 - exp(-1.0)
    heating_fraction = (heating.column_layer_temperatures()[3] - heating_start) / (heating_target - heating_start)
    cooling_fraction = (cooling_start - cooling.column_layer_temperatures()[3]) / (cooling_start - cooling_target)

    assert heating_start < heating.column_layer_temperatures()[3] < heating_target
    assert cooling_target < cooling.column_layer_temperatures()[3] < cooling_start
    assert heating_fraction == pytest.approx(expected_fraction)
    assert cooling_fraction == pytest.approx(expected_fraction)


def test_middle_layers_reach_about_95_percent_after_60_ticks():
    state = ProcessState(top_temperature=40.0, bottom_temperature=100.0)
    initial = state.column_layer_temperatures()[3]
    target = process.interpolate_column_layer_temperatures(140.0, 80.0)[3]

    for _ in range(60):
        state = state.step({"top_temperature": 80.0, "bottom_temperature": 140.0}, {}, 1.0)

    fraction = (state.column_layer_temperatures()[3] - initial) / (target - initial)
    assert fraction == pytest.approx(1.0 - exp(-3.0))


def test_layer_tags_publish_transient_middle_temperatures():
    state = ProcessState(top_temperature=40.0, bottom_temperature=100.0)
    state = state.step({"top_temperature": 20.0, "bottom_temperature": 130.0}, {}, 1.0)
    tags = state.to_tags()

    assert tags["DT101.PV.LAYER_04_TEMP"] == pytest.approx(state.middle_layer_temperatures[2])
    assert tags["DT101.PV.MID_TEMP"] == pytest.approx(state.middle_layer_temperatures[2])
    assert tags["DT101.PV.LAYER_04_TEMP"] != 70.0


def test_normalize_process_state_upgrades_legacy_state_at_equilibrium():
    normalize = getattr(process, "normalize_process_state", None)
    assert normalize is not None
    legacy = SimpleNamespace(top_temperature=40.0, bottom_temperature=100.0, feed_tank_level=72.0)

    upgraded = normalize(legacy)

    assert isinstance(upgraded, ProcessState)
    assert upgraded.feed_tank_level == 72.0
    assert upgraded.column_layer_temperatures() == (100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0)


def test_layer_temperature_derivation_supports_stale_state_objects():
    class StaleProcessState:
        top_temperature = 40.0
        bottom_temperature = 100.0

    layers = derive_column_layer_temperatures(StaleProcessState())

    assert layers == (100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0)


def test_closed_feed_valve_stops_feed_flow():
    state = ProcessState()

    next_state = state.step({"feed_pump": True, "feed_valve": 0.0}, {}, 1.0)

    assert next_state.feed_flow == 0.0


def test_no_column_feed_stops_product_inflows_and_preserves_tank_levels():
    state = ProcessState(distillate_tank_level=22.3, bottoms_tank_level=31.8)

    next_state = state.step(
        {
            "feed_pump": True,
            "feed_valve": 0.0,
            "distillate_valve": 100.0,
            "bottoms_valve": 100.0,
            "distillate_export_pump": False,
            "distillate_export_valve": False,
            "bottoms_export_pump": False,
            "bottoms_export_valve": False,
        },
        {},
        1.0,
    )

    assert next_state.feed_flow == 0.0
    assert next_state.distillate_flow == 0.0
    assert next_state.bottoms_flow == 0.0
    assert next_state.distillate_tank_level == state.distillate_tank_level
    assert next_state.bottoms_tank_level == state.bottoms_tank_level


def test_fully_open_feed_valve_produces_twenty_liters_per_minute():
    state = ProcessState()

    next_state = state.step({"feed_pump": True, "feed_valve": 100.0}, {}, 1.0)

    assert next_state.feed_flow == 20.0


def test_feed_supply_balances_normal_column_feed():
    state = ProcessState(feed_tank_level=80.0)

    next_state = state.step(
        {
            "feed_supply_pump": True,
            "feed_supply_valve": True,
            "feed_pump": True,
            "feed_valve": 50.0,
        },
        {},
        1.0,
    )

    assert next_state.feed_inlet_flow == 10.0
    assert next_state.feed_flow == 10.0
    assert next_state.feed_tank_level == 80.0
    assert next_state.to_tags()["DT101.PV.FEED_INLET_FLOW"] == 10.0


@pytest.mark.parametrize(
    ("pump_running", "valve_open"),
    [(False, False), (True, False), (False, True)],
)
def test_feed_supply_requires_both_pump_and_valve(pump_running, valve_open):
    state = ProcessState(feed_tank_level=80.0)

    next_state = state.step(
        {
            "feed_supply_pump": pump_running,
            "feed_supply_valve": valve_open,
            "feed_pump": False,
            "feed_valve": 0.0,
        },
        {},
        1.0,
    )

    assert next_state.feed_inlet_flow == 0.0
    assert next_state.feed_tank_level == 80.0


def test_feed_supply_raises_level_when_column_feed_is_stopped():
    state = ProcessState(feed_tank_level=80.0)

    next_state = state.step(
        {
            "feed_supply_pump": True,
            "feed_supply_valve": True,
            "feed_pump": False,
            "feed_valve": 0.0,
        },
        {},
        1.0,
    )

    assert next_state.feed_inlet_flow == 10.0
    assert next_state.feed_tank_level == pytest.approx(80.15)


def test_product_export_requires_linked_pump_and_valve():
    state = ProcessState(distillate_tank_level=70.0, bottoms_tank_level=70.0)

    next_state = state.step(
        {
            "distillate_export_pump": True,
            "distillate_export_valve": True,
            "bottoms_export_pump": True,
            "bottoms_export_valve": False,
            "distillate_valve": 0.0,
            "bottoms_valve": 0.0,
        },
        {},
        1.0,
    )

    assert next_state.distillate_outlet_flow == 15.0
    assert next_state.bottoms_outlet_flow == 0.0
    assert next_state.distillate_tank_level < state.distillate_tank_level
    assert next_state.bottoms_tank_level == state.bottoms_tank_level


def test_product_export_flows_are_published_as_tags():
    state = ProcessState().step(
        {
            "distillate_export_pump": True,
            "distillate_export_valve": True,
            "bottoms_export_pump": True,
            "bottoms_export_valve": True,
        },
        {},
        1.0,
    )

    tags = state.to_tags()
    assert tags["DT101.PV.DISTILLATE_OUTLET_FLOW"] == 15.0
    assert tags["DT101.PV.BOTTOMS_OUTLET_FLOW"] == 15.0


def test_condenser_opening_reduces_pressure_trend():
    restricted_cooling = ProcessState(column_pressure=120.0)
    strong_cooling = ProcessState(column_pressure=120.0)

    for _ in range(50):
        restricted_cooling = restricted_cooling.step(
            {"reboiler_duty": 70.0, "condenser_valve": 10.0, "reflux_valve": 50.0},
            {},
            1.0,
        )
        strong_cooling = strong_cooling.step(
            {"reboiler_duty": 70.0, "condenser_valve": 100.0, "reflux_valve": 50.0},
            {},
            1.0,
        )

    assert strong_cooling.column_pressure < restricted_cooling.column_pressure - 8.0
