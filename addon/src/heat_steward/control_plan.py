from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import HeatingConfig, HotWaterState, PresetName
from .entity_state import (
    external_freshness,
    is_available,
    reported_age_seconds,
    reported_timestamp,
    temperature,
    timestamp,
    timestamp_age_seconds,
)
from .overrides import OverrideState
from .predictive import RoomThermalModel
from .schedules import (
    active_hot_water_state,
    active_room_preset,
    next_hot_water_state,
    next_room_preset,
)


def _rounded_setpoint(value: float) -> float:
    return max(4.0, min(35.0, round(value * 2) / 2))


@dataclass(frozen=True)
class RoomDecision:
    device_id: str
    room_group: str
    preset: PresetName
    schedule_preset: PresetName
    schedule_temperature: float
    scheduled_temperature: float
    current_temperature: float | None
    current_temperature_entity: str
    external_temperature: float | None
    trv_temperature: float | None
    trv_target_temperature: float | None
    commanded_temperature: float
    demand: bool
    available: bool
    hvac_mode: str | None
    hvac_action: str | None
    status: str
    valve_opening_degree: float | None = None
    valve_closing_degree: float | None = None
    battery_level: float | None = None
    linkquality: float | None = None
    external_temperature_freshness: str | None = None
    external_temperature_health_entity: str | None = None
    external_temperature_last_seen_at: datetime | None = None
    external_temperature_age_seconds: float | None = None
    external_temperature_stale_after_seconds: int | None = None
    trv_last_seen_at: datetime | None = None
    next_schedule_at: datetime | None = None
    next_preset: PresetName | None = None
    next_scheduled_temperature: float | None = None
    warning: str | None = None
    control_source: str = "schedule"
    override_until: datetime | None = None
    override_mode: str | None = None
    override_scope_type: str | None = None
    override_scope_id: str | None = None
    predictive_action: str | None = None
    predictive_available: bool = False
    thermal_model_status: str = "disabled"
    heating_rate_c_per_hour: float | None = None
    heating_samples: int = 0
    model_confidence: float = 0.0
    predicted_minutes_to_target: float | None = None


@dataclass(frozen=True)
class ControlPlan:
    generated_at: datetime
    rooms: tuple[RoomDecision, ...]
    heat_requests: int
    boiler_should_be_on: bool
    boiler_is_on: bool | None
    hot_water_should_be_on: bool | None
    hot_water_is_on: bool | None
    hot_water_next_change_at: datetime | None
    hot_water_next_state: HotWaterState | None


def build_control_plan(
    config: HeatingConfig,
    states: list[dict[str, Any]],
    at: datetime,
    previous_demands: dict[str, bool] | None = None,
    overrides: OverrideState | None = None,
    thermal_models: dict[str, RoomThermalModel] | None = None,
) -> ControlPlan:
    states_by_entity = {
        state["entity_id"]: state for state in states if isinstance(state.get("entity_id"), str)
    }
    schedules = {schedule.room_group: schedule for schedule in config.schedules}
    previous_demands = previous_demands or {}
    overrides = (overrides or OverrideState()).active_at(at)
    thermal_models = thermal_models or {}
    decisions: list[RoomDecision] = []

    boiler_state = states_by_entity.get(config.boiler_switch) if config.boiler_switch else None
    boiler_is_on = boiler_state.get("state") == "on" if is_available(boiler_state) else None

    for trv in config.trvs:
        schedule = schedules.get(trv.room_group)
        if schedule is None:
            continue
        scheduled_preset = active_room_preset(schedule, at)
        next_schedule = next_room_preset(schedule, at)
        applicable_overrides = [
            override
            for override in overrides.temperature_overrides
            if (
                override.scope_type == "house"
                or (override.scope_type == "group" and override.scope_id == trv.room_group)
                or (override.scope_type == "room" and override.scope_id == trv.device_id)
            )
        ]
        active_override = (
            max(applicable_overrides, key=lambda override: override.sequence)
            if applicable_overrides
            else None
        )
        if active_override is not None:
            if active_override.override_mode == "preset":
                preset = active_override.preset or scheduled_preset
                desired = float(getattr(config.presets, preset))
                control_source = "boost" if preset == "boost" else "override"
            else:
                preset = scheduled_preset
                desired = float(
                    active_override.temperature if active_override.temperature is not None else 5
                )
                control_source = "override"
            override_until = active_override.expires_at
            override_mode = active_override.override_mode
            override_scope_type = active_override.scope_type
            override_scope_id = active_override.scope_id
        elif overrides.mode == "away":
            preset = config.control.away_preset
            desired = float(getattr(config.presets, preset))
            control_source = "away"
            override_until = None
            override_mode = None
            override_scope_type = None
            override_scope_id = None
        else:
            preset = scheduled_preset
            desired = float(getattr(config.presets, preset))
            control_source = "schedule"
            override_until = None
            override_mode = None
            override_scope_type = None
            override_scope_id = None

        climate = states_by_entity.get(trv.entities.climate)
        attributes = climate.get("attributes", {}) if climate else {}
        trv_temperature = temperature(attributes.get("current_temperature"))
        if trv_temperature is None and trv.entities.local_temperature:
            trv_temperature = temperature(
                states_by_entity.get(trv.entities.local_temperature, {}).get("state")
            )

        last_seen = states_by_entity.get(trv.entities.last_seen) if trv.entities.last_seen else None
        last_seen_age = timestamp_age_seconds(last_seen.get("state") if last_seen else None, at)
        climate_age = reported_age_seconds(climate, at)
        if trv.trv_freshness == "disabled":
            trv_age = None
            trv_last_seen_at = None
        elif trv.trv_freshness == "state_updates":
            trv_age = climate_age
            trv_last_seen_at = reported_timestamp(climate)
        else:
            trv_age = last_seen_age if last_seen_age is not None else climate_age
            trv_last_seen_at = timestamp(
                last_seen.get("state") if last_seen else None
            ) or reported_timestamp(climate)
        trv_stale = (
            trv.trv_freshness != "disabled"
            and last_seen is not None
            and not is_available(last_seen)
            or trv_age is not None
            and trv_age > config.control.trv_stale_seconds
        )

        external_temperature = None
        current_temperature = trv_temperature
        current_entity = trv.entities.climate
        external_freshness_mode = None
        external_health_entity = None
        external_last_seen_at = None
        external_age = None
        external_stale_after = None
        warning = None
        if trv.external_temperature_sensor:
            external_state = states_by_entity.get(trv.external_temperature_sensor)
            (
                external_freshness_mode,
                external_health_entity,
                external_last_seen_at,
                external_age,
            ) = external_freshness(trv, states_by_entity, external_state, at)
            external_stale_after = (
                trv.external_sensor_stale_seconds or config.control.external_sensor_stale_seconds
            )
            external_health_unavailable = (
                external_freshness_mode == "last_seen"
                and external_health_entity is not None
                and (
                    not is_available(states_by_entity.get(external_health_entity))
                    or external_last_seen_at is None
                )
            )
            external_stale = (
                external_health_unavailable
                or external_age is not None
                and external_age > external_stale_after
            )
            if is_available(external_state) and not external_stale:
                external_temperature = temperature(external_state.get("state"))
            if external_temperature is not None:
                current_temperature = external_temperature
                current_entity = trv.external_temperature_sensor
            elif external_stale:
                warning = "External room sensor is stale; using the TRV temperature"
            else:
                warning = "External room sensor unavailable; using the TRV temperature"

        available = is_available(climate) and current_temperature is not None and not trv_stale
        if trv_stale:
            warning = "TRV has not reported recently and this room is being skipped"
        previous = previous_demands.get(trv.device_id, False)
        model = thermal_models.get(trv.device_id)
        current_source = "external" if external_temperature is not None else "trv"
        compatible_model = (
            model
            if model
            and model.temperature_source == current_source
            and model.temperature_entity == current_entity
            else None
        )
        model_status = (
            "disabled"
            if not config.control.predictive_heating_enabled or not trv.predictive_heating_enabled
            else compatible_model.status(at)
            if compatible_model
            else "learning"
        )
        predictive_action = None
        predicted_minutes_to_target = None
        predictive_available = bool(
            compatible_model is not None and compatible_model.actionable(at)
        )

        if (
            available
            and config.control.predictive_heating_enabled
            and trv.predictive_heating_enabled
            and control_source == "schedule"
            and compatible_model is not None
            and compatible_model.heating_ready(at)
            and compatible_model.heating_rate_c_per_hour
            and next_schedule is not None
        ):
            next_temperature = float(getattr(config.presets, next_schedule[1]))
            seconds_until_change = (next_schedule[0] - at).total_seconds()
            if next_temperature > desired and seconds_until_change > 0:
                degrees_to_heat = max(0.0, next_temperature - current_temperature)
                predicted_minutes_to_target = (
                    degrees_to_heat / compatible_model.heating_rate_c_per_hour * 60
                )
                # Five minutes absorbs valve travel and cycle granularity; the learned
                # rate already includes the rest of the room's normal response delay.
                lead_minutes = min(
                    config.control.maximum_preheat_seconds / 60,
                    predicted_minutes_to_target + 5,
                )
                if seconds_until_change <= lead_minutes * 60 and degrees_to_heat > 0:
                    preset = next_schedule[1]
                    desired = next_temperature
                    control_source = "preheat"
                    predictive_action = "preheating"

        if not available:
            demand = False
        elif current_temperature <= desired - config.control.hysteresis:
            demand = True
        elif current_temperature >= desired:
            demand = False
        else:
            demand = previous

        command = _rounded_setpoint(
            desired + config.control.heating_offset
            if demand
            else desired - config.control.idle_offset
        )
        hvac_mode = str(climate.get("state")) if climate else None
        hvac_action = attributes.get("hvac_action") or attributes.get("running_state")
        trv_heating = hvac_action in {"heat", "heating"}

        if not is_available(climate) or current_temperature is None:
            status = "unavailable"
        elif trv_stale:
            status = "stale"
        elif demand and trv_heating:
            status = "heating"
        elif demand and config.boiler_switch and not boiler_is_on:
            status = "waiting"
        elif demand:
            status = "opening"
        elif trv_heating:
            status = "closing"
        elif current_temperature < desired:
            status = "within_range"
        else:
            status = "idle"

        decisions.append(
            RoomDecision(
                device_id=trv.device_id,
                room_group=trv.room_group,
                preset=preset,
                schedule_preset=scheduled_preset,
                schedule_temperature=float(getattr(config.presets, scheduled_preset)),
                scheduled_temperature=desired,
                current_temperature=current_temperature,
                current_temperature_entity=current_entity,
                external_temperature=external_temperature,
                trv_temperature=trv_temperature,
                trv_target_temperature=temperature(attributes.get("temperature")),
                commanded_temperature=command,
                demand=demand,
                available=available,
                hvac_mode=hvac_mode,
                hvac_action=str(hvac_action) if hvac_action is not None else None,
                status=status,
                valve_opening_degree=temperature(
                    states_by_entity.get(trv.entities.valve_opening_degree, {}).get("state")
                ),
                valve_closing_degree=temperature(
                    states_by_entity.get(trv.entities.valve_closing_degree, {}).get("state")
                ),
                battery_level=temperature(
                    states_by_entity.get(trv.entities.battery, {}).get("state")
                ),
                linkquality=temperature(
                    states_by_entity.get(trv.entities.linkquality, {}).get("state")
                ),
                external_temperature_freshness=external_freshness_mode,
                external_temperature_health_entity=external_health_entity,
                external_temperature_last_seen_at=external_last_seen_at,
                external_temperature_age_seconds=external_age,
                external_temperature_stale_after_seconds=external_stale_after,
                trv_last_seen_at=trv_last_seen_at,
                next_schedule_at=next_schedule[0] if next_schedule else None,
                next_preset=next_schedule[1] if next_schedule else None,
                next_scheduled_temperature=(
                    float(getattr(config.presets, next_schedule[1])) if next_schedule else None
                ),
                warning=warning,
                control_source=control_source,
                override_until=override_until,
                override_mode=override_mode,
                override_scope_type=override_scope_type,
                override_scope_id=override_scope_id,
                predictive_action=predictive_action,
                predictive_available=predictive_available,
                thermal_model_status=model_status,
                heating_rate_c_per_hour=(
                    compatible_model.heating_rate_c_per_hour if compatible_model else None
                ),
                heating_samples=compatible_model.heating_samples if compatible_model else 0,
                model_confidence=compatible_model.confidence if compatible_model else 0.0,
                predicted_minutes_to_target=predicted_minutes_to_target,
            )
        )

    heat_requests = sum(decision.demand for decision in decisions if decision.available)
    boiler_should_be_on = bool(
        config.boiler_switch and heat_requests >= config.minimum_heat_requests
    )
    hot_water_state = (
        states_by_entity.get(config.hot_water_switch) if config.hot_water_switch else None
    )
    hot_water_is_on = (
        hot_water_state.get("state") == "on" if is_available(hot_water_state) else None
    )
    hot_water_should_be_on = (
        overrides.hot_water_override.on
        if config.hot_water_switch and overrides.hot_water_override is not None
        else (
            False
            if config.hot_water_switch and overrides.mode == "away"
            else (
                active_hot_water_state(config.hot_water_schedule, at) == "on"
                if config.hot_water_switch
                else None
            )
        )
    )
    hot_water_next_change = (
        next_hot_water_state(config.hot_water_schedule, at) if config.hot_water_switch else None
    )

    return ControlPlan(
        generated_at=at,
        rooms=tuple(decisions),
        heat_requests=heat_requests,
        boiler_should_be_on=boiler_should_be_on,
        boiler_is_on=boiler_is_on,
        hot_water_should_be_on=hot_water_should_be_on,
        hot_water_is_on=hot_water_is_on,
        hot_water_next_change_at=(hot_water_next_change[0] if hot_water_next_change else None),
        hot_water_next_state=(hot_water_next_change[1] if hot_water_next_change else None),
    )
