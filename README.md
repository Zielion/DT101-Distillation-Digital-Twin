# DT101 Distillation Digital Twin

This project is a Python/Streamlit digital twin of a simplified continuous binary distillation column. It connects an explainable process model to PLC-style control, simulated sensors and actuators, fault injection, a local tag bus and SQLite historian, interactive process visualization, and a DeepSeek-powered operator assistant.

The implementation follows the assignment concept described in `Distillation_Processing_Introduction_EN.md`. It prioritizes understandable process behavior and demonstrable Industry 4.0 data flow over rigorous chemical thermodynamics.

## Features

- Interactive process overview with selectable equipment and live operating values.
- Direct operator control of the top temperature from `-20` to `80 degC`.
- Direct operator control of the bottom temperature from `30` to `150 degC`.
- Seven column temperature layers from Layer 1 at the bottom to Layer 7 at the top.
- Five persistent middle-layer temperatures with a 20-second first-order response for heating and cooling.
- PLC scan cycle, operating state machine, PID-like loops, alarms, and safety interlocks.
- Operator-controlled feed valve `V-100` with separate HMI request, PLC command, physical feedback, and resulting feed flow.
- Four injectable faults covering sensor, equipment, process, and infrastructure layers.
- SQLite historian with tick-based general trends and a dedicated seven-layer temperature chart.
- DeepSeek operator recommendations with a deterministic fallback when the API is unavailable.

## Run

Create a virtual environment, install the dependencies, and start Streamlit:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open `http://localhost:8501/` if the browser does not open automatically.

### DeepSeek configuration

Set the API key in the current PowerShell session:

```powershell
$env:DEEPSEEK_API_KEY="your_api_key"
$env:DEEPSEEK_MODEL="deepseek-v4-flash"
```

Alternatively, create `.streamlit/secrets.toml` locally:

```toml
DEEPSEEK_API_KEY = "your_api_key"
```

The default API base URL is `https://api.deepseek.com`. Never commit a real API key. `.streamlit/secrets.toml` and `.env` are excluded by `.gitignore`.

If `DEEPSEEK_API_KEY` is missing or the API request fails, the assistant returns a deterministic structured recommendation so the demonstration can continue offline.

## Architecture

```text
Operator controls and fault injection
                |
                v
Digital process simulator <-> simulated sensors and actuators
                |
                v
PLC scan cycle, PID-like control, state machine, and interlocks
                |
                v
In-memory tag bus -> SQLite historian -> Streamlit trends
                |
                v
DeepSeek operator assistant or deterministic fallback
```

The local tag bus and historian simulate the role of MQTT/OPC-UA connectivity and industrial data storage without requiring external infrastructure. Fast control and safety decisions remain in the PLC layer; the AI assistant receives process evidence and produces recommendations but does not directly control equipment.

## Project structure

- `app.py`: Streamlit UI, interactive process figure, operator controls, simulation loop, trends, alarms, and AI panel.
- `distillation/process.py`: mass balances, flows, levels, pressure, product-quality proxy, and seven-layer thermal model.
- `distillation/plc.py`: PLC scan cycle, PID-like loops, operating states, actuator commands, and safety interlocks.
- `distillation/faults.py`: fault injection and alarm detection for the four assignment layers.
- `distillation/tags.py`: tag metadata for process values, commands, feedback, states, and alarms.
- `distillation/historian.py`: in-memory tag bus and SQLite historian with timestamp and tick storage.
- `distillation/visualization.py`: seven-layer temperature historian chart configuration.
- `distillation/ai_assistant.py`: DeepSeek client, prompt builder, and deterministic fallback.
- `tests/`: focused tests for process dynamics, PLC behavior, faults, historian storage, visualization, Streamlit integration, and AI prompting.

## Simulation time and historian

The simulation begins at tick `0`. Each process tick uses a one-second time step:

```text
1 tick = 1 simulated second
```

The historian stores both a UTC timestamp and the simulation tick for each tag value. Dashboard charts use the tick as the X-axis and label it `Second`, so a point at tick `n` represents simulated second `n` rather than wall-clock time.

Two historian charts are available:

- **Historian trends**: top and bottom temperatures, pressure, purity proxy, setpoints, and key actuator commands/feedback.
- **Layer temperature historian trends**: seven independently colored traces sourced directly from `DT101.PV.LAYER_01_TEMP` through `DT101.PV.LAYER_07_TEMP`.

Resetting the simulation clears the historian and creates a new equilibrium record at tick `0`. During the data-staleness fault, broker and historian writes stop intentionally while the local process model can continue advancing. The charts therefore receive no new points until communication is restored.

## Column temperature model

The sidebar temperature sliders directly set the actual endpoint process values:

- **Top temperature TIC102**: `-20..80 degC`, published as `DT101.PV.TOP_TEMP` and Layer 7.
- **Bottom temperature TIC101**: `30..150 degC`, published as `DT101.PV.BOTTOM_TEMP` and Layer 1.

These inputs are direct PV overrides for teaching and demonstration. They are not conventional setpoint-only inputs. The PLC still evaluates its PID-like loops and computes reflux and reboiler commands to preserve the course control-system architecture, but the slider values remain authoritative for the displayed top and bottom temperatures.

The five middle-layer target temperatures are linearly spaced between the current bottom and top temperatures. Their actual temperatures are persistent and approach those targets gradually on every tick:

```text
alpha = 1 - exp(-dt / 20)
next_temperature = current_temperature + alpha * (target_temperature - current_temperature)
```

All five middle layers use a 20-second time constant. This produces monotonic, non-overshooting heating and cooling behavior. Layer 4 is also used as the model's mid-column temperature.

## PLC and control behavior

The controller follows a deterministic scan-cycle model:

```text
Read inputs -> Execute state/PID/interlock logic -> Update outputs
```

Operating states:

```text
IDLE -> FILLING -> STARTUP_HEATING -> STABILIZING
     -> NORMAL_OPERATION -> FAULT_HANDLING -> SHUTDOWN
```

Implemented PID-like loops:

- `PIC101`: column pressure to condenser cooling valve.
- `TIC101`: bottom temperature to reboiler duty.
- `TIC102`: top temperature/purity proxy to reflux valve.
- `LIC101`: reflux drum level to distillate valve.
- `LIC102`: bottom sump level to bottoms valve.

Safety interlocks remain authoritative. For example, high-high pressure forces reboiler duty to zero, fully opens condenser cooling, stops the feed pump, closes the feed valve, and places the controller in `SHUTDOWN`.

## Feed valve V-100

The sidebar button opens or closes the feed valve between the feed system and the column. Each button click performs one complete one-second PLC scan and process tick so the diagram, live table, tag bus, and historian update during the same interaction.

The valve uses separate signals:

| Signal | Tag | Meaning |
| --- | --- | --- |
| Operator request | `DT101.HMI.FEED_VALVE_OPEN_REQUEST` | Requested open/closed state from the sidebar |
| PLC command | `DT101.CMD.FEED_VALVE` | Commanded valve position in percent |
| Actual feedback | `DT101.FB.FEED_VALVE_OPEN` | Physical open/closed feedback used by the display |
| Resulting flow | `DT101.PV.FEED_FLOW` | Simulated feed flow into the column |

In normal active operation, an open request commands `50%` and produces approximately `10 L/min`; a closed request commands `0%` and produces `0 L/min`. `IDLE`, high-high pressure, or low-low feed-tank level can override an open request and keep feeding stopped.

## Main temperature tags

Layer numbering runs from bottom to top:

| Physical position | Tag |
| --- | --- |
| Layer 7 - Top | `DT101.PV.LAYER_07_TEMP` |
| Layer 6 - Middle 5 | `DT101.PV.LAYER_06_TEMP` |
| Layer 5 - Middle 4 | `DT101.PV.LAYER_05_TEMP` |
| Layer 4 - Middle 3 | `DT101.PV.LAYER_04_TEMP` |
| Layer 3 - Middle 2 | `DT101.PV.LAYER_03_TEMP` |
| Layer 2 - Middle 1 | `DT101.PV.LAYER_02_TEMP` |
| Layer 1 - Bottom | `DT101.PV.LAYER_01_TEMP` |

The dashboard Tag dictionary tab contains metadata for the complete tag namespace.

## Fault catalog

| Layer | Injected fault | Alarm |
| --- | --- | --- |
| Sensor | Top temperature sensor drift | `DT101.ALARM.TOP_TEMP_SENSOR_DRIFT` |
| Equipment | Reflux valve stuck partially closed | `DT101.ALARM.REFLUX_VALVE_STUCK` |
| Process | Feed composition disturbance | `DT101.ALARM.FEED_COMPOSITION_DISTURBANCE` |
| Infrastructure | Broker/historian data staleness | `DT101.ALARM.DATA_STALE` |

Each fault is designed to produce detectable evidence within 60 simulated seconds. Faults can be injected and cleared from the sidebar.

## Demo script

1. Reset the simulation and explain that the historian starts at tick `0`.
2. Select equipment in the interactive process overview and show its role, control meaning, and live values.
3. Change the top and bottom temperatures. Point out that Layers 7 and 1 change immediately.
4. Run multiple ticks and show Layers 2-6 approaching their new linear targets gradually in the process figure and layer-temperature chart.
5. Close `V-100`. Verify the request, command, feedback, and feed flow change to the closed/no-flow condition.
6. Reopen `V-100` and verify normal feed flow returns unless a PLC interlock prevents it.
7. Inject top temperature sensor drift and show the inconsistency with pressure, reflux, and purity evidence.
8. Inject the reflux-valve-stuck fault and show the command-feedback mismatch and separation-quality degradation.
9. Inject a feed-composition disturbance and observe the temperature profile, controller outputs, and purity proxy.
10. Inject data staleness and show that historian traces stop receiving new points while the local simulation can continue.
11. Compare the general historian chart with the dedicated seven-layer temperature chart. Both use simulated seconds on the X-axis.
12. Ask the DeepSeek assistant for a recommendation and explain the safety boundary: AI recommends; PLC logic controls.

## Tests

Run the complete test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite covers:

- Process flow, level, pressure, purity, reboiler, condenser, and reflux behavior.
- Direct top and bottom temperature control.
- Seven-layer initialization, tag publication, exponential heating/cooling, convergence, and non-overshoot behavior.
- PLC state transitions, bounded outputs, high-high pressure shutdown, and low-level interlocks.
- Immediate V-100 request, command, feedback, and feed-flow synchronization.
- Four fault-detection paths.
- SQLite writes, legacy database migration, tick queries, reset behavior, and latest-tick recovery.
- General and layer-temperature chart rendering with tick-based `Second` axes.
- Streamlit session compatibility after process, historian, or visualization model changes.
- AI prompt safety content and deterministic fallback recommendations.
