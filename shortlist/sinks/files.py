"""File destinations: one JSON file per job (for another system) and one CSV per job (for a person)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import shown_path, sink


@sink("json_files")
def json_files(s: dict, payload: dict, root: Path) -> list[str]:
    folder = root / s["path"]
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for shortlist in payload["shortlists"]:
        path = folder / f"{shortlist['job']}.json"
        doc = {"simulated_destination": True, "as_of": payload["as_of"], "reference_date": payload["reference_date"],
               "configuration": payload["configuration"], **shortlist}
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(shown_path(path, root))
    return written


@sink("csv")
def csv_files(s: dict, payload: dict, root: Path) -> list[str]:
    folder = root / s["path"]
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for shortlist in payload["shortlists"]:
        path = folder / f"{shortlist['job']}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            out = csv.writer(fh)
            out.writerow(["rank", "name", "tier", "score", "score_low", "score_high", "next_action", "summary", "open_questions"])
            for i, m in enumerate(shortlist["matches"], start=1):
                out.writerow([i, m["name"], m["tier"], m["score"], m["score_low"], m["score_high"], m["next_action"],
                              m["summary"], " | ".join(m["open_questions"])])
        written.append(shown_path(path, root))
    return written
