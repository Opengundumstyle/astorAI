# Troubleshooting Know-How Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the storefront assistant a curated troubleshooting table, a deterministic `troubleshoot` tool that resolves each fix to catalog products, and a gold set that measures it.

**Architecture:** A new `astor.curation` package loads the five curation CSVs into frozen dataclasses with cross-table validation and exposes a cached singleton. `astor.curation.troubleshoot` matches a customer symptom to rows by CJK-aware token overlap with an embedding fallback, and resolves each row's `fix_role` to products through the existing lexical product search. The chat layer gains one tool and one prompt paragraph; the eval layer gains a retrieval-hit-rate runner that reuses the assistant scenario gate.

**Tech Stack:** Python 3.12, stdlib `csv`/`dataclasses`, existing `astor.catalog.embeddings.Embedder` protocol, FastAPI app factory, pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-troubleshooting-knowhow-design.md`

## Global Constraints

- **Do not commit.** The user has asked that nothing be committed in this session. Every task ends by running the test suite, not `git commit`.
- **No new database tables or migrations.** Tables load from `docs/curation/*.csv` at startup.
- **No LLM call inside the tool.** Matching is deterministic given tables, embedder, and catalog.
- **Buyer gate on every product.** Products returned to the model pass through `roles.gate_product(r, roles.BUYER)`.
- **CSV encoding:** UTF-8 with BOM (`utf-8-sig` on read), matching the existing four tables.
- **Scope:** categories `western_blot`, `rt_qpcr`, `elisa`, `cell_culture_transfection` only.
- **Test runner:** `.venv/bin/pytest` from the repo root. `pythonpath = ["src"]` is set in `pyproject.toml`.
- **Confidence vocabulary:** `drafted` | `reviewed`. **Source vocabulary:** `checklist` | `mary` | `model`.

## Deviations from the spec, decided while planning

1. **Tables are a cached module singleton, not app state passed through `request_context`.** `run_chat` forwards `request_context` untouched to `tools.dispatch`, and the demo chat router passes none. Threading tables through that path would touch three call sites for no gain. `astor.curation.tables()` loads once and caches; tests swap it with `monkeypatch`. Startup still validates by calling it in `create_app`.
2. **`roles.csv` gains 27 rows.** The checklist references ten roles that `roles.csv` never defined (every rt-qPCR and ELISA role), and cell culture has none. The spec's validation rule needs them. New rows carry `notes` = `drafted 2026-09-15 for troubleshooting; needs review` so Mary can find them.
3. **The seed script is a lint/report script.** The draft rows are authored directly in this plan, so `scripts/seed_troubleshooting.py` from the spec becomes `scripts/check_troubleshooting.py`: it loads the tables, prints per-category counts, the drafted share, and any role the checklist uses that roles.csv lacks.

## File Structure

| File | Responsibility |
|---|---|
| `src/astor/curation/__init__.py` | `CURATION_DIR`, cached `tables()` accessor, `reset()` for tests. |
| `src/astor/curation/loader.py` | Dataclasses for the five tables, per-file loaders, `load_all` with cross-table validation. |
| `src/astor/curation/text.py` | Tokeniser (Latin words plus CJK bigrams) and overlap score. Pure functions. |
| `src/astor/curation/troubleshoot.py` | Category hints, `classify_category`, `Matcher` (keyword then embedding), `resolve_products`. |
| `src/astor/chat/tools.py` | `_troubleshoot` handler and its schema. |
| `src/astor/chat/agent.py` | One prompt paragraph. |
| `src/astor/api/main.py` | Startup validation call. |
| `src/astor/eval/troubleshooting.py` | Gold case loader, retrieval-layer evaluation, gate, conversion to assistant `Scenario`s. |
| `scripts/check_troubleshooting.py` | Lint/report over the real tables. |
| `scripts/run_troubleshooting_eval.py` | Retrieval-layer eval against the real tables, optional live assistant layer. |
| `docs/curation/troubleshooting.csv` | The table. |
| `docs/curation/roles.csv` | 27 added roles. |
| `data/eval/troubleshooting_gold.csv` | The gold set. |
| `docs/curation/01-每张表怎么填.md` | Section for table 5. |

---

### Task 1: Curation loader with cross-table validation

**Files:**
- Create: `src/astor/curation/__init__.py`
- Create: `src/astor/curation/loader.py`
- Modify: `docs/curation/roles.csv` (append 27 rows)
- Create: `docs/curation/troubleshooting.csv` (header plus two rows for now; Task 2 fills it)
- Test: `tests/test_curation_loader.py`

**Interfaces:**
- Produces:
  - `loader.Category(category_id: str, category_name: str)`
  - `loader.Role(role: str, plain_description: str, lab_usually_owns_it: str, always_buy_fresh: str, notes: str)`
  - `loader.ChecklistRow(category_id, role, plain_name, required_or_optional, is_control, commonly_omitted, why_required, spec_constraint, depends_on)`
  - `loader.TroubleshootingEntry(entry_id, category_id, symptom, likely_cause, check_or_fix, fix_role: str | None, buy_needed, checklist_role_ref: str | None, confidence, source, reviewed_by, reviewed_on, notes)`
  - `loader.CurationTables(categories: dict[str, Category], roles: dict[str, Role], checklist: list[ChecklistRow], troubleshooting: list[TroubleshootingEntry])`
  - `loader.CurationError(ValueError)`
  - `loader.load_all(root: Path) -> CurationTables`
  - `astor.curation.tables() -> CurationTables` (cached), `astor.curation.reset() -> None`, `astor.curation.CURATION_DIR: Path`

- [ ] **Step 1: Append the missing roles to `docs/curation/roles.csv`**

Append these lines (the file has no trailing blank line; ensure a newline before the first appended row):

```csv
no_template_control,无模板对照（NTC，用水代替模板）/ no-template control,yes,no,"is_control=yes; drafted 2026-09-15 for troubleshooting; needs review"
no_rt_control,无逆转录酶对照（-RT）/ minus-RT control,yes,no,"is_control=yes; procedural, uses the same RNA; drafted 2026-09-15 for troubleshooting; needs review"
reference_gene,内参基因引物（GAPDH / ACTB / 18S）/ reference-gene primer set,no,no,"is_control=yes; drafted 2026-09-15 for troubleshooting; needs review"
primers,目标基因引物 / 探针 / target primers or probe,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
detection_chemistry,qPCR 检测化学（SYBR Green / TaqMan 预混液）/ qPCR master mix,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
rt_enzyme,逆转录酶 / 逆转录试剂盒 / reverse transcriptase or RT kit,no,yes,"drafted 2026-09-15 for troubleshooting; needs review"
qpcr_master_mix,qPCR 预混液 / qPCR master mix,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
rna_extraction_kit,RNA 提取试剂盒 / RNA extraction kit,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
dnase,DNase I（去除基因组DNA）/ DNase I,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
standard_curve,标准品（用于标准曲线）/ ELISA standard,no,no,"is_control=yes; usually in the kit; drafted 2026-09-15 for troubleshooting; needs review"
blank_zero_standard,空白 / 零标准（只有稀释液）/ blank well,sometimes,no,"is_control=yes; drafted 2026-09-15 for troubleshooting; needs review"
negative_control,阴性对照样本 / negative control matrix,sometimes,no,"is_control=yes; drafted 2026-09-15 for troubleshooting; needs review"
capture_detection_pair,捕获 / 检测抗体配对 / matched antibody pair,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
sample_diluent,样本 / 标准品稀释液 / assay diluent,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
tmb_substrate,TMB 显色底物 / TMB substrate,no,yes,"drafted 2026-09-15 for troubleshooting; needs review"
stop_solution,终止液 / stop solution,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
wash_buffer,洗涤缓冲液（PBST）/ wash buffer,yes,no,"drafted 2026-09-15 for troubleshooting; needs review"
elisa_plate,ELISA 板（预包被或高结合板）/ ELISA plate,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
transfection_reagent,转染试剂 / transfection reagent,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
plasmid_dna,质粒 DNA（无内毒素制备）/ plasmid DNA prep,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
culture_medium,细胞培养基 / culture medium,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
serum,血清（FBS）/ fetal bovine serum,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
antibiotic,抗生素（青链霉素）/ antibiotic supplement,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
cell_line,细胞系（低代次冻存）/ low-passage cell stock,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
trypsin,胰酶 / trypsin-EDTA,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
culture_plate,细胞培养板（TC处理）/ tissue-culture treated plate,sometimes,no,"drafted 2026-09-15 for troubleshooting; needs review"
mycoplasma_test,支原体检测试剂盒 / mycoplasma detection kit,no,no,"drafted 2026-09-15 for troubleshooting; needs review"
```

- [ ] **Step 2: Create `docs/curation/troubleshooting.csv` with the header and two rows**

Write with a UTF-8 BOM. Task 2 replaces the body.

```csv
entry_id,category_id,symptom,likely_cause,check_or_fix,fix_role,buy_needed,checklist_role_ref,confidence,source,reviewed_by,reviewed_on,notes
T0001,western_blot,完全没有条带 / no bands at all,二抗宿主与一抗不匹配 / secondary antibody does not match the primary host species,确认二抗是 anti-[一抗宿主]（如一抗是 rabbit 就用 anti-rabbit）；不匹配则更换二抗 / confirm the secondary is anti-[primary host]; replace it if not,secondary_antibody,yes,western_blot:secondary_antibody,drafted,checklist,,,
T0002,western_blot,完全没有条带 / no bands at all,显影底物（ECL）失效或过期 / ECL substrate inactive or expired,检查 ECL 有效期，用新鲜底物；确认 HRP 二抗未失活 / check the ECL date and use fresh substrate; confirm the HRP secondary is active,detection_substrate,yes,,drafted,model,,,
```

- [ ] **Step 3: Write the failing loader tests**

Create `tests/test_curation_loader.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_curation_loader.py -q`
Expected: ImportError, `astor.curation` does not exist.

- [ ] **Step 5: Write `src/astor/curation/loader.py`**

```python
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
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


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

        def bad(msg: str) -> CurationError:
            return CurationError(f"{path.name} {eid}: {msg}")

        if eid in seen:
            raise bad("duplicate entry_id")
        seen.add(eid)
        if r["category_id"] not in categories:
            raise bad(f"unknown category_id {r['category_id']!r}")
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
```

- [ ] **Step 6: Write `src/astor/curation/__init__.py`**

```python
"""Curated domain knowledge (docs/curation/*.csv) as a process-wide, validated singleton.

`tables()` loads on first use and caches. `create_app` calls it at startup so a
malformed CSV fails boot. Tests point CURATION_DIR at a temp dir and call reset().
"""
from __future__ import annotations

from pathlib import Path

from astor.curation.loader import CurationError, CurationTables, load_all

CURATION_DIR: Path = Path(__file__).resolve().parents[3] / "docs" / "curation"

_cache: CurationTables | None = None


def tables() -> CurationTables:
    global _cache
    if _cache is None:
        _cache = load_all(CURATION_DIR)
    return _cache


def reset() -> None:
    global _cache
    _cache = None


__all__ = ["CURATION_DIR", "CurationError", "CurationTables", "reset", "tables"]
```

- [ ] **Step 7: Run the loader tests**

Run: `.venv/bin/pytest tests/test_curation_loader.py -q`
Expected: all pass. If `test_real_tables_load` fails on a missing role, the roles append in Step 1 was incomplete; compare the checklist's `role` column against `roles.csv`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: no regressions.

---

### Task 2: The troubleshooting table content

**Files:**
- Modify: `docs/curation/troubleshooting.csv` (replace body)
- Create: `scripts/check_troubleshooting.py`

**Interfaces:**
- Consumes: `astor.curation.tables()`
- Produces: the 50-row table every later task reads.

- [ ] **Step 1: Replace `docs/curation/troubleshooting.csv` with the full table**

Keep the BOM and header from Task 1. Body:

```csv
T0001,western_blot,完全没有条带 / no bands at all,二抗宿主与一抗不匹配 / secondary antibody does not match the primary host species,确认二抗是 anti-[一抗宿主]（一抗 rabbit 就用 anti-rabbit）；不匹配则更换二抗 / confirm the secondary is anti-[primary host] and replace it if not,secondary_antibody,yes,western_blot:secondary_antibody,drafted,checklist,,,
T0002,western_blot,完全没有条带 / no bands at all,显影底物（ECL）失效或过期 / ECL substrate inactive or expired,检查 ECL 有效期并换新鲜底物；确认 HRP 二抗未失活 / check the ECL date and use fresh substrate; confirm the HRP secondary is active,detection_substrate,yes,,drafted,model,,,
T0003,western_blot,完全没有条带 / no bands at all,转膜失败（膜未活化、三明治装反、气泡）/ transfer failed (membrane not activated or sandwich reversed or bubbles),转膜后用丽春红染色确认蛋白在膜上；PVDF 须甲醇活化；检查方向和气泡 / Ponceau-stain the membrane after transfer; activate PVDF in methanol; check orientation and bubbles,membrane,sometimes,,drafted,model,,,
T0004,western_blot,目标条带没有但不确定是抗体还是样本的问题 / no target band and unsure whether antibody or sample is at fault,缺少阳性对照，无法区分'目标不表达'和'实验失败' / no positive control so target-absent and assay-failed look the same,加一条已知表达目标蛋白的裂解液作为阳性对照 / run a lysate known to express the target as a positive control,positive_control,yes,western_blot:positive_control,drafted,checklist,,,
T0005,western_blot,磷酸化蛋白信号很弱或没有 / weak or no signal for a phospho-protein,用脱脂奶粉封闭，奶粉中的磷蛋白掩盖磷酸化表位 / blocked with milk whose phosphoproteins mask phospho-epitopes,封闭和稀释抗体都改用 5% BSA/TBST，不用奶粉 / block and dilute antibodies in 5% BSA in TBST instead of milk,blocking_agent,sometimes,western_blot:blocking_agent,drafted,checklist,,,
T0006,western_blot,整张膜背景高 / high background across the membrane,封闭不足或抗体浓度过高 / insufficient blocking or antibodies too concentrated,室温封闭 1 h（5%）；稀释一抗二抗；增加 TBST 洗涤次数和时间 / block 1 h at room temperature; dilute both antibodies; add TBST washes,blocking_agent,sometimes,,drafted,model,,,
T0007,western_blot,背景呈斑点状 / speckled or dotted background,封闭液或抗体液中有颗粒聚集，或膜曾干过 / aggregates in blocking or antibody solution or the membrane dried out,过滤封闭液；抗体液离心后使用；全程保持膜湿润 / filter the blocking solution; spin antibody solutions; keep the membrane wet throughout,blocking_agent,no,,drafted,model,,,
T0008,western_blot,多条非特异条带 / multiple non-specific bands,一抗浓度过高或特异性差；样本蛋白降解 / primary too concentrated or poorly specific; sample degraded,滴定一抗浓度；裂解时加蛋白酶抑制剂；样本保持低温 / titrate the primary; add protease inhibitors at lysis; keep samples cold,primary_antibody,sometimes,,drafted,model,,,
T0009,western_blot,条带位置与预期分子量不符 / band at an unexpected molecular weight,翻译后修饰、剪接异构体，或 marker 读错 / post-translational modification or isoform or ladder misread,对照预染 marker 重新判读；查文献中该蛋白的表观分子量 / re-read against a pre-stained ladder; check the literature for the apparent size,protein_ladder,sometimes,,drafted,model,,,
T0010,western_blot,泳道之间条带强弱无法比较 / cannot compare band intensity between lanes,没有上样对照，上样量差异无法排除 / no loading control so loading differences cannot be ruled out,同一张膜检测管家蛋白（GAPDH / β-actin）作上样对照 / probe the same membrane for a housekeeping protein as loading control,loading_control,yes,western_blot:loading_control,drafted,checklist,,,
T0011,western_blot,上样对照条带与目标条带重叠 / loading control band overlaps the target band,管家蛋白与目标蛋白分子量相近 / housekeeping protein has a similar size to the target,换一个分子量不同的上样对照（GAPDH 37 kDa、β-actin 42、tubulin 55）/ pick a loading control at a different size,loading_control,yes,western_blot:loading_control,drafted,checklist,,,
T0012,western_blot,条带笑脸状或变形 / smiling or distorted bands,电泳过热或样本盐浓度高 / gel overheated or high salt in the sample,降低电压、冰上或冷室电泳；样本脱盐或减少上样体积 / run at lower voltage or in the cold; desalt or load less,buffer_running,no,,drafted,model,,,
T0013,western_blot,曝光后出现白色（负）条带 / white or negative bands on exposure,蛋白量或 HRP 过多导致底物局部耗尽 / too much protein or HRP so the substrate is locally depleted,减少上样量或稀释抗体；缩短曝光 / load less or dilute antibodies; shorten the exposure,detection_substrate,sometimes,,drafted,model,,,
T0101,rt_qpcr,无模板对照（NTC）有信号 / signal in the no-template control,试剂污染或引物二聚体 / reagent contamination or primer dimers,每块板都做 NTC；换新水和预混液；看熔解曲线是否为二聚体峰 / run NTC on every plate; use fresh water and master mix; check the melt curve for a dimer peak,qpcr_master_mix,sometimes,rt_qpcr:no_template_control,drafted,checklist,,,
T0102,rt_qpcr,-RT 对照有信号 / signal in the minus-RT control,基因组 DNA 污染 / genomic DNA contamination,RNA 做 DNase 处理；引物设计跨外显子连接处 / DNase-treat the RNA; design primers across an exon-exon junction,dnase,yes,rt_qpcr:no_rt_control,drafted,checklist,,,
T0103,rt_qpcr,所有样本都没有 Ct 值 / no Ct in any sample,RNA 降解或逆转录失败 / RNA degraded or reverse transcription failed,检查 RNA 完整性（A260/280、胶）；换新鲜逆转录酶重做 cDNA / check RNA integrity; remake cDNA with fresh reverse transcriptase,rt_enzyme,yes,,drafted,model,,,
T0104,rt_qpcr,某对引物完全不扩增 / one primer pair never amplifies,引物设计有误或效率低 / primers mis-designed or inefficient,用标准曲线验证引物效率（90–110%）；不合格则重新设计 / validate efficiency with a standard curve and redesign if outside 90–110%,primers,yes,rt_qpcr:primers,drafted,checklist,,,
T0105,rt_qpcr,所有样本 Ct 都很晚（>35）/ late Ct (>35) in every sample,模板太少或提取残留抑制物（酚、乙醇）/ too little template or carried-over inhibitors,重新纯化 RNA；稀释模板看 Ct 是否反而提前 / re-purify the RNA; dilute the template and see whether Ct improves,rna_extraction_kit,sometimes,,drafted,model,,,
T0106,rt_qpcr,熔解曲线多峰 / multiple peaks in the melt curve,非特异扩增或引物二聚体（SYBR）/ non-specific amplification or primer dimers with SYBR,重新设计引物或提高退火温度；必要时改用 TaqMan 探针 / redesign primers or raise annealing temperature; consider a TaqMan probe,primers,yes,rt_qpcr:detection_chemistry,drafted,checklist,,,
T0107,rt_qpcr,内参基因 Ct 随处理变化 / reference gene Ct shifts with treatment,该管家基因在此条件下不稳定 / the housekeeping gene is not stable under this condition,测试 2–3 个内参基因，选在你的条件下最稳定的 / test two or three reference genes and keep the stable one,reference_gene,yes,rt_qpcr:reference_gene,drafted,checklist,,,
T0108,rt_qpcr,复孔之间差异大 / high variability between technical replicates,移液误差、体积太小、气泡 / pipetting error or tiny volumes or bubbles,配预混液再分装；反应体积不低于 10 µL；上机前离心 / make a master mix; keep reactions at 10 µL or more; spin the plate,pipette_tips,no,,drafted,model,,,
T0109,rt_qpcr,表达倍数结果不合理 / fold-change results make no sense,未用内参归一化，或引物效率不同 / no reference-gene normalisation or unequal primer efficiencies,用 ΔΔCt 并用验证过效率的引物；确认内参稳定 / use ΔΔCt with efficiency-validated primers; confirm the reference gene is stable,reference_gene,yes,rt_qpcr:reference_gene,drafted,checklist,,,
T0110,rt_qpcr,基因组 DNA 样本也能扩增 / genomic DNA template also amplifies,引物不跨外显子连接处 / primers do not span an exon junction,重新设计跨外显子-外显子连接的引物 / redesign primers across an exon-exon junction,primers,yes,rt_qpcr:primers,drafted,checklist,,,
T0111,rt_qpcr,RNA 产量低 / low RNA yield,样本量少、裂解不完全或 RNase 污染 / small sample or incomplete lysis or RNase contamination,全程无 RNase 操作；选择适合该样本类型的提取试剂盒 / RNase-free workflow throughout; choose an extraction kit suited to the sample type,rna_extraction_kit,yes,,drafted,model,,,
T0112,rt_qpcr,扩增曲线形状异常或提前平台 / abnormal or early-plateau amplification curves,仪器染料通道与检测化学不匹配，或封板膜问题 / dye channel does not match the chemistry or plate seal issue,确认仪器通道与 SYBR/探针一致；用光学封板膜 / confirm the instrument channel matches SYBR or the probe; use optical seals,detection_chemistry,sometimes,,drafted,model,,,
T0201,elisa,整板包括标准品都没有信号 / no signal in any well including standards,底物或 HRP 失活、读数波长错误、未加终止液 / substrate or HRP inactive or wrong wavelength or no stop solution,换新鲜 TMB；加终止液后在 450 nm 读数 / use fresh TMB; read at 450 nm after adding stop solution,tmb_substrate,yes,,drafted,model,,,
T0202,elisa,标准品正常但样本无信号 / standards work but samples give no signal,样本浓度低于检测范围或基质效应 / analyte below range or matrix effect,减少稀释倍数；标准品用与样本相同的基质稀释 / dilute less; prepare standards in the same matrix as the samples,sample_diluent,yes,elisa:sample_diluent,drafted,checklist,,,
T0203,elisa,整板背景高 / high background across the plate,洗涤或封闭不足 / insufficient washing or blocking,增加洗涤次数、每次浸泡 30 s；检查洗涤液配方 / add wash cycles with a 30 s soak; check the wash buffer,wash_buffer,sometimes,,drafted,model,,,
T0204,elisa,空白孔读数高 / blank wells read high,检测抗体浓度过高或 TMB 见光 / detection antibody too concentrated or TMB exposed to light,滴定检测抗体；TMB 避光保存和使用 / titrate the detection antibody; keep TMB in the dark,capture_detection_pair,sometimes,,drafted,model,,,
T0205,elisa,标准曲线不线性或 R² 低 / standard curve non-linear or low R²,标准品复溶错误、梯度稀释误差或 hook 效应 / standard reconstituted wrongly or dilution error or hook effect,用新标准品重新复溶；至少 5 个点加空白；仔细做梯度稀释 / reconstitute a fresh standard; at least five points plus blank; careful serial dilution,standard_curve,yes,elisa:standard_curve,drafted,checklist,,,
T0206,elisa,样本读数超出最高标准品 / sample readings above the top standard,样本浓度过高 / sample too concentrated,用相同稀释液稀释样本后重测 / dilute the sample in the same diluent and re-run,sample_diluent,sometimes,elisa:sample_diluent,drafted,checklist,,,
T0207,elisa,复孔 CV 高 / high CV between duplicates,移液误差、边缘效应、洗涤不彻底 / pipetting or edge effects or incomplete washing,避开边缘孔；统一加样时间；确认每孔洗净 / avoid edge wells; keep timing consistent; confirm complete washing,pipette_tips,no,,drafted,model,,,
T0208,elisa,阴性对照有信号 / signal in the negative control,非特异结合或交叉反应 / non-specific binding or cross-reactivity,确认抗体是验证过的配对；增加封闭剂；用与样本相同基质的阴性对照 / confirm a validated pair; add blocker; use a negative control in the sample matrix,negative_control,yes,elisa:negative_control,drafted,checklist,,,
T0209,elisa,夹心法有待测物却没有信号 / sandwich ELISA gives no signal although analyte is present,捕获与检测抗体不是验证配对或识别同一表位 / capture and detection antibodies not a validated pair or bind the same epitope,改用验证过的 matched pair（识别不同表位）/ switch to a validated matched pair that binds different epitopes,capture_detection_pair,yes,elisa:capture_detection_pair,drafted,checklist,,,
T0210,elisa,不同板或不同天结果不一致 / results differ between plates or days,没有阳性对照，试剂盒批次变化无法察觉 / no positive control so lot changes go unnoticed,每块板加一个浓度落在曲线中段的阳性对照 / include a positive control near mid-curve on every plate,positive_control,yes,elisa:positive_control,drafted,checklist,,,
T0211,elisa,显色过快、全板饱和 / colour develops too fast and saturates,HRP 偶联物过多或显色时间过长 / too much HRP conjugate or too long a development,稀释偶联物；缩短 TMB 时间；及时加终止液 / dilute the conjugate; shorten TMB time; stop promptly,stop_solution,sometimes,,drafted,model,,,
T0212,elisa,板内出现颜色梯度 / colour gradient across the plate,温度梯度、未封板、边缘蒸发 / temperature gradient or unsealed plate or edge evaporation,封板；试剂平衡至室温再用；避免边缘孔 / seal the plate; equilibrate reagents to room temperature; avoid edge wells,elisa_plate,no,,drafted,model,,,
T0213,elisa,OD 无法扣除本底 / cannot subtract background from OD,没有做空白 / 零标准 / no blank or zero standard,每块板用相同稀释液做空白孔 / include a blank in the same diluent on every plate,blank_zero_standard,no,elisa:blank_zero_standard,drafted,checklist,,,
T0301,cell_culture_transfection,转染效率低 / low transfection efficiency,转染试剂与 DNA 比例不当或细胞密度不合适 / wrong reagent-to-DNA ratio or cells at the wrong density,优化试剂:DNA 比例；在 70–80% 汇合度时转染 / optimise the reagent-to-DNA ratio; transfect at 70–80% confluence,transfection_reagent,yes,,drafted,model,,,
T0302,cell_culture_transfection,转染效率低 / low transfection efficiency,质粒质量差（内毒素、A260/280 偏低）/ poor plasmid quality (endotoxin or low A260/280),用无内毒素质粒提取试剂盒重新制备 / re-prepare with an endotoxin-free plasmid kit,plasmid_dna,yes,,drafted,model,,,
T0303,cell_culture_transfection,转染后细胞大量死亡 / cells die after transfection,试剂毒性、DNA 过量或无血清时间过长 / reagent toxicity or too much DNA or too long in serum-free medium,减少试剂和 DNA 用量；4–6 h 后换含血清完全培养基 / reduce reagent and DNA; change to complete medium after 4–6 h,transfection_reagent,yes,,drafted,model,,,
T0304,cell_culture_transfection,转染后不表达 / no expression after transfection,启动子在该细胞系不活跃或质粒有误 / promoter inactive in this line or wrong plasmid,用 GFP 质粒作阳性对照；测序确认质粒；核对启动子 / run a GFP plasmid as a control; sequence the plasmid; check the promoter,plasmid_dna,sometimes,,drafted,model,,,
T0305,cell_culture_transfection,细胞生长缓慢 / cells grow slowly,培养基或血清批次不对、代次过高、支原体 / wrong medium or serum lot or high passage or mycoplasma,核对培养基配方和血清批次；复苏低代次冻存；做支原体检测 / check medium and serum lot; thaw a low-passage stock; test for mycoplasma,culture_medium,sometimes,,drafted,model,,,
T0306,cell_culture_transfection,贴壁细胞脱落 / adherent cells detach,胰酶消化过度、污染或板未经 TC 处理 / over-trypsinisation or contamination or plate not TC-treated,缩短胰酶时间；用 TC 处理培养板；检查污染 / shorten trypsin exposure; use TC-treated plates; check for contamination,trypsin,sometimes,,drafted,model,,,
T0307,cell_culture_transfection,培养基浑浊、变色 / medium cloudy or discoloured,细菌或真菌污染 / bacterial or fungal contamination,丢弃并消毒；短期加青链霉素；检查无菌操作 / discard and decontaminate; add pen-strep short-term; review aseptic technique,antibiotic,yes,,drafted,model,,,
T0308,cell_culture_transfection,培养基清亮但细胞状态差、长得慢 / medium clear but cells unhealthy and slow,支原体污染 / mycoplasma contamination,做支原体检测；阳性则处理或丢弃并换新冻存 / test for mycoplasma; treat or discard and thaw a fresh stock,mycoplasma_test,yes,,drafted,model,,,
T0309,cell_culture_transfection,细胞形态改变 / cells change morphology,代次过高、血清换批或支原体 / high passage or serum lot change or mycoplasma,复苏低代次冻存；固定血清批次 / thaw a low-passage stock; keep the serum lot constant,cell_line,yes,,drafted,model,,,
T0310,cell_culture_transfection,培养基很快变黄 / medium turns yellow quickly,细胞过密、污染或代谢负荷高 / over-confluent or contaminated or high metabolic load,更频繁传代或换液；排查污染 / passage or feed more often; rule out contamination,culture_medium,sometimes,,drafted,model,,,
T0311,cell_culture_transfection,培养基变紫红 / medium turns pink or purple,CO2 不足或培养箱故障 / low CO2 or incubator fault,检查培养箱 CO2 和培养基缓冲体系 / check incubator CO2 and the medium buffer system,culture_medium,no,,drafted,model,,,
T0312,cell_culture_transfection,同一方法在某些细胞系有效、另一些无效 / works in one cell line but not another,难转染细胞系（原代、悬浮）/ hard-to-transfect line (primary or suspension),改用针对该细胞类型的脂质体试剂、电转或病毒载体 / switch to a line-specific lipid reagent or electroporation or viral delivery,transfection_reagent,yes,,drafted,model,,,
T0313,cell_culture_transfection,板内转染不均匀 / uneven transfection across the plate,复合物未混匀或细胞成团 / complexes not mixed or cells clumped,复合物逐滴加入并轻摇；铺板时确保单细胞悬液 / add complexes dropwise and rock; seed as a single-cell suspension,transfection_reagent,no,,drafted,model,,,
```

- [ ] **Step 2: Write `scripts/check_troubleshooting.py`**

```python
"""Lint the troubleshooting table and report what Mary still has to review.

    .venv/bin/python scripts/check_troubleshooting.py

Exit 1 if the tables fail validation. Otherwise print per-category counts, the
share of rows still `drafted`, and any checklist role missing from roles.csv.
"""
from __future__ import annotations

import sys
from collections import Counter

from astor import curation
from astor.curation.loader import CurationError


def main() -> int:
    try:
        t = curation.tables()
    except CurationError as exc:
        print(f"INVALID: {exc}")
        return 1
    per_cat = Counter(e.category_id for e in t.troubleshooting)
    drafted = sum(1 for e in t.troubleshooting if e.confidence == "drafted")
    print(f"{len(t.troubleshooting)} entries across {len(per_cat)} categories")
    for cid, n in sorted(per_cat.items()):
        print(f"  {cid:28s} {n:3d}")
    print(f"drafted: {drafted}/{len(t.troubleshooting)}")
    missing = sorted({r.role for r in t.checklist} - set(t.roles))
    print(f"checklist roles missing from roles.csv: {missing or 'none'}")
    no_fix = [e.entry_id for e in t.troubleshooting if e.fix_role is None]
    print(f"entries with procedural fix only: {no_fix or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run the check and the loader tests**

Run: `.venv/bin/python scripts/check_troubleshooting.py && .venv/bin/pytest tests/test_curation_loader.py -q`
Expected: `51 entries across 4 categories`, per-category counts 13/12/13/13, `drafted: 51/51`, `missing: none`, and tests pass.

---

### Task 3: Tokeniser and matcher

**Files:**
- Create: `src/astor/curation/text.py`
- Create: `src/astor/curation/troubleshoot.py`
- Test: `tests/test_troubleshoot.py`

**Interfaces:**
- Consumes: `loader.CurationTables`, `loader.TroubleshootingEntry`, `astor.catalog.embeddings.Embedder` (`.embed(list[str]) -> list[list[float]]`), `astor.api.repo.list_products(session, q, category, page, page_size)`.
- Produces:
  - `text.tokens(s: str) -> set[str]`
  - `text.overlap(query: set[str], doc: set[str]) -> float`
  - `troubleshoot.CATEGORY_HINTS: dict[str, tuple[str, ...]]`
  - `troubleshoot.classify_category(symptom: str, categories: Iterable[str]) -> str | None`
  - `troubleshoot.Hit(entry: TroubleshootingEntry, score: float)`
  - `troubleshoot.Matcher(tables, embedder=None)` with `.search(symptom: str, *, category: str | None, limit: int) -> tuple[list[Hit], str]` where the string is `"keyword"` or `"semantic"`.
  - `troubleshoot.KEYWORD_FLOOR: float = 0.2`
  - `troubleshoot.resolve_products(session, entry, tables, *, limit=3) -> tuple[list[dict], str | None]` returning raw product rows and the role's `lab_usually_owns_it`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_troubleshoot.py`:

```python
"""Symptom matching is deterministic: same tables, same query, same rows.

Fixtures are tiny so each assertion names the row it expects. The embedder is a
fake so the semantic fallback is tested without a network."""
from __future__ import annotations

import pytest

from astor.api import repo
from astor.curation import text, troubleshoot
from astor.curation.loader import (Category, ChecklistRow, CurationTables, Role,
                                   TroubleshootingEntry)


def _e(eid, cat, symptom, cause, fix_role=None, confidence="drafted"):
    return TroubleshootingEntry(eid, cat, symptom, cause, "fix", fix_role, "yes", None,
                                confidence, "model", "", "", "")


@pytest.fixture
def tables():
    cats = {c: Category(c, c) for c in ("western_blot", "rt_qpcr", "elisa", "cell_culture_transfection")}
    roles = {
        "secondary_antibody": Role("secondary_antibody", "二抗 / secondary antibody", "no", "no", ""),
        "water": Role("water", "水", "yes", "no", ""),
        "primers": Role("primers", "引物 / primers", "no", "no", ""),
    }
    entries = [
        _e("T1", "western_blot", "完全没有条带 / no bands at all", "二抗宿主不匹配 / secondary mismatch", "secondary_antibody"),
        _e("T2", "western_blot", "背景高 / high background", "封闭不足 / insufficient blocking", None),
        _e("T3", "rt_qpcr", "NTC 有信号 / signal in the no-template control", "污染 / contamination", "water"),
        _e("T4", "rt_qpcr", "熔解曲线多峰 / multiple peaks in the melt curve", "引物二聚体 / primer dimers", "primers"),
        _e("T5", "elisa", "标准曲线不线性 / standard curve non-linear", "稀释错误 / dilution error", None),
        _e("T6", "cell_culture_transfection", "转染效率低 / low transfection efficiency", "比例不对 / wrong ratio", None),
    ]
    return CurationTables(cats, roles, [], entries)


class _FakeEmbedder:
    """Maps a few known strings to fixed unit vectors so cosine is predictable."""
    _vec = {"gel looks weird": [1.0, 0.0], "smiling": [1.0, 0.0]}

    def embed(self, texts):
        return [self._vec.get(t, [0.0, 1.0]) for t in texts]


# ------------------------------------------------------------------ tokens #
def test_tokens_split_latin_words_and_cjk_bigrams():
    assert text.tokens("No bands at all") == {"no", "bands", "at", "all"}
    assert text.tokens("没有条带") == {"没有", "有条", "条带"}
    assert text.tokens("带") == {"带"}


def test_tokens_mix_scripts_and_keep_hyphenated_terms():
    assert text.tokens("RT-qPCR 熔解曲线") == {"rt-qpcr", "熔解", "解曲", "曲线"}


def test_overlap_is_fraction_of_query_tokens_found():
    assert text.overlap({"no", "bands"}, {"no", "bands", "at", "all"}) == 1.0
    assert text.overlap({"no", "bands", "xyz"}, {"no", "bands"}) == pytest.approx(2 / 3)
    assert text.overlap(set(), {"a"}) == 0.0


# ---------------------------------------------------------------- category #
def test_classify_category_by_hint_words():
    cats = ["western_blot", "rt_qpcr", "elisa", "cell_culture_transfection"]
    assert troubleshoot.classify_category("my western blot has no bands", cats) == "western_blot"
    assert troubleshoot.classify_category("qPCR 没有 Ct", cats) == "rt_qpcr"
    assert troubleshoot.classify_category("ELISA 标准曲线不好", cats) == "elisa"
    assert troubleshoot.classify_category("转染效率很低", cats) == "cell_culture_transfection"


def test_classify_category_returns_none_when_no_hint():
    assert troubleshoot.classify_category("everything is broken", ["western_blot"]) is None


# ----------------------------------------------------------------- matcher #
def test_keyword_match_with_explicit_category(tables):
    m = troubleshoot.Matcher(tables)
    hits, kind = m.search("no bands at all", category="western_blot", limit=5)
    assert kind == "keyword"
    assert [h.entry.entry_id for h in hits][0] == "T1"
    assert all(h.entry.category_id == "western_blot" for h in hits)


def test_keyword_match_in_chinese(tables):
    m = troubleshoot.Matcher(tables)
    hits, kind = m.search("熔解曲线有好几个峰", category=None, limit=5)
    assert kind == "keyword"
    assert hits[0].entry.entry_id == "T4"


def test_category_omitted_is_classified_from_symptom(tables):
    m = troubleshoot.Matcher(tables)
    hits, _ = m.search("western blot 完全没有条带", category=None, limit=5)
    assert hits[0].entry.entry_id == "T1"
    assert all(h.entry.category_id == "western_blot" for h in hits)


def test_unknown_category_is_ignored_not_fatal(tables):
    m = troubleshoot.Matcher(tables)
    hits, _ = m.search("no bands", category="flow_cytometry", limit=5)
    assert hits[0].entry.entry_id == "T1"


def test_limit_and_ordering(tables):
    m = troubleshoot.Matcher(tables)
    hits, _ = m.search("no bands high background", category="western_blot", limit=1)
    assert len(hits) == 1
    assert hits[0].score >= troubleshoot.KEYWORD_FLOOR


def test_semantic_fallback_when_keywords_miss(tables):
    m = troubleshoot.Matcher(tables, embedder=_FakeEmbedder())
    # give T2 a symptom the fake embedder recognises
    m._doc_vectors[1] = [1.0, 0.0]
    hits, kind = m.search("gel looks weird", category="western_blot", limit=3)
    assert kind == "semantic"
    assert hits[0].entry.entry_id == "T2"


def test_keyword_only_when_no_embedder(tables):
    m = troubleshoot.Matcher(tables, embedder=None)
    hits, kind = m.search("gel looks weird", category="western_blot", limit=3)
    assert hits == [] and kind == "keyword"


def test_embedder_failure_at_build_degrades_to_keyword(tables):
    class Boom:
        def embed(self, texts): raise RuntimeError("no key")
    m = troubleshoot.Matcher(tables, embedder=Boom())
    hits, kind = m.search("no bands", category="western_blot", limit=3)
    assert kind == "keyword" and hits[0].entry.entry_id == "T1"


# ---------------------------------------------------------------- resolve #
def test_resolve_products_searches_by_role_description(monkeypatch, tables):
    calls = []
    def fake_list(session, q, category, page, page_size, **kw):
        calls.append(q)
        return [{"id": "p1", "name": "Goat anti-Rabbit IgG HRP", "brand": "X", "category": "antibodies",
                 "astor_sku": "A1", "mpn": None, "region": None, "offer_count": 1, "best_landed": None}], 1
    monkeypatch.setattr(repo, "list_products", fake_list)
    entry = tables.troubleshooting[0]  # T1, secondary_antibody
    products, owns = troubleshoot.resolve_products(object(), entry, tables)
    assert calls == ["secondary antibody"]
    assert products[0]["id"] == "p1" and owns == "no"


def test_resolve_skips_search_when_lab_owns_role(monkeypatch, tables):
    monkeypatch.setattr(repo, "list_products", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not search")))
    entry = tables.troubleshooting[2]  # T3, water
    products, owns = troubleshoot.resolve_products(object(), entry, tables)
    assert products == [] and owns == "yes"


def test_resolve_without_fix_role_returns_nothing(tables):
    entry = tables.troubleshooting[1]  # T2, no fix_role
    assert troubleshoot.resolve_products(object(), entry, tables) == ([], None)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_troubleshoot.py -q`
Expected: ImportError on `astor.curation.text`.

- [ ] **Step 3: Write `src/astor/curation/text.py`**

```python
"""Script-aware tokens for short bilingual strings.

Latin runs become lowercase words (hyphens and plus signs kept, so 'rt-qpcr'
survives). CJK runs become character bigrams, which is the cheapest thing that
matches '没有条带' against '完全没有条带' without a segmenter. A lone CJK
character is kept as a unigram so it is not silently dropped.
"""
from __future__ import annotations

import re

_LATIN = re.compile(r"[a-z0-9][a-z0-9\-\+]*")
_CJK = re.compile(r"[一-鿿]+")

STOPWORDS = frozenset({"the", "a", "an", "my", "i", "is", "are", "it", "of", "and", "to",
                       "in", "on", "with", "for", "有", "了", "很", "我", "的"})


def tokens(s: str) -> set[str]:
    s = (s or "").lower()
    out: set[str] = set()
    for w in _LATIN.findall(s):
        if w not in STOPWORDS:
            out.add(w)
    for run in _CJK.findall(s):
        if len(run) == 1:
            out.add(run)
            continue
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram not in STOPWORDS:
                out.add(bigram)
    return out


def overlap(query: set[str], doc: set[str]) -> float:
    """Fraction of query tokens present in the document. Query-recall, because a
    customer's symptom is short and the row's text is long."""
    if not query:
        return 0.0
    return len(query & doc) / len(query)
```

- [ ] **Step 4: Write `src/astor/curation/troubleshoot.py`**

```python
"""Match a customer's failed-experiment description to curated troubleshooting rows.

Two stages, both deterministic given the tables: token overlap first, and if
nothing clears KEYWORD_FLOOR, cosine similarity over row embeddings computed once
when the Matcher is built. No LLM call. The tool layer decides how to present
`drafted` rows; this module only finds them.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

from astor.api import repo
from astor.curation import text
from astor.curation.loader import CurationTables, TroubleshootingEntry

log = logging.getLogger(__name__)

KEYWORD_FLOOR = 0.2

# Words that place a symptom in a category when the model did not say which.
# Hand-written, like protocols/categories.py SEED_SYNONYMS, and scored by overlap
# with the tokeniser so a Chinese hint is matched as bigrams.
CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "western_blot": ("western", "blot", "immunoblot", "wb", "membrane", "bands", "band", "ecl",
                     "条带", "转膜", "免疫印迹", "显影"),
    "rt_qpcr": ("qpcr", "rt-qpcr", "pcr", "ct", "amplification", "melt", "primer", "primers",
                "ntc", "cdna", "扩增", "引物", "熔解", "内参", "逆转录"),
    "elisa": ("elisa", "plate", "od", "standard", "absorbance", "tmb", "wells", "标准曲线", "酶联",
              "显色", "复孔", "空白"),
    "cell_culture_transfection": ("transfection", "transfect", "cells", "culture", "medium", "confluent",
                                  "plasmid", "mycoplasma", "转染", "细胞", "培养基", "支原体", "传代"),
}


def classify_category(symptom: str, categories: Iterable[str]) -> str | None:
    q = text.tokens(symptom)
    best, best_hits = None, 0
    for cid in categories:
        hints = CATEGORY_HINTS.get(cid, ())
        hint_tokens: set[str] = set()
        for h in hints:
            hint_tokens |= text.tokens(h)
        hits = len(q & hint_tokens)
        if hits > best_hits:
            best, best_hits = cid, hits
    return best


@dataclass(frozen=True)
class Hit:
    entry: TroubleshootingEntry
    score: float


def _doc_text(e: TroubleshootingEntry) -> str:
    return f"{e.symptom} {e.likely_cause}"


def _cosine(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u)) or 1.0
    nv = math.sqrt(sum(b * b for b in v)) or 1.0
    return dot / (nu * nv)


class Matcher:
    def __init__(self, tables: CurationTables, embedder=None) -> None:
        self.tables = tables
        self.entries = list(tables.troubleshooting)
        self._doc_tokens = [text.tokens(_doc_text(e)) for e in self.entries]
        self._embedder = embedder
        self._doc_vectors: list[list[float]] | None = None
        if embedder is not None and self.entries:
            try:
                self._doc_vectors = embedder.embed([_doc_text(e) for e in self.entries])
            except Exception:  # noqa: BLE001 — a dead embedder degrades to keyword-only
                log.warning("troubleshooting row embeddings unavailable; keyword-only", exc_info=True)
                self._embedder = None

    def _candidates(self, symptom: str, category: str | None) -> list[int]:
        cats = set(self.tables.categories)
        if category in cats:
            chosen = {category}
        else:
            guess = classify_category(symptom, cats)
            chosen = {guess} if guess else cats
        return [i for i, e in enumerate(self.entries) if e.category_id in chosen]

    def search(self, symptom: str, *, category: str | None, limit: int) -> tuple[list[Hit], str]:
        idx = self._candidates(symptom, category)
        q = text.tokens(symptom)
        scored = [(text.overlap(q, self._doc_tokens[i]), i) for i in idx]
        keyword = sorted(((s, i) for s, i in scored if s >= KEYWORD_FLOOR), key=lambda t: (-t[0], t[1]))
        if keyword:
            return [Hit(self.entries[i], round(s, 3)) for s, i in keyword[:limit]], "keyword"
        if self._embedder is None or self._doc_vectors is None:
            return [], "keyword"
        try:
            qv = self._embedder.embed([symptom])[0]
        except Exception:  # noqa: BLE001
            log.warning("troubleshooting query embedding failed", exc_info=True)
            return [], "keyword"
        sem = sorted(((_cosine(qv, self._doc_vectors[i]), i) for i in idx), key=lambda t: (-t[0], t[1]))
        return [Hit(self.entries[i], round(s, 3)) for s, i in sem[:limit] if s > 0], "semantic"


def _search_term(description: str) -> str:
    """'二抗 —— 检测一抗、带酶标' or '引物 / 探针 / target primers' -> the last
    English segment, which is what the lexical product ranker can use."""
    parts = [p.strip() for p in description.replace("——", "/").split("/") if p.strip()]
    latin = [p for p in parts if any(c.isascii() and c.isalpha() for c in p)]
    return (latin[-1] if latin else parts[-1] if parts else description).strip()


def resolve_products(session, entry: TroubleshootingEntry, tables: CurationTables, *,
                     limit: int = 3) -> tuple[list[dict], str | None]:
    """Turn a row's fix_role into catalog rows. Returns (products, lab_usually_owns_it).
    Products are raw repo rows; the caller gates them for the buyer."""
    if not entry.fix_role:
        return [], None
    role = tables.roles[entry.fix_role]
    if role.lab_usually_owns_it == "yes":
        return [], "yes"
    rows, _ = repo.list_products(session, _search_term(role.plain_description), None, 1, limit)
    return rows, role.lab_usually_owns_it
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_troubleshoot.py -q`
Expected: all pass. If `test_tokens_mix_scripts_and_keep_hyphenated_terms` fails, check that `_LATIN` keeps the hyphen. If `test_resolve_products_searches_by_role_description` fails on the query string, check `_search_term` against `"二抗 / secondary antibody"`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: no regressions.

---

### Task 4: The `troubleshoot` chat tool

**Files:**
- Modify: `src/astor/chat/tools.py` (imports, handler, `_HANDLERS`, `TOOL_SCHEMAS`)
- Test: `tests/test_chat_tools.py` (append)

**Interfaces:**
- Consumes: `astor.curation.tables()`, `troubleshoot.Matcher`, `troubleshoot.resolve_products`, `astor.catalog.embeddings.get_embedder`.
- Produces: tool name `troubleshoot`; result shape `{"entries": [...], "match": "keyword"|"semantic"}`; `tools.matcher() -> Matcher` cached accessor; `tools.reset_matcher()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_tools.py`:

```python
# ------------------------------------------------------------ troubleshoot #
from astor.curation import troubleshoot as _ts
from astor.curation.loader import (Category, CurationTables, Role, TroubleshootingEntry)


def _ts_tables():
    cats = {"western_blot": Category("western_blot", "Western blot")}
    roles = {"secondary_antibody": Role("secondary_antibody", "二抗 / secondary antibody", "no", "no", ""),
             "water": Role("water", "水 / water", "yes", "no", "")}
    entries = [
        TroubleshootingEntry("T1", "western_blot", "no bands at all / 没有条带", "secondary mismatch",
                             "check host", "secondary_antibody", "yes", "western_blot:secondary_antibody",
                             "drafted", "checklist", "", "", ""),
        TroubleshootingEntry("T2", "western_blot", "smiling bands", "gel too hot", "run colder",
                             "water", "no", None, "reviewed", "mary", "Mary", "2026-09-15", ""),
    ]
    return CurationTables(cats, roles, [], entries)


@pytest.fixture
def ts_matcher(monkeypatch):
    monkeypatch.setattr(tools, "_matcher", _ts.Matcher(_ts_tables()))
    yield
    monkeypatch.setattr(tools, "_matcher", None)


def test_troubleshoot_returns_entries_with_products_and_refs(monkeypatch, ts_matcher):
    monkeypatch.setattr(repo, "list_products",
        lambda s, q, category, page, page_size, **kw: (
            [{"id": "p1", "name": "Goat anti-Rabbit IgG HRP", "brand": "X", "category": "antibodies",
              "astor_sku": "A1", "mpn": None, "region": None, "offer_count": 1, "best_landed": None}], 1))
    result, items = tools.dispatch(_sess(), "troubleshoot", {"symptom": "no bands at all", "category": "western_blot"})
    assert result["match"] == "keyword"
    e = result["entries"][0]
    assert e["entry_id"] == "T1" and e["confidence"] == "drafted" and e["fix_role"] == "secondary_antibody"
    assert e["lab_usually_owns_it"] == "no"
    # buyer-gated: brand/mpn/region withheld
    assert e["products"] == [{"id": "p1", "astor_sku": "A1", "name": "Goat anti-Rabbit IgG HRP",
                              "category": "antibodies", "offer_count": 1, "best_landed": None}]
    assert items == [tools.ReferencedItem("product", "p1", "Goat anti-Rabbit IgG HRP")]


def test_troubleshoot_lab_owned_role_has_no_products(monkeypatch, ts_matcher):
    monkeypatch.setattr(repo, "list_products", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no search")))
    result, items = tools.dispatch(_sess(), "troubleshoot", {"symptom": "smiling bands"})
    e = result["entries"][0]
    assert e["entry_id"] == "T2" and e["products"] == [] and e["lab_usually_owns_it"] == "yes"
    assert items == []


def test_troubleshoot_empty_result_shape(ts_matcher):
    result, items = tools.dispatch(_sess(), "troubleshoot", {"symptom": "zzz qqq"})
    assert result == {"entries": [], "match": "keyword"} and items == []


def test_troubleshoot_requires_symptom(ts_matcher):
    result, items = tools.dispatch(_sess(), "troubleshoot", {})
    assert "error" in result and items == []


def test_troubleshoot_schema_registered():
    names = {s["name"] for s in tools.TOOL_SCHEMAS}
    assert "troubleshoot" in names
    schema = next(s for s in tools.TOOL_SCHEMAS if s["name"] == "troubleshoot")
    assert schema["input_schema"]["required"] == ["symptom"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_chat_tools.py -q -k troubleshoot`
Expected: `AttributeError: module 'astor.chat.tools' has no attribute '_matcher'` or unknown tool error.

- [ ] **Step 3: Add the handler, accessor, registry entry, and schema to `src/astor/chat/tools.py`**

Add to the imports at the top:

```python
from astor import curation
from astor.curation import troubleshoot as _troubleshoot_mod
```

Add after `_flag_sourcing_request` and before `_HANDLERS`:

```python
_matcher: _troubleshoot_mod.Matcher | None = None


def matcher() -> _troubleshoot_mod.Matcher:
    """Row embeddings are computed once per process; a dead embedder leaves the
    matcher keyword-only rather than failing the tool."""
    global _matcher
    if _matcher is None:
        from astor.catalog.embeddings import get_embedder
        try:
            embedder = get_embedder()
        except Exception:  # noqa: BLE001
            log.warning("embedder unavailable; troubleshooting is keyword-only", exc_info=True)
            embedder = None
        _matcher = _troubleshoot_mod.Matcher(curation.tables(), embedder=embedder)
    return _matcher


def reset_matcher() -> None:
    global _matcher
    _matcher = None


def _troubleshoot(session, args, request_context=None) -> tuple[dict, list[ReferencedItem]]:
    """READ: curated symptom -> cause -> fix rows, each fix resolved to catalog products.
    Deterministic; the model never sees a row that is not in docs/curation/troubleshooting.csv."""
    symptom = (args.get("symptom") or "").strip()
    if not symptom:
        raise ValueError("symptom is required")
    limit = min(int(args.get("limit") or 5), 10)
    m = matcher()
    hits, kind = m.search(symptom, category=args.get("category") or None, limit=limit)
    entries, items = [], []
    for h in hits:
        products, owns = _troubleshoot_mod.resolve_products(session, h.entry, m.tables)
        gated = [roles.gate_product(p, roles.BUYER) for p in products]
        items.extend(ReferencedItem("product", p["id"], p["name"]) for p in products)
        entries.append({
            "entry_id": h.entry.entry_id,
            "category_id": h.entry.category_id,
            "symptom": h.entry.symptom,
            "likely_cause": h.entry.likely_cause,
            "check_or_fix": h.entry.check_or_fix,
            "confidence": h.entry.confidence,
            "fix_role": h.entry.fix_role,
            "buy_needed": h.entry.buy_needed,
            "lab_usually_owns_it": owns,
            "products": gated,
        })
    return {"entries": entries, "match": kind}, items
```

Add to `_HANDLERS`:

```python
    "troubleshoot": _troubleshoot,
```

Append to `TOOL_SCHEMAS` (after the `flag_sourcing_request` entry):

```python
    {"name": "troubleshoot",
     "description": "Look up Astor's curated troubleshooting guidance for a failed or unexpected "
                    "experimental result: 'no bands', 'no Ct', 'high background', 'cells died', "
                    "'low transfection efficiency'. Pass the customer's own words as `symptom`. "
                    "Returns symptom/cause/fix entries and, for each fix, the catalog products "
                    "that fill the needed role. Entries marked confidence='drafted' are commonly "
                    "reported causes, not yet confirmed by Astor's specialist; say so.",
     "input_schema": {"type": "object",
                      "properties": {
                          "symptom": {"type": "string",
                                      "description": "The failure in the customer's words, any language."},
                          "category": {"type": "string",
                                       "enum": ["western_blot", "rt_qpcr", "elisa", "cell_culture_transfection"],
                                       "description": "Omit if unsure; it is inferred from the symptom."},
                          "limit": {"type": "integer"}},
                      "required": ["symptom"]}},
```

- [ ] **Step 4: Run the tool tests**

Run: `.venv/bin/pytest tests/test_chat_tools.py -q`
Expected: all pass, including the pre-existing ones.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: no regressions.

---

### Task 5: Prompt paragraph and startup validation

**Files:**
- Modify: `src/astor/chat/agent.py` (SYSTEM string, GROUNDING SPECIFICS block)
- Modify: `src/astor/api/main.py` (`create_app`)
- Test: `tests/test_chat_agent.py` (append), `tests/api/test_startup_curation.py` (create)

**Interfaces:**
- Consumes: `astor.curation.tables()`, `astor.curation.CurationError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_agent.py`:

```python
def test_system_prompt_routes_failures_to_troubleshoot():
    assert "troubleshoot" in agent.SYSTEM
    assert "drafted" in agent.SYSTEM
    assert "what to buy" in agent.SYSTEM
```

Create `tests/api/test_startup_curation.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_chat_agent.py::test_system_prompt_routes_failures_to_troubleshoot tests/api/test_startup_curation.py -q`
Expected: the prompt test fails on the `"troubleshoot"` assertion; the bad-table test fails because `create_app` does not raise.

- [ ] **Step 3: Add the paragraph to `SYSTEM` in `src/astor/chat/agent.py`**

Insert as the last bullet of the GROUNDING SPECIFICS block, immediately before the `"\n\nSTYLE` line:

```python
    "- For a FAILED or unexpected result ('no bands', 'no Ct', 'high background', 'cells detached', "
    "'low transfection efficiency'), call troubleshoot with the customer's own words BEFORE "
    "answering. Lead with the most likely cause, say what to check, and end with what to buy "
    "when a fix needs an item Astor carries (the tool returns the products). If an entry is "
    "marked drafted, call it a commonly reported cause rather than Astor's confirmed guidance. "
    "If the tool returns no entries, answer from general knowledge and say Astor has no specific "
    "note on that case yet.\n\n"
```

The existing bullet before it ends with `"...Do not say 'no protocol exists'.\n\n"`; change that bullet's trailing `"\n\n"` to `"\n"` so the list stays contiguous, and let the new bullet carry the `"\n\n"` before STYLE.

- [ ] **Step 4: Add startup validation to `create_app` in `src/astor/api/main.py`**

Add to imports:

```python
from astor import curation
```

Add as the first statement of `create_app`, before the admin-token check:

```python
    # Mary's curation tables are validated here so a typo in a role or category
    # fails boot with the entry id, instead of reaching a shopper as an empty answer.
    curation.tables()
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_chat_agent.py tests/api/test_startup_curation.py -q`
Expected: pass.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: no regressions. The `tests/api/` suite calls `create_app` many times; the cached singleton keeps that cheap.

---

### Task 6: Gold set and retrieval-layer eval

**Files:**
- Create: `data/eval/troubleshooting_gold.csv`
- Create: `src/astor/eval/troubleshooting.py`
- Create: `scripts/run_troubleshooting_eval.py`
- Test: `tests/test_troubleshooting_eval.py`

**Interfaces:**
- Consumes: `troubleshoot.Matcher`, `astor.eval.assistant.Scenario`.
- Produces:
  - `troubleshooting.GoldCase(case_id, category_id, question, expected_entry_ids: tuple[str, ...], expected_fix_roles: tuple[str, ...], must_match: str, notes: str)`
  - `troubleshooting.load_gold(path) -> list[GoldCase]`
  - `troubleshooting.CaseResult(case, returned_ids: tuple[str, ...], hit: float, match: str)`
  - `troubleshooting.RetrievalReport(results, overall: float, per_category: dict[str, float], drafted_share: float)`
  - `troubleshooting.evaluate_retrieval(matcher, cases, *, limit=5) -> RetrievalReport`
  - `troubleshooting.Bars(min_overall=0.8, min_per_category=0.6)`
  - `troubleshooting.gate(report, bars=Bars()) -> GateResult` (reuses `astor.eval.gate.GateResult`)
  - `troubleshooting.to_scenarios(cases) -> list[Scenario]`
  - `troubleshooting.render(report) -> str`

- [ ] **Step 1: Write `data/eval/troubleshooting_gold.csv`**

```csv
case_id,category_id,question,expected_entry_ids,expected_fix_roles,must_match,notes
TG0001,western_blot,My western blot shows no bands at all. Primary is rabbit and I used an anti-mouse secondary.,T0001,secondary_antibody,anti-rabbit|secondary|二抗,classic host mismatch
TG0002,western_blot,完全没有条带，ECL 是去年开的,T0002,detection_substrate,ECL|substrate|显影,expired substrate
TG0003,western_blot,No bands and Ponceau shows nothing on the membrane,T0003,membrane,PVDF|membrane|nitrocellulose|膜,transfer failed
TG0004,western_blot,我做磷酸化蛋白的 WB 信号很弱，用的是脱脂奶粉封闭,T0005,blocking_agent,BSA|albumin,milk masks phospho
TG0005,western_blot,High background over the whole blot,T0006,blocking_agent,BSA|milk|block|封闭,blocking
TG0006,western_blot,I see multiple non-specific bands on my western,T0008,primary_antibody,antibody|抗体,primary titration
TG0007,western_blot,The band is at the wrong molecular weight compared to what I expected,T0009,protein_ladder,ladder|marker,ladder misread
TG0008,western_blot,泳道之间条带强弱没法比较，没有做上样对照,T0010,loading_control,GAPDH|actin|tubulin|上样,loading control
TG0009,western_blot,My loading control band overlaps with my target band,T0011,loading_control,GAPDH|actin|tubulin,different-size control
TG0010,western_blot,Bands are smiling and distorted,T0012,buffer_running,,lab owns running buffer; no product expected
TG0011,western_blot,不确定是抗体不行还是样本里没有目标蛋白,T0004,positive_control,lysate|positive|阳性,positive control
TG0012,rt_qpcr,My NTC wells show amplification,T0101,qpcr_master_mix,master mix|SYBR|qPCR,contamination
TG0013,rt_qpcr,-RT 对照有信号,T0102,dnase,DNase,genomic DNA
TG0014,rt_qpcr,No Ct values in any sample after RT-qPCR,T0103,rt_enzyme,reverse transcriptase|RT kit|cDNA|逆转录,RT failed
TG0015,rt_qpcr,One of my primer pairs never amplifies anything,T0104,primers,primer|引物,primer design
TG0016,rt_qpcr,所有样本 Ct 都在 36 以后,T0105,rna_extraction_kit,RNA|extraction|提取,inhibitors or low template
TG0017,rt_qpcr,The melt curve has two peaks,T0106,primers,primer|probe|TaqMan|引物,dimers
TG0018,rt_qpcr,My GAPDH Ct changes a lot between treated and untreated,T0107,reference_gene,reference|housekeeping|内参|ACTB|18S,unstable reference
TG0019,rt_qpcr,复孔之间 Ct 差异很大,T0108,pipette_tips,,lab owns tips; no product expected
TG0020,rt_qpcr,My fold changes make no biological sense,T0109,reference_gene,reference|housekeeping|内参,normalisation
TG0021,rt_qpcr,基因组 DNA 也能被我的引物扩增,T0110,primers,primer|引物,exon-spanning
TG0022,rt_qpcr,RNA yield from my tissue samples is very low,T0111,rna_extraction_kit,RNA|extraction|提取,yield
TG0023,elisa,No signal in any well including the standards,T0201,tmb_substrate,TMB|substrate|底物,substrate dead
TG0024,elisa,标准品正常但样本完全没有信号,T0202,sample_diluent,diluent|稀释,matrix effect
TG0025,elisa,High background in every well of my ELISA,T0203,wash_buffer,wash|PBST|洗涤,washing
TG0026,elisa,The blank wells read high,T0204,capture_detection_pair,antibody|pair|抗体,detection too concentrated
TG0027,elisa,我的标准曲线 R² 很低，不线性,T0205,standard_curve,standard|标准品,standard curve
TG0028,elisa,Sample readings are above the highest standard,T0206,sample_diluent,diluent|稀释,dilute
TG0029,elisa,阴性对照孔有信号,T0208,negative_control,negative|阴性|control,non-specific
TG0030,elisa,Sandwich ELISA gives no signal even though I know the protein is there,T0209,capture_detection_pair,pair|matched|capture|detection|抗体,matched pair
TG0031,elisa,Results are different every time I run a new plate,T0210,positive_control,positive|阳性|control,inter-plate
TG0032,elisa,显色太快整板都饱和了,T0211,stop_solution,stop|终止,saturation
TG0033,elisa,I have no way to subtract background because I did not run a blank,T0213,blank_zero_standard,,blank is diluent; may be lab-owned
TG0034,cell_culture_transfection,My transfection efficiency is very low in HEK293,T0301;T0302,transfection_reagent;plasmid_dna,transfection|plasmid|转染|质粒,two plausible causes
TG0035,cell_culture_transfection,转染以后细胞死了一大半,T0303,transfection_reagent,transfection|转染,toxicity
TG0036,cell_culture_transfection,Cells transfected fine by GFP but my construct shows no expression,T0304,plasmid_dna,plasmid|质粒,promoter or plasmid
TG0037,cell_culture_transfection,细胞长得很慢,T0305,culture_medium,medium|serum|FBS|培养基|血清,slow growth
TG0038,cell_culture_transfection,My adherent cells keep detaching from the plate,T0306,trypsin,trypsin|plate|胰酶|培养板,detaching
TG0039,cell_culture_transfection,培养基浑浊了，好像有污染,T0307,antibiotic,antibiotic|penicillin|streptomycin|青链霉素,contamination
TG0040,cell_culture_transfection,Medium is clear but cells look unhealthy and grow slowly,T0308,mycoplasma_test,mycoplasma|支原体,mycoplasma
TG0041,cell_culture_transfection,细胞形态和以前不一样了,T0309,cell_line,cell|细胞,morphology
TG0042,cell_culture_transfection,Medium turns yellow within a day,T0310,culture_medium,medium|培养基,overconfluent
TG0043,cell_culture_transfection,The transfection works in HeLa but not in my primary cells,T0312,transfection_reagent,transfection|electroporation|转染,hard-to-transfect
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_troubleshooting_eval.py`:

```python
"""Retrieval-layer eval for the troubleshooting table: no model, no network.

Pins the scoring so a green bench cannot be an artefact of how hits are counted."""
from __future__ import annotations

from pathlib import Path

from astor import curation
from astor.curation import troubleshoot
from astor.curation.loader import Category, CurationTables, Role, TroubleshootingEntry
from astor.eval import troubleshooting as ev

GOLD = Path(__file__).resolve().parents[1] / "data" / "eval" / "troubleshooting_gold.csv"


def _e(eid, cat, symptom, confidence="drafted"):
    return TroubleshootingEntry(eid, cat, symptom, "cause", "fix", None, "no", None,
                                confidence, "model", "", "", "")


def _tables():
    cats = {"western_blot": Category("western_blot", "WB"), "elisa": Category("elisa", "ELISA")}
    return CurationTables(cats, {}, [], [
        _e("T1", "western_blot", "no bands at all"),
        _e("T2", "western_blot", "high background", confidence="reviewed"),
        _e("T3", "elisa", "standard curve not linear"),
    ])


def _case(cid, cat, q, ids, roles=()):
    return ev.GoldCase(cid, cat, q, tuple(ids), tuple(roles), "", "")


def test_load_gold_parses_lists():
    cases = ev.load_gold(GOLD)
    assert len(cases) >= 40
    multi = next(c for c in cases if c.case_id == "TG0034")
    assert multi.expected_entry_ids == ("T0301", "T0302")
    assert multi.expected_fix_roles == ("transfection_reagent", "plasmid_dna")


def test_gold_references_only_real_entries_and_roles():
    t = curation.tables()
    ids = {e.entry_id for e in t.troubleshooting}
    for c in ev.load_gold(GOLD):
        assert set(c.expected_entry_ids) <= ids, c.case_id
        assert set(c.expected_fix_roles) <= set(t.roles), c.case_id
        assert c.category_id in t.categories, c.case_id


def test_hit_is_fraction_of_expected_ids_returned():
    m = troubleshoot.Matcher(_tables())
    cases = [
        _case("a", "western_blot", "no bands at all", ["T1"]),
        _case("b", "western_blot", "no bands at all", ["T1", "T2"]),
        _case("c", "elisa", "nothing matches here zz", ["T3"]),
    ]
    r = ev.evaluate_retrieval(m, cases, limit=5)
    by = {x.case.case_id: x for x in r.results}
    assert by["a"].hit == 1.0
    assert by["b"].hit == 0.5
    assert by["c"].hit == 0.0 and by["c"].returned_ids == ()
    assert r.per_category["western_blot"] == 0.75
    assert r.per_category["elisa"] == 0.0
    assert r.overall == (1.0 + 0.5 + 0.0) / 3


def test_drafted_share_counts_returned_rows():
    m = troubleshoot.Matcher(_tables())
    r = ev.evaluate_retrieval(m, [_case("a", "western_blot", "no bands high background", ["T1"])], limit=5)
    # returned T1 (drafted) and T2 (reviewed)
    assert r.drafted_share == 0.5


def test_gate_bars():
    rep = ev.RetrievalReport(results=[], overall=0.85, per_category={"western_blot": 0.9, "elisa": 0.5},
                             drafted_share=1.0)
    g = ev.gate(rep)
    assert g.passed is False and any("elisa" in r for r in g.reasons)
    rep2 = ev.RetrievalReport(results=[], overall=0.85, per_category={"western_blot": 0.9}, drafted_share=1.0)
    assert ev.gate(rep2).passed is True


def test_to_scenarios_skips_cases_without_must_match():
    cases = [ev.GoldCase("x", "western_blot", "q", ("T1",), (), "anti-rabbit", ""),
             ev.GoldCase("y", "western_blot", "q2", ("T2",), (), "", "")]
    s = ev.to_scenarios(cases)
    assert len(s) == 1 and s[0].question == "q" and s[0].must_match == "anti-rabbit"


def test_render_mentions_overall_and_categories():
    m = troubleshoot.Matcher(_tables())
    r = ev.evaluate_retrieval(m, [_case("a", "western_blot", "no bands at all", ["T1"])], limit=5)
    out = ev.render(r)
    assert "overall" in out and "western_blot" in out
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest tests/test_troubleshooting_eval.py -q`
Expected: ImportError on `astor.eval.troubleshooting`.

- [ ] **Step 4: Write `src/astor/eval/troubleshooting.py`**

```python
"""Does the troubleshooting tool surface the right rows?

Retrieval layer only: no model, no network. Each gold case names the entry ids a
correct lookup must include; the score is the fraction returned in the top N.
The assistant layer (does the reply reference the right product chips) reuses
astor.eval.assistant scenarios via to_scenarios().
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from astor.curation.troubleshoot import Matcher
from astor.eval.assistant import PRODUCT, Scenario
from astor.eval.gate import GateResult


@dataclass(frozen=True)
class GoldCase:
    case_id: str
    category_id: str
    question: str
    expected_entry_ids: tuple[str, ...]
    expected_fix_roles: tuple[str, ...]
    must_match: str
    notes: str


def _split(v: str | None) -> tuple[str, ...]:
    return tuple(x.strip() for x in (v or "").split(";") if x.strip())


def load_gold(path: Path) -> list[GoldCase]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return [
            GoldCase(r["case_id"].strip(), r["category_id"].strip(), r["question"].strip(),
                     _split(r.get("expected_entry_ids")), _split(r.get("expected_fix_roles")),
                     (r.get("must_match") or "").strip(), (r.get("notes") or "").strip())
            for r in csv.DictReader(f) if (r.get("case_id") or "").strip()
        ]


@dataclass(frozen=True)
class CaseResult:
    case: GoldCase
    returned_ids: tuple[str, ...]
    hit: float
    match: str
    drafted_returned: int = 0


@dataclass
class RetrievalReport:
    results: list[CaseResult]
    overall: float
    per_category: dict[str, float]
    drafted_share: float


def evaluate_retrieval(matcher: Matcher, cases: list[GoldCase], *, limit: int = 5) -> RetrievalReport:
    results: list[CaseResult] = []
    for c in cases:
        hits, kind = matcher.search(c.question, category=None, limit=limit)
        ids = tuple(h.entry.entry_id for h in hits)
        expected = set(c.expected_entry_ids)
        hit = (len(expected & set(ids)) / len(expected)) if expected else 0.0
        drafted = sum(1 for h in hits if h.entry.confidence == "drafted")
        results.append(CaseResult(c, ids, hit, kind, drafted))
    overall = sum(r.hit for r in results) / len(results) if results else 0.0
    per_cat: dict[str, list[float]] = {}
    for r in results:
        per_cat.setdefault(r.case.category_id, []).append(r.hit)
    per_category = {k: sum(v) / len(v) for k, v in per_cat.items()}
    returned = sum(len(r.returned_ids) for r in results)
    drafted_share = (sum(r.drafted_returned for r in results) / returned) if returned else 0.0
    return RetrievalReport(results, overall, per_category, drafted_share)


@dataclass
class Bars:
    min_overall: float = 0.8
    min_per_category: float = 0.6


def gate(report: RetrievalReport, bars: Bars = Bars()) -> GateResult:
    reasons: list[str] = []
    if report.overall < bars.min_overall:
        reasons.append(f"overall hit rate {report.overall:.3f} < {bars.min_overall}")
    for cid, rate in sorted(report.per_category.items()):
        if rate < bars.min_per_category:
            reasons.append(f"{cid} hit rate {rate:.3f} < {bars.min_per_category}")
    return GateResult(passed=not reasons, reasons=reasons)


def to_scenarios(cases: list[GoldCase]) -> list[Scenario]:
    """Assistant-layer scenarios for cases that name a product to expect."""
    return [Scenario(question=c.question, must_match=c.must_match, expect=PRODUCT, notes=c.case_id)
            for c in cases if c.must_match]


def render(report: RetrievalReport) -> str:
    lines = [f"overall hit rate: {report.overall:.3f}   drafted share of returned rows: {report.drafted_share:.2f}"]
    for cid, rate in sorted(report.per_category.items()):
        lines.append(f"  {cid:28s} {rate:.3f}")
    misses = [r for r in report.results if r.hit < 1.0]
    if misses:
        lines.append("misses:")
        for r in misses:
            lines.append(f"  {r.case.case_id} [{r.match}] expected {list(r.case.expected_entry_ids)} "
                         f"got {list(r.returned_ids)} :: {r.case.question}")
    return "\n".join(lines)
```

- [ ] **Step 5: Write `scripts/run_troubleshooting_eval.py`**

```python
"""Retrieval-layer eval of the troubleshoot tool against the real curation tables.

    .venv/bin/python scripts/run_troubleshooting_eval.py            # keyword + dev embedder
    .venv/bin/python scripts/run_troubleshooting_eval.py --no-embed # keyword only

Exit 1 when the gate fails. The assistant layer (live model) is run through
scripts/run_assistant_eval.py with the scenarios this script can export:

    .venv/bin/python scripts/run_troubleshooting_eval.py --export-scenarios /tmp/ts_scenarios.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from astor import curation
from astor.curation.troubleshoot import Matcher
from astor.eval import troubleshooting as ev

GOLD = Path(__file__).resolve().parents[1] / "data" / "eval" / "troubleshooting_gold.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--export-scenarios", type=Path)
    args = ap.parse_args()

    embedder = None
    if not args.no_embed:
        from astor.catalog.embeddings import get_embedder
        embedder = get_embedder()
    matcher = Matcher(curation.tables(), embedder=embedder)
    cases = ev.load_gold(GOLD)

    if args.export_scenarios:
        with args.export_scenarios.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["question", "expect", "must_match", "notes"])
            for s in ev.to_scenarios(cases):
                w.writerow([s.question, s.expect, s.must_match, s.notes])
        print(f"wrote {args.export_scenarios}")

    report = ev.evaluate_retrieval(matcher, cases, limit=args.limit)
    print(ev.render(report))
    g = ev.gate(report)
    print("GATE:", "PASS" if g.passed else "FAIL", *g.reasons, sep="\n  " if g.reasons else " ")
    return 0 if g.passed else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests and the eval**

Run: `.venv/bin/pytest tests/test_troubleshooting_eval.py -q && .venv/bin/python scripts/run_troubleshooting_eval.py --no-embed`
Expected: tests pass; the eval prints per-category hit rates and `GATE: PASS`. If the gate fails, read the `misses:` block. The two usual fixes are adding the missing customer phrasing to the row's `symptom` cell in `troubleshooting.csv`, or adding a hint word to `CATEGORY_HINTS`. Do not lower the bars.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: no regressions.

---

### Task 7: Curation guide section for the fifth table

**Files:**
- Modify: `docs/curation/01-每张表怎么填.md` (append a section)

- [ ] **Step 1: Append the section**

Append at the end of the file:

```markdown

---

## 表 5：`troubleshooting.csv` —— 实验失败时"先查什么、再买什么"

**作用：** 客户说"我的 Western blot 没有条带"时，助手用这张表回答：最可能的原因是什么、
先检查什么、如果要买东西买哪一类。每一行 = 一个症状 + 一个原因。同一症状有三个原因就写三行。

**与 checklist 的关系：** 大多数失败都是清单里某一项缺了或配错了。这类行请在
`checklist_role_ref` 里指向对应的清单行（格式 `品类:角色`，如 `western_blot:secondary_antibody`）。

**当前状态：** 工程团队先起草了约 50 行（`confidence = drafted`），请您逐行审核。
审核无误就把 `confidence` 改成 `reviewed`，并填上 `reviewed_by` 和 `reviewed_on`。
不对的行直接删；缺的行直接加（`source` 填 `mary`）。**在您改成 reviewed 之前，助手会把这一行
说成"常见的可能原因"，而不是"Astor 的建议"。**

| 列 | 填什么 | 示例 |
|---|---|---|
| `entry_id` | 编号，`T` + 四位数字，工程团队分配，不要改 | `T0001` |
| `category_id` | 品类代号，须在 `categories.csv` 里 | `western_blot` |
| `symptom` | 客户会怎么描述这个现象，中英文用 ` / ` 隔开 | `完全没有条带 / no bands at all` |
| `likely_cause` | 这一行对应的**一个**原因 | `二抗宿主与一抗不匹配 / secondary does not match primary host` |
| `check_or_fix` | 具体先查什么、怎么改，越具体越好 | `确认二抗是 anti-[一抗宿主]；不匹配则更换` |
| `fix_role` | 修复需要的物品角色，须在 `roles.csv` 里；纯操作问题留空 | `secondary_antibody` |
| `buy_needed` | 通常要买东西吗？`yes` / `no` / `sometimes` | `yes` |
| `checklist_role_ref` | 对应的清单行 `品类:角色`；无则留空 | `western_blot:secondary_antibody` |
| `confidence` | `drafted`（未审）或 `reviewed`（已审） | `reviewed` |
| `source` | 内容来源：`checklist` / `mary` / `model` | `mary` |
| `reviewed_by` / `reviewed_on` | 审核人、日期（`reviewed` 时必填，`drafted` 时留空） | `Mary` / `2026-09-20` |
| `notes` | 备注 | |

> `roles.csv` 也新增了 27 个角色（备注写着 `drafted 2026-09-15 for troubleshooting; needs review`），
> 主要是 qPCR、ELISA、细胞培养会用到的物品。请顺手核对 `lab_usually_owns_it` 一列：
> 标 `yes` 的物品助手不会推荐采购。
```

- [ ] **Step 2: Run the check script one last time**

Run: `.venv/bin/python scripts/check_troubleshooting.py`
Expected: same counts as Task 2; the doc change does not affect loading.

---

## Self-review

**Spec coverage.**
- §4 table and validation rules → Task 1 (loader, rules), Task 2 (content). ✔
- §5 workflow → Task 2 content authored with `source`/`confidence`; Task 7 tells Mary how to review; check script reports drafted share. Seed script became a lint script (deviation 3). ✔
- §6 loader and startup → Task 1 (loader), Task 5 (startup call). Singleton instead of app state (deviation 1). ✔
- §7 tool, behaviour, prompt → Task 3 (matching, resolve), Task 4 (handler, schema), Task 5 (prompt). Row embeddings at Matcher build, keyword-only on embedder failure. ✔
- §8 eval, both layers → Task 6: retrieval layer with bars 0.8/0.6, assistant layer via `to_scenarios` and the export flag. ✔
- §9 files → all present; `scripts/seed_troubleshooting.py` renamed per deviation 3. ✔
- §10 tests → loader rules (Task 1), tool cases (Tasks 3–4), prompt (Task 5), eval (Task 6), startup (Task 5). ✔
- §11 risks → Chinese cases in gold (11 of 43), drafted share reported, unknown role refused at startup, `_search_term` documented as approximate. ✔

**Placeholder scan.** None.

**Type consistency.** `Matcher.search` returns `(list[Hit], str)` in Task 3 and is consumed that way in Tasks 4 and 6. `resolve_products` returns `(list[dict], str | None)` in Task 3 and is unpacked as `products, owns` in Task 4. `GateResult` comes from `astor.eval.gate` in Task 6. `tools._matcher` is the monkeypatch target in Task 4 tests and is defined as a module global in Task 4 step 3. ✔
