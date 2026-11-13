# Evaluation

The packet has 16 people and 4 jobs and says it is not an answer key, so this makes no accuracy claim. It shows what the tool does in each situation the data sets up, beside what a simple matcher does on the same files; lists the tests behind the behaviour; and says what it cannot do.

## Why the results are useful

The middle column is **run, not assumed**: `python3 -m shortlist baseline` is a small field-and-keyword matcher (profession, preferred-state and status fields as filters, then a count of the job's requirement words in the person's text). It is not part of the product. Default settings, 18:00 snapshot.

| Situation in the data | Field-and-keyword matcher (run) | Tracefit |
|---|---|---|
| Home-health job: thirteen physical therapists | All thirteen score 4 of 4 words and cannot be told apart | Ten pass, two need an answer, with scores from 81 to 93; R07 and R12 are ruled out |
| Blair: the résumé is outpatient only and says "No home-health, OASIS, or Homecare Homebase detail supplied" | 4 of 4 words, level with the best: it counts the words in the sentence that says they are missing | **Needs an answer** on home health and OASIS (unknown, not failed); goes through recruiter R2 |
| Alex: a March record says Oregon only, no travel; a November message says Spokane is fine | Removed by the preferred-state field | **Match** (93), the old record shown as replaced |
| Casey: status "placed", but a message says the assignment is Monday to Wednesday and offers both weekend shifts | Removed by the status field | **Match** (94) for the ED job by the other-boards route, flagged to check, through recruiter R3 |
| Drew: preferred board, declined both dates, asked for no contact | The only person returned for the ED job, ranked first | **Not a fit** for these dates and **do not contact** |
| R07 can only start a week late; R12 withdrew Spokane | Both kept, 4 of 4 | **Not a fit**, each with its reason |
| R11 asks for no contact in a message; the restriction field is empty | Kept, 4 of 4 | **Do not contact** until 15 Jan 2027, listed last |
| R05 says they have not used Homecare Homebase; the profile lists it | Counted as having it | The later denial is used; skills drop |
| R04 says three years; the profile shows 18 months | Not read | Passes on the note's one-year minimum, loses evidence points, and the claim becomes the question to ask |
| Feed: two years and Homecare Homebase required. Later account-manager note: one year, trainable | Reads neither | Shows both with sources; uses the note, flags every result that relies on it, asks for confirmation before submitting |
| R01, R02, R03 differ only by id and email | In id order, with ten others | Grouped; order marked arbitrary; the page says what to ask to tell them apart |
| 18:05: a new outpatient job | Not run on events | Matched at once: Blair first at 96; everyone else 75 or under |
| 18:10: Alex can only start 8 December | Not run on events | One result changes; the notice names the rule and the message |

[WORKED_EXAMPLE.md](WORKED_EXAMPLE.md) takes four of these people through both stages, rule by rule.

## Tests and experiments

`python3 -m unittest discover -s tests` runs 231 tests. The main groups:

1. **Three client profiles on the same data** (bonus: comparing configurations). Home-health job at 18:00:

   | Profile | Pass every required rule | Need an answer | Not a fit |
   |---|---|---|---|
   | Default: use the account manager's note, flagged | 10 | 2 | 4 |
   | Vendor feed only | 0 | 2 | 14 |
   | Strict pay | 8 | 3 | 5 |

   On the page a recruiter can also re-rank with presets or their own weights. Changing weights reorders the list and never moves anyone past a required rule (tested).
2. **Every rule is touched.** Every rule runs for every pair, every outcome each rule can return is reached by a synthetic case, and every one of the 55 text rules fires on the data or a case.
3. **The score separates people.** The ten who pass every required rule score between 81 and 93, each difference traceable to one dimension. The checker confirms each score is the weighted average of its dimensions.
4. **Updates equal a rebuild.** Applying events one at a time gives the same rows, order, totals and messages as rebuilding from scratch, on the sample and all 110 scenarios. Events sharing a timestamp give the same lists in 40 random delivery orders.
5. **Sources and destinations are independent.** Matching imports no adapter; a synthetic fifth source and a new destination added at run time change nobody else's result.
6. **Deterministic.** Two runs give identical output; moving the reference date changes experience arithmetic and ends Drew's no-contact period, as it should.
7. **110 synthetic scenarios** (`python3 tests/scenario_base.py`): 53 matching, 25 failures and fallbacks, 20 events, 12 identity. They reuse the sample's phrasing, so they test logic, not reading.
8. **163 reworded sentences** (`python3 tests/test_rewording.py`). A "no", an "unsure" or a passing mention must never be read as a "yes", and a request not to be contacted must never be missed. On these sentences: 0 unsafe readings, 11 of 11 contact requests caught, 28 of 30 plain yes sentences read. This is a property of these sentences, not of language (see the next section).
9. **Inputs outside the sample** (`tests/test_outside_the_sample.py`): staff-recorded contact restrictions in ten wordings, impossible dates, unknown event types that close a job, two sources reusing an event id, placeholder emails, a profession that contains another ("Physician Assistant"), mistyped settings.
10. **The checker** (`python3 -m shortlist audit`) is clean on all 176 results in the sample, on both other profiles and on all scenarios; twelve planted mistakes are each caught. It checks the product against its own rules.
11. **A recruiter's own criteria**: 37 typed phrases and 22 institutions; five known limits are listed (a different word, an abbreviation, initials).
12. **Size and workers.** Compact rows give exactly the list and explanations of full ones; lists built on 2, 3 and 5 worker processes are identical to the one-process list. Timings are in [ARCHITECTURE.md](ARCHITECTURE.md#scale-latency-and-cost).

## What independent review found

Before submitting, three review passes were run against the finished tool by AI sessions that had not seen it built.

- **Every result against the raw data.** One pass formed its own view of all 16 people against all 4 jobs at the three moments, then compared. It found no wrong contact decision and no wrong fit or not-fit in 176 results. It disagreed on three judgement calls, listed under limitations.
- **Wording outside the sample.** Two passes probed the reader with sentences the sample does not use. About forty were read as a yes that should not have been ("held a Washington license until 2023", "my colleague handles OASIS", "happy to be trained on OASIS"), a staff-recorded "do not contact" was ignored unless it used the sample's phrasing, and "June 31" stopped the tool. These are fixed and are now tests 8 and 9.
- **Earlier rounds** found the same kind of thing: the first version of the reader behaved like a keyword finder (57 unsafe readings on the first sweep), two people sharing an `info@` mailbox were merged, and same-moment messages were settled by arrival order.

The lesson is the same each time: the logic holds up, and the reading of free text is where new wording finds new mistakes.

## Limitations

- **The text reader is pattern-based.** It leaves 76 clauses of the sample unread; they stay visible and are counted. On real data it will misread wordings nobody has tried yet. Known and not fixed: a date range in a sentence is given to every care setting in that sentence; "less than two years" is read as two years; an "except" in a shift offer ("most dates, except December 5") is not understood; a note worded unlike the sample's ("they'll take someone with 12 months") is not read as a requirement change.
- **Job postings are read by fewer patterns than candidate text.** A requirement sentence the reader cannot use is shown on the job as unread; one it half reads may lose a detail.
- **Judgement calls a client may want differently.** Pay above the approved rate lowers the score but keeps R06 a match (the `strict_pay` profile rules R06 out). A start one week late rules Alex and R07 out and raises a question for the account manager. A claim the records do not support costs R04 thirty evidence points, although it is unsupported, not contradicted. Each is a setting or one line.
- **Weights and thresholds are reasoned defaults, not calibrated.** The score ranks and explains; it is not a probability of placement.
- **A typed criterion finds words, not meanings.** "Kids" does not find "pediatric". The card shows the sentence.
- **Nothing is verified.** Licenses, certificates and schedules are what a candidate or a record says.
- **Identity is cautious.** Two records of one person sharing no email or reference stay apart.
- **Experience arithmetic is simple.** Overlapping roles are merged; part-time work and gaps inside a role are not modelled.
- **The page has no automated tests**, and it replays supplied events; it has no form for adding a job or correcting a fact.
- **No fairness review yet.** No rule uses a protected attribute, but an adverse-impact analysis is needed before real use.

## How I would evaluate with the client

1. Run in shadow for a few weeks: compare the top of each list with whom recruiters actually contacted, submitted and placed.
2. Review a sample of unread sentences and misreadings each week, and add them to the sweep.
3. Have recruiters mark each "needs an answer" as useful or noise.
4. Track time from job arrival to first qualified contact, and how often someone shortlisted was blocked by something the tool did not show.
5. Calibrate the dimension weights per division on those outcomes, and check for adverse impact before any threshold becomes policy.
