"""The `wearstream` command line.

`version` and `--help` never read configuration, so they can't fail on it; `config` reads every
setting and reports it (operator-interface design, per-command settings). The other commands arrive
with the components that own them.
"""

from __future__ import annotations

import argparse
import os

from wearstream import __version__, log
from wearstream.settings import load, paths
from wearstream.settings.report import describe

_DIRS = ("WEARSTREAM_DATA_DIR", "WEARSTREAM_STATE_DIR", "WEARSTREAM_SECRETS_DIR")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wearstream",
        description="WearStream keeps a complete local copy of your raw Whoop and Garmin data.",
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser(
        "version", help="print the installed version", description="Print the installed version."
    )
    commands.add_parser(
        "config",
        help="show every setting, its value and where it came from, and any problems",
        description="Show the effective settings, which secret files exist, and every problem.",
    )
    return parser


# @spec OPERATOR-CONFIG-001
def _config() -> int:
    loaded = load(os.environ, "config")
    resolved = paths(loaded.values) if all(name in loaded.values for name in _DIRS) else None
    print(describe(loaded, resolved))
    return 2 if loaded.problems else 0


# @spec OPERATOR-CMD-003, OPERATOR-SET-009
def main(argv: list[str] | None = None) -> int:
    """Run a command and return its exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "config":
        return _config()
    parser.print_help()
    return 2


def _early(name: str, choices: tuple[str, ...]) -> str:
    """A logging setting read leniently, before the command validates it properly."""
    value = os.environ.get(name, "").strip().lower()
    return value if value in choices else choices[0]


# @spec OPERATOR-LOG-002
def entry() -> None:
    """The `wearstream` executable: route all process output into the log, then run."""
    log.configure(
        fmt=_early("WEARSTREAM_LOG_FORMAT", ("console", "json")),
        level=_early("WEARSTREAM_LOG_LEVEL", ("info", "debug")),
    )
    log.route_process_output()
    raise SystemExit(main())
