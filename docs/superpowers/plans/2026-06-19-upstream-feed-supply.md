# Upstream Feed Supply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PLC-controlled upstream inlet pipe, pump P-100, and valve V-099 that physically replenish the Feed tank at 10 L/min with a latched 95% high-level trip.

**Architecture:** One HMI run request enters the existing PLC scan. The PLC issues linked but separately published pump and valve commands, applies a latched high-level interlock, and passes actual feedback into the process model. The Streamlit board renders feedback-driven animation and status while TagBus and SQLite receive the new tags through the existing publication path.

**Tech Stack:** Python dataclasses, Streamlit, HTML/CSS, SQLite historian, pytest, Streamlit AppTest, Playwright/Edge.

---

### Task 1: Feed Tank Inlet Process Model

**Files:**
- Modify: `distillation/config.py`
- Modify: `distillation/process.py`
- Test: `tests/test_process.py`

- [ ] **Step 1: Write failing process tests**

Add tests proving that linked pump/valve feedback produces 10 L/min, either device off produces zero, equal inlet/outlet flows hold the tank level, inlet-only operation raises it, and `DT101.PV.FEED_INLET_FLOW` is published.

```python
def test_upstream_supply_balances_normal_column_feed():
    state = ProcessState(feed_tank_level=80.0)
    next_state = state.step(
        {"feed_supply_pump": True, "feed_supply_valve": True, "feed_pump": True, "feed_valve": 50.0},
        {},
        1.0,
    )
    assert next_state.feed_inlet_flow == 10.0
    assert next_state.feed_flow == 10.0
    assert next_state.feed_tank_level == 80.0
```

- [ ] **Step 2: Run focused tests and confirm missing inlet behavior fails**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_process.py -q -k feed_supply`

- [ ] **Step 3: Implement the process balance**

Add `FEED_SUPPLY_FLOW_LPM = 10.0`, a persistent `feed_inlet_flow` field, linked feedback evaluation, and:

```python
feed_inlet_flow = FEED_SUPPLY_FLOW_LPM if feed_supply_pump and feed_supply_valve else 0.0
feed_tank_level = clamp(
    self.feed_tank_level + (feed_inlet_flow - feed_flow) * dt * 0.015,
    0.0,
    100.0,
)
```

Publish the value from `to_tags()` and retain legacy-state normalization through dataclass defaults.

- [ ] **Step 4: Run focused process tests and confirm PASS**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_process.py -q -k "feed_supply or feed_valve"`

### Task 2: PLC Commands And Latched High-Level Trip

**Files:**
- Modify: `distillation/config.py`
- Modify: `distillation/plc.py`
- Test: `tests/test_plc.py`

- [ ] **Step 1: Write failing PLC tests**

Cover linked RUN/STOP commands, 95% trip, persistence below 95%, OFF reset, and subsequent ON restart.

```python
def test_feed_supply_high_level_trip_requires_off_then_on_restart():
    controller = PLCController(mode="NORMAL_OPERATION")
    run = {**ProcessState(feed_tank_level=95.0).to_tags(), "DT101.HMI.FEED_SUPPLY_RUN_REQUEST": True}
    tripped = controller.scan(run, 1.0)
    assert tripped.commands["feed_supply_pump"] is False
    assert tripped.commands["feed_supply_valve"] is False
    assert "DT101.ALARM.FEED_TANK_HIGH_HIGH" in tripped.alarms
```

- [ ] **Step 2: Run focused tests and confirm commands are missing**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_plc.py -q -k feed_supply`

- [ ] **Step 3: Implement PLC latch semantics**

Add `FEED_TANK_HIGH_HIGH = 95.0` and `feed_supply_high_trip_latched: bool = False`. A level at or above 95% sets the latch. An OFF request below 95% clears it. Both commands equal `request and not latch`; while latched, publish the high-high alarm. Keep this upstream filling system independent of the column's `IDLE` mode.

- [ ] **Step 4: Run all PLC tests and confirm PASS**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_plc.py -q`

### Task 3: Tags, Session State, And Button Integration

**Files:**
- Modify: `distillation/tags.py`
- Modify: `app.py`
- Test: `tests/test_app_feed_valve.py`

- [ ] **Step 1: Write failing AppTest coverage**

Verify reset defaults, a single button interaction, request/command/feedback/flow publication, tick increment, and tag metadata.

```python
def test_input_supply_toggle_updates_linked_devices_and_flow():
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    button_with_label(app, "Start input supply P-100 / V-099").click().run()
    assert app.session_state.feed_supply_run_request is True
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_PUMP_RUNNING"] is True
    assert app.session_state.bus.tags["DT101.FB.FEED_SUPPLY_VALVE_OPEN"] is True
    assert app.session_state.bus.tags["DT101.PV.FEED_INLET_FLOW"] == 10.0
```

- [ ] **Step 2: Run focused AppTest and confirm missing session/button behavior fails**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_app_feed_valve.py -q -k input_supply`

- [ ] **Step 3: Wire the app and tag dictionary**

Initialize/reset `feed_supply_run_request = False`; add a callback that toggles it and calls `simulation_tick()`. Add the HMI request to the PLC snapshot, derive feedback from commands, pass feedback to `ProcessState.step()`, and publish all seven approved tags. Add sidebar caption/button and Feed-system watch values.

- [ ] **Step 4: Run focused AppTest and confirm PASS**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_app_feed_valve.py -q -k "input_supply or feed_valve"`

### Task 4: P-100 And V-099 Process Overview

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_feed_valve.py`

- [ ] **Step 1: Write failing markup tests**

Assert the Overview includes `Input`, `P-100`, `V-099`, feedback statuses, inlet flow, rotor elements, and a running-only animation class while retaining V-100 and all tank-capacity labels.

- [ ] **Step 2: Run the markup tests and confirm FAIL**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_app_feed_valve.py -q -k input_supply_overview`

- [ ] **Step 3: Implement the visual equipment**

Redistribute the horizontal board. Add a cyan inlet line, circular three-blade P-100 rotor, bow-tie V-099, feedback-driven running/open classes, actual inlet-flow text, and Feed-system focus highlighting. Use CSS `@keyframes pump-rotation` only on the running feedback class and preserve the board's minimum-width horizontal scrolling.

- [ ] **Step 4: Run all Streamlit integration tests and confirm PASS**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_app_feed_valve.py -q`

### Task 5: Complete Verification

**Files:**
- Verify: `app.py`, `distillation/`, `tests/`

- [ ] **Step 1: Compile and run the full suite**

```powershell
.\.venv\Scripts\python.exe -m compileall app.py distillation tests
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp_upstream_feed_supply
```

- [ ] **Step 2: Run rendered validation**

Open `http://localhost:8501/` with Playwright/Edge. Check desktop `1440x1000` and narrow `1100x900`, no app/console errors, no overlaps, stopped default, RUN animation/open valve/10 L/min after button click, and latched trip presentation at 95%.

- [ ] **Step 3: Inspect final scope**

Run `git diff --check` and `git status --short`. Confirm no process/PLC tags were removed and no README or technical-introduction files changed.
