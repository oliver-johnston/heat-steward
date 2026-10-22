import type {
  ControlNumberKey,
  ControlSettings,
  ControlValues,
  Discovery,
  HotWaterSchedule,
  HotWaterState,
  NotificationSettings,
  NotificationValues,
  PredictiveControlNumberKey,
  PresetKey,
  PresetValues,
  SavedConfig,
  Selection,
  Weekday,
} from "./types";

export const DEFAULT_PRESETS: PresetValues = {
  boost: "23",
  comfort: "21",
  eco: "16",
  frost: "5",
};

export const DEFAULT_HOT_WATER_SCHEDULE: HotWaterSchedule = {
  default_state: "off",
  periods: [],
};
export const DEFAULT_CONTROL: ControlValues = {
  enabled: false,
  away_preset: "frost",
  cycle_interval_seconds: "30",
  heating_offset: "2",
  idle_offset: "2",
  hysteresis: "0.2",
  smart_temperature_control: false,
  open_window_detection: false,
  child_lock: false,
  frost_protection_temperature: "5",
  minimum_boiler_on_minutes: "5",
  minimum_boiler_off_minutes: "5",
  maximum_boiler_on_minutes: "240",
  command_retries: "2",
  failures_before_alert: "3",
  external_sensor_stale_minutes: "90",
  trv_stale_minutes: "60",
  predictive_heating_enabled: false,
  maximum_preheat_minutes: "120",
};
export const ADDON_CONTROL_FIELDS: Array<{
  key: Exclude<ControlNumberKey, "frost_protection_temperature">;
  title: string;
  description: string;
  inputLabel: string;
  min: string;
  max: string;
  step: string;
}> = [
  {
    key: "cycle_interval_seconds",
    title: "Control cycle",
    description: "How often the add-on reads Home Assistant and reconciles heating state.",
    inputLabel: "Seconds",
    min: "10",
    max: "300",
    step: "1",
  },
  {
    key: "hysteresis",
    title: "Start heating below target",
    description: "Start this far below the target; stop as soon as the target is reached.",
    inputLabel: "Degrees °C",
    min: "0",
    max: "2",
    step: "0.1",
  },
  {
    key: "heating_offset",
    title: "Heating offset",
    description: "How far above the effective target to command a TRV while heat is needed.",
    inputLabel: "Degrees °C",
    min: "0.5",
    max: "10",
    step: "0.5",
  },
  {
    key: "idle_offset",
    title: "Idle offset",
    description: "How far below the effective target to command a satisfied TRV.",
    inputLabel: "Degrees °C",
    min: "0.5",
    max: "10",
    step: "0.5",
  },
  {
    key: "minimum_boiler_on_minutes",
    title: "Minimum boiler on time",
    description: "Keep the heating relay on for at least this long to prevent short cycling.",
    inputLabel: "Minutes",
    min: "0",
    max: "60",
    step: "0.5",
  },
  {
    key: "minimum_boiler_off_minutes",
    title: "Minimum boiler off time",
    description: "Wait this long after switch-off before starting the boiler again.",
    inputLabel: "Minutes",
    min: "0",
    max: "60",
    step: "0.5",
  },
  {
    key: "maximum_boiler_on_minutes",
    title: "Maximum continuous boiler run",
    description: "Raise a safety stop if the boiler remains requested for longer than this.",
    inputLabel: "Minutes",
    min: "5",
    max: "360",
    step: "5",
  },
  {
    key: "command_retries",
    title: "Command retries",
    description: "How many times to retry a Home Assistant command that is not confirmed.",
    inputLabel: "Retries",
    min: "0",
    max: "5",
    step: "1",
  },
  {
    key: "failures_before_alert",
    title: "Failed cycles before alert",
    description: "Consecutive failed cycles allowed before reporting a control problem.",
    inputLabel: "Cycles",
    min: "1",
    max: "10",
    step: "1",
  },
  {
    key: "external_sensor_stale_minutes",
    title: "External sensor stale time",
    description: "Fall back to the TRV sensor when the external sensor is older than this.",
    inputLabel: "Minutes",
    min: "5",
    max: "1440",
    step: "5",
  },
  {
    key: "trv_stale_minutes",
    title: "TRV stale time",
    description: "Skip a TRV and exclude it from heat demand after it stops reporting for this long.",
    inputLabel: "Minutes",
    min: "5",
    max: "1440",
    step: "5",
  },
];

export const PREHEATING_CONTROL_FIELDS: Array<{
  key: PredictiveControlNumberKey;
  title: string;
  description: string;
  inputLabel: string;
  min: string;
  max: string;
  step: string;
}> = [
  {
    key: "maximum_preheat_minutes",
    title: "Maximum preheat time",
    description: "Limit how early learning-based control may begin warming a room.",
    inputLabel: "Minutes",
    min: "15",
    max: "240",
    step: "5",
  },
];
export const DEFAULT_NOTIFICATIONS: NotificationValues = {
  enabled: false,
  level: "critical",
  send_recovery: true,
  warning_delay_minutes: "15",
};
export const CONFIGURED_WEBHOOK_VALUE = "Webhook configured";

export function notificationValues(settings: NotificationSettings | undefined): NotificationValues {
  if (!settings) return DEFAULT_NOTIFICATIONS;
  return {
    enabled: settings.enabled,
    level: settings.level,
    send_recovery: settings.send_recovery,
    warning_delay_minutes: String(settings.warning_delay_seconds / 60),
  };
}

export function selectionValues(found: Discovery, existing: SavedConfig | null) {
  const existingByDevice = new Map(existing?.trvs.map((trv) => [trv.device_id, trv]));
  const selections: Record<string, Selection> = {};
  for (const trv of found.trvs) {
    const configured = existingByDevice.get(trv.device_id);
    const externalTemperatureSensor =
      configured?.external_temperature_sensor ?? trv.suggested_temperature_sensor ?? "";
    const suggestedExternalLastSeen = found.temperature_sensors.find(
      (sensor) => sensor.entity_id === externalTemperatureSensor,
    )?.suggested_last_seen_entity;
    const externalLastSeen =
      configured?.external_temperature_last_seen ?? suggestedExternalLastSeen ?? "";
    const configuredExternalFreshness = configured?.external_temperature_freshness ?? "auto";
    selections[trv.device_id] = {
      enabled: configured ? true : existing === null,
      room_group: configured?.room_group ?? trv.area_name ?? trv.suggested_group,
      external_temperature_sensor: externalTemperatureSensor,
      external_temperature_last_seen: externalLastSeen,
      external_temperature_freshness:
        configuredExternalFreshness === "auto"
          ? externalLastSeen
            ? "last_seen"
            : "state_updates"
          : configuredExternalFreshness,
      external_sensor_stale_minutes: configured?.external_sensor_stale_seconds
        ? String(configured.external_sensor_stale_seconds / 60)
        : "",
      trv_last_seen: configured?.entities?.last_seen ?? trv.entities.last_seen ?? "",
      trv_freshness:
        configured?.trv_freshness === "auto"
          ? trv.entities.last_seen
            ? "last_seen"
            : "state_updates"
          : configured?.trv_freshness ?? (trv.entities.last_seen ? "last_seen" : "state_updates"),
      predictive_heating_enabled: configured?.predictive_heating_enabled ?? false,
    };
  }
  return selections;
}

export function controlValues(settings: ControlSettings | undefined): ControlValues {
  if (!settings) return DEFAULT_CONTROL;
  return {
    enabled: settings.enabled,
    away_preset: settings.away_preset ?? "frost",
    cycle_interval_seconds: String(settings.cycle_interval_seconds),
    heating_offset: String(settings.heating_offset),
    idle_offset: String(settings.idle_offset),
    hysteresis: String(settings.hysteresis),
    smart_temperature_control: settings.smart_temperature_control,
    open_window_detection: settings.open_window_detection,
    child_lock: settings.child_lock,
    frost_protection_temperature: String(settings.frost_protection_temperature),
    minimum_boiler_on_minutes: String(settings.minimum_boiler_on_seconds / 60),
    minimum_boiler_off_minutes: String(settings.minimum_boiler_off_seconds / 60),
    maximum_boiler_on_minutes: String((settings.maximum_boiler_on_seconds ?? 14400) / 60),
    command_retries: String(settings.command_retries ?? 2),
    failures_before_alert: String(settings.failures_before_alert ?? 3),
    external_sensor_stale_minutes: String((settings.external_sensor_stale_seconds ?? 5400) / 60),
    trv_stale_minutes: String((settings.trv_stale_seconds ?? 3600) / 60),
    predictive_heating_enabled: settings.predictive_heating_enabled ?? false,
    maximum_preheat_minutes: String((settings.maximum_preheat_seconds ?? 7200) / 60),
  };
}

export function controlPayload(settings: ControlValues) {
  return {
    enabled: settings.enabled,
    cycle_interval_seconds: Number(settings.cycle_interval_seconds),
    heating_offset: Number(settings.heating_offset),
    idle_offset: Number(settings.idle_offset),
    hysteresis: Number(settings.hysteresis),
    trv_mode: "heat",
    temperature_source: "internal",
    away_preset: settings.away_preset,
    smart_temperature_control: settings.smart_temperature_control,
    open_window_detection: settings.open_window_detection,
    child_lock: settings.child_lock,
    frost_protection_temperature: Number(settings.frost_protection_temperature),
    minimum_boiler_on_seconds: Number(settings.minimum_boiler_on_minutes) * 60,
    minimum_boiler_off_seconds: Number(settings.minimum_boiler_off_minutes) * 60,
    maximum_boiler_on_seconds: Number(settings.maximum_boiler_on_minutes) * 60,
    command_retries: Number(settings.command_retries),
    failures_before_alert: Number(settings.failures_before_alert),
    external_sensor_stale_seconds: Number(settings.external_sensor_stale_minutes) * 60,
    trv_stale_seconds: Number(settings.trv_stale_minutes) * 60,
    predictive_heating_enabled: settings.predictive_heating_enabled,
    maximum_preheat_seconds: Number(settings.maximum_preheat_minutes) * 60,
  };
}

export function validateControlValues(settings: ControlValues) {
  for (const field of [...ADDON_CONTROL_FIELDS, ...PREHEATING_CONTROL_FIELDS]) {
    const raw = settings[field.key].trim();
    const value = Number(raw);
    if (!raw || !Number.isFinite(value)) return `${field.title} must be a number.`;
    if (value < Number(field.min) || value > Number(field.max)) {
      return `${field.title} must be between ${field.min} and ${field.max}.`;
    }
  }
  const frost = settings.frost_protection_temperature.trim();
  const frostValue = Number(frost);
  if (!frost || !Number.isFinite(frostValue)) {
    return "Frost protection temperature must be a number.";
  }
  if (frostValue < 4 || frostValue > 35) {
    return "Frost protection temperature must be between 4°C and 35°C.";
  }
  return "";
}
export const HOT_WATER_SCHEDULE_KEY = "__hot_water__";

export const PRESET_DEFINITIONS: Array<{ key: PresetKey; name: string; description: string }> = [
  { key: "boost", name: "Boost", description: "Quickly warm a room above its normal target." },
  { key: "comfort", name: "Comfort", description: "The normal temperature for an occupied room." },
  { key: "eco", name: "Eco", description: "Save energy while keeping the room gently heated." },
  { key: "frost", name: "Frost", description: "Protect the room and pipework from freezing." },
];
export const HOT_WATER_DEFINITIONS: Array<{ key: HotWaterState; name: string }> = [
  { key: "on", name: "On" },
  { key: "off", name: "Off" },
];

export const WEEKDAYS: Array<{ key: Weekday; short: string; name: string }> = [
  { key: "monday", short: "Mon", name: "Monday" },
  { key: "tuesday", short: "Tue", name: "Tuesday" },
  { key: "wednesday", short: "Wed", name: "Wednesday" },
  { key: "thursday", short: "Thu", name: "Thursday" },
  { key: "friday", short: "Fri", name: "Friday" },
  { key: "saturday", short: "Sat", name: "Saturday" },
  { key: "sunday", short: "Sun", name: "Sunday" },
];
