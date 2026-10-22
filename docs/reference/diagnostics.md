# Diagnostics guide

Diagnostics is the best place to answer “why is Heat Steward doing that?” It
shows the decision the controller made, the state Home Assistant reported, and
the result of recent commands.

## Start at the top

Check **Controller health** and **Current issues** first. They show whether the
control loop is healthy, waiting for Home Assistant, overdue, or in a safety
stop. Requested and actual relay states help distinguish a calculated decision
from a relay delay or failure.

## Read a room decision

For the affected room, compare:

- measured temperature and whether it comes from the external sensor or TRV;
- sensor age, stale limit, and any fallback warning;
- schedule preset and effective target;
- active override scope and expiry;
- heat demand and its explanation;
- calculated TRV command and command result;
- TRV availability, last seen, valve state, battery, and link quality;
- predictive status, evidence, learned values, and current action.

The explanation text states why the target, demand, and command were chosen.
Remember that the TRV command intentionally differs from the room target because
of the heating and idle offsets.

## Use temperature history

Select one or more rooms and choose 6 hours, 24 hours, or 7 days. The default
series show effective room temperature, scheduled temperature, and the periods
when the room requested heat while the boiler was on. Optional series expose
external and TRV readings, effective targets, and TRV commands.

Useful patterns include:

- a flat external reading followed by fallback, suggesting a stale sensor;
- room demand with no boiler-on period, suggesting threshold, anti-cycle, or
  relay trouble;
- continued rise after demand ends, which helps explain room overshoot;
- repeated rapid demand changes, suggesting hysteresis needs adjustment.

## Recent activity

Recent activity shows settings changes, override actions, safety events, and
Home Assistant command outcomes. Use timestamps to line these up with the chart.

## Download diagnostics

Use **Download diagnostics** when reporting a bug. The JSON includes current
configuration, relevant Home Assistant states, controller health, decisions,
and recent results. It excludes stored secrets and the full historical database.

Review the file before sharing because room names, entity IDs, device details,
and temperatures may still be private.
