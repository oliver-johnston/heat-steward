from __future__ import annotations

from datetime import UTC, datetime, timedelta

from heat_steward.predictive import learn_room_model


def observation(
    observed_at: datetime,
    temperature: float,
    *,
    demand: bool,
    boiler_on: bool,
    source: str = "external",
) -> dict[str, object]:
    return {
        "observed_at": observed_at,
        "current_temperature": temperature,
        "demand": demand,
        "boiler_on": boiler_on,
        "temperature_source": source,
        "current_temperature_entity": f"sensor.room_{source}_temperature",
    }


def learned_history(at: datetime) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for days_ago in (3, 2, 1):
        started = at - timedelta(days=days_ago, hours=2)
        rows.extend(
            [
                observation(started, 18.0, demand=True, boiler_on=True),
                observation(
                    started + timedelta(minutes=15),
                    18.25,
                    demand=True,
                    boiler_on=True,
                ),
                observation(
                    started + timedelta(minutes=30),
                    18.5,
                    demand=True,
                    boiler_on=True,
                ),
                observation(
                    started + timedelta(minutes=31),
                    18.5,
                    demand=False,
                    boiler_on=True,
                ),
                observation(
                    started + timedelta(minutes=41),
                    18.7,
                    demand=False,
                    boiler_on=False,
                ),
                observation(
                    started + timedelta(minutes=51),
                    18.8,
                    demand=False,
                    boiler_on=False,
                ),
            ]
        )
    return rows


def test_learns_heating_rate_from_complete_episodes():
    at = datetime(2026, 10, 16, 12, tzinfo=UTC)

    model = learn_room_model("dining", learned_history(at), at=at)

    assert model.temperature_source == "external"
    assert model.temperature_entity == "sensor.room_external_temperature"
    assert model.heating_samples == 3
    assert model.heating_rate_c_per_hour is not None
    assert round(model.heating_rate_c_per_hour, 2) == 0.97
    assert model.heating_ready(at) is True
    assert model.status(at) == "ready"


def test_sparse_or_stale_history_never_becomes_actionable():
    at = datetime(2026, 10, 16, 12, tzinfo=UTC)
    sparse = learned_history(at)[:6]

    sparse_model = learn_room_model("dining", sparse, at=at)
    stale_model = learn_room_model(
        "dining",
        learned_history(at - timedelta(days=20)),
        at=at,
    )

    assert sparse_model.status(at) == "learning"
    assert sparse_model.heating_ready(at) is False
    assert stale_model.status(at) == "stale"
    assert stale_model.heating_ready(at) is False


def test_demand_closing_reading_completes_sparse_heating_episode():
    at = datetime(2026, 10, 22, 8, tzinfo=UTC)
    started = at - timedelta(hours=2)
    rows = [
        observation(started, 20.0, demand=True, boiler_on=True),
        observation(started + timedelta(minutes=15), 20.6, demand=True, boiler_on=True),
        observation(started + timedelta(minutes=30), 20.6, demand=True, boiler_on=True),
        observation(started + timedelta(minutes=45), 20.6, demand=True, boiler_on=True),
        observation(started + timedelta(minutes=60), 20.6, demand=True, boiler_on=True),
        observation(started + timedelta(minutes=78), 20.6, demand=True, boiler_on=True),
        observation(started + timedelta(minutes=79), 21.6, demand=False, boiler_on=True),
    ]

    model = learn_room_model("office", rows, at=at)

    assert model.heating_samples == 1
    assert model.heating_rate_c_per_hour is not None
    assert round(model.heating_rate_c_per_hour, 2) == 1.22


def test_implausible_episode_does_not_pull_a_stable_model_off_course():
    at = datetime(2026, 10, 16, 12, tzinfo=UTC)
    rows = learned_history(at)
    started = at - timedelta(hours=5)
    rows.extend(
        [
            observation(started, 17.0, demand=True, boiler_on=True),
            observation(
                started + timedelta(minutes=15),
                18.0,
                demand=True,
                boiler_on=True,
            ),
            observation(
                started + timedelta(minutes=30),
                19.0,
                demand=True,
                boiler_on=True,
            ),
            observation(
                started + timedelta(minutes=31),
                19.0,
                demand=False,
                boiler_on=True,
            ),
            observation(
                started + timedelta(minutes=41),
                19.0,
                demand=False,
                boiler_on=False,
            ),
        ]
    )

    model = learn_room_model("dining", rows, at=at)

    assert model.heating_samples == 4
    assert model.heating_rate_c_per_hour is not None
    assert round(model.heating_rate_c_per_hour, 2) == 0.97


def test_sensor_source_change_relearns_instead_of_splicing_temperatures():
    at = datetime(2026, 10, 16, 12, tzinfo=UTC)
    rows = learned_history(at)
    started = at - timedelta(hours=1)
    rows.extend(
        [
            observation(started, 19.0, demand=True, boiler_on=True, source="trv"),
            observation(
                started + timedelta(minutes=15),
                19.2,
                demand=True,
                boiler_on=True,
                source="trv",
            ),
            observation(
                started + timedelta(minutes=30),
                19.4,
                demand=True,
                boiler_on=True,
                source="trv",
            ),
            observation(
                started + timedelta(minutes=31),
                19.4,
                demand=False,
                boiler_on=False,
                source="trv",
            ),
        ]
    )

    model = learn_room_model("dining", rows, at=at)

    assert model.temperature_source == "trv"
    assert model.heating_samples == 1
    assert model.heating_ready(at) is False
