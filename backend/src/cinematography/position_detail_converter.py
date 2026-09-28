"""Compatibility import; live implementation is in ``positioning``."""

try:
    from .positioning.detail_converter import PositionDetailConverter
except ImportError:  # Direct execution support for legacy position_agent.py.
    from positioning.detail_converter import PositionDetailConverter

__all__ = ["PositionDetailConverter"]
