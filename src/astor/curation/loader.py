"""Load the curation tables Mary maintains in docs/curation/*.csv.

Five small CSVs, one loader. The troubleshooting table is validated against the
other four at load time so a typo in a role name or category fails startup with
the entry id, instead of surfacing as a silent empty answer in the storefront.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

CONFIDENCE = ("drafted", "reviewed")
SOURCE = ("checklist", "mary", "model")
BUY_NEEDED = ("yes", "no", "sometimes")


class CurationError(ValueError):
    """A curation table is malformed. The message names the file and row."""


@dataclass(frozen=True)
class Category:
    category_id: str
    category_name: str


@dataclass(frozen=True)
class Role:
    role: str
    plain_description: str
    lab_usually_owns_it: str
    always_buy_fresh: str
    notes: str


@dataclass(frozen=True)
class ChecklistRow:
    category_id: str
    role: str
    plain_name: str
    required_or_optional: str
    is_control: str
    commonly_omitted: str
    why_required: str
    spec_constraint: str
    depends_on: str


@dataclass(frozen=True)
class TroubleshootingEntry:
    entry_id: str
    category_id: str
    symptom: str
    likely_cause: str
    check_or_fix: str
    fix_role: str | None
    buy_needed: str
    checklist_role_ref: str | None
    confidence: str
    source: str
    reviewed_by: str
    reviewed_on: str
    notes: str


@dataclass(frozen=True)
class CurationTables:
    categories: dict[str, Category]
    roles: dict[str, Role]
    checklist: list[ChecklistRow]
    troubleshooting: list[TroubleshootingEntry]


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items() if k is not None}
                for row in csv.DictReader(fh)]


def load_categories(path: Path) -> dict[str, Category]:
    out: dict[str, Category] = {}
    for r in _rows(path):
        if r.get("category_id"):
            out[r["category_id"]] = Category(r["category_id"], r.get("category_name", ""))
    return out


def load_roles(path: Path) -> dict[str, Role]:
    out: dict[str, Role] = {}
    for r in _rows(path):
        if r.get("role"):
            out[r["role"]] = Role(r["role"], r.get("plain_description", ""),
                                  r.get("lab_usually_owns_it", ""), r.get("always_buy_fresh", ""),
                                  r.get("notes", ""))
    return out


def load_checklist(path: Path) -> list[ChecklistRow]:
    return [
        ChecklistRow(r["category_id"], r["role"], r.get("plain_name", ""),
                     r.get("required_or_optional", ""), r.get("is_control", ""),
                     r.get("commonly_omitted", ""), r.get("why_required", ""),
                     r.get("spec_constraint", ""), r.get("depends_on", ""))
        for r in _rows(path) if r.get("category_id") and r.get("role")
    ]


def load_troubleshooting(path: Path, *, categories: dict[str, Category], roles: dict[str, Role],
                         checklist: list[ChecklistRow]) -> list[TroubleshootingEntry]:
    checklist_keys = {f"{c.category_id}:{c.role}" for c in checklist}
    seen: set[str] = set()
    out: list[TroubleshootingEntry] = []
    for r in _rows(path):
        eid = r.get("entry_id", "")
        if not eid:
            continue

        def bad(msg: str, _eid: str = eid) -> CurationError:
            return CurationError(f"{path.name} {_eid}: {msg}")

        if eid in seen:
            raise bad("duplicate entry_id")
        seen.add(eid)
        if r.get("category_id") not in categories:
            raise bad(f"unknown category_id {r.get('category_id')!r}")
        fix_role = r.get("fix_role") or None
        if fix_role and fix_role not in roles:
            raise bad(f"unknown fix_role {fix_role!r}")
        ref = r.get("checklist_role_ref") or None
        if ref and ref not in checklist_keys:
            raise bad(f"checklist_role_ref {ref!r} not in checklist")
        if r.get("confidence") not in CONFIDENCE:
            raise bad(f"confidence must be one of {CONFIDENCE}, got {r.get('confidence')!r}")
        if r.get("source") not in SOURCE:
            raise bad(f"source must be one of {SOURCE}, got {r.get('source')!r}")
        if r.get("buy_needed") not in BUY_NEEDED:
            raise bad(f"buy_needed must be one of {BUY_NEEDED}, got {r.get('buy_needed')!r}")
        by, on = r.get("reviewed_by", ""), r.get("reviewed_on", "")
        if r["confidence"] == "reviewed" and not (by and on):
            raise bad("reviewed rows need reviewed_by and reviewed_on")
        if r["confidence"] == "drafted" and (by or on):
            raise bad("drafted rows must not carry reviewed_by/reviewed_on")
        out.append(TroubleshootingEntry(
            eid, r["category_id"], r.get("symptom", ""), r.get("likely_cause", ""),
            r.get("check_or_fix", ""), fix_role, r["buy_needed"], ref, r["confidence"],
            r["source"], by, on, r.get("notes", "")))
    return out


def load_all(root: Path) -> CurationTables:
    root = Path(root)
    categories = load_categories(root / "categories.csv")
    roles = load_roles(root / "roles.csv")
    checklist = load_checklist(root / "checklist.csv")
    troubleshooting = load_troubleshooting(
        root / "troubleshooting.csv", categories=categories, roles=roles, checklist=checklist)
    return CurationTables(categories, roles, checklist, troubleshooting)
