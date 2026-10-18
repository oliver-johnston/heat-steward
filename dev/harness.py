from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import WSMsgType, web
from heat_steward.config import ConfigStore
from heat_steward.control import HeatingController
from heat_steward.ha_client import HomeAssistantClient, RegistrySnapshot
from heat_steward.web import WebApplication

from dev.history_seed import seed_simulator_history

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "dev-token"
SCENARIOS = {
    "healthy": "Eight healthy valves, matching room thermometers, and both boiler relays.",
    "missing_entities": "Dining TRV is missing its external-temperature input and smart control.",
    "offline_sensor": "The Kitchen external thermometer is unavailable.",
    "offline_trv": "The Kitchen TRV is unavailable while all other rooms remain online.",
    "ignored_commands": "Devices accept commands but never report the requested state.",
    "no_trvs": "No compatible radiator valves are present.",
}
ROOMS = (
    ("kitchen", "Kitchen", 20.4),
    ("dining", "Dining", 20.1),
    ("living", "Living", 20.8),
    ("guest", "Guest", 18.4),
    ("bedroom", "Bedroom", 19.9),
    ("bedroom_2", "Bedroom 2", 21.7),
    ("bedroom_3", "Bedroom 3", 20.2),
    ("office", "Office", 19.5),
)


def _entity(
    entity_id: str,
    device_id: str,
    original_name: str,
    *,
    area_id: str | None = None,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "device_id": device_id,
        "original_name": original_name,
        "name": None,
        "area_id": area_id,
        "disabled_by": None,
    }


def _state(entity_id: str, state: str, **attributes: Any) -> dict[str, Any]:
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


def build_snapshot(scenario: str = "healthy") -> RegistrySnapshot:
    """Build a deterministic, realistic Home Assistant registry snapshot."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown harness scenario: {scenario}")

    areas = [{"area_id": slug, "name": name} for slug, name, _ in ROOMS]
    areas.append({"area_id": "utility", "name": "Utility Room"})
    devices: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []

    auxiliary_entities = (
        ("number", "external_temperature_input", "External temperature input", "20.0"),
        ("number", "frost_protection_temperature", "Frost protection temperature", "5.0"),
        ("number", "local_temperature_calibration", "Local temperature calibration", "0.0"),
        ("number", "temperature_accuracy", "Temperature accuracy", "0.2"),
        ("number", "temporary_mode_duration", "Temporary mode duration", "60"),
        ("number", "timer_mode_target_temp", "Timer mode target temp", "21.0"),
        ("sensor", "local_temperature", "Local temperature", "20.0"),
        ("sensor", "battery", "Battery", "87"),
        ("sensor", "last_seen", "Last seen", "2026-10-12T06:59:00+00:00"),
        ("switch", "child_lock", "Child lock", "off"),
        ("switch", "smart_temperature_control", "Smart temperature control", "on"),
        ("switch", "open_window", "Open window", "off"),
        ("select", "temperature_sensor_select", "Temperature sensor select", "external"),
        ("select", "temporary_mode_select", "Temporary mode select", "off"),
        ("number", "valve_opening_degree", "Valve opening degree", "100"),
        ("number", "valve_closing_degree", "Valve closing degree", "0"),
        ("sensor", "closing_steps", "Closing steps", "476"),
        ("sensor", "idle_steps", "Idle steps", "210"),
        ("sensor", "valve_opening_limit_voltage", "Valve opening limit voltage", "1.8"),
        ("sensor", "valve_closing_limit_voltage", "Valve closing limit voltage", "1.7"),
        ("sensor", "valve_motor_running_voltage", "Valve motor running voltage", "1.5"),
    )

    for index, (slug, name, temperature) in enumerate(ROOMS, start=1):
        trv_id = f"mock-{slug}-trv"
        thermometer_id = f"mock-{slug}-thermometer"
        devices.extend(
            [
                {
                    "id": trv_id,
                    "name": f"{name} TRV",
                    "name_by_user": None,
                    "manufacturer": "SONOFF",
                    "model": "Zigbee thermostatic radiator valve",
                    "sw_version": "1.4.4",
                    "area_id": slug,
                },
                {
                    "id": thermometer_id,
                    "name": f"{name} thermometer",
                    "name_by_user": None,
                    "manufacturer": "SONOFF",
                    "model": "Zigbee temperature and humidity sensor",
                    "sw_version": "1.0.0",
                    "area_id": slug,
                },
            ]
        )
        climate_id = f"climate.{slug}_trv"
        entities.append(_entity(climate_id, trv_id, "Thermostat"))
        states.append(
            _state(
                climate_id,
                "heat",
                friendly_name=f"{name} TRV",
                current_temperature=temperature - 0.4,
                temperature=21.0,
                hvac_action="heating" if temperature < 20 else "idle",
            )
        )
        for domain, suffix, original_name, value in auxiliary_entities:
            entity_id = f"{domain}.{slug}_trv_{suffix}"
            entities.append(_entity(entity_id, trv_id, original_name))
            attributes: dict[str, Any] = {"friendly_name": f"{name} TRV {original_name}"}
            if suffix in {"local_temperature", "external_temperature_input"}:
                attributes.update(device_class="temperature", unit_of_measurement="°C")
            elif suffix == "battery":
                attributes.update(device_class="battery", unit_of_measurement="%")
            states.append(
                _state(
                    entity_id,
                    str(temperature) if suffix == "local_temperature" else value,
                    **attributes,
                )
            )

        linkquality_id = f"sensor.0x00124b0024{index:06x}_linkquality"
        entities.append(_entity(linkquality_id, trv_id, "Linkquality"))
        states.append(_state(linkquality_id, str(80 + index), unit_of_measurement="lqi"))
        update_id = f"update.{slug}_trv"
        entities.append(_entity(update_id, trv_id, "Firmware"))
        states.append(_state(update_id, "off", installed_version="1.4.4", latest_version="1.4.4"))

        sensor_id = f"sensor.{slug}_thermometer_temperature"
        entities.append(_entity(sensor_id, thermometer_id, "Temperature"))
        states.append(
            _state(
                sensor_id,
                str(temperature),
                friendly_name=f"{name} thermometer Temperature",
                device_class="temperature",
                unit_of_measurement="°C",
            )
        )
        sensor_last_seen_id = f"sensor.{slug}_thermometer_last_seen"
        entities.append(_entity(sensor_last_seen_id, thermometer_id, "Last seen"))
        states.append(_state(sensor_last_seen_id, "2026-10-12T06:59:00+00:00"))

    devices.append(
        {
            "id": "mock-boiler",
            "name": "Boiler Switch",
            "name_by_user": None,
            "manufacturer": "SONOFF",
            "model": "Two-channel Wi-Fi switch",
            "sw_version": "3.7.6",
            "area_id": "utility",
        }
    )
    for entity_id, original_name in (
        ("switch.boiler_switch_central_heating_switch", "Central heating switch"),
        ("switch.boiler_switch_hot_water_switch", "Hot water switch"),
    ):
        entities.append(_entity(entity_id, "mock-boiler", original_name))
        states.append(_state(entity_id, "off", friendly_name=original_name.title()))

    snapshot = RegistrySnapshot(devices=devices, entities=entities, areas=areas, states=states)
    return _apply_scenario(snapshot, scenario)


def _apply_scenario(snapshot: RegistrySnapshot, scenario: str) -> RegistrySnapshot:
    result = copy.deepcopy(snapshot)
    devices = result.devices
    entities = result.entities
    states = result.states
    if scenario == "missing_entities":
        missing = {
            "number.dining_trv_external_temperature_input",
            "switch.dining_trv_smart_temperature_control",
        }
        entities = [item for item in entities if item["entity_id"] not in missing]
        states = [item for item in states if item["entity_id"] not in missing]
    elif scenario == "offline_sensor":
        for state in states:
            if state["entity_id"] == "sensor.kitchen_thermometer_temperature":
                state["state"] = "unavailable"
    elif scenario == "offline_trv":
        for state in states:
            if state["entity_id"] == "climate.kitchen_trv":
                state["state"] = "unavailable"
    elif scenario == "no_trvs":
        trv_ids = {device["id"] for device in devices if str(device["id"]).endswith("-trv")}
        devices = [device for device in devices if device["id"] not in trv_ids]
        trv_entities = {
            entity["entity_id"] for entity in entities if entity.get("device_id") in trv_ids
        }
        entities = [entity for entity in entities if entity.get("device_id") not in trv_ids]
        states = [state for state in states if state["entity_id"] not in trv_entities]
    return RegistrySnapshot(devices=devices, entities=entities, areas=result.areas, states=states)


class MockHomeAssistant:
    """Small Home Assistant protocol double used by the local harness and tests."""

    def __init__(self, scenario: str = "healthy"):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown harness scenario: {scenario}")
        self.scenario = scenario
        self.room_values = {
            slug: {
                "sensor_temperature": temperature,
                "trv_temperature": round(temperature - 0.4, 1),
                "setpoint": 21.0,
                "hvac_mode": "heat",
            }
            for slug, _, temperature in ROOMS
        }
        self.relay_values = {"central_heating": "off", "hot_water": "off"}
        self.entity_overrides: dict[str, str] = {}
        self.service_calls: list[dict[str, Any]] = []
        self.notifications: dict[str, dict[str, str]] = {}
        self.event_subscribers: list[tuple[web.WebSocketResponse, str]] = []
        self.event_subscription_ready = asyncio.Event()

    @property
    def snapshot(self) -> RegistrySnapshot:
        snapshot = build_snapshot(self.scenario)
        for state in snapshot.states:
            entity_id = state["entity_id"]
            if entity_id.endswith("_last_seen"):
                state["state"] = datetime.now(UTC).isoformat()
            for slug, values in self.room_values.items():
                sensor_id = f"sensor.{slug}_thermometer_temperature"
                climate_id = f"climate.{slug}_trv"
                local_id = f"sensor.{slug}_trv_local_temperature"
                if entity_id == sensor_id and state["state"] != "unavailable":
                    state["state"] = str(values["sensor_temperature"])
                elif entity_id == local_id:
                    state["state"] = str(values["trv_temperature"])
                elif entity_id == climate_id and state["state"] != "unavailable":
                    state["state"] = values["hvac_mode"]
                    state["attributes"]["current_temperature"] = values["trv_temperature"]
                    state["attributes"]["temperature"] = values["setpoint"]
                    state["attributes"]["hvac_action"] = (
                        "heating"
                        if values["hvac_mode"] == "heat"
                        and values["trv_temperature"] < values["setpoint"]
                        else "idle"
                    )
            if entity_id == "switch.boiler_switch_central_heating_switch":
                state["state"] = self.relay_values["central_heating"]
            elif entity_id == "switch.boiler_switch_hot_water_switch":
                state["state"] = self.relay_values["hot_water"]
            if entity_id in self.entity_overrides:
                state["state"] = self.entity_overrides[entity_id]
        return snapshot

    def _authorised(self, request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {TOKEN}"

    async def states(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            raise web.HTTPUnauthorized()
        return web.json_response(self.snapshot.states)

    async def config(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            raise web.HTTPUnauthorized()
        return web.json_response({"time_zone": "Europe/London"})

    async def call_service(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            raise web.HTTPUnauthorized()
        domain = request.match_info["domain"]
        service = request.match_info["service"]
        data = await request.json()
        entity_id = str(data.get("entity_id", ""))
        self.service_calls.append({"domain": domain, "service": service, "data": data})

        if domain == "persistent_notification" and service == "create":
            notification_id = str(data.get("notification_id", "heat_steward"))
            self.notifications[notification_id] = {
                "title": str(data.get("title", "")),
                "message": str(data.get("message", "")),
            }
        elif domain == "persistent_notification" and service == "dismiss":
            self.notifications.pop(str(data.get("notification_id", "")), None)
        elif self.scenario == "ignored_commands":
            pass
        elif domain == "climate" and entity_id.startswith("climate."):
            slug = entity_id.removeprefix("climate.").removesuffix("_trv")
            if slug not in self.room_values:
                raise web.HTTPBadRequest(text="Unknown climate entity")
            if service == "set_hvac_mode":
                self.room_values[slug]["hvac_mode"] = str(data["hvac_mode"])
            elif service == "set_temperature":
                self.room_values[slug]["setpoint"] = float(data["temperature"])
            else:
                raise web.HTTPBadRequest(text="Unsupported climate service")
        elif domain == "switch" and service in {"turn_on", "turn_off"}:
            value = "on" if service == "turn_on" else "off"
            relay_entities = {
                "switch.boiler_switch_central_heating_switch": "central_heating",
                "switch.boiler_switch_hot_water_switch": "hot_water",
            }
            if entity_id in relay_entities:
                self.relay_values[relay_entities[entity_id]] = value
            else:
                self.entity_overrides[entity_id] = value
        elif domain == "select" and service == "select_option":
            self.entity_overrides[entity_id] = str(data["option"])
        elif domain == "number" and service == "set_value":
            self.entity_overrides[entity_id] = str(data["value"])
        else:
            raise web.HTTPBadRequest(text="Unsupported service")
        return web.json_response([])

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.send_json({"type": "auth_required", "ha_version": "2026.10.0"})
        try:
            auth = await socket.receive_json()
        except (TypeError, ValueError):
            await socket.close()
            return socket
        if auth != {"type": "auth", "access_token": TOKEN}:
            await socket.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await socket.close()
            return socket
        await socket.send_json({"type": "auth_ok", "ha_version": "2026.10.0"})

        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            payload = json.loads(message.data)
            snapshot = self.snapshot
            results = {
                "config/device_registry/list": snapshot.devices,
                "config/entity_registry/list": snapshot.entities,
                "config/area_registry/list": snapshot.areas,
            }
            command = payload.get("type")
            if command in results:
                response = {
                    "id": payload.get("id"),
                    "type": "result",
                    "success": True,
                    "result": results[command],
                }
            elif command == "subscribe_events":
                event_type = payload.get("event_type")
                if isinstance(event_type, str):
                    self.event_subscribers.append((socket, event_type))
                    self.event_subscription_ready.set()
                response = {
                    "id": payload.get("id"),
                    "type": "result",
                    "success": True,
                    "result": None,
                }
            else:
                response = {
                    "id": payload.get("id"),
                    "type": "result",
                    "success": False,
                    "error": {"code": "unknown_command", "message": f"Unknown command: {command}"},
                }
            await socket.send_json(response)
        self.event_subscribers = [
            subscriber for subscriber in self.event_subscribers if subscriber[0] is not socket
        ]
        return socket

    async def fire_event(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            raise web.HTTPUnauthorized()
        event_type = request.match_info["event_type"]
        data = await request.json()
        payload = {
            "id": 1,
            "type": "event",
            "event": {"event_type": event_type, "data": data},
        }
        delivered = 0
        for socket, subscribed_type in list(self.event_subscribers):
            if subscribed_type == event_type and not socket.closed:
                await socket.send_json(payload)
                delivered += 1
        return web.json_response({"message": f"Event {event_type} fired.", "listeners": delivered})

    async def control(self, request: web.Request) -> web.Response:
        index: Path = request.app["static_dir"] / "simulator.html"
        if not index.exists():
            return web.Response(
                text="Simulator frontend is not built. Run `npm run build:simulator`.",
                status=503,
            )
        return web.FileResponse(index)

    async def simulator_state(self, request: web.Request) -> web.Response:
        config_path: Path = request.app["config_path"]

        def scenario_label(name: str) -> str:
            return name.replace("_", " ").title().replace("Trvs", "TRVs")

        return web.json_response(
            {
                "current_scenario": scenario_label(self.scenario),
                "configured": config_path.exists(),
                "heating_url": f"http://127.0.0.1:{request.app['heating_port']}/",
                "scenarios": [
                    {
                        "id": name,
                        "label": scenario_label(name),
                        "description": description,
                        "active": name == self.scenario,
                    }
                    for name, description in SCENARIOS.items()
                ],
                "rooms": [
                    {"slug": slug, "name": name, **self.room_values[slug]}
                    for slug, name, _ in ROOMS
                ],
                "relays": [
                    {"id": relay_id, "label": label, "state": self.relay_values[relay_id]}
                    for relay_id, label in (
                        ("central_heating", "Central heating relay"),
                        ("hot_water", "Hot water relay"),
                    )
                ],
                "notifications": list(self.notifications.values()),
            }
        )

    async def select_scenario(self, request: web.Request) -> web.Response:
        scenario = request.match_info["scenario"]
        if scenario not in SCENARIOS:
            raise web.HTTPNotFound(text="Unknown scenario")
        self.scenario = scenario
        raise web.HTTPSeeOther("/")

    async def update_room(self, request: web.Request) -> web.Response:
        slug = request.match_info["slug"]
        if slug not in self.room_values:
            raise web.HTTPNotFound(text="Unknown room")
        form = await request.post()
        try:
            sensor_temperature = float(form["sensor_temperature"])
            trv_temperature = float(form["trv_temperature"])
            setpoint = float(form["setpoint"])
        except (KeyError, TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text="Temperatures must be numbers") from exc
        if (
            not 5 <= sensor_temperature <= 35
            or not 5 <= trv_temperature <= 35
            or not 5 <= setpoint <= 35
        ):
            raise web.HTTPBadRequest(text="Temperatures must be between 5 and 35°C")
        hvac_mode = str(form.get("hvac_mode", ""))
        if hvac_mode not in {"heat", "off"}:
            raise web.HTTPBadRequest(text="TRV mode must be heat or off")
        self.room_values[slug] = {
            "sensor_temperature": sensor_temperature,
            "trv_temperature": trv_temperature,
            "setpoint": setpoint,
            "hvac_mode": hvac_mode,
        }
        raise web.HTTPSeeOther("/")

    async def toggle_relay(self, request: web.Request) -> web.Response:
        channel = request.match_info["channel"]
        if channel not in self.relay_values:
            raise web.HTTPNotFound(text="Unknown relay")
        self.relay_values[channel] = "on" if self.relay_values[channel] == "off" else "off"
        raise web.HTTPSeeOther("/")

    async def reset_config(self, request: web.Request) -> web.Response:
        path: Path = request.app["config_path"]
        path.unlink(missing_ok=True)
        raise web.HTTPSeeOther("/")

    def create_app(
        self,
        *,
        heating_port: int = 8099,
        config_path: Path | None = None,
        static_dir: Path | None = None,
    ) -> web.Application:
        app = web.Application()
        app["heating_port"] = heating_port
        app["config_path"] = config_path or ROOT / ".dev-data" / "heat-steward-config.json"
        app["static_dir"] = static_dir or ROOT / "addon" / "frontend" / "simulator-dist"
        app.router.add_get("/", self.control)
        app.router.add_get("/__harness/state", self.simulator_state)
        app.router.add_get("/api/states", self.states)
        app.router.add_get("/api/config", self.config)
        app.router.add_post("/api/services/{domain}/{service}", self.call_service)
        app.router.add_post("/api/events/{event_type}", self.fire_event)
        app.router.add_get("/websocket", self.websocket)
        app.router.add_post("/__harness/scenario/{scenario}", self.select_scenario)
        app.router.add_post("/__harness/room/{slug}", self.update_room)
        app.router.add_post("/__harness/relay/{channel}", self.toggle_relay)
        app.router.add_post("/__harness/reset", self.reset_config)
        assets = app["static_dir"] / "assets"
        if assets.exists():
            app.router.add_static("/assets", assets, show_index=False)
        return app


async def _serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ha_port = int(os.environ.get("HARNESS_HA_PORT", "18123"))
    heating_port = int(os.environ.get("HARNESS_APP_PORT", "8099"))
    config_path = Path(
        os.environ.get("HARNESS_CONFIG_PATH", ROOT / ".dev-data" / "heat-steward-config.json")
    )
    static_dir = ROOT / "addon" / "frontend" / "dist"
    simulator_static_dir = ROOT / "addon" / "frontend" / "simulator-dist"
    if not (static_dir / "index.html").exists():
        raise RuntimeError("Frontend is not built. Run `npm --prefix addon/frontend run build`.")
    if not (simulator_static_dir / "simulator.html").exists():
        raise RuntimeError(
            "Simulator frontend is not built. Run "
            "`npm --prefix addon/frontend run build:simulator`."
        )

    mock = MockHomeAssistant(os.environ.get("HARNESS_SCENARIO", "healthy"))
    mock_runner = web.AppRunner(
        mock.create_app(
            heating_port=heating_port,
            config_path=config_path,
            static_dir=simulator_static_dir,
        )
    )
    await mock_runner.setup()
    await web.TCPSite(mock_runner, "127.0.0.1", ha_port).start()

    timeout = aiohttp.ClientTimeout(total=30)
    session = aiohttp.ClientSession(timeout=timeout)
    client = HomeAssistantClient(f"http://127.0.0.1:{ha_port}", TOKEN, session)
    store = ConfigStore(config_path)
    seeded_cycles = await asyncio.to_thread(seed_simulator_history, store)
    if seeded_cycles:
        logging.getLogger(__name__).info(
            "Seeded %s realistic historical control cycles for the simulator",
            seeded_cycles,
        )
    controller = HeatingController(client, store)
    heating = WebApplication(client, store, static_dir=static_dir, controller=controller)
    heating_runner = web.AppRunner(heating.create())
    await heating_runner.setup()
    await web.TCPSite(heating_runner, "127.0.0.1", heating_port).start()
    controller_task = asyncio.create_task(controller.run_forever())
    mode_event_task = asyncio.create_task(
        client.listen_events("heat_steward_set_mode", controller.handle_mode_event)
    )
    override_event_task = asyncio.create_task(
        client.listen_events("heat_steward_override", controller.handle_override_event)
    )

    print(f"\nHeat Steward:       http://127.0.0.1:{heating_port}/")
    print(f"Device simulator:   http://127.0.0.1:{ha_port}/")
    print("Press Ctrl-C to stop.\n")
    try:
        await asyncio.Event().wait()
    finally:
        for task in (controller_task, mode_event_task, override_event_task):
            task.cancel()
        for task in (controller_task, mode_event_task, override_event_task):
            with suppress(asyncio.CancelledError):
                await task
        await heating_runner.cleanup()
        await session.close()
        await mock_runner.cleanup()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_serve())


if __name__ == "__main__":
    main()
