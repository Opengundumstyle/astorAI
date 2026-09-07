"""Conversation mechanics for the benchmark runner. No network."""
from __future__ import annotations

import urllib.error

import pytest

from astor.eval import probes as probes_mod
from scripts import run_bench


def test_resolve_substitutes_an_item_name_and_id():
    template = 'Tell me more about "{{items[0].name}}" (protocol id: {{items[0].id}})'
    resolved = run_bench.resolve(template, [{"id": "abc-123", "name": "Western Blot"}])
    assert resolved == 'Tell me more about "Western Blot" (protocol id: abc-123)'


def test_resolve_leaves_a_template_without_placeholders_alone():
    assert run_bench.resolve("plain question", [{"id": "x", "name": "y"}]) == "plain question"


def test_resolve_raises_when_the_index_is_missing():
    """Better a loud failure than a probe that silently asks about nothing."""
    with pytest.raises(IndexError):
        run_bench.resolve("{{items[0].name}}", [])


def test_play_sends_each_turn_and_collects_replies():
    probe = probes_mod.Probe(id="M01", row="R13", turns=("I need media", "HEK293, 500 mL"),
                             dimensions=("D1",), must_match="DMEM")
    sent = []

    def post(messages):
        sent.append(list(messages))
        n = len(sent)
        return {"reply": f"reply {n}", "items": [{"id": f"i{n}", "name": f"item {n}"}]}

    turns = run_bench.play(probe, post)
    assert [t.reply for t in turns] == ["reply 1", "reply 2"]
    assert [t.item_names for t in turns] == [["item 1"], ["item 2"]]


def test_play_carries_conversation_history_forward():
    probe = probes_mod.Probe(id="M01", row="R13", turns=("first", "second"),
                             dimensions=("D8",))
    sent = []

    def post(messages):
        sent.append(list(messages))
        return {"reply": "ok", "items": []}

    run_bench.play(probe, post)
    assert [m["content"] for m in sent[0]] == ["first"]
    assert [m["role"] for m in sent[1]] == ["user", "assistant", "user"]
    assert sent[1][-1]["content"] == "second"


def test_play_resolves_a_placeholder_from_the_previous_turn():
    probe = probes_mod.Probe(
        id="M02", row="R4",
        turns=("protocols?", 'Tell me about "{{items[0].name}}" (protocol id: {{items[0].id}})'),
        dimensions=("D8",))
    sent = []

    def post(messages):
        sent.append(list(messages))
        return {"reply": "ok", "items": [{"id": "p-9", "name": "Western Blot"}]}

    run_bench.play(probe, post)
    assert sent[1][-1]["content"] == 'Tell me about "Western Blot" (protocol id: p-9)'


def test_consent_rows_are_dropped_when_the_admin_token_is_missing():
    """A D4C cell that reads 'ok' because nothing was checked is a false pass."""
    results = [("R12", "D1", True), ("R12", "D4C", True)]
    assert run_bench.drop_unverifiable_consent(results, admin_token=None) == [
        ("R12", "D1", True)]


def test_consent_rows_are_kept_when_the_admin_token_is_present():
    results = [("R12", "D4C", True)]
    assert run_bench.drop_unverifiable_consent(results, admin_token="t") == results


def test_post_with_retry_retries_a_rate_limit_then_succeeds():
    calls = []

    def send():
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)
        return {"ok": True}

    assert run_bench._post_with_retry(send, attempts=3, backoff=0) == {"ok": True}
    assert len(calls) == 2


def test_post_with_retry_does_not_retry_an_auth_error():
    """401 is a real error — retrying it just wastes three requests."""
    def send():
        raise urllib.error.HTTPError("u", 401, "Unauthorized", None, None)

    with pytest.raises(urllib.error.HTTPError):
        run_bench._post_with_retry(send, attempts=3, backoff=0)


def test_post_with_retry_gives_up_after_the_attempt_budget():
    calls = []

    def send():
        calls.append(1)
        raise urllib.error.URLError("boom")

    with pytest.raises(urllib.error.URLError):
        run_bench._post_with_retry(send, attempts=3, backoff=0)
    assert len(calls) == 3
