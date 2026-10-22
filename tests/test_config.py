import json

from heat_steward.config import (
    ConfigStore,
    HeatingConfig,
    HotWaterPeriod,
    HotWaterSchedule,
    NotificationSettings,
    RoomSchedule,
    SchedulePeriod,
    SetupProgress,
    TemperaturePresets,
)
from heat_steward.discovery import OnboardingRequest, discover, materialise_config
from pydantic import ValidationError


def test_config_round_trip_is_versioned_and_atomic(tmp_path, registry_snapshot):
    result = discover(registry_snapshot)
    request = OnboardingRequest.model_validate(
        {
            "trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}],
        }
    )
    expected = materialise_config(request, result)
    store = ConfigStore(tmp_path / "nested" / "heat-steward-config.json")

    store.save(expected)
    actual = store.load()

    assert actual == expected
    assert actual.schema_version == 10
    assert actual.presets == TemperaturePresets(boost=23, comfort=21, eco=16, frost=5)
    assert actual.setup == SetupProgress(devices=True, presets=False, schedules=False)
    assert list((tmp_path / "nested").glob("*.tmp")) == []

    legacy_payload = expected.model_dump(mode="json")
    legacy_payload["boiler_switch"] = "switch.boiler_switch_central_heating_switch"
    legacy_payload["hot_water_switch"] = "switch.boiler_switch_hot_water_switch"
    del legacy_payload["presets"]
    del legacy_payload["setup"]
    del legacy_payload["control"]
    del legacy_payload["schedules"]
    del legacy_payload["hot_water_schedule"]
    store.path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    assert store.load().presets == TemperaturePresets()
    assert store.load().setup == SetupProgress(boiler=True)
    assert store.load().schedules == []
    assert store.load().hot_water_schedule == HotWaterSchedule()
    assert store.load().control.enabled is False
    assert store.load().control.temperature_source == "internal"
    assert store.load().control.away_preset == "frost"
    assert store.load().control.maximum_boiler_on_seconds == 14400
    assert store.load().control.command_retries == 2
    assert store.load().control.failures_before_alert == 3
    assert store.load().control.external_sensor_stale_seconds == 5400
    assert store.load().control.trv_stale_seconds == 3600
    assert store.load().trvs[0].predictive_heating_enabled is False
    assert store.load().control.predictive_heating_enabled is False
    assert store.load().control.maximum_preheat_seconds == 7200
    assert store.load().safety.fault_latched is False


def test_schema_four_default_trv_timeout_is_migrated(registry_snapshot):
    result = discover(registry_snapshot)
    request = OnboardingRequest.model_validate(
        {"trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}]}
    )
    payload = materialise_config(request, result).model_dump(mode="json")
    payload["schema_version"] = 4
    payload["control"]["trv_stale_seconds"] = 900

    migrated = HeatingConfig.model_validate(payload)

    assert migrated.schema_version == 10
    assert migrated.control.trv_stale_seconds == 3600


def test_schema_five_failure_threshold_is_migrated_to_alert_threshold(registry_snapshot):
    result = discover(registry_snapshot)
    request = OnboardingRequest.model_validate(
        {"trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}]}
    )
    payload = materialise_config(request, result).model_dump(mode="json")
    payload["schema_version"] = 5
    payload["control"]["failures_before_fault"] = 7
    del payload["control"]["failures_before_alert"]

    migrated = HeatingConfig.model_validate(payload)

    assert migrated.schema_version == 10
    assert migrated.control.failures_before_alert == 7


def test_schema_seven_global_predictive_setting_migrates_to_safe_room_defaults(
    registry_snapshot,
):
    result = discover(registry_snapshot)
    request = OnboardingRequest.model_validate(
        {"trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}]}
    )
    payload = materialise_config(request, result).model_dump(mode="json")
    payload["schema_version"] = 7
    payload["control"]["predictive_heating_enabled"] = True
    del payload["trvs"][0]["predictive_heating_enabled"]

    migrated = HeatingConfig.model_validate(payload)

    assert migrated.schema_version == 10
    assert migrated.trvs[0].predictive_heating_enabled is False
    assert migrated.control.predictive_heating_enabled is False


def test_schema_four_custom_trv_timeout_is_preserved(registry_snapshot):
    result = discover(registry_snapshot)
    request = OnboardingRequest.model_validate(
        {"trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}]}
    )
    payload = materialise_config(request, result).model_dump(mode="json")
    payload["schema_version"] = 4
    payload["control"]["trv_stale_seconds"] = 7200

    migrated = HeatingConfig.model_validate(payload)

    assert migrated.schema_version == 10
    assert migrated.control.trv_stale_seconds == 7200


def test_schema_nine_removes_retired_early_stop_setting(registry_snapshot):
    result = discover(registry_snapshot)
    request = OnboardingRequest.model_validate(
        {"trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}]}
    )
    payload = materialise_config(request, result).model_dump(mode="json")
    payload["schema_version"] = 9
    payload["control"]["maximum_early_stop_seconds"] = 1800

    migrated = HeatingConfig.model_validate(payload)

    assert migrated.schema_version == 10
    assert "maximum_early_stop_seconds" not in migrated.control.model_dump()


def test_presets_must_descend_from_boost_to_frost():
    try:
        TemperaturePresets(boost=21, comfort=23, eco=16, frost=5)
    except ValidationError as exc:
        assert "descend from Boost to Frost" in str(exc)
    else:
        raise AssertionError("Expected invalid preset order to be rejected")


def test_notifications_require_a_real_discord_webhook():
    try:
        NotificationSettings(
            enabled=True,
            discord_webhook_url="https://example.com/api/webhooks/123/not-discord",
        )
    except ValidationError as exc:
        assert "valid HTTPS Discord webhook URL" in str(exc)
    else:
        raise AssertionError("Expected a non-Discord webhook URL to be rejected")


def test_room_schedule_rejects_overlapping_periods():
    try:
        RoomSchedule(
            room_group="Downstairs",
            periods=[
                SchedulePeriod(days=["monday"], start="06:30", end="09:00", preset="comfort"),
                SchedulePeriod(days=["monday"], start="08:30", end="10:00", preset="boost"),
            ],
        )
    except ValidationError as exc:
        assert "overlap on Monday" in str(exc)
    else:
        raise AssertionError("Expected overlapping schedule periods to be rejected")


def test_hot_water_schedule_rejects_overlapping_periods():
    try:
        HotWaterSchedule(
            periods=[
                HotWaterPeriod(days=["monday"], start="06:30", end="09:00", state="on"),
                HotWaterPeriod(days=["monday"], start="08:30", end="10:00", state="off"),
            ]
        )
    except ValidationError as exc:
        assert "overlap on Monday" in str(exc)
    else:
        raise AssertionError("Expected overlapping hot-water periods to be rejected")


def test_completed_schedules_must_cover_every_room_group(registry_snapshot):
    result = discover(registry_snapshot)
    request = OnboardingRequest.model_validate(
        {
            "trvs": [{"device_id": "device-dining", "room_group": "Dining Room"}],
        }
    )
    payload = materialise_config(request, result).model_dump(mode="json")
    payload["setup"]["schedules"] = True

    try:
        HeatingConfig.model_validate(payload)
    except ValidationError as exc:
        assert "must include every room group" in str(exc)
    else:
        raise AssertionError("Expected an incomplete completed schedule to be rejected")
