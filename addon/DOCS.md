# Heat Steward

Heat Steward provides one place to configure, operate, and diagnose a heating
system built around SONOFF TRVZB radiator valves. Central-heating and hot-water
relay control are optional: without a heating relay, Heat Steward can still
schedule and control the TRVs.

## Before you start

You need at least one SONOFF TRVZB device available in Home Assistant. For the
best results, give each valve and thermometer a clear Home Assistant device name
and area before opening the add-on.

Optional equipment:

- An external thermometer for any room where the radiator valve is a poor
  representation of room temperature.
- A Home Assistant `switch` entity that operates the central-heating relay.
- A separate `switch` entity that operates hot water.
- A Discord channel webhook for direct alerts.

Heat Steward is experimental. Test relay control and safety limits while someone
is home before relying on it unattended.

## Detailed guides

This page is the practical manual shown inside Home Assistant. More detailed,
plain-language guides are available on GitHub:

- [Documentation index](https://github.com/oliver-johnston/heat-steward/blob/main/docs/README.md)
- [Getting started](https://github.com/oliver-johnston/heat-steward/blob/main/docs/getting-started.md)
- [How heating control works](https://github.com/oliver-johnston/heat-steward/blob/main/docs/concepts/heating-control.md)
- [Schedules and overrides](https://github.com/oliver-johnston/heat-steward/blob/main/docs/concepts/schedules-and-overrides.md)
- [Predictive heating](https://github.com/oliver-johnston/heat-steward/blob/main/docs/features/predictive-heating.md)
- [External temperature sensors](https://github.com/oliver-johnston/heat-steward/blob/main/docs/features/external-temperature-sensors.md)
- [Boiler and hot-water control](https://github.com/oliver-johnston/heat-steward/blob/main/docs/features/boiler-control.md)
- [Safety and fault handling](https://github.com/oliver-johnston/heat-steward/blob/main/docs/features/safety.md)
- [Settings reference](https://github.com/oliver-johnston/heat-steward/blob/main/docs/reference/settings.md)
- [Diagnostics](https://github.com/oliver-johnston/heat-steward/blob/main/docs/reference/diagnostics.md)
- [Troubleshooting](https://github.com/oliver-johnston/heat-steward/blob/main/docs/troubleshooting.md)
- [Practical tuning examples](https://github.com/oliver-johnston/heat-steward/blob/main/docs/tuning-examples.md)
- [Technical references](https://github.com/oliver-johnston/heat-steward/tree/main/docs/technical)

## Initial setup

Open the add-on Web UI. The setup wizard covers Radiators, Boiler, and Schedules
in order, validating saved values against Home Assistant. Control, Preheating,
Presets, Notifications, and the other advanced settings start with safe
defaults and remain available in Settings after the dashboard opens.

### Radiators

Select every valve Heat Steward should control. For each valve:

- Assign a room group. Valves in the same group share a schedule and group-level
  override.
- Optionally choose an external temperature sensor.
- Choose how external-sensor freshness is checked. **Auto** prefers a last-seen
  entity on the same device and otherwise uses state updates.
- Optionally override the global stale timeout for that sensor.

Heat Steward derives the climate entity and supported SONOFF diagnostic and
configuration entities from the selected device. Expand **Technical details** to
inspect that mapping. Correctly configured valves collapse into a concise summary.

### Boiler

Both relays are optional:

- **Central heating switch** controls the boiler or heating-zone relay.
- **Hot water switch** controls hot water independently.
- **Minimum heat requests** is the number of available rooms that must request
  heat before the heating switch is turned on.

Leaving the heating switch unset controls TRVs only. The hot-water schedule is
ignored when no hot-water switch is selected.

### Schedules

Create a weekly schedule for each room group. Every group has a default preset,
plus any number of non-overlapping weekday/time periods. The schedule editor
shows the resulting week visually and reports the next change on the dashboard.

Hot water has a separate on/off schedule with its own default state and periods.
Away mode forces hot water off unless a temporary hot-water override is active.

Saving these schedules completes onboarding and opens the dashboard.

## Additional settings

The remaining settings use the defaults below during onboarding. They can be
adjusted at any time from Settings after the dashboard opens.

### Control

Control settings are split into add-on behaviour and settings enforced on the
TRVs.

Important defaults:

| Setting | Default | Purpose |
| --- | ---: | --- |
| Control cycle | 30 seconds | Re-evaluates schedules, sensors, overrides, and commands. |
| Heating offset | 2°C | Commands a requesting TRV above the room target. |
| Idle offset | 2°C | Commands a satisfied TRV below the room target. |
| Start hysteresis | 0.2°C | Starts demand this far below target; demand stops at target. |
| Minimum boiler on/off | 5 minutes | Prevents short cycling. |
| Maximum boiler runtime | 4 hours | Latches a safety stop if continuously exceeded. |
| Command retries | 2 | Retries commands that Home Assistant does not confirm. |
| External sensor stale time | 90 minutes | Falls back to the TRV sensor after this period. |
| TRV stale time | 60 minutes | Marks a quiet valve as not recently seen. |

Automatic control starts disabled on a new installation. Enable it only after
checking the dashboard and relay mapping. Saving later settings does not disable
control; the controller validates and reconciles the new configuration immediately.

TRVs default to `heat` mode and their internal temperature source. External room
temperatures guide Heat Steward's decision but are not written into the valve.
This preserves useful local TRV behaviour if the add-on stops.

### Preheating

The Preheating tab keeps predictive control in one place. It contains the global
master switch, the maximum preheat time, and a card for each room showing its
warm-up rate, estimated minutes per degree, and evidence count. A room's switch
remains unavailable until at least three recent warm-up episodes have been
gathered with its selected temperature sensor. The dashboard availability banner
links to this tab and can be dismissed permanently in that browser.

### Presets

The default temperatures are:

| Preset | Default |
| --- | ---: |
| Boost | 23°C |
| Comfort | 21°C |
| Eco | 16°C |
| Frost | 5°C |

Temperatures must descend from Boost to Frost. Validation explains any invalid
ordering when the settings are saved.

### Notifications

Heat Steward can post directly to a Discord channel webhook. Direct delivery is
deliberate: it can report a controller problem even when Home Assistant's normal
notification services are unavailable.

- **Critical** sends relay failures, repeated command failures, and safety stops.
- **Warning** also includes unavailable TRVs and external-sensor fallback after
  the configured delay.
- **Send recovery** posts once when a reported problem clears.

The webhook is stored as a secret. It is not returned by the UI API, written to
logs, or included in downloaded diagnostics. Use **Send test** after saving it.

## Daily operation

<p align="center">
  <img src="https://raw.githubusercontent.com/oliver-johnston/heat-steward/main/docs/images/dashboard.png" alt="Heat Steward compact dashboard" width="920">
</p>

The Dashboard defaults to compact mode. Each room shows its measured temperature,
scheduled preset icon, and live state colour. Select a room or plant card to open
the detailed view. Detailed mode shows all cards inline.

<p align="center">
  <img src="https://raw.githubusercontent.com/oliver-johnston/heat-steward/main/docs/images/dashboard-active.png" alt="Heat Steward dashboard with room and floor overrides and mixed heating demand" width="920">
</p>

The toolbar provides:

- **Mode** — switch between Home and Away.
- **View** — switch between Compact and Detailed layouts.
- **Emergency stop** — deliberately disable automatic control and turn the
  heating relay off after confirmation.
- **Refresh** — immediately reload dashboard state.

### Home and Away

Home follows room schedules. Away replaces every scheduled room target with the
configured Away preset, which defaults to Frost, and turns hot water off.

Home Assistant automations can change mode by firing this event:

```yaml
action:
  - event: heat_steward_set_mode
    event_data:
      mode: away
```

Use `mode: home` to resume schedules. Invalid mode values are ignored and logged
as warnings.

Home Assistant automations can also set or clear room, group, whole-house, and
hot-water overrides by firing `heat_steward_override`:

```yaml
action:
  - event: heat_steward_override
    event_data:
      channel: temperature
      scope_type: room
      scope_id: climate.dining_trv
      override_mode: preset
      override_preset: comfort
      duration_minutes: 60
```

Room targets should use the stable climate entity ID. Group targets use the exact
Heat Steward group name; whole-house targets use `scope_type: house` with no
`scope_id`. Use `override_mode: temperature` plus `override_temperature` for a
custom target. Hot water uses `channel: hot_water`, `scope_type: hot_water`,
`override_mode: on_off`, and `on_off: true` or `false`.

All set commands require `duration_minutes` from 1 to 10,080. To cancel one,
identify the same channel and scope, set `operation: clear`, and omit the value
and duration. Use `operation: clear_all` by itself to clear every active
temporary override without changing Home/Away mode. Invalid commands are ignored
and recorded in Recent activity.

### Temporary overrides

The override button is available for an individual room, room group, the whole
house, and hot water. Temperature overrides can use a preset or a custom target.
Available durations are 30 minutes, one hour, two hours, and four hours.

Overrides take precedence over Home/Away and schedules. Cancelling an override
immediately restores the underlying state. Expired overrides are removed on the
next control cycle and survive add-on restarts until then.

## Heating decisions

For each available room, Heat Steward resolves the effective target in this order:

1. The most recently applied override that covers the room.
2. Away preset when Away mode is active.
3. Active weekly schedule period or the group's default preset.

This means a later room, group, or whole-house override replaces an earlier
overlapping override for that room.

The configured external room sensor supplies the measured temperature when it is
healthy. If it is unavailable or stale, the controller raises a warning and falls
back to the TRV's internal temperature.

A room starts requesting heat below `target − hysteresis` and stops at the target.
This asymmetric band reduces overshoot from a radiator that remains hot after its
valve closes. Requesting rooms receive `target + heating offset`; satisfied rooms
receive `target − idle offset`.

Successful schedule-driven history is separated into complete heating episodes
for each room. The reading that causes demand to close is included as the episode
endpoint, so a delayed sensor update is not discarded. The Preheating settings
show the learned warm-up rate while data accumulates, then unlock that room's
Predictive heating checkbox after at least three recent episodes. Once enabled,
the learned warm-up rate can begin heating before a higher scheduled target.
Estimates favour recent evidence, reject implausible responses, never combine
different sensor entities, and expire when no longer recent. Overrides and Away
mode are never predicted. Until evidence is sufficient, or whenever metrics are
unhealthy, normal hysteresis remains active unchanged.

Only available, recently seen TRVs contribute to central-heating demand. When the
number of requests reaches the configured threshold, the heating relay is requested
subject to its minimum on/off time.

## Diagnostics

Diagnostics is the authoritative view when something looks wrong. It includes:

- Overall controller and control-loop health.
- Requested and actual central-heating and hot-water relay state.
- Current issues and temporary overrides.
- Sensor source, freshness, fallback, schedule, target, demand, and TRV command
  for every room.
- Predictive model readiness, learned heating rate, episode count, confidence,
  estimated time to target, and any active preheating action.
- Valve position, battery, and link quality when those SONOFF entities are available.
- The reason each target and command was selected.
- Recent controller actions and Home Assistant command results.
- Historical-metrics health, sample count, retention, and database size.
- Interactive temperature history for one or more rooms. The chart defaults to the
  effective room temperature, scheduled temperature, and periods when that room was
  requesting heat while the central-heating relay was actually on. Optional series
  expose external and TRV sensor readings, effective targets, and TRV commands.

**Download diagnostics** creates a JSON snapshot containing controller state and
only the Home Assistant entities configured for heating. Secrets are excluded.

## Failure and safety behaviour

Heat Steward keeps automatic control enabled through recoverable failures:

- An unavailable or stale TRV is isolated. Other rooms continue to operate and
  the affected room is excluded from boiler demand.
- A stale external thermometer falls back to the internal TRV sensor.
- A relay that remains unavailable for 90 seconds raises a critical issue without
  stopping unrelated room or hot-water control. The short delay prevents restart
  notifications while Home Assistant restores its entities; relay isolation itself
  is immediate.
- Commands are read back for up to five seconds and retried a bounded number of
  times. The failure threshold controls alerting, not whether control is disabled.
- Heating and hot-water relays recover independently when Home Assistant reports
  them available again.
- Unexpected loop failures use bounded recovery. Repeated process crashes are left
  to Home Assistant Supervisor, which monitors the add-on health endpoint.

The maximum continuous boiler runtime is the only automatic hard safety stop. It
turns the heating relay off and pauses relay control until acknowledged. The saved
automatic-control setting remains enabled, allowing normal operation to resume
after acknowledgement.

## Data, restarts, and backups

Configuration is stored as a versioned JSON document under `/data`. Temporary
controls and mode changes use a bounded append-only action ledger that is replayed
on every cycle and startup. Recent activity is also persisted. Writes are atomic;
an unreadable action ledger is not overwritten.

Every completed control cycle is also written to the versioned SQLite database
`/data/heat-steward-metrics.db`. It records system and relay state plus one sample
per room containing temperatures, valve state, schedule, effective target,
override context, heat demand, calculated commands, and command outcomes. Raw
samples are retained for 90 days. Collection failures are shown in Diagnostics
and retried on the next cycle; they never fail or pause heating control.

Home Assistant includes the add-on's `/data` directory in backups. Heat Steward
declares cold-backup mode so its files are captured while the add-on is stopped.
After a restore or restart, schedules, modes, and unexpired overrides are recovered.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| A room is not heating | Open its detailed card, then Diagnostics. Check actual temperature, effective target, demand reason, valve availability, and last seen. |
| External sensor fallback | Confirm the temperature and selected health entity are available. Increase the per-sensor stale time only if its normal report interval requires it. |
| Heating relay stays off | Check the number of heat requests, minimum request threshold, minimum off time, relay entity state, and current issues. |
| Heating relay turns off unexpectedly | Check maximum runtime, anti-cycling, Home Assistant automations, and the recent activity list. |
| TRV target differs from room target | This is expected: Heat Steward applies the heating or idle offset to make the valve open or close decisively. |
| Settings appear unchanged | Use Refresh or reopen the add-on. Unsaved changes can be discarded with Revert on the relevant tab. |
| Discord does not notify | Save the webhook, enable notifications, and use Send test. Rotate the webhook if it has ever been exposed. |

## License

Heat Steward is released under the
[MIT License](https://github.com/oliver-johnston/heat-steward/blob/main/LICENSE).
