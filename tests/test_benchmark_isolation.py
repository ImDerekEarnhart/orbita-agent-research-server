from __future__ import annotations

import hashlib
import json

import pytest
from starlette.testclient import TestClient

from orbita_agent.benchmark_isolation import BenchmarkIsolationProbe
from orbita_agent.config import AgentConfig
from orbita_agent.gateway import AgentGateway
from orbita_agent.mcp_server import build_mcp_server

COMMIT = "1" * 40
TOKEN = "benchmark-token-" + ("x" * 48)


def _set_common(monkeypatch, *, home, cache, condition="B3"):
    values = {
        "ORBITA_BENCHMARK_CONDITION": condition,
        "ORBITA_BENCHMARK_REPLICATE_ID": "replicate_01",
        "ORBITA_BENCHMARK_PACKET_ID": "synthetic_packet_01",
        "ORBITA_BENCHMARK_BOOT_NONCE": "nonce-" + ("n" * 48),
        "ORBITA_BENCHMARK_EXPECTED_COMMIT": COMMIT,
        "GIT_COMMIT_SHA": COMMIT,
        "ORBITA_AGENT_HOME": str(home),
        "ORBITA_BENCHMARK_CACHE_ROOT": str(cache),
        "ORBITA_AGENT_AUTH_MODE": "bearer",
        "ORBITA_AGENT_REQUIRE_AUTH": "1",
        "ORBITA_AGENT_API_TOKEN": TOKEN,
    }
    for name in (
        "ORBITA_DISCOVERY_GENOME_URL",
        "ORBITA_DISCOVERY_GENOME_SERVICE_TOKEN",
        "ORBITA_DISCOVERY_GENOME_USERNAME",
        "RAILWAY_VOLUME_ID",
        "RAILWAY_VOLUME_NAME",
        "RAILWAY_VOLUME_MOUNT_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _canonical_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_b3_empty_boot_is_hash_attested_without_secrets_or_packet_access(tmp_path, monkeypatch):
    home = tmp_path / "fresh-home"
    cache = tmp_path / "fresh-cache"
    _set_common(monkeypatch, home=home, cache=cache)

    config = AgentConfig(home=home)
    probe = BenchmarkIsolationProbe.capture(config)
    assert probe is not None
    gateway = AgentGateway(config)
    try:
        receipt = probe.finalize(
            gateway,
            authentication="bearer",
            guided_bridge_disabled=True,
        )
    finally:
        gateway.close()

    receipt_hash = receipt.pop("attestation_sha256")
    assert receipt_hash == _canonical_hash(receipt)
    assert receipt["status"] == "VERIFIED_EMPTY_BOOT"
    assert receipt["condition"] == "B3"
    assert receipt["gateway_case_count_after_initialization"] == 0
    assert receipt["home_before_gateway"]["entry_count"] == 0
    assert receipt["packet_content_accessed_by_attestor"] is False
    assert receipt["secrets_in_attestation"] is False
    assert TOKEN not in json.dumps(receipt)


def test_b3_route_is_administrative_and_does_not_change_mcp_tool_surface(tmp_path, monkeypatch):
    home = tmp_path / "fresh-home"
    cache = tmp_path / "fresh-cache"
    _set_common(monkeypatch, home=home, cache=cache)

    mcp, gateway = build_mcp_server(config=AgentConfig(home=home))
    try:
        assert "benchmark_isolation" not in {
            tool.name for tool in mcp._tool_manager.list_tools()
        }
        with TestClient(mcp.streamable_http_app()) as client:
            response = client.get("/benchmark-isolation")
        assert response.status_code == 200
        assert response.json()["status"] == "VERIFIED_EMPTY_BOOT"
        assert response.json()["packet_id"] == "synthetic_packet_01"
    finally:
        gateway.close()


def test_b3_fails_closed_when_home_contains_prior_state(tmp_path, monkeypatch):
    home = tmp_path / "reused-home"
    home.mkdir()
    (home / "old-state.db").write_bytes(b"not benchmark data")
    _set_common(monkeypatch, home=home, cache=tmp_path / "cache")

    with pytest.raises(RuntimeError, match="home was not empty"):
        BenchmarkIsolationProbe.capture(AgentConfig(home=home))


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("RAILWAY_VOLUME_ID", "volume_123", "must not have a Railway volume"),
        ("ORBITA_DISCOVERY_GENOME_URL", "https://guided.invalid", "Guided/Genome bridge"),
    ],
)
def test_b3_fails_closed_on_volume_or_external_guided_bridge(
    tmp_path, monkeypatch, name, value, message
):
    home = tmp_path / "fresh-home"
    _set_common(monkeypatch, home=home, cache=tmp_path / "cache")
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        BenchmarkIsolationProbe.capture(AgentConfig(home=home))


def test_b2_requires_empty_replicate_scoped_volume_paths(tmp_path, monkeypatch):
    volume = tmp_path / "volume"
    home = volume / "replicate_01" / "home"
    cache = volume / "replicate_01" / "cache"
    _set_common(monkeypatch, home=home, cache=cache, condition="B2")
    monkeypatch.delenv("ORBITA_BENCHMARK_PACKET_ID", raising=False)
    monkeypatch.setenv("RAILWAY_VOLUME_ID", "volume_123")
    monkeypatch.setenv("RAILWAY_VOLUME_NAME", "orbita-sharb-b2")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(volume))

    probe = BenchmarkIsolationProbe.capture(AgentConfig(home=home))

    assert probe is not None
    assert probe.condition == "B2"
    assert probe.packet_id is None
    assert probe.volume_mount_path == str(volume)


def test_benchmark_mode_refuses_a_gateway_initialized_before_attestation(
    tmp_path, monkeypatch
):
    home = tmp_path / "fresh-home"
    _set_common(monkeypatch, home=home, cache=tmp_path / "cache")
    gateway = AgentGateway(AgentConfig(home=home))
    try:
        with pytest.raises(RuntimeError, match="pre-initialized gateway"):
            build_mcp_server(gateway=gateway)
    finally:
        gateway.close()

