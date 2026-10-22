# Control cycle

Automatic control runs repeatedly at the configured interval, 30 seconds by
default. One cycle follows this sequence.

1. Load and validate saved configuration.
2. Read relevant entity states from Home Assistant.
3. Replay saved mode and override actions, ignoring expired effects.
4. Refresh predictive models when enabled and due.
5. Build one control plan for rooms, central heating, and hot water.
6. Apply the plan with safety-aware relay ordering, TRV reconciliation,
   read-back, and bounded retries.
7. Update dashboard state, issues, notifications, and recent activity.
8. Record the completed cycle and room samples in SQLite.

## Per-room planning

For every configured radiator with a schedule, the planner:

1. resolves the schedule and next schedule change;
2. applies the latest relevant override or Away mode;
3. selects a healthy external temperature or falls back to the TRV;
4. checks TRV freshness and availability;
5. applies compatible predictive preheating when eligible;
6. calculates demand with hysteresis;
7. calculates the TRV setpoint from the effective target and offset.

Unavailable rooms have demand forced off and are excluded from the boiler count.

## Relay planning

The central-heating request is true when the count of available rooms requesting
heat reaches the configured threshold. The executor then applies the minimum on
or off time. Hot water uses its own override, Away behavior, and schedule.

Requested and actual relay state are kept separate in Diagnostics so a delay or
failure is visible.

## Failure behavior

A failed command or metrics write is recorded without discarding the already
calculated plan. Repeated command or loop failures create an issue and alert;
they do not turn the saved control switch off. A maximum-runtime safety stop is
the exception and remains latched until acknowledged.
