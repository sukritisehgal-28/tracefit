"""Matching is independent of where data comes from and where results go.

Shown two ways: by reading the imports, and by adding a source and a destination
at run time without editing any matching code.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from helpers import engine
from shortlist import config
from shortlist.pipeline import Engine
from shortlist.sinks import SINKS, sink
from shortlist.sources import ADAPTERS, adapter

PACKAGE = Path(config.ROOT) / "shortlist"
CORE = ["rules.py", "matching.py", "world.py", "extract.py", "lexicon.py", "timeutil.py", "model.py"]


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            found.add(("." * node.level) + (node.module or ""))
            found.update(("." * node.level) + (node.module or "") + "." + a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


class Independence(unittest.TestCase):
    def test_the_matching_core_imports_no_source_and_no_sink(self):
        for name in CORE:
            bad = [i for i in imports_of(PACKAGE / name) if "sources" in i or "sinks" in i or "pipeline" in i or "server" in i]
            self.assertEqual(bad, [], f"{name} must not depend on {bad}")

    def test_sources_and_sinks_import_no_matching_code(self):
        for folder in ("sources", "sinks"):
            for path in (PACKAGE / folder).glob("*.py"):
                bad = [i for i in imports_of(path) if any(x in i for x in ("rules", "matching", "world", "pipeline"))]
                self.assertEqual(bad, [], f"{folder}/{path.name} must not depend on {bad}")

    def test_a_new_source_needs_one_adapter_and_one_config_block(self):
        """A SYNTHETIC fifth candidate source in a made-up shape (a list of rows)."""
        @adapter("toy_rows")
        def toy_rows(raw, L):
            for row in raw:
                subj = L.person(row["ref"])
                rec = L.evidence(row["ref"], "structured_record", subj, row["seen"], "system", f"Toy row {row['ref']}")
                L.claim(rec, "name", row["who"])
                L.claim(rec, "profession", row["role"])
                L.evidence(row["ref"] + "-CV", "resume", subj, row["seen"], "candidate", f"Toy résumé {row['ref']}", text=row["cv"])
                L.evidence(row["ref"] + "-SAID", "candidate_message", subj, row["seen"], "candidate", f"Toy message {row['ref']}", text=row["said"])
        try:
            cfg = config.load()
            cfg["sources"].append({"name": "toy", "adapter": "toy_rows", "label": "Toy (synthetic)", "system_id": "toy", "path": "(inline)", "data": [{
                "ref": "T1", "who": "Synthetic Person", "role": "Physical Therapist", "seen": "2026-11-06",
                "cv": "Home-health PT, 2023-01 to present: OASIS start-of-care documentation, Homecare Homebase. Candidate reports active Washington PT license.",
                "said": "I am open to Spokane, can start December 1, work weekdays for thirteen weeks, and need at least $2,300 per week for forty hours.",
            }]})
            extended, base = Engine(cfg=cfg), engine()
            ranked = extended.matches(0)["job:VMS-901"][0]
            new = next(m for m in ranked if m.person == "person:toy:T1")
            self.assertEqual((new.tier, new.score), ("strong", 100))
            self.assertNotIn("provisional", new.flags, "nearly four documented years meets either version of the requirement")
            before = {m.person: (m.tier, m.score, m.action["status"]) for m in base.matches(0)["job:VMS-901"][0]}
            after = {m.person: (m.tier, m.score, m.action["status"]) for m in ranked if m.person != "person:toy:T1"}
            self.assertEqual(before, after, "adding a source changed nobody else's result")
        finally:
            ADAPTERS.pop("toy_rows", None)

    def test_a_new_destination_needs_one_function_and_one_config_block(self):
        received = []

        @sink("memory")
        def memory(s, payload, root):
            received.append(payload)
            return ["(memory)"]
        try:
            cfg = config.load()
            cfg["sinks"] = [{"name": "in_memory", "adapter": "memory", "path": "(memory)"}]
            written = Engine(cfg=cfg).publish(2)
            self.assertEqual(written, ["(memory)"])
            payload = received[0]
            self.assertTrue(payload["simulated"])
            self.assertEqual({s["job"] for s in payload["shortlists"]}, {"VMS-901", "VMS-902", "VMS-904"}, "open jobs only")
            first = payload["shortlists"][0]["matches"][0]
            self.assertEqual(sorted(first), ["action_status", "name", "next_action", "open_questions", "person", "score",
                                             "score_high", "score_low", "summary", "tier"])
        finally:
            SINKS.pop("memory", None)

    def test_the_built_in_destinations_write_only_local_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load()
            for s in cfg["sinks"]:
                s["path"] = str(Path(tmp) / Path(s["path"]).name)
            written = Engine(cfg=cfg).publish(2)
            names = sorted(Path(w).name for w in written)
            self.assertIn("VMS-904.json", names)
            self.assertIn("VMS-904.csv", names)
            self.assertIn("outbox.jsonl", names)
            self.assertTrue(all(Path(w).exists() for w in written))


if __name__ == "__main__":
    unittest.main()
