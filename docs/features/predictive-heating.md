# Predictive heating

Predictive heating learns how each room behaves and can start before a scheduled
temperature increase.

It is optional, off by default, and enabled separately for each room.

## What Heat Steward learns

For each radiator, Heat Steward estimates:

- **Warm-up rate** — how many degrees per hour the room gains while it requests
  heat and the boiler is actually on.
- **Minutes per degree** — the same rate expressed as an easier planning value.
- **Evidence** — the number of usable warm-up episodes.

These values appear on **Settings → Preheating**. Heat Steward needs at least
three recent warm-up episodes before the room's switch becomes available.

## Enable it

1. Let Heat Steward run normally while it gathers evidence.
2. Open **Settings → Preheating** and review the learned values.
3. Enable predictive heating for the rooms you want to use.
4. Enable the global predictive-heating switch.

The dashboard may show an availability banner when one or more rooms have enough
evidence. Its link opens Preheating settings. Dismissing the banner hides it
permanently in that browser.

## How preheating works

Before the next higher scheduled target, Heat Steward estimates:

`time needed = temperature increase ÷ learned warm-up rate`

It adds five minutes for valve travel and control-cycle timing, then starts
preheating when the schedule change falls inside that lead time. The result is
capped by **Maximum preheat time**.

Example: a room is `18°C`, the next target is `21°C`, and the learned rate is
`1.5°C/hour`. The raw estimate is two hours. Heat Steward adds five minutes, but
a two-hour maximum caps the lead time at two hours.

## When it falls back

Normal hysteresis control remains active when:

- the global or room switch is off;
- the estimate needed for that action is still learning or stale;
- the selected temperature sensor has changed;
- metrics cannot be read;
- the room is unavailable;
- an override or Away mode supplies the target.

Preheating needs a fresh warm-up estimate. When it becomes stale, Heat Steward
falls back to the normal schedule and hysteresis calculation.

Changing from the TRV sensor to an external sensor, or changing the external
sensor entity, starts a new compatible model. Old readings from another sensor
are never joined to the new readings.

For the exact sample filtering and weighting, see the
[predictive-heating algorithm](../technical/predictive-heating-algorithm.md).
