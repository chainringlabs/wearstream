"""The capture tree under `/data`: writing captures, reading them back, and cleaning up."""

from wearstream.capture.index import clear_index
from wearstream.capture.read import StoredCapture, has_data, list_captures
from wearstream.capture.repair import repair
from wearstream.capture.screen import screen_secrets
from wearstream.capture.self_test import SelfTestResult, self_test
from wearstream.capture.write import CaptureResult, Outcome, capture

__all__ = [
    "CaptureResult",
    "Outcome",
    "SelfTestResult",
    "StoredCapture",
    "capture",
    "clear_index",
    "has_data",
    "list_captures",
    "repair",
    "screen_secrets",
    "self_test",
]
