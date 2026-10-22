# Settings reference

This page lists the settings that most often need explanation. Defaults are a
safe starting point, not a promise that every heating system should use them.

## Radiators

| Setting | Meaning |
| --- | --- |
| Room group | Radiators with the same group share a schedule and group override. |
| External temperature sensor | Optional room reading used instead of the TRV reading when healthy. |
| External freshness | How Heat Steward decides whether that reading is current. |
| External stale time | Optional room-specific replacement for the global timeout; 5 minutes to 24 hours. |
| TRV freshness | Last-seen, state-update, automatic, or disabled age checking. |

Predictive heating for each room is configured on the separate **Preheating**
tab, after the room has enough evidence.

## Boiler

| Setting | Default | Range or note |
| --- | ---: | --- |
| Central-heating switch | None | Optional Home Assistant `switch`. |
| Hot-water switch | None | Optional and must differ from the heating switch. |
| Minimum heat requests | 1 | At least 1; cannot exceed the number of configured TRVs. |

## Control

| Setting | Default | Allowed range |
| --- | ---: | ---: |
| Automatic control | Off | Enable after checking the setup. |
| Control cycle | 30 seconds | 10–300 seconds |
| Heating offset | 2°C | 0.5–10°C |
| Idle offset | 2°C | 0.5–10°C |
| Start hysteresis | 0.2°C | 0–2°C |
| Away preset | Frost | Any preset |
| Minimum boiler on time | 5 minutes | 0–60 minutes |
| Minimum boiler off time | 5 minutes | 0–60 minutes |
| Maximum boiler runtime | 4 hours | 5 minutes–6 hours |
| Command retries | 2 | 0–5 |
| Failures before alert | 3 | 1–10 |
| External sensor stale time | 90 minutes | 5 minutes–24 hours |
| TRV stale time | 60 minutes | 5 minutes–24 hours |

Heat Steward also applies the chosen heat mode, internal TRV temperature source,
smart-temperature control, open-window detection, child lock, and frost
protection temperature to supported SONOFF valves.

## Preheating

| Setting | Default | Allowed range |
| --- | ---: | ---: |
| Global predictive heating | Off | Master switch for all rooms. |
| Maximum preheat time | 2 hours | 15 minutes–4 hours |
| Room predictive heating | Off | Available after 3 warm-up episodes. |

The maximum is a safety and comfort limit; the learner can choose a shorter time.
See [Predictive heating](../features/predictive-heating.md).

## Presets

| Preset | Default |
| --- | ---: |
| Boost | 23°C |
| Comfort | 21°C |
| Eco | 16°C |
| Frost | 5°C |

Each value must be between `5°C` and `30°C`, and they must strictly descend from
Boost to Frost.

## Notifications

| Setting | Default | Meaning |
| --- | ---: | --- |
| Enabled | Off | Requires an HTTPS Discord webhook. |
| Level | Critical | Critical only, or warnings and critical issues. |
| Send recovery | On | Send one message when a reported issue clears. |
| Warning delay | 15 minutes | 1 minute–24 hours; prevents brief warnings from notifying. |

The webhook is treated as a secret and omitted from API responses, logs, and
downloaded diagnostics.
