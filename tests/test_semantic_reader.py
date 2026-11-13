"""Regressions and claim review. Every document is synthetic; tests call no API."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import PERSON, run_rule, tiny_world
from shortlist import config
from shortlist.extract import Extractor
from shortlist.lexicon import Lexicon
from shortlist.model import Evidence
from shortlist.model_reader import ModelReader, make_reader, openai_provider
from shortlist.server import Handler
from test_rewording import SAID, read
from reader_cases import CASES, holds


def reviewed(facts=(), reject=()):
    def provider(text, kind, vocabulary):
        return {"facts": list(facts), "reviews": [
            {"claim_id": c["id"], "verdict": "unsupported" if c["attribute"] in reject else "supported", "quote": c["quote"]}
            for c in vocabulary["_claims"]]}
    return provider


def semantic(provider, store=None):
    return ModelReader(provider, Lexicon(config.load()), "synthetic-model", store, review_claims=True)


class OfflineRegressions(unittest.TestCase):
    def test_less_than_the_minimum_is_not_an_exact_year_claim(self):
        for words, expected in (("less than two", "not_met"), ("under two", "not_met"),
                                ("at most two", "unknown"), ("at least two", "met"), ("more than two", "met")):
            with self.subTest(words=words):
                w, cfg = tiny_world(job_fields={"req.min_years": {"years": 2, "level": "required"}},
                                   job_text="Home health", texts=[("candidate_message", "2026-11-06", f"I have {words} years of home health experience.")])
                self.assertEqual(run_rule("setting_experience", w, cfg).outcome, expected)

    def test_excluded_dates_are_never_offered(self):
        for text in ("I can cover most dates, except December 5.", "I can work most weekends, though I am away December 5 and 6."):
            claims = read(text)
            self.assertFalse(any(c.attr == "shift_availability" and c.polarity == "pos" for c in claims))
            self.assertTrue(any(c.attr == "shift_availability" and c.key == "2026-12-05" and c.polarity == "neg" for c in claims))

    def test_earliest_start_is_read(self):
        c = next(c for c in read("My earliest start is December 15.") if c.attr == "available_from")
        self.assertEqual(c.value, date(2026, 12, 15))

    def test_notice_period_is_not_contract_length(self):
        self.assertFalse(any(c.attr == "weeks" for c in read("I gave two weeks notice at my current job.")))

    def test_dates_belong_to_the_role_they_follow(self):
        w, _ = tiny_world(texts=[("resume", "2026-11-06", "Outpatient PT 2015 to present, per-diem home health 2023.")])
        p = w.people[PERSON]
        self.assertGreater(p.fact("experience", "outpatient").value["years_documented"], 10)
        self.assertIsNone(p.fact("experience", "home_health").value["years_documented"])

    def test_months_in_an_account_manager_note_are_converted(self):
        w, cfg = tiny_world(job_text="Home health; minimum experience: 2 years.",
                           note="They'll take someone with 12 months of home-health experience.",
                           texts=[("resume", "2026-11-06", "Home-health PT, May 2025 to present.")])
        self.assertEqual(w.jobs["job:T-1"].req("req.min_years").clar.value["years"], 1)
        self.assertEqual(run_rule("setting_experience", w, cfg).outcome, "met")


class ClaimReview(unittest.TestCase):
    def test_recorded_sample_keeps_the_original_fit_and_contact_decisions(self):
        from shortlist.pipeline import Engine
        from shortlist.audit import audit
        cfg = config.load("openai")
        cfg["model_reader"].update(provider="recorded", recorded_path="recorded/semantic_sample_reads.json", verify_claims=False)
        plain, reviewed_engine = Engine(), Engine(cfg=cfg)
        for step in range(len(plain.steps)):
            for job, (rows, _) in plain.matches(step).items():
                after = reviewed_engine.shortlist(step, job).by_person
                for row in rows:
                    self.assertEqual((row.tier, row.action["status"]), (after[row.person].tier, after[row.person].action["status"]))
        self.assertEqual(audit(reviewed_engine)["findings"], [])
        self.assertFalse(any(w.extractor.model.stats.get("errors") for w in reviewed_engine._worlds.values()))

    def test_review_punctuation_round_trip_keeps_the_original_source_quote(self):
        def provider(text, kind, vocabulary):
            result = reviewed()(text, kind, vocabulary)
            for r in result["reviews"]:
                r["quote"] = r["quote"].replace("—", "\x7f")
            return result
        text = "Washington license — active."
        claims = read(text, model=semantic(provider))
        self.assertTrue(any(c.attr == "license" and c.polarity == "pos" and c.quote == text for c in claims))

    def test_model_cannot_invent_a_denial_for_an_explicit_shift_offer(self):
        text = "I could cover both December 5 and 6 day shifts in Dayton."
        provider = reviewed([dict(kind="shift", key="", says="no", value="2026-12-05", quote=text)])
        claims = read(text, model=semantic(provider))
        self.assertTrue(any(c.attr == "shift_availability" and c.key == "2026-12-05" and c.polarity == "pos" for c in claims))
        self.assertFalse(any(c.attr == "shift_availability" and c.polarity == "neg" for c in claims))

    def test_recorded_real_model_readings_hold_on_the_synthetic_cases(self):
        from shortlist.model_reader import recorded
        store = config.ROOT / "recorded" / "semantic_reads.json"
        model = ModelReader(recorded, Lexicon(config.load()), "gpt-4o", store, review_claims=True)
        cfg = config.load()
        for name, text, kind, attr, key, expected in CASES:
            with self.subTest(case=name):
                job = kind.startswith("job")
                ev = Evidence("evaluation/" + name, name, "synthetic", kind, "job:test" if job else "person:test:1",
                              SAID, False, "account_manager" if job else "candidate", "synthetic", text)
                claims = Extractor(cfg, Lexicon(cfg), model).run(ev)
                self.assertTrue(holds(claims, attr, key, expected))
        self.assertEqual(model.stats["replayed"], len(CASES))

    def test_conflicting_model_no_withdraws_a_false_positive_even_if_review_says_supported(self):
        provider = reviewed([dict(kind="cert", key="acls", says="unsure", value="", quote="ACLS certification remains on my to-do list.")])
        claims = read("ACLS certification remains on my to-do list.", model=semantic(provider))
        self.assertFalse(any(c.attr == "cert" and c.polarity == "pos" for c in claims))

    def test_guarded_value_correction_can_replace_a_confirmed_but_wrong_parse(self):
        provider = reviewed([dict(kind="schedule", key="", says="yes", value="weekdays", quote="can do weekdays")])
        claims = read("I prefer nights but can do weekdays.", model=semantic(provider))
        self.assertEqual([c.value for c in claims if c.attr == "schedule"], ["weekdays"])

    def test_model_can_correct_the_wrong_schedule(self):
        text = "I prefer nights but can do weekdays."
        provider = reviewed([dict(kind="schedule", key="", says="yes", value="weekdays", quote="can do weekdays")], reject=("schedule",))
        claims = read(text, model=semantic(provider))
        self.assertEqual([c.value for c in claims if c.attr == "schedule"], ["weekdays"])
        self.assertFalse(any(c.rule == "model.review" for c in claims))

    def test_unconfirmed_positive_is_removed_not_silently_trusted(self):
        model = semantic(reviewed(reject=("skill",)))
        claims = read("I use OASIS daily.", model=model)
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in claims))
        self.assertTrue(any(c.attr == "unclear" for c in claims))

    def test_api_failure_does_not_fall_back_to_a_confident_yes(self):
        def broken(*args):
            raise TimeoutError
        model = semantic(broken)
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in read("I use OASIS.", model=model)))
        self.assertEqual(model.stats["errors"], 1)

    def test_duplicate_or_forged_review_confirms_nothing(self):
        def forged(text, kind, vocabulary):
            review = dict(claim_id=0, verdict="supported", quote="invented quote")
            return {"facts": [], "reviews": [review, review]}
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in read("I use OASIS.", model=semantic(forged))))

    def test_model_cannot_reintroduce_the_parse_it_rejected(self):
        provider = reviewed([dict(kind="skill", key="oasis", says="yes", value="", quote="I use OASIS.")], reject=("skill",))
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in read("I use OASIS.", model=semantic(provider))))

    def test_unsupported_new_message_invalidates_an_older_yes(self):
        def provider(text, kind, vocabulary):
            return reviewed(reject=("skill",) if "recently" in text else ())(text, kind, vocabulary)
        w, cfg = tiny_world(job_text="Requirements: OASIS.", texts=[
            ("candidate_message", "2026-11-05", "I use OASIS."),
            ("candidate_message", "2026-11-06", "I recently used OASIS.")],
            overrides={"model_reader": {"_provider": provider, "review_claims": True}})
        self.assertEqual(run_rule("skills", w, cfg).outcome, "unknown")

    def test_unreviewed_job_requirement_stays_a_question(self):
        w, cfg = tiny_world(job_text="Home health; minimum experience: 2 years.", texts=[
            ("resume", "2026-11-06", "Home health since 2020.")],
            overrides={"model_reader": {"_provider": reviewed(reject=("req.min_years",)), "review_claims": True}})
        self.assertEqual(run_rule("setting_experience", w, cfg).outcome, "unknown")

    def test_no_contact_is_preserved_even_when_every_review_is_missing(self):
        claims = read("Please do not contact me. I use OASIS.", model=semantic(lambda *args: {"facts": [], "reviews": []}))
        self.assertTrue(any(c.attr == "dnc_until" for c in claims))

    def test_model_cannot_confuse_shift_blackout_with_contact_permission(self):
        provider = reviewed([dict(kind="do_not_contact", key="", says="yes", value="2026-12-05", quote="December 5 is a blackout date for me.")])
        self.assertFalse(any(c.attr == "dnc_until" for c in read("December 5 is a blackout date for me.", model=semantic(provider))))

    def test_review_cache_is_persistent_and_changes_with_vocabulary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "reading.json"
            first = semantic(reviewed(), store)
            read("I use OASIS.", model=first)
            second = semantic(Mock(side_effect=AssertionError("network called")), store)
            read("I use OASIS.", model=second)
            self.assertEqual(second.stats["replayed"], 1)
            cfg = config.load()
            cfg["lexicon"]["skill"]["new_tool"] = ["new tool"]
            Extractor(cfg, Lexicon(cfg), second).run(Evidence(
                "test/1", "1", "test", "candidate_statement", PERSON,
                SAID, False, "candidate", "test", "I use OASIS."))
            self.assertEqual(second.stats["errors"], 1)


class OpenAISetup(unittest.TestCase):
    def test_env_is_read_as_data_and_process_values_win(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            Path(tmp, ".env").write_text("export OPENAI_API_KEY='unit-test-key' # local\nOPENAI_MODEL=gpt-4o\nIGNORED=unused\n")
            self.assertEqual(config.openai_environment(tmp)["OPENAI_API_KEY"], "unit-test-key")
            with patch.dict("os.environ", {"OPENAI_API_KEY": "process-test-key"}):
                self.assertEqual(config.openai_environment(tmp)["OPENAI_API_KEY"], "process-test-key")

    def test_missing_key_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY is not set"):
                openai_provider({"_root": tmp})

    def test_request_stays_server_side_and_uses_structured_review(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "unit-test-key", "OPENAI_MODEL": "test-model"}):
            provider = openai_provider({})
        response = Mock()
        response.read.return_value = json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"facts": [], "reviews": []})}}]}).encode()
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = response
            self.assertEqual(provider("Fictional text", "candidate_message", {"_claims": []}), {"facts": [], "reviews": []})
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer unit-test-key")
        self.assertEqual(json.loads(req.data)["response_format"]["json_schema"]["schema"]["required"], ["facts", "reviews"])

    def test_configured_model_identity_uses_actual_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "override-model"}):
            cfg = config.load("openai")
            cfg["_root"] = tmp
            self.assertEqual(make_reader(cfg).model_id, "override-model")

    def test_live_provider_still_requires_external_text_permission(self):
        cfg = config.load("openai")
        cfg["model_reader"]["allow_external_text"] = False
        with self.assertRaisesRegex(ValueError, "allow_external_text"):
            make_reader(cfg)

    def test_server_bootstraps_profile_but_allows_explicit_offline_default(self):
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(default_overlay="openai")
        handler.path = "/"
        handler._send = Mock()
        handler.do_GET()
        self.assertIn(b'data-overlay="openai"', handler._send.call_args.args[1])
        handler.path = "/api/state?overlay="
        self.assertIsNone(handler._params()[2])


if __name__ == "__main__":
    unittest.main()
