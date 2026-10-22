# Troubleshooting

Open **Diagnostics** first. It shows the current target, temperature source,
demand reason, relay states, command results, and active issues.

## A room is not heating

1. Check that automatic control is enabled.
2. Check the room's measured temperature, effective target, and demand reason.
3. Check whether the TRV is stale or unavailable.
4. Check the calculated TRV command and its result.
5. If a boiler relay is configured, compare the heat-request count with the
   minimum threshold and check for a minimum-off delay.

Inside the hysteresis band, the previous demand state is intentionally retained.

## The boiler stays off

Check:

- how many available rooms request heat;
- **Minimum heat requests**;
- minimum boiler off time;
- actual relay availability and state;
- a latched maximum-runtime safety stop;
- recent command failures or another Home Assistant automation controlling the
  same switch.

## The boiler stays on or stops unexpectedly

Look for rooms still requesting heat, the minimum-on timer, the maximum-runtime
stop, relay command results, and competing automations.

If behavior is unsafe, use **Emergency stop** and investigate before enabling
control again.

## External sensor fallback

Confirm that the temperature entity is numeric and available. Check the selected
freshness method and health entity. Increase the stale timeout only when the
sensor's normal update interval is genuinely longer.

Fallback to the TRV is expected until the external sensor is healthy again.

## Predictive heating is unavailable

The room needs at least three usable warm-up episodes from the current temperature
sensor. Evidence also requires the configured central-heating relay to be
confirmed on.

Check the Preheating card for counts and last update times. Evidence older than
14 days becomes stale. Changing the selected sensor starts a separate compatible
model. A metrics issue also forces normal hysteresis control.

## Predictive heating starts too early

First allow more normal cycles so recent evidence carries more weight. Then
reduce **Maximum preheat time** to place a firm cap on the action. Check for
unusual samples caused by open windows, manual valve changes, or another heat
source.

## The TRV target differs from the room target

This is normal. Heat Steward adds the heating offset to open a valve and
subtracts the idle offset to close it. See
[Heating control](concepts/heating-control.md).

## Settings appear unchanged

Save the current tab, then use Refresh or reopen the add-on. Revert discards
unsaved changes on that tab. If saving fails, read the validation message; common
causes are overlapping schedule periods or preset temperatures in the wrong
order.

## Discord does not notify

Confirm notifications are enabled, the webhook was saved, and the selected
level includes the issue. Warning notifications wait for the configured delay.
Use **Send test**. Rotate the webhook if it has ever been exposed.

## What to include in a bug report

- Heat Steward and Home Assistant versions.
- A short description of expected and actual behavior.
- The relevant time and room.
- A downloaded diagnostics JSON after reviewing it for private information.
- A screenshot or short extract from the Diagnostics chart when useful.

Report issues at <https://github.com/oliver-johnston/heat-steward/issues>.
