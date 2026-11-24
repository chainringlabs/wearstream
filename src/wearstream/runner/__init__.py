"""Running one unit of work: the `wearstream run` child and hand-started runs."""

STOP_GRACE_SECONDS = 45  # how long a stopped run may take before it is ended outright

OK, FAILED, USAGE, ENVIRONMENT, NEEDS_AUTH, BUSY, RATE_LIMITED = 0, 1, 2, 3, 4, 5, 6
CRASHED, STOPPED = 70, 75
OUTCOMES = {
    OK: "success",
    FAILED: "failed",
    ENVIRONMENT: "environment",
    NEEDS_AUTH: "needs_auth",
    BUSY: "busy",
    RATE_LIMITED: "rate_limited",
    STOPPED: "interrupted",
}


def outcome_for(code: int) -> str:
    """A unit's outcome from its exit code alone (the daemon also knows what it signalled)."""
    return OUTCOMES.get(code, "crashed")
