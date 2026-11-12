"""Load configuration: config/default.toml plus an optional overlay, merged key by key."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
