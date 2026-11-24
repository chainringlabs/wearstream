"""WearStream's logging: one event per line on stdout, in console or JSON format."""

from wearstream.log.errors import exception_fields
from wearstream.log.route import quiet_libraries, route_process_output, startup_warnings
from wearstream.log.setup import configure, get_logger

__all__ = [
    "configure",
    "exception_fields",
    "get_logger",
    "quiet_libraries",
    "route_process_output",
    "startup_warnings",
]
