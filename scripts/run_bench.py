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
    M03 and M05 can create rows in `sourcing_requests`. Their turns embed the tag
    `astor-bench-probe`, but the row is written from a string the MODEL composes,
    so the tag is not guaranteed to reach it. Rows are therefore identified by
    shop and by this run's start time, and the DELETE handed to the operator is
    scoped BY ID. The script never deletes anything itself.

    python -m scripts.run_bench

CALIBRATING D5
    python -m scripts.run_bench --label bench.transcripts.json   > worksheet.txt
    # label the KEY entries into labels.json, then:
    python -m scripts.run_bench --kappa-from labels.json \\
                                --transcripts bench.transcripts.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from astor.config import settings
from astor.eval import calibration, dimensions, probes, report
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

# The `type` the proxy stamps on a product card (`shopify_proxy.py:71`); the
# other value it emits is "protocol".
_PRODUCT_ITEM = "product"


@dataclass(frozen=True)
class TurnResult:
    reply: str
    item_names: list[str]           # everything the turn surfaced
    seconds: float = 0.0
    # Only the items the proxy typed as products. D1 asks whether a PRODUCT was
    # surfaced, and the ground-truth pre-flight validates against products only,
    # so scoring D1 on protocol titles too would make the two disagree.
    product_names: list[str] = field(default_factory=list)


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
        turns.append(TurnResult(
            reply=payload["reply"],
            item_names=[i["name"] for i in last_items],
            # `last_seconds` excludes retry backoff; a fake `post` in a test has
            # no such attribute, so the wall clock stands in.
            seconds=getattr(post, "last_seconds", elapsed),
            product_names=[i["name"] for i in last_items
                           if i.get("type") == _PRODUCT_ITEM],
        ))
    return turns


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
# A 429 or a provider blip during a 256-turn run must cost one turn, not the
# whole run. 4xx other than 429 is a real error and is not retried.
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


def _post_with_retry(send, *, attempts: int = 3, backoff: float = 5.0
                     ) -> tuple[object, float]:
    """Returns `(result, slept_seconds)`.

    The backoff is reported separately because the caller times the request: at
    `--sleep 3.0` against a 20/min cap a 429 is expected, and folding 5s + 10s
    of sleep into a turn time would put fifteen fictional seconds into the
    reported p95 — a headline number.
    """
    last: Exception | None = None
    slept = 0.0
    for attempt in range(attempts):
        try:
            return send(), slept
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        if attempt < attempts - 1:
            delay = backoff * (2 ** attempt)
            time.sleep(delay)
            slept += delay
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
        started = time.monotonic()
        payload, slept = _post_with_retry(send)
        post.last_seconds = time.monotonic() - started - slept
        return payload
    post.last_seconds = 0.0
    return post


def _parsed(stamp: str) -> datetime:
    """`created_at` comes back as an ISO string from a timezone-aware column. A
    naive one would still be UTC on the server, so treat it as such rather than
    comparing an aware and a naive datetime and raising mid-run."""
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def row_is_from_this_run(row: dict, *, since: datetime, shop: str | None) -> bool:
    """Was this `sourcing_requests` row written by the benchmark we are running?

    Identified by shop and by time, NOT by the probe tag. The tag lives in the
    turn text; the row is written from `args["item"]`, a string the MODEL
    composes, and a model that normalises "Matrigel (astor-bench-probe)" to
    "Matrigel" leaves an untraceable row in production — with the cleanup SQL
    unprinted and M05's D4C reading a false pass. Shop and timestamp are
    server-supplied and the model cannot touch either.
    """
    if shop is not None and row.get("shop") != shop:
        return False
    stamp = row.get("created_at")
    return bool(stamp) and _parsed(stamp) >= since


def _sourcing_rows_since(base: str, admin_token: str, since: datetime,
                         *, shop: str | None = None) -> list[dict]:
    """This run's `sourcing_requests` rows, newest first. The endpoint is behind
    require_admin_token (api/main.py:50).

    Retry-wrapped like the chat post: a blip on this read would otherwise
    discard a whole transcript while the production row stays written.
    """
    def send() -> dict:
        request = urllib.request.Request(
            f"{base}/api/sourcing-requests?limit=200",
            headers={"X-Admin-Token": admin_token})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())

    payload, _slept = _post_with_retry(send)
    return [row for row in payload["items"]
            if row_is_from_this_run(row, since=since, shop=shop)]


def writes_to_production(corpus: list) -> bool:
    """Could any probe in this corpus cause a `sourcing_requests` write?

    True for the tagged consent probes (M03, M05) and for anything asserting
    that no row was written — those are precisely the runs whose rows must be
    reported to an operator afterwards.
    """
    return any(probes.PROBE_TAG in " ".join(p.turns) or p.must_not_flag
               for p in corpus)


def cleanup_sql(rows: list[dict]) -> str:
    """The DELETE handed to an operator. Scoped BY ID, never by tag: the tag is
    only present if the model chose to carry it. The benchmark never deletes
    from production itself."""
    if not rows:
        return "no sourcing_requests rows were written by this run."
    listed = "\n".join(
        f"  - {row['id']}  {row.get('created_at', '')}  {row.get('requested_item', '')!r}"
        for row in rows)
    ids = ", ".join(f"'{row['id']}'" for row in rows)
    return (f"{len(rows)} sourcing row(s) were written to production by this run:\n"
            f"{listed}\n"
            "Clean up with:\n"
            "  DELETE FROM sourcing_requests\n"
            f"   WHERE id IN ({ids});")


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


def judge_question(turns: list[dict]) -> str:
    """What the judge is asked to grade against.

    On a single-turn probe this is just the question. On a multi-turn one the
    earlier turns come with it, because the rubric refers to them: M06's rubric
    demands the answer "carries the substitute from turn 1", and a judge shown
    only "will that work for primary neurons?" cannot assess that at all. Three
    of the six multi-turn probes declare D5.

    Still blind by construction — no items, no probe id, no run index.
    """
    if len(turns) == 1:
        return turns[0]["ask"]
    prior = "\n".join(f"{t['ask']}\n(assistant: {t['reply']})" for t in turns[:-1])
    return f"{prior}\n{turns[-1]['ask']}"


def judge_transcripts(transcripts: list[dict], corpus: list, *, judge_fn=None
                      ) -> list[tuple[str, str, bool]]:
    """Second pass: grade D5 on collected transcripts.

    Separate from the run so a judge failure cannot abort thirty minutes of
    collection, and so tightened rubrics can be re-judged without paying for the
    turns again.

    The verdict is stamped back onto the transcript so it survives into the
    `.transcripts.json` sidecar — that is what a human's labels are later joined
    against to measure kappa.
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
        verdict = judge_fn(judge_question(transcript["turns"]), final["reply"],
                           probe.rubric)
        transcript["judge"] = {"passed": bool(verdict.passed),
                               "reason": verdict.reason}
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
    by_id = {p.id: p for p in corpus}
    eligible = [t for t in transcripts
                if (probe := by_id.get(t["probe"])) is not None
                and "D5" in probe.dimensions and probe.rubric is not None]
    if not eligible:
        return "No judged transcripts to label — run with --judge first."

    chosen = calibration.sample_for_labelling(eligible, size=size, seed=seed)
    lines = [f"Label {len(chosen)} transcripts pass/fail, then measure agreement "
             "with --kappa-from.", ""]
    for n, transcript in enumerate(chosen, 1):
        probe = by_id[transcript["probe"]]
        final = transcript["turns"][-1]
        lines += [f"--- {n}. {probe.id} ({probe.row}) run {transcript['run']} ---",
                  # The stable join key. Kappa compares two raters on the SAME
                  # items, so a label has to name the transcript it belongs to.
                  f"KEY: {calibration.label_key(transcript)}",
                  f"ASKED:  {judge_question(transcript['turns'])}",
                  f"ANSWER: {final['reply']}",
                  "MUST CONVEY:"]
        lines += [f"  - {c}" for c in probe.rubric.must_convey]
        if probe.rubric.disqualifiers:
            lines.append("DISQUALIFIERS:")
            lines += [f"  - {d}" for d in probe.rubric.disqualifiers]
        lines += ["VERDICT (write pass or fail): ____", ""]
    keys = ", ".join(f'"{calibration.label_key(t)}": "pass"' for t in chosen[:2])
    lines += [
        "When you are done, write your verdicts as a JSON object keyed by the KEY",
        f"line above — {{{keys}, ...}} — and pass that file to:",
        "  python -m scripts.run_bench --kappa-from labels.json "
        "--transcripts <run>.transcripts.json",
    ]
    return "\n".join(lines)


def render_kappa(kappa: float, n: int) -> str:
    verdict = ("D5 is calibrated and gates"
               if is_calibrated(kappa)
               else "D5 stays uncalibrated: reported, excluded from the gate")
    return (f"judge-vs-human kappa {kappa:.2f} over {n} jointly-labelled "
            f"transcript(s), bar {calibration.CALIBRATED_AT:.2f} — {verdict}.")


def is_calibrated(kappa: float | None) -> bool:
    return kappa is not None and kappa >= calibration.CALIBRATED_AT


def render_sections(cells: list, probe_cells: list, *, calibrated: bool,
                    backlog_rows: list, latencies: list[float],
                    failures: list[str], kappa_line: str = "",
                    cleanup: str = "") -> list[str]:
    """The report, once. stdout and `--out` print the same text — a report that
    differs from what the operator saw on screen is a report nobody trusts."""
    sections = ["## Rows\n\n" + report.render_scorecard(cells, calibrated=calibrated)]
    if kappa_line:
        sections.append(kappa_line)
    # The row matrix aggregates several probes into one cell, so a probe failing
    # three runs in five can sit inside a green row — which is precisely the
    # 2026-09-02 defect this benchmark exists to catch. The gate still reads the
    # row cells; this table is what anyone actually debugs from.
    sections.append("## Per-probe\n\n" + report.render_scorecard(
        probe_cells, calibrated=calibrated, gate=False, key_label="probe"))
    sections.append(report.render_backlog(backlog_rows))
    sections.append(report.render_latency(latencies))
    if failures:
        sections.append(f"## Failures\n\n{len(failures)} turn(s) failed and were "
                        "not scored:\n"
                        + "\n".join(f"  - {failure}" for failure in failures))
    if cleanup:
        sections.append("## Production rows\n\n" + cleanup)
    return sections


@dataclass
class Collected:
    results: list[tuple[str, str, bool]] = field(default_factory=list)
    probe_results: list[tuple[str, str, bool]] = field(default_factory=list)
    transcripts: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)


def collect(corpus: list, post, *, denylist: list[str], sleep: float,
            runs_override: int = 0, base: str = "", shop: str | None = None,
            admin_token: str | None = None,
            since: datetime | None = None) -> Collected:
    """Play every probe N times and score the deterministic dimensions.

    One failing run costs its own transcript and nothing else: a blip 200 turns
    into a 40-minute collection must not discard the 199 that worked.
    """
    out = Collected()
    for probe in corpus:
        for run in range(runs_override or probe.runs):
            try:
                consent_probe = bool(probe.must_not_flag and admin_token)
                before_run = (len(_sourcing_rows_since(base, admin_token, since,
                                                       shop=shop))
                              if consent_probe else 0)
                turns = play(probe, post)
                out.latencies += [t.seconds for t in turns]
                flagged = (len(_sourcing_rows_since(base, admin_token, since,
                                                    shop=shop)) > before_run
                           if consent_probe else False)
                scored = dimensions.score_run(
                    probe, [t.reply for t in turns], [t.item_names for t in turns],
                    denylist=denylist, flagged=flagged,
                    products_per_turn=[t.product_names for t in turns])
                out.results += scored
                out.probe_results += [(probe.id, dim, passed)
                                      for _row, dim, passed in scored]
                out.transcripts.append({
                    "probe": probe.id, "row": probe.row, "run": run + 1,
                    "turns": [{"ask": q, "reply": t.reply, "items": t.item_names,
                               "products": t.product_names,
                               "seconds": round(t.seconds, 2)}
                              for q, t in zip(probe.turns, turns)],
                })
            except Exception as exc:
                out.failures.append(
                    f"{probe.id} run {run + 1}: {type(exc).__name__}: {exc}")
                print(f"  ! {probe.id} run {run + 1} failed: "
                      f"{type(exc).__name__}: {exc}")
            time.sleep(sleep)
        print(f"  {probe.id:<6} {probe.turns[0][:56]}")
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
                    help="an ALREADY-MEASURED judge-vs-human agreement; prefer "
                         "--kappa-from, which measures it here")
    ap.add_argument("--kappa-from", type=Path, default=None,
                    help='JSON file of human labels, {"P21:1": "pass", ...}, keyed '
                         "by the KEY lines the --label worksheet prints; measures "
                         "kappa against the judge's own verdicts")
    ap.add_argument("--transcripts", type=Path, default=None,
                    help="a saved .transcripts.json to work from instead of running "
                         "turns; with --kappa-from this measures agreement offline")
    ap.add_argument("--label", type=Path, default=None,
                    help="print a labelling worksheet from a saved transcripts JSON "
                         "file and exit; no turns are run")
    return ap.parse_args(argv)


def main() -> None:
    args = _parse_args()

    corpus = probes.load_probes(args.probes)
    if args.only:
        wanted = {p.strip() for p in args.only.split(",")}
        corpus = [p for p in corpus if p.id in wanted]

    if args.label:
        print(label_worksheet(json.loads(args.label.read_text()), corpus))
        return

    if args.kappa_from and args.transcripts:
        # Offline: no turns, no network, no key needed.
        kappa, n = calibration.kappa_from_labels(
            json.loads(args.transcripts.read_text()),
            json.loads(args.kappa_from.read_text()))
        print(render_kappa(kappa, n))
        return

    if args.transcripts:
        raise SystemExit("--transcripts is only meaningful with --kappa-from")

    secret = settings.shopify_app_proxy_secret or settings.shopify_client_secret
    if not secret:
        raise SystemExit("needs SHOPIFY_APP_PROXY_SECRET (or client secret) in .env")
    # Everything a run needs is checked HERE, before a single turn. A missing key
    # or an unreadable labels file discovered forty minutes in costs the whole
    # run, throws away the transcripts, and skips the cleanup block — silently
    # orphaning M03's rows in production.
    if args.judge and not settings.anthropic_api_key:
        raise SystemExit("--judge needs ANTHROPIC_API_KEY in .env")
    labels: dict = {}
    if args.kappa_from:
        if not args.judge:
            raise SystemExit("--kappa-from needs --judge (or --transcripts, to "
                             "measure agreement against an earlier run's verdicts)")
        labels = json.loads(args.kappa_from.read_text())

    denylist = dimensions.build_denylist(
        _brands_from_db() if args.brands_from_db else _BRANDS)
    post = _proxy_post(args.base, args.shop, secret)

    # Rows are identified by shop and start time, not by the probe tag: the tag
    # is in the turn text, but the row is written from a string the MODEL
    # composes, and a model that drops it leaves an untraceable row behind.
    # Note M03 (consent GIVEN) has no must_not_flag and so scores no D4C cell:
    # there is no per-turn signal for "it flagged at the right moment". It is
    # observed instead through the rows listed at the end of the run.
    write_capable = writes_to_production(corpus)
    admin_token = settings.admin_token
    if write_capable and not admin_token:
        print("! ADMIN_TOKEN unset — consent (D4C) cells will be skipped, and any "
              "row this run writes to production will go unreported.\n")
    run_started = datetime.now(timezone.utc)

    total_turns = sum(len(p.turns) * (args.runs or p.runs) for p in corpus)
    print(f"probes={len(corpus)}  turns={total_turns}  shop={args.shop}  base={args.base}\n")

    collected = collect(corpus, post, denylist=denylist, sleep=args.sleep,
                        runs_override=args.runs, base=args.base, shop=args.shop,
                        admin_token=admin_token, since=run_started)
    results, transcripts = collected.results, collected.transcripts
    probe_results = collected.probe_results

    if args.judge:
        print("\njudging science answers...")
        results += judge_transcripts(transcripts, corpus)
        probe_results += [(t["probe"], "D5", t["judge"]["passed"])
                          for t in transcripts if t.get("judge")]

    kappa_line = ""
    calibrated = is_calibrated(args.kappa)
    if args.kappa_from:
        try:
            kappa, n = calibration.kappa_from_labels(transcripts, labels)
        except ValueError as exc:
            # Never lose a completed run over a labelling mistake: report the
            # problem, leave D5 uncalibrated, print everything else.
            kappa_line = f"! kappa not measured: {exc}"
        else:
            calibrated = is_calibrated(kappa)
            kappa_line = render_kappa(kappa, n)

    results = drop_unverifiable_consent(results, admin_token=admin_token)
    probe_results = drop_unverifiable_consent(probe_results, admin_token=admin_token)
    cells = report.aggregate(results)

    # Printed whenever a write-capable probe ran and a token was available, not
    # only when the count grew: "nothing was written" is itself the result M05
    # asserts, and an operator needs to see it stated.
    cleanup = ""
    if write_capable and admin_token:
        try:
            cleanup = cleanup_sql(_sourcing_rows_since(args.base, admin_token,
                                                       run_started, shop=args.shop))
        except Exception as exc:            # noqa: BLE001
            # An unreadable admin endpoint must not swallow the fact that rows
            # may be sitting in production.
            cleanup = (f"! could not read /api/sourcing-requests "
                       f"({type(exc).__name__}: {exc}). Check sourcing_requests "
                       f"for rows on {args.shop} since "
                       f"{run_started.isoformat()} by hand.")

    sections = render_sections(
        cells, report.aggregate(probe_results), calibrated=calibrated,
        backlog_rows=backlog(transcripts, denylist), latencies=collected.latencies,
        failures=collected.failures, kappa_line=kappa_line, cleanup=cleanup)
    printed = "\n\n".join(sections)
    print("\n" + printed)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(printed + "\n\n## Transcripts\n\n"
                            + json.dumps(transcripts, indent=2))
        print(f"\nreport written to {args.out}")

        transcripts_path = args.out.with_suffix(".transcripts.json")
        transcripts_path.write_text(json.dumps(transcripts, indent=2))
        print(f"transcripts written to {transcripts_path}")

    # An empty scorecard is not a pass. If every turn errored, `results` is
    # empty, nothing is below a bar, and the old exit was 0 with GATE: PASS.
    failed_gate = bool(report.failing(cells, calibrated=calibrated))
    sys.exit(1 if failed_gate or collected.failures or not cells else 0)


if __name__ == "__main__":
    main()
