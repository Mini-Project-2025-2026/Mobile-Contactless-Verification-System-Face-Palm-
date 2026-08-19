"""Reconciling our local score floor with the one the service actually applies.

The point of these is the relationship between two numbers, not either number
alone: a local floor below the service's threshold rejects nothing, and saying
so is the difference between a safety setting and the appearance of one.
"""
from __future__ import annotations

import pytest

from app import biometric, policy
from app.config import settings


def _config(**over) -> biometric.TenantConfig:
    base = dict(match_threshold=0.60, identify_margin=0.08, dupe_threshold=0.75,
                samples_per_user=5, active_liveness=True, palm_enabled=True)
    base.update(over)
    return biometric.TenantConfig(**base)


@pytest.fixture(autouse=True)
def clean_policy():
    policy.reset_cache()
    yield
    policy.reset_cache()


def test_the_service_threshold_wins_when_ours_is_lower(monkeypatch):
    monkeypatch.setattr(biometric, "tenant_config", lambda: _config(match_threshold=0.60))
    monkeypatch.setattr(settings, "min_verify_score", 0.40)

    floor = policy.effective_score_floor()
    assert floor.min_score == 0.60
    assert floor.source == "service"
    # and the local setting is named for what it is: inert
    assert floor.local_floor_is_inert is True


def test_a_stricter_local_floor_is_kept_and_named(monkeypatch):
    monkeypatch.setattr(biometric, "tenant_config", lambda: _config(match_threshold=0.60))
    monkeypatch.setattr(settings, "min_verify_score", 0.80)

    floor = policy.effective_score_floor()
    assert floor.min_score == 0.80
    assert floor.source == "local"
    assert floor.local_floor_is_inert is False


def test_an_unreadable_tenant_falls_back_to_the_local_floor(monkeypatch):
    def boom():
        raise biometric.BiometricError("unreachable")

    monkeypatch.setattr(biometric, "tenant_config", boom)
    monkeypatch.setattr(settings, "min_verify_score", 0.45)

    floor = policy.effective_score_floor()
    assert floor.min_score == 0.45
    assert floor.source == "local-only"


def test_an_outage_keeps_the_last_known_configuration(monkeypatch):
    """Thresholds that were right five minutes ago beat a value invented now."""
    monkeypatch.setattr(biometric, "tenant_config", lambda: _config(match_threshold=0.66))
    assert policy.tenant().match_threshold == 0.66

    def boom():
        raise biometric.BiometricError("unreachable")

    monkeypatch.setattr(biometric, "tenant_config", boom)
    assert policy.tenant(refresh=True).match_threshold == 0.66


def test_the_configuration_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(biometric, "tenant_config",
                        lambda: (calls.append(1), _config())[1])
    for _ in range(5):
        policy.tenant()
    assert len(calls) == 1


def test_palm_is_unavailable_when_the_tenant_has_it_off(monkeypatch):
    monkeypatch.setattr(biometric, "tenant_config", lambda: _config(palm_enabled=False))
    assert policy.palm_available() is False


def test_palm_is_assumed_available_when_the_tenant_cannot_be_asked(monkeypatch):
    """An outage must not silently withdraw a feature that does work."""
    def boom():
        raise biometric.BiometricError("unreachable")

    monkeypatch.setattr(biometric, "tenant_config", boom)
    assert policy.palm_available() is True


def test_describe_reports_both_numbers(monkeypatch):
    monkeypatch.setattr(biometric, "tenant_config", lambda: _config(match_threshold=0.60))
    monkeypatch.setattr(settings, "min_verify_score", 0.40)

    summary = policy.describe()
    assert summary["service_match_threshold"] == 0.60
    assert summary["local_floor"] == 0.40
    assert summary["min_score"] == 0.60
    assert summary["identify_margin"] == 0.08
    assert summary["samples_per_user"] == 5


def test_a_down_service_is_not_asked_again_on_every_request(monkeypatch):
    """Otherwise "the config is unreadable" quietly becomes "check-in is slow"."""
    calls = []

    def boom():
        calls.append(1)
        raise biometric.BiometricError("unreachable")

    monkeypatch.setattr(biometric, "tenant_config", boom)
    for _ in range(10):
        policy.effective_score_floor()
    assert len(calls) == 1
