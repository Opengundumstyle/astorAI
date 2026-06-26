"""Run the equivalence-matching accuracy harness.

Usage:
    python -m scripts.run_eval --products data/eval/products.csv --gold data/eval/gold.csv

Set EMBEDDINGS_PROVIDER=voyage (+ VOYAGE_API_KEY) in .env for meaningful numbers;
the default DevEmbedder is offline and NOT semantically meaningful.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from astor.config import settings
from astor.eval.accuracy import run
from astor.catalog.embeddings import get_embedder


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", required=True, type=Path)
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--exact", type=float, default=settings.equiv_exact_threshold)
    ap.add_argument("--substitute", type=float, default=settings.equiv_substitute_threshold)
    args = ap.parse_args()

    embedder = get_embedder()
    print(f"embedder = {type(embedder).__name__}  (provider={settings.embeddings_provider})")
    if type(embedder).__name__ == "DevEmbedder":
        print("WARNING: DevEmbedder is offline/deterministic - numbers are NOT meaningful.\n")
    report = run(
        args.products, args.gold, embedder,
        exact_threshold=args.exact, substitute_threshold=args.substitute,
    )
    print(report.render())


if __name__ == "__main__":
    main()
