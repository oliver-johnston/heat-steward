<p align="center">
  <img src="addon/logo.png" alt="Heat Steward" width="250">
</p>

# Heat Steward

Heat Steward is a Home Assistant add-on for reliable room-by-room heating and
hot-water control. It is designed specifically for systems built from SONOFF
TRVZB radiator valves, an optional central-heating relay, and an optional
hot-water relay.

It brings discovery, schedules, overrides, control, monitoring, and diagnostics
into one interface while leaving each TRV usable if the add-on or Home Assistant
is unavailable.

> [!IMPORTANT]
> Heat Steward is currently experimental. It can operate heating relays and
> radiator valves, so review the configuration and test it while someone is home.

<p align="center">
  <img src="docs/images/dashboard.png" alt="Heat Steward compact dashboard" width="920">
</p>

The compact dashboard keeps normal scheduled operation easy to scan. Active
overrides and heat demand remain visible without opening individual rooms:

<p align="center">
  <img src="docs/images/dashboard-active.png" alt="Heat Steward dashboard with room and floor overrides and mixed heating demand" width="920">
</p>

## What it does

- Discovers SONOFF TRVZB devices and maps their related Home Assistant entities.
- Groups one or more radiator valves into rooms or heating zones.
- Uses weekly preset schedules for each room group.
- Optionally uses an external room thermometer, with automatic fallback to the
  valve's internal sensor.
- Controls an optional central-heating relay from aggregate room demand.
- Controls an optional hot-water relay from its own weekly schedule.
- Provides Home, Away, room, group, whole-house, and hot-water overrides.
- Explains every control decision in a dedicated Diagnostics view.
- Sends optional fault and recovery notifications directly to Discord.
- Keeps healthy rooms working when one device or sensor becomes unavailable.

## Supported setup

Heat Steward currently targets:

- Home Assistant 2026.1 or newer with Supervisor and add-on support.
- SONOFF TRVZB radiator valves already exposed to Home Assistant.
- Optional `sensor` or `input_number` entities for external room temperature.
- Optional `switch` entities for central heating and hot water.

The add-on is intentionally specialised. Other TRV models may expose different
entities and are not currently supported by automatic discovery.

## Installation

1. In Home Assistant, open **Settings → Add-ons → Add-on Store**.
2. Open the menu in the top-right and choose **Repositories**.
3. Add `https://github.com/oliver-johnston/heat-steward`.
4. Install **Heat Steward** from the new repository.
5. Start the add-on and enable **Show in sidebar**.
6. Open the Web UI and complete the setup wizard.

No YAML configuration is required.

## Initial setup

The wizard covers the three things needed before Heat Steward can show a useful dashboard:

1. **Radiators** — choose valves, assign room groups, and optionally pair room
   sensors and freshness entities.
2. **Boiler** — optionally select heating and hot-water relays and the number of
   room requests needed to run central heating.
3. **Schedules** — create weekly room-group and hot-water periods.

Control, Preheating, Presets, and Notifications use safe defaults during
onboarding and remain available alongside the three setup areas afterward.
Unsaved changes can be reverted per tab.

## How heating control works

Every control cycle, Heat Steward resolves the active schedule, Home/Away mode,
and any temporary overrides. A room starts requesting heat when its measured
temperature falls below the target by the configured hysteresis amount. It stops
requesting heat as soon as it reaches the target.

While heat is required, the TRV is commanded above the room target by the
configured heating offset. Once satisfied, it is commanded below the target by
the idle offset. This lets an external thermometer guide the room while the TRV
continues using its own internal temperature and remains independently usable.

Optional predictive heating learns each room's warm-up rate and the residual rise
after its valve closes from successful scheduled cycles. The Preheating settings
show the estimates and evidence for each room, and only unlock its switch after
enough recent warm-up and coast observations exist. A master switch controls the
feature across all selected rooms. Once enabled, it can start
ahead of a higher scheduled target and close the valve early to coast into that
target. Predictions are time-capped, ignored for overrides and Away mode, and fall
back room-by-room to normal hysteresis whenever the model is sparse, stale, or
unavailable.

The boiler relay turns on only when the configured number of available rooms are
requesting heat. Minimum on/off times prevent short cycling. A maximum continuous
runtime is the sole automatic hard stop and must be acknowledged before relay
control resumes.

## Reliability model

Heat Steward is designed to degrade in parts instead of shutting down the whole
system:

- An unavailable TRV is skipped and does not count toward boiler demand.
- A stale external thermometer falls back to the TRV sensor and raises a warning.
- Relay, TRV, and setting commands are read back and retried.
- Recoverable failures do not disable automatic control.
- Heating and hot-water relays fail independently.
- Home Assistant Supervisor monitors the health endpoint and can restart the add-on.
- Configuration, override actions, event history, and 90 days of raw control-cycle
  metrics are stored under `/data` and included in Home Assistant add-on backups.
- Metrics collection is fail-open: storage problems are reported in Diagnostics but
  cannot interrupt heating control.

## Home Assistant automations

Automations can switch between Home and Away by firing an event:

```yaml
action:
  - event: heat_steward_set_mode
    event_data:
      mode: away
```

Use `mode: home` to resume weekly room schedules. Away applies the configured
Away preset—Frost by default—and turns scheduled hot water off. Boosts remain a
deliberate override.

Automations can also set or clear every override type by firing
`heat_steward_override`. This example sets a one-hour Comfort override on one
room. Use the room's stable climate entity ID; Heat Steward resolves it to the
configured radiator internally.

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

The same event supports room-group and whole-house preset or custom-temperature
overrides, timed hot-water On or Off overrides, and cancellation. Set actions
must include `duration_minutes` (1–10,080); clear actions use `operation: clear`
and omit the value and duration. Use `operation: clear_all` by itself to clear
every active temporary override without changing Home/Away mode. See
[Schedules and overrides](docs/concepts/schedules-and-overrides.md#home-assistant-override-commands)
for complete payloads.

## Development

Run the self-contained local harness:

```sh
make harness
```

It builds the real frontend and starts:

- Heat Steward at <http://127.0.0.1:8099/>
- an imaginary Home Assistant at <http://127.0.0.1:18123/>

The simulator provides eight anonymous SONOFF TRVs, room sensors, heating and
hot-water relays, several failure scenarios, and a rolling 24 hours of realistic
five-minute history for exercising the Diagnostics chart. Local state is stored
in `.dev-data/` and is excluded from the add-on image.

Run checks independently with:

```sh
make test
make soak
make smoke
make lint
npm --prefix addon/frontend run build
```

`make soak` advances through a deterministic seven-day heating simulation,
including schedule transitions, stale sensors, unavailable devices, and a
transient relay failure.

`make smoke` requires Docker. It builds the production image, connects it to the
simulator, completes setup, verifies real TRV and relay commands, replaces the
container, and confirms that configuration and active overrides survive.

GitHub Actions repeats the tests, linting, frontend builds, Home Assistant app
lint, packaged simulator smoke test, and `amd64`/`aarch64` image builds for every
push and pull request. It does not publish development commits.

To publish a release, update all version files and the changelog, wait for CI on
`main`, then push a matching tag such as `v0.27.0`. The release workflow rejects
mismatched versions before publishing signed architecture images, a combined
GHCR image, and the GitHub Release.

## Repository layout

- `addon/` — Home Assistant add-on image, Python controller, and React UI.
- `dev/` — local Home Assistant simulator and development harness.
- `docs/` — user guides, technical references, and screenshots.
- `tests/` — unit, API, reliability, and soak tests.

The production image contains Python and compiled static files, but not Node.js.
Compiled frontend assets are intentionally not committed.

## Documentation

Start with the [documentation index](docs/README.md) for setup, concepts,
features, tuning, diagnostics, troubleshooting, and technical references. The
[add-on manual](addon/DOCS.md) is the shorter practical guide displayed inside
Home Assistant.

## License

Heat Steward is released under the [MIT License](LICENSE).
