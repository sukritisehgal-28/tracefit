"""Separate source checks; all cases are synthetic and no tests call an API."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from shortlist import config
from shortlist.lexicon import Lexicon
from shortlist.model_reader import ModelReader, SemanticVerifier, openai_provider, VERIFY_SYSTEM
from test_rewording import read
from test_semantic_reader import reviewed
from helpers import tiny_world, run_rule


def pipeline(checker):
    lex = Lexicon(config.load())
    return ModelReader(reviewed(), lex, "reader", review_claims=True,
                       verifier=SemanticVerifier(checker, lex, "checker"))


class SeparateChecker(unittest.TestCase):
    def test_checker_sees_final_facts_but_no_extractor_verdicts(self):
        seen = []
        def check(text, kind, vocabulary):
            seen.append(vocabulary)
            return reviewed(reject=("skill",))(text, kind, vocabulary)
        claims = read("I use OASIS daily.", model=pipeline(check))
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in claims))
        self.assertTrue(any(c.rule == "model.verifier" for c in claims))
        self.assertEqual(seen[0]["_purpose"], "verify")
        self.assertNotIn("reviews", seen[0])

    def test_checker_failure_withholds_positive_and_preserves_contact_restriction(self):
        def failed(*args):
            raise TimeoutError
        claims = read("I use OASIS. Please do not contact me.", model=pipeline(failed))
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in claims))
        self.assertTrue(any(c.attr == "dnc_until" for c in claims))

    def test_checker_cannot_add_facts_or_reinstate_its_rejected_claim(self):
        check = reviewed([dict(kind="skill", key="oasis", value="", says="yes", quote="I use OASIS.")], reject=("skill",))
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in read("I use OASIS.", model=pipeline(check))))

    def test_disagreement_is_explained_in_the_matching_result(self):
        cfg = {"model_reader": {"_provider": reviewed(), "review_claims": True,
                                "verify_claims": True}}
        checker = pipeline(reviewed(reject=("skill",)))
        cfg["model_reader"]["_provider"] = reviewed()
        with patch("shortlist.model_reader.SemanticVerifier", return_value=checker.verifier):
            world, settings = tiny_world(job_text="Requirements: OASIS.",
                texts=[("candidate_message", "2026-11-06", "I use OASIS.")], overrides=cfg)
        result = run_rule("skills", world, settings)
        self.assertEqual(result.outcome, "unknown")
        self.assertTrue(any("separate source check" in n for n in result.notes))

    def test_checked_reading_is_cached_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reads.json"
            lex = Lexicon(config.load())
            first = ModelReader(reviewed(), lex, "model", path, True, SemanticVerifier(reviewed(), lex, "model", path))
            read("I use OASIS.", model=first)
            self.assertEqual(len(json.loads(path.read_text())), 2)
            second = ModelReader(Mock(side_effect=AssertionError), lex, "model", path, True,
                                 SemanticVerifier(Mock(side_effect=AssertionError), lex, "model", path))
            read("I use OASIS.", model=second)
            self.assertEqual(second.verifier.stats["replayed"], 1)

    def test_api_request_uses_a_separate_verifier_prompt(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "unit-test-key"}):
            provider = openai_provider({})
        response = Mock()
        response.read.return_value = json.dumps({"choices": [{"message": {"content": '{"facts":[],"reviews":{}}'}, "finish_reason": "stop"}]}).encode()
        with patch("urllib.request.urlopen") as call:
            call.return_value.__enter__.return_value = response
            provider("Fictional source", "candidate_message", {"_purpose": "verify", "_claims": []})
        self.assertEqual(json.loads(call.call_args.args[0].data)["messages"][0]["content"], VERIFY_SYSTEM)


if __name__ == "__main__":
    unittest.main()
