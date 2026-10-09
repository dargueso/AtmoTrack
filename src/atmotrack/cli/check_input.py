"""``atmotrack-check-input`` — report whether the configured input can be tracked.

Reads every ``pattern_*`` stream of ``[data_source]`` and prints, per stream,
the files, time axis, grid and variables with their units, plus a verdict:
``ready``, ``prepare`` (convertible with ``atmotrack-prepare``) or
``unsupported`` (with the reason).
"""

from __future__ import annotations

import argparse
import json
import logging

from atmotrack import config as cfg
from atmotrack.inputcheck import READY, check_all, format_report, overall_status
from atmotrack.prepare import STREAMS
from atmotrack.utils import get_logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--streams",
        nargs="+",
        choices=list(STREAMS),
        metavar="STREAM",
        help=f"streams to check (default: all configured; choices: {', '.join(STREAMS)})",
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report instead of text")
    parser.add_argument("--reindex", action="store_true", help="rebuild the file index cache")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    get_logger("atmotrack", level=logging.DEBUG if args.verbose else logging.WARNING)

    reports = check_all(args.streams, reindex=args.reindex)
    if args.json:
        print(
            json.dumps(
                {
                    "config": str(cfg.config_path),
                    "data_input": str(cfg.data_input),
                    "DT": cfg.DT,
                    "status": overall_status(reports),
                    "streams": [r.as_dict() for r in reports],
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(format_report(reports))
    return 0 if overall_status(reports) == READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
