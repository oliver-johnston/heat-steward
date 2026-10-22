from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import HeatingConfig
from .control import HeatingController
from .control_plan import build_control_plan
from .overrides import OverrideState


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _available(state: dict[str, Any] | None) -> bool:
    return state is not None and state.get("state") not in {None, "unknown", "unavailable"}


def _switch_state(states: dict[str, dict[str, Any]], entity_id: str | None) -> bool | None:
    if not entity_id:
        return None
    state = states.get(entity_id)
    return state.get("state") == "on" if _available(state) else None


def _temperature_label(value: float) -> str:
    return f"{value:g}°C"


def _room_reasoning(decision: Any, config: HeatingConfig) -> tuple[str, str, str]:
    """Explain the effective target, heat demand and resulting TRV command."""
    effective = _temperature_label(decision.scheduled_temperature)
    scheduled = (
        f"{decision.schedule_preset.title()} ({_temperature_label(decision.schedule_temperature)})"
    )
    if decision.control_source == "schedule":
        temperature_reason = f"The room schedule currently selects {scheduled}."
    elif decision.control_source == "preheat":
        temperature_reason = (
            f"Predictive control is preparing for the next scheduled "
            f"{decision.preset.title()} target ({effective})."
        )
    elif decision.control_source == "away":
        temperature_reason = (
            f"Away mode replaces the room schedule ({scheduled}) with "
            f"{decision.preset.title()} ({effective})."
        )
    else:
        scope = {
            "house": "whole-house",
            "group": f"{decision.room_group} group",
            "room": "room",
        }.get(decision.override_scope_type, "temporary")
        if decision.override_mode == "temperature":
            value = effective
        else:
            value = f"{decision.preset.title()} ({effective})"
        temperature_reason = (
            f"A {scope} override replaces the room schedule ({scheduled}) with {value}."
        )

    lower = decision.scheduled_temperature - config.control.hysteresis
    target = decision.scheduled_temperature
    if not decision.available:
        demand_reason = (
            "This room is unavailable, so it is not requesting heat and is excluded from "
            "the boiler heat-request count."
        )
        command_reason = "No TRV command is sent while this room is unavailable."
    elif decision.demand and decision.current_temperature <= lower:
        demand_reason = (
            f"The measured temperature ({_temperature_label(decision.current_temperature)}) "
            f"is at or below the {_temperature_label(lower)} lower hysteresis threshold."
        )
        command_reason = (
            f"Heat is requested, so the effective target ({effective}) plus the "
            f"{config.control.heating_offset:g}°C heating offset gives a "
            f"{_temperature_label(decision.commanded_temperature)} TRV command."
        )
    elif not decision.demand and decision.current_temperature >= target:
        demand_reason = (
            f"The measured temperature ({_temperature_label(decision.current_temperature)}) "
            f"has reached the {_temperature_label(target)} target, so heating stops."
        )
        command_reason = (
            f"No heat is requested, so the effective target ({effective}) minus the "
            f"{config.control.idle_offset:g}°C idle offset gives a "
            f"{_temperature_label(decision.commanded_temperature)} TRV command."
        )
    else:
        retained_state = (
            "heat request is retained" if decision.demand else "satisfied state is retained"
        )
        demand_reason = (
            f"The measured temperature ({_temperature_label(decision.current_temperature)}) "
            f"is between the {_temperature_label(lower)} start threshold and the "
            f"{_temperature_label(target)} target, so the previous {retained_state}."
        )
        if decision.demand:
            command_reason = (
                f"The retained heat request uses the effective target ({effective}) plus the "
                f"{config.control.heating_offset:g}°C heating offset, producing a "
                f"{_temperature_label(decision.commanded_temperature)} TRV command."
            )
        else:
            command_reason = (
                f"The retained satisfied state uses the effective target ({effective}) minus "
                f"the {config.control.idle_offset:g}°C idle offset, producing a "
                f"{_temperature_label(decision.commanded_temperature)} TRV command."
            )
    return temperature_reason, demand_reason, command_reason


def build_diagnostics(
    config: HeatingConfig,
    states: list[dict[str, Any]],
    controller: HeatingController | None,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Build a human-readable snapshot of controller and device health."""
    now = at or datetime.now().astimezone()
    status = controller.status if controller else None
    overrides = controller.get_override_state(now) if controller else OverrideState()
    plan = (
        status.plan
        if status and status.plan
        else build_control_plan(config, states, now, overrides=overrides)
    )
    states_by_entity = {
        state["entity_id"]: state for state in states if isinstance(state.get("entity_id"), str)
    }
    issues: list[dict[str, str]] = []
    metrics = controller.metrics_store.health().as_dict() if controller else None

    if metrics and not metrics["healthy"]:
        issues.append(
            {
                "severity": "warning",
                "title": "Historical metrics are not being recorded",
                "detail": (
                    f"{metrics['last_error'] or 'The metrics database is unavailable'}. "
                    "Heating control is unaffected and collection will be retried next cycle."
                ),
            }
        )

    if controller and controller.predictive_learning_error:
        issues.append(
            {
                "severity": "warning",
                "title": "Predictive heating is using its fallback",
                "detail": (
                    f"{controller.predictive_learning_error}. Standard temperature hysteresis "
                    "remains active."
                ),
            }
        )

    if controller and controller.override_store.last_error:
        issues.append(
            {
                "severity": "warning",
                "title": "Override history could not be read",
                "detail": (
                    f"{controller.override_store.last_error}. The controller is continuing with "
                    "the last valid state and will not overwrite the damaged file."
                ),
            }
        )

    if status is None or status.last_cycle_finished_at is None:
        loop_status = "starting"
        issues.append(
            {
                "severity": "warning",
                "title": "No completed control cycle",
                "detail": "The controller has not completed a cycle since this add-on started.",
            }
        )
    else:
        age_seconds = max(0, (now - status.last_cycle_finished_at).total_seconds())
        overdue_after = max(60, config.control.cycle_interval_seconds * 2)
        if age_seconds > overdue_after:
            loop_status = "overdue"
            issues.append(
                {
                    "severity": "error",
                    "title": "Control loop is overdue",
                    "detail": (
                        f"The last cycle finished {round(age_seconds)} seconds ago; "
                        f"cycles should run every {config.control.cycle_interval_seconds} seconds."
                    ),
                }
            )
        elif status.waiting_for_readiness:
            loop_status = "waiting"
        elif status.last_error:
            loop_status = "error"
        else:
            loop_status = "healthy"

    fault_latched = status.fault_latched if status else config.safety.fault_latched
    fault_reason = status.fault_reason if status else config.safety.fault_reason
    if fault_latched:
        issues.append(
            {
                "severity": "error",
                "title": "Heating paused by a safety stop",
                "detail": fault_reason or "Acknowledge the stop to resume automatic control.",
            }
        )
    elif status and status.waiting_for_readiness:
        issues.append(
            {
                "severity": "warning",
                "title": "Waiting for Home Assistant",
                "detail": status.last_error or "Waiting for configured devices to become ready.",
            }
        )
    elif status and status.last_error:
        issues.append(
            {
                "severity": "error",
                "title": "Latest control error",
                "detail": status.last_error,
            }
        )

    trvs = {trv.device_id: trv for trv in config.trvs}
    rooms: list[dict[str, Any]] = []
    for decision in plan.rooms:
        trv = trvs[decision.device_id]
        temperature_reason, demand_reason, command_reason = _room_reasoning(decision, config)
        source = "external" if decision.external_temperature is not None else "TRV"
        if not decision.available:
            stale = decision.status == "stale"
            unavailable_reason = (
                decision.warning
                if decision.warning
                else (
                    "No usable temperature is available from "
                    f"{decision.current_temperature_entity}."
                )
                if decision.current_temperature is None
                else f"Home Assistant reports {trv.entities.climate} as unavailable."
            )
            issues.append(
                {
                    "severity": "warning",
                    "title": (
                        f"{trv.area_name or trv.name} has not reported recently"
                        if stale
                        else f"{trv.area_name or trv.name} is unavailable"
                    ),
                    "detail": (
                        "This room is being skipped and does not count towards heat demand. "
                        f"{unavailable_reason}"
                    ),
                }
            )
        elif decision.warning:
            issues.append(
                {
                    "severity": "warning",
                    "title": f"{trv.area_name or trv.name} is using its fallback sensor",
                    "detail": decision.warning,
                }
            )
        rooms.append(
            {
                "device_id": decision.device_id,
                "name": trv.area_name or trv.name,
                "room_group": decision.room_group,
                "climate_entity": trv.entities.climate,
                "available": decision.available,
                "status": decision.status,
                "current_temperature": decision.current_temperature,
                "temperature_source": source,
                "temperature_entity": decision.current_temperature_entity,
                "external_temperature": decision.external_temperature,
                "trv_temperature": decision.trv_temperature,
                "external_temperature_freshness": decision.external_temperature_freshness,
                "external_temperature_health_entity": decision.external_temperature_health_entity,
                "external_temperature_last_seen_at": _iso(
                    decision.external_temperature_last_seen_at
                ),
                "external_temperature_age_seconds": decision.external_temperature_age_seconds,
                "external_temperature_stale_after_seconds": (
                    decision.external_temperature_stale_after_seconds
                ),
                "trv_last_seen_at": _iso(decision.trv_last_seen_at),
                "next_schedule_at": _iso(decision.next_schedule_at),
                "next_preset": decision.next_preset,
                "next_scheduled_temperature": decision.next_scheduled_temperature,
                "schedule_preset": decision.schedule_preset,
                "schedule_temperature": decision.schedule_temperature,
                "scheduled_temperature": decision.scheduled_temperature,
                "preset": decision.preset,
                "demand": decision.demand,
                "commanded_temperature": decision.commanded_temperature,
                "reported_target_temperature": decision.trv_target_temperature,
                "hvac_mode": decision.hvac_mode,
                "hvac_action": decision.hvac_action,
                "valve_opening_degree": decision.valve_opening_degree,
                "valve_closing_degree": decision.valve_closing_degree,
                "battery_level": decision.battery_level,
                "linkquality": decision.linkquality,
                "warning": decision.warning,
                "control_source": decision.control_source,
                "override_until": _iso(decision.override_until),
                "override_mode": decision.override_mode,
                "override_scope_type": decision.override_scope_type,
                "override_scope_id": decision.override_scope_id,
                "predictive_action": decision.predictive_action,
                "thermal_model_status": decision.thermal_model_status,
                "heating_rate_c_per_hour": decision.heating_rate_c_per_hour,
                "heating_samples": decision.heating_samples,
                "model_confidence": decision.model_confidence,
                "predicted_minutes_to_target": decision.predicted_minutes_to_target,
                "temperature_reason": temperature_reason,
                "demand_reason": demand_reason,
                "command_reason": command_reason,
            }
        )

    boiler_actual = _switch_state(states_by_entity, config.boiler_switch)
    hot_water_actual = _switch_state(states_by_entity, config.hot_water_switch)
    for name, entity_id, requested, actual in (
        ("Central heating", config.boiler_switch, plan.boiler_should_be_on, boiler_actual),
        ("Hot water", config.hot_water_switch, plan.hot_water_should_be_on, hot_water_actual),
    ):
        if entity_id and actual is None:
            issues.append(
                {
                    "severity": ("warning" if status and status.waiting_for_readiness else "error"),
                    "title": f"{name} relay is unavailable",
                    "detail": f"Home Assistant cannot read {entity_id}.",
                }
            )
        elif config.control.enabled and entity_id and requested is not None and requested != actual:
            issues.append(
                {
                    "severity": "warning",
                    "title": f"{name} is waiting to change",
                    "detail": (
                        f"Requested {'on' if requested else 'off'}, but {entity_id} "
                        f"currently reports {'on' if actual else 'off'}. Anti-cycling "
                        "or the next control cycle may account for this."
                    ),
                }
            )

    if any(issue["severity"] == "error" for issue in issues):
        overall_status = "error"
    elif issues:
        overall_status = "warning"
    elif config.control.enabled:
        overall_status = "running"
    else:
        overall_status = "monitoring"

    events = []
    if status:
        events = [
            {
                "occurred_at": event.occurred_at.isoformat(),
                "level": event.level,
                "message": event.message,
            }
            for event in reversed(status.events[-50:])
        ]

    return {
        "updated_at": now.isoformat(),
        "overall_status": overall_status,
        "controller": {
            "enabled": config.control.enabled,
            "loop_status": loop_status,
            "cycle_interval_seconds": config.control.cycle_interval_seconds,
            "last_cycle_started_at": _iso(status.last_cycle_started_at) if status else None,
            "last_cycle_finished_at": _iso(status.last_cycle_finished_at) if status else None,
            "last_successful_cycle_at": _iso(status.last_successful_cycle_at) if status else None,
            "last_error": status.last_error if status else None,
            "waiting_for_readiness": status.waiting_for_readiness if status else False,
            "fault_latched": fault_latched,
            "fault_reason": fault_reason,
            "control_alert_reason": status.control_alert_reason if status else None,
            "consecutive_failures": status.consecutive_failures if status else 0,
            "loop_restarts": status.loop_restarts if status else 0,
            "latest_actions": list(status.actions) if status else [],
            "mode": overrides.mode,
            "active_boosts": [
                {
                    "room_group": boost.room_group,
                    "expires_at": boost.expires_at.isoformat(),
                }
                for boost in overrides.boosts
            ],
            "hot_water_boost": (
                {
                    "started_at": overrides.hot_water_boost.started_at.isoformat(),
                    "expires_at": overrides.hot_water_boost.expires_at.isoformat(),
                }
                if overrides.hot_water_boost
                else None
            ),
            "active_temperature_overrides": [
                override.model_dump(mode="json") for override in overrides.temperature_overrides
            ],
            "active_hot_water_override": (
                overrides.hot_water_override.model_dump(mode="json")
                if overrides.hot_water_override
                else None
            ),
        },
        "plant": {
            "boiler": {
                "configured": config.boiler_switch is not None,
                "entity_id": config.boiler_switch,
                "requested": plan.boiler_should_be_on if config.boiler_switch else None,
                "actual": boiler_actual,
            },
            "hot_water": {
                "configured": config.hot_water_switch is not None,
                "entity_id": config.hot_water_switch,
                "requested": plan.hot_water_should_be_on,
                "actual": hot_water_actual,
            },
        },
        "metrics": metrics,
        "issues": issues,
        "rooms": sorted(
            rooms, key=lambda room: (room["room_group"].casefold(), room["name"].casefold())
        ),
        "events": events,
    }
