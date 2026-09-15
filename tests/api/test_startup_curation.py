"""A malformed curation table must fail boot, not reach a shopper as an empty answer."""
from __future__ import annotations

import pytest

from astor import curation
from astor.api.main import create_app
from astor.curation import loader

HEADER = "entry_id,category_id,symptom,likely_cause,check_or_fix,fix_role,buy_needed,checklist_role_ref,confidence,source,reviewed_by,reviewed_on,notes\n"


def test_app_boots_with_real_tables():
    curation.reset()
    create_app()
    assert curation.tables().troubleshooting


def test_app_refuses_to_boot_on_bad_table(tmp_path, monkeypatch):
    (tmp_path / "categories.csv").write_text("category_id,category_name\nwestern_blot,WB\n", encoding="utf-8")
    (tmp_path / "roles.csv").write_text("role,plain_description,lab_usually_owns_it,always_buy_fresh,notes\n", encoding="utf-8")
    (tmp_path / "checklist.csv").write_text("category_id,role,plain_name,required_or_optional,is_control,commonly_omitted,why_required,spec_constraint,depends_on\n", encoding="utf-8")
    (tmp_path / "troubleshooting.csv").write_text(
        HEADER + "T0001,western_blot,s,c,f,ghost_role,yes,,drafted,model,,,\n", encoding="utf-8")
    monkeypatch.setattr(curation, "CURATION_DIR", tmp_path)
    curation.reset()
    try:
        with pytest.raises(loader.CurationError) as exc:
            create_app()
        assert "T0001: unknown fix_role 'ghost_role'" in str(exc.value)
    finally:
        curation.reset()
