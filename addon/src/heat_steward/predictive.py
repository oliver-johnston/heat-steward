from __future__ import annotations

import math
import statistics
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

MIN_EPISODES = 3
LEARNING_HORIZON = timedelta(days=30)
MODEL_STALE_AFTER = timedelta(days=14)
REFRESH_INTERVAL = timedelta(hours=1)
MAX_SAMPLE_GAP = timedelta(minutes=20)
MIN_HEATING_DURATION = timedelta(minutes=5)
MAX_HEATING_DURATION = timedelta(hours=4)


@dataclass(frozen=True)
class ThermalObservation:
    observed_at: datetime
    temperature: float
    demand: bool
    boiler_on: bool | None
    temperature_source: str
    temperature_entity: str


@dataclass(frozen=True)
class RoomThermalModel:
    device_id: str
    temperature_source: str | None = None
    temperature_entity: str | None = None
    heating_rate_c_per_hour: float | None = None
    heating_samples: int = 0
    heating_updated_at: datetime | None = None
    confidence: float = 0.0

    def heating_ready(self, at: datetime) -> bool:
        return (
            self.heating_rate_c_per_hour is not None
            and self.heating_samples >= MIN_EPISODES
            and self.heating_updated_at is not None
            and at.astimezone(UTC) - self.heating_updated_at <= MODEL_STALE_AFTER
        )

    def status(self, at: datetime) -> str:
        if self.heating_ready(at):
            return "ready"
        newest = self.heating_updated_at
        if newest and at.astimezone(UTC) - newest > MODEL_STALE_AFTER:
            return "stale"
        return "learning"

    def actionable(self, at: datetime) -> bool:
        return self.heating_ready(at)

    def as_dict(self, at: datetime) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "temperature_source": self.temperature_source,
            "temperature_entity": self.temperature_entity,
            "status": self.status(at),
            "ready": self.actionable(at),
            "heating_ready": self.heating_ready(at),
            "heating_rate_c_per_hour": self.heating_rate_c_per_hour,
            "heating_samples": self.heating_samples,
            "heating_updated_at": (
                self.heating_updated_at.isoformat() if self.heating_updated_at else None
            ),
            "confidence": self.confidence,
        }


def _normalise_observations(rows: list[dict[str, Any]]) -> list[ThermalObservation]:
    observations: list[ThermalObservation] = []
    for row in rows:
        observed_at = row.get("observed_at")
        temperature = row.get("current_temperature")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            continue
        if not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
            continue
        observations.append(
            ThermalObservation(
                observed_at=observed_at.astimezone(UTC),
                temperature=float(temperature),
                demand=bool(row.get("demand")),
                boiler_on=(bool(row["boiler_on"]) if row.get("boiler_on") is not None else None),
                temperature_source=str(row.get("temperature_source") or "trv"),
                temperature_entity=str(row.get("current_temperature_entity") or "unknown"),
            )
        )
    observations.sort(key=lambda item: item.observed_at)
    return observations


def _robust_weighted_average(samples: list[tuple[float, datetime]], at: datetime) -> float | None:
    if not samples:
        return None
    values = [value for value, _ in samples]
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    tolerance = max(3 * mad, 0.25, abs(median) * 0.5)
    filtered = [sample for sample in samples if abs(sample[0] - median) <= tolerance]
    if not filtered:
        filtered = samples
    now = at.astimezone(UTC)
    weighted = [
        (value, 0.5 ** (max(0.0, (now - observed_at).total_seconds()) / 604800))
        for value, observed_at in filtered
    ]
    total_weight = sum(weight for _, weight in weighted)
    return sum(value * weight for value, weight in weighted) / total_weight


def learn_room_model(
    device_id: str,
    rows: list[dict[str, Any]],
    *,
    at: datetime,
) -> RoomThermalModel:
    """Learn a robust room heating-rate estimate from completed heating episodes."""
    observations = _normalise_observations(rows)
    if not observations:
        return RoomThermalModel(device_id=device_id)

    # Never splice together readings from different sensors. Prefer the entity used by
    # the latest observation so a sensor configuration change naturally relearns.
    temperature_source = observations[-1].temperature_source
    temperature_entity = observations[-1].temperature_entity
    observations = [
        observation
        for observation in observations
        if observation.temperature_source == temperature_source
        and observation.temperature_entity == temperature_entity
    ]

    heating_samples: list[tuple[float, datetime]] = []
    episode: list[ThermalObservation] = []

    def finish_heating() -> None:
        nonlocal episode
        if len(episode) >= 2:
            duration = episode[-1].observed_at - episode[0].observed_at
            rise = episode[-1].temperature - episode[0].temperature
            if MIN_HEATING_DURATION <= duration <= MAX_HEATING_DURATION and rise >= 0.1:
                rate = rise / (duration.total_seconds() / 3600)
                if 0.05 <= rate <= 5.0:
                    heating_samples.append((rate, episode[-1].observed_at))
        episode = []

    for observation in observations:
        heating = observation.demand and observation.boiler_on is True
        if not heating:
            # The temperature update that reaches the target also causes demand to close.
            # Treat that boundary reading as the end of the heating episode so sparse
            # sensors do not lose their final, often largest, reported rise.
            if (
                episode
                and not observation.demand
                and observation.observed_at - episode[-1].observed_at <= MAX_SAMPLE_GAP
            ):
                episode.append(observation)
            finish_heating()
            continue
        if episode and observation.observed_at - episode[-1].observed_at > MAX_SAMPLE_GAP:
            finish_heating()
        episode.append(observation)

    heating_rate = _robust_weighted_average(heating_samples, at)
    newest = max((timestamp for _, timestamp in heating_samples), default=None)
    recency = (
        0.5
        ** (
            max(0.0, (at.astimezone(UTC) - newest).total_seconds())
            / MODEL_STALE_AFTER.total_seconds()
        )
        if newest
        else 0.0
    )
    evidence = min(1.0, len(heating_samples) / 6)
    return RoomThermalModel(
        device_id=device_id,
        temperature_source=temperature_source,
        temperature_entity=temperature_entity,
        heating_rate_c_per_hour=heating_rate,
        heating_samples=len(heating_samples),
        heating_updated_at=(
            max(timestamp for _, timestamp in heating_samples) if heating_samples else None
        ),
        confidence=round(evidence * recency, 3),
    )


class ThermalModelLearner:
    """Caches adaptive models while keeping SQLite work out of the control-loop thread."""

    def __init__(self, metrics_store: Any, *, refresh_interval: timedelta = REFRESH_INTERVAL):
        self.metrics_store = metrics_store
        self.refresh_interval = refresh_interval
        self._lock = threading.Lock()
        self._refreshed_at: datetime | None = None
        self._models: dict[str, RoomThermalModel] = {}

    def models(self, device_ids: list[str], *, at: datetime) -> dict[str, RoomThermalModel]:
        now = at.astimezone(UTC)
        with self._lock:
            cache_age = now - self._refreshed_at if self._refreshed_at else None
            if (
                cache_age is not None
                and timedelta(0) <= cache_age < self.refresh_interval
                and set(device_ids).issubset(self._models)
            ):
                return {device_id: self._models[device_id] for device_id in device_ids}

            rows = self.metrics_store.thermal_observations(
                device_ids,
                start_at=now - LEARNING_HORIZON,
                end_at=now,
            )
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[str(row["device_id"])].append(row)
            self._models = {
                device_id: learn_room_model(device_id, grouped[device_id], at=now)
                for device_id in device_ids
            }
            self._refreshed_at = now
            return dict(self._models)
