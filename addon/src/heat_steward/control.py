from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import HeatingConfig
from .control_executor import ControlExecutor
from .control_plan import ControlPlan, RoomDecision, build_control_plan
from .controller_status import ControllerStatus
from .event_recorder import EventRecorder
from .event_store import EventStore
from .ha_client import HomeAssistantClient, HomeAssistantError
from .ha_commands import HomeAssistantCommandRunner
from .metrics import CycleCommandResults, MetricsStore
from .notifications import DiscordNotifier
from .override_service import OverrideService
from .overrides import (
    HeatingMode,
    OverrideActionRequest,
    OverrideActor,
    OverrideState,
    OverrideStore,
)
from .predictive import ThermalModelLearner
from .preflight import PreflightCheck, PreflightResult, build_preflight
from .safety import SafetyManager

__all__ = [
    "ControlPlan",
    "ControllerStatus",
    "HeatingController",
    "PreflightCheck",
    "PreflightResult",
    "RoomDecision",
    "build_control_plan",
    "build_preflight",
]

log = logging.getLogger(__name__)


class HeatingController:
    def __init__(
        self,
        ha: HomeAssistantClient,
        store: Any,
        *,
        event_store: EventStore | None = None,
        notifier: DiscordNotifier | None = None,
        override_store: OverrideStore | None = None,
        metrics_store: MetricsStore | None = None,
        app_version: str | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.ha = ha
        self.store = store
        self.status = ControllerStatus()
        self.event_store = event_store or EventStore(store.events_path)
        self.events = EventRecorder(self.event_store, self.status)
        self.notifier = notifier or DiscordNotifier(
            getattr(ha, "session", None), store.notification_state_path
        )
        self.override_store = override_store or OverrideStore(store.action_store_path)
        self.metrics_store = metrics_store or MetricsStore(store.metrics_path)
        self._app_version = app_version or os.environ.get("HEAT_STEWARD_VERSION", "development")
        self._sleep = sleep
        self._demands: dict[str, bool] = {}
        self._lock = asyncio.Lock()
        self._was_enabled: bool | None = None
        self._startup_ready = False
        self._metrics_error: str | None = None
        self._learning_error: str | None = None
        self.thermal_learner = ThermalModelLearner(self.metrics_store)
        self.command_runner = HomeAssistantCommandRunner(
            ha,
            on_success=self._record_success,
            on_failure=self._record_command_failure,
            sleep=sleep,
        )
        self.executor = ControlExecutor(
            self.command_runner,
            record_event=self._record_event,
            set_error=self._set_error,
            latch_fault=lambda config, reason: self.safety.latch_fault(config, reason),
        )
        self.safety = SafetyManager(
            ha,
            store,
            self.status,
            self.notifier,
            self.override_store,
            self.executor,
            record_event=self._record_event,
        )
        self.overrides = OverrideService(
            store,
            self.override_store,
            self._lock,
            record_event=self._record_event,
            run_control_cycle=self.run_once,
        )

    def get_override_state(self, at: datetime | None = None) -> OverrideState:
        return self.overrides.state(at)

    @property
    def predictive_learning_error(self) -> str | None:
        return self._learning_error

    async def apply_override(
        self,
        request: OverrideActionRequest,
        *,
        actor: OverrideActor,
        action_id: str | None = None,
    ) -> OverrideState:
        return await self.overrides.apply(request, actor=actor, action_id=action_id)

    async def set_mode(
        self,
        mode: HeatingMode,
        *,
        source: str = "dashboard",
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        return await self.overrides.set_mode(mode, source=source, actor=actor)

    async def start_boost(
        self,
        room_group: str | None,
        duration_minutes: int,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        return await self.overrides.start_boost(
            room_group,
            duration_minutes,
            actor=actor,
        )

    async def cancel_boost(
        self,
        room_group: str | None,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        return await self.overrides.cancel_boost(room_group, actor=actor)

    async def start_hot_water_boost(
        self,
        duration_minutes: int,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        return await self.overrides.start_hot_water_boost(duration_minutes, actor=actor)

    async def cancel_hot_water_boost(
        self,
        *,
        actor: OverrideActor | None = None,
    ) -> OverrideState:
        return await self.overrides.cancel_hot_water_boost(actor=actor)

    async def clear_all_overrides(self, *, actor: OverrideActor) -> OverrideState:
        return await self.overrides.clear_all(actor=actor)

    async def handle_mode_event(self, event_data: dict[str, Any]) -> None:
        await self.overrides.handle_mode_event(event_data)

    async def handle_override_event(self, event_data: dict[str, Any]) -> None:
        await self.overrides.handle_override_event(event_data)

    async def preflight(self, config: HeatingConfig | None = None) -> PreflightResult:
        async with self._lock:
            config = config or self.store.load()
            if config is None:
                raise ValueError("Complete setup before checking automatic control")
            return build_preflight(config, await self.ha.get_states())

    async def emergency_stop(self) -> bool:
        async with self._lock:
            config = self.store.load()
            if config is None:
                raise ValueError("Heating is not configured")
            config.control.enabled = False
            config.updated_at = datetime.now().astimezone()
            self.store.save(config)
            stopped = await self.executor.turn_off_relays(
                config,
                "because an emergency stop was requested",
            )
            self._was_enabled = False
            self.executor.observe_boiler_off()
            self._record_event("warning", "Emergency stop requested; automatic control disabled")
            return stopped

    async def disable_control(self) -> bool:
        async with self._lock:
            config = self.store.load()
            if config is None:
                raise ValueError("Heating is not configured")
            config.control.enabled = False
            config.updated_at = datetime.now().astimezone()
            self.store.save(config)
            stopped = await self.executor.turn_off_relays(
                config,
                "because automatic control was disabled",
            )
            self._was_enabled = False
            self.executor.observe_boiler_off()
            self._record_event("warning", "Automatic control disabled; relays commanded off")
            return stopped

    async def apply_configuration_change(
        self,
        config: HeatingConfig,
        section: str,
    ) -> HeatingConfig:
        """Store a validated change without interrupting automatic control.

        The controller lock lets any in-flight cycle finish before the atomic config
        replacement. The immediate cycle after releasing the lock reconciles every
        output against the new configuration.
        """
        async with self._lock:
            previous = self.store.load()
            if previous is None:
                raise ValueError("Heating is not configured")

            relay_selection_changed = (
                previous.boiler_switch != config.boiler_switch
                or previous.hot_water_switch != config.hot_water_switch
            )
            if previous.control.enabled and relay_selection_changed:
                stopped = await self.executor.turn_off_relays(
                    previous,
                    f"before applying changed {section}",
                )
                if not stopped:
                    raise HomeAssistantError(
                        "The existing relays could not be confirmed off; the change was not saved"
                    )

            self.store.save(config)
            self._record_event(
                "info",
                f"Updated {section}; automatic control remains enabled",
            )

        await self.run_once()
        return self.store.load() or config

    async def acknowledge_fault(self) -> None:
        async with self._lock:
            await self.safety.acknowledge_fault()
            self._startup_ready = False

    async def send_test_notification(self) -> None:
        async with self._lock:
            config = self.store.load()
            if config is None:
                raise ValueError("Heating is not configured")
            await self.notifier.send_test(config.notifications)

    async def run_once(self, at: datetime | None = None) -> ControlPlan | None:
        async with self._lock:
            self.status.last_cycle_started_at = datetime.now().astimezone()
            self.status.actions = []
            config: HeatingConfig | None = None
            states: list[dict[str, Any]] | None = None
            plan: ControlPlan | None = None
            overrides = OverrideState()
            commands = CycleCommandResults()
            try:
                config = self.store.load()
                if config is None:
                    self.status.plan = None
                    self.status.last_error = None
                    return None
                if not config.setup.schedules:
                    self.status.plan = None
                    self.status.last_error = None
                    if config.control.enabled:
                        await self.executor.turn_off_relays(
                            config,
                            "because schedule setup is incomplete",
                        )
                    return None
                self.safety.restore_fault(config)
                if at is None:
                    try:
                        zone = ZoneInfo(await self.ha.get_time_zone())
                    except (HomeAssistantError, ZoneInfoNotFoundError):
                        zone = datetime.now().astimezone().tzinfo
                    at = datetime.now(zone)
                states = await self.ha.get_states()
                overrides = self.get_override_state(at)
                thermal_models = {}
                predictive_enabled = config.control.predictive_heating_enabled and any(
                    trv.predictive_heating_enabled for trv in config.trvs
                )
                if self._metrics_error is not None:
                    if predictive_enabled:
                        message = f"Metrics storage is unavailable: {self._metrics_error}"
                        if message != self._learning_error:
                            self._record_event(
                                "warning",
                                "Predictive learning is unavailable; standard hysteresis remains "
                                f"active: {message}",
                            )
                        self._learning_error = message
                else:
                    try:
                        thermal_models = await asyncio.to_thread(
                            self.thermal_learner.models,
                            [trv.device_id for trv in config.trvs],
                            at=at,
                        )
                    except Exception as exc:
                        message = f"{type(exc).__name__}: {exc}"
                        if predictive_enabled:
                            if message != self._learning_error:
                                self._record_event(
                                    "warning",
                                    "Predictive learning is unavailable; standard hysteresis "
                                    f"remains active: {message}",
                                )
                            self._learning_error = message
                            log.warning(
                                "Could not update predictive heating models; using standard "
                                "control: %s",
                                message,
                            )
                    else:
                        if self._learning_error is not None:
                            self._record_event("info", "Predictive heating learning recovered")
                        self._learning_error = None
                if not predictive_enabled:
                    self._learning_error = None
                plan = build_control_plan(
                    config,
                    states,
                    at,
                    self._demands,
                    overrides,
                    thermal_models,
                )
                self._demands = {room.device_id: room.demand for room in plan.rooms}
                self.status.plan = plan
                self.status.last_error = None
                was_enabled = self._was_enabled
                self._was_enabled = config.control.enabled
                if not config.control.enabled:
                    self.status.waiting_for_readiness = False
                if was_enabled is not None and was_enabled != config.control.enabled:
                    self._startup_ready = False
                if was_enabled is None:
                    mode = "automatic control" if config.control.enabled else "monitoring-only mode"
                    self._record_event("info", f"Controller started in {mode}")
                elif was_enabled != config.control.enabled:
                    mode = "enabled" if config.control.enabled else "disabled"
                    self._record_event(
                        "warning" if config.control.enabled is False else "info",
                        f"Automatic control {mode}",
                    )

                if was_enabled is None and not config.control.enabled:
                    states_by_entity = {
                        state["entity_id"]: state
                        for state in states
                        if isinstance(state.get("entity_id"), str)
                    }
                    relay_reported_on = any(
                        entity_id and states_by_entity.get(entity_id, {}).get("state") == "on"
                        for entity_id in (config.boiler_switch, config.hot_water_switch)
                    )
                    if self.status.fault_latched or relay_reported_on:
                        await self.executor.turn_off_relays(
                            config,
                            "during safe startup reconciliation",
                        )

                if self.status.fault_latched:
                    await self.safety.enforce_safety_stop(config, states)
                    if not self.status.notification_sent:
                        await self.safety.notify_fault(
                            self.status.fault_reason or "Unknown safety stop", config
                        )

                if config.control.enabled and not self.status.fault_latched:
                    if not self._startup_ready:
                        preflight = build_preflight(config, states)
                        if not preflight.ok:
                            failures = [
                                check.detail
                                for check in preflight.checks
                                if not check.passed and check.severity == "error"
                            ]
                            message = "Waiting for Home Assistant to finish starting: " + " ".join(
                                failures
                            )
                            self.status.last_error = message
                            self.status.waiting_for_readiness = True
                            self.status.consecutive_failures = 0
                            self._record_event("warning", message)
                            log.warning(message)
                        else:
                            self._startup_ready = True
                            self.status.consecutive_failures = 0
                            self.status.last_error = None
                            self.status.waiting_for_readiness = False
                            self._record_event(
                                "info", "Startup checks passed; automatic control is running"
                            )
                    if not self.status.fault_latched and self._startup_ready:
                        applied = await self.executor.apply(config, states, plan, commands)
                        if applied:
                            self.status.consecutive_failures = 0
                            await self.safety.resolve_control_alert(config)
                        elif not self.status.fault_latched:
                            await self.safety.register_failure(
                                config,
                                self.status.last_error or "One or more commands were not confirmed",
                            )
                elif was_enabled:
                    await self.executor.turn_off_relays(
                        config,
                        "because automatic control was disabled",
                    )

                try:
                    await self.notifier.reconcile(
                        config.notifications,
                        self.safety.notification_issues(config, states, plan),
                        at=at,
                    )
                except Exception as exc:
                    log.warning(
                        "Unexpected notification reconciliation failure (%s)",
                        type(exc).__name__,
                    )

                if self.status.last_error is None and not self.status.fault_latched:
                    self.status.last_successful_cycle_at = datetime.now().astimezone()
                return plan
            except HomeAssistantError as exc:
                self._startup_ready = False
                self.status.consecutive_failures = 0
                self.status.last_error = f"Waiting for Home Assistant: {exc}"
                self.status.waiting_for_readiness = bool(
                    config and config.control.enabled and not self.status.fault_latched
                )
                self._record_event("warning", self.status.last_error)
                log.warning("Heating control is waiting for Home Assistant: %s", exc)
                return None
            except (OSError, ValueError) as exc:
                self.status.waiting_for_readiness = False
                self.status.last_error = str(exc)
                self._record_event("error", f"Control cycle failed: {exc}")
                if config and config.control.enabled:
                    await self.safety.register_failure(config, str(exc))
                log.warning("Heating control cycle failed: %s", exc)
                return None
            finally:
                self.status.last_cycle_finished_at = datetime.now().astimezone()
                if config is not None and plan is not None:
                    await self._record_metrics(config, plan, overrides, commands)

    async def _record_metrics(
        self,
        config: HeatingConfig,
        plan: ControlPlan,
        overrides: OverrideState,
        commands: CycleCommandResults,
    ) -> None:
        try:
            await asyncio.to_thread(
                self.metrics_store.record_cycle,
                config=config,
                plan=plan,
                heating_mode=overrides.mode,
                commands=commands,
                cycle_started_at=self.status.last_cycle_started_at,
                cycle_finished_at=self.status.last_cycle_finished_at or datetime.now().astimezone(),
                cycle_successful=(self.status.last_error is None and not self.status.fault_latched),
                control_error=self.status.last_error,
                fault_latched=self.status.fault_latched,
                fault_reason=self.status.fault_reason,
                app_version=self._app_version,
                actions=tuple(self.status.actions),
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if message != self._metrics_error:
                self._record_event(
                    "warning",
                    f"Metrics storage failed; heating control is unaffected: {message}",
                )
            self._metrics_error = message
            log.warning("Could not record heating metrics; control is unaffected: %s", message)
        else:
            if self._metrics_error is not None:
                self._record_event("info", "Metrics storage recovered")
            self._metrics_error = None

    async def run_forever(self, *, maximum_cycles: int | None = None) -> None:
        unexpected_failures = 0
        cycles = 0
        self.status.loop_running = True
        try:
            while maximum_cycles is None or cycles < maximum_cycles:
                started = monotonic()
                try:
                    await self.run_once()
                    unexpected_failures = 0
                    cycles += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    unexpected_failures += 1
                    self.status.loop_restarts += 1
                    self.status.last_error = f"Unexpected controller failure: {exc}"
                    self._record_event("error", self.status.last_error)
                    log.exception("Unexpected heating controller failure")
                    if unexpected_failures >= 3:
                        raise RuntimeError(
                            "Heating controller repeatedly crashed; exiting for Supervisor restart"
                        ) from exc
                    await self._sleep(min(30, 2 ** (unexpected_failures - 1)))
                    continue
                try:
                    config = self.store.load()
                    interval = config.control.cycle_interval_seconds if config else 30
                except (OSError, ValueError):
                    interval = 30
                if maximum_cycles is None or cycles < maximum_cycles:
                    await self._sleep(max(1, interval - (monotonic() - started)))
        finally:
            self.status.loop_running = False

    def _record_event(self, level: str, message: str) -> None:
        self.events.record(level, message)

    def _record_success(self, description: str) -> None:
        self.status.actions.append(description)
        self._record_event("info", description)

    def _record_command_failure(self, description: str, message: str) -> None:
        self.status.actions.append(f"Failed: {description}")
        self.status.last_error = message
        self._record_event("error", f"Failed: {description} — {message}")
        log.warning("Control action failed (%s): %s", description, message)

    def _set_error(self, message: str) -> None:
        self.status.last_error = message
