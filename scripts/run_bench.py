"""Run the storefront assistant benchmark against the deployed assistant.

WHAT THIS MEASURES
    The system a shopper actually reaches: the production engine, the production
    prompt, the production catalog, through a signed Shopify App Proxy request.
    A red cell therefore means "the storefront is wrong", not "the code is
    wrong" — diagnosing one means re-running that probe against the database.

BEFORE YOU RUN
    python -m scripts.check_bench_ground_truth      # probes still match the catalog

    ANTHROPIC_API_KEY               not needed here; the deployed engine holds it
    SHOPIFY_APP_PROXY_SECRET        signs the proxy request
    ADMIN_TOKEN                     reads /api/sourcing-requests, the only way to
                                    see whether a consent probe wrote a row

COST
    256 turns at the 20/min per-shop limit: 30-40 minutes of wall clock.

TWO PROBES WRITE TO PRODUCTION
    M03 and M05 can create rows in `sourcing_requests`. Both embed the tag
    `astor-bench-probe`. After the run this script prints a DELETE scoped to that
    tag for an operator to run; it never deletes anything itself.

    python -m scripts.run_bench
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from astor.config import settings
from astor.eval import dimensions, probes, report
from scripts.run_assistant_eval import _signed_url

_ROOT = Path(__file__).resolve().parent.parent
_CORPUS = _ROOT / "data" / "eval" / "bench_probes.yaml"

# Fallback if the local database is not reachable. These are the 17 brands in the
# catalog as of 2026-09-06; `--brands-from-db` re-reads them.
_BRANDS = ["Astor Scientific", "AstorScientific", "TribioScience", "GenDEPOT",
           "Vazyme", "SARSTED", "Biologix", "NEST Scientific", "NICHIRYO",
           "FireGene", "3helix", "Southwest Science", "Corning", "Nordic",
           "Invitrogen", "Yeasen", "GenScript"]

_PLACEHOLDER = re.compile(r"\{\{items\[(\d+)\]\.(name|id)\}\}")


@dataclass(frozen=True)
class TurnResult:
    reply: str
    item_names: list[str]


def resolve(template: str, items: list[dict]) -> str:
    """Fill `{{items[0].name}}` / `{{items[0].id}}` from the previous turn.

    Raises IndexError rather than substituting an empty string: a card-click
    probe that silently asks about nothing would score as a pass.
    """
    def substitute(match: re.Match) -> str:
        return str(items[int(match.group(1))][match.group(2)])

    return _PLACEHOLDER.sub(substitute, template)


def play(probe, post) -> list[TurnResult]:
    """Run one conversation. `post(messages) -> {"reply": str, "items": [...]}`."""
    history: list[dict] = []
    turns: list[TurnResult] = []
    last_items: list[dict] = []
    for template in probe.turns:
        history.append({"role": "user", "content": resolve(template, last_items)})
        payload = post(history)
        last_items = payload.get("items") or []
        history.append({"role": "assistant", "content": payload["reply"]})
        turns.append(TurnResult(payload["reply"], [i["name"] for i in last_items]))
    return turns


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def _proxy_post(base: str, shop: str, secret: str):
    def post(messages: list[dict]) -> dict:
        params = {"shop": shop, "path_prefix": "/apps/astor",
                  "timestamp": str(int(time.time()))}
        body = json.dumps({"messages": messages}).encode()
        request = urllib.request.Request(
            _signed_url(base, "/proxy/chat", params, secret), data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode())
    return post


def _tagged_sourcing_rows(base: str, admin_token: str) -> int:
    """How many `sourcing_requests` rows carry the probe tag. The endpoint is
    behind require_admin_token (api/main.py:50)."""
    request = urllib.request.Request(
        f"{base}/api/sourcing-requests?limit=200", headers={"X-Admin-Token": admin_token})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode())
    return sum(1 for row in payload["items"]
               if probes.PROBE_TAG in json.dumps(row).lower())


def _brands_from_db() -> list[str]:
    from sqlalchemy import text

    from astor.db.base import session_scope
    with session_scope() as session:
        return [r[0] for r in session.execute(
            text("select distinct brand from products "
                 "where brand is not null and brand <> ''")).all()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probes", type=Path, default=_CORPUS)
    ap.add_argument("--base", default="https://astor-engine.onrender.com")
    ap.add_argument("--shop", default="astor-dev.myshopify.com")
    ap.add_argument("--runs", type=int, default=0,
                    help="override every probe's run count (0 = use the probe's own)")
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--sleep", type=float, default=3.0,
                    help="seconds between turns; the shop limit is 20/min")
    ap.add_argument("--brands-from-db", action="store_true",
                    help="read the denylist from the local catalog instead of the literal")
    ap.add_argument("--out", type=Path, default=None, help="write the report here")
    args = ap.parse_args()

    secret = settings.shopify_app_proxy_secret or settings.shopify_client_secret
    if not secret:
        raise SystemExit("needs SHOPIFY_APP_PROXY_SECRET (or client secret) in .env")

    corpus = probes.load_probes(args.probes)
    if args.only:
        wanted = {p.strip() for p in args.only.split(",")}
        corpus = [p for p in corpus if p.id in wanted]

    denylist = dimensions.build_denylist(
        _brands_from_db() if args.brands_from_db else _BRANDS)
    post = _proxy_post(args.base, args.shop, secret)

    # Consent probes need a before/after read of production's sourcing rows.
    # Note M03 (consent GIVEN) has no must_not_flag and so scores no D4C cell:
    # there is no per-turn signal for "it flagged at the right moment". It is
    # observed instead through the end-of-run row count printed below.
    needs_consent = any(p.must_not_flag or p.id == "M03" for p in corpus)
    admin_token = settings.admin_token
    if needs_consent and not admin_token:
        print("! ADMIN_TOKEN unset — consent (D4C) cells will be skipped.\n")
    before = (_tagged_sourcing_rows(args.base, admin_token)
              if needs_consent and admin_token else 0)

    total_turns = sum(len(p.turns) * (args.runs or p.runs) for p in corpus)
    print(f"probes={len(corpus)}  turns={total_turns}  shop={args.shop}  base={args.base}\n")

    results: list[tuple[str, str, bool]] = []
    transcripts: list[dict] = []
    for probe in corpus:
        for run in range(args.runs or probe.runs):
            turns = play(probe, post)
            flagged = False
            if probe.must_not_flag and admin_token:
                flagged = _tagged_sourcing_rows(args.base, admin_token) > before
            results += dimensions.score_run(
                probe, [t.reply for t in turns], [t.item_names for t in turns],
                denylist=denylist, flagged=flagged)
            transcripts.append({
                "probe": probe.id, "row": probe.row, "run": run + 1,
                "turns": [{"ask": q, "reply": t.reply, "items": t.item_names}
                          for q, t in zip(probe.turns, turns)],
            })
            time.sleep(args.sleep)
        print(f"  {probe.id:<6} {probe.turns[0][:56]}")

    cells = report.aggregate(results)
    scorecard = report.render_scorecard(cells, calibrated=False)
    print("\n" + scorecard)

    if needs_consent and admin_token:
        after = _tagged_sourcing_rows(args.base, admin_token)
        if after > before:
            print(f"\n{after - before} tagged sourcing row(s) were written to production. "
                  "Clean up with:\n"
                  "  DELETE FROM sourcing_requests\n"
                  f"   WHERE requested_item ILIKE '%{probes.PROBE_TAG}%'\n"
                  f"      OR context ILIKE '%{probes.PROBE_TAG}%';")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            scorecard + "\n\n## Transcripts\n\n"
            + json.dumps(transcripts, indent=2))
        print(f"\nreport written to {args.out}")

    sys.exit(0 if not report.failing(cells) else 1)


if __name__ == "__main__":
    main()
