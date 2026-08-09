"""Seed queries per curated category (docs/curation/categories.csv).

Synonyms are hand-written because the category_id is a slug, not a search term,
and one phrasing misses protocols filed under another ('immunoblot' vs 'western
blot'). Categories without an explicit entry fall back to a name-derived query
so a newly-added row still harvests, just less precisely."""
from __future__ import annotations

import csv as _csv
from dataclasses import dataclass
from pathlib import Path

SEED_SYNONYMS: dict[str, list[str]] = {
    "western_blot": ["western blot", "immunoblot", "protein immunoblotting"],
    "rt_qpcr": ["RT-qPCR", "quantitative real-time PCR", "real-time reverse transcription PCR"],
    "elisa": ["ELISA", "enzyme-linked immunosorbent assay", "sandwich ELISA"],
    "cell_culture_transfection": ["cell culture transfection", "mammalian transfection", "lipofection"],
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
