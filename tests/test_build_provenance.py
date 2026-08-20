from __future__ import annotations

from orbita_agent.build_provenance import public_build_provenance


def test_build_provenance_exposes_only_non_secret_runtime_identity(monkeypatch):
    commit = "a" * 40
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", commit)
    monkeypatch.setenv("RAILWAY_GIT_BRANCH", "agent/arc-audit-hardening")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deployment-1")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "service-1")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "environment-1")
    monkeypatch.setenv("ORBITA_AGENT_API_TOKEN", "must-not-appear")

    provenance = public_build_provenance()

    assert provenance == {
        "schema": "hodgeform-build-provenance/1",
        "commit_sha": commit,
        "branch": "agent/arc-audit-hardening",
        "deployment_id": "deployment-1",
        "service_id": "service-1",
        "environment_id": "environment-1",
        "source": "runtime_environment",
        "secrets_included": False,
    }
    assert "must-not-appear" not in repr(provenance)


def test_build_provenance_rejects_abbreviated_commit(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")

    assert public_build_provenance()["commit_sha"] is None


def test_capabilities_include_public_build_provenance(gateway, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "b" * 40)

    provenance = gateway.capabilities()["build_provenance"]

    assert provenance["commit_sha"] == "b" * 40
    assert provenance["secrets_included"] is False
