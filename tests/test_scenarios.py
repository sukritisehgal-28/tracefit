"""Run the whole synthetic scenario base (tests/scenario_base.py) as part of the test suite."""
from __future__ import annotations

import unittest
from collections import Counter

from scenario_base import SCENARIOS, run


class ScenarioBase(unittest.TestCase):
    def test_every_scenario_holds(self):
        for s in SCENARIOS:
            with self.subTest(scenario=s["id"], title=s["title"]):
                self.assertEqual(run(s), [])

    def test_the_base_covers_every_area_and_has_unique_ids(self):
        ids = [s["id"] for s in SCENARIOS]
        self.assertEqual(len(ids), len(set(ids)))
        areas = Counter(s["area"] for s in SCENARIOS)
        for area, minimum in (("M", 30), ("F", 15), ("E", 8), ("I", 5)):
            self.assertGreaterEqual(areas[area], minimum, f"area {area} is thin")


if __name__ == "__main__":
    unittest.main()
