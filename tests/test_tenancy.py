from __future__ import annotations

import json

import pytest

from orbita_agent.genome_client import DiscoveryGenomeClient, DiscoveryGenomeConfig, DiscoveryGenomeError
from orbita_agent.tenancy import (
    LEGACY_BINDING_SOURCE,
    OPERATOR_BINDING_SOURCE,
    LegacySinglePrincipal,
    TenantRegistry,
    TenantResolutionError,
    build_registry,
)


@pytest.fixture
def registry(tmp_path) -> TenantRegistry:
    return TenantRegistry(tmp_path / "tenants.db")


def test_unbound_subject_is_refused_and_never_served_a_default(registry):
    with pytest.raises(TenantResolutionError, match="no Discovery Genome tenant is bound"):
        registry.resolve("github:1234")


@pytest.mark.parametrize("subject", [None, "", "   "])
def test_unauthenticated_request_is_refused(registry, subject):
    with pytest.raises(TenantResolutionError, match="requires an authenticated identity"):
        registry.resolve(subject)


def test_distinct_subjects_resolve_to_their_own_tenants(registry):
    registry.bind("github:1", "alice")
    registry.bind("github:2", "bob")
    assert registry.resolve("github:1") == "alice"
    assert registry.resolve("github:2") == "bob"


def test_a_tenant_is_not_silently_shared_between_subjects(registry):
    registry.bind("github:1", "alice")
    with pytest.raises(TenantResolutionError, match="already bound to this Discovery Genome tenant"):
        registry.bind("github:2", "alice")
    assert registry.resolve("github:1") == "alice"


def test_collaborators_can_share_a_tenant_only_deliberately(registry):
    registry.bind("github:1", "alice")
    registry.bind("github:2", "alice", allow_shared=True)
    assert registry.resolve("github:2") == "alice"


def test_rebinding_a_subject_requires_an_explicit_overwrite(registry):
    registry.bind("github:1", "alice")
    with pytest.raises(TenantResolutionError, match="already bound to a different"):
        registry.bind("github:1", "bob")
    assert registry.resolve("github:1") == "alice"


def test_rebinding_the_same_tenant_is_idempotent(registry):
    first = registry.bind("github:1", "alice")
    second = registry.bind("github:1", "alice")
    assert second.bound_at == first.bound_at


def test_unbind_revokes_access_and_is_audited(registry):
    registry.bind("github:1", "alice")
    assert registry.unbind("github:1") is True
    with pytest.raises(TenantResolutionError):
        registry.resolve("github:1")
    actions = [event["action"] for event in registry.list_events()]
    assert actions == ["unbind", "bind"]


def test_unbind_reports_a_missing_binding(registry):
    assert registry.unbind("github:404") is False


# -- legacy single-principal compatibility ------------------------------------


def test_legacy_single_principal_auto_binds_the_one_allowed_login(tmp_path):
    registry = TenantRegistry(
        tmp_path / "tenants.db",
        legacy=LegacySinglePrincipal(github_login="DerekEarnhart", genome_username="dkscr711"),
    )
    registry.record_identity("github:1234", "DerekEarnhart")
    assert registry.resolve("github:1234") == "dkscr711"

    binding = registry.list_bindings()[0]
    assert binding.bound_by == LEGACY_BINDING_SOURCE
    assert binding.genome_username == "dkscr711"


def test_legacy_mode_does_not_serve_a_different_signed_in_login(tmp_path):
    registry = TenantRegistry(
        tmp_path / "tenants.db",
        legacy=LegacySinglePrincipal(github_login="DerekEarnhart", genome_username="dkscr711"),
    )
    registry.record_identity("github:999", "SomeoneElse")
    with pytest.raises(TenantResolutionError, match="no Discovery Genome tenant is bound"):
        registry.resolve("github:999")


def test_legacy_mode_disengages_when_a_second_user_is_allowed(monkeypatch):
    monkeypatch.setenv("ORBITA_DISCOVERY_GENOME_USERNAME", "dkscr711")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart")
    assert LegacySinglePrincipal.from_env() is not None

    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart,SecondUser")
    assert LegacySinglePrincipal.from_env() is None


def test_legacy_mode_requires_a_configured_genome_username(monkeypatch):
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_USERNAME", raising=False)
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart")
    assert LegacySinglePrincipal.from_env() is None


# -- environment seeding -------------------------------------------------------


def test_env_seed_creates_bindings_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ORBITA_GENOME_TENANT_BINDINGS", json.dumps({"github:1": "alice", "github:2": "bob"})
    )
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_USERNAME", raising=False)

    first = build_registry(tmp_path)
    assert first.resolve("github:1") == "alice"
    assert first.resolve("github:2") == "bob"

    second = build_registry(tmp_path)
    assert len(second.list_bindings()) == 2


def test_env_seed_never_overwrites_an_operator_binding(tmp_path, monkeypatch):
    monkeypatch.delenv("ORBITA_GENOME_TENANT_BINDINGS", raising=False)
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_USERNAME", raising=False)
    registry = build_registry(tmp_path)
    registry.bind("github:1", "operator-choice", actor=OPERATOR_BINDING_SOURCE)

    monkeypatch.setenv("ORBITA_GENOME_TENANT_BINDINGS", json.dumps({"github:1": "env-choice"}))
    reloaded = build_registry(tmp_path)
    assert reloaded.resolve("github:1") == "operator-choice"


def test_malformed_env_seed_is_rejected_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("ORBITA_GENOME_TENANT_BINDINGS", "not-json")
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        build_registry(tmp_path)


# -- the resolved tenant actually reaches the wire ------------------------------


def test_resolved_tenant_is_sent_as_the_genome_user_header(monkeypatch):
    client = DiscoveryGenomeClient(
        DiscoveryGenomeConfig(base_url="https://guided.example", service_token="t" * 48)
    )
    captured: dict[str, str] = {}

    class FakeResponse:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout=None):
        captured.update(request.headers)
        return FakeResponse()

    monkeypatch.setattr("orbita_agent.genome_client.urlopen", fake_urlopen)
    client.for_username("alice").list_operators()
    assert captured["X-orbita-genome-user"] == "alice"


def test_an_unbound_client_cannot_reach_the_genome_service(monkeypatch):
    client = DiscoveryGenomeClient(
        DiscoveryGenomeConfig(base_url="https://guided.example", service_token="t" * 48)
    )

    def explode(*_args, **_kwargs):
        raise AssertionError("an unbound client must not perform a request")

    monkeypatch.setattr("orbita_agent.genome_client.urlopen", explode)
    with pytest.raises(DiscoveryGenomeError, match="not bound to a tenant"):
        client.list_operators()


def test_deployment_configuration_no_longer_requires_a_tenant_username():
    config = DiscoveryGenomeConfig(base_url="https://guided.example", service_token="t" * 48)
    assert config.missing() == []
