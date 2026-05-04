"""Runtime plugins for different agent CLIs."""

from typing import Dict, Type
from .base import AgentRuntime

# Registry will be populated when runtime modules are imported
_RUNTIMES: Dict[str, Type[AgentRuntime]] = {}


def register_runtime(name: str):
    """Decorator to register a runtime plugin."""
    def decorator(cls: Type[AgentRuntime]):
        _RUNTIMES[name] = cls
        return cls
    return decorator


def get_runtime(name: str) -> AgentRuntime:
    """Get runtime by name."""
    if name not in _RUNTIMES:
        raise ValueError(
            f"Unknown runtime: {name}. Available: {list(_RUNTIMES.keys())}"
        )
    return _RUNTIMES[name]()


def list_runtimes() -> list[str]:
    """List available runtimes."""
    return list(_RUNTIMES.keys())
