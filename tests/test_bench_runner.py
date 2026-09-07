"""Conversation mechanics for the benchmark runner. No network."""
from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone

import pytest

from astor.eval import probes as probes_mod
from astor.eval import report as report_mod
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

    result, slept = run_bench._post_with_retry(send, attempts=3, backoff=0)
    assert result == {"ok": True}
    assert slept == 0
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


# ------------------------------------------------------------------- judging #
def _corpus():
    return [probes_mod.Probe(
        id="P21", row="R7", turns=("high background?",), dimensions=("D5", "D8"),
        rubric=probes_mod.Rubric(must_convey=("names a cause",)))]


def _transcript(reply="Try more blocking. Want the buffer?"):
    return [{"probe": "P21", "row": "R7", "run": 1,
             "turns": [{"ask": "high background?", "reply": reply, "items": []}]}]


def test_judge_transcripts_produces_one_d5_cell_per_transcript():
    from astor.eval import judge as judge_mod

    calls = []

    def fake(question, reply, rubric, **kw):
        calls.append(question)
        return judge_mod.Verdict(passed=True, reason="ok")

    results = run_bench.judge_transcripts(_transcript(), _corpus(), judge_fn=fake)
    assert results == [("R7", "D5", True)]
    assert calls == ["high background?"]


def test_probes_without_d5_are_not_judged():
    corpus = [probes_mod.Probe(id="P21", row="R7", turns=("q",), dimensions=("D8",))]
    called = []
    run_bench.judge_transcripts(_transcript(), corpus,
                               judge_fn=lambda *a, **k: called.append(1))
    assert called == []


def test_judge_grades_the_final_turn():
    from astor.eval import judge as judge_mod

    seen = []
    transcripts = [{"probe": "P21", "row": "R7", "run": 1, "turns": [
        {"ask": "q1", "reply": "first", "items": []},
        {"ask": "q2", "reply": "second", "items": []}]}]

    def fake(question, reply, rubric, **kw):
        seen.append(reply)
        return judge_mod.Verdict(passed=True, reason="ok")

    run_bench.judge_transcripts(transcripts, _corpus(), judge_fn=fake)
    assert seen == ["second"]


# ------------------------------------------------------------------- backlog #
def test_backlog_counts_vendor_tokens_echoed_from_returned_names():
    transcripts = [{"probe": "P01", "row": "R1", "run": 1, "turns": [
        {"ask": "q", "reply": "We have DMEM/F12, HEPES (TBS8083). Want it?",
         "items": ["DMEM/F12, HEPES (TBS8083) - 500 ML"]}]}]
    assert run_bench.backlog(transcripts, ["GenDEPOT"]) == [("TBS8083", 1)]


def test_backlog_excludes_model_leaks():
    """A vendor the model produced from nothing is D4B, not a catalog defect."""
    transcripts = [{"probe": "P39", "row": "R14", "run": 1, "turns": [
        {"ask": "q", "reply": "GenDEPOT makes it.", "items": ["DMEM - 500ml"]}]}]
    assert run_bench.backlog(transcripts, ["GenDEPOT"]) == []


def test_backlog_is_ordered_by_frequency():
    turn = lambda name: {"ask": "q", "reply": f"We have {name}. Want it?", "items": [name]}
    transcripts = [
        {"probe": "P01", "row": "R1", "run": 1, "turns": [turn("A (TBS8083)")]},
        {"probe": "P01", "row": "R1", "run": 2, "turns": [turn("A (TBS8083)")]},
        {"probe": "P02", "row": "R1", "run": 1, "turns": [turn("B (TMP081)")]},
    ]
    assert run_bench.backlog(transcripts, []) == [("TBS8083", 2), ("TMP081", 1)]


# -------------------------------------------------------------------- label #
def test_label_worksheet_includes_the_probe_the_answer_and_the_rubric():
    sheet = run_bench.label_worksheet(_transcript(), _corpus())
    assert "P21" in sheet
    assert "Try more blocking" in sheet
    assert "names a cause" in sheet
    assert "VERDICT" in sheet


def test_label_worksheet_grades_the_final_turn_of_a_conversation():
    """The answer under label is the final reply. The earlier turn appears as
    context — the human must see exactly what the judge saw, or kappa compares
    two raters working from different information."""
    transcripts = [{"probe": "P21", "row": "R7", "run": 1, "turns": [
        {"ask": "q1", "reply": "first", "items": []},
        {"ask": "q2", "reply": "second", "items": []}]}]
    sheet = run_bench.label_worksheet(transcripts, _corpus())
    assert "ANSWER: second" in sheet
    assert "(assistant: first)" in sheet
    assert "ANSWER: first" not in sheet


def test_label_worksheet_skips_probes_the_judge_never_graded():
    """kappa compares two raters on the same items, so a transcript the judge
    never scored must not reach the human."""
    corpus = [probes_mod.Probe(id="P21", row="R7", turns=("q",), dimensions=("D8",))]
    assert "No judged transcripts" in run_bench.label_worksheet(_transcript(), corpus)


def test_label_worksheet_is_reproducible_for_a_seed():
    many = [{"probe": "P21", "row": "R7", "run": n,
             "turns": [{"ask": "q", "reply": f"answer {n}", "items": []}]}
            for n in range(40)]
    first = run_bench.label_worksheet(many, _corpus(), size=5, seed=3)
    assert first == run_bench.label_worksheet(many, _corpus(), size=5, seed=3)


# ------------------------------------------------- I4: products vs protocols #
def test_play_separates_products_from_protocols():
    """D1 asks whether a PRODUCT was surfaced. A protocol titled "Matrigel-based
    3D culture" is a legitimate hit that must not invert an absence probe."""
    probe = probes_mod.Probe(id="P34", row="R12", turns=("do you sell Matrigel?",),
                             dimensions=("D1",))

    def post(messages):
        return {"reply": "no", "items": [
            {"type": "protocol", "id": "x", "name": "Matrigel-based 3D culture"},
            {"type": "product", "id": "y", "name": "DMEM - 500ml"}]}

    turns = run_bench.play(probe, post)
    assert turns[0].item_names == ["Matrigel-based 3D culture", "DMEM - 500ml"]
    assert turns[0].product_names == ["DMEM - 500ml"]


# ---------------------------------------------------- D8: latency vs backoff #
def test_recorded_latency_excludes_retry_backoff():
    """5s + 10s of backoff folded into a turn time makes the reported p95 —
    a headline number — fifteen seconds wrong."""
    probe = probes_mod.Probe(id="P01", row="R1", turns=("q",), dimensions=("D8",))

    def post(messages):
        return {"reply": "ok", "items": []}

    post.last_seconds = 0.4
    assert run_bench.play(probe, post)[0].seconds == 0.4


def test_post_with_retry_reports_the_seconds_it_slept():
    calls = []

    def send():
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)
        return {"ok": True}

    slept_for = []
    original = run_bench.time.sleep
    run_bench.time.sleep = slept_for.append
    try:
        result, slept = run_bench._post_with_retry(send, attempts=3, backoff=5.0)
    finally:
        run_bench.time.sleep = original
    assert result == {"ok": True}
    assert slept_for == [5.0, 10.0]
    assert slept == 15.0


# ------------------------------------------ C3: identifying production rows #
_SINCE = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def _row(**kw):
    base = {"id": "r1", "shop": "astor-dev.myshopify.com",
            "created_at": "2026-09-07T12:05:00+00:00", "requested_item": "Matrigel"}
    return {**base, **kw}


def test_a_row_this_run_wrote_is_recognised_without_the_probe_tag():
    """The tag is in the turn text; the row is written from a string the MODEL
    composes. A model normalising "Matrigel (astor-bench-probe)" to "Matrigel"
    must not make its own production row invisible."""
    assert run_bench.row_is_from_this_run(
        _row(), since=_SINCE, shop="astor-dev.myshopify.com") is True


def test_a_row_written_before_the_run_is_not_ours():
    assert run_bench.row_is_from_this_run(
        _row(created_at="2026-09-07T11:59:59+00:00"), since=_SINCE,
        shop="astor-dev.myshopify.com") is False


def test_a_row_from_another_shop_is_not_ours():
    assert run_bench.row_is_from_this_run(
        _row(shop="someone-else.myshopify.com"), since=_SINCE,
        shop="astor-dev.myshopify.com") is False


def test_a_naive_timestamp_is_read_as_utc_rather_than_raising():
    """Comparing a naive and an aware datetime raises, and it would raise inside
    the consent baseline read — discarding the transcript mid-run."""
    assert run_bench.row_is_from_this_run(
        _row(created_at="2026-09-07T12:05:00"), since=_SINCE,
        shop="astor-dev.myshopify.com") is True


def test_the_cleanup_delete_is_scoped_by_id():
    sql = run_bench.cleanup_sql([_row(id="aaa"), _row(id="bbb")])
    assert "'aaa', 'bbb'" in sql
    assert "WHERE id IN" in sql
    assert "ILIKE" not in sql          # never scoped by a tag the model may drop


def test_the_cleanup_block_says_so_when_nothing_was_written():
    """M05's whole assertion is that nothing was written; an operator needs to
    see that stated, not inferred from silence."""
    assert "no sourcing_requests rows" in run_bench.cleanup_sql([])


def test_tagged_and_consent_probes_are_write_capable():
    tagged = probes_mod.Probe(id="M03", row="R12", dimensions=("D4",),
                              turns=("do you have Matrigel (astor-bench-probe)?",
                                     "yes please"))
    refusing = probes_mod.Probe(id="P34", row="R12", dimensions=("D4",),
                                turns=("do you sell Matrigel?",), must_not_flag=True)
    read_only = probes_mod.Probe(id="P01", row="R1", dimensions=("D1",),
                                 turns=("do you have DMEM?",))
    assert run_bench.writes_to_production([tagged]) is True
    assert run_bench.writes_to_production([refusing]) is True
    assert run_bench.writes_to_production([read_only]) is False


# ------------------------------------------- I9: the judge sees the context #
def test_the_judge_question_carries_the_earlier_turns():
    """M06's rubric demands the answer "carries the substitute from turn 1". A
    judge shown only "will that work for primary neurons?" cannot assess that."""
    turns = [{"ask": "what can I use instead of FBS?", "reply": "Try KSR."},
             {"ask": "will that work for primary neurons?", "reply": "Partly."}]
    assert run_bench.judge_question(turns) == (
        "what can I use instead of FBS?\n(assistant: Try KSR.)\n"
        "will that work for primary neurons?")


def test_the_judge_question_of_a_single_turn_probe_is_just_the_question():
    assert run_bench.judge_question([{"ask": "q", "reply": "a"}]) == "q"


def test_the_judge_still_sees_no_items_or_probe_id():
    """Blind by construction: the context is turns, never the item list."""
    turns = [{"ask": "q1", "reply": "r1", "items": ["DMEM - 500ml"]},
             {"ask": "q2", "reply": "r2", "items": ["DMEM - 500ml"]}]
    assert "DMEM" not in run_bench.judge_question(turns)


def test_judge_transcripts_grades_a_multi_turn_probe_with_its_context():
    from astor.eval import judge as judge_mod

    seen = []
    transcripts = [{"probe": "P21", "row": "R7", "run": 1, "turns": [
        {"ask": "q1", "reply": "first", "items": []},
        {"ask": "q2", "reply": "second", "items": []}]}]

    def fake(question, reply, rubric, **kw):
        seen.append(question)
        return judge_mod.Verdict(passed=True, reason="ok")

    run_bench.judge_transcripts(transcripts, _corpus(), judge_fn=fake)
    assert seen == ["q1\n(assistant: first)\nq2"]


# ---------------------------------------- I8: the verdict survives to a file #
def test_the_judge_verdict_is_stamped_onto_the_transcript():
    """Without this the sidecar carries no verdict, so a human's labels have
    nothing to be joined against and cohens_kappa keeps no caller."""
    from astor.eval import judge as judge_mod

    transcripts = _transcript()
    run_bench.judge_transcripts(
        transcripts, _corpus(),
        judge_fn=lambda *a, **k: judge_mod.Verdict(passed=False, reason="thin"))
    assert transcripts[0]["judge"] == {"passed": False, "reason": "thin"}
    assert json.loads(json.dumps(transcripts))[0]["judge"]["passed"] is False


def test_the_label_worksheet_prints_a_join_key_and_how_to_use_it():
    sheet = run_bench.label_worksheet(_transcript(), _corpus())
    assert "KEY: P21:1" in sheet
    assert '"P21:1": "pass"' in sheet
    assert "--kappa-from" in sheet


# ------------------------------------------------- I7/I6: the exit contract #
def test_render_sections_includes_a_per_probe_table_under_its_own_heading():
    """C2: the row matrix aggregates 7 probes x 5 runs into one cell, so a probe
    failing 3 of 5 renders green. The probe table is what makes it visible."""
    sections = run_bench.render_sections(
        report_mod.aggregate([("R1", "D1", True)] * 32 + [("R1", "D1", False)] * 3),
        report_mod.aggregate([("P01", "D1", True)] * 30
                             + [("P04", "D1", False)] * 3 + [("P04", "D1", True)] * 2),
        calibrated=True, backlog_rows=[], latencies=[1.0], failures=[])
    text = "\n\n".join(sections)
    assert "## Per-probe" in text
    assert "P04" in text
    assert text.count("GATE:") == 1        # the row matrix owns the verdict
    assert "GATE: PASS" in text            # ...and the row cell is green
    assert "P04/D1" in text                # ...while the probe is not


def test_the_gate_verdict_is_read_with_the_calibration_flag():
    """I7 at the exit: an uncalibrated D5 cell below its bar must not set the
    process's exit code any more than it sets GATE: FAIL."""
    cells = report_mod.aggregate([("R7", "D5", False)] * 5)
    assert report_mod.failing(cells, calibrated=False) == []
    assert report_mod.failing(cells, calibrated=True) != []


def test_an_empty_scorecard_has_no_failing_cells():
    """I6's premise: with every turn errored, `cells` is empty and nothing is
    below a bar — so `failing` alone cannot distinguish a clean run from a run
    that collected nothing. main() exits non-zero on `not cells` as well."""
    assert report_mod.aggregate([]) == []
    assert report_mod.failing(report_mod.aggregate([])) == []


def test_collect_isolates_a_failing_run_and_keeps_the_others():
    probe = probes_mod.Probe(id="P01", row="R1", turns=("q",), dimensions=("D8",))
    calls = []

    def post(messages):
        calls.append(1)
        if len(calls) == 2:
            raise urllib.error.URLError("blip")
        return {"reply": "We have it. Want one?", "items": []}

    collected = run_bench.collect([probe], post, denylist=[], sleep=0.0,
                                  runs_override=3)
    assert len(collected.transcripts) == 2
    assert len(collected.failures) == 1
    assert "P01 run 2" in collected.failures[0]
    assert [r[1] for r in collected.results] == ["D8", "D8"]
    assert [r[0] for r in collected.probe_results] == ["P01", "P01"]
