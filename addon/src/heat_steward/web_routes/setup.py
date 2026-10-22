from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from aiohttp import web
from pydantic import ValidationError

from ..config import BoilerSettingsUpdate, HeatingConfig, ScheduleUpdate, TemperaturePresets
from ..discovery import OnboardingRequest, discover, materialise_config
from ..ha_client import HomeAssistantError
from .common import WebContext

log = logging.getLogger(__name__)


class SetupRoutes:
    def __init__(self, context: WebContext):
        self.context = context

    def register(self, app: web.Application) -> None:
        app.router.add_get("/api/config", self.get_config)
        app.router.add_get("/api/onboarding/discovery", self.get_discovery)
        app.router.add_put("/api/onboarding", self.save_onboarding)
        app.router.add_put("/api/boiler", self.save_boiler)
        app.router.add_put("/api/presets", self.save_presets)
        app.router.add_put("/api/schedules", self.save_schedules)

    async def _predictive_models(self, device_ids: list[str], at: datetime):
        controller = self.context.controller
        if controller is None:
            return {}, "The heating controller is not running"
        try:
            models = await asyncio.to_thread(
                controller.thermal_learner.models,
                device_ids,
                at=at,
            )
        except Exception:
            log.exception("Could not load predictive heating models")
            return {}, "Predictive heating history is unavailable"
        return models, None

    async def get_config(self, request: web.Request) -> web.Response:
        del request
        try:
            config = self.context.store.load()
        except (OSError, ValidationError) as exc:
            log.exception("Stored configuration is invalid")
            return web.json_response(
                {"configured": False, "error": f"Stored configuration is invalid: {exc}"},
                status=500,
            )
        return web.json_response(
            {
                "configured": config is not None,
                "config": self.context.public_config(config) if config else None,
            }
        )

    async def get_discovery(self, request: web.Request) -> web.Response:
        del request
        try:
            result = discover(await self.context.ha.get_registry_snapshot())
            now = datetime.now(UTC)
            models, model_error = await self._predictive_models(
                [trv["device_id"] for trv in result["trvs"]],
                now,
            )
            result["predictive_models"] = {
                device_id: model.as_dict(now) for device_id, model in models.items()
            }
            result["predictive_models_error"] = model_error
        except HomeAssistantError as exc:
            log.warning("Discovery failed: %s", exc)
            return web.json_response({"error": str(exc)}, status=503)
        return web.json_response(result)

    async def save_onboarding(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            selection = OnboardingRequest.model_validate(body)
            discovery_result = discover(await self.context.ha.get_registry_snapshot())
            existing = self.context.store.load()
            now = datetime.now(UTC)
            discovered_trvs = {trv["device_id"]: trv for trv in discovery_result["trvs"]}
            existing_trvs = (
                {trv.device_id: trv for trv in existing.trvs} if existing is not None else {}
            )
            requested_predictive = []
            for choice in selection.trvs:
                if not choice.predictive_heating_enabled:
                    continue
                discovered = discovered_trvs.get(choice.device_id, {})
                temperature_entity = choice.external_temperature_sensor or discovered.get(
                    "entities", {}
                ).get("climate")
                previous = existing_trvs.get(choice.device_id)
                previous_temperature_entity = (
                    previous.external_temperature_sensor or previous.entities.climate
                    if previous is not None
                    else None
                )
                if (
                    previous is None
                    or not previous.predictive_heating_enabled
                    or previous_temperature_entity != temperature_entity
                ):
                    requested_predictive.append(choice)
            if requested_predictive:
                models, model_error = await self._predictive_models(
                    [choice.device_id for choice in requested_predictive],
                    now,
                )
                if model_error:
                    raise ValueError(model_error)
                for choice in requested_predictive:
                    discovered = discovered_trvs.get(choice.device_id, {})
                    temperature_entity = choice.external_temperature_sensor or discovered.get(
                        "entities", {}
                    ).get("climate")
                    model = models.get(choice.device_id)
                    if (
                        model is None
                        or not model.actionable(now)
                        or model.temperature_entity != temperature_entity
                    ):
                        raise ValueError(
                            "Predictive heating cannot be enabled for "
                            f"{discovered.get('name') or choice.device_id} until enough recent "
                            "warm-up information has been gathered with its "
                            "selected temperature sensor"
                        )
            config = materialise_config(selection, discovery_result)
            changed = existing is not None and config.trvs != existing.trvs
            if existing is not None:
                config.boiler_switch = existing.boiler_switch
                config.hot_water_switch = existing.hot_water_switch
                config.minimum_heat_requests = min(existing.minimum_heat_requests, len(config.trvs))
                config.control = existing.control
                config.safety = existing.safety
                config.notifications = existing.notifications
                config.presets = existing.presets
                config.setup = existing.setup
                room_groups = {trv.room_group for trv in config.trvs}
                existing_groups = {schedule.room_group for schedule in existing.schedules}
                config.schedules = [
                    schedule
                    for schedule in existing.schedules
                    if schedule.room_group in room_groups
                ]
                config.hot_water_schedule = existing.hot_water_schedule
                config.setup.schedules = existing.setup.schedules and existing_groups == room_groups
            config.setup.devices = True
            config = await self.context.apply_change(config, "radiator devices", changed)
        except ValidationError as exc:
            return web.json_response(
                {
                    "error": "Configuration is invalid",
                    "details": exc.errors(include_url=False, include_context=False),
                },
                status=400,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except HomeAssistantError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        except OSError:
            log.exception("Could not save configuration")
            return web.json_response({"error": "Could not save configuration"}, status=500)
        return web.json_response(
            {
                "status": "saved",
                "config": self.context.public_config(config),
            }
        )

    async def save_boiler(self, request: web.Request) -> web.Response:
        try:
            existing = self.context.store.load()
            if existing is None:
                return web.json_response(
                    {"error": "Complete device setup before saving boiler settings"}, status=409
                )
            update = BoilerSettingsUpdate.model_validate(await request.json())
            if update.minimum_heat_requests > len(existing.trvs):
                raise ValueError(
                    "Minimum heat requests cannot exceed the number of configured TRVs"
                )
            discovery_result = discover(await self.context.ha.get_registry_snapshot())
            known_switches = {item["entity_id"] for item in discovery_result["switches"]}
            if update.boiler_switch not in known_switches | {None}:
                raise ValueError(f"Heating switch is not available: {update.boiler_switch}")
            if update.hot_water_switch not in known_switches | {None}:
                raise ValueError(f"Hot-water switch is not available: {update.hot_water_switch}")

            changed = (
                update.boiler_switch != existing.boiler_switch
                or update.hot_water_switch != existing.hot_water_switch
                or update.minimum_heat_requests != existing.minimum_heat_requests
            )
            payload = existing.model_dump()
            payload.update(update.model_dump())
            payload["setup"]["boiler"] = True
            payload["updated_at"] = datetime.now(UTC)
            config = HeatingConfig.model_validate(payload)
            config = await self.context.apply_change(config, "boiler settings", changed)
        except ValidationError as exc:
            return web.json_response(
                {
                    "error": "Boiler settings are invalid",
                    "details": exc.errors(include_url=False, include_context=False),
                },
                status=400,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except HomeAssistantError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        except OSError:
            log.exception("Could not save boiler settings")
            return web.json_response({"error": "Could not save boiler settings"}, status=500)
        return web.json_response(
            {
                "status": "saved",
                "config": self.context.public_config(config),
            }
        )

    async def save_schedules(self, request: web.Request) -> web.Response:
        try:
            config = self.context.store.load()
            if config is None:
                return web.json_response(
                    {"error": "Complete system setup before saving schedules"}, status=409
                )
            if not config.setup.boiler:
                return web.json_response(
                    {"error": "Complete or skip boiler setup before saving schedules"}, status=409
                )
            update = ScheduleUpdate.model_validate(await request.json())
            room_groups = {trv.room_group for trv in config.trvs}
            scheduled_groups = [schedule.room_group for schedule in update.schedules]
            duplicate_groups = {
                group for group in scheduled_groups if scheduled_groups.count(group) > 1
            }
            if duplicate_groups:
                groups = ", ".join(sorted(duplicate_groups))
                raise ValueError(f"Room groups can only have one schedule: {groups}")
            missing_groups = room_groups - set(scheduled_groups)
            unknown_groups = set(scheduled_groups) - room_groups
            if missing_groups:
                groups = ", ".join(sorted(missing_groups))
                raise ValueError(f"Schedules are missing room groups: {groups}")
            if unknown_groups:
                groups = ", ".join(sorted(unknown_groups))
                raise ValueError(f"Schedules contain unknown room groups: {groups}")
            changed = (
                update.schedules != config.schedules
                or update.hot_water_schedule != config.hot_water_schedule
            )
            config.schedules = update.schedules
            config.hot_water_schedule = update.hot_water_schedule
            config.setup.schedules = True
            config.updated_at = datetime.now(UTC)
            config = await self.context.apply_change(config, "schedules", changed)
        except ValidationError as exc:
            return web.json_response(
                {
                    "error": "Schedules are invalid",
                    "details": exc.errors(include_url=False, include_context=False),
                },
                status=400,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except HomeAssistantError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        except OSError:
            log.exception("Could not save schedules")
            return web.json_response({"error": "Could not save schedules"}, status=500)
        return web.json_response(
            {
                "status": "saved",
                "config": self.context.public_config(config),
            }
        )

    async def save_presets(self, request: web.Request) -> web.Response:
        try:
            config = self.context.store.load()
            if config is None:
                return web.json_response(
                    {"error": "Complete system setup before saving presets"}, status=409
                )
            presets = TemperaturePresets.model_validate(await request.json())
            changed = presets != config.presets
            config.presets = presets
            config.setup.presets = True
            config.updated_at = datetime.now(UTC)
            config = await self.context.apply_change(config, "temperature presets", changed)
        except ValidationError as exc:
            return web.json_response(
                {
                    "error": "Presets are invalid",
                    "details": exc.errors(include_url=False, include_context=False),
                },
                status=400,
            )
        except HomeAssistantError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        except OSError:
            log.exception("Could not save presets")
            return web.json_response({"error": "Could not save presets"}, status=500)
        return web.json_response(
            {
                "status": "saved",
                "config": self.context.public_config(config),
            }
        )
