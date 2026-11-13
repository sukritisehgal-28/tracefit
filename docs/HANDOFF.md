# Handoff

## Assumptions

Each is a setting in `config/default.toml` or one named choice in the code.

1. **The clock is fixed** at 7 November 2026. Nothing reads the system clock.
2. **Identity is conservative.** Records join only on an explicit reference or an identical email; never a similar name, a name guessed from an email or résumé, or a shared mailbox like `info@`. **The twelve profiles in our own system (R01–R12) are twelve candidates**, separate from C1–C4: each has its own id and email, no file links them to an ATS candidate (where the sources do mean the same person they say so, as the client database and the 18:10 update do), and their statements differ from one another. They share one background, so they are the similarly qualified case; R01–R03 are level on everything and the page says so. A reference or shared email from the client would merge any that turn out to be copies, with no code change. Two people with the same name stay two people, and the page shows which record each is.
3. **Newest wins by default**, per fact, until the client sets source precedence. The older statement stays visible as replaced.
4. **A bare claim does not override dated history.** "Three years" against a résumé showing 18 months is flagged and costs evidence points.
5. **An account manager's note is a requirement change**: used, flagged on every result that depends on it, until the feed agrees.
6. **"Minimum experience" means years in the job's care setting**, not in the profession.
7. **A recruiter's impression is context, not evidence of a skill.**
8. **Required by default:** profession, setting experience and skills when the posting requires them, license, certificates, location, start date, schedule, shift dates. **Preferred:** pay.
9. **Unknown is not no.** It is a question, counts as half in the score, and widens the range.
10. **Permission never changes fit.** Ownership and contact restrictions change the next action, not the score. A blank restriction field says nothing.
11. **The score ranks; it is not a probability.** Weights are reasoned defaults until calibrated on outcomes.
12. **No source says how many people a job needs**, so no list is cut off; the page opens on the top 10.
13. **A broken source, record or date never stops matching.** It is worked around and listed.
14. **Names have a source.** Stated, else the top of the résumé, else an email that clearly looks like a name, else the record id, and the page says which. The twelve profiles in our own system have no name field and their emails are `r01@…`, so they show as "Candidate R01".
15. **A destination gets the top 200 per job and the totals.**

## Questions for the client

Only questions the data cannot answer, where the answer changes a result. [CLIENT_EMAIL.md](CLIENT_EMAIL.md) is a draft email with the first six; nothing has been sent.

| # | Question | Raised by | Assumed today | A different answer changes |
|---|---|---|---|---|
| 1 | May a recruiter act on an account manager's note before the vendor feed is updated? Would a submission be rejected meanwhile? | Feed `VMS-901` (25 Oct): two years, Homebase required. Note `J1-AM` (5 Nov): one year, trainable | Use it, flag it, confirm before submitting | With the note 10 people qualify for home health; with the feed alone, none |
| 2 | Which source wins when two disagree, fact by fact? | The client lead's discovery note; ATS and VMS disagree on fields | Newest wins; it is now a setting | e.g. "the feed is the authority on start dates" |
| 3 | Does "minimum experience" mean years in the setting or in the profession? | Bare number in the feed; the note says "home-health experience"; Blair has ten outpatient years | In the setting | Blair clears experience; only OASIS stays open |
| 4 | Does a candidate's own claim of experience count toward a minimum? | `R04-UPDATE` "three years" against one employer since May 2025 | Only dated history | Under the feed's two years, R04 alone would qualify |
| 5 | Does a no-contact request take effect at once, without a recruiter confirming it? | `C4-MSG`, `R11-UPDATE`; `C4-OPS` still says `contact_allowed=true` | Yes, at once | Drew and R11 stay contactable until a field is updated |
| 6 | Should other recruiters see someone another recruiter owns, and hand over, or not see them? | `C2-OPS`, `C3-OPS` | Shown, "send to the owner" | Casey would disappear from everyone else's ED list |
| 7 | Is approved pay a ceiling? Is a start date fixed? | R06 asks $50 over; Alex and R07 can start a week late; `J1-NOTE-02` | Pay lowers the score; a late start rules out and raises a question | Strict pay removes R06; a week's tolerance brings Alex and R07 back |
| 8 | For the ED job, who approves a doctor on the "other boards" route, and is one-day cover acceptable? | `J2-NOTE-01` | Met and flagged | Without approval the ED job has no match to submit today |
| 9 | How many people does each job need, and how many may be submitted? | No openings or limit anywhere | Nothing is cut off | The page could say "contact the top 2" and stop once that many are submitted |
| 10 | Does your own system ever hold a second copy of an ATS candidate without a reference to it? | R01–R12 share one profile text; R01–R03 differ only by id and email | No: twelve separate candidates (assumption 2); level people grouped with what to ask each | "10 matches" may be two or three |
| 11 | Can we see past submissions and placements? | Weights are hand-set | Reasoned defaults | Weights calibrated per division on real outcomes |
| 12 | May candidate text leave your systems, for example to a language-model provider? | The optional model reader | Off | It could read the phrasings the rules miss |

## Implemented, simulated, deferred

| Area | Status |
|---|---|
| Five adapters, text reading, identity, source precedence, résumé background | Implemented |
| Gates, six-dimension quality score, tiers, grouping, next action | Implemented |
| Event replay (18:05 new job, 18:10 update) and the one-event-at-a-time service path | Implemented; tested equal to a full rebuild |
| Recruiter page: one ranked list (top 10, then the next 10), rank-by weights, presets, filters and typed criteria, radar, compare, how the list was made (with the whole process on request), coverage, data problems | Implemented |
| Settings profiles (default, vendor feed only, strict pay) and per-division weights | Implemented |
| Checker, scenario base, rewording sweep, worked example kept in step with the tool | Implemented; data **synthetic** |
| Scale benchmark | Implemented on **synthetic** data: speed and memory only |
| Worker processes on one machine (`shortlist/workers.py`) | Implemented and measured in the benchmark; the product runs on one process by default. Queue, database and several machines are designed, not built |
| Destinations: JSON, CSV, notifications | **Simulated**: local files, nothing sent |
| Language-model reader behind a guard | Built, **off, never run against a real model** |
| Database, queue, workers, live connections, sign-in | Designed, not built ([ARCHITECTURE.md](ARCHITECTURE.md#as-a-running-service-designed-not-built)) |

## Onboarding another client

1. Write one adapter per source from a sample export (30 to 80 lines each here) and list sources and destinations in the settings.
2. Add their settings, tools and certificates to the vocabulary: configuration, not code.
3. Run `python3 -m shortlist coverage`: it lists text the reader could not use and résumé skills no rule checks, which shows what to add.
4. Agree the questions above and set them as a division profile: gates, dimension weights, precedence, freshness, target employers.
5. Run `python3 -m shortlist audit` and review a few real jobs with recruiters, using Compare and the presets, before anyone relies on it.
6. Add destination adapters.

A new rule is one function, one registry line and one settings block; the tests fail until it has a case for every outcome.

## How AI was used and checked

The code, tests and documents were written with Claude Code at my direction. I chose the two-stage, rule-based design and set the requirements that every rule be exercised and every point of a score trace to a quote.

How it was checked:

- **The data.** The four JSON files came as PDF printouts; they were rebuilt and compared with the PDF text character for character, and all 37 identifiers the supplement refers to resolve.
- **The behaviour.** 187 tests, 102 scenarios, 124 reworded sentences, and the checker, which re-derives each result's tier and score independently. Several of these found real defects, listed in [EVALUATION.md](EVALUATION.md).
- **The document.** The worked example is generated by the tool, and a test fails if it drifts.
- **The page.** Each view, the replay, the rank-by bar, Compare and the settings switch were exercised in a browser, at desktop and phone width, light and dark, with no script errors.
- **The numbers.** Every scale figure comes from running the benchmark, labeled synthetic, with the machine's state stated.

Nothing in the running product calls a model by default.

> Before submitting, add one or two sentences here on what you reviewed yourself.
