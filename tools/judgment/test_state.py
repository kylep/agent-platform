"""The staged state adapter keeps Judgment's guarded multi-record writes."""
import copy

import pytest

import state


REF = "relay:" + "c" * 32 + "/" + "a" * 32


def test_belief_create_retries_with_the_same_ids_and_one_transaction(monkeypatch):
    calls = []

    def fake(base, action, **body):
        calls.append((action, copy.deepcopy(body)))
        if action == "get":
            return {"id": body["id"], "version": 1, "values": {}}
        return {"results": []}

    monkeypatch.setattr(state, "_call", fake)
    args = {"claim": "Kyle likes a clear answer", "confidence": "medium",
            "provenance": "observed", "request_id": "belief-1"}
    first = state._belief("proxy/", args)
    second = state._belief("proxy/", args)
    assert first == second == {"ok": True, "id": first["id"], "version": 1}
    assert calls[0] == calls[1]
    ops = calls[0][1]["operations"]
    assert [op["collection"] for op in ops] == ["beliefs", "belief_versions"]
    assert ops[1]["values"]["belief"] == ops[0]["id"]
    with pytest.raises(ValueError, match="kyle_confirmed"):
        state._belief("proxy/", {**args, "provenance": "kyle_confirmed"})


def test_prediction_pins_the_observed_version_and_guards_it(monkeypatch):
    calls = []

    def fake(base, action, **body):
        calls.append((action, copy.deepcopy(body)))
        if action == "get":
            return {"id": body["id"], "version": 3,
                    "values": {"claim": "a belief", "number": 2}}
        return {"results": []}

    monkeypatch.setattr(state, "_call", fake)
    args = {"scenario": "choose a trail", "predicted_choice": "short route",
            "confidence": "medium", "outcome_known": False,
            "beliefs": ["b" * 32], "request_id": "prediction-1"}
    result = state._predict("proxy/", args)
    assert result["timing"] == "prospective"
    tx = calls[-1][1]
    assert tx["guards"] == [{"collection": "beliefs", "id": "b" * 32,
                             "version": 3}]
    assert tx["operations"][1]["values"]["belief_version"] == 2
    assert tx["operations"][1]["values"]["prediction"] == result["id"]


def test_feedback_never_claims_kyles_confirmation(monkeypatch):
    calls = []

    def fake(base, action, **body):
        calls.append((action, copy.deepcopy(body)))
        if action == "get":
            return {"id": body["id"], "version": 1, "values": {}}
        return {"results": []}

    monkeypatch.setattr(state, "_call", fake)
    state._feedback("proxy/", {"prediction_id": "a" * 32, "kyle_words": "No",
                                "source_ref": REF, "outcome": "contradicted",
                                "request_id": "feedback-1"})
    values = calls[-1][1]["operations"][0]["values"]
    assert "confirmed_at" not in values and values["source_ref"] == REF
