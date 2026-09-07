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
import urllib.error
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
    seconds: float = 0.0


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
        started = time.monotonic()
        payload = post(history)
        elapsed = time.monotonic() - started
        last_items = payload.get("items") or []
        history.append({"role": "assistant", "content": payload["reply"]})
        turns.append(TurnResult(payload["reply"], [i["name"] for i in last_items], elapsed))
    return turns


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
# A 429 or a provider blip during a 256-turn run must cost one turn, not the
# whole run. 4xx other than 429 is a real error and is not retried.
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


def _post_with_retry(send, *, attempts: int = 3, backoff: float = 5.0):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return send()
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(backoff * (2 ** attempt))
    raise last


def _proxy_post(base: str, shop: str, secret: str):
    def post(messages: list[dict]) -> dict:
        def send() -> dict:
            params = {"shop": shop, "path_prefix": "/apps/astor",
                      "timestamp": str(int(time.time()))}
            body = json.dumps({"messages": messages}).encode()
            request = urllib.request.Request(
                _signed_url(base, "/proxy/chat", params, secret), data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode())
        return _post_with_retry(send)
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


def drop_unverifiable_consent(results: list[tuple[str, str, bool]],
                              *, admin_token: str | None) -> list[tuple[str, str, bool]]:
    """Remove D4C rows when we cannot observe production's sourcing table.

    score_run emits a D4C row for every must_not_flag probe, and `flagged`
    defaults to False, so without an admin token those rows would score as
    passing. A cell absent from the scorecard is honest; a cell that reads
    "ok" because nothing was checked is not.
    """
    if admin_token:
        return results
    return [r for r in results if r[1] != "D4C"]


def judge_transcripts(transcripts: list[dict], corpus: list, *, judge_fn=None
                      ) -> list[tuple[str, str, bool]]:
    """Second pass: grade D5 on collected transcripts.

    Separate from the run so a judge failure cannot abort thirty minutes of
    collection, and so tightened rubrics can be re-judged without paying for the
    turns again.
    """
    from astor.eval import judge as judge_module

    judge_fn = judge_fn or judge_module.judge_science
    by_id = {p.id: p for p in corpus}
    results: list[tuple[str, str, bool]] = []
    for transcript in transcripts:
        probe = by_id.get(transcript["probe"])
        if probe is None or "D5" not in probe.dimensions or probe.rubric is None:
            continue
        final = transcript["turns"][-1]
        verdict = judge_fn(final["ask"], final["reply"], probe.rubric)
        results.append((probe.row, "D5", bool(verdict.passed)))
    return results


def backlog(transcripts: list[dict], denylist: list[str]) -> list[tuple[str, int]]:
    """D4A worklist: vendor tokens the assistant echoed out of names it was given.

    Counted across every turn, not just final ones — a leak on turn 1 is just as
    visible to the customer.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for transcript in transcripts:
        for turn in transcript["turns"]:
            for leak in dimensions.confidentiality_leaks(
                    turn["reply"], turn["items"], denylist,
                    carried=bool(turn["items"])):
                if leak.kind == "D4A":
                    counts[leak.token] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def label_worksheet(transcripts: list[dict], corpus: list, *, size: int = 20,
                    seed: int = 0) -> str:
    """A worksheet for a human to label, drawn from the transcripts the judge scored.

    Only D5 probes are eligible: kappa compares two raters on the SAME items, so a
    sample containing transcripts the judge never graded would not be comparable.
    Stratified and seeded by `calibration.sample_for_labelling`, so the set does not
    over-represent whichever row carries the most probes and a disputed kappa can be
    re-derived from the same transcripts.
    """
    from astor.eval import calibration

    by_id = {p.id: p for p in corpus}
    eligible = [t for t in transcripts
                if (probe := by_id.get(t["probe"])) is not None
                and "D5" in probe.dimensions and probe.rubric is not None]
    if not eligible:
        return "No judged transcripts to label — run with --judge first."

    chosen = calibration.sample_for_labelling(eligible, size=size, seed=seed)
    lines = [f"Label {len(chosen)} transcripts pass/fail, then re-run with --kappa.",
             ""]
    for n, transcript in enumerate(chosen, 1):
        probe = by_id[transcript["probe"]]
        final = transcript["turns"][-1]
        lines += [f"--- {n}. {probe.id} ({probe.row}) run {transcript['run']} ---",
                  f"ASKED:  {final['ask']}",
                  f"ANSWER: {final['reply']}",
                  "MUST CONVEY:"]
        lines += [f"  - {c}" for c in probe.rubric.must_convey]
        if probe.rubric.disqualifiers:
            lines.append("DISQUALIFIERS:")
            lines += [f"  - {d}" for d in probe.rubric.disqualifiers]
        lines += ["VERDICT (write pass or fail): ____", ""]
    return "\n".join(lines)


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
    ap.add_argument("--judge", action="store_true",
                    help="run the D5 science judge over the collected transcripts")
    ap.add_argument("--kappa", type=float, default=None,
                    help="measured judge-vs-human agreement; marks D5 calibrated")
    ap.add_argument("--label", type=Path, default=None,
                    help="print a labelling worksheet from a saved transcripts JSON "
                         "file and exit; no turns are run")
    args = ap.parse_args()

    corpus = probes.load_probes(args.probes)
    if args.only:
        wanted = {p.strip() for p in args.only.split(",")}
        corpus = [p for p in corpus if p.id in wanted]

    if args.label:
        saved = json.loads(args.label.read_text())
        print(label_worksheet(saved, corpus))
        return

    secret = settings.shopify_app_proxy_secret or settings.shopify_client_secret
    if not secret:
        raise SystemExit("needs SHOPIFY_APP_PROXY_SECRET (or client secret) in .env")

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
    failures: list[str] = []
    latencies: list[float] = []
    for probe in corpus:
        for run in range(args.runs or probe.runs):
            try:
                consent_probe = probe.must_not_flag and admin_token
                before_run = (_tagged_sourcing_rows(args.base, admin_token)
                              if consent_probe else 0)
                turns = play(probe, post)
                latencies += [t.seconds for t in turns]
                flagged = (_tagged_sourcing_rows(args.base, admin_token) > before_run
                           if consent_probe else False)
                results += dimensions.score_run(
                    probe, [t.reply for t in turns], [t.item_names for t in turns],
                    denylist=denylist, flagged=flagged)
                transcripts.append({
                    "probe": probe.id, "row": probe.row, "run": run + 1,
                    "turns": [{"ask": q, "reply": t.reply, "items": t.item_names,
                               "seconds": round(t.seconds, 2)}
                              for q, t in zip(probe.turns, turns)],
                })
            except Exception as exc:
                failures.append(f"{probe.id} run {run + 1}: {type(exc).__name__}: {exc}")
                print(f"  ! {probe.id} run {run + 1} failed: {type(exc).__name__}: {exc}")
            time.sleep(args.sleep)
        print(f"  {probe.id:<6} {probe.turns[0][:56]}")

    if args.judge:
        if not settings.anthropic_api_key:
            raise SystemExit("--judge needs ANTHROPIC_API_KEY in .env")
        print("\njudging science answers...")
        results += judge_transcripts(transcripts, corpus)

    from astor.eval import calibration
    calibrated = args.kappa is not None and args.kappa >= calibration.CALIBRATED_AT

    results = drop_unverifiable_consent(results, admin_token=admin_token)
    cells = report.aggregate(results)
    scorecard = report.render_scorecard(cells, calibrated=calibrated)
    print("\n" + scorecard)

    backlog_report = report.render_backlog(backlog(transcripts, denylist))
    print("\n" + backlog_report)

    latency_report = report.render_latency(latencies)
    print("\n" + latency_report)

    if failures:
        print(f"\n{len(failures)} turn(s) failed and were not scored:")
        for failure in failures:
            print(f"  - {failure}")

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
        failures_section = (
            "\n\n## Failures\n\n" + "\n".join(f"- {failure}" for failure in failures)
            if failures else "")
        args.out.write_text(
            scorecard + "\n\n" + backlog_report + "\n\n" + latency_report + failures_section
            + "\n\n## Transcripts\n\n" + json.dumps(transcripts, indent=2))
        print(f"\nreport written to {args.out}")

        transcripts_path = args.out.with_suffix(".transcripts.json")
        transcripts_path.write_text(json.dumps(transcripts, indent=2))
        print(f"transcripts written to {transcripts_path}")

    sys.exit(0 if not report.failing(cells) else 1)


if __name__ == "__main__":
    main()
