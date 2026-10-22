# Practical tuning examples

Change one setting at a time and watch at least several normal heating cycles.
Weather, doors, sunlight, and occupancy can easily make one cycle unusual.

## A room hovers just below target

If demand stops and starts too often around the target, increase hysteresis in
small steps, for example from `0.2°C` to `0.3°C`. A larger value allows a wider
temperature swing before heat starts.

If the valve does not open decisively, increase the heating offset slightly. If
it remains open after the room is satisfied, increase the idle offset slightly.
The offsets change the TRV command, not the displayed room target.

## A room overshoots after the valve closes

First confirm the measured sensor is placed well and reports often enough while
the room is warming. A slow sensor can cross the target between reports, leaving
the controller no opportunity to stop sooner. Reduce hysteresis only if the room
is starting too cold; it does not compensate for delayed above-target readings.

## A room is cold at the start of a Comfort period

Let predictive learning collect enough evidence, then enable that room and the
global switch. Keep **Maximum preheat time** at two hours initially. Increase it
only if the room often needs longer and the learned rate looks believable.

Without predictive heating, move the schedule period earlier manually.

## One small room should not run a large boiler

Raise **Minimum heat requests** from `1` to `2` only if you accept that one cold
room may wait for another room to request heat. This is a system-efficiency
choice, not a comfort improvement.

## The boiler short-cycles

Check whether demand is rapidly changing because of noisy sensors or very small
hysteresis. Then consider increasing the minimum on and off times. Do not make
them so long that rooms regularly overheat or wait uncomfortably.

## A battery sensor reports rarely

For an external thermometer, choose a stale timeout comfortably longer than its
normal reporting interval. Prefer **Auto** freshness first. Do not disable
freshness merely to hide warnings: a frozen reading could then control the room
indefinitely.

## A cautious starting profile

For a new installation, keep the defaults:

- 30-second control cycle;
- `0.2°C` hysteresis;
- `2°C` heating and idle offsets;
- five-minute boiler on/off minimums;
- one minimum heat request;
- predictive heating off until each room has enough evidence.

Test relay mapping and Emergency stop before tuning comfort behavior.
