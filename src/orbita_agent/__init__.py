"""AI-facing gateway for governed Orbita research workflows."""

__version__ = "0.6.0"

from .config import AgentConfig
from .gateway import AgentGateway

__all__ = ["AgentConfig", "AgentGateway"]
