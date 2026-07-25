"""
Platform adapters for the portable Hermes harness.

Only the base contract and generic server/webhook adapters remain;
IM-specific adapters have been stripped.
"""

from .base import BasePlatformAdapter, MessageEvent, SendResult

__all__ = [
    "BasePlatformAdapter",
    "MessageEvent",
    "SendResult",
]
