# Heating / Reboiler Code Review - Annotated Notes

Scope: review only. No source code was modified.  
Related source files:

- `distillation/process.py`
- `distillation/plc.py`
- `app.py`
- `tests/test_process.py`
- `tests/test_plc.py`

## 1. Process model: where heating enters the simulation

Source: `distillation/process.py`

```python
reboiler_duty = float(commands.get("reboiler_duty", 50.0))
# Review:
# - This reads the PLC command for the reboiler.
# - The command is interpreted as a percentage duty, normally 0-100.
# - If no command is provided, the model assumes 50% duty.
# - This is reasonable for a teaching simulation, but a real plant would distinguish
#   steam valve position, heater power, and measured heat duty.
```

```python
condensate_flow = clamp(3.0 + condenser_valve * 0.055 + reboiler_duty * 0.025, 1.0, 12.0)
# Review:
# - Reboiler duty increases vapor generation, which then increases overhead condensation.
# - This is a simplified coupling: more boil-up means more vapor reaches the condenser.
# - The `clamp(..., 1.0, 12.0)` prevents unrealistic negative or runaway condensate flow.
# - This is good for demo stability.
# - Limitation: the model does not explicitly calculate vapor-liquid equilibrium or latent heat.
```

```python
target_bottom = 100.0 + 0.12 * (reboiler_duty - 50.0) + 0.06 * feed_flow_deviation + 0.03 * comp_deviation
# Review:
# - This is the main bottom-temperature heating relationship.
# - Base bottom temperature is 100 degC at 50% reboiler duty.
# - Higher reboiler duty increases target bottom temperature.
# - Higher feed flow also increases target bottom temperature slightly, representing added column load.
# - Feed composition disturbance also shifts the temperature profile.
# - The relationship is linear and intentionally simplified for explainability.
```

```python
target_pressure = 105.0 + 0.22 * (reboiler_duty - 50.0) + 0.18 * feed_flow_deviation - 0.32 * (condenser_valve - 55.0)
# Review:
# - This connects heating to pressure.
# - Increasing reboiler duty increases vapor generation, so column pressure rises.
# - Increasing condenser valve opening lowers pressure by improving cooling/condensation.
# - This is a useful demo behavior because excess heating can trigger pressure alarms.
# - This supports the assignment's safety/control story.
```

```python
bottom_temperature = self.bottom_temperature + (target_bottom - self.bottom_temperature) * dt / 22.0
column_pressure = clamp(self.column_pressure + (target_pressure - self.column_pressure) * dt / 16.0, 80.0, 160.0)
# Review:
# - These are first-order dynamic responses.
# - Bottom temperature has a slower time constant than pressure.
# - This avoids unrealistic instant jumps after reboiler duty changes.
# - The pressure clamp protects the simulation from extreme values.
# - For a demo, this is appropriate because trends are visible and explainable.
```

## 2. PLC: heating controller and safety interlocks

Source: `distillation/plc.py`

```python
"TIC101": PID(kp=1.2, ki=0.03, bias=50.0, reverse=False),
# Review:
# - TIC101 is the bottom-temperature controller.
# - It controls reboiler duty based on bottom temperature.
# - `bias=50.0` means the default output is around 50% duty.
# - `reverse=False` means if bottom temperature is below setpoint,
#   error is positive and output increases.
# - That direction is correct for heating control.
```

```python
"reboiler_duty": self.pids["TIC101"].compute(BOTTOM_TEMP_SETPOINT, bottom_temp, dt),
# Review:
# - This calculates the reboiler command every PLC scan.
# - The controlled variable is `DT101.PV.BOTTOM_TEMP`.
# - The manipulated variable is `reboiler_duty`.
# - This matches the intended loop:
#   TIC101: bottom temperature -> reboiler duty.
```

```python
if pressure > PRESSURE_HIGH_HIGH:
    alarms.append("DT101.ALARM.HIGH_HIGH_PRESSURE")
    commands["reboiler_duty"] = 0.0
    commands["condenser_valve"] = 100.0
    commands["feed_pump"] = False
    commands["esd_shutdown"] = True
    self.mode = "SHUTDOWN"
# Review:
# - This is the most important heating-related safety interlock.
# - If pressure exceeds the high-high limit, heating is immediately cut.
# - Cooling is fully opened and feed is stopped.
# - This is correct: the PLC handles safety locally instead of relying on AI.
# - This is strong evidence for the demo/viva: AI recommends, PLC protects.
```

```python
if bottom_level < BOTTOM_LOW_LOW:
    alarms.append("DT101.ALARM.BOTTOM_LEVEL_LOW_LOW")
    commands["reboiler_duty"] = 0.0
# Review:
# - This prevents dry heating.
# - If bottom sump level is too low, the reboiler duty is cut to zero.
# - This is a good industrial safety behavior.
# - In a real plant, this could be a hardwired or safety PLC interlock.
```

```python
if self.mode == "IDLE":
    commands.update({"feed_pump": False, "feed_valve": 0.0, "reboiler_duty": 0.0})
# Review:
# - In IDLE, heating is forced off.
# - This prevents heating before startup sequence begins.
# - Good behavior for a controlled startup model.
```

## 3. UI: how heating is shown to the operator

Source: `app.py`

```python
reboiler_duty = st.session_state.bus.tags.get("DT101.CMD.REBOILER_DUTY", 0)
# Review:
# - The UI reads the latest reboiler duty command from the tag bus.
# - If no command has been published yet, it displays 0%.
# - This keeps the SCADA-style diagram safe at startup.
```

```python
<!-- Reboiler and vapor return -->
...
<text x="858" y="558" font-family="Consolas, monospace" font-size="11" fill="#b7edf5">REB-101 duty {reboiler_duty:04.1f}%</text>
# Review:
# - The diagram explicitly displays `REB-101 duty`.
# - This is useful during demo because the audience can see the heating command change.
# - The SVG also visually links reboiler heat to vapor return.
```

```python
"Reboiler": {
    "role": "Adds heat at the column bottom to generate boil-up vapor.",
    "watch": {
        "Bottom temperature": f"{state.bottom_temperature:.1f} degC",
        "Bottom sump level": f"{state.bottom_sump_level:.1f} %",
        "Pressure": f"{state.column_pressure:.1f} kPa",
    },
    "control": "TIC101 adjusts reboiler duty; safety interlock cuts duty on high-high pressure or low-low bottom level.",
    "fault_link": "Excess duty can increase pressure; dry heating must be prevented by PLC interlock.",
}
# Review:
# - This operator-facing explanation is accurate and aligned with the PLC/process code.
# - It correctly tells users what to watch: bottom temperature, bottom level, pressure.
# - It also explains the safety link: high pressure and low bottom level cut heating.
```

## 4. Tests covering heating behavior

Source: `tests/test_process.py`

```python
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
# Review:
# - This test directly verifies the heating model.
# - Higher reboiler duty must increase both bottom temperature and pressure.
# - This is the correct expected direction for the simplified model.
```

Source: `tests/test_plc.py`

```python
def test_high_high_pressure_forces_shutdown_actions():
    controller = PLCController(mode="NORMAL_OPERATION")
    state = ProcessState(column_pressure=145.0)

    output = controller.scan(state.to_tags(), 1.0)

    assert output.commands["reboiler_duty"] == 0.0
    assert output.commands["condenser_valve"] == 100.0
    assert output.commands["feed_pump"] is False
    assert output.mode == "SHUTDOWN"
    assert "DT101.ALARM.HIGH_HIGH_PRESSURE" in output.alarms
# Review:
# - This test verifies the high-pressure heating safety interlock.
# - It confirms that the PLC cuts heat, opens cooling, stops feed, and transitions to shutdown.
# - This is one of the strongest safety-related tests in the project.
```

## 5. Review findings

### Finding 1: Heating logic is coherent for a teaching digital twin

The heating path is internally consistent:

```text
TIC101 -> reboiler_duty -> bottom_temperature / vapor generation -> pressure -> safety interlock
```

This is exactly the control narrative needed for the assignment.

### Finding 2: Safety handling is appropriately local to the PLC

The code correctly keeps critical protection inside `PLCController.scan()`:

- high-high pressure cuts reboiler duty
- high-high pressure fully opens condenser cooling
- low-low bottom level cuts reboiler duty
- IDLE mode forces reboiler duty to zero

This matches good industrial design: AI can advise, but PLC protects.

### Finding 3: The model is intentionally simplified, not thermodynamic

The process model uses linear relationships and first-order lag. This is acceptable for the demo, but it should be explained clearly during viva:

- no rigorous VLE calculation
- no explicit steam pressure or heat-transfer coefficient
- no tray-by-tray temperature profile
- no condenser/reboiler energy balance in physical units

This is not a bug. It is a deliberate simplification for explainable simulation.

### Finding 4: Main improvement opportunity if source changes are allowed later

If future changes are allowed, consider adding an explicit reboiler fault:

```text
reboiler_overduty
reboiler_failure_low_duty
steam_valve_stuck
bottom_level_sensor_stuck
```

Right now the four required assignment faults are covered, but there is no dedicated heating equipment fault. The existing equipment fault focuses on the reflux valve.

## 6. Summary

The heating/reboiler code is suitable for the assignment demo. It clearly supports:

- bottom temperature control through TIC101
- pressure response to heating
- high-pressure shutdown
- dry-heating prevention through low-low bottom level
- operator visualization of reboiler duty
- tests that verify heating behavior and safety response

No immediate source-code change is required for the current assignment scope.
