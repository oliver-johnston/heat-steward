from __future__ import annotations

import asyncio
import copy
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from heat_steward.config import (
    ConfigStore,
    ControlSettings,
    HotWaterPeriod,
    HotWaterSchedule,
    RoomSchedule,
    SchedulePeriod,
    SetupProgress,
)
from heat_steward.control import HeatingController, build_control_plan, build_preflight
from heat_steward.diagnostics import build_diagnostics
from heat_steward.discovery import OnboardingRequest, discover, materialise_config
from heat_steward.ha_client import HomeAssistantError
from heat_steward.overrides import (
    ActiveBoost,
    ActiveHotWaterBoost,
    ActiveTemperatureOverride,
    OverrideActionRequest,
    OverrideActor,
    OverrideState,
)
from heat_steward.predictive import RoomThermalModel

from dev.harness import build_snapshot


def configured_system(registry_snapshot, *, enabled: bool = False):
    config = materialise_config(
        OnboardingRequest.model_validate(
            {
                "trvs": [
                    {
                        "device_id": "device-dining",
                        "room_group": "Dining Room",
                        "external_temperature_sensor": "sensor.dining_thermometer_temperature",
                    }
                ]
            }
        ),
        discover(registry_snapshot),
    )
    config.boiler_switch = "switch.boiler_switch_central_heating_switch"
    config.hot_water_switch = "switch.boiler_switch_hot_water_switch"
    config.control = ControlSettings(
        enabled=enabled,
        minimum_boiler_on_seconds=0,
        minimum_boiler_off_seconds=0,
    )
    config.setup = SetupProgress(
        devices=True, boiler=True, control=True, presets=True, schedules=True
    )
    config.schedules = [
        RoomSchedule(
            room_group="Dining Room",
            default_preset="eco",
            periods=[SchedulePeriod(days=["monday"], start="06:00", end="09:00", preset="comfort")],
        )
    ]
    config.hot_water_schedule = HotWaterSchedule(
        periods=[HotWaterPeriod(days=["monday"], start="06:00", end="08:00", state="on")]
    )
    return config


def test_control_plan_uses_external_measurement_without_syncing_it_to_trv(registry_snapshot):
    config = configured_system(registry_snapshot)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    plan = build_control_plan(config, registry_snapshot.states, at)

    room = plan.rooms[0]
    assert room.preset == "comfort"
    assert room.scheduled_temperature == 21
    assert room.current_temperature == 20.4
    assert room.external_temperature == 20.4
    assert room.trv_temperature == 20.0
    assert room.demand is True
    assert room.commanded_temperature == 23
    assert plan.boiler_should_be_on is True
    assert plan.hot_water_should_be_on is True
    assert plan.hot_water_next_change_at == datetime(
        2026, 10, 12, 8, 0, tzinfo=ZoneInfo("Europe/London")
    )
    assert plan.hot_water_next_state == "off"


def test_control_plan_holds_previous_demand_inside_hysteresis(registry_snapshot):
    config = configured_system(registry_snapshot)
    states = copy.deepcopy(registry_snapshot.states)
    temperature = next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_temperature"
    )
    temperature["state"] = "20.9"
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    heating = build_control_plan(config, states, at, {"device-dining": True})
    idle = build_control_plan(config, states, at, {"device-dining": False})

    assert heating.rooms[0].demand is True
    assert idle.rooms[0].demand is False
    assert idle.rooms[0].status == "within_range"

    temperature["state"] = "21.0"
    at_target = build_control_plan(config, states, at, {"device-dining": True})
    assert at_target.rooms[0].demand is False

    temperature["state"] = "20.8"
    at_start_threshold = build_control_plan(config, states, at, {"device-dining": False})
    assert at_start_threshold.rooms[0].demand is True


def test_control_plan_preheats_for_an_upcoming_higher_schedule(registry_snapshot):
    config = configured_system(registry_snapshot)
    config.control.predictive_heating_enabled = True
    config.trvs[0].predictive_heating_enabled = True
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_temperature"
    )["state"] = "20.0"
    at = datetime(2026, 10, 12, 5, 0, tzinfo=ZoneInfo("Europe/London"))
    model = RoomThermalModel(
        device_id="device-dining",
        temperature_source="external",
        temperature_entity="sensor.dining_thermometer_temperature",
        heating_rate_c_per_hour=1.0,
        heating_samples=4,
        heating_updated_at=at,
        confidence=0.8,
    )

    room = build_control_plan(
        config,
        states,
        at,
        thermal_models={"device-dining": model},
    ).rooms[0]

    assert room.control_source == "preheat"
    assert room.predictive_action == "preheating"
    assert room.preset == "comfort"
    assert room.schedule_preset == "eco"
    assert room.scheduled_temperature == 21
    assert room.demand is True
    assert room.predicted_minutes_to_target == 60

    away_room = build_control_plan(
        config,
        states,
        at,
        overrides=OverrideState(mode="away"),
        thermal_models={"device-dining": model},
    ).rooms[0]
    assert away_room.control_source == "away"
    assert away_room.predictive_action is None


def test_control_plan_uses_hysteresis_while_model_is_still_learning(registry_snapshot):
    config = configured_system(registry_snapshot)
    config.control.predictive_heating_enabled = True
    config.trvs[0].predictive_heating_enabled = True
    at = datetime(2026, 10, 12, 5, 0, tzinfo=ZoneInfo("Europe/London"))
    model = RoomThermalModel(
        device_id="device-dining",
        temperature_source="external",
        temperature_entity="sensor.dining_thermometer_temperature",
        heating_rate_c_per_hour=1.0,
        heating_samples=2,
        heating_updated_at=at,
    )

    room = build_control_plan(
        config,
        registry_snapshot.states,
        at,
        thermal_models={"device-dining": model},
    ).rooms[0]

    assert room.control_source == "schedule"
    assert room.thermal_model_status == "learning"
    assert room.predictive_action is None
    assert room.scheduled_temperature == 16
    assert room.demand is False


def test_control_plan_closes_early_when_learned_residual_will_reach_target(
    registry_snapshot,
):
    config = configured_system(registry_snapshot)
    config.control.predictive_heating_enabled = True
    config.trvs[0].predictive_heating_enabled = True
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_temperature"
    )["state"] = "20.7"
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    model = RoomThermalModel(
        device_id="device-dining",
        temperature_source="external",
        temperature_entity="sensor.dining_thermometer_temperature",
        heating_rate_c_per_hour=1.0,
        heating_samples=4,
        heating_updated_at=at,
        residual_rise_c=0.4,
        residual_samples=4,
        residual_updated_at=at,
        confidence=0.8,
    )

    room = build_control_plan(
        config,
        states,
        at,
        previous_demands={"device-dining": True},
        thermal_models={"device-dining": model},
    ).rooms[0]

    assert room.demand is False
    assert room.predictive_action == "coasting"
    assert room.predictive_available is True
    assert room.commanded_temperature == 19

    config.control.predictive_heating_enabled = False
    disabled_room = build_control_plan(
        config,
        states,
        at,
        previous_demands={"device-dining": True},
        thermal_models={"device-dining": model},
    ).rooms[0]
    assert disabled_room.predictive_available is True
    assert disabled_room.predictive_action is None
    assert disabled_room.demand is True


def test_control_plan_does_not_coast_without_a_reliable_heating_rate(registry_snapshot):
    config = configured_system(registry_snapshot)
    config.control.predictive_heating_enabled = True
    config.trvs[0].predictive_heating_enabled = True
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_temperature"
    )["state"] = "20.7"
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    model = RoomThermalModel(
        device_id="device-dining",
        temperature_source="external",
        temperature_entity="sensor.dining_thermometer_temperature",
        heating_rate_c_per_hour=1.0,
        heating_samples=2,
        heating_updated_at=at,
        residual_rise_c=0.4,
        residual_samples=4,
        residual_updated_at=at,
    )

    room = build_control_plan(
        config,
        states,
        at,
        previous_demands={"device-dining": True},
        thermal_models={"device-dining": model},
    ).rooms[0]

    assert room.demand is True
    assert room.predictive_action is None


def test_away_and_boost_override_schedule_with_boost_taking_precedence(registry_snapshot):
    config = configured_system(registry_snapshot)
    config.control.away_preset = "frost"
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    away = build_control_plan(
        config,
        registry_snapshot.states,
        at,
        overrides=OverrideState(mode="away"),
    )
    assert away.rooms[0].preset == "frost"
    assert away.rooms[0].scheduled_temperature == 5
    assert away.rooms[0].control_source == "away"
    assert away.hot_water_should_be_on is False

    boosted = build_control_plan(
        config,
        registry_snapshot.states,
        at,
        overrides=OverrideState(
            mode="away",
            boosts=[
                ActiveBoost(
                    room_group="Dining Room",
                    started_at=at,
                    expires_at=at + timedelta(hours=2),
                )
            ],
        ),
    )
    assert boosted.rooms[0].preset == "boost"
    assert boosted.rooms[0].scheduled_temperature == 23
    assert boosted.rooms[0].control_source == "boost"
    assert boosted.rooms[0].override_until == at + timedelta(hours=2)
    assert boosted.hot_water_should_be_on is False

    hot_water_boosted = build_control_plan(
        config,
        registry_snapshot.states,
        at,
        overrides=OverrideState(
            mode="away",
            hot_water_boost=ActiveHotWaterBoost(
                started_at=at,
                expires_at=at + timedelta(hours=1),
            ),
        ),
    )
    assert hot_water_boosted.hot_water_should_be_on is True

    hot_water_expired = build_control_plan(
        config,
        registry_snapshot.states,
        at + timedelta(hours=2),
        overrides=OverrideState(
            mode="away",
            hot_water_boost=ActiveHotWaterBoost(
                started_at=at,
                expires_at=at + timedelta(hours=1),
            ),
        ),
    )
    assert hot_water_expired.hot_water_should_be_on is False

    expired = build_control_plan(
        config,
        registry_snapshot.states,
        at + timedelta(hours=3),
        overrides=OverrideState(
            mode="away",
            boosts=[
                ActiveBoost(
                    room_group=None,
                    started_at=at,
                    expires_at=at + timedelta(hours=2),
                )
            ],
        ),
    )
    assert expired.rooms[0].preset == "frost"
    assert expired.rooms[0].control_source == "away"
    assert expired.hot_water_next_change_at == datetime(
        2026, 10, 19, 6, 0, tzinfo=ZoneInfo("Europe/London")
    )
    assert expired.hot_water_next_state == "on"


def test_latest_applicable_temperature_action_wins_across_scopes(registry_snapshot):
    config = configured_system(registry_snapshot)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    overrides = OverrideState(
        temperature_overrides=[
            ActiveTemperatureOverride(
                sequence=1,
                action_id="house",
                scope_type="house",
                override_mode="preset",
                preset="boost",
                started_at=at,
                expires_at=at + timedelta(hours=2),
            ),
            ActiveTemperatureOverride(
                sequence=2,
                action_id="room",
                scope_type="room",
                scope_id="device-dining",
                override_mode="temperature",
                temperature=19,
                started_at=at,
                expires_at=at + timedelta(hours=1),
            ),
        ]
    )

    plan = build_control_plan(config, registry_snapshot.states, at, overrides=overrides)

    assert plan.rooms[0].control_source == "override"
    assert plan.rooms[0].scheduled_temperature == 19
    assert plan.rooms[0].override_until == at + timedelta(hours=1)
    assert plan.rooms[0].override_mode == "temperature"
    assert plan.rooms[0].override_scope_type == "room"
    assert plan.rooms[0].override_scope_id == "device-dining"


async def test_modes_and_boosts_persist_and_home_assistant_event_sets_mode(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    controller = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)

    await controller.handle_mode_event({"mode": "away"})
    await controller.start_boost("Dining Room", 120)
    await controller.start_hot_water_boost(60)

    restarted = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)
    state = restarted.get_override_state()
    assert state.mode == "away"
    assert state.boosts[0].room_group == "Dining Room"
    assert state.hot_water_boost is not None
    diagnostics = build_diagnostics(config, registry_snapshot.states, restarted)
    assert diagnostics["controller"]["active_temperature_overrides"][0]["scope_id"] == (
        "Dining Room"
    )
    assert diagnostics["controller"]["active_hot_water_override"]["on"] is True

    await restarted.handle_mode_event({"mode": "invalid"})
    assert restarted.get_override_state().mode == "away"
    assert restarted.get_override_state().boosts[0].room_group == "Dining Room"

    await restarted.cancel_boost("Dining Room")
    assert restarted.get_override_state().boosts == []
    await restarted.cancel_hot_water_boost()
    assert restarted.get_override_state().hot_water_boost is None

    started_at = datetime.now().astimezone()
    restarted.override_store.record(
        OverrideActionRequest(
            channel="hot_water",
            scope_type="hot_water",
            override_mode="on_off",
            on_off=True,
            duration_minutes=30,
        ),
        OverrideActor(source="test"),
        at=started_at,
    )
    expired = restarted.get_override_state(started_at + timedelta(minutes=31))
    assert expired.mode == "away"
    assert expired.hot_water_boost is None
    assert any(event.message == "Hot-water boost ended" for event in restarted.status.events)
    assert any(
        event.message == "Heating mode changed to Away from Home Assistant automation"
        for event in controller.status.events
    )


async def test_home_assistant_override_event_controls_every_override_scope(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    controller = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)

    commands = [
        {
            "channel": "temperature",
            "scope_type": "room",
            "scope_id": "climate.dining_trv",
            "override_mode": "temperature",
            "override_temperature": 22,
            "duration_minutes": 30,
        },
        {
            "channel": "temperature",
            "scope_type": "group",
            "scope_id": "Dining Room",
            "override_mode": "preset",
            "override_preset": "comfort",
            "duration_minutes": 60,
        },
        {
            "channel": "temperature",
            "scope_type": "house",
            "override_mode": "preset",
            "override_preset": "eco",
            "duration_minutes": 120,
        },
        {
            "channel": "hot_water",
            "scope_type": "hot_water",
            "override_mode": "on_off",
            "on_off": False,
            "duration_minutes": 30,
        },
    ]
    for index, command in enumerate(commands):
        await controller.handle_override_event(
            {
                **command,
                "_ha_context": {"id": f"command-{index}", "user_id": "user-1"},
            }
        )

    state = controller.get_override_state()
    assert [
        (override.scope_type, override.scope_id) for override in state.temperature_overrides
    ] == [
        ("room", "device-dining"),
        ("group", "Dining Room"),
        ("house", None),
    ]
    assert state.temperature_overrides[0].temperature == 22
    assert state.temperature_overrides[0].actor_user_id == "user-1"
    assert state.temperature_overrides[0].source == "Home Assistant automation"
    assert state.hot_water_override is not None
    assert state.hot_water_override.on is False

    await controller.handle_override_event(
        {
            "channel": "temperature",
            "scope_type": "room",
            "scope_id": "climate.dining_trv",
            "operation": "clear",
            "_ha_context": {"id": "clear-room", "user_id": "user-1"},
        }
    )

    state = controller.get_override_state()
    assert all(override.scope_type != "room" for override in state.temperature_overrides)
    assert len(controller.override_store.load_ledger().actions) == 5


async def test_home_assistant_override_event_rejects_unsafe_or_invalid_commands(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    controller = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)

    await controller.handle_override_event(
        {
            "channel": "hot_water",
            "scope_type": "hot_water",
            "override_mode": "on_off",
            "on_off": True,
        }
    )
    await controller.handle_override_event(
        {
            "channel": "temperature",
            "scope_type": "room",
            "scope_id": "climate.unknown",
            "override_mode": "preset",
            "override_preset": "boost",
            "duration_minutes": 30,
        }
    )
    await controller.handle_override_event({"channel": "temperature"})

    assert controller.override_store.load_ledger().actions == []
    messages = [event.message for event in controller.status.events]
    assert any("set actions require duration_minutes" in message for message in messages)
    assert any("Unknown room: climate.unknown" in message for message in messages)
    assert any("invalid data" in message for message in messages)


async def test_home_assistant_override_event_can_clear_all_temporary_overrides(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    controller = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)
    await controller.set_mode("away")

    commands = [
        {
            "channel": "temperature",
            "scope_type": "room",
            "scope_id": "climate.dining_trv",
            "override_mode": "preset",
            "override_preset": "boost",
            "duration_minutes": 30,
        },
        {
            "channel": "temperature",
            "scope_type": "group",
            "scope_id": "Dining Room",
            "override_mode": "preset",
            "override_preset": "comfort",
            "duration_minutes": 60,
        },
        {
            "channel": "hot_water",
            "scope_type": "hot_water",
            "override_mode": "on_off",
            "on_off": True,
            "duration_minutes": 30,
        },
    ]
    for command in commands:
        await controller.handle_override_event(command)

    await controller.handle_override_event(
        {
            "operation": "clear_all",
            "_ha_context": {"id": "clear-all", "user_id": "user-1"},
        }
    )

    state = controller.get_override_state()
    assert state.temperature_overrides == []
    assert state.hot_water_override is None
    assert state.mode == "away"
    actions = controller.override_store.load_ledger().actions
    assert len(actions) == 7
    assert [action.operation for action in actions[-3:]] == ["clear", "clear", "clear"]
    assert all(action.actor_user_id == "user-1" for action in actions[-3:])
    assert any(
        event.message == "Cleared 3 active overrides from Home Assistant automation"
        for event in controller.status.events
    )


async def test_clear_all_override_event_rejects_extra_fields(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    controller = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)
    await controller.start_boost("Dining Room", 30)

    await controller.handle_override_event({"operation": "clear_all", "scope_type": "house"})

    assert len(controller.get_override_state().temperature_overrides) == 1
    assert any(
        "clear_all event with unexpected data" in event.message
        for event in controller.status.events
    )


def test_control_plan_falls_back_to_internal_measurement(registry_snapshot):
    config = configured_system(registry_snapshot)
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_temperature"
    )["state"] = "unavailable"
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    plan = build_control_plan(config, states, at)
    room = plan.rooms[0]

    assert room.current_temperature == 20.0
    assert room.current_temperature_entity == "climate.dining_trv"
    assert "using the TRV temperature" in str(room.warning)


def test_control_plan_falls_back_when_external_measurement_is_stale(registry_snapshot):
    config = configured_system(registry_snapshot)
    states = copy.deepcopy(registry_snapshot.states)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    last_seen = next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_last_seen"
    )
    last_seen["state"] = (at - timedelta(seconds=5401)).isoformat()

    plan = build_control_plan(config, states, at)
    room = plan.rooms[0]

    assert room.current_temperature == 20.0
    assert room.external_temperature is None
    assert room.available is True
    assert room.warning == "External room sensor is stale; using the TRV temperature"


def test_stable_external_measurement_uses_fresh_device_last_seen(registry_snapshot):
    config = configured_system(registry_snapshot)
    states = copy.deepcopy(registry_snapshot.states)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    external = next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_temperature"
    )
    external["last_reported"] = (at - timedelta(hours=6)).isoformat()

    plan = build_control_plan(config, states, at)
    room = plan.rooms[0]

    assert room.current_temperature == 20.4
    assert room.external_temperature == 20.4
    assert room.external_temperature_health_entity == "sensor.dining_thermometer_last_seen"
    assert room.warning is None


def test_external_sensor_freshness_can_be_disabled(registry_snapshot):
    config = configured_system(registry_snapshot)
    config.trvs[0].external_temperature_freshness = "disabled"
    states = copy.deepcopy(registry_snapshot.states)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    last_seen = next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_last_seen"
    )
    last_seen["state"] = (at - timedelta(days=1)).isoformat()

    room = build_control_plan(config, states, at).rooms[0]

    assert room.external_temperature == 20.4
    assert room.external_temperature_freshness == "disabled"
    assert room.warning is None


def test_external_sensor_can_override_the_stale_timeout(registry_snapshot):
    config = configured_system(registry_snapshot)
    config.trvs[0].external_sensor_stale_seconds = 300
    states = copy.deepcopy(registry_snapshot.states)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    last_seen = next(
        state for state in states if state["entity_id"] == "sensor.dining_thermometer_last_seen"
    )
    last_seen["state"] = (at - timedelta(seconds=301)).isoformat()

    room = build_control_plan(config, states, at).rooms[0]

    assert room.external_temperature is None
    assert room.warning == "External room sensor is stale; using the TRV temperature"


def test_control_plan_reports_next_schedule_change(registry_snapshot):
    config = configured_system(registry_snapshot)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    room = build_control_plan(config, registry_snapshot.states, at).rooms[0]

    assert room.next_schedule_at == datetime(2026, 10, 12, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assert room.next_preset == "eco"
    assert room.next_scheduled_temperature == 16


def test_control_plan_skips_a_stale_trv(registry_snapshot):
    config = configured_system(registry_snapshot)
    states = copy.deepcopy(registry_snapshot.states)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    states.append(
        {
            "entity_id": "sensor.dining_trv_last_seen",
            "state": (at - timedelta(seconds=3601)).isoformat(),
            "attributes": {},
        }
    )

    plan = build_control_plan(config, states, at)
    room = plan.rooms[0]

    assert room.available is False
    assert room.demand is False
    assert room.status == "stale"
    assert room.warning == "TRV has not reported recently and this room is being skipped"
    assert plan.boiler_should_be_on is False


class RecordingHomeAssistant:
    def __init__(self, states, *, apply_commands: bool = True):
        self.states = copy.deepcopy(states)
        self.calls = []
        self.apply_commands = apply_commands

    async def get_states(self):
        return self.states

    async def get_time_zone(self):
        return "Europe/London"

    async def call_service(self, domain, service, data):
        self.calls.append((domain, service, data))
        if not self.apply_commands or domain == "persistent_notification":
            return []
        state = next(
            (item for item in self.states if item["entity_id"] == data.get("entity_id")),
            None,
        )
        if state is None:
            return []
        if domain == "climate" and service == "set_hvac_mode":
            state["state"] = data["hvac_mode"]
        elif domain == "climate" and service == "set_temperature":
            state["attributes"]["temperature"] = data["temperature"]
        elif domain == "switch" and service in {"turn_on", "turn_off"}:
            state["state"] = "on" if service == "turn_on" else "off"
        elif domain == "select" and service == "select_option":
            state["state"] = data["option"]
        elif domain == "number" and service == "set_value":
            state["state"] = str(data["value"])
        return []


class BrokenNotifier:
    async def reconcile(self, settings, issues, *, at=None):
        raise RuntimeError("Discord exploded")

    async def notify_fault(self, settings, reason, *, paused=True):
        del paused
        raise RuntimeError("Discord exploded")

    async def resolve_fault(self, settings, *, paused=True):
        del paused
        raise RuntimeError("Discord exploded")


class BrokenMetricsStore:
    def record_cycle(self, **kwargs):
        del kwargs
        raise sqlite3.OperationalError("disk is unavailable")


def test_relay_unavailability_notification_uses_restart_grace(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot)
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state
        for state in states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    )["state"] = "unavailable"
    plan = build_control_plan(
        config,
        states,
        datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")),
    )
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    controller = HeatingController(RecordingHomeAssistant(states), store)

    issues = controller.safety.notification_issues(config, states, plan)
    relay_issue = next(
        issue for issue in issues if issue.key == "relay:central-heating:unavailable"
    )

    assert relay_issue.severity == "critical"
    assert relay_issue.notification_delay_seconds == 90


async def test_away_mode_turns_hot_water_off_and_home_restores_its_schedule(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    states = copy.deepcopy(registry_snapshot.states)
    hot_water = next(
        state for state in states if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
    )
    hot_water["state"] = "on"
    ha = RecordingHomeAssistant(states)
    controller = HeatingController(ha, store)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    await controller.set_mode("away")

    assert controller.get_override_state().mode == "away"
    assert (
        next(
            state
            for state in ha.states
            if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
        )["state"]
        == "off"
    )
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) in ha.calls

    ha.calls = []
    await controller.start_hot_water_boost(30)

    assert controller.get_override_state().mode == "away"
    assert controller.get_override_state().hot_water_boost is not None
    assert (
        next(
            state
            for state in ha.states
            if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
        )["state"]
        == "on"
    )

    ha.calls = []
    await controller.cancel_hot_water_boost()

    assert controller.get_override_state().mode == "away"
    assert controller.get_override_state().hot_water_boost is None
    assert (
        next(
            state
            for state in ha.states
            if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
        )["state"]
        == "off"
    )

    ha.calls = []
    await controller.set_mode("home")
    plan = await controller.run_once(at)

    assert plan is not None
    assert plan.hot_water_should_be_on is True
    assert (
        next(
            state
            for state in ha.states
            if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
        )["state"]
        == "on"
    )
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) in ha.calls


async def test_notification_failure_does_not_fail_control_cycle(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    controller = HeatingController(
        RecordingHomeAssistant(registry_snapshot.states),
        store,
        notifier=BrokenNotifier(),
    )

    plan = await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    assert plan is not None
    assert controller.status.last_error is None
    assert controller.status.fault_latched is False


async def test_metrics_failure_does_not_fail_control_cycle(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot)
    config.control.predictive_heating_enabled = True
    config.trvs[0].predictive_heating_enabled = True
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    controller = HeatingController(
        RecordingHomeAssistant(registry_snapshot.states),
        store,
        metrics_store=BrokenMetricsStore(),
    )

    plan = await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))
    fallback_plan = await controller.run_once(
        datetime(2026, 10, 12, 7, 1, tzinfo=ZoneInfo("Europe/London"))
    )

    assert plan is not None
    assert fallback_plan is not None
    assert fallback_plan.rooms[0].thermal_model_status == "learning"
    assert "Metrics storage is unavailable" in str(controller.predictive_learning_error)
    assert controller.status.last_error is None
    assert controller.status.fault_latched is False
    assert any(
        "Metrics storage failed; heating control is unaffected" in event.message
        for event in controller.status.events
    )


async def test_disabled_controller_only_calculates(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)

    plan = await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    assert plan is not None
    assert plan.rooms[0].commanded_temperature == 23
    assert ha.calls == []
    assert controller.status.last_successful_cycle_at is not None
    assert controller.status.events[-1].message == "Controller started in monitoring-only mode"

    diagnostics = build_diagnostics(config, registry_snapshot.states, controller)
    assert diagnostics["overall_status"] == "monitoring"
    assert diagnostics["controller"]["loop_status"] == "healthy"
    assert diagnostics["plant"]["boiler"] == {
        "configured": True,
        "entity_id": "switch.boiler_switch_central_heating_switch",
        "requested": True,
        "actual": False,
    }
    room = diagnostics["rooms"][0]
    assert room["temperature_source"] == "external"
    assert room["external_temperature"] == 20.4
    assert room["trv_temperature"] == 20.0
    assert room["schedule_preset"] == "comfort"
    assert room["schedule_temperature"] == 21
    assert room["temperature_reason"] == ("The room schedule currently selects Comfort (21°C).")
    assert "lower hysteresis threshold" in room["demand_reason"]
    assert "heating offset" in room["command_reason"]
    assert diagnostics["events"][0]["message"] == "Controller started in monitoring-only mode"


async def test_disabled_controller_turns_off_a_relay_found_on_at_startup(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state
        for state in states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    )["state"] = "on"
    ha = RecordingHomeAssistant(states)
    controller = HeatingController(ha, store)

    await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) in ha.calls


async def test_enabled_controller_reconciles_internal_trv_settings_without_external_sync(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)

    await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    assert (
        "climate",
        "set_temperature",
        {"entity_id": "climate.dining_trv", "temperature": 23.0},
    ) in ha.calls
    assert (
        "select",
        "select_option",
        {
            "entity_id": "select.dining_trv_temperature_sensor_select",
            "option": "local_temperature",
        },
    ) in ha.calls
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.dining_trv_smart_temperature_control"},
    ) in ha.calls
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) in ha.calls
    assert all(
        call[2].get("entity_id") != "number.dining_trv_external_temperature_input"
        for call in ha.calls
    )
    assert any(event.message == "Turned central heating on" for event in controller.status.events)


async def test_startup_isolates_unavailable_relay_without_disabling_control(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    states = copy.deepcopy(registry_snapshot.states)
    relay = next(
        state
        for state in states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    )
    relay["state"] = "unknown"
    ha = RecordingHomeAssistant(states)
    controller = HeatingController(ha, store)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    await controller.run_once(at)

    stored = store.load()
    assert stored.control.enabled is True
    assert stored.safety.fault_latched is False
    assert controller.status.fault_latched is False
    assert controller.status.consecutive_failures == 1
    assert controller.status.waiting_for_readiness is False
    assert "central-heating relay" in str(controller.status.last_error)
    assert any(call[:2] == ("climate", "set_temperature") for call in ha.calls)
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) in ha.calls

    diagnostics = build_diagnostics(
        stored,
        ha.states,
        controller,
        at=controller.status.last_cycle_finished_at,
    )
    assert diagnostics["overall_status"] == "error"
    assert diagnostics["controller"]["loop_status"] == "error"

    next(
        state
        for state in ha.states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    )["state"] = "off"
    await controller.run_once(at)

    assert controller.status.last_error is None
    assert controller.status.waiting_for_readiness is False
    assert controller.status.consecutive_failures == 0
    assert store.load().control.enabled is True
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls


async def test_home_assistant_restart_reenters_readiness_without_latching(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    config.control.failures_before_alert = 1
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)

    class RestartingHomeAssistant(RecordingHomeAssistant):
        fail_states = False

        async def get_states(self):
            if self.fail_states:
                raise HomeAssistantError("502: Bad Gateway")
            return await super().get_states()

    ha = RestartingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    await controller.run_once(at)
    assert controller.status.last_error is None
    assert controller.status.waiting_for_readiness is False

    ha.fail_states = True
    await controller.run_once(at)
    await controller.run_once(at)

    assert store.load().control.enabled is True
    assert store.load().safety.fault_latched is False
    assert controller.status.consecutive_failures == 0
    assert controller.status.waiting_for_readiness is True
    assert "Waiting for Home Assistant" in str(controller.status.last_error)

    ha.fail_states = False
    await controller.run_once(at)

    assert controller.status.last_error is None
    assert controller.status.waiting_for_readiness is False
    assert controller.status.fault_latched is False


async def test_controller_can_start_heating_without_a_hot_water_switch(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=True)
    config.hot_water_switch = None
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)

    await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls


async def test_disabling_controller_turns_off_relays(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=True)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))
    await controller.run_once(at)

    states = copy.deepcopy(registry_snapshot.states)
    for state in states:
        if state["entity_id"] in {
            "switch.boiler_switch_central_heating_switch",
            "switch.boiler_switch_hot_water_switch",
        }:
            state["state"] = "on"
    ha.states = states
    ha.calls = []
    config.control.enabled = False
    store.save(config)

    await controller.run_once(at)

    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) in ha.calls


def test_preflight_warns_but_allows_an_unavailable_trv(registry_snapshot):
    config = configured_system(registry_snapshot, enabled=True)
    states = copy.deepcopy(registry_snapshot.states)
    next(state for state in states if state["entity_id"] == "climate.dining_trv")["state"] = (
        "unavailable"
    )

    result = build_preflight(config, states)

    assert result.ok is True
    assert any(
        check.key == "trv:device-dining"
        and not check.passed
        and check.severity == "warning"
        and "will not count towards heat demand" in check.detail
        for check in result.checks
    )


async def test_unavailable_trv_is_skipped_while_other_rooms_continue(tmp_path):
    snapshot = build_snapshot("healthy")
    config = materialise_config(
        OnboardingRequest.model_validate(
            {
                "trvs": [
                    {
                        "device_id": "mock-kitchen-trv",
                        "room_group": "Downstairs",
                        "external_temperature_sensor": "sensor.kitchen_thermometer_temperature",
                    },
                    {
                        "device_id": "mock-dining-trv",
                        "room_group": "Downstairs",
                        "external_temperature_sensor": "sensor.dining_thermometer_temperature",
                    },
                ]
            }
        ),
        discover(snapshot),
    )
    config.boiler_switch = "switch.boiler_switch_central_heating_switch"
    config.minimum_heat_requests = 1
    config.control = ControlSettings(
        enabled=True,
        minimum_boiler_on_seconds=0,
        minimum_boiler_off_seconds=0,
    )
    config.setup = SetupProgress(
        devices=True, boiler=True, control=True, presets=True, schedules=True
    )
    config.schedules = [RoomSchedule(room_group="Downstairs", default_preset="comfort")]
    states = copy.deepcopy(snapshot.states)
    next(state for state in states if state["entity_id"] == "climate.kitchen_trv")["state"] = (
        "unavailable"
    )
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(states)
    controller = HeatingController(ha, store)

    plan = await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    assert plan is not None
    rooms = {room.device_id: room for room in plan.rooms}
    assert rooms["mock-kitchen-trv"].available is False
    assert rooms["mock-kitchen-trv"].demand is False
    assert rooms["mock-dining-trv"].available is True
    assert rooms["mock-dining-trv"].demand is True
    assert plan.heat_requests == 1
    assert plan.boiler_should_be_on is True
    assert controller.status.fault_latched is False
    assert all("kitchen_trv" not in str(call[2].get("entity_id")) for call in ha.calls)
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls
    diagnostics = build_diagnostics(config, ha.states, controller)
    issue = next(issue for issue in diagnostics["issues"] if "Kitchen" in issue["title"])
    assert issue["severity"] == "warning"
    assert "does not count towards heat demand" in issue["detail"]


async def test_unconfirmed_commands_alert_and_keep_retrying(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=True)
    config.control.command_retries = 1
    config.control.failures_before_alert = 2
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states, apply_commands=False)

    async def no_wait(_: float) -> None:
        await asyncio.sleep(0)

    controller = HeatingController(ha, store, sleep=no_wait)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    await controller.run_once(at)
    assert controller.status.consecutive_failures == 1
    assert store.load().control.enabled is True

    await controller.run_once(at)

    stored = store.load()
    assert stored.control.enabled is True
    assert stored.safety.fault_latched is False
    assert controller.status.fault_latched is False
    assert "did not confirm" in str(controller.status.control_alert_reason)
    temperature_calls = [call for call in ha.calls if call[:2] == ("climate", "set_temperature")]
    assert len(temperature_calls) == 4
    assert any(
        call[:2] == ("persistent_notification", "create")
        and call[2]["notification_id"] == "heat_steward_control_problem"
        for call in ha.calls
    )

    ha.apply_commands = True
    await controller.run_once(at)

    assert controller.status.control_alert_reason is None
    assert controller.status.consecutive_failures == 0
    assert store.load().control.enabled is True


async def test_success_after_restart_clears_a_persisted_control_problem(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    config.control.command_retries = 0
    config.control.failures_before_alert = 1
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states, apply_commands=False)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    first = HeatingController(ha, store)
    await first.run_once(at)
    assert first.status.control_alert_reason is not None
    assert any(
        call[:2] == ("persistent_notification", "create")
        and call[2]["notification_id"] == "heat_steward_control_problem"
        for call in ha.calls
    )

    ha.apply_commands = True
    ha.calls = []
    restarted = HeatingController(ha, store)
    await restarted.run_once(at)

    assert restarted.status.control_alert_reason is None
    assert restarted.status.last_error is None
    assert (
        "persistent_notification",
        "dismiss",
        {"notification_id": "heat_steward_control_problem"},
    ) in ha.calls


async def test_command_confirmation_waits_for_a_delayed_trv_update(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=True)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)

    class DelayedHomeAssistant(RecordingHomeAssistant):
        def __init__(self, states):
            super().__init__(states)
            self.reads_after_command = 0

        async def call_service(self, domain, service, data):
            if domain == "climate" and service == "set_temperature":
                self.calls.append((domain, service, data))
                return []
            return await super().call_service(domain, service, data)

        async def get_states(self):
            if any(call[:2] == ("climate", "set_temperature") for call in self.calls):
                self.reads_after_command += 1
                if self.reads_after_command == 4:
                    climate = next(
                        state for state in self.states if state["entity_id"] == "climate.dining_trv"
                    )
                    climate["attributes"]["temperature"] = 23
            return self.states

    waits: list[float] = []

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    ha = DelayedHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store, sleep=record_wait)

    await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    temperature_calls = [call for call in ha.calls if call[:2] == ("climate", "set_temperature")]
    assert len(temperature_calls) == 1
    assert waits[:3] == [0.5, 0.5, 0.5]
    assert controller.status.consecutive_failures == 0


async def test_unavailable_relay_is_isolated_and_recovers_without_disabling_control(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    config.control.failures_before_alert = 3
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    await controller.run_once(at)
    relay = next(
        state
        for state in ha.states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    )
    relay["state"] = "unavailable"
    climate = next(state for state in ha.states if state["entity_id"] == "climate.dining_trv")
    climate["attributes"]["temperature"] = 16
    hot_water = next(
        state
        for state in ha.states
        if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
    )
    hot_water["state"] = "off"
    ha.calls = []

    await controller.run_once(at)
    await controller.run_once(at)
    assert store.load().control.enabled is True
    assert controller.status.consecutive_failures == 2

    await controller.run_once(at)

    stored = store.load()
    assert stored.control.enabled is True
    assert stored.safety.fault_latched is False
    assert controller.status.fault_latched is False
    assert "central-heating relay" in str(controller.status.control_alert_reason)
    assert any(call[:2] == ("climate", "set_temperature") for call in ha.calls)
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) in ha.calls

    relay["state"] = "off"
    await controller.run_once(at)

    assert controller.status.control_alert_reason is None
    assert controller.status.consecutive_failures == 0
    assert store.load().control.enabled is True
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls


async def test_unavailable_hot_water_relay_does_not_block_heating_or_trvs(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)
    at = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London"))

    await controller.run_once(at)
    next(
        state
        for state in ha.states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    )["state"] = "off"
    next(
        state
        for state in ha.states
        if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
    )["state"] = "unavailable"
    next(state for state in ha.states if state["entity_id"] == "climate.dining_trv")["attributes"][
        "temperature"
    ] = 16
    ha.calls = []

    await controller.run_once(at)

    assert store.load().control.enabled is True
    assert controller.status.fault_latched is False
    assert any(call[:2] == ("climate", "set_temperature") for call in ha.calls)
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls


async def test_control_loop_recovers_from_two_unexpected_failures(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)

    async def no_wait(_: float) -> None:
        await asyncio.sleep(0)

    class RecoveringController(HeatingController):
        attempts = 0

        async def run_once(self, at=None):
            del at
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("synthetic controller crash")
            return None

    controller = RecoveringController(
        RecordingHomeAssistant(registry_snapshot.states), store, sleep=no_wait
    )

    await controller.run_forever(maximum_cycles=1)

    assert controller.attempts == 3
    assert controller.status.loop_restarts == 2
    assert controller.status.loop_running is False
    assert any("synthetic controller crash" in event.message for event in controller.status.events)


async def test_control_loop_exits_after_three_consecutive_crashes(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)

    async def no_wait(_: float) -> None:
        await asyncio.sleep(0)

    class CrashingController(HeatingController):
        async def run_once(self, at=None):
            del at
            raise RuntimeError("persistent synthetic crash")

    controller = CrashingController(
        RecordingHomeAssistant(registry_snapshot.states), store, sleep=no_wait
    )

    with pytest.raises(RuntimeError, match="Supervisor restart"):
        await controller.run_forever(maximum_cycles=1)

    assert controller.status.loop_restarts == 3
    assert controller.status.loop_running is False


async def test_event_history_survives_controller_restart(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=False)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)

    first = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)
    await first.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))
    second = HeatingController(RecordingHomeAssistant(registry_snapshot.states), store)

    assert any(
        event.message == "Controller started in monitoring-only mode"
        for event in second.status.events
    )


async def test_emergency_stop_persists_disabled_before_turning_relays_off(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    states = copy.deepcopy(registry_snapshot.states)
    for state in states:
        if state["entity_id"] in {
            "switch.boiler_switch_central_heating_switch",
            "switch.boiler_switch_hot_water_switch",
        }:
            state["state"] = "on"
    ha = RecordingHomeAssistant(states)
    controller = HeatingController(ha, store)

    confirmed = await controller.emergency_stop()

    assert confirmed is True
    assert store.load().control.enabled is False
    assert (
        next(
            state
            for state in ha.states
            if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
        )["state"]
        == "off"
    )
    assert (
        next(
            state
            for state in ha.states
            if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
        )["state"]
        == "off"
    )


async def test_acknowledging_a_legacy_fault_does_not_override_an_explicit_disabled_state(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=False)
    config.safety.fault_latched = True
    config.safety.fault_reason = "Test safety stop"
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    ha = RecordingHomeAssistant(registry_snapshot.states)
    controller = HeatingController(ha, store)

    await controller.acknowledge_fault()

    stored = store.load()
    assert stored.control.enabled is False
    assert stored.safety.fault_latched is False
    assert any(call[:2] == ("persistent_notification", "dismiss") for call in ha.calls)


async def test_maximum_continuous_boiler_runtime_latches_a_fault(tmp_path, registry_snapshot):
    config = configured_system(registry_snapshot, enabled=True)
    config.control.maximum_boiler_on_seconds = 300
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state
        for state in states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    ).update(
        {
            "state": "on",
            "last_changed": (datetime.now().astimezone() - timedelta(seconds=301)).isoformat(),
        }
    )
    ha = RecordingHomeAssistant(states)
    next(
        state
        for state in ha.states
        if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
    )["state"] = "on"
    controller = HeatingController(ha, store)

    await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    stored = store.load()
    assert stored.control.enabled is True
    assert stored.safety.fault_latched is True
    assert "maximum continuous runtime" in str(stored.safety.fault_reason)
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) not in ha.calls

    await controller.acknowledge_fault()
    await controller.run_once(datetime(2026, 10, 12, 7, 1, tzinfo=ZoneInfo("Europe/London")))

    recovered = store.load()
    assert recovered.control.enabled is True
    assert recovered.safety.fault_latched is False
    assert controller.status.fault_latched is False
    assert (
        "switch",
        "turn_on",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls


async def test_persisted_safety_stop_reasserts_boiler_off_after_restart(
    tmp_path, registry_snapshot
):
    config = configured_system(registry_snapshot, enabled=True)
    config.safety.fault_latched = True
    config.safety.fault_reason = "Maximum runtime safety stop"
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    store.save(config)
    states = copy.deepcopy(registry_snapshot.states)
    next(
        state
        for state in states
        if state["entity_id"] == "switch.boiler_switch_central_heating_switch"
    )["state"] = "on"
    next(
        state for state in states if state["entity_id"] == "switch.boiler_switch_hot_water_switch"
    )["state"] = "on"
    ha = RecordingHomeAssistant(states)
    controller = HeatingController(ha, store)

    await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))

    assert store.load().control.enabled is True
    assert controller.status.fault_latched is True
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_central_heating_switch"},
    ) in ha.calls
    assert (
        "switch",
        "turn_off",
        {"entity_id": "switch.boiler_switch_hot_water_switch"},
    ) not in ha.calls
