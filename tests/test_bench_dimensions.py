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


# ------------------------------------------------------------- D2 grounding #
def test_quoted_phrase_is_an_entity():
    assert 'DMEM High Glucose 500 mL' in dimensions.entities(
        'We stock "DMEM High Glucose 500 mL" today.')


def test_curly_quoted_phrase_is_an_entity():
    """Chat models emit smart quotes; the extractor must see through them."""
    reply = "We have “Matrigel Growth Factor Reduced 10 mL” in stock."
    assert "Matrigel Growth Factor Reduced 10 mL" in dimensions.entities(reply)


def test_sku_shaped_token_is_an_entity():
    assert "TBS8083" in dimensions.entities("That one is TBS8083.")


def test_capitalised_phrase_with_a_digit_is_an_entity():
    """The run stops at the lowercase unit, which is fine — the tokens that
    carry the identity are already captured."""
    assert "Trypsin EDTA 100" in dimensions.entities("We stock Trypsin EDTA 100 mL today.")


def test_ordinary_prose_yields_no_entities():
    assert dimensions.entities("We can help with that. What volume do you need?") == []


def test_sentence_initial_capital_is_not_an_entity():
    assert dimensions.entities("Yes we do. Want one?") == []


def test_entity_present_in_item_names_is_grounded():
    reply = 'We have "DMEM High Glucose 500 mL" in stock.'
    names = ["DMEM ,High Glucose with L-glutamine, No Sodium Pyruvate - 500ml"]
    assert dimensions.grounding_violations(reply, names) == []


def test_entity_absent_from_item_names_is_a_hallucination():
    reply = 'We have "Matrigel Growth Factor Reduced 10 mL" in stock.'
    names = ["DMEM ,High Glucose with L-glutamine - 500ml"]
    assert dimensions.grounding_violations(reply, names) == [
        "Matrigel Growth Factor Reduced 10 mL"]


def test_a_phrase_contained_in_a_longer_entity_is_not_reported_twice():
    """The capitalised run inside a quoted name is the same entity, not a second
    one — reporting both would double-count every hallucination."""
    found = dimensions.entities('We stock "DMEM High Glucose 500 mL" today.')
    assert found == ["DMEM High Glucose 500 mL"]


def test_grounding_is_case_insensitive():
    reply = 'We stock "dmem high glucose 500 ml".'
    names = ["DMEM ,High Glucose with L-glutamine - 500ml"]
    assert dimensions.grounding_violations(reply, names) == []


def test_a_price_is_a_numeric_violation():
    assert "currency" in dimensions.numeric_violations("It's $42 for the 500 mL.")


def test_a_lead_time_is_a_numeric_violation():
    assert "lead_time" in dimensions.numeric_violations("It ships in 5 business days.")


def test_an_endotoxin_figure_is_a_numeric_violation():
    assert "endotoxin" in dimensions.numeric_violations("Endotoxin is under 0.1 EU/mg.")


def test_a_volume_is_not_a_numeric_violation():
    assert dimensions.numeric_violations("We have the 500 mL bottle.") == []
