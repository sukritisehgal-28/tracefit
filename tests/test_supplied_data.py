"""Behaviour on the supplied sample packet.

These are the situations the packet appears built to probe. The packet says it is
not an answer key, so these tests assert the behaviours we chose and explain in
docs/EVALUATION.md, not a "correct ranking".
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import engine, match, rule
from shortlist import config

HOME_HEALTH, EMERGENCY, FILLED, OUTPATIENT = "VMS-901", "VMS-902", "VMS-903", "VMS-904"


class Snapshot(unittest.TestCase):
    """18:00 UTC, before any event."""

    def setUp(self):
        self.e = engine()
        self.w = self.e.world(0)

    # ---- identity
    def test_records_are_linked_only_on_explicit_references(self):
        self.assertEqual(len(self.w.people), 16, "4 ATS candidates and 12 profiles")
        self.assertEqual(self.w.canon("person:client_db:501"), "person:ats:C1")
        self.assertEqual(self.w.jobs["job:VMS-901"].ids, {"ats": "J1", "vms": "VMS-901"})
        self.assertEqual(sorted(self.w.jobs), ["job:VMS-901", "job:VMS-902", "job:VMS-903"])

    def test_identical_profile_text_does_not_merge_twelve_people(self):
        profiles = [p for k, p in self.w.people.items() if k.startswith("person:our_system:")]
        self.assertEqual(len(profiles), 12)

    def test_recruiter_ids_are_not_confused_with_profile_ids(self):
        self.assertEqual(match(self.e, 0, HOME_HEALTH, "R10").action["owner"], "recruiter R10")
        self.assertEqual(match(self.e, 0, HOME_HEALTH, "C2").action["owner"], "recruiter R2")
        self.assertEqual(rule(match(self.e, 0, HOME_HEALTH, "R02"), "ownership").outcome, "clear")

    # ---- stale record against a newer message
    def test_a_newer_message_replaces_a_stale_record_and_both_stay_visible(self):
        loc = rule(match(self.e, 0, HOME_HEALTH, "C1"), "location")
        self.assertEqual(loc.outcome, "met")
        self.assertIn("replaced", loc.flags)
        self.assertTrue(any(r["note"] == "replaced" and "OR" in r["quote"] for r in loc.refs))
        self.assertTrue(any("I am open to Spokane now." in r["quote"] for r in loc.refs))

    # ---- missing is not "no", and praise is not evidence
    def test_the_best_candidate_on_paper_is_held_back_by_an_unanswered_question(self):
        blair = match(self.e, 0, HOME_HEALTH, "C2")
        self.assertEqual(blair.tier, "clarify")
        self.assertEqual(rule(blair, "setting_experience").outcome, "unknown")
        self.assertEqual(rule(blair, "skills").outcome, "unknown")
        for name in ("license", "location", "start_date", "schedule", "pay"):
            self.assertEqual(rule(blair, name).outcome, "met", name)
        self.assertEqual(blair.action["status"], "route")
        self.assertTrue(any("no answer recorded as of 6 Nov 2026" in q for q in blair.questions))

    def test_a_sentence_about_missing_detail_is_not_read_as_experience(self):
        blair = self.w.people["person:ats:C2"]
        self.assertEqual(blair.fact("experience", "home_health").state, "unanswered")
        self.assertEqual(blair.fact("skill", "oasis").state, "unanswered")
        self.assertEqual(blair.fact("skill", "homecare_homebase").state, "unanswered")

    def test_a_recruiters_impression_does_not_become_a_skill(self):
        blair = self.w.people["person:ats:C2"]
        from_notes = [c for f in blair.facts.values() for c in f.claims
                      if self.w.evidence[c.evidence_id].kind == "recruiter_note" and c.polarity == "pos" and c.attr in ("skill", "experience")]
        self.assertEqual(from_notes, [])

    # ---- the same evidence is not counted twice
    def test_a_fuller_resume_and_a_note_repeating_it_are_one_source(self):
        fact = self.w.people["person:ats:C1"].fact("experience", "home_health")
        self.assertEqual([c.evidence_id for c in fact.claims], ["ats/C1-CV"])
        self.assertEqual(self.w.root("supplement/C1-CV-FULL"), "ats/C1-CV")
        self.assertIn("supplement/C1-NOTE-02", self.w.notes_on(["ats/C1-CV"]))
        self.assertEqual(self.w.root("vms/VMS-901"), self.w.root("ats/J1"), "the ATS job text is a copy of the feed text")

    def test_supplement_text_files_and_json_entries_are_the_same_evidence(self):
        folder = Path(config.ROOT) / "assessment-data" / "fde-assessment-supplement"
        packet = json.loads((folder / "supplemental_source_packet.json").read_text(encoding="utf-8"))
        for doc in packet["resume_documents"]:
            body = (folder / f"{doc['document_id']}.txt").read_text(encoding="utf-8").split("\n", 3)[3]
            self.assertEqual(body, doc["text"])

    # ---- two versions of a requirement
    def test_a_result_that_depends_on_an_unapproved_note_says_so(self):
        alex = match(self.e, 0, HOME_HEALTH, "C1")
        exp = rule(alex, "setting_experience")
        self.assertEqual((alex.tier, exp.outcome), ("strong", "met"))
        self.assertIn("provisional", exp.flags)
        self.assertEqual(exp.alt["outcome"], "not_met")
        self.assertEqual(rule(alex, "requirement_source").outcome, "confirm")

    def test_a_claim_that_conflicts_with_the_dated_history_is_flagged_not_trusted(self):
        exp = rule(match(self.e, 0, HOME_HEALTH, "R04"), "setting_experience")
        self.assertIn("disputed", exp.flags)
        self.assertEqual(exp.outcome, "met", "18 months meets the one-year clarification either way")
        self.assertEqual(exp.alt["outcome"], "unknown", "under the feed's two years it depends on the unverified claim")

    def test_a_candidates_own_correction_replaces_the_imported_profile(self):
        skills = rule(match(self.e, 0, HOME_HEALTH, "R05"), "skills")
        self.assertEqual(skills.outcome, "met")
        self.assertLess(skills.credit, 1.0)
        self.assertIn("replaced", skills.flags)
        self.assertEqual(skills.alt["outcome"], "not_met")

    # ---- similarly qualified candidates
    def test_identical_candidates_are_grouped_not_ranked_apart(self):
        trio = [match(self.e, 0, HOME_HEALTH, r) for r in ("R01", "R02", "R03")]
        self.assertEqual({m.identical for m in trio}, {3})
        self.assertEqual(len({(m.tier, m.score, m.low, m.high, m.band) for m in trio}), 1)
        self.assertTrue(all("arbitrary" in m.decided_by for m in trio[1:]))

    def test_each_lookalike_profile_differs_for_its_one_reason(self):
        expect = {
            "R06": ("strong", "pay", "not_met"), "R07": ("not_fit", "start_date", "not_met"),
            "R08": ("clarify", "start_date", "unknown"), "R09": ("strong", "pay", "unknown"),
            "R12": ("not_fit", "location", "not_met"),
        }
        for person, (tier, name, outcome) in expect.items():
            m = match(self.e, 0, HOME_HEALTH, person)
            self.assertEqual((m.tier, rule(m, name).outcome), (tier, outcome), person)
        self.assertEqual(rule(match(self.e, 0, HOME_HEALTH, "R08"), "schedule").outcome, "unknown")
        r09 = match(self.e, 0, HOME_HEALTH, "R09")
        self.assertLess(r09.low, r09.score)
        self.assertLess(r09.score, r09.high, "an unknown answer widens the range instead of failing")

    # ---- a recommendation is not permission
    def test_a_message_blocks_contact_even_when_the_structured_field_is_empty(self):
        r11 = match(self.e, 0, HOME_HEALTH, "R11")
        self.assertEqual(r11.tier, "strong")
        self.assertEqual(r11.action["status"], "blocked")
        self.assertIn("15 Jan 2027", r11.action["label"])

    def test_wrong_profession_is_not_a_fit(self):
        for person in ("C3", "C4"):
            m = match(self.e, 0, HOME_HEALTH, person)
            self.assertEqual((m.tier, rule(m, "profession").outcome), ("not_fit", "not_met"))

    # ---- locums: placed is not unavailable
    def test_a_placed_doctor_who_offered_the_weekend_shifts_is_the_match(self):
        casey = match(self.e, 0, EMERGENCY, "C3")
        shifts = rule(casey, "shift_coverage")
        self.assertEqual((casey.tier, shifts.outcome), ("strong", "met"))
        self.assertTrue(any("placed status does not say which days" in n for n in shifts.notes))
        certs = rule(casey, "certifications")
        self.assertEqual(certs.outcome, "met")
        self.assertIn("alternative_route", certs.flags)
        self.assertTrue(any("Emergency Medicine" in n and "family medicine" in n for n in certs.notes))
        self.assertEqual((casey.action["status"], casey.action["owner"]), ("route", "recruiter R3"))
        self.assertEqual(self.e.matches(0)["job:" + EMERGENCY][0][0].person, "person:ats:C3", "Casey is listed first")

    def test_the_ideal_doctor_on_paper_is_unavailable_and_must_not_be_contacted(self):
        drew = match(self.e, 0, EMERGENCY, "C4")
        self.assertEqual(rule(drew, "certifications").outcome, "met")
        self.assertEqual((drew.tier, rule(drew, "shift_coverage").outcome), ("not_fit", "not_met"))
        contact = rule(drew, "contact_permission")
        self.assertEqual((drew.action["status"], contact.outcome), ("blocked", "blocked"))
        self.assertIn("replaced", contact.flags, "the stale contact_allowed=true field is shown as replaced")

    # ---- a filled job
    def test_nobody_is_recommended_for_a_filled_job(self):
        statuses = {m.action["status"] for m in self.e.matches(0)["job:" + FILLED][0]}
        self.assertEqual(statuses, {"closed"})
        self.assertFalse(any(FILLED in n["text"] for n in self.e.notifications(2)))

    def test_credentials_are_never_called_verified(self):
        # everything the page can show: the first response, and every person opened on every open job
        shown = [self.e.state(2)]
        for key, (rows, _) in self.e.matches(2).items():
            shown += [self.e.detail(2, key, m.person) for m in rows]
        text = json.dumps(shown, default=str).lower()
        self.assertNotIn("verified license", text)
        self.assertNotIn("license verified", text)
        self.assertIn("verify before submitting", text)


class Replay(unittest.TestCase):
    """18:05 a new job arrives; 18:10 a candidate sends an update."""

    def setUp(self):
        self.e = engine()

    def test_events_are_replayed_in_time_order(self):
        self.assertEqual([(s["at"][11:16], s["kind"]) for s in self.e.steps],
                         [("18:00", "snapshot"), ("18:05", "job.created"), ("18:10", "candidate.updated")])

    def test_the_new_job_is_matched_as_soon_as_it_arrives(self):
        self.assertNotIn("job:" + OUTPATIENT, self.e.matches(0))
        ranked = self.e.matches(1)["job:" + OUTPATIENT][0]
        self.assertEqual(len(ranked), 16)
        top = ranked[0]
        self.assertEqual((top.person, top.tier, top.action["status"]), ("person:ats:C2", "strong", "route"))
        self.assertEqual([c["kind"] for c in self.e.changes(1)], ["job_added"])

    def test_home_health_therapists_are_possible_not_strong_for_the_outpatient_job(self):
        alex = match(self.e, 1, OUTPATIENT, "C1")
        exp = rule(alex, "setting_experience")
        self.assertEqual((alex.tier, exp.outcome, exp.level), ("possible", "unknown", "soft"))
        self.assertTrue(any("dates" in q for q in exp.questions))

    def test_the_candidate_update_changes_exactly_what_it_should(self):
        changes = self.e.changes(2)
        self.assertEqual([(c["name"], c["job"]) for c in changes], [("Alex Sample", "job:VMS-901")])
        self.assertEqual(changes[0]["cause"], ["our_system/EV-C1-NEW"])
        alex = match(self.e, 2, HOME_HEALTH, "C1")
        start = rule(alex, "start_date")
        self.assertEqual((alex.tier, start.outcome), ("not_fit", "not_met"))
        self.assertIn("replaced", start.flags)
        self.assertTrue(any("No exception to the start date is confirmed" in n for n in start.notes))
        self.assertEqual(rule(match(self.e, 2, OUTPATIENT, "C1"), "start_date").outcome, "met")

    def test_an_update_sent_through_one_system_reaches_the_person_held_in_another(self):
        person = self.e.world(2).people["person:ats:C1"]
        self.assertIn("our_system/EV-C1-NEW", person.evidence_ids)
        self.assertEqual(str(person.fact("available_from").value), "2026-12-08")

    def test_notifications_are_marked_simulated(self):
        outbox = self.e.notifications(2)
        self.assertTrue(outbox and all(n["simulated"] for n in outbox))
        self.assertEqual([n["event"] for n in outbox if n["step"] > 0], ["job_added", "match_changed"])


if __name__ == "__main__":
    unittest.main()


class ListOrder(unittest.TestCase):
    """What a recruiter sees first, and how people with the same score are told apart."""

    def test_someone_who_must_not_be_contacted_is_listed_after_everyone_a_recruiter_can_act_on(self):
        eng = engine()
        best = [m for m in eng.matches(0)["job:VMS-901"][0] if m.tier in ("strong", "possible")]
        statuses = [m.action["status"] for m in best]
        self.assertEqual(statuses[-1], "blocked")
        self.assertNotIn("blocked", statuses[:-1])
        self.assertEqual(best[-1].score, max(m.score for m in best))   # the score is untouched; only the place changes
        self.assertEqual(best[-1].tier, "strong")

    def test_that_order_is_a_setting(self):
        from shortlist import config as configuration
        from shortlist.pipeline import Engine
        cfg = configuration.load()
        cfg["scoring"]["actionable_first"] = False
        best = [m for m in Engine(cfg=cfg).matches(0)["job:VMS-901"][0] if m.tier == "strong"]
        blocked = next(i for i, m in enumerate(best) if m.action["status"] == "blocked")
        self.assertLess(blocked, len(best) - 1)                 # by fit alone, a score of 100 is not last

    def test_a_hand_over_to_another_recruiter_still_counts_as_something_to_do(self):
        eng = engine()
        top = eng.matches(1)["job:VMS-904"][0][0]
        self.assertEqual((top.tier, top.action["status"]), ("strong", "route"))

    def test_a_met_rule_with_a_conflicting_claim_is_shown_as_a_caution_not_a_strength(self):
        from shortlist.matching import card_dict
        eng = engine()
        row = next(m for m in eng.matches(0)["job:VMS-901"][0] if m.person.endswith(":R04"))
        card = card_dict(eng.world(0), row)
        self.assertEqual(row.tier, "strong")
        self.assertTrue(any("records show" in line for line in card["watch"]))
        self.assertIn("caution", card["marks"])
        self.assertEqual(card["good"], [])

    def test_both_of_two_people_nothing_separates_are_told_the_order_is_arbitrary(self):
        eng = engine()
        trio = [m for m in eng.matches(0)["job:VMS-901"][0] if m.person.split(":")[-1] in ("R01", "R02", "R03")]
        self.assertTrue(all("arbitrary" in m.decided_by for m in trio))


class HowTheListWasMade(unittest.TestCase):
    """The counts behind the 'How this list was made' panel on the page."""

    def test_every_stage_is_counted_and_adds_up(self):
        job = next(j for j in engine().state(0)["jobs"] if j["key"] == "job:VMS-901")
        g, a = job["groups"], job["best_actions"]
        self.assertEqual((job["stats"]["people"], job["stats"]["evaluated"]), (16, 16))
        self.assertEqual(g["best"] + g["not_fit"], job["stats"]["evaluated"])
        self.assertEqual((g["best"], job["need_answer"], g["not_fit"]), (12, 2, 4))     # one list: 10 pass, 2 need an answer
        self.assertEqual(sum(a.values()), g["best"])
        self.assertEqual((a.get("ready", 0) + a.get("route", 0), a.get("clarify", 0), a.get("blocked", 0)), (10, 1, 1))

    def test_the_reasons_name_the_rule_that_holds_each_person_back(self):
        job = next(j for j in engine().state(0)["jobs"] if j["key"] == "job:VMS-901")
        self.assertEqual(job["reasons"]["not_fit"], {"Profession": 2, "Start date": 1, "Location": 1})
        self.assertTrue(all(n >= 1 for n in job["reasons"]["clarify"].values()))

    def test_the_number_of_openings_is_not_invented(self):
        self.assertTrue(all(j["openings"] is None for j in engine().state(0)["jobs"]))

    def test_the_whole_process_counts_every_person_on_every_rule_the_job_uses(self):
        job = next(j for j in engine().state(0)["jobs"] if j["key"] == "job:VMS-901")
        rules = {r["name"]: r for r in job["process"]["rules"]}
        for r in rules.values():
            self.assertEqual(r["met"] + r["unknown"] + r["not_met"] + r["na"], 16, r["label"])
        self.assertEqual((rules["profession"]["met"], rules["profession"]["not_met"]), (14, 2))
        self.assertEqual(rules["shift_coverage"]["na"], 16)                      # a rule this job does not use
        self.assertEqual({d["name"] for d in engine().state(0)["meta"]["dimensions"] if d["means"]},
                         {"setting", "skills", "depth", "evidence", "readiness", "pay"})


class PeopleLevelOnEverything(unittest.TestCase):
    """R01, R02 and R03 differ in the supplied file only by id and email. The tool must not invent a difference:
    it says they are level, says their documents are the same words, and names what to ask."""

    def cards(self):
        return {c["person"].split(":")[-1]: c for c in engine().cards(0, "job:VMS-901", "best", 0, 50)["cards"]}

    def test_the_supplied_profiles_really_are_identical(self):
        raw = json.loads((Path(config.ROOT) / "assessment-data" / "our_system_response.json").read_text())
        strip = lambda p: json.dumps(p, sort_keys=True).replace(p["profile_id"], "X").replace(p["profile_id"].lower(), "x")   # noqa: E731
        by_id = {p["profile_id"]: strip(p) for p in raw["profiles"]}
        self.assertEqual(by_id["R01"], by_id["R02"])
        self.assertEqual(by_id["R01"], by_id["R03"])
        self.assertNotEqual(by_id["R01"], by_id["R10"])               # R10's documents are the same, but a recruiter owns R10

    def test_they_are_grouped_and_nothing_separates_them(self):
        c = self.cards()
        self.assertEqual({c[k]["identical"] for k in ("R01", "R02", "R03")}, {3})
        self.assertEqual(len({c[k]["same"] for k in ("R01", "R02", "R03")}), 1)
        self.assertEqual(len({c[k]["docs"] for k in ("R01", "R02", "R03")}), 1)    # word for word the same documents
        self.assertEqual(len({c[k]["score"] for k in ("R01", "R02", "R03")}), 1)

    def test_the_card_names_what_to_ask_to_tell_them_apart(self):
        asks = [d["key"] for d in self.cards()["R01"]["apart"]]
        self.assertEqual(asks, ["home", "history", "license"])
        self.assertIn("between 2022 and May 2025", self.cards()["R01"]["apart"][1]["text"])

    def test_someone_who_is_not_level_with_anyone_carries_no_such_line(self):
        self.assertIsNone(self.cards()["R04"]["apart"])

    def test_a_person_whose_home_is_on_file_is_not_asked_for_it(self):
        from shortlist.matching import open_details
        w = engine().world(0)
        keys = [d["key"] for d in open_details(w, w.jobs["job:VMS-901"], w.people["person:ats:C1"])]
        self.assertNotIn("home", keys)

    def test_the_data_problems_say_twelve_profiles_share_one_text(self):
        notes = [i for i in engine().issues(0) if "word for word" in i["message"]]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0]["message"].startswith("12 people"))
        self.assertIn("For 4 of them", notes[0]["message"])           # R01, R02, R03 and R10
        self.assertEqual(notes[0]["severity"], "info")


class PeopleWhoAreRuledOut(unittest.TestCase):
    """Someone a required rule rules out, or who must not be contacted, is not ranked and not questioned."""

    def test_a_physical_therapist_is_asked_nothing_about_the_physician_job(self):
        m = match(engine(), 0, "VMS-902", "R01")
        self.assertEqual(m.tier, "not_fit")
        self.assertEqual([q for r in m.results + m.gates for q in r.questions], [])

    def test_the_question_on_the_rule_that_failed_is_kept(self):
        m = match(engine(), 0, "VMS-901", "R07")                 # can start a week late: the facility is the one to ask
        self.assertTrue(any("account manager" in q for q in rule(m, "start_date").questions))
        self.assertEqual([q for r in m.results if r.rule != "start_date" for q in r.questions], [])

    def test_someone_who_asked_for_no_contact_is_asked_nothing(self):
        for job, who in (("VMS-901", "C4"), ("VMS-902", "C4"), ("VMS-901", "R11")):
            m = match(engine(), 0, job, who)
            self.assertEqual(m.action["status"], "blocked")
            self.assertFalse([q for r in m.results + m.gates for q in r.questions if q.startswith("Ask " + m.name.split()[0])])

    def test_a_no_contact_request_that_outlasts_the_start_says_so(self):
        gate = rule(match(engine(), 0, "VMS-901", "R11"), "contact_permission")
        self.assertTrue(any("lasts past this job's start (1 Dec 2026)" in n for n in gate.notes))

    def test_no_score_is_sent_for_someone_ruled_out(self):
        sent = next(s for s in engine().payload(0)["shortlists"] if s["job"] == "VMS-902")
        by = {m["person"].split(":")[-1]: m for m in sent["matches"]}
        self.assertEqual(by["C3"]["score"], 94)
        self.assertIsNone(by["R01"]["score"])
        self.assertFalse(any("license" in q for m in sent["matches"] if m["tier"] == "Not a fit" for q in m["open_questions"]))

    def test_people_with_different_scores_are_not_called_identical(self):
        rows = {m.person.split(":")[-1]: m for m in engine().matches(0)["job:VMS-902"][0]}
        self.assertNotEqual(rows["R10"].score, rows["C2"].score)
        self.assertEqual((rows["R10"].identical, rows["C2"].identical), (1, 1))

    def test_the_licence_question_uses_the_right_article(self):
        qs = [q for who in ("C2", "C3") for q in match(engine(), 0, "VMS-901", who).questions] + \
             [q for r in match(engine(), 0, "VMS-903", "R01").results for q in r.questions]
        self.assertFalse(any(" a Oregon" in q or " a Ohio" in q for q in qs))
