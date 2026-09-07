"""Deterministic dimension scorers — no model, no network, no database."""
from __future__ import annotations

from astor.eval import dimensions


# ---------------------------------------------------------------- D8 format #
def test_sentence_count_ignores_trailing_whitespace():
    assert dimensions.sentence_count("We stock it. Want the 500 mL? ") == 2


def test_sentence_count_treats_a_decimal_as_one_sentence():
    assert dimensions.sentence_count("It ships at 4.5 degrees.") == 1


def test_clean_reply_has_no_format_violations():
    reply = "Yes, we carry DMEM. Want the 500 mL high-glucose one?"
    assert dimensions.format_violations(reply, named=["DMEM"]) == []


def test_bold_markdown_is_a_violation():
    reply = "Yes, we carry **DMEM**. Want the 500 mL?"
    assert "markdown:bold" in dimensions.format_violations(reply, named=[])


def test_heading_and_backtick_and_link_are_violations():
    violations = dimensions.format_violations(
        "## Media\nUse `DMEM`. See [here](http://x.test). Want one?", named=[])
    assert "markdown:heading" in violations
    assert "markdown:code" in violations
    assert "markdown:link" in violations


def test_one_sentence_is_too_short():
    assert "sentences:1" in dimensions.format_violations("We carry DMEM.", named=[])


def test_six_sentences_is_too_long():
    reply = "One. Two. Three. Four. Five. Six."
    assert "sentences:6" in dimensions.format_violations(reply, named=[])


def test_naming_more_than_three_items_is_a_violation():
    reply = "We carry a few. Want one?"
    named = ["a 500 mL", "b 500 mL", "c 500 mL", "d 500 mL"]
    assert "named_items:4" in dimensions.format_violations(reply, named=named)


def test_naming_exactly_three_items_is_allowed():
    reply = "We carry a few. Want one?"
    named = ["a 500 mL", "b 500 mL", "c 500 mL"]
    assert dimensions.format_violations(reply, named=named) == []
