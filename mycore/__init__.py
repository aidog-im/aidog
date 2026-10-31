"""mycore — self-developed kernel scaffold (P4-1a).

Public surface:
    from mycore import run_turn, build_registry, ChatClient, compact
"""
from .loop import TurnResult, run_turn
from .model import ChatClient, ModelConfig, ModelError
from .tools import ToolError, build_registry
from .compact import compact, measure

__all__ = ["run_turn", "TurnResult", "ChatClient", "ModelConfig", "ModelError",
           "ToolError", "build_registry", "compact", "measure"]
__version__ = "0.1.0-a1"
