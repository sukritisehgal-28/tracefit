"""MOCK notification destination.

A real version would post each notification to a chat channel or to the ATS.
This one writes what would be sent to a local file, one JSON object per line.
Nothing leaves the machine and nobody is contacted.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import shown_path, sink


@sink("mock_webhook")
def mock_webhook(s: dict, payload: dict, root: Path) -> list[str]:
    path = root / s["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(n, ensure_ascii=False) for n in payload["notifications"]]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return [shown_path(path, root)]
