# Upstream Feed Supply Design

## Purpose

Add a real upstream feed supply before the existing Feed tank. The new supply must visibly and functionally model an inlet pipe, pump `P-100`, and valve `V-099`, with one operator control keeping the pump and valve linked.

The complete displayed flow is:

```text
Input pipe -> P-100 -> V-099 -> Feed tank -> V-100 -> Column -> Condenser -> Distillate product
                                                    Column -> Bottom product
```

The existing `V-100` remains the downstream valve between the Feed tank and Column.

## Control Architecture

The sidebar exposes separate automatic-operation enables for the input supply and V-100. Reset initializes both enables active, the Feed tank at `10%`, and all three devices closed. Each button click executes one normal one-second PLC/process tick so enables, commands, feedback, flow, tank level, TagBus, and historian update together.

The PLC drives the linked devices through separate command and feedback signals:

- `DT101.CMD.FEED_SUPPLY_PUMP`: Boolean pump run command.
- `DT101.CMD.FEED_SUPPLY_VALVE`: Boolean inlet-valve open command.
- `DT101.FB.FEED_SUPPLY_PUMP_RUNNING`: Boolean actual pump feedback.
- `DT101.FB.FEED_SUPPLY_VALVE_OPEN`: Boolean actual valve feedback.

The PLC stores one mutually exclusive phase, `FILLING_FEED_TANK` or `FEEDING_COLUMN`. At `<=10%`, it closes V-100 and starts P-100/V-099 only after V-100 closed feedback is confirmed. At `>=95%`, it stops P-100/V-099 and opens V-100 only after pump-stopped and valve-closed feedback are confirmed. The previous scan's feedback creates a break-before-make interval, while operator enables and safety interlocks remain authoritative. The process publishes `DT101.PV.FEED_INLET_FLOW` as `10 L/min` only when both input feedback signals are true.

## Tank Balance And Safety

The Feed tank balance uses the same level conversion already used for the downstream feed flow:

```text
feed_tank_level_next = feed_tank_level
                     + (feed_inlet_flow - feed_flow) * dt * 0.015
```

Consequences:

- Supply `10 L/min`, column feed `10 L/min`: level remains approximately stable.
- Supply `10 L/min`, column feed `0 L/min`: level increases.
- Supply `0 L/min`, column feed `10 L/min`: level decreases as before.
- The final level remains clamped to `0-100%`.

At `>=95%`, the PLC raises `DT101.ALARM.FEED_TANK_HIGH_HIGH` while transitioning to the column-feeding phase. The alarm clears automatically below `95%`; the phase remains latched until the level reaches `<=10%`, allowing continuous automatic cycling without operator alarm reset.

## Process Overview

The left side of the Process Overview gains a cyan inlet pipe with `P-100` followed by `V-099` before the Feed tank.

- `P-100` has a circular casing and three-blade rotor based on the supplied reference.
- Pump feedback controls the visual state. Running feedback rotates the rotor with CSS animation and illuminates the casing and inlet pipe; stopped feedback freezes and dims them.
- `V-099` uses the existing professional bow-tie valve style. Valve feedback controls its open/closed color and connected line state.
- Visible status includes `P-100 RUNNING/STOPPED`, `V-099 OPEN/CLOSED`, and actual inlet flow.
- The `Feed system` focus highlights the inlet pipe, P-100, V-099, Feed tank, V-100, and their connecting lines.
- Existing tank presentation remains: label above, percentage centered inside, maximum/current liters and other metrics below.
- Equipment positions are redistributed without removing narrow-screen horizontal scrolling.

## Tags And Data Flow

Add metadata and normal historian publication for:

- `DT101.HMI.FEED_SUPPLY_RUN_REQUEST`
- `DT101.CMD.FEED_SUPPLY_PUMP`
- `DT101.CMD.FEED_SUPPLY_VALVE`
- `DT101.FB.FEED_SUPPLY_PUMP_RUNNING`
- `DT101.FB.FEED_SUPPLY_VALVE_OPEN`
- `DT101.PV.FEED_INLET_FLOW`
- `DT101.ALARM.FEED_TANK_HIGH_HIGH`

Data-staleness behavior remains unchanged: local simulation may continue, but TagBus and historian publication remains frozen while that fault is active.

## Verification

Automated tests cover reset defaults, linked commands and feedback, exact inlet flow, Feed tank material balance, high-level trip latching/reset, tag publication, historian writes, and retained downstream V-100 behavior. Streamlit integration tests verify the button updates all related state in one interaction and the Overview markup contains P-100, V-099, statuses, and inlet flow.

Rendered validation covers desktop and narrow widths, label and pipeline overlap, pump rotation state, valve state, button linkage, application errors, and console health. Existing temperature control, fault injection, historian charts, and AI assistant behavior must remain operational.
