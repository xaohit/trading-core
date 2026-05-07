"""
Hermes Trading Core
模块化交易系统框架，供其他 AI Agent 调用
"""

__version__ = "0.1.0"

from . import config
from . import market
from . import state
from . import scanner
from . import executor
from . import risk
from . import core_memory
from . import db
from . import strategies
from .tools import TOOLS, TOOL_DEFINITIONS

TOOL_NAMES = list(TOOLS.keys())

__all__ = [
    "config",
    "market",
    "state",
    "scanner",
    "executor",
    "risk",
    "core_memory",
    "db",
    "strategies",
    "TOOLS",
    "TOOL_DEFINITIONS",
    "TOOL_NAMES",
]
