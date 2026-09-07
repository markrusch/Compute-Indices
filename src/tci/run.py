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

    args = parser.parse_args(argv)

    if args.command == "migrate":
        return _cmd_migrate(args)
    if args.command == "docs":
        return _cmd_docs(args)
    if args.command == "sources":
        return _cmd_sources(args)
    if args.command in {"daily", "constituents", "backfill", "weights", "validate", "post"}:
        from tci import commands

        return commands.dispatch(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
