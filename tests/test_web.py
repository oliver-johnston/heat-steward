from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from heat_steward.config import ConfigStore
from heat_steward.control import HeatingController
from heat_steward.predictive import RoomThermalModel
from heat_steward.web import WebApplication


class FakeHomeAssistant:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    async def get_registry_snapshot(self):
        return self.snapshot

    async def get_states(self):
        return self.snapshot.states

    async def get_time_zone(self):
        return "Europe/London"

    async def call_service(self, domain, service, data):
        self.calls.append((domain, service, data))
        if domain == "persistent_notification":
            return []
        state = next(
            (item for item in self.snapshot.states if item["entity_id"] == data.get("entity_id")),
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


class FakeNotifier:
    def __init__(self):
        self.tests = 0

    async def send_test(self, settings):
        assert settings.discord_webhook_url
        self.tests += 1

    async def reconcile(self, settings, issues, *, at=None):
        pass

    async def notify_fault(self, settings, reason, *, paused=True):
        del paused
        return True

    async def resolve_fault(self, settings, *, paused=True):
        del paused
        pass


class FixedThermalLearner:
    def __init__(self, models: dict[str, RoomThermalModel]):
        self.fixed_models = models

    def models(self, device_ids, *, at):
        del at
        return {
            device_id: self.fixed_models[device_id]
            for device_id in device_ids
            if device_id in self.fixed_models
        }


async def test_onboarding_api_discovers_and_saves(
    aiohttp_client, monkeypatch, tmp_path, registry_snapshot
):
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    ha = FakeHomeAssistant(registry_snapshot)
    notifier = FakeNotifier()
    controller = HeatingController(ha, store, notifier=notifier)
    app = WebApplication(ha, store, controller=controller).create()
    client = await aiohttp_client(app)

    discovery_response = await client.get("/api/onboarding/discovery")
    assert discovery_response.status == 200
    discovery = await discovery_response.json()
    assert discovery["trvs"][0]["name"] == "Dining TRV"

    save_response = await client.put(
        "/api/onboarding",
        json={
            "trvs": [
                {
                    "device_id": "device-dining",
                    "room_group": "Dining Room",
                    "external_temperature_sensor": "sensor.dining_thermometer_temperature",
                }
            ],
        },
    )
    assert save_response.status == 200
    assert store.load().trvs[0].entities.climate == "climate.dining_trv"

    boiler_response = await client.put(
        "/api/boiler",
        json={
            "boiler_switch": "switch.boiler_switch_central_heating_switch",
            "hot_water_switch": "switch.boiler_switch_hot_water_switch",
            "minimum_heat_requests": 1,
        },
    )
    assert boiler_response.status == 200
    boiler_config = (await boiler_response.json())["config"]
    assert boiler_config["setup"]["boiler"] is True
    assert boiler_config["minimum_heat_requests"] == 1

    control_response = await client.put(
        "/api/control",
        json={
            "enabled": False,
            "cycle_interval_seconds": 30,
            "heating_offset": 2,
            "idle_offset": 2,
            "hysteresis": 0.2,
            "trv_mode": "heat",
            "temperature_source": "internal",
            "smart_temperature_control": False,
            "open_window_detection": False,
            "child_lock": False,
            "frost_protection_temperature": 5,
            "minimum_boiler_on_seconds": 300,
            "minimum_boiler_off_seconds": 300,
        },
    )
    assert control_response.status == 200
    control_config = (await control_response.json())["config"]
    assert control_config["setup"]["control"] is True
    assert control_config["control"]["temperature_source"] == "internal"

    presets_response = await client.put(
        "/api/presets",
        json={"boost": 24, "comfort": 20.5, "eco": 15, "frost": 6},
    )
    assert presets_response.status == 200
    assert (await presets_response.json())["config"]["setup"]["presets"] is True
    assert store.load().presets.model_dump() == {
        "boost": 24.0,
        "comfort": 20.5,
        "eco": 15.0,
        "frost": 6.0,
    }

    invalid_presets_response = await client.put(
        "/api/presets",
        json={"boost": 23, "comfort": 21, "eco": 16, "frost": 20},
    )
    assert invalid_presets_response.status == 400
    invalid_presets = await invalid_presets_response.json()
    assert invalid_presets["error"] == "Presets are invalid"
    assert "descend from Boost to Frost" in invalid_presets["details"][0]["msg"]

    invalid_schedules_response = await client.put(
        "/api/schedules",
        json={
            "schedules": [
                {
                    "room_group": "Dining Room",
                    "default_preset": "eco",
                    "periods": [
                        {
                            "days": ["monday"],
                            "start": "06:30",
                            "end": "09:00",
                            "preset": "comfort",
                        },
                        {
                            "days": ["monday"],
                            "start": "08:30",
                            "end": "10:00",
                            "preset": "boost",
                        },
                    ],
                }
            ],
        },
    )
    assert invalid_schedules_response.status == 400
    invalid_schedules = await invalid_schedules_response.json()
    assert invalid_schedules["error"] == "Schedules are invalid"
    assert "overlap on Monday" in invalid_schedules["details"][0]["msg"]

    schedules_response = await client.put(
        "/api/schedules",
        json={
            "schedules": [
                {
                    "room_group": "Dining Room",
                    "default_preset": "eco",
                    "periods": [
                        {
                            "days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                            "start": "06:30",
                            "end": "08:30",
                            "preset": "comfort",
                        }
                    ],
                }
            ],
            "hot_water_schedule": {
                "default_state": "off",
                "periods": [
                    {
                        "days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                        "start": "06:00",
                        "end": "07:30",
                        "state": "on",
                    }
                ],
            },
        },
    )
    assert schedules_response.status == 200
    assert (await schedules_response.json())["config"]["setup"]["schedules"] is True
    assert store.load().schedules[0].periods[0].preset == "comfort"
    assert store.load().hot_water_schedule.default_state == "off"
    assert store.load().hot_water_schedule.periods[0].state == "on"

    room_override_response = await client.post(
        "/api/overrides/actions",
        json={
            "channel": "temperature",
            "scope_type": "room",
            "scope_id": "device-dining",
            "override_mode": "temperature",
            "override_temperature": 22,
            "duration_minutes": 30,
        },
        headers={
            "X-Remote-User-Id": "user-1",
            "X-Remote-User-Display-Name": "Oliver",
            "Idempotency-Key": "room-override-1",
        },
    )
    assert room_override_response.status == 200
    room_override = await room_override_response.json()
    assert room_override["overrides"]["temperature_overrides"][0]["temperature"] == 22

    actions_response = await client.get("/api/overrides/actions")
    assert actions_response.status == 200
    actions = (await actions_response.json())["actions"]
    assert actions[0]["action_id"] == "room-override-1"
    assert actions[0]["actor_user_id"] == "user-1"
    assert actions[0]["actor_name"] == "Oliver"

    clear_room_override_response = await client.post(
        "/api/overrides/actions",
        json={
            "channel": "temperature",
            "scope_type": "room",
            "scope_id": "device-dining",
            "operation": "clear",
        },
    )
    assert clear_room_override_response.status == 200
    assert (await clear_room_override_response.json())["overrides"]["temperature_overrides"] == []

    away_response = await client.put("/api/mode", json={"mode": "away"})
    assert away_response.status == 200
    assert (await away_response.json())["overrides"]["mode"] == "away"

    hot_water_boost_response = await client.post(
        "/api/hot-water/boost",
        json={"duration_minutes": 60},
    )
    assert hot_water_boost_response.status == 200
    assert (await hot_water_boost_response.json())["overrides"]["hot_water_boost"] is not None

    hot_water_dashboard_response = await client.get("/api/dashboard")
    assert hot_water_dashboard_response.status == 200
    hot_water_dashboard = await hot_water_dashboard_response.json()
    assert hot_water_dashboard["overrides"]["mode"] == "away"
    assert hot_water_dashboard["overrides"]["hot_water_boost"] is not None
    assert hot_water_dashboard["controller"]["hot_water_should_be_on"] is True

    invalid_hot_water_boost_response = await client.post(
        "/api/hot-water/boost",
        json={"duration_minutes": 45},
    )
    assert invalid_hot_water_boost_response.status == 400

    cancel_hot_water_boost_response = await client.post("/api/hot-water/boost/cancel")
    assert cancel_hot_water_boost_response.status == 200
    assert (await cancel_hot_water_boost_response.json())["overrides"]["hot_water_boost"] is None

    hot_water_away_dashboard_response = await client.get("/api/dashboard")
    assert hot_water_away_dashboard_response.status == 200
    hot_water_away_dashboard = await hot_water_away_dashboard_response.json()
    assert hot_water_away_dashboard["controller"]["hot_water_should_be_on"] is False

    boost_response = await client.post(
        "/api/boost",
        json={"room_group": "Dining Room", "duration_minutes": 120},
    )
    assert boost_response.status == 200
    assert (await boost_response.json())["overrides"]["boosts"][0]["room_group"] == "Dining Room"

    dashboard_with_boost_response = await client.get("/api/dashboard")
    assert dashboard_with_boost_response.status == 200
    dashboard_with_boost = await dashboard_with_boost_response.json()
    assert dashboard_with_boost["overrides"]["mode"] == "away"
    assert dashboard_with_boost["groups"][0]["rooms"][0]["control_source"] == "boost"
    assert dashboard_with_boost["groups"][0]["rooms"][0]["preset"] == "boost"

    invalid_boost_response = await client.post(
        "/api/boost",
        json={"room_group": "Dining Room", "duration_minutes": 45},
    )
    assert invalid_boost_response.status == 400

    cancel_boost_response = await client.post(
        "/api/boost/cancel",
        json={"room_group": "Dining Room"},
    )
    assert cancel_boost_response.status == 200
    assert (await cancel_boost_response.json())["overrides"]["boosts"] == []

    dashboard_away_response = await client.get("/api/dashboard")
    assert dashboard_away_response.status == 200
    dashboard_away = await dashboard_away_response.json()
    assert dashboard_away["groups"][0]["rooms"][0]["control_source"] == "away"
    assert dashboard_away["groups"][0]["rooms"][0]["preset"] == "frost"

    home_response = await client.put("/api/mode", json={"mode": "home"})
    assert home_response.status == 200
    assert (await home_response.json())["overrides"]["mode"] == "home"

    notification_response = await client.put(
        "/api/notifications",
        json={
            "enabled": True,
            "discord_webhook_url": "https://discord.com/api/webhooks/123456/secret-token",
            "level": "warning",
            "send_recovery": True,
            "warning_delay_seconds": 600,
        },
    )
    assert notification_response.status == 200
    public_notifications = (await notification_response.json())["config"]["notifications"]
    assert public_notifications["discord_webhook_configured"] is True
    assert "discord_webhook_url" not in public_notifications
    assert store.load().notifications.discord_webhook_url.endswith("secret-token")

    invalid_notification_response = await client.put(
        "/api/notifications",
        json={
            "enabled": True,
            "discord_webhook_url": "https://example.com/api/webhooks/123/do-not-echo",
            "level": "critical",
            "send_recovery": True,
            "warning_delay_seconds": 900,
        },
    )
    assert invalid_notification_response.status == 400
    assert "do-not-echo" not in await invalid_notification_response.text()

    test_notification_response = await client.post("/api/notifications/test")
    assert test_notification_response.status == 200
    assert notifier.tests == 1

    await controller.run_once(datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("Europe/London")))
    diagnostics_response = await client.get("/api/diagnostics")
    assert diagnostics_response.status == 200
    diagnostics = await diagnostics_response.json()
    assert diagnostics["overall_status"] == "monitoring"
    assert diagnostics["controller"]["loop_status"] == "healthy"
    assert diagnostics["metrics"]["healthy"] is True
    assert diagnostics["metrics"]["cycle_count"] >= 1
    assert diagnostics["metrics"]["room_sample_count"] >= 1
    assert diagnostics["rooms"][0]["name"] == "Dining Room"
    assert "valve_opening_degree" in diagnostics["rooms"][0]
    assert any(
        event["message"] == "Controller started in monitoring-only mode"
        for event in diagnostics["events"]
    )

    class HistoryDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 12, 7, 1, tzinfo=ZoneInfo("Europe/London"))
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr("heat_steward.web_routes.diagnostics.datetime", HistoryDatetime)
    history_response = await client.get("/api/diagnostics/history?hours=24&device_id=device-dining")
    assert history_response.status == 200
    history = await history_response.json()
    assert history["hours"] == 24
    assert history["bucket_seconds"] == 120
    assert history["rooms"] == [
        {
            "device_id": "device-dining",
            "name": "Dining Room",
            "room_group": "Dining Room",
            "climate_entity": "climate.dining_trv",
        }
    ]
    assert history["samples"][0]["device_id"] == "device-dining"
    assert history["samples"][0]["actual_temperature"] == 20.4
    assert history["samples"][0]["target_temperature"] == 20.5
    assert isinstance(history["samples"][0]["heating"], bool)

    invalid_history_response = await client.get(
        "/api/diagnostics/history?hours=24&device_id=unknown-device"
    )
    assert invalid_history_response.status == 400

    export_response = await client.get("/api/diagnostics/export")
    assert export_response.status == 200
    assert "attachment" in export_response.headers["Content-Disposition"]
    exported = await export_response.json()
    assert "discord_webhook_url" not in exported["configuration"]["notifications"]
    assert exported["configuration"]["trvs"][0]["device_id"] == "device-dining"
    assert exported["diagnostics"]["rooms"][0]["name"] == "Dining Room"
    exported_ids = {state["entity_id"] for state in exported["configured_entity_states"]}
    assert "climate.dining_trv" in exported_ids
    assert "sensor.living_thermometer_temperature" not in exported_ids

    dashboard_response = await client.get("/api/dashboard")
    assert dashboard_response.status == 200
    dashboard = await dashboard_response.json()
    assert dashboard["groups"][0]["name"] == "Dining Room"
    assert dashboard["controller"]["hot_water_next_state"] == "off"
    assert dashboard["controller"]["hot_water_next_change_at"].startswith("2026-10-12T07:30:00")
    room = dashboard["groups"][0]["rooms"][0]
    assert room["device_id"] == "device-dining"
    assert room["room_group"] == "Dining Room"
    assert room["current_temperature"] == 20.4
    assert room["external_temperature"] == 20.4
    assert room["trv_temperature"] == 20.0
    assert room["target_temperature"] == 21.0
    assert room["preset"] in {"boost", "comfort", "eco", "frost"}
    offset = 2 if room["demand"] else -2
    assert room["commanded_temperature"] == max(
        4, min(35, round((room["scheduled_temperature"] + offset) * 2) / 2)
    )
    assert room["available"] is True
    assert room["predictive_heating_available"] is False
    assert room["predictive_heating_enabled"] is False
    assert dashboard["controller"]["enabled"] is False
    assert dashboard["controller"]["fault_latched"] is False
    assert dashboard["issues"] == []

    enabled_control = {
        "enabled": True,
        "cycle_interval_seconds": 30,
        "heating_offset": 2,
        "idle_offset": 2,
        "hysteresis": 0.2,
        "trv_mode": "heat",
        "temperature_source": "internal",
        "smart_temperature_control": False,
        "open_window_detection": False,
        "child_lock": False,
        "frost_protection_temperature": 5,
        "minimum_boiler_on_seconds": 0,
        "minimum_boiler_off_seconds": 0,
        "maximum_boiler_on_seconds": 14400,
        "command_retries": 1,
        "failures_before_alert": 2,
        "predictive_heating_enabled": True,
        "maximum_preheat_seconds": 7200,
    }
    next(
        state
        for state in ha.snapshot.states
        if state["entity_id"] == "sensor.dining_thermometer_last_seen"
    )["state"] = datetime.now().astimezone().isoformat()
    preflight_response = await client.post("/api/control/preflight", json=enabled_control)
    assert preflight_response.status == 200
    preflight = await preflight_response.json()
    assert preflight["ok"] is True
    assert all(check["passed"] for check in preflight["checks"])

    enable_response = await client.put("/api/control", json=enabled_control)
    assert enable_response.status == 200
    enabled_config = (await enable_response.json())["config"]["control"]
    assert enabled_config["enabled"] is True
    assert enabled_config["predictive_heating_enabled"] is True

    schedule_change_response = await client.put(
        "/api/schedules",
        json={
            "schedules": [
                {
                    "room_group": "Dining Room",
                    "default_preset": "comfort",
                    "periods": [
                        {
                            "days": [
                                "monday",
                                "tuesday",
                                "wednesday",
                                "thursday",
                                "friday",
                            ],
                            "start": "06:30",
                            "end": "08:30",
                            "preset": "comfort",
                        }
                    ],
                }
            ],
            "hot_water_schedule": {
                "default_state": "off",
                "periods": [
                    {
                        "days": [
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                        ],
                        "start": "06:00",
                        "end": "07:30",
                        "state": "on",
                    }
                ],
            },
        },
    )
    assert schedule_change_response.status == 200
    changed = await schedule_change_response.json()
    assert changed["config"]["control"]["enabled"] is True

    changed_control = dict(enabled_control)
    changed_control["heating_offset"] = 3
    control_change_response = await client.put("/api/control", json=changed_control)
    assert control_change_response.status == 200
    control_change = await control_change_response.json()
    assert control_change["config"]["control"]["enabled"] is True
    assert control_change["config"]["control"]["heating_offset"] == 3

    preset_change_response = await client.put(
        "/api/presets",
        json={"boost": 24, "comfort": 22, "eco": 17, "frost": 6},
    )
    assert preset_change_response.status == 200
    assert (await preset_change_response.json())["config"]["control"]["enabled"] is True

    emergency_response = await client.post("/api/control/emergency-stop")
    assert emergency_response.status == 200
    emergency = await emergency_response.json()
    assert emergency["relays_confirmed_off"] is True
    assert emergency["config"]["control"]["enabled"] is False
    assert all(
        state["state"] == "off"
        for state in registry_snapshot.states
        if state["entity_id"]
        in {
            "switch.boiler_switch_central_heating_switch",
            "switch.boiler_switch_hot_water_switch",
        }
    )

    health_response = await client.get("/api/health")
    assert health_response.status == 200
    controller.status.last_cycle_finished_at = datetime.now().astimezone() - timedelta(seconds=100)
    overdue_response = await client.get("/api/health")
    assert overdue_response.status == 503
    assert (await overdue_response.json())["controller"] == "overdue"
    controller.status.last_cycle_finished_at = datetime.now().astimezone()

    save_again_response = await client.put(
        "/api/onboarding",
        json={
            "trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}],
        },
    )
    assert save_again_response.status == 200
    assert store.load().presets.comfort == 22
    assert store.load().boiler_switch == "switch.boiler_switch_central_heating_switch"
    assert store.load().hot_water_switch == "switch.boiler_switch_hot_water_switch"
    assert store.load().minimum_heat_requests == 1
    assert store.load().control.temperature_source == "internal"
    assert store.load().setup.control is True
    assert store.load().setup.presets is True
    assert store.load().setup.schedules is True
    assert store.load().schedules[0].room_group == "Dining Room"
    assert store.load().hot_water_schedule.periods[0].state == "on"

    changed_group_response = await client.put(
        "/api/onboarding",
        json={
            "trvs": [{"device_id": "device-dining", "room_group": "Downstairs"}],
        },
    )
    assert changed_group_response.status == 200
    assert store.load().setup.schedules is False
    assert store.load().schedules == []


async def test_predictive_heating_is_exposed_and_only_newly_enabled_when_ready(
    aiohttp_client, tmp_path, registry_snapshot
):
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    ha = FakeHomeAssistant(registry_snapshot)
    controller = HeatingController(ha, store, notifier=FakeNotifier())
    now = datetime.now(UTC)
    model = RoomThermalModel(
        device_id="device-dining",
        temperature_source="external",
        temperature_entity="sensor.dining_thermometer_temperature",
        heating_rate_c_per_hour=1.2,
        heating_samples=4,
        heating_updated_at=now,
        confidence=0.7,
    )
    controller.thermal_learner = FixedThermalLearner({model.device_id: model})
    client = await aiohttp_client(WebApplication(ha, store, controller=controller).create())

    discovery_response = await client.get("/api/onboarding/discovery")
    assert discovery_response.status == 200
    discovery = await discovery_response.json()
    assert discovery["predictive_models"]["device-dining"]["ready"] is True
    assert discovery["predictive_models"]["device-dining"]["heating_rate_c_per_hour"] == 1.2

    selection = {
        "trvs": [
            {
                "device_id": "device-dining",
                "room_group": "Dining Room",
                "external_temperature_sensor": "sensor.dining_thermometer_temperature",
                "predictive_heating_enabled": True,
            }
        ]
    }
    enabled_response = await client.put("/api/onboarding", json=selection)
    assert enabled_response.status == 200
    assert store.load().trvs[0].predictive_heating_enabled is True

    controller.thermal_learner = FixedThermalLearner({})
    unchanged_response = await client.put("/api/onboarding", json=selection)
    assert unchanged_response.status == 200

    disabled_selection = {"trvs": [{**selection["trvs"][0], "predictive_heating_enabled": False}]}
    disabled_response = await client.put("/api/onboarding", json=disabled_selection)
    assert disabled_response.status == 200
    assert store.load().trvs[0].predictive_heating_enabled is False

    rejected_response = await client.put("/api/onboarding", json=selection)
    assert rejected_response.status == 400
    assert "until enough recent" in (await rejected_response.json())["error"]


async def test_onboarding_api_rejects_unknown_device(aiohttp_client, tmp_path, registry_snapshot):
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    app = WebApplication(FakeHomeAssistant(registry_snapshot), store).create()
    client = await aiohttp_client(app)

    response = await client.put(
        "/api/onboarding",
        json={
            "trvs": [{"device_id": "not-real", "room_group": "Nowhere"}],
        },
    )

    assert response.status == 400
    assert "not available" in (await response.json())["error"]


async def test_boiler_setup_can_be_skipped_and_validates_heat_request_count(
    aiohttp_client, tmp_path, registry_snapshot
):
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    app = WebApplication(FakeHomeAssistant(registry_snapshot), store).create()
    client = await aiohttp_client(app)

    devices_response = await client.put(
        "/api/onboarding",
        json={"trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}]},
    )
    assert devices_response.status == 200

    skip_response = await client.put(
        "/api/boiler",
        json={
            "boiler_switch": None,
            "hot_water_switch": None,
            "minimum_heat_requests": 1,
        },
    )
    assert skip_response.status == 200
    skipped = (await skip_response.json())["config"]
    assert skipped["setup"]["boiler"] is True
    assert skipped["boiler_switch"] is None
    assert skipped["hot_water_switch"] is None

    invalid_response = await client.put(
        "/api/boiler",
        json={
            "boiler_switch": "switch.boiler_switch_central_heating_switch",
            "hot_water_switch": "switch.boiler_switch_hot_water_switch",
            "minimum_heat_requests": 2,
        },
    )
    assert invalid_response.status == 400
    assert "cannot exceed" in (await invalid_response.json())["error"]


async def test_required_setup_can_finish_with_default_control_and_presets(
    aiohttp_client, tmp_path, registry_snapshot
):
    store = ConfigStore(tmp_path / "heat-steward-config.json")
    app = WebApplication(FakeHomeAssistant(registry_snapshot), store).create()
    client = await aiohttp_client(app)

    devices_response = await client.put(
        "/api/onboarding",
        json={"trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}]},
    )
    assert devices_response.status == 200

    schedules_before_boiler = await client.put(
        "/api/schedules",
        json={
            "schedules": [
                {
                    "room_group": "Dining Room",
                    "default_preset": "eco",
                    "periods": [],
                }
            ]
        },
    )
    assert schedules_before_boiler.status == 409

    boiler_response = await client.put(
        "/api/boiler",
        json={
            "boiler_switch": None,
            "hot_water_switch": None,
            "minimum_heat_requests": 1,
        },
    )
    assert boiler_response.status == 200

    schedules_response = await client.put(
        "/api/schedules",
        json={
            "schedules": [
                {
                    "room_group": "Dining Room",
                    "default_preset": "eco",
                    "periods": [],
                }
            ]
        },
    )
    assert schedules_response.status == 200

    saved = store.load()
    assert saved is not None
    assert saved.setup.boiler is True
    assert saved.setup.control is False
    assert saved.setup.presets is False
    assert saved.setup.schedules is True
    assert saved.control.enabled is False
    assert saved.presets.model_dump() == {
        "boost": 23.0,
        "comfort": 21.0,
        "eco": 16.0,
        "frost": 5.0,
    }
