from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FreshnessMode = Literal["auto", "last_seen", "state_updates", "disabled"]


def _validate_entity_id(value: str, domains: set[str], label: str) -> str:
    value = value.strip()
    if "." not in value:
        raise ValueError(f"{label} must be a Home Assistant entity ID")
    domain, object_id = value.split(".", 1)
    if domain not in domains or not object_id:
        allowed = ", ".join(sorted(domains))
        raise ValueError(f"{label} must use one of these domains: {allowed}")
    return value


class TRVEntities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    climate: str
    external_temperature_input: str | None = None
    frost_protection_temperature: str | None = None
    local_temperature_calibration: str | None = None
    temperature_accuracy: str | None = None
    temporary_mode_duration: str | None = None
    timer_mode_target_temperature: str | None = None
    local_temperature: str | None = None
    battery: str | None = None
    last_seen: str | None = None
    child_lock: str | None = None
    smart_temperature_control: str | None = None
    open_window: str | None = None
    temperature_sensor_select: str | None = None
    temporary_mode_select: str | None = None
    valve_opening_degree: str | None = None
    valve_closing_degree: str | None = None
    closing_steps: str | None = None
    idle_steps: str | None = None
    valve_opening_limit_voltage: str | None = None
    valve_closing_limit_voltage: str | None = None
    valve_motor_running_voltage: str | None = None
    linkquality: str | None = None
    firmware_update: str | None = None

    @field_validator("climate")
    @classmethod
    def validate_climate(cls, value: str) -> str:
        return _validate_entity_id(value, {"climate"}, "TRV climate entity")


class ControlledTRV(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    manufacturer: str | None = None
    model: str | None = None
    software_version: str | None = None
    area_name: str | None = None
    room_group: str = Field(min_length=1, max_length=80)
    external_temperature_sensor: str | None = None
    external_temperature_last_seen: str | None = None
    external_temperature_freshness: FreshnessMode = "auto"
    external_sensor_stale_seconds: int | None = Field(default=None, ge=300, le=86400)
    trv_freshness: FreshnessMode = "last_seen"
    predictive_heating_enabled: bool = False
    entities: TRVEntities

    @field_validator("device_id", "name", "room_group")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("external_temperature_sensor")
    @classmethod
    def validate_external_sensor(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_entity_id(value, {"sensor", "input_number"}, "Temperature sensor")

    @field_validator("external_temperature_last_seen")
    @classmethod
    def validate_external_last_seen(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_entity_id(value, {"sensor"}, "External sensor last-seen entity")


class TemperaturePresets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boost: float = Field(default=23, ge=5, le=30)
    comfort: float = Field(default=21, ge=5, le=30)
    eco: float = Field(default=16, ge=5, le=30)
    frost: float = Field(default=5, ge=5, le=30)

    @model_validator(mode="after")
    def validate_temperature_order(self) -> TemperaturePresets:
        if not self.boost > self.comfort > self.eco > self.frost:
            raise ValueError("Preset temperatures must descend from Boost to Frost")
        return self


class SetupProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: bool = True
    boiler: bool = False
    control: bool = False
    presets: bool = False
    schedules: bool = False


Weekday = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
PresetName = Literal["boost", "comfort", "eco", "frost"]
HotWaterState = Literal["on", "off"]


def _time_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


class SchedulePeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[Weekday] = Field(min_length=1)
    start: str
    end: str
    preset: PresetName

    @field_validator("days")
    @classmethod
    def validate_unique_days(cls, value: list[Weekday]) -> list[Weekday]:
        if len(value) != len(set(value)):
            raise ValueError("A weekday can only be selected once per period")
        return value

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Times must use 24-hour HH:MM format")
        return value

    @model_validator(mode="after")
    def validate_time_order(self) -> SchedulePeriod:
        if _time_minutes(self.start) >= _time_minutes(self.end):
            raise ValueError("A schedule period must end after it starts")
        return self


class RoomSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_group: str = Field(min_length=1, max_length=80)
    default_preset: PresetName = "eco"
    periods: list[SchedulePeriod] = Field(default_factory=list)

    @field_validator("room_group")
    @classmethod
    def strip_room_group(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Room group must not be blank")
        return value

    @model_validator(mode="after")
    def validate_no_overlaps(self) -> RoomSchedule:
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ):
            periods = sorted(
                (
                    (_time_minutes(period.start), _time_minutes(period.end))
                    for period in self.periods
                    if day in period.days
                ),
                key=lambda period: period[0],
            )
            if any(
                current[0] < previous[1]
                for previous, current in zip(periods, periods[1:], strict=False)
            ):
                raise ValueError(f"Schedule periods overlap on {day.title()}")
        return self


class HotWaterPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[Weekday] = Field(min_length=1)
    start: str
    end: str
    state: HotWaterState

    @field_validator("days")
    @classmethod
    def validate_unique_days(cls, value: list[Weekday]) -> list[Weekday]:
        if len(value) != len(set(value)):
            raise ValueError("A weekday can only be selected once per period")
        return value

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Times must use 24-hour HH:MM format")
        return value

    @model_validator(mode="after")
    def validate_time_order(self) -> HotWaterPeriod:
        if _time_minutes(self.start) >= _time_minutes(self.end):
            raise ValueError("A hot-water period must end after it starts")
        return self


class HotWaterSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_state: HotWaterState = "off"
    periods: list[HotWaterPeriod] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_no_overlaps(self) -> HotWaterSchedule:
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ):
            periods = sorted(
                (
                    (_time_minutes(period.start), _time_minutes(period.end))
                    for period in self.periods
                    if day in period.days
                ),
                key=lambda period: period[0],
            )
            if any(
                current[0] < previous[1]
                for previous, current in zip(periods, periods[1:], strict=False)
            ):
                raise ValueError(f"Hot-water periods overlap on {day.title()}")
        return self


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedules: list[RoomSchedule]
    hot_water_schedule: HotWaterSchedule = Field(default_factory=HotWaterSchedule)


class BoilerSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boiler_switch: str | None = None
    hot_water_switch: str | None = None
    minimum_heat_requests: int = Field(default=1, ge=1)

    @field_validator("boiler_switch", "hot_water_switch")
    @classmethod
    def validate_switch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_entity_id(value, {"switch"}, "Relay")

    @model_validator(mode="after")
    def validate_unique_switches(self) -> BoilerSettingsUpdate:
        if self.boiler_switch and self.boiler_switch == self.hot_water_switch:
            raise ValueError("Heating and hot-water switches must be different")
        return self


class ControlSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    cycle_interval_seconds: int = Field(default=30, ge=10, le=300)
    heating_offset: float = Field(default=2, ge=0.5, le=10)
    idle_offset: float = Field(default=2, ge=0.5, le=10)
    hysteresis: float = Field(default=0.2, ge=0, le=2)
    trv_mode: Literal["heat"] = "heat"
    temperature_source: Literal["internal"] = "internal"
    away_preset: PresetName = "frost"
    smart_temperature_control: bool = False
    open_window_detection: bool = False
    child_lock: bool = False
    frost_protection_temperature: float = Field(default=5, ge=4, le=35)
    minimum_boiler_on_seconds: int = Field(default=300, ge=0, le=3600)
    minimum_boiler_off_seconds: int = Field(default=300, ge=0, le=3600)
    maximum_boiler_on_seconds: int = Field(default=14400, ge=300, le=21600)
    command_retries: int = Field(default=2, ge=0, le=5)
    failures_before_alert: int = Field(default=3, ge=1, le=10)
    external_sensor_stale_seconds: int = Field(default=5400, ge=300, le=86400)
    trv_stale_seconds: int = Field(default=3600, ge=300, le=86400)
    predictive_heating_enabled: bool = False
    maximum_preheat_seconds: int = Field(default=7200, ge=900, le=14400)


class ControlSettingsUpdate(ControlSettings):
    pass


class SafetyState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_latched: bool = False
    fault_reason: str | None = None
    fault_at: datetime | None = None


NotificationLevel = Literal["critical", "warning"]


def _validate_discord_webhook(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    allowed_hosts = {
        "discord.com",
        "discordapp.com",
        "canary.discord.com",
        "ptb.discord.com",
    }
    path_parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or len(path_parts) < 4
        or path_parts[:2] != ["api", "webhooks"]
    ):
        raise ValueError("Enter a valid HTTPS Discord webhook URL")
    return value


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    discord_webhook_url: str | None = Field(default=None, repr=False)
    level: NotificationLevel = "critical"
    send_recovery: bool = True
    warning_delay_seconds: int = Field(default=900, ge=60, le=86400)

    @field_validator("discord_webhook_url")
    @classmethod
    def validate_discord_webhook(cls, value: str | None) -> str | None:
        return _validate_discord_webhook(value)

    @model_validator(mode="after")
    def validate_enabled_webhook(self) -> NotificationSettings:
        if self.enabled and not self.discord_webhook_url:
            raise ValueError("A Discord webhook URL is required when notifications are enabled")
        return self


class NotificationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    discord_webhook_url: str | None = None
    clear_webhook: bool = False
    level: NotificationLevel = "critical"
    send_recovery: bool = True
    warning_delay_seconds: int = Field(default=900, ge=60, le=86400)

    @field_validator("discord_webhook_url")
    @classmethod
    def validate_discord_webhook(cls, value: str | None) -> str | None:
        return _validate_discord_webhook(value)


class HeatingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 10
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    boiler_switch: str | None = None
    hot_water_switch: str | None = None
    minimum_heat_requests: int = Field(default=1, ge=1)
    control: ControlSettings = Field(default_factory=ControlSettings)
    safety: SafetyState = Field(default_factory=SafetyState)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    trvs: list[ControlledTRV] = Field(min_length=1)
    presets: TemperaturePresets = Field(default_factory=TemperaturePresets)
    setup: SetupProgress = Field(default_factory=SetupProgress)
    schedules: list[RoomSchedule] = Field(default_factory=list)
    hot_water_schedule: HotWaterSchedule = Field(default_factory=HotWaterSchedule)

    @model_validator(mode="before")
    @classmethod
    def migrate_setup_progress(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        schema_version = int(migrated.get("schema_version") or 1)
        if schema_version < 2:
            control = dict(migrated.get("control") or {})
            if control.get("external_sensor_stale_seconds", 1800) == 1800:
                control["external_sensor_stale_seconds"] = 5400
            migrated["control"] = control
        if schema_version < 5:
            control = dict(migrated.get("control") or {})
            if control.get("trv_stale_seconds", 900) == 900:
                control["trv_stale_seconds"] = 3600
            migrated["control"] = control
            migrated["schema_version"] = 5
        if schema_version < 6:
            control = dict(migrated.get("control") or {})
            if "failures_before_fault" in control and "failures_before_alert" not in control:
                control["failures_before_alert"] = control.pop("failures_before_fault")
            migrated["control"] = control
            migrated["schema_version"] = 6
        if schema_version < 7:
            migrated["schema_version"] = 7
        if schema_version < 8:
            control = dict(migrated.get("control") or {})
            control.pop("predictive_heating_enabled", None)
            migrated["control"] = control
            migrated["trvs"] = [
                ({**trv, "predictive_heating_enabled": False} if isinstance(trv, dict) else trv)
                for trv in migrated.get("trvs", [])
            ]
            migrated["schema_version"] = 8
        if schema_version < 9:
            control = dict(migrated.get("control") or {})
            control.setdefault("predictive_heating_enabled", False)
            migrated["control"] = control
            migrated["schema_version"] = 9
        if schema_version < 10:
            control = dict(migrated.get("control") or {})
            control.pop("maximum_early_stop_seconds", None)
            migrated["control"] = control
            migrated["schema_version"] = 10
        setup = dict(migrated.get("setup") or {})
        if "boiler" not in setup:
            setup["boiler"] = bool(
                migrated.get("boiler_switch") or migrated.get("hot_water_switch")
            )
        migrated["setup"] = setup
        return migrated

    @field_validator("boiler_switch", "hot_water_switch")
    @classmethod
    def validate_switch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_entity_id(value, {"switch"}, "Relay")

    @model_validator(mode="after")
    def validate_unique_assignments(self) -> HeatingConfig:
        if self.boiler_switch and self.boiler_switch == self.hot_water_switch:
            raise ValueError("Heating and hot-water switches must be different")
        device_ids = [trv.device_id for trv in self.trvs]
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("A TRV device can only be configured once")
        climates = [trv.entities.climate for trv in self.trvs]
        if len(climates) != len(set(climates)):
            raise ValueError("A TRV climate entity can only be configured once")
        room_groups = {trv.room_group for trv in self.trvs}
        scheduled_groups = [schedule.room_group for schedule in self.schedules]
        if len(scheduled_groups) != len(set(scheduled_groups)):
            raise ValueError("A room group can only have one schedule")
        unknown_groups = set(scheduled_groups) - room_groups
        if unknown_groups:
            raise ValueError(
                f"Schedules contain unknown room groups: {', '.join(sorted(unknown_groups))}"
            )
        if self.setup.schedules and set(scheduled_groups) != room_groups:
            raise ValueError("Completed schedules must include every room group")
        if self.minimum_heat_requests > len(self.trvs):
            raise ValueError("Minimum heat requests cannot exceed the number of configured TRVs")
        return self


class ConfigStore:
    """Atomic, versioned persistence for the user-owned system configuration."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def events_path(self) -> Path:
        return self.path.with_name("heat-steward-events.json")

    @property
    def notification_state_path(self) -> Path:
        return self.path.with_name("heat-steward-notifications.json")

    @property
    def action_store_path(self) -> Path:
        return self.path.with_name("heat-steward-actions.json")

    @property
    def metrics_path(self) -> Path:
        return self.path.with_name("heat-steward-metrics.db")

    def load(self) -> HeatingConfig | None:
        if not self.path.exists():
            return None
        return HeatingConfig.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, config: HeatingConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = config.model_dump_json(indent=2)
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        except Exception:
            with suppress(FileNotFoundError):
                os.unlink(temporary_path)
            raise
