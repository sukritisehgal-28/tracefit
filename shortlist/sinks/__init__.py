"""Destination adapters: the only code that knows where results go.

Each sink receives the same destination-neutral payload and writes it somewhere.
The matching code never imports a sink. To add a destination, write one function
here and add one [[sinks]] block to the configuration.

Every built-in sink is SIMULATED: it writes local files and contacts nothing.
"""
from __future__ import annotations

from pathlib import Path

SINKS: dict = {}


def sink(name: str):
    def register(fn):
        SINKS[name] = fn
        return fn
    return register


def shown_path(path: Path, root: Path) -> str:
    """The path as written in reports: relative to the project when it is inside it."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def publish(cfg: dict, payload: dict, root: Path) -> list[str]:
    """Hand the payload to every configured sink. Returns the paths written."""
    written: list[str] = []
    for s in cfg.get("sinks", []):
        written.extend(SINKS[s["adapter"]](s, payload, Path(root)))
    return written


from . import files, webhook  # noqa: E402,F401  (registers the built-in sinks)
