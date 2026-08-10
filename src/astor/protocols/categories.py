"""Seed queries per curated category (docs/curation/categories.csv).

Synonyms are hand-written because the category_id is a slug, not a search term,
and one phrasing misses protocols filed under another ('immunoblot' vs 'western
blot'). Categories without an explicit entry fall back to a name-derived query
so a newly-added row still harvests, just less precisely."""
from __future__ import annotations

import csv as _csv
from dataclasses import dataclass
from pathlib import Path

# VERIFIED 2026-08-09 against live v3 total_results: the API's `key` search matches
# SHORT terms well and returns ~0 for long multi-word phrases. Counts observed:
# "western blot"=223, "immunoblot"=43, "elisa"=427, "transfection"=201,
# "cell culture"=366, "rt-qpcr"=17 — while "quantitative real-time PCR",
# "sandwich ELISA", "cell culture transfection" etc. all returned 0. Keep terms
# short; we re-rank by review downstream so broad terms are fine.
SEED_SYNONYMS: dict[str, list[str]] = {
    # Launch four (verified against live counts, 2026-08-09).
    "western_blot": ["western blot", "immunoblot"],
    "rt_qpcr": ["rt-qpcr", "qpcr", "real-time PCR"],
    "elisa": ["elisa"],
    "cell_culture_transfection": ["transfection", "cell culture"],
    # Coverage expansion (2026-08-10), sized to the live product distribution:
    # 9,207 recombinant proteins + inhibitors dominate the catalog. Terms kept
    # short (2 words max) because the v3 key search returns ~0 for longer phrases.
    "protein_purification": ["protein purification", "affinity purification", "affinity chromatography"],
    "immunoprecipitation": ["immunoprecipitation", "co-immunoprecipitation", "chromatin immunoprecipitation"],
    "enzyme_inhibitor_assay": ["enzyme assay", "enzyme activity", "kinase assay"],
    "nucleic_acid_extraction": ["DNA extraction", "RNA extraction", "plasmid extraction"],
    "cloning_protein_expression": ["molecular cloning", "protein expression", "gibson assembly"],
}


@dataclass(frozen=True)
class CategorySeed:
    category_id: str
    category_name: str
    queries: list[str]


def _fallback_query(category_name: str) -> str:
    # "Western blot / 蛋白免疫印迹" -> "Western blot"
    return category_name.split("/")[0].strip()


def load_categories(csv_path: str | Path) -> list[CategorySeed]:
    seeds: list[CategorySeed] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in _csv.DictReader(fh):
            cid = (row.get("category_id") or "").strip()
            if not cid:
                continue
            name = (row.get("category_name") or "").strip()
            queries = SEED_SYNONYMS.get(cid) or [_fallback_query(name) or cid]
            seeds.append(CategorySeed(cid, name, list(queries)))
    return seeds
