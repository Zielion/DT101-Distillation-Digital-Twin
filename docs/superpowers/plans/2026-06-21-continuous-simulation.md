# Continuous Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a start/stop control that advances one simulation tick every 0.5 seconds while active.

**Architecture:** Keep scheduling in Streamlit using a native timed fragment and keep all process advancement inside the existing `simulation_tick()` function. A session-state Boolean controls whether scheduled fragment executions advance the model.

**Tech Stack:** Python, Streamlit, pytest, Streamlit AppTest

---

### Task 1: Continuous-run state and callback

**Files:**
- Modify: `tests/test_app_feed_valve.py`
- Modify: `app.py`

- [ ] **Step 1: Write failing state tests**

Add AppTest coverage asserting that the initial state is stopped, the start/stop button toggles `continuous_run`, and reset clears it.

- [ ] **Step 2: Verify the tests fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_app_feed_valve.py -k continuous_run --basetemp=.pytest_tmp_continuous_red
```

Expected: failure because `continuous_run` and its controls do not exist.

- [ ] **Step 3: Implement session state and controls**

Initialize `st.session_state.continuous_run = False`, add a toggle callback, render `Start continuous run` or `Stop continuous run`, and set the value to `False` in `reset_simulation()`.

- [ ] **Step 4: Verify state tests pass**

Run the focused command from Step 2 and expect all selected tests to pass.

### Task 2: Timed single-tick execution

**Files:**
- Modify: `tests/test_app_feed_valve.py`
- Modify: `app.py`

- [ ] **Step 1: Write a failing callback test**

Add a test that invokes the timer fragment while active and verifies `tick_count` increases by exactly one; verify the inactive fragment leaves it unchanged.

- [ ] **Step 2: Verify the callback test fails**

Run the focused continuous-run tests and expect failure because no timer fragment exists.

- [ ] **Step 3: Implement the native timer fragment**

Define a `@st.fragment(run_every=0.5)` function that calls `simulation_tick()` once only when `continuous_run` is true, and render the fragment from the sidebar.

- [ ] **Step 4: Verify focused tests pass**

Run the focused continuous-run tests and expect all to pass.

### Task 3: Full verification and runtime reload

**Files:**
- Verify: `app.py`
- Verify: `tests/test_app_feed_valve.py`

- [ ] **Step 1: Run complete verification**

```powershell
.\.venv\Scripts\python.exe -m compileall -q app.py distillation tests
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp_continuous_final
git diff --check
```

Expected: compile succeeds, all tests pass, and `git diff --check` reports no errors.

- [ ] **Step 2: Restart and health-check Streamlit**

Restart the single local Streamlit process and verify `http://127.0.0.1:8501/_stcore/health` returns HTTP 200 with body `ok`.
