"""The staged state adapter keeps Judgment's guarded multi-record writes."""
import copy
import json

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
            return {"id": body["id"],
                    "values": {"version": 3, "claim": "a belief", "number": 2}}
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
            return {"id": body["id"], "values": {"version": 1}}
        return {"results": []}

    monkeypatch.setattr(state, "_call", fake)
    state._feedback("proxy/", {"prediction_id": "a" * 32, "kyle_words": "No",
                                "source_ref": REF, "outcome": "contradicted",
                                "request_id": "feedback-1"})
    values = calls[-1][1]["operations"][0]["values"]
    assert "confirmed_at" not in values and values["source_ref"] == REF


def test_revision_writes_the_belief_and_its_version_together(monkeypatch):
    calls = []

    def fake(base, action, **body):
        calls.append((action, copy.deepcopy(body)))
        if action == "get":
            return {"id": body["id"], "values": {
                "version": 7, "number": 2, "claim": "old", "provenance": "observed",
                "confidence": "low", "status": "active"}}
        if action == "query":
            return {"rows": []}
        return {"results": []}

    monkeypatch.setattr(state, "_call", fake)
    state._belief("proxy/", {"id": "b" * 32, "expected_version": 2,
                             "claim": "narrower", "provenance": "inference",
                             "reason": "Kyle built it to learn", "request_id": "rev-1"})
    tx = calls[-1][1]
    assert [(op["op"], op["collection"]) for op in tx["operations"]] == [
        ("update", "beliefs"), ("create", "belief_versions")]
    assert tx["operations"][0]["expected_version"] == 7
    assert tx["operations"][1]["values"]["number"] == 3
    assert tx["guards"] == [{"collection": "beliefs", "id": "b" * 32, "version": 7}]


def test_feedback_names_its_belief_and_what_kyle_shared(monkeypatch):
    calls = []

    def fake(base, action, **body):
        calls.append((action, copy.deepcopy(body)))
        if action == "query":
            return {"rows": [{"id": "v" * 32, "values": {"version": 1}}]}
        return {"results": []}

    monkeypatch.setattr(state, "_call", fake)
    state._feedback("proxy/", {"belief_id": "b" * 32, "belief_version": 2,
                                "kyle_words": "I'd rather build it", "source_ref": REF,
                                "outcome": "contradicted", "shared_as": "preference"})
    values = calls[-1][1]["operations"][0]["values"]
    assert values["belief"] == "b" * 32 and values["belief_version"] == "v" * 32
    assert values["shared_as"] == "preference"
    with pytest.raises(ValueError, match="shared_as"):
        state._feedback("proxy/", {"belief_id": "b" * 32, "belief_version": 2,
                                    "kyle_words": "x", "source_ref": REF,
                                    "outcome": "mixed", "shared_as": "guess"})


T0, T1 = "2026-10-01T10:00:00+00:00", "2026-10-02T10:00:00+00:00"
P = {"id": "p" * 32, "created_at": T0, "scenario": "build or buy",
     "predicted_choice": "buy", "confidence": "medium", "timing": "prospective"}
RETRO = {**P, "id": "r" * 32, "timing": "retrospective"}
WAITING = {**P, "id": "w" * 32}
FB = {"id": "f" * 32, "created_at": T1, "prediction": "p" * 32, "belief": "b" * 32,
      "kyle_words": "build: learning is the point", "outcome": "contradicted",
      "shared_as": "choice", "confirmed_at": None}
V1 = {"id": "1" * 32, "belief": "b" * 32, "number": 1, "claim": "Kyle buys",
      "provenance": "kyle_confirmed"}
V2 = {"id": "2" * 32, "belief": "b" * 32, "number": 2, "claim": "Kyle buys chores",
      "provenance": "inference", "feedback": "f" * 32}
BELIEF = {"id": "b" * 32, "version": 3, "number": 2, "claim": "Kyle buys chores",
          "status": "active"}


def _store(monkeypatch, paged=False):
    """A fake App-data proxy over one belief, three predictions and one reply."""
    views = {
        "predictions_recent": [P, RETRO, WAITING], "feedback_recent": [FB],
        "versions_for_belief": [V1, V2], "feedback_for_belief": [FB],
        "links_for_belief": [{"id": "l" * 32, "prediction": "p" * 32}],
        "feedback_for_prediction": [FB], "belief_search": [BELIEF],
        "versions_for_feedback": [V2]}

    def fake(base, action, **body):
        assert action == "query"
        rows = [{"id": r["id"], "values": {"version": 1, **r}}
                for r in views[body["view"]]]
        if paged and body.get("cursor") is None and len(rows) > 1:
            return {"rows": rows[:1], "next_cursor": "next"}
        if paged and body.get("cursor"):
            return {"rows": rows[1:], "next_cursor": None}
        return {"rows": rows}

    monkeypatch.setattr(state, "_call", fake)


def test_recall_returns_the_claim_with_its_correction_and_counterevidence(monkeypatch):
    _store(monkeypatch)
    [belief] = state._read("proxy/", {"action": "recall", "query": "buys"})["beliefs"]
    assert belief["latest_kyle_confirmed"] == {"version": 1, "claim": "Kyle buys",
                                               "scope": None}
    assert [(f["id"], f["on"]) for f in belief["feedback"]] == [("f" * 32, "belief")]
    # Counterevidence reached only through a prediction that relied on the belief.
    FB2 = {**FB, "id": "g" * 32, "belief": None, "outcome": "mixed"}
    monkeypatch.setattr(state, "_query_all", lambda base, view, params=None: {
        "versions_for_belief": [], "feedback_for_belief": [],
        "links_for_belief": [{"prediction": "p" * 32}],
        "feedback_for_prediction": [FB2]}[view])
    [belief] = state._read("proxy/", {"action": "recall", "query": "buys"})["beliefs"]
    assert [(f["id"], f["on"]) for f in belief["feedback"]] == [("g" * 32, "prediction")]


def test_review_counts_samples_and_keeps_the_original_prediction(monkeypatch):
    _store(monkeypatch, paged=True)
    review = state._read("proxy/", {"action": "review"})
    counts = review["counts"]
    assert counts["prospective_resolved"]["contradicted"] == 1
    assert counts["prospective_pending"] == 1 and counts["retrospective"] == 1
    assert counts["prospective_resolved_relayed_only"] == 1
    assert "accuracy" not in json.dumps(review)
    [item] = review["resolved_prospective"]
    assert item["prediction"]["predicted_choice"] == "buy"
    assert item["feedback"][0]["revisions"] == [{
        "belief": "b" * 32, "version": 2, "claim": "Kyle buys chores",
        "provenance": "inference"}]


def test_pending_skips_feedback_a_version_already_answers(monkeypatch):
    _store(monkeypatch, paged=True)
    pending = state._read("proxy/", {"action": "pending"})
    assert [p["id"] for p in pending["pending"]] == ["w" * 32]
    assert pending["contradicting_feedback_no_version_cites"] == []
