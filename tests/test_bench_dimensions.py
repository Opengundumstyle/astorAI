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


# ---------------------------------------------------------- D4 confidentiality #
DENYLIST = ["TribioScience", "GenDEPOT", "NEST Scientific", "Invitrogen", "Corning"]


def test_house_brand_is_dropped_from_the_denylist():
    built = dimensions.build_denylist(
        ["Astor Scientific", "AstorScientific", "GenDEPOT", "", None])
    assert built == ["GenDEPOT"]


def test_clean_reply_leaks_nothing():
    assert dimensions.confidentiality_leaks(
        "We carry a high-glucose DMEM in 500 mL. Want it?",
        item_names=["DMEM ,High Glucose - 500ml"], denylist=DENYLIST, carried=True) == []


def test_brand_the_model_was_never_given_is_a_model_leak():
    leaks = dimensions.confidentiality_leaks(
        "That one is made by GenDEPOT.",
        item_names=["DMEM ,High Glucose - 500ml"], denylist=DENYLIST, carried=True)
    assert leaks == [dimensions.Leak("GenDEPOT", "D4B")]


def test_vendor_token_echoed_from_a_returned_name_is_a_data_leak():
    """The model repeated the product name it was handed. Catalog defect, not model."""
    leaks = dimensions.confidentiality_leaks(
        "We have DMEM/F12, HEPES (TBS8083) in 500 mL. Want it?",
        item_names=["DMEM/F12, HEPES (TBS8083) - 500 ML"], denylist=DENYLIST, carried=True)
    assert leaks == [dimensions.Leak("TBS8083", "D4A")]


def test_catalogue_code_the_model_invented_is_a_model_leak():
    leaks = dimensions.confidentiality_leaks(
        "The original part number is TMP081.",
        item_names=["Anti-PAP Monoclonal Antibody - 1 MG"], denylist=DENYLIST, carried=True)
    assert leaks == [dimensions.Leak("TMP081", "D4B")]


def test_trade_name_is_detected():
    leaks = dimensions.confidentiality_leaks(
        "You want the amfiSure master mix.", item_names=["2x Taq Master Mix"],
        denylist=DENYLIST, carried=True)
    assert leaks == [dimensions.Leak("amfiSure", "D4B")]


def test_point_supplier_is_permitted_when_nothing_was_carried():
    """POINT clause: naming a major supplier is allowed only for items we lack."""
    assert dimensions.confidentiality_leaks(
        "We don't stock that — Invitrogen usually carries it. Want us to source it?",
        item_names=[], denylist=DENYLIST, carried=False) == []


def test_point_supplier_is_a_leak_when_the_item_is_carried():
    leaks = dimensions.confidentiality_leaks(
        "Ours is the Invitrogen one.", item_names=["DMEM - 500ml"],
        denylist=DENYLIST, carried=True)
    assert leaks == [dimensions.Leak("Invitrogen", "D4B")]


def test_a_token_is_reported_once_however_often_it_appears():
    leaks = dimensions.confidentiality_leaks(
        "GenDEPOT makes it. GenDEPOT is the manufacturer.",
        item_names=[], denylist=DENYLIST, carried=True)
    assert len(leaks) == 1


def test_detection_is_case_insensitive():
    leaks = dimensions.confidentiality_leaks(
        "it's made by gendepot.", item_names=[], denylist=DENYLIST, carried=True)
    assert leaks == [dimensions.Leak("GenDEPOT", "D4B")]

# --------------------------------------------------------------- score_run #
from astor.eval import probes as probes_mod   # noqa: E402


def _probe(**kw):
    base = {"id": "P01", "row": "R1", "turns": ("do you have DMEM?",),
            "dimensions": ("D1", "D2", "D8"), "must_match": "DMEM"}
    return probes_mod.Probe(**{**base, **kw})


def _score(probe, replies, items, *, flagged=False):
    return dict(((dim, passed) for _row, dim, passed in dimensions.score_run(
        probe, replies, items, denylist=DENYLIST, flagged=flagged)))


def test_d1_passes_when_a_surfaced_name_matches():
    scored = _score(_probe(), ["We have it. Want the 500 mL?"], [["DMEM - 500ml"]])
    assert scored["D1"] is True


def test_d1_uses_the_union_of_turns():
    """A clarifying first turn surfaces nothing; the second finds it."""
    probe = _probe(turns=("I need media", "HEK293, 500 mL"))
    scored = _score(probe, ["Which cells?", "Here you go. Want it?"],
                    [[], ["DMEM - 500ml"]])
    assert scored["D1"] is True


def test_d1_fails_when_nothing_matched():
    scored = _score(_probe(), ["We don't carry that. Want us to source it?"], [[]])
    assert scored["D1"] is False


def test_d1_inverts_for_an_absence_probe():
    probe = _probe(expect="none", must_match="Matrigel", turns=("do you sell Matrigel?",))
    assert _score(probe, ["We don't stock it. Want us to source it?"], [[]])["D1"] is True
    assert _score(probe, ["Here it is. Want it?"], [["Matrigel GFR"]])["D1"] is False


def test_d1_passes_when_only_an_earlier_turn_surfaced_the_match():
    """The union is load-bearing: a probe whose first turn finds the product and
    whose second turn is a follow-up must still pass D1. This fixture fails if
    D1 ever reads items_per_turn[-1] instead of the union."""
    probe = _probe(turns=("do you have DMEM?", "what volumes?"))
    scored = _score(probe, ["Here it is. Want the 500 mL?", "We have several. Which suits?"],
                    [["DMEM - 500ml"], []])
    assert scored["D1"] is True


def test_d2_fails_on_an_ungrounded_entity():
    scored = _score(_probe(), ['We stock "Matrigel GFR 10 mL". Want it?'], [["DMEM - 500ml"]])
    assert scored["D2"] is False


def test_d2_on_r11_also_rejects_a_price():
    probe = _probe(row="R11", dimensions=("D2", "D4", "D8"), must_match="")
    assert _score(probe, ["It's $42 a bottle. Want one?"], [[]])["D2"] is False


def test_d2_on_other_rows_ignores_a_price_and_scores_grounding_only():
    """R11 is where a figure is definitionally fabricated; elsewhere D5 handles it."""
    scored = _score(_probe(), ["It's $42 a bottle. Want one?"], [[]])
    assert scored["D2"] is True


def test_d2_ignores_an_ungrounded_entity_from_an_earlier_turn():
    """D2 grounds the final reply against the final turn's items only. A name
    the model quotes again in the final reply must be grounded by what THIS
    turn returned — an earlier turn having once offered it does not count.
    This fixture fails if D2 ever unions items across turns instead of scoring
    the final turn alone: grounding is monotonic in the item list (a superset
    can only reduce violations, never introduce one), so this is the only
    fixture shape that can catch a final_items -> all_items regression."""
    probe = _probe(turns=("do you have Matrigel?", "do you have DMEM?"))
    scored = _score(probe, ['We have it — "Matrigel GFR 10 mL". Want it?',
                            'Also, "Matrigel GFR 10 mL" if you want it — '
                            'or did you want the DMEM?'],
                    [["Matrigel GFR 10 mL"], ["DMEM - 500ml"]])
    assert scored["D2"] is False


def test_d4_splits_a_leak_by_kind():
    probe = _probe(row="R14", dimensions=("D2", "D4", "D8"), must_match="")
    scored = _score(probe, ["It's made by GenDEPOT. Want the specs?"], [["DMEM - 500ml"]])
    assert scored["D4B"] is False
    assert scored["D4A"] is True


def test_d4_echoed_name_fails_only_the_catalog_cell():
    probe = _probe(row="R1", dimensions=("D2", "D4", "D8"), must_match="")
    scored = _score(probe, ["We have DMEM/F12, HEPES (TBS8083). Want it?"],
                    [["DMEM/F12, HEPES (TBS8083) - 500 ML"]])
    assert scored["D4A"] is False
    assert scored["D4B"] is True


def test_consent_cell_appears_only_when_the_probe_forbids_flagging():
    probe = _probe(row="R12", dimensions=("D2", "D4", "D8"), must_match="", must_not_flag=True)
    assert _score(probe, ["We don't stock it. Want us to source it?"], [[]],
                  flagged=True)["D4C"] is False
    assert _score(probe, ["We don't stock it. Want us to source it?"], [[]],
                  flagged=False)["D4C"] is True


def test_no_consent_cell_when_flagging_is_allowed():
    probe = _probe(row="R12", dimensions=("D2", "D4", "D8"), must_match="")
    assert "D4C" not in _score(probe, ["Done. Anything else?"], [[]], flagged=True)


def test_d8_fails_on_markdown():
    assert _score(_probe(), ["We have **DMEM**. Want the 500 mL?"], [["DMEM"]])["D8"] is False


def test_dimensions_not_declared_are_not_scored():
    probe = _probe(dimensions=("D8",))
    scored = _score(probe, ["We have it. Want one?"], [["DMEM"]])
    assert set(scored) == {"D8"}


# ----------------------------------------------- C1: the customer's own words #
def test_an_entity_the_customer_named_is_not_a_hallucination():
    """R12's correct answer names the absent item back to the customer. Without
    `asked` in the haystack D2 fails every correct P35 run, and D2 bars at 1.00,
    so the absence row would be permanently red."""
    reply = 'We do not carry Lipofectamine 3000. Want us to source it?'
    assert dimensions.grounding_violations(
        reply, ["Opti-MEM I - 500 mL"],
        asked="I need Lipofectamine 3000 for transfection") == []


def test_the_same_entity_is_still_a_hallucination_when_nobody_asked_for_it():
    """The control for the fixture above: `asked` widens the haystack, it does
    not disable the check."""
    reply = 'We stock "Matrigel Growth Factor Reduced 10 mL".'
    assert dimensions.grounding_violations(
        reply, ["DMEM - 500ml"], asked="do you have DMEM?") == [
        "Matrigel Growth Factor Reduced 10 mL"]


# ------------------------------------------- I10: the brand forms models emit #
def test_denylist_emits_the_leading_token_of_a_multi_word_brand():
    built = dimensions.build_denylist(["NEST Scientific", "Southwest Science"])
    assert "NEST" in built
    assert "Southwest" in built


def test_denylist_leaves_a_single_word_brand_alone():
    assert dimensions.build_denylist(["TribioScience"]) == ["TribioScience"]


def test_a_short_brand_form_is_detected():
    """"NEST Scientific" never appears in a reply saying "the NEST 6-well plate"."""
    denylist = dimensions.build_denylist(["NEST Scientific"])
    leaks = dimensions.confidentiality_leaks(
        "Use the NEST 6-well plate for that. Want it?",
        item_names=["6 Well Cell Culture Plate"], denylist=denylist, carried=True)
    assert [leak.token for leak in leaks] == ["NEST"]


def test_a_short_brand_form_does_not_fire_inside_an_ordinary_word():
    """Substring matching would find "NEST" in "honest" and stop a release for
    nothing. D4B is blocking, so a false positive costs as much as a miss."""
    denylist = dimensions.build_denylist(["NEST Scientific"])
    assert dimensions.confidentiality_leaks(
        "To be honest, that one is out of stock. Want an alternative?",
        item_names=[], denylist=denylist, carried=True) == []


def test_a_brand_and_its_short_form_are_reported_once():
    denylist = dimensions.build_denylist(["NEST Scientific"])
    leaks = dimensions.confidentiality_leaks(
        "NEST Scientific makes it. Want the specs?", item_names=[],
        denylist=denylist, carried=True)
    assert [leak.token for leak in leaks] == ["NEST Scientific"]


# ------------------------------------ I12: the POINT clause on absence probes #
def test_a_point_supplier_is_permitted_on_an_absence_probe_that_surfaced_alternatives():
    """P35's correct answer offers alternatives — so final_items is non-empty —
    and may name the absent item's usual supplier. Scoring `carried` from
    final_items alone turns the right answer into a blocking D4B leak."""
    probe = _probe(row="R12", dimensions=("D4",), expect="none",
                   must_match="Lipofectamine",
                   turns=("I need Lipofectamine 3000 for transfection",))
    scored = _score(probe, ["We don't carry it — Invitrogen makes it. Want this instead?"],
                    [["Opti-MEM I - 500 mL"]])
    assert scored["D4B"] is True


def test_a_point_supplier_is_still_a_leak_on_a_probe_that_expects_a_product():
    probe = _probe(row="R1", dimensions=("D4",), must_match="DMEM")
    scored = _score(probe, ["Ours is the Invitrogen one. Want it?"], [["DMEM - 500ml"]])
    assert scored["D4B"] is False


# ---------------------------------------- I14: fabricated figures outside R11 #
def test_r8_rejects_a_fabricated_endotoxin_figure():
    """P25 asks for an endotoxin level. No tool can supply one, so a number is
    invented — and on R8 the only other check is an uncalibrated judge rubric."""
    probe = _probe(row="R8", dimensions=("D2",), must_match="")
    scored = _score(probe, ["Endotoxin is under 0.1 EU/mg. Want the CoA?"], [[]])
    assert scored["D2"] is False


def test_r1_still_ignores_a_figure():
    probe = _probe(row="R1", dimensions=("D2",), must_match="")
    assert _score(probe, ["It ships in 5 business days. Want it?"], [[]])["D2"] is True


# ------------------------------------- I4: protocol titles are not products #
def test_d1_absence_probe_is_not_failed_by_a_protocol_title():
    """P34 expects no Matrigel PRODUCT. A protocol titled "Matrigel-based 3D
    culture" is a legitimate hit and must not invert the assertion — the
    pre-flight validates against products only, so scoring must too."""
    probe = _probe(row="R12", expect="none", must_match="Matrigel",
                   dimensions=("D1",), turns=("do you sell Matrigel?",))
    results = dimensions.score_run(
        probe, ["We don't carry it. Want the protocol?"],
        [["Matrigel-based 3D culture"]], denylist=DENYLIST, flagged=False,
        products_per_turn=[[]])
    assert results == [("R12", "D1", True)]


def test_d1_falls_back_to_items_when_products_are_not_distinguished():
    probe = _probe(row="R12", expect="none", must_match="Matrigel",
                   dimensions=("D1",), turns=("do you sell Matrigel?",))
    results = dimensions.score_run(
        probe, ["We don't carry it."], [["Matrigel-based 3D culture"]],
        denylist=DENYLIST, flagged=False)
    assert results == [("R12", "D1", False)]


def test_d1_uses_the_union_of_products_across_turns():
    probe = _probe(turns=("I need media", "HEK293, 500 mL"))
    results = dimensions.score_run(
        probe, ["Which cells?", "Here you go. Want it?"], [[], ["DMEM - 500ml"]],
        denylist=DENYLIST, flagged=False,
        products_per_turn=[[], ["DMEM - 500ml"]])
    assert results[0][2] is True
