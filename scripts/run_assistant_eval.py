"""Measure how often the storefront assistant actually finds what Astor carries.

Each scenario is asked N times, because the failure this guards against is
probabilistic: the model picks the search phrasing, and a brittle retrieval rule
turns some phrasings into "we don't carry that". See `astor.eval.assistant`.

Backends
    fixture (default)  the real agent + real search rules over the frozen
                       catalog in data/eval/catalog_sample.csv. No database.
                       Only the model varies, so a drop in pass rate is a
                       retrieval regression and nothing else.
    --db               the real agent against your Postgres catalog.
    --live             the deployed assistant, through a signed Shopify App
                       Proxy request. Confirms a deploy, not a code change.

Usage
    python -m scripts.run_assistant_eval --runs 8
    python -m scripts.run_assistant_eval --runs 3 --live

Needs ANTHROPIC_API_KEY (and, for --live, SHOPIFY_APP_PROXY_SECRET) in .env.
Costs one chat turn per run: scenarios x runs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from astor.api import repo
from astor.catalog import search
from astor.chat import agent
from astor.config import settings
from astor.eval import assistant

_ROOT = Path(__file__).resolve().parent.parent
_SCENARIOS = _ROOT / "data" / "eval" / "assistant_scenarios.csv"
_CATALOG = _ROOT / "data" / "eval" / "catalog_sample.csv"


# --------------------------------------------------------------------------- #
# Fixture backend: the real agent, the real search rules, a frozen catalog.
# --------------------------------------------------------------------------- #
class FixtureCatalog:
    """Stands in for `repo`, applying the same `search` module production uses."""

    def __init__(self, path: Path, *, legacy: bool = False) -> None:
        with path.open() as f:
            self.rows = list(csv.DictReader(f))
        self.queries: list[str] = []
        self.legacy = legacy

    def _haystack(self, row: dict) -> str:
        return f"{row['name']} {row['brand']} {row['mpn']}"

    def _legacy_list(self, q, page_size):
        """The retrieval rule as it shipped: one whole-phrase ILIKE, ordered by
        insertion. Kept so the harness can be shown failing on the bug it exists
        to catch — a gate nobody has watched fail is not a gate."""
        hits = [r for r in self.rows if (q or "").lower() in self._haystack(r).lower()]
        return hits[:page_size], len(hits)

    def list_products(self, session, q, category, page, page_size):
        self.queries.append(q)
        if self.legacy:
            rows, total = self._legacy_list(q, page_size)
            return [{"id": r["id"], "astor_sku": "ASR-FIXTURE", "name": r["name"],
                     "category": r["category"], "brand": r["brand"], "mpn": r["mpn"],
                     "region": None, "offer_count": 1, "best_landed": None}
                    for r in rows], total
        toks = search.tokens(q)
        if not toks:
            return [], 0
        df = {t: sum(1 for r in self.rows if search.df_match(t, self._haystack(r)))
              for t in toks}
        rows, total = search.page(
            self.rows, q, page=page, page_size=page_size, df=df,
            haystack=self._haystack, name_of=lambda r: r["name"],
        )
        return [{"id": r["id"], "astor_sku": "ASR-FIXTURE", "name": r["name"],
                 "category": r["category"], "brand": r["brand"], "mpn": r["mpn"],
                 "region": None, "offer_count": 1, "best_landed": None}
                for r in rows], total

    def install(self, monkey: list) -> None:
        """Point the chat tools at this catalog; protocol lookups return empty so
        a scenario measures product retrieval only."""
        monkey.append((repo, "list_products", repo.list_products))
        repo.list_products = self.list_products
        repo.list_protocols = lambda s, q, p, ps: ([], 0)
        repo.protocols_by_material = lambda s, m, limit=10: {"total": 0, "protocols": []}
        repo.protocol_source_uris = lambda s, ids: {}


def run_fixture(scenario, runs: int, *, legacy: bool = False) -> assistant.Outcome:
    outcome = assistant.Outcome(scenario=scenario)
    for _ in range(runs):
        catalog = FixtureCatalog(_CATALOG, legacy=legacy)
        catalog.install([])
        reply = agent.run_chat(None, [{"role": "user", "content": scenario.question}])
        names = [i.name for i in reply.items]
        outcome.runs += 1
        outcome.passes += int(assistant.judge(scenario, names))
        outcome.queries.append(list(catalog.queries))
    return outcome


# --------------------------------------------------------------------------- #
# Database backend
# --------------------------------------------------------------------------- #
def run_db(scenario, runs: int) -> assistant.Outcome:
    from astor.db.base import session_scope

    outcome = assistant.Outcome(scenario=scenario)
    for _ in range(runs):
        with session_scope() as session:
            reply = agent.run_chat(session, [{"role": "user", "content": scenario.question}])
        outcome.runs += 1
        outcome.passes += int(assistant.judge(scenario, [i.name for i in reply.items]))
    return outcome


# --------------------------------------------------------------------------- #
# Live backend: the deployed assistant via a signed App Proxy request
# --------------------------------------------------------------------------- #
def _signed_url(base: str, path: str, params: dict, secret: str) -> str:
    message = "".join(sorted(f"{k}={v}" for k, v in params.items()))
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{base}{path}?" + urllib.parse.urlencode({**params, "signature": signature})


def run_live(scenario, runs: int, *, base: str, shop: str) -> assistant.Outcome:
    secret = settings.shopify_app_proxy_secret or settings.shopify_client_secret
    if not secret:
        raise SystemExit("--live needs SHOPIFY_APP_PROXY_SECRET (or client secret) in .env")
    outcome = assistant.Outcome(scenario=scenario)
    for _ in range(runs):
        params = {"shop": shop, "path_prefix": "/apps/astor",
                  "timestamp": str(int(time.time()))}
        body = json.dumps({"messages": [{"role": "user", "content": scenario.question}]}).encode()
        request = urllib.request.Request(
            _signed_url(base, "/proxy/chat", params, secret), data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode())
        outcome.runs += 1
        outcome.passes += int(assistant.judge(scenario, [i["name"] for i in payload["items"]]))
        time.sleep(3)      # stay under the per-shop chat rate limit
    return outcome


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="repetitions per scenario")
    ap.add_argument("--scenarios", type=Path, default=_SCENARIOS)
    ap.add_argument("--min-pass-rate", type=float, default=1.0)
    ap.add_argument("--db", action="store_true", help="use the Postgres catalog")
    ap.add_argument("--live", action="store_true", help="use the deployed assistant")
    ap.add_argument("--base", default="https://astor-engine.onrender.com")
    # The widget is served from the password-locked dev store; the live shop
    # (f19702-2.myshopify.com) has no Astor app proxy configured yet.
    ap.add_argument("--shop", default="astor-dev.myshopify.com")
    ap.add_argument("--legacy-search", action="store_true",
                    help="use the pre-fix whole-phrase ILIKE rule (proves the gate bites)")
    ap.add_argument("--show-queries", action="store_true",
                    help="print the search strings the model issued (fixture backend)")
    args = ap.parse_args()

    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set — the eval drives the real assistant.")

    scenarios = assistant.load_scenarios(args.scenarios)
    backend = "live" if args.live else ("db" if args.db else
                                       ("fixture:legacy-search" if args.legacy_search
                                        else "fixture"))
    print(f"backend={backend}  scenarios={len(scenarios)}  runs={args.runs}  "
          f"turns={len(scenarios) * args.runs}\n")

    outcomes = []
    for scenario in scenarios:
        if args.live:
            outcome = run_live(scenario, args.runs, base=args.base, shop=args.shop)
        elif args.db:
            outcome = run_db(scenario, args.runs)
        else:
            outcome = run_fixture(scenario, args.runs, legacy=args.legacy_search)
        outcomes.append(outcome)
        print(f"  {outcome.passes}/{outcome.runs}  {scenario.question[:60]}")
        if args.show_queries and outcome.queries:
            for i, queries in enumerate(outcome.queries, 1):
                print(f"        run {i}: {queries}")

    bars = assistant.GateBars(min_pass_rate=args.min_pass_rate)
    print("\n" + assistant.render(outcomes, bars))
    sys.exit(0 if assistant.gate(outcomes, bars).passed else 1)


if __name__ == "__main__":
    main()
