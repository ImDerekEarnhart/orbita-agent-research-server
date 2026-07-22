from __future__ import annotations

import pytest

from orbita_agent.genome_client import (
    OPERATOR_FREEZE_PHRASE,
    RESULT_RECORD_PHRASE,
    TOURNAMENT_FREEZE_PHRASE,
    DiscoveryGenomeClient,
    DiscoveryGenomeConfig,
    DiscoveryGenomeError,
    hash_json,
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


def test_result_requires_exact_payload_hash_and_confirmation():
    result_payload = {"observed": "effect vanished", "n": 40}
    result_hash = hash_json(result_payload)
    client = FakeGenomeClient(
        [{"entry": {"id": "entry-1", "verdict": "survived"}}]
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
    assert client.calls[0][2]["result"] == result_payload


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
