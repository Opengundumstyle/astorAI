"""Ingestion-quality checks.

Extraction is transcription, not judgement: the truth is in the source record, so
these are verifiable without a domain expert. Every check here corresponds to a
defect found in the live catalog on 2026-09-07 — see `scripts/audit_ingestion.py`.
"""
from astor.catalog import audit


# --------------------------------------------------------------- placeholders #
def test_recognises_the_placeholders_shopify_writes():
    assert audit.is_placeholder("Default Title")
    assert audit.is_placeholder("default title")
    assert audit.is_placeholder("N/A")
    assert audit.is_placeholder("  ")
    assert audit.is_placeholder(None)


def test_a_real_value_is_not_a_placeholder():
    assert not audit.is_placeholder("500 ML")
    assert not audit.is_placeholder("0")          # a real quantity, not absence


# ------------------------------------------------------- recoverable catalog #
def test_finds_a_catalog_code_the_name_carries():
    """3,247 names hold a code; `mpn` is empty on 16,016/16,019 because Shopify's
    metafield and barcode are both blank upstream. The data was there all along."""
    assert audit.code_in_name("SB-525334, ALK5 inhibitor (TBI2983) - 25 MG") == "TBI2983"
    assert audit.code_in_name("2 x Phanta Flash Master Mix (Dye Plus) P520 - P520-01") == "P520"


def test_prefers_a_parenthesised_code_over_a_bare_compound_name():
    """`AZD8055` is the compound; `TBI4816` is the catalogue number. Only the
    parenthesised form is reliably a SKU."""
    assert audit.code_in_name("AZD8055, mTOR kinase inhibitor (TBI4816) - 10 MG") == "TBI4816"


def test_no_code_when_the_name_has_none():
    assert audit.code_in_name("DMEM, High Glucose with L-Glutamine - 500ml") is None


def test_recoverable_mpn_only_reports_when_the_column_is_empty():
    name = "Biotinyl Tyramide (TBI3446) - 20 MG"
    assert audit.recoverable_mpn(name, None) == "TBI3446"
    assert audit.recoverable_mpn(name, "") == "TBI3446"
    assert audit.recoverable_mpn(name, "TBI3446") is None   # already extracted
    assert audit.recoverable_mpn("DMEM - 500ml", None) is None


# ------------------------------------------------------------ brand collisions #
def test_detects_brands_that_differ_only_by_punctuation_or_case():
    """`Astor Scientific` (9,213) and `AstorScientific` (122) are one supplier
    split in two, which suppresses genuine same-brand matches."""
    groups = audit.brand_collisions(["Astor Scientific", "AstorScientific", "Biologix"])
    assert groups == {"astorscientific": ["Astor Scientific", "AstorScientific"]}


def test_distinct_brands_do_not_collide():
    assert audit.brand_collisions(["Biologix", "GenDEPOT", "Vazyme"]) == {}


# ------------------------------------------------------------------- encoding #
def test_flags_a_mangled_apostrophe():
    """`Astor Eagleˇs Minimum Essential Medium` reaches a customer-facing card."""
    assert "mangled_apostrophe" in audit.encoding_defects("Astor Eagleˇs Minimum Essential")


def test_flags_doubled_spaces_and_replacement_chars():
    assert "double_space" in audit.encoding_defects("Astor Eagle  Medium")
    assert "replacement_char" in audit.encoding_defects("Astor � Medium")


def test_a_clean_name_has_no_defects():
    assert audit.encoding_defects("DMEM, High Glucose with L-Glutamine - 500ml") == []
    assert audit.encoding_defects("Eagle's Minimum Essential Medium") == []


# --------------------------------------------------------------- internal keys #
def test_internal_spec_keys_are_reported():
    """`_cost_basis` rides on 16,016 products into both the embedded string and
    the attribute bonus, while `_public_specs` strips it before the model sees it."""
    assert audit.internal_spec_keys({"Size": "500 ML", "_cost_basis": "x"}) == ["_cost_basis"]
    assert audit.internal_spec_keys({"Size": "500 ML"}) == []
    assert audit.internal_spec_keys(None) == []


# ------------------------------------------- auto-fill safety (found by the audit) #
def test_a_bare_token_is_not_safe_to_autofill_as_mpn():
    """The audit's own first run surfaced `Tribo™ Human CA125 ELISA Kit` -> "CA125".
    CA125 is a biomarker, not a catalogue number. Writing it to `mpn` would make
    `scoring`'s +0.50 brand+MPN rule fire on an analyte name — a false identity
    claim, which is worse than the empty column it replaces."""
    name = "Tribo™ Human CA125 ELISA Kit - 1 x 96-well plate"
    assert audit.recoverable_mpn(name, None) is None
    assert audit.possible_mpn(name, None) == "CA125"


def test_a_parenthesised_code_stays_safe_to_autofill():
    name = "Tissue DNA Extraction Kit (TBS6006) - 1000 Sample"
    assert audit.recoverable_mpn(name, None) == "TBS6006"
    assert audit.possible_mpn(name, None) is None   # already certain; not a maybe


def test_neither_tier_fires_when_mpn_is_already_set():
    name = "Tissue DNA Extraction Kit (TBS6006) - 1000 Sample"
    assert audit.recoverable_mpn(name, "TBS6006") is None
    assert audit.possible_mpn("Tribo™ Human CA125 ELISA Kit", "X1") is None


# ------------------------------------------------------------- backfill planning #
def test_stem_ignores_pack_size_and_the_code_itself():
    a = audit.product_stem("Acipimox, GPR109A agonist (TBI2526) - 100 MG")
    b = audit.product_stem("Acipimox, GPR109A agonist (TBI2526) - 500 MG")
    assert a == b, "same product, different pack size -> one identity"


def test_stem_separates_genuinely_different_compounds():
    a = audit.product_stem("Acipimox, GPR109A agonist (TBI2526) - 100 MG")
    b = audit.product_stem("Dehydrozingerone, Glutathione sponge (TBI2526) - 100 MG")
    assert a != b, "different compounds must not collapse to one identity"


def test_a_code_on_two_rows_is_blocked_by_the_unique_constraint():
    """`UniqueConstraint("brand", "mpn")` -- "a (brand, mpn) pair identifies one
    canonical product". Pack-size variants are separate Product ROWS here, so
    writing one code to both violates the schema. Skip, and say why."""
    plan, skipped = audit.mpn_backfill_plan([
        ("1", "5-Fluorouracil, TS inhibitor (TBI1276) - 250 MG", "TribioScience", None),
        ("2", "5-Fluorouracil, TS inhibitor (TBI1276) - 1 G", "TribioScience", None),
    ])
    assert plan == {}
    assert skipped[("TribioScience", "TBI1276")] == "unique_constraint"


def test_a_code_on_exactly_one_row_is_assignable():
    plan, skipped = audit.mpn_backfill_plan([
        ("1", "Protein Assay Kit (TBS2005) - 1000 Tests", "TribioScience", None),
    ])
    assert plan == {"1": "TBS2005"}
    assert skipped == {}


def test_two_different_products_sharing_a_code_are_flagged_distinctly():
    """TBI2526 sits on both Acipimox and Dehydrozingerone in the live catalog.
    That is a vendor/transcription error, not merely a schema collision, so it is
    reported under its own reason -- it needs fixing, not just skipping."""
    plan, skipped = audit.mpn_backfill_plan([
        ("1", "Acipimox, GPR109A agonist (TBI2526) - 100 MG", "TribioScience", None),
        ("2", "Dehydrozingerone, Glutathione sponge (TBI2526) - 100 MG", "TribioScience", None),
    ])
    assert plan == {}
    assert skipped[("TribioScience", "TBI2526")] == "two_identities"


def test_plan_never_overwrites_an_existing_mpn():
    plan, _ = audit.mpn_backfill_plan([
        ("1", "Protein Assay Kit (TBS2005) - 1000 Tests", "TribioScience", "ALREADY-SET"),
    ])
    assert plan == {}


def test_plan_ignores_bare_tokens():
    """CA125 is an analyte, not a SKU — it must not reach `mpn` via the plan either."""
    plan, _ = audit.mpn_backfill_plan([
        ("1", "Tribo Human CA125 ELISA Kit - 1 x 96-well plate", "TribioScience", None),
    ])
    assert plan == {}


def test_the_same_code_under_two_brands_stays_separate():
    """MPN is only unique WITHIN a manufacturer, so the key is (brand, code)."""
    plan, skipped = audit.mpn_backfill_plan([
        ("1", "Widget (AB123) - 1 G", "BrandOne", None),
        ("2", "Sprocket (AB123) - 1 G", "BrandTwo", None),
    ])
    assert plan == {"1": "AB123", "2": "AB123"}
    assert skipped == {}
