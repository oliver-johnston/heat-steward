# Changelog

## 0.40.0 — 2026-10-22

- Removed residual-rise learning and predictive early shutoff while retaining
  warm-up-based preheating.
- Include the demand-closing temperature reading in the preceding warm-up episode
  so delayed sensor updates contribute to the learned heating rate.

## 0.39.0 — 2026-10-18

- Added the `heat_steward_override` Home Assistant event for timed room,
  room-group, whole-house, and hot-water overrides, including cancellation and
  clearing all active overrides, plus stable climate-entity targeting for
  individual rooms.

## 0.38.0 — 2026-10-18

- Clarified room readiness on the Preheating settings page with a prominent
  red-accented `Disabled | Learning` badge and a compact grid of non-duplicated
  learning evidence and estimates.

## 0.37.0 — 2026-10-17

- Wait 90 seconds before notifying that a relay is unavailable, preventing false
  alert-and-recovery pairs while Home Assistant restores entities after a restart.
  Relay isolation remains immediate, as do genuine safety-stop notifications.

## 0.36.0 — 2026-10-17

- Added a focused documentation library covering setup, control concepts,
  predictive heating, sensors, relays, safety, tuning, diagnostics, stored data,
  troubleshooting, and technical behavior.
- Linked the Home Assistant add-on manual to the detailed guides and changed its
  screenshots to explicit GitHub URLs so they render correctly inside Home
  Assistant.

## 0.35.0 — 2026-10-17

- Added optional learning-based predictive heating. Heat Steward learns a robust
  per-room warm-up rate and residual temperature rise from successful scheduled
  heating episodes, preheats for higher upcoming targets, and closes valves early
  when stored heat is expected to finish the warm-up.
- Added conservative evidence, recency, sensor-consistency, and configurable time
  caps. Rooms automatically retain standard hysteresis while a model is learning,
  stale, incompatible, or unavailable.
- Added room-level predictive model status, episode counts, learned estimates,
  confidence, and active preheating/coasting decisions to Diagnostics.
- Added a dedicated Preheating settings tab with a global master switch, timing
  caps, and per-room learned details and switches. Room switches remain unavailable
  until both models have enough evidence, with the same rule enforced by the API.
- Added a dismissible dashboard availability banner linking directly to the
  Preheating settings tab.

## 0.34.0 — 2026-10-16

- Made compact room temperatures consistently follow the configured control
  bands: readings normally round to the nearest target-relative hysteresis step,
  while readings inside the satisfied range display as the target temperature.
- Kept exact sensor readings unchanged in room details and diagnostics.

## 0.33.0 — 2026-10-16

- Added a clearer **Within range** room status when the temperature is below
  target but has not crossed the configured heating start threshold.
- Simplified compact dashboard temperatures to match their target-relative
  control bands while retaining exact readings in room details and diagnostics.
- Fixed detail dialogs so they remain centred and their floating close buttons
  stay fully visible at mobile and desktop widths.

## 0.32.0 — 2026-10-13

- Added a user-selectable iOS-inspired dark theme with glass surfaces, vivid
  status colours, and dark-mode styling across the dashboard, settings, and
  diagnostics history chart.
- Added a dedicated Theme settings tab; appearance changes apply immediately
  and are remembered separately on each browser or device.

## 0.31.0 — 2026-10-12

- Refactored the frontend application, heating controller, override handling,
  event history, and HTTP routes into smaller focused components for easier
  maintenance and safer future changes, without changing heating behaviour.

## 0.30.0 — 2026-10-12

- Added interactive Diagnostics temperature history charts with selectable rooms,
  time ranges, effective and sensor temperatures, scheduled and overridden targets,
  TRV commands, compact multi-select filters, and genuine heating periods when both
  room demand and the boiler relay were on.
- Added a deterministic 24-hour history profile to the local simulator for testing
  schedules, warm-up, cooling, overshoot, room boosts, and boiler activity.

## 0.29.0 — 2026-10-12

- Added a versioned SQLite history of every completed control cycle, including room temperatures, valve state, schedules, effective targets, overrides, commands, relay state, and command outcomes.
- Added 90-day raw-history retention and fail-open collection so metrics storage problems can never interrupt heating control.
- Added metrics collection health, sample counts, database size, valve position, battery, and link-quality information to Diagnostics.

## 0.28.0 — 2026-10-11

- Simplified the branding to the cutout shield mark throughout the add-on and documentation.
- Moved the main navigation to a fixed bottom bar and combined the compact logo, system status, and responsive page controls into one top toolbar.
- Refreshed the dashboard documentation screenshots at a more compact viewport size.

## 0.27.0 — 2026-10-11

- Added an Add-on Store introduction with supported setup, beta guidance, and links to the full documentation.
- Added automated testing and release checks, including a simulator smoke test that verifies control and restart recovery.
- Switched releases to tested, signed, prebuilt images for both supported architectures.

## 0.26.0 — 2026-10-11

- Streamlined initial setup to the required Radiators, Boiler, and Schedules steps, leaving Control, Presets, and Notifications for later adjustment.
- Grouped Boiler settings into clearer Central heating and Hot water sections.
- Renamed and rebranded the add-on as Heat Steward with new SVG and Home Assistant artwork.
- Added dashboard screenshots covering normal schedules, active overrides, and mixed room demand.
- Reworked the project README around installation, supported hardware, everyday use, and the reliability model.
- Expanded the add-on guide with control defaults, setup guidance, safety behaviour, backups, automations, and troubleshooting.

## 0.25.0 — 2026-10-11

- Standardised the Radiators, Boiler, Control, Presets, and Notifications settings into a clearer one-setting-per-row layout with concise descriptions.
- Replaced wizard-style Back buttons with per-tab Revert actions after onboarding, restoring unsaved changes to the last saved configuration.
- Clarified that omitting the central-heating relay still allows the add-on to control TRVs.
- Displayed boiler anti-cycling and sensor freshness durations in minutes while preserving their existing stored values.
- Improved configured Discord webhook handling so the secret stays hidden without a misleading empty field.
- Made the main navigation sticky, improved narrow-screen wrapping, and fixed mobile override controls overlapping dashboard card content.
- Renamed Devices to Radiators, moved Rescan devices beside the selection summary, and reduced visual clutter across settings pages.
- Changed the default Away preset from Eco to Frost.

## 0.24.0 — 2026-10-11

- Changed heating hysteresis to start below the target and stop as soon as the target is reached, reducing temperature overshoot from hot radiators.
- Separated add-on control options from settings enforced directly on the TRVs.
- Added concise descriptions and a clearer one-setting-per-row layout to the Control page.
- Removed misleading required-field markers while retaining explicit validation, and simplified the save confirmation.

## 0.23.0 — 2026-10-11

- Added compact room, central-heating, and hot-water cards with detailed pop-up views.
- Added temporary preset and temperature overrides for rooms, groups, and the whole house.
- Added a persistent, replayable override action ledger, including actor and source metadata.
- Unified room and plant detail cards and moved dashboard controls into responsive toolbar menus.
- Expanded Diagnostics into the authoritative troubleshooting view, with active overrides, applied override scope, complete sensor and TRV state, schedule context, and Target → Demand → Command explanations.
- Improved narrow-screen wrapping and restored status-coloured borders to compact dashboard cards.

## 0.22.0 — 2026-10-10

- Added timed hot-water boost controls and dashboard status.

## 0.21.0 — 2026-10-10

- Made Away mode turn hot water off as well as applying the configured heating preset.

## 0.20.0 — 2026-10-10

- Isolated unavailable TRVs so healthy rooms continue operating and unavailable rooms do not count toward boiler demand.
- Kept automatic control enabled through recoverable device and relay failures.

## 0.19.0 — 2026-10-10

- Improved TRV freshness detection and command confirmation.
- Added configurable external-sensor health sources and stale thresholds.
- Added external-sensor fallback warnings, last-seen details, and next-schedule information.

## 0.18.0 — 2026-10-10

- Added Home and Away modes for dashboard and Home Assistant automation control.
- Added timed room-group and whole-house Boost overrides.

## 0.17.0 — 2026-10-10

- Preserved automatic-control state when saving unrelated settings.

## 0.16.0 — 2026-10-10

- Added direct Discord webhook notifications with delayed, deduplicated issue and recovery alerts.

## 0.15.0 — 2026-10-10

- Added hot-water status and next schedule change to the dashboard.

## 0.14.0 — 2026-10-10

- Collapsed correctly configured device cards into concise summaries with an Edit action.

## 0.13.0 — 2026-10-10

- Improved external-temperature freshness handling and fallback visibility.
- Added sensor health and next-schedule information to dashboard room details.

## 0.12.0 — 2026-10-10

- Added control-loop recovery, command retries, anti-cycling protection, maximum boiler runtime protection, and persistent fault reporting.
- Added automated reliability and soak-test coverage.

## 0.11.0 — 2026-10-10

- Enabled live automatic TRV and boiler control from room schedules.
- Added hysteresis and configurable heating and idle TRV offsets.
- Reconciled required SONOFF TRV settings on every control cycle without syncing external temperatures into the valve.

## 0.10.0 — 2026-10-10

- Added the Diagnostics page with controller, relay, room, issue, and recent-activity information.
- Surfaced active issues on the dashboard.

## 0.9.0 — 2026-10-10

- Added a prominent monitoring-only warning linked directly to Control settings.
- Replaced control checkboxes with Material switches.

## 0.8.0 — 2026-10-10

- Added the observable 30-second heating-control loop and monitoring-only mode.
- Added effective schedule targets, TRV commands, demand state, and room status colouring.

## 0.7.0 — 2026-10-10

- Added weekday/time hot-water on/off scheduling.

## 0.6.0 — 2026-10-10

- Simplified onboarding save actions and removed nonessential explanatory text.

## 0.5.0 — 2026-10-10

- Added optional boiler configuration for central-heating and hot-water switches.
- Added the configurable minimum number of room heat requests required to start the boiler.

## 0.4.0 — 2026-10-10

- Added room-group weekday/time schedule editing and visualisation.
- Added the first configured-system dashboard and Dashboard/Settings navigation.

## 0.3.0 — 2026-10-10

- Added configurable Boost, Comfort, Eco, and Frost presets.
- Added the multi-step onboarding structure for Devices, Boiler, Control, Presets, and Schedules.

## 0.2.0 — 2026-10-10

- Improved Home Assistant entity labels with device and room context.
- Added save validation feedback at both the top and bottom of settings forms.
- Removed household-specific names from the local simulator.

## 0.1.0 — 2026-10-10

- Added SONOFF TRV discovery and device onboarding.
- Added optional external room sensors, room grouping, boiler and hot-water switch selection, and configuration validation.
- Added the standalone local Home Assistant simulator and test harness.
- Adopted the official Material Web component library for the add-on and simulator UI.

## 0.0.0

- Pre-release project baseline; no packaged Home Assistant add-on was published at this version.
