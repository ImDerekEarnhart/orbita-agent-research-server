from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

import pytest

import orbita_agent.genome_client as genome_client
from orbita_agent.genome_client import (
    OPERATOR_FREEZE_PHRASE,
    RESULT_RECORD_PHRASE,
    TOURNAMENT_FREEZE_PHRASE,
    DiscoveryGenomeClient,
    DiscoveryGenomeConfig,
    DiscoveryGenomeError,
    hash_json,
    tournament_result_receipt,
)


class FakeGenomeClient(DiscoveryGenomeClient):
    def __init__(self, responses):
        super().__init__(
            DiscoveryGenomeConfig(
                base_url="https://guided.example",
                service_token="t" * 48,
                username="derek",
            )
        )
        self.responses = list(responses)
        self.calls = []

    def _request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        return self.responses.pop(0)


def test_status_reports_missing_configuration_without_network():
    client = DiscoveryGenomeClient(
        DiscoveryGenomeConfig(base_url="", service_token="", username="")
    )
    status = client.status()
    assert status["configured"] is False
    assert "ORBITA_DISCOVERY_GENOME_URL" in status["missing"]


def test_socket_timeout_is_redacted_as_unavailable(monkeypatch):
    client = DiscoveryGenomeClient(
        DiscoveryGenomeConfig(
            base_url="https://guided.example",
            service_token="t" * 48,
            username="derek",
        )
    )

    def timeout_urlopen(*_args, **_kwargs):
        raise TimeoutError("private socket detail")

    monkeypatch.setattr(genome_client, "urlopen", timeout_urlopen)
    with pytest.raises(DiscoveryGenomeError, match="service is unavailable") as exc_info:
        client.status()
    assert "private socket detail" not in str(exc_info.value)


def test_http_error_body_is_not_exposed(monkeypatch):
    client = DiscoveryGenomeClient(
        DiscoveryGenomeConfig(
            base_url="https://guided.example",
            service_token="t" * 48,
            username="derek",
        )
    )

    def rejected_urlopen(*_args, **_kwargs):
        raise HTTPError(
            "https://guided.example",
            409,
            "Conflict",
            {},
            BytesIO(b'{"error":"private database detail"}'),
        )

    monkeypatch.setattr(genome_client, "urlopen", rejected_urlopen)
    with pytest.raises(DiscoveryGenomeError, match="request failed with HTTP 409") as exc_info:
        client.status()
    assert "private database detail" not in str(exc_info.value)


def test_freeze_operator_is_bound_to_server_review_hash_and_phrase():
    review_hash = "a" * 64
    client = FakeGenomeClient(
        [
            {"operators": [{"id": "op-1", "review_hash": review_hash}]},
            {"operator": {"id": "op-1", "contract_hash": review_hash, "status": "frozen"}},
        ]
    )
    result = client.freeze_operator(
        "op-1",
        expected_review_hash=review_hash,
        confirmation=OPERATOR_FREEZE_PHRASE,
    )
    assert result["operator"]["status"] == "frozen"
    assert client.calls[-1][1] == "/operators/op-1/freeze"
    assert client.calls[-1][2] == {"expected_review_hash": review_hash}


def test_freeze_operator_rejects_stale_hash_before_mutation():
    client = FakeGenomeClient(
        [{"operators": [{"id": "op-1", "review_hash": "a" * 64}]}]
    )
    with pytest.raises(DiscoveryGenomeError, match="hash mismatch"):
        client.freeze_operator(
            "op-1",
            expected_review_hash="b" * 64,
            confirmation=OPERATOR_FREEZE_PHRASE,
        )
    assert len(client.calls) == 1


def test_freeze_tournament_is_bound_to_prospective_manifest_hash():
    review_hash = "c" * 64
    client = FakeGenomeClient(
        [
            {"tournament": {"id": "tour-1", "review_hash": review_hash}},
            {"tournament": {"id": "tour-1", "manifest_hash": review_hash, "status": "frozen"}},
        ]
    )
    result = client.freeze_tournament(
        "tour-1",
        expected_review_hash=review_hash,
        confirmation=TOURNAMENT_FREEZE_PHRASE,
    )
    assert result["tournament"]["status"] == "frozen"
    assert client.calls[-1][2] == {"expected_review_hash": review_hash}


def test_result_requires_exact_target_payload_hash_and_confirmation():
    result_payload = {"observed": "effect vanished", "n": 40}
    receipt = tournament_result_receipt("tour-1", "entry-1", "survived", result_payload)
    result_hash = hash_json(receipt)
    client = FakeGenomeClient(
        [{
            "entry": {
                "id": "entry-1",
                "tournament_id": "tour-1",
                "verdict": "survived",
                "result_json": result_payload,
                "result_hash": result_hash,
            }
        }]
    )
    response = client.record_tournament_result(
        "tour-1",
        "entry-1",
        verdict="survived",
        result=result_payload,
        expected_result_hash=result_hash,
        confirmation=RESULT_RECORD_PHRASE,
    )
    assert response["result_hash"] == result_hash
    assert client.calls[0][2]["verdict"] == "survived"
    assert client.calls[0][2]["result"] == result_payload
    assert client.calls[0][2]["expected_result_hash"] == result_hash


@pytest.mark.parametrize(
    "changed",
    [
        {"tournament_id": "tour-1", "entry_id": "entry-1", "verdict": "refuted"},
        {"tournament_id": "tour-1", "entry_id": "entry-2", "verdict": "survived"},
        {"tournament_id": "tour-2", "entry_id": "entry-1", "verdict": "survived"},
    ],
)
def test_result_hash_cannot_be_transplanted_or_change_verdict(changed):
    result_payload = {"observed": "effect vanished", "n": 40}
    reviewed_hash = hash_json(
        tournament_result_receipt("tour-1", "entry-1", "survived", result_payload)
    )
    client = FakeGenomeClient([])

    with pytest.raises(DiscoveryGenomeError, match="result hash mismatch"):
        client.record_tournament_result(
            changed["tournament_id"],
            changed["entry_id"],
            verdict=changed["verdict"],
            result=result_payload,
            expected_result_hash=reviewed_hash,
            confirmation=RESULT_RECORD_PHRASE,
        )

    assert client.calls == []


def test_result_rejects_a_mismatched_persisted_entry():
    result_payload = {"observed": "effect vanished", "n": 40}
    result_hash = hash_json(
        tournament_result_receipt("tour-1", "entry-1", "survived", result_payload)
    )
    client = FakeGenomeClient(
        [{
            "entry": {
                "id": "entry-2",
                "tournament_id": "tour-1",
                "verdict": "survived",
                "result_json": result_payload,
                "result_hash": result_hash,
            }
        }]
    )

    with pytest.raises(DiscoveryGenomeError, match="does not match the reviewed operation"):
        client.record_tournament_result(
            "tour-1",
            "entry-1",
            verdict="survived",
            result=result_payload,
            expected_result_hash=result_hash,
            confirmation=RESULT_RECORD_PHRASE,
        )


@pytest.mark.parametrize(
    "method,args",
    [
        (
            "freeze_operator",
            {
                "operator_id": "op-1",
                "expected_review_hash": "a" * 64,
                "confirmation": "yes",
            },
        ),
        (
            "freeze_tournament",
            {
                "tournament_id": "tour-1",
                "expected_review_hash": "a" * 64,
                "confirmation": "yes",
            },
        ),
    ],
)
def test_freeze_actions_reject_approximate_confirmation(method, args):
    client = FakeGenomeClient([])
    with pytest.raises(DiscoveryGenomeError, match="confirmation must exactly equal"):
        getattr(client, method)(**args)
    assert client.calls == []
