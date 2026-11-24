"""The `wearstream` command line.

`version` and `--help` never read configuration, so they can't fail on it; every other command
reads only the settings it uses (operator-interface design, per-command settings).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from wearstream import __version__, log
from wearstream.settings import load, paths
from wearstream.settings.report import describe

_DIRS = ("WEARSTREAM_DATA_DIR", "WEARSTREAM_STATE_DIR", "WEARSTREAM_SECRETS_DIR")


def _add_auth(commands: Any) -> None:
    auth = commands.add_parser(
        "auth",
        help="authorize WearStream with a source (interactive; run with docker exec -it)",
        description="Get a source's first credential. Needs an interactive terminal.",
    )
    auth.add_argument("source", choices=("whoop", "garmin"), help="whoop or garmin")
    auth.add_argument(
        "--new-account",
        action="store_true",
        help="accept a grant for a different account than the current token's",
    )


# @spec OPERATOR-CMD-002
def _auth(source: str, new_account: bool) -> int:
    if not sys.stdin.isatty():
        print(
            f"`wearstream auth {source}` is interactive: run it with `docker exec -it <container> "
            f"wearstream auth {source}` or `docker run -it`."
        )
        return 2
    if source == "garmin":
        from wearstream.garmin.bootstrap import auth_garmin

        return auth_garmin(new_account)
    from wearstream.whoop.bootstrap import auth_whoop

    return auth_whoop(new_account)


def _add_state_commands(commands: Any) -> None:
    """`accept-schema` and `status`, which work on `/state`."""
    accept = commands.add_parser(
        "accept-schema",
        help="accept a collection's new payload shape after a schema_drift alert",
        description="Record a collection's current payload keys as its accepted schema.",
    )
    accept.add_argument("source", help="whoop or garmin")
    target = accept.add_mutually_exclusive_group(required=True)
    target.add_argument("collection", nargs="?", help="the collection to accept")
    target.add_argument(
        "--all", action="store_true", help="every collection with a drift or unreadable baseline"
    )
    status = commands.add_parser(
        "status",
        help="show what WearStream is doing and what needs attention",
        description="Show the daemon, each source, its jobs and backfill, and failing checks.",
    )
    status.add_argument("--json", action="store_true", help="print one JSON object")


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
        "daemon",
        help="run the scheduler (the container's main process)",
        description="Run the scheduler: capture on schedule, catch up, backfill and run checks.",
    )
    run = commands.add_parser(
        "run",
        help="run one unit of a job now, by hand",
        description="Run one unit of a job by hand, under the same locks and limits as the daemon.",
    )
    run.add_argument("source", help="whoop or garmin")
    run.add_argument("job", help="the job: range, snapshots, daily, reference or checks")
    run.add_argument("--day", help="the partition day (YYYY-MM-DD), for range and daily only")
    for hidden in ("--run-id", "--trigger"):
        run.add_argument(hidden, help=argparse.SUPPRESS)
    run.add_argument("--result-fd", type=int, help=argparse.SUPPRESS)
    commands.add_parser(
        "healthcheck",
        help="exit 0 when the daemon is healthy (for the container health check)",
        description="Exit 0 when the daemon's heartbeat is fresh and capture is not paused.",
    )
    _add_auth(commands)
    _add_state_commands(commands)
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


# @spec OPERATOR-CMD-001, OPERATOR-CMD-003, OPERATOR-SET-009, SUPERVISOR-HEALTH-002
def main(argv: list[str] | None = None) -> int:
    """Run a command and return its exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "config":
        return _config()
    if args.command == "daemon":
        from wearstream.supervisor import run_daemon

        return run_daemon()
    if args.command == "run":
        from wearstream.runner.run import run_command

        return run_command(
            args.source, args.job, args.day, args.run_id, args.trigger, args.result_fd
        )
    if args.command == "auth":
        return _auth(args.source, args.new_account)
    if args.command == "accept-schema":
        from wearstream.checks.accept import accept_schema

        return accept_schema(args.source, args.collection, args.all)
    if args.command == "status":
        from wearstream.status import status_command

        return status_command(as_json=args.json)
    if args.command == "healthcheck":
        from wearstream.supervisor import healthcheck

        return healthcheck()
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
