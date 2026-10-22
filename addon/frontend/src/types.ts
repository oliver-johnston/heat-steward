import type {
  ActiveHotWaterOverride,
  ActiveTemperatureOverride,
} from "./OverrideDialog";

export type EntityMap = Record<string, string | null>;

export type DiscoveredTRV = {
  device_id: string;
  name: string;
  manufacturer: string | null;
  model: string | null;
  software_version: string | null;
  area_name: string | null;
  suggested_group: string;
  suggested_temperature_sensor: string | null;
  entities: EntityMap;
  missing_recommended: string[];
};

export type EntityOption = {
  entity_id: string;
  name: string;
  entity_name: string;
  area_name: string | null;
  device_name: string | null;
  state: string | null;
  unit?: string | null;
  suggested_last_seen_entity?: string | null;
};

export type Discovery = {
  trvs: DiscoveredTRV[];
  temperature_sensors: EntityOption[];
  health_sensors: EntityOption[];
  switches: EntityOption[];
  suggested: {
    boiler_switch: string | null;
    hot_water_switch: string | null;
  };
  predictive_models: Record<string, PredictiveModel>;
  predictive_models_error: string | null;
};

export type PredictiveModel = {
  device_id: string;
  temperature_source: "external" | "trv" | null;
  temperature_entity: string | null;
  status: "learning" | "ready" | "stale";
  ready: boolean;
  heating_ready: boolean;
  heating_rate_c_per_hour: number | null;
  heating_samples: number;
  heating_updated_at: string | null;
  confidence: number;
};

export type FreshnessMode = "auto" | "last_seen" | "state_updates" | "disabled";

export type SavedTRV = {
  device_id: string;
  room_group: string;
  external_temperature_sensor: string | null;
  external_temperature_last_seen: string | null;
  external_temperature_freshness: FreshnessMode;
  external_sensor_stale_seconds: number | null;
  trv_freshness: FreshnessMode;
  predictive_heating_enabled: boolean;
  entities: EntityMap;
};

export type TemperaturePresets = {
  boost: number;
  comfort: number;
  eco: number;
  frost: number;
};

export type PresetKey = keyof TemperaturePresets;
export type Weekday =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday";

export type SchedulePeriod = {
  days: Weekday[];
  start: string;
  end: string;
  preset: PresetKey;
};

export type RoomSchedule = {
  room_group: string;
  default_preset: PresetKey;
  periods: SchedulePeriod[];
};

export type HotWaterState = "on" | "off";

export type HotWaterPeriod = {
  days: Weekday[];
  start: string;
  end: string;
  state: HotWaterState;
};

export type HotWaterSchedule = {
  default_state: HotWaterState;
  periods: HotWaterPeriod[];
};

export type SetupProgress = {
  devices: boolean;
  boiler: boolean;
  control: boolean;
  presets: boolean;
  schedules: boolean;
};

export type ControlSettings = {
  enabled: boolean;
  cycle_interval_seconds: number;
  heating_offset: number;
  idle_offset: number;
  hysteresis: number;
  trv_mode: "heat";
  temperature_source: "internal";
  away_preset: PresetKey;
  smart_temperature_control: boolean;
  open_window_detection: boolean;
  child_lock: boolean;
  frost_protection_temperature: number;
  minimum_boiler_on_seconds: number;
  minimum_boiler_off_seconds: number;
  maximum_boiler_on_seconds: number;
  command_retries: number;
  failures_before_alert: number;
  external_sensor_stale_seconds: number;
  trv_stale_seconds: number;
  predictive_heating_enabled: boolean;
  maximum_preheat_seconds: number;
};

export type SafetyState = {
  fault_latched: boolean;
  fault_reason: string | null;
  fault_at: string | null;
};

export type NotificationSettings = {
  enabled: boolean;
  level: "critical" | "warning";
  send_recovery: boolean;
  warning_delay_seconds: number;
  discord_webhook_configured: boolean;
};

export type NotificationValues = {
  enabled: boolean;
  level: "critical" | "warning";
  send_recovery: boolean;
  warning_delay_minutes: string;
};

export type SavedConfig = {
  boiler_switch: string | null;
  hot_water_switch: string | null;
  minimum_heat_requests: number;
  control: ControlSettings;
  safety: SafetyState;
  notifications: NotificationSettings;
  trvs: SavedTRV[];
  presets: TemperaturePresets;
  setup: SetupProgress;
  schedules: RoomSchedule[];
  hot_water_schedule: HotWaterSchedule;
  updated_at: string;
};

export type DashboardRoom = {
  device_id: string;
  room_group: string;
  name: string;
  trv_name: string;
  climate_entity: string;
  current_temperature_entity: string;
  current_temperature: number | null;
  external_temperature: number | null;
  trv_temperature: number | null;
  target_temperature: number | null;
  scheduled_temperature: number;
  commanded_temperature: number;
  preset: PresetKey;
  demand: boolean;
  status:
    | "heating"
    | "waiting"
    | "opening"
    | "closing"
    | "within_range"
    | "idle"
    | "stale"
    | "unavailable";
  hvac_mode: string | null;
  hvac_action: string | null;
  warning: string | null;
  external_temperature_freshness: FreshnessMode | null;
  external_temperature_health_entity: string | null;
  external_temperature_last_seen_at: string | null;
  external_temperature_age_seconds: number | null;
  external_temperature_stale_after_seconds: number | null;
  trv_last_seen_at: string | null;
  next_schedule_at: string | null;
  next_preset: PresetKey | null;
  next_scheduled_temperature: number | null;
  unit: string;
  available: boolean;
  control_source: "schedule" | "away" | "boost" | "override" | "preheat";
  override_until: string | null;
  override_mode: "preset" | "temperature" | null;
  override_scope_type: "house" | "group" | "room" | null;
  override_scope_id: string | null;
  predictive_heating_enabled: boolean;
  predictive_heating_available: boolean;
};

export type ActiveBoost = {
  room_group: string | null;
  started_at: string;
  expires_at: string;
};

export type ActiveHotWaterBoost = {
  started_at: string;
  expires_at: string;
};

export type DiagnosticIssue = {
  severity: "warning" | "error";
  title: string;
  detail: string;
};

export type DashboardData = {
  updated_at: string;
  controller: {
    enabled: boolean;
    last_error: string | null;
    heat_requests: number;
    minimum_heat_requests: number;
    boiler_should_be_on: boolean;
    boiler_is_on: boolean | null;
    hot_water_should_be_on: boolean | null;
    hot_water_is_on: boolean | null;
    hot_water_next_change_at: string | null;
    hot_water_next_state: "on" | "off" | null;
    fault_latched: boolean;
    fault_reason: string | null;
  };
  issues: DiagnosticIssue[];
  overrides: {
    mode: "home" | "away";
    away_preset: PresetKey;
    boosts: ActiveBoost[];
    hot_water_boost: ActiveHotWaterBoost | null;
    temperature_overrides?: ActiveTemperatureOverride[];
    hot_water_override?: ActiveHotWaterOverride | null;
  };
  groups: Array<{ name: string; rooms: DashboardRoom[] }>;
};

export type DiagnosticRoom = {
  device_id: string;
  name: string;
  room_group: string;
  climate_entity: string;
  available: boolean;
  status: DashboardRoom["status"];
  current_temperature: number | null;
  temperature_source: "external" | "TRV";
  temperature_entity: string;
  external_temperature: number | null;
  trv_temperature: number | null;
  external_temperature_freshness: FreshnessMode | null;
  external_temperature_health_entity: string | null;
  external_temperature_last_seen_at: string | null;
  external_temperature_age_seconds: number | null;
  external_temperature_stale_after_seconds: number | null;
  trv_last_seen_at: string | null;
  next_schedule_at: string | null;
  next_preset: PresetKey | null;
  next_scheduled_temperature: number | null;
  schedule_preset: PresetKey;
  schedule_temperature: number;
  scheduled_temperature: number;
  preset: PresetKey;
  demand: boolean;
  commanded_temperature: number;
  reported_target_temperature: number | null;
  hvac_mode: string | null;
  hvac_action: string | null;
  valve_opening_degree: number | null;
  valve_closing_degree: number | null;
  battery_level: number | null;
  linkquality: number | null;
  warning: string | null;
  control_source: "schedule" | "away" | "boost" | "override" | "preheat";
  override_until: string | null;
  override_mode: "preset" | "temperature" | null;
  override_scope_type: "house" | "group" | "room" | null;
  override_scope_id: string | null;
  predictive_action: "preheating" | null;
  thermal_model_status: "disabled" | "learning" | "ready" | "stale";
  heating_rate_c_per_hour: number | null;
  heating_samples: number;
  model_confidence: number;
  predicted_minutes_to_target: number | null;
  temperature_reason: string;
  demand_reason: string;
  command_reason: string;
};

export type DiagnosticRelay = {
  configured: boolean;
  entity_id: string | null;
  requested: boolean | null;
  actual: boolean | null;
};

export type DiagnosticsData = {
  updated_at: string;
  overall_status: "monitoring" | "running" | "warning" | "error";
  controller: {
    enabled: boolean;
    loop_status: "starting" | "waiting" | "healthy" | "overdue" | "error";
    cycle_interval_seconds: number;
    last_cycle_started_at: string | null;
    last_cycle_finished_at: string | null;
    last_successful_cycle_at: string | null;
    last_error: string | null;
    waiting_for_readiness: boolean;
    fault_latched: boolean;
    fault_reason: string | null;
    control_alert_reason: string | null;
    consecutive_failures: number;
    loop_restarts: number;
    latest_actions: string[];
    mode: "home" | "away";
    active_boosts: Array<{ room_group: string | null; expires_at: string }>;
    hot_water_boost: ActiveHotWaterBoost | null;
    active_temperature_overrides: ActiveTemperatureOverride[];
    active_hot_water_override: ActiveHotWaterOverride | null;
  };
  plant: {
    boiler: DiagnosticRelay;
    hot_water: DiagnosticRelay;
  };
  metrics: {
    healthy: boolean;
    schema_version: number | null;
    retention_days: number;
    cycle_count: number;
    room_sample_count: number;
    oldest_sample_at: string | null;
    latest_sample_at: string | null;
    database_size_bytes: number;
    last_successful_write_at: string | null;
    consecutive_failures: number;
    last_error: string | null;
  } | null;
  issues: DiagnosticIssue[];
  rooms: DiagnosticRoom[];
  events: Array<{
    occurred_at: string;
    level: "info" | "warning" | "error";
    message: string;
  }>;
};

export type Selection = {
  enabled: boolean;
  room_group: string;
  external_temperature_sensor: string;
  external_temperature_last_seen: string;
  external_temperature_freshness: Exclude<FreshnessMode, "auto">;
  external_sensor_stale_minutes: string;
  trv_last_seen: string;
  trv_freshness: Exclude<FreshnessMode, "auto">;
  predictive_heating_enabled: boolean;
};

export type PresetValues = Record<PresetKey, string>;

export type ControlNumberKey =
  | "cycle_interval_seconds"
  | "heating_offset"
  | "idle_offset"
  | "hysteresis"
  | "frost_protection_temperature"
  | "minimum_boiler_on_minutes"
  | "minimum_boiler_off_minutes"
  | "maximum_boiler_on_minutes"
  | "command_retries"
  | "failures_before_alert"
  | "external_sensor_stale_minutes"
  | "trv_stale_minutes";

export type PredictiveControlNumberKey = "maximum_preheat_minutes";

export type ControlValues = Record<ControlNumberKey | PredictiveControlNumberKey, string> & {
  enabled: boolean;
  predictive_heating_enabled: boolean;
  away_preset: PresetKey;
  smart_temperature_control: boolean;
  open_window_detection: boolean;
  child_lock: boolean;
};

export type PreflightCheck = {
  key: string;
  label: string;
  passed: boolean;
  severity: "warning" | "error";
  detail: string;
};

export type PreflightResult = { ok: boolean; checks: PreflightCheck[] };

export type SettingsScreen =
  | "devices"
  | "boiler"
  | "control"
  | "preheating"
  | "presets"
  | "schedules"
  | "notifications"
  | "theme";

export type Screen = "dashboard" | "diagnostics" | SettingsScreen;
