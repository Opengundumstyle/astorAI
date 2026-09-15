"""The curation loader turns Mary's five CSVs into validated tables.

Every validation rule has a fixture that breaks it, so a malformed table fails
loudly at startup with the offending entry instead of reaching a shopper."""
from __future__ import annotations

from pathlib import Path

import pytest

from astor import curation
from astor.curation import loader

REAL = Path(__file__).resolve().parents[1] / "docs" / "curation"

CATEGORIES = "﻿category_id,category_name,why_this_one,has_run_it,notes\nwestern_blot,Western blot / 蛋白免疫印迹,x,yes,\nelisa,ELISA,x,yes,\n"
ROLES = "﻿role,plain_description,lab_usually_owns_it,always_buy_fresh,notes\nsecondary_antibody,二抗,no,no,\nwater,水,yes,no,\n"
CHECKLIST = ("﻿category_id,role,plain_name,required_or_optional,is_control,commonly_omitted,why_required,spec_constraint,depends_on\n"
             "western_blot,secondary_antibody,二抗,required,no,no,no signal if host mismatched,host differs,\n")
HEADER = "entry_id,category_id,symptom,likely_cause,check_or_fix,fix_role,buy_needed,checklist_role_ref,confidence,source,reviewed_by,reviewed_on,notes\n"
GOOD_ROW = "T0001,western_blot,no bands / 没有条带,secondary mismatch,check host,secondary_antibody,yes,western_blot:secondary_antibody,drafted,checklist,,,\n"


def _write(root: Path, troubleshooting_body: str, *, roles: str = ROLES) -> Path:
    (root / "categories.csv").write_text(CATEGORIES, encoding="utf-8")
    (root / "roles.csv").write_text(roles, encoding="utf-8")
    (root / "checklist.csv").write_text(CHECKLIST, encoding="utf-8")
    (root / "troubleshooting.csv").write_text("﻿" + HEADER + troubleshooting_body, encoding="utf-8")
    return root


def test_loads_a_valid_set(tmp_path):
    t = loader.load_all(_write(tmp_path, GOOD_ROW))
    assert set(t.categories) == {"western_blot", "elisa"}
    assert t.roles["water"].lab_usually_owns_it == "yes"
    assert t.checklist[0].role == "secondary_antibody"
    e = t.troubleshooting[0]
    assert e.entry_id == "T0001"
    assert e.fix_role == "secondary_antibody"
    assert e.checklist_role_ref == "western_blot:secondary_antibody"
    assert e.confidence == "drafted" and e.source == "checklist"


def test_empty_fix_role_and_ref_become_none(tmp_path):
    row = "T0002,western_blot,smiling bands,gel too hot,run colder,,no,,drafted,model,,,\n"
    t = loader.load_all(_write(tmp_path, row))
    assert t.troubleshooting[0].fix_role is None
    assert t.troubleshooting[0].checklist_role_ref is None


@pytest.mark.parametrize("bad, fragment", [
    (GOOD_ROW + GOOD_ROW, "T0001: duplicate entry_id"),
    ("T0001,rt_qpcr,s,c,f,secondary_antibody,yes,,drafted,model,,,\n", "T0001: unknown category_id 'rt_qpcr'"),
    ("T0001,western_blot,s,c,f,unicorn_role,yes,,drafted,model,,,\n", "T0001: unknown fix_role 'unicorn_role'"),
    ("T0001,western_blot,s,c,f,,yes,elisa:standard_curve,drafted,model,,,\n", "T0001: checklist_role_ref 'elisa:standard_curve' not in checklist"),
    ("T0001,western_blot,s,c,f,,yes,,maybe,model,,,\n", "T0001: confidence must be one of"),
    ("T0001,western_blot,s,c,f,,yes,,drafted,wikipedia,,,\n", "T0001: source must be one of"),
    ("T0001,western_blot,s,c,f,,yes,,reviewed,mary,,,\n", "T0001: reviewed rows need reviewed_by and reviewed_on"),
    ("T0001,western_blot,s,c,f,,yes,,drafted,model,Mary,2026-09-15,\n", "T0001: drafted rows must not carry reviewed_by/reviewed_on"),
    ("T0001,western_blot,s,c,f,,perhaps,,drafted,model,,,\n", "T0001: buy_needed must be one of"),
])
def test_each_validation_rule_names_the_entry(tmp_path, bad, fragment):
    with pytest.raises(loader.CurationError) as exc:
        loader.load_all(_write(tmp_path, bad))
    assert fragment in str(exc.value)


def test_real_tables_load():
    t = loader.load_all(REAL)
    assert {"western_blot", "rt_qpcr", "elisa", "cell_culture_transfection"} <= set(t.categories)
    # every role the checklist uses is defined, so troubleshooting refs can resolve
    missing = {r.role for r in t.checklist} - set(t.roles)
    assert missing == set()
    assert t.troubleshooting, "troubleshooting.csv must not be empty"


def test_tables_is_cached_and_resettable(monkeypatch, tmp_path):
    monkeypatch.setattr(curation, "CURATION_DIR", _write(tmp_path, GOOD_ROW))
    curation.reset()
    first = curation.tables()
    assert curation.tables() is first
    curation.reset()
    assert curation.tables() is not first
    curation.reset()
