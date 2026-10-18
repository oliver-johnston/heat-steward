from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from .config import ConfigStore, HeatingConfig
from .control_plan import ControlPlan
from .overrides import (
    HeatingMode,
    OverrideActionRequest,
    OverrideActor,
    OverrideState,
    OverrideStore,
)

RecordEvent = Callable[[str, str], None]
RunControlCycle = Callable[[], Awaitable[ControlPlan | None]]


class OverrideService:
    """Validate, record, and replay user heating overrides."""

    def __init__(
        self,
        store: ConfigStore,
        override_store: OverrideStore,
        lock: asyncio.Lock,
        *,
        record_event: RecordEvent,
        run_control_cycle: RunControlCycle,
    ) -> None:
        self._store = store
        self._override_store = override_store
        self._lock = lock
        self._record_event = record_event
        self._run_control_cycle = run_control_cycle
        self._store_error: str | None = None
        self._reported_expired_actions: set[str] = set()

    def state(self, at: datetime | None = None) -> OverrideState:
        now = at or datetime.now().astimezone()
        state = self._override_store.load(now)
        store_error = self._override_store.last_error
        if store_error and store_error != self._store_error:
            self._record_event(
                "warning",
                f"{store_error}; continuing with the last valid override state",
            )
        elif self._store_error and not store_error:
            self._record_event("info", "Override action storage recovered")
        self._store_error = store_error

        ledger = self._override_store.load_ledger()
        latest_by_key = {action.key: action for action in ledger.actions}
        for action in latest_by_key.values():
            if (
                action.operation == "set"
                and action.expires_at is not None
                and action.expires_at <= now
                and action.action_id not in self._reported_expired_actions
                and (now - action.expires_at).total_seconds() <= 120
            ):
                self._reported_expired_actions.add(action.action_id)
                if action.channel == "hot_water":
                    self._record_event("info", "Hot-water boost ended")
                elif action.channel == "temperature" and action.override_preset == "boost":
                    target = action.scope_id or "the whole house"
                    self._record_event("info", f"Boost ended for {target}")
        return state

    async def apply(
        self,
        request: OverrideActionRequest,
        *,
        actor: OverrideActor,
        action_id: str | None = None,
    ) -> OverrideState:
        async with self._lock:
            config = self._require_config()
            request = self._canonicalise_target(config, request)
            self._validate_target(config, request)
            action = self._override_store.record(request, actor, action_id=action_id)
            target = request.scope_id or request.scope_type.replace("_", " ")
            self._record_event(
                "info",
                f"Recorded {request.channel} {request.operation} for {target} "
                f"from {self._actor_label(actor)} ({action.action_id})",
            )
        await self._run_control_cycle()
        return self.state()

    async def set_mode(
        self,
        mode: HeatingMode,
        *,
        source: str = "dashboard",
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        if mode not in {"home", "away"}:
            raise ValueError("Heating mode must be home or away")
        actor = actor or OverrideActor(source=source)
        async with self._lock:
            self._require_config()
            state = self.state()
            if state.mode != mode:
                self._override_store.record(
                    OverrideActionRequest(
                        channel="occupancy",
                        scope_type="house",
                        override_mode="mode",
                        mode=mode,
                    ),
                    actor,
                )
                self._record_event(
                    "info",
                    f"Heating mode changed to {mode.title()} from {self._actor_label(actor)}",
                )
        await self._run_control_cycle()
        return self.state()

    async def start_boost(
        self,
        room_group: str | None,
        duration_minutes: int,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        if duration_minutes not in {30, 60, 120, 240}:
            raise ValueError("Boost duration must be 30, 60, 120, or 240 minutes")
        actor = actor or OverrideActor(source="dashboard")
        async with self._lock:
            config = self._require_config()
            groups = {trv.room_group for trv in config.trvs}
            if room_group is not None and room_group not in groups:
                raise ValueError(f"Unknown room group: {room_group}")
            self._override_store.record(
                OverrideActionRequest(
                    channel="temperature",
                    scope_type="house" if room_group is None else "group",
                    scope_id=room_group,
                    override_mode="preset",
                    override_preset="boost",
                    duration_minutes=duration_minutes,
                ),
                actor,
            )
            target = room_group or "the whole house"
            self._record_event("info", f"Started a {duration_minutes}-minute boost for {target}")
        await self._run_control_cycle()
        return self.state()

    async def cancel_boost(
        self,
        room_group: str | None,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        actor = actor or OverrideActor(source="dashboard")
        async with self._lock:
            self._require_config()
            state = self.state()
            scope_type = "house" if room_group is None else "group"
            matching = any(
                override.scope_type == scope_type
                and override.scope_id == room_group
                and override.override_mode == "preset"
                and override.preset == "boost"
                for override in state.temperature_overrides
            )
            if matching:
                self._override_store.record(
                    OverrideActionRequest(
                        channel="temperature",
                        scope_type=scope_type,
                        scope_id=room_group,
                        operation="clear",
                    ),
                    actor,
                )
                target = room_group or "the whole house"
                self._record_event("info", f"Cancelled boost for {target}")
        await self._run_control_cycle()
        return self.state()

    async def start_hot_water_boost(
        self,
        duration_minutes: int,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        if duration_minutes not in {30, 60, 120, 240}:
            raise ValueError("Hot-water boost duration must be 30, 60, 120, or 240 minutes")
        actor = actor or OverrideActor(source="dashboard")
        async with self._lock:
            config = self._require_config()
            if not config.hot_water_switch:
                raise ValueError("Hot water is not configured")
            self._override_store.record(
                OverrideActionRequest(
                    channel="hot_water",
                    scope_type="hot_water",
                    override_mode="on_off",
                    on_off=True,
                    duration_minutes=duration_minutes,
                ),
                actor,
            )
            self._record_event("info", f"Started a {duration_minutes}-minute hot-water boost")
        await self._run_control_cycle()
        return self.state()

    async def cancel_hot_water_boost(
        self,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        actor = actor or OverrideActor(source="dashboard")
        async with self._lock:
            self._require_config()
            state = self.state()
            if state.hot_water_override is not None and state.hot_water_override.on:
                self._override_store.record(
                    OverrideActionRequest(
                        channel="hot_water",
                        scope_type="hot_water",
                        operation="clear",
                    ),
                    actor,
                )
                self._record_event("info", "Cancelled hot-water boost")
        await self._run_control_cycle()
        return self.state()

    async def clear_all(
        self,
        *,
        actor: OverrideActor,
    ) -> OverrideState:
        """Clear every active temperature and hot-water override."""
        async with self._lock:
            self._require_config()
            state = self.state()
            if self._override_store.last_error:
                raise OSError(self._override_store.last_error)
            requests = [
                OverrideActionRequest(
                    channel="temperature",
                    scope_type=override.scope_type,
                    scope_id=override.scope_id,
                    operation="clear",
                )
                for override in state.temperature_overrides
            ]
            if state.hot_water_override is not None:
                requests.append(
                    OverrideActionRequest(
                        channel="hot_water",
                        scope_type="hot_water",
                        operation="clear",
                    )
                )
            for request in requests:
                self._override_store.record(request, actor)
            count = len(requests)
            noun = "override" if count == 1 else "overrides"
            self._record_event(
                "info",
                f"Cleared {count} active {noun} from {self._actor_label(actor)}",
            )
        await self._run_control_cycle()
        return self.state()

    async def handle_mode_event(self, event_data: dict[str, Any]) -> None:
        mode = event_data.get("mode")
        if mode not in {"home", "away"}:
            self._record_event(
                "warning",
                "Ignored heat_steward_set_mode event with an invalid mode",
            )
            return
        context = event_data.get("_ha_context")
        user_id = context.get("user_id") if isinstance(context, dict) else None
        await self.set_mode(
            mode,
            actor=OverrideActor(
                source="Home Assistant automation",
                user_id=user_id if isinstance(user_id, str) else None,
            ),
        )

    async def handle_override_event(self, event_data: dict[str, Any]) -> None:
        """Apply one validated override command received from Home Assistant."""
        context = event_data.get("_ha_context")
        payload = {key: value for key, value in event_data.items() if key != "_ha_context"}
        actor = OverrideActor(
            source="Home Assistant automation",
            user_id=(
                context.get("user_id")
                if isinstance(context, dict) and isinstance(context.get("user_id"), str)
                else None
            ),
        )
        if payload.get("operation") == "clear_all":
            if payload != {"operation": "clear_all"}:
                self._record_event(
                    "warning",
                    "Ignored heat_steward_override clear_all event with unexpected data",
                )
                return
            try:
                await self.clear_all(actor=actor)
            except (OSError, ValueError) as exc:
                self._record_event(
                    "warning",
                    f"Ignored heat_steward_override clear_all event: {exc}",
                )
            return

        try:
            request = OverrideActionRequest.model_validate(payload)
        except ValidationError as exc:
            detail = exc.errors(include_url=False, include_context=False)[0]
            location = ".".join(str(part) for part in detail.get("loc", ()))
            reason = detail.get("msg", "invalid event data")
            if location:
                reason = f"{location}: {reason}"
            self._record_event(
                "warning",
                f"Ignored heat_steward_override event with invalid data ({reason})",
            )
            return

        if request.operation == "set" and request.duration_minutes is None:
            self._record_event(
                "warning",
                "Ignored heat_steward_override event because set actions require duration_minutes",
            )
            return

        context_id = context.get("id") if isinstance(context, dict) else None
        action_id = f"ha-event-{context_id}"[:64] if isinstance(context_id, str) else None
        try:
            await self.apply(
                request,
                actor=actor,
                action_id=action_id,
            )
        except (OSError, ValueError) as exc:
            self._record_event(
                "warning",
                f"Ignored heat_steward_override event: {exc}",
            )

    def _require_config(self) -> HeatingConfig:
        config = self._store.load()
        if config is None:
            raise ValueError("Heating is not configured")
        return config

    @staticmethod
    def _canonicalise_target(
        config: HeatingConfig,
        request: OverrideActionRequest,
    ) -> OverrideActionRequest:
        """Store room targets by device ID while accepting stable climate entity IDs."""
        if request.channel != "temperature" or request.scope_type != "room":
            return request
        trv = next(
            (
                item
                for item in config.trvs
                if request.scope_id in {item.device_id, item.entities.climate}
            ),
            None,
        )
        if trv is None or request.scope_id == trv.device_id:
            return request
        return request.model_copy(update={"scope_id": trv.device_id})

    @staticmethod
    def _actor_label(actor: OverrideActor) -> str:
        return actor.name or actor.source

    @staticmethod
    def _validate_target(config: HeatingConfig, request: OverrideActionRequest) -> None:
        if request.channel == "temperature" and request.scope_type == "group":
            groups = {trv.room_group for trv in config.trvs}
            if request.scope_id not in groups:
                raise ValueError(f"Unknown room group: {request.scope_id}")
        if request.channel == "temperature" and request.scope_type == "room":
            device_ids = {trv.device_id for trv in config.trvs}
            if request.scope_id not in device_ids:
                raise ValueError(f"Unknown room: {request.scope_id}")
        if request.channel == "hot_water" and not config.hot_water_switch:
            raise ValueError("Hot water is not configured")
