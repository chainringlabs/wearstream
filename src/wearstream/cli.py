"""The `wearstream` command line.

Only `version` and `--help` exist so far; the other commands arrive with the components that own
them. `version` and `--help` never read configuration or the environment, so they can't fail on
either (operator-interface design, per-command settings).
"""

from __future__ import annotations

import argparse

from wearstream import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wearstream",
        description="WearStream keeps a complete local copy of your raw Whoop and Garmin data.",
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser(
        "version", help="print the installed version", description="Print the installed version."
    )
    return parser


# @spec OPERATOR-CMD-003, OPERATOR-SET-009
def main(argv: list[str] | None = None) -> int:
    """Run a command and return its exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    parser.print_help()
    return 2
