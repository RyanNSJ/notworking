"""AgentDown: Downdetector for AI agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentdown")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0"
