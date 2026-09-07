"""Judge prompt construction and verdict handling. No network."""
from __future__ import annotations

from types import SimpleNamespace

from astor.eval import judge, probes

RUBRIC = probes.Rubric(
    must_convey=("names at least two plausible causes", "ends with a next step"),
    disqualifiers=("blames a product it did not surface",))


class _FakeClient:
    """Mimics the SDK surface judge_science uses: client.messages.parse(...)."""

    def __init__(self, parsed):
        self.calls = []
        self.messages = SimpleNamespace(parse=self._parse)
        self._parsed = parsed

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self._parsed)


def test_prompt_contains_the_question_the_reply_and_both_rubric_halves():
    text = judge.prompt("why is my blot dirty?", "Try more blocking. Want the buffer?", RUBRIC)
    assert "why is my blot dirty?" in text
    assert "Try more blocking" in text
    assert "names at least two plausible causes" in text
    assert "blames a product it did not surface" in text


def test_prompt_omits_the_disqualifier_section_when_there_are_none():
    text = judge.prompt("q", "a", probes.Rubric(must_convey=("x",)))
    assert "x" in text
    assert "Disqualifiers" not in text


def test_verdict_is_returned_from_the_parsed_output():
    client = _FakeClient(judge.Verdict(passed=True, reason="covers both causes"))
    verdict = judge.judge_science("q", "a", RUBRIC, client=client)
    assert verdict.passed is True
    assert verdict.reason == "covers both causes"


def test_a_refusal_is_a_failed_verdict_not_an_exception():
    """A judge that raises would abort a 30-minute run over one transcript."""
    class _Refusing(_FakeClient):
        def _parse(self, **kwargs):
            return SimpleNamespace(stop_reason="refusal", parsed_output=None)

    verdict = judge.judge_science("q", "a", RUBRIC, client=_Refusing(None))
    assert verdict.passed is False
    assert "refus" in verdict.reason.lower()


def test_the_judge_is_blind_to_everything_but_question_rubric_and_reply():
    client = _FakeClient(judge.Verdict(passed=True, reason="ok"))
    judge.judge_science("q", "a", RUBRIC, client=client)
    sent = str(client.calls[0])
    assert "probe" not in sent.lower()
    assert "run" not in sent.lower().split("rubric")[0]
