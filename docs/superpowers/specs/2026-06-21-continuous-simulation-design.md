# Continuous Simulation Design

## Objective

Add an operator-controlled continuous simulation mode to the Streamlit sidebar. While active, the application advances exactly one simulation tick every 0.5 seconds. One tick continues to represent one simulated second.

## Interface

- Add a `Start continuous run` button when continuous mode is stopped.
- Replace it with `Stop continuous run` while continuous mode is active.
- Keep `Run selected ticks` and `Single PLC scan + process tick` unchanged.
- Resetting the simulation also stops continuous mode.

## Runtime Behavior

- Store the running state in `st.session_state.continuous_run`, defaulting to `False`.
- Use Streamlit's native fragment timer with a 0.5-second interval.
- Each scheduled fragment execution calls `simulation_tick()` exactly once while running.
- Starting or stopping does not itself advance a tick.
- Session state prevents duplicate timer loops.

## Safety And Compatibility

- Existing PLC, fault, historian, alarm, and process logic remain unchanged because all automatic advancement uses the existing `simulation_tick()` path.
- The implementation does not use a blocking loop or background thread.
- Manual controls remain available when continuous mode is stopped.

## Verification

- Confirm start and stop controls toggle the session state.
- Confirm an active timer callback advances exactly one tick.
- Confirm an inactive callback does not advance the simulation.
- Confirm reset stops continuous mode.
- Run compile checks and the complete pytest suite.
