# Evaluation

The packet has 16 people and 4 jobs and says it is not an answer key, so this makes no accuracy claim. It shows the tool does the sensible thing in every situation the data sets up, proves the behaviours with tests, and says plainly what it cannot do.

## Why the results are useful

In each case a matcher that trusts structured fields and keywords gives a confident wrong answer. Default settings, 18:00 snapshot, unless a time is given.

| Situation in the data | Field-and-keyword matcher | Tracefit |
|---|---|---|
| Blair fits every structured field; the résumé is outpatient only and home health was asked about, unanswered | Ranks Blair first | **Needs an answer** on home health and OASIS (unknown, not failed); goes through recruiter R2 |
| Alex's March record says Oregon only, no travel; a November message and the résumé say otherwise | Excludes Alex | **Match** (93), the old record shown as replaced |
| Feed: two years and Homecare Homebase required. Later account-manager note: one year, trainable | Uses whichever it read last | Shows both with sources; uses the note, flags every result that relies on it, asks for confirmation before submitting |
| Casey is "placed", but a message says the assignment is Monday to Wednesday and offers both weekend shifts | Excludes Casey | **Match** for the ED job, by the other-boards route, through recruiter R3 |
| Drew has the preferred board, declined both dates, asked for no contact; an older field says contact allowed | Ranks Drew first | **Not a fit** for these dates and **do not contact**; the stale field shown as replaced |
| R01, R02, R03 differ only by id and email | Orders them by accident, or invents a difference | Grouped; order marked arbitrary; the page says their documents are the same words and what to ask to tell them apart |
| Twelve profiles share one profile text, each with its own id and email | Merges them on similar text, or ranks them by accident | Twelve separate candidates, joined to nobody (no reference or email links them); the shared text is stated once under data problems |
| R04 says three years; the profile shows 18 months | Believes one | Passes on the note's one-year minimum but loses 30 evidence points, so ranks below R01; the claim becomes the question to ask |
| R05 says they have not used Homecare Homebase; the profile lists it | Counts it | Uses the later denial; skills drop |
| R11 asks for no contact; the restriction field is empty | Contactable | **Do not contact** until 15 Jan 2027, listed last |
| R08 scores 87 but one answer is missing | Dropped, or shown as a full match | Ninth of twelve, marked **Needs an answer**, with the question to ask |
| "No home-health, OASIS, or Homecare Homebase detail supplied" | Three skills found | Three unanswered questions |
| Full résumés repeat the extracts; notes repeat messages | Two or three sources | One source, repeats linked |
| 18:05: a new outpatient job | — | Matched at once: Blair is first at 96; everyone else is 75 or under |
| 18:10: Alex can only start 8 December | — | One result changes; the banner names the rule, the message, and the note that no exception is confirmed |

[WORKED_EXAMPLE.md](WORKED_EXAMPLE.md) takes four of these people through both stages, rule by rule and dimension by dimension.

## Experiments

Run everything with `python3 -m unittest discover -s tests`: 187 tests.

1. **Three client profiles on the same data** (the bonus: comparing configurations). Home-health job at 18:00:

   | Profile | Pass every required rule | Need an answer | Not a fit | What changed |
   |---|---|---|---|---|
   | Default: use the account manager's note, flagged | 10 | 2 | 4 | — |
   | Vendor feed only | 0 | 2 | 14 | Nobody has two documented years; R05 fails on Homecare Homebase |
   | Strict pay, no credit for unknowns | 8 | 3 | 5 | R06 is out ($50 over); R09 (no pay given) needs an answer |

   On the page, a recruiter can also re-rank with presets or their own weights, live: changing weights reorders the list and never moves anyone past a gate (tested). A typed criterion finds who says it and shows the sentence; "no … detail supplied" does not count as a mention (tested).

2. **Every rule is touched.** Every rule runs for every pair; every outcome each rule can return is reached by a synthetic case; every one of the 55 text rules fires on the data or a case.
3. **The quality score separates people the old score tied.** Under the default weights the ten people who pass every required rule score between 81 and 93, each difference traceable to one dimension; the score is checked to be exactly the weighted average of its dimensions.
4. **Updates equal a rebuild.** Applying events one at a time gives the same rows, order, totals and messages as rebuilding from scratch, on the sample and all 102 scenarios; after a candidate update every other row is untouched.
5. **Sources and destinations are independent.** Matching imports no adapter; a synthetic fifth source and a new destination added at run time change nobody else's result.
6. **Deterministic.** Two runs give byte-identical output; moving the reference date changes experience arithmetic and ends Drew's no-contact period, as it should.
7. **102 synthetic scenarios** (`python3 tests/scenario_base.py`): 53 matching, 25 failures and fallbacks, 12 events, 12 identity (including three people called Maria Lopez: no email, two systems, and one person under a married name). Writing the last batch found five real defects, all fixed: a denial worded unusually was read as a yes; two people sharing an `info@` mailbox were merged; an unknown feed event became a job update; a record dated after its own export was trusted; a reopened job was announced as nonsense.
8. **124 reworded sentences** (`python3 tests/test_rewording.py`). A "no" or "unsure" must never be read as a "yes", and a request not to be contacted must never be missed. The first run found **57 unsafe readings and 10 of 11 contact requests missed**: the reader had worked like a keyword finder ("My Washington license lapsed" counted as a license). Now: 0 unsafe, 11 of 11 caught, 28 of 30 plain yes sentences still read, the sample unchanged.
9. **The model reader's guard** against stand-in models that say yes to everything, invent quotes, quote true words about something else, disagree, or crash: no false yes, no contact request lifted, nobody's tier raised on the sample. This tests the guard, not a model.
10. **The checker** (`python3 -m shortlist audit`) is clean on all 176 results in the sample, both overlays, all scenarios and with the lying model on; twelve planted mistakes are each caught by their own check.
11. **A recruiter's own criteria** (`python3 tests/test_own_criteria.py`): 37 typed phrases and 22 institutions against synthetic résumés in layouts the sample does not use. Writing it found that the first version matched letters, not words ("art" matched "start"); it now matches whole words whatever their case, accents, hyphens or plural. Five cases are listed as known limits: a different word, an abbreviation or initials are not found.
12. **Size.** Compact rows give exactly the list, totals and explanations of full ones; pages add up to the list; the first response is the same size for 1 person as for 300. Measuring at size found two defects sixteen people could not show: loading grew with the square of the records (now linear), and half of shortlist time was memory clean-up (now paused). Timings are in [ARCHITECTURE.md](ARCHITECTURE.md#scale-latency-and-cost).
13. **Worker processes.** Lists built on 2, 3 and 5 worker processes are identical to the one-process list, card for card and total for total. At 20,000 synthetic people a new job takes 0.78 s on one worker and 0.30 s on four. The first run was slower with workers: one date-formatting call was four times slower in a forked process, now replaced. One machine only; no queue or database is involved.

## Limitations

- **The text reader is pattern-based.** It leaves 76 clauses of the sample unread (headings, hedges, context); they stay visible and are counted. Experiment 8 shows unusual wording is left unread rather than misread, not that the reader understands it.
- **Weights and thresholds are reasoned defaults, not calibrated.** The score ranks and explains; it is not a probability of placement. Calibration needs submission and placement outcomes.
- **A typed criterion finds words, not meanings.** "Kids" does not find "pediatric". The card shows the sentence, so a recruiter sees what matched.
- **Nothing is verified.** Licenses, certificates and schedules are what a candidate or a record says.
- **Identity is cautious.** Two records of one person sharing no email or reference stay apart.
- **Experience arithmetic is simple.** Overlapping roles are merged; part-time work and gaps inside a role are not modelled.
- **Two date-only facts on the same day** cannot be ordered.
- **No fairness review yet.** No rule uses a protected attribute, but an adverse-impact analysis is needed before real use.

## How I would evaluate with the client

1. Run in shadow for a few weeks: compare the top of each list with whom recruiters actually contacted, submitted and placed.
2. Have recruiters mark each "needs an answer" as useful or noise, and each unread clause that mattered.
3. Track time from job arrival to first qualified contact, and how often someone shortlisted was blocked by something the tool did not show.
4. Calibrate the dimension weights per division on those outcomes, and check for adverse impact before any threshold becomes policy.
