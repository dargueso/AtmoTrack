"""``atmotrack-config`` — show or initialise the AtmoTrack configuration file.

Without options it prints the path of the ``config.toml`` that the
``atmotrack-*`` commands would use from the current directory (see
:mod:`atmotrack.config` for the lookup order).  ``--init`` copies the packaged
default to ``./config.toml`` so it can be edited for a new project.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil

from atmotrack import __version__
from atmotrack import config as cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--init",
        action="store_true",
        help="copy the packaged default config.toml into the current directory",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=pathlib.Path,
        default=pathlib.Path(cfg.FILENAME),
        help="destination for --init (default: ./config.toml)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite the destination if it already exists",
    )
    parser.add_argument(
        "--show", action="store_true", help="print the contents of the config in use"
    )
    args = parser.parse_args(argv)

    if args.init:
        dest = args.output
        if dest.exists() and not args.force:
            parser.error(f"{dest} already exists (use --force to overwrite)")
        shutil.copyfile(cfg.default_path(), dest)
        print(f"Wrote {dest} (copied from the packaged default)")
        return 0

    print(f"atmotrack {__version__}")
    print(f"config file: {cfg.config_path}")
    if cfg.config_path == cfg.default_path():
        print(f"(packaged default; set {cfg.ENV_VAR} or create ./config.toml to override)")
    if args.show:
        print()
        print(cfg.config_path.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
