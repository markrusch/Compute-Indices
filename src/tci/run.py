# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""CLI entrypoint: python -m tci.run <command>."""

from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger("tci")


def _cmd_migrate(args: argparse.Namespace) -> int:
    from tci import db

    conn = db.connect()
    applied = db.migrate(conn)
    print(f"applied: {applied or 'nothing (up to date)'}")
    return 0


def _cmd_docs(args: argparse.Namespace) -> int:
    from tci import methodology

    methodology.generate()
    print("METHODOLOGY.md and METHODOLOGY.lock regenerated.")
    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from tci import db, sources

    today = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(UTC).date()
    )
    registry = sources.load_registry()
    regions = sources.load_regions()
    if args.due:
        due = registry.due_for_review(today)
        for s in due:
            age = s.review_age_days(today)
            print(f"{s.id:<22} {'never reviewed' if age is None else str(age) + 'd ago'}")
        if not due:
            print("nothing due")
        return 0
    conn = db.connect()
    print(
        sources.render_report(
            conn, registry, regions, today, status=args.status, block=args.block
        )
    )
    return 0


def _cmd_reproduce(args: argparse.Namespace) -> int:
    """Exit 0: every checked print matched. 1: a mismatch. 2: could not run."""
    from tci import db, reproduce

    start = args.date or args.date_from
    end = args.date or args.date_to
    try:
        conn = db.connect()
        report = reproduce.reproduce_prints(conn, start, end, args.series)
        print("observations -> prints (recomputed under the version live on each date)")
        reproduce.print_report(report, args.verbose)
        bad = bool(report.mismatches)
        if args.published:
            pub = reproduce.check_published(conn)
            print("prints -> published files (digests)")
            reproduce.print_report(pub, args.verbose)
            bad = bad or bool(pub.mismatches)
    except Exception as exc:  # noqa: BLE001 - any failure to run is exit 2, never a pass
        print(f"reproduce could not run: {type(exc).__name__}: {exc}")
        return 2
    return 1 if bad else 0


def _cmd_effect(args: argparse.Namespace) -> int:
    """Recompute one date under two methodology versions and report the difference."""
    from tci import db, version_effect

    try:
        effects = version_effect.compare(
            db.connect(), args.date, args.before, args.after, args.series
        )
    except KeyError as exc:
        print(exc.args[0])
        return 2
    print(version_effect.render(effects, args.date, args.before, args.after))
    return 0


def _cmd_mlperf(args: argparse.Namespace) -> int:
    """MLPerf Training results by sellers TCI prices, against TCI's EU/EEA price."""
    from datetime import UTC, datetime

    from tci import db, mlperf
    from tci.config import load_factors

    if args.refresh:
        snapshot = mlperf.fetch(list(args.round or mlperf.DEFAULT_ROUNDS),
                                frozenset(mlperf.SUBMITTER_TO_PROVIDER))
        path = mlperf.save(snapshot)
        print(f"{len(snapshot.systems)} systems, {len(snapshot.results)} results -> {path}")
        for repo, commit in sorted(snapshot.repos.items()):
            print(f"  {repo} pinned at {commit}")
        return 0

    date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
    conn = db.connect()
    factors = load_factors(for_date=date)
    print(mlperf.render(mlperf.table(conn, date, factors.eu_eea_countries), date))
    return 0


def _cmd_forward(args: argparse.Namespace) -> int:
    """Record the forward estimate for a date and write site/data/forward/.

    Earlier stored dates with no estimate are written too, flagged backfilled. The daily run
    does exactly this after its prints; this is the same step on demand.
    """
    from tci import db, forward_data
    from tci.outputs import webdata

    conn = db.connect()
    db.migrate(conn)
    date = args.date or conn.execute(
        "SELECT MAX(r.utc_date) FROM runs r JOIN observations o ON o.run_id = r.run_id"
    ).fetchone()[0]
    if date is None:
        print("no stored observations to estimate from")
        return 1
    written = forward_data.record_forward(conn, date)
    path = webdata.write_forward(conn)
    print(f"forward {date}: {written} ledger rows written; {path}")
    tables = forward_data.forward_tables(conn)
    for r in (tables or {}).get("latest", []):
        state = (f"{r['value_usd']:.4f}" if r["value_usd"] is not None
                 else f"gap: {r['detail'].get('gap')}")
        print(f"  {r['component']} {r['horizon_days']:>3}d  {state}")
    return 0


def _cmd_term(args: argparse.Namespace) -> int:
    """The term tables for one date: every pooled cell, and how far each is from
    publishing. Roadmap L5.3: "re-check quarterly whether any tenor has reached three
    sellers" — this is that check, run on demand instead of by ad-hoc query."""
    from tci import db
    from tci.outputs.webdata import term_dates, term_tables

    conn = db.connect()
    date = args.date
    if date is None:
        dates = term_dates(conn)
        if not dates:
            print("no term-priced observations stored yet")
            return 1
        date = dates[-1]
    tables = term_tables(conn, date)
    cells = tables["cells"]
    published = [c for c in cells if c["published"]]
    print(f"term cells for {date} ({tables['note']})")
    print()
    width = max((len(c["gpu_model"]) for c in cells), default=10)
    for c in sorted(cells, key=lambda c: (-c["n_sellers"], c["gpu_model"], c["tenor_months"])):
        mark = "PUBLISHED" if c["published"] else f"{c['n_sellers']} of 3"
        ratio = f"median {c['median_ratio']}" if c["published"] else ""
        print(f"  {c['gpu_model']:<{width}}  {c['tenor_months']:>3}mo  {mark:<10}"
              f"  sellers={','.join(c['sellers'])}  {ratio}")
    print()
    print(f"{len(published)} of {len(cells)} cells published (>= 3 sellers)")
    close = [c for c in cells if not c["published"] and c["n_sellers"] == 2]
    if close:
        print(f"{len(close)} cell(s) one seller away from publishing:")
        for c in close:
            sellers = ', '.join(c['sellers'])
            print(f"  {c['gpu_model']} / {c['tenor_months']}mo needs one more; has {sellers}")
    if tables["stale_schedules"]:
        print(f"stale published schedules (past max_age_days): {tables['stale_schedules']}")
    return 0


def _cmd_reliability(args: argparse.Namespace) -> int:
    """Where the record gapped, and how close a series is to its gate."""
    from tci import db, reliability
    from tci.config import load_factors

    conn = db.connect()
    if args.coverage:
        days = reliability.coverage(conn, args.coverage, args.days)
        print(reliability.render_coverage(
            args.coverage, days, load_factors().methodology_version
        ))
        return 0
    print(reliability.render_gaps(reliability.gap_log(conn, args.series), args.limit))
    return 0


def _cmd_intake(args: argparse.Namespace) -> int:
    """The collected-to-published funnel, from the stored ledger or computed on the spot."""
    import uuid

    from tci import db, intake

    conn = db.connect()
    if args.backfill:
        # The daily run now repairs its own gaps (see intake.backfill), so this is for
        # filling the record ahead of the next run rather than the only way it happens.
        pending = intake.missing(conn, args.block)
        if not pending:
            print(f"every collection day already has an intake ledger for {args.block}")
            return 0
        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO runs (run_id, utc_date, source, started_utc, status)"
            " VALUES (?, ?, 'intake', ?, 'ok')",
            (run_id, pending[-1], db.utc_now_iso()),
        )
        conn.commit()
        filled = intake.backfill(conn, args.block, run_id)
        print(f"stored an intake ledger for {len(filled)} collection days")
        return 0

    date = args.date
    if not date:
        row = conn.execute(
            "SELECT MAX(utc_date) FROM runs r JOIN observations o ON o.run_id = r.run_id"
        ).fetchone()
        date = row[0] if row else None
    if not date:
        print("no observations stored")
        return 1

    stored = intake.head(conn, date, args.block)
    cells, factors = intake.compute(conn, date, args.block)
    if stored and stored != cells:
        # Worth saying out loud rather than silently preferring one: the stored ledger is
        # what the run recorded, the recomputation is what today's code makes of the same
        # rows. They differ when the classifier changed, which is exactly when a reader
        # wants to know.
        print(f"note: the stored ledger for {date} differs from a recomputation today")
    print(intake.render(
        stored or cells, date, factors.methodology_version,
        intake.shifts(conn, date, args.block),
        intake.dropouts(conn, date, args.block),
    ))
    return 0


def _cmd_canary(args: argparse.Namespace) -> int:
    """Live collection into a throwaway database. Exit 1 if a source stopped reporting."""
    from tci import canary

    results = canary.run(frozenset(args.source) if args.source else None)
    if not results:
        print(f"no such source: {', '.join(args.source or [])}")
        return 2
    print(canary.render(results))
    return 1 if any(r.broken for r in results) else 0


def _cmd_contrib(args: argparse.Namespace) -> int:
    """Contributed term prices: validate a file, ingest it privately, or aggregate.

    Exit 0 on success, 1 on a validation failure (the message names the line and field).
    """
    from pathlib import Path

    from tci import contrib
    from tci.db import utc_now_iso

    try:
        if args.action == "validate":
            rows, digest = contrib.read_file(Path(args.file))
            print(f"{len(rows)} rows valid (sha256 {digest[:12]}...)")
            return 0
        if args.action == "ingest":
            rows, digest = contrib.read_file(Path(args.file))
            conn = contrib.connect_private()
            n = contrib.ingest(conn, args.contributor, rows, digest, utc_now_iso(),
                                args.supersedes)
            print(f"{n} rows stored privately at {contrib.private_db_path()}")
            return 0
        if args.action == "aggregate":
            conn = contrib.connect_private()
            cells = contrib.aggregate(conn, args.date_from, args.date_to)
            text = contrib.publishable_json(cells, args.date_from, args.date_to)
            if args.out:
                Path(args.out).write_text(text + "\n", encoding="utf-8")
            else:
                print(text)
            return 0
    except contrib.ContributionError as exc:
        print(f"rejected: {exc}")
        return 1
    return 2


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(prog="tci", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending database migrations")
    sub.add_parser("docs", help="regenerate METHODOLOGY.md and METHODOLOGY.lock")

    p_daily = sub.add_parser("daily", help="collect + compute + export for a UTC date")
    p_daily.add_argument("--date", help="YYYY-MM-DD (default: today UTC)")

    p_cons = sub.add_parser("constituents", help="audit table for a print")
    p_cons.add_argument("--date", required=True)
    p_cons.add_argument("--series", default="EU-CRI-H100")

    p_back = sub.add_parser("backfill", help="recompute index range from stored observations")
    p_back.add_argument("--from", dest="date_from", required=True)
    p_back.add_argument("--to", dest="date_to", required=True)

    p_weights = sub.add_parser(
        "weights", help="show (computing if due) the effective weight review for a date"
    )
    p_weights.add_argument("--date", help="YYYY-MM-DD (default: today UTC)")

    sub.add_parser("validate", help="source-dropout sensitivity + check-series correlation")
    sub.add_parser("post", help="regenerate substack_post.md")

    p_src = sub.add_parser("sources", help="source register + region-block coverage")
    p_src.add_argument("--status", help="show only sources with this status")
    p_src.add_argument("--block", help="show only sources serving this region block")
    p_src.add_argument("--due", action="store_true", help="list sources past their review date")
    p_src.add_argument("--date", help="YYYY-MM-DD to evaluate the review clock against")

    p_rep = sub.add_parser(
        "reproduce",
        help="recompute stored prints from stored observations and check published digests",
    )
    p_rep.add_argument("--from", dest="date_from", help="first print date (default: all)")
    p_rep.add_argument("--to", dest="date_to", help="last print date (default: all)")
    p_rep.add_argument("--date", help="one print date (sets --from and --to)")
    p_rep.add_argument("--series", help="check one series only")
    p_rep.add_argument("--published", action="store_true",
                       help="also check site/data/prints/*.json and latest.json digests")
    p_rep.add_argument("--verbose", action="store_true", help="print MATCH lines too")

    p_term = sub.add_parser(
        "term", help="term-price cells for one date, and how close each is to publishing"
    )
    p_term.add_argument("--date", help="YYYY-MM-DD (default: the latest term-priced date)")

    p_fwd = sub.add_parser(
        "forward", help="record the forward estimate for a date and write site/data/forward"
    )
    p_fwd.add_argument("--date", help="YYYY-MM-DD (default: the latest collection date)")

    p_rel = sub.add_parser(
        "reliability", help="the record's gaps, and how close a series is to its gate"
    )
    p_rel.add_argument("--series", help="gap log for one series only")
    p_rel.add_argument("--coverage", metavar="SERIES",
                       help="replay the gate for this series over stored observations")
    p_rel.add_argument("--days", type=int, default=21, help="sessions to replay (default 21)")
    p_rel.add_argument("--limit", type=int, default=40, help="gaps to list (default 40)")

    p_intake = sub.add_parser(
        "intake",
        help="of everything collected, what reached the calculation and why the rest did not",
    )
    p_intake.add_argument("--date", help="YYYY-MM-DD (default: the newest collection day)")
    p_intake.add_argument("--block", default="EU_EEA", help="region block (default EU_EEA)")
    p_intake.add_argument(
        "--backfill", action="store_true",
        help="store a ledger revision for every collection day that has none",
    )

    p_eff = sub.add_parser(
        "effect",
        help="recompute one date under two methodology versions and show the difference",
    )
    p_eff.add_argument("--date", required=True, help="the print date to recompute")
    p_eff.add_argument("--before", required=True, help="methodology version, e.g. 0.4.0")
    p_eff.add_argument("--after", required=True, help="methodology version, e.g. 0.5.0")
    p_eff.add_argument("--series", help="one series only")

    p_ml = sub.add_parser(
        "mlperf", help="MLPerf Training results by sellers TCI prices, against TCI's price"
    )
    p_ml.add_argument("--date", help="price date (default: today UTC)")
    p_ml.add_argument("--refresh", action="store_true",
                      help="re-fetch from MLCommons and move the pinned commits")
    p_ml.add_argument("--round", action="append",
                      help="results repository to fetch (repeatable, with --refresh)")

    p_can = sub.add_parser(
        "canary",
        help="collect from every live source into a throwaway db; report what stopped reporting",
    )
    p_can.add_argument("--source", action="append",
                       help="check only this source (repeatable)")

    p_con = sub.add_parser(
        "contrib", help="contributed term prices: validate, ingest privately, aggregate"
    )
    p_con.add_argument("action", choices=["validate", "ingest", "aggregate"])
    p_con.add_argument("--file", help="submitted CSV (validate, ingest)")
    p_con.add_argument("--contributor", help="pseudonym for the submitting party (ingest)")
    p_con.add_argument("--supersedes", type=int,
                       help="id of the row a one-row correction file replaces (ingest)")
    p_con.add_argument("--from", dest="date_from", help="first as_of date (aggregate)")
    p_con.add_argument("--to", dest="date_to", help="last as_of date (aggregate)")
    p_con.add_argument("--out", help="write the publishable aggregates here (aggregate)")

    args = parser.parse_args(argv)

    if args.command == "contrib":
        return _cmd_contrib(args)
    if args.command == "migrate":
        return _cmd_migrate(args)
    if args.command == "docs":
        return _cmd_docs(args)
    if args.command == "sources":
        return _cmd_sources(args)
    if args.command == "reproduce":
        return _cmd_reproduce(args)
    if args.command == "canary":
        return _cmd_canary(args)
    if args.command == "intake":
        return _cmd_intake(args)
    if args.command == "reliability":
        return _cmd_reliability(args)
    if args.command == "mlperf":
        return _cmd_mlperf(args)
    if args.command == "effect":
        return _cmd_effect(args)
    if args.command == "term":
        return _cmd_term(args)
    if args.command == "forward":
        return _cmd_forward(args)
    if args.command in {"daily", "constituents", "backfill", "weights", "validate", "post"}:
        from tci import commands

        return commands.dispatch(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
