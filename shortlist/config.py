"""Load configuration: config/default.toml plus an optional overlay, merged key by key."""
from __future__ import annotations

import copy
import os
import shlex
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def openai_environment(root: Path | str = ROOT) -> dict[str, str]:
    """OpenAI credentials/settings from the process, falling back to a local .env.

    Only these two variables are read. The file is parsed as data, never executed,
    and its values are never included in configuration or browser payloads.
    """
    names = ("OPENAI_API_KEY", "OPENAI_MODEL")
    values = {}
    path = Path(root) / ".env"
    if path.is_file():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip().removeprefix("export ")
            name, sep, value = line.partition("=")
            if not sep or name.strip() not in names:
                continue
            try:
                parts = shlex.split(value, comments=True)
            except ValueError:
                raise ValueError(f".env line {line_no}: quote the value correctly") from None
            if len(parts) > 1:
                raise ValueError(f".env line {line_no}: use one value, quoted if it contains spaces")
            values[name.strip()] = parts[0] if parts else ""
    return {name: os.environ.get(name, values.get(name, "")).strip() for name in names}


def deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def list_overlays(root: Path | str = ROOT) -> list[dict]:
    found = []
    for path in sorted((Path(root) / "config" / "overlays").glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        found.append({"name": path.stem, "label": data.get("label", path.stem), "description": data.get("description", "")})
    return found


def load(overlay: str | None = None, root: Path | str = ROOT, path: str = "config/default.toml") -> dict:
    root = Path(root)
    cfg = tomllib.loads((root / path).read_text(encoding="utf-8"))
    if overlay:
        if overlay not in {o["name"] for o in list_overlays(root)}:
            raise ValueError(f"unknown configuration overlay: {overlay}")
        extra = tomllib.loads((root / "config" / "overlays" / f"{overlay}.toml").read_text(encoding="utf-8"))
        extra.pop("label", None)
        extra.pop("description", None)
        cfg = deep_merge(cfg, extra)
    cfg["_overlay"] = overlay
    cfg["_root"] = str(root)
    return cfg


def for_division(cfg: dict, division: str | None) -> dict:
    """Rules, scoring and freshness windows with the division's overrides applied."""
    override = cfg.get("divisions", {}).get(division or "", {})
    return {
        "rules": deep_merge(cfg["rules"], override.get("rules", {})),
        "gates": cfg["gates"],
        "scoring": deep_merge(cfg["scoring"], override.get("scoring", {})),
        "freshness_days": deep_merge(cfg["freshness_days"], override.get("freshness_days", {})),
        "dimensions": deep_merge(cfg.get("dimensions", {}), override.get("dimensions", {})),
    }
