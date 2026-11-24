"""WearStream's configuration: every `WEARSTREAM_` setting, read and checked per command."""

from wearstream.settings.load import Loaded, load
from wearstream.settings.paths import Paths, paths
from wearstream.settings.registry import REGISTRY, RETIRED, Setting
from wearstream.settings.validate import apply_umask, ensure_valid

__all__ = [
    "REGISTRY",
    "RETIRED",
    "Loaded",
    "Paths",
    "Setting",
    "apply_umask",
    "ensure_valid",
    "load",
    "paths",
]
