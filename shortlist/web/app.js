"use strict";
// Recruiter page. Plain JavaScript, no build step.
// The main view shows only what a recruiter needs: jobs, people, one reason, one next step.
// Evidence opens on click. The sample replay and the settings live behind the Demo button.
//
// The page never holds a whole shortlist. /api/state gives the jobs and their totals; people arrive a
// screenful at a time from /api/cards; one person's explanation arrives from /api/detail when opened.
// So the page behaves the same for sixteen people or a hundred thousand.

const S = { step: 0, overlay: "", data: null, job: null, filter: "best", details: false, person: null,
            openRules: new Set(), showNA: false, compare: new Map(), comparing: false, pop: null, page: "jobs",
            toasts: [], published: null,
            lists: {},          // group -> { items, total } for the job on screen
            opened: {},         // "job|person" -> { match, person } fetched on demand
            evidence: {},       // evidence items fetched so far, by id
            coverage: null,
            weights: null,      // the recruiter's own weights for this job, or null for the configured ones
            preset: "",         // the preset those weights came from, if any
            flt: new Set(),     // the recruiter's filters for this job
            everything: false };  // "Show everything" in the person panel

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");
const hhmm = (iso) => { const d = new Date(iso); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
function when(iso, dateOnly) {
  const d = new Date(iso);
  const day = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  return dateOnly ? day : `${day}, ${hhmm(iso)} UTC`;
}

const OUT = {
  met: { i: "✓", label: "Met" }, not_met: { i: "✕", label: "Not met" }, unknown: { i: "?", label: "Unknown" },
  na: { i: "–", label: "Does not apply" }, clear: { i: "✓", label: "Clear" }, route: { i: "→", label: "Go through the owner" },
  blocked: { i: "✕", label: "Blocked" }, confirm: { i: "!", label: "Confirm first" },
};
const TIER_ICON = { strong: "✓", possible: "○", clarify: "?", not_fit: "✕" };
const FLAG = {
  provisional: "relies on a note not yet in the feed", pending_clarification: "a pending note would change this",
  disputed: "conflicting claim", unread_update: "a newer message could not be read", same_moment: "two statements at the same moment disagree", replaced: "newer statement used", stale: "old information", alternative_route: "other-boards route",
};
const KIND = {
  structured_record: "record", resume: "résumé", profile: "imported profile", candidate_message: "candidate message",
  candidate_statement: "candidate statement", recruiter_note: "recruiter note", ops_note: "operations note",
  job_feed: "vendor feed", job_record: "job record", job_note: "job note",
};
// One list per job. People who need an answer are on it, placed by score. People a required rule rules out
// are kept off it and reachable from "How this list was made".
const groupOf = (tier) => (tier === "not_fit" ? "not_fit" : "best");

const ev = (id) => S.evidence[id];
const num = (n) => Number(n).toLocaleString("en-US");
const detailOf = (person) => S.opened[S.job + "|" + person];
const srcOf = (ref) => { const e = ev(ref.evidence); return `<span class="src">${esc(e.label)} · ${esc(e.source)} · ${when(e.at, e.date_only)}</span>`; };
const flagTag = (f) => (FLAG[f] ? `<span class="tag">${esc(FLAG[f])}</span>` : "");
const currentJob = () => S.data.jobs.find((j) => j.key === S.job);
// A person no source names is shown by record id, so the avatar shows the id, not made-up initials.
const initials = (m) => (m.name_basis === "id" ? m.name.replace(/^Candidate\s+/, "") : m.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase());
const NAME_TAG = { id: ["no name on file", "The sources hold an id and an email for this person, but no name"],
                   resume: ["name taken from résumé", "No source states this person's name. It is read from the top of their résumé"],
                   email: ["name taken from email", "No source states this person's name. It is read from their email address and may be wrong"] };
const nameTag = (basis) => (NAME_TAG[basis] ? `<span class="tag" title="${esc(NAME_TAG[basis][1])}">${NAME_TAG[basis][0]}</span>` : "");
// The only label a card carries is the one a recruiter must act on: an answer is missing.
const badge = (m) => (m.tier === "clarify" ? `<span class="badge clarify"><span class="i" aria-hidden="true">?</span>Needs an answer</span>` : "");

function countsText(j) {
  const n = j.groups.best, ask = j.need_answer;
  if (!n) return "Nobody on the list yet";
  return `${num(n)} on the list` + (ask ? ` · ${num(ask)} need${ask > 1 ? "" : "s"} an answer` : "");
}

function nextLine(m) {                       // the single next step shown on a card
  const st = m.action.status;
  if (st === "ready") return ["✓", "Can reach out"];
  if (st === "route") return ["→", `Send to ${m.action.owner}`];
  if (st === "clarify") return ["?", "Ask first"];
  if (st === "blocked") { const d = /until (.+)$/.exec(m.action.label); return ["✕", "Do not contact" + (d ? ` until ${d[1]}` : "")]; }
  return ["–", st === "closed" ? "Job closed" : "No action"];
}

function docText(e, quote) {
  const t = e.text || "", i = quote ? t.indexOf(quote) : -1;
  return i < 0 ? esc(t) : esc(t.slice(0, i)) + "<mark>" + esc(quote) + "</mark>" + esc(t.slice(i + quote.length));
}

function cite(ref) {
  const e = ev(ref.evidence);
  const field = / = |^skills: |^requirements: /.test(ref.quote);
  const quote = field ? `<code>${esc(ref.quote)}</code>` : `<span class="q">“${esc(ref.quote)}”</span>`;
  const more = !field && e.text && e.text.trim() !== ref.quote.trim()
    ? `<details ${S.everything ? "open" : ""}><summary>Read the full ${esc(KIND[e.kind] || "source")}</summary><div class="doc">${docText(e, ref.quote)}</div></details>` : "";
  return `<div class="cite${ref.note ? " replaced" : ""}">${quote}${ref.note ? '<span class="tag">replaced</span>' : ""}<br>${srcOf(ref)}${more}</div>`;
}

function fileBlock(id) {
  const e = ev(id);
  const same = e.same_as ? `<span class="tag">same as ${esc(ev(e.same_as).local_id)}</span>` : "";
  const repeats = e.repeats.length ? `<span class="tag">repeats ${esc(e.repeats.map((r) => ev(r).local_id).join(", "))}</span>` : "";
  const body = e.text ? `<details><summary>Read</summary><div class="doc">${esc(e.text)}</div></details>` : "";
  return `<div class="file"><b>${esc(e.label)}</b>${same}${repeats}<div class="src">${esc(e.source)} · ${esc(KIND[e.kind] || e.kind)} · ${when(e.at, e.date_only)}</div>${body}</div>`;
}

// ------------------------------------------------------------------ loading
async function api(path, params = {}) {
  const q = new URLSearchParams({ step: S.step, overlay: S.overlay, ...params });
  let res, body;
  try {
    res = await fetch(`/api/${path}?${q}`);
    body = await res.json();
  } catch (err) {
    body = { error: "The page could not reach the tool. Is it still running?" };
  }
  if (!res || !res.ok || body.error) throw new Error(body.error || "The tool could not answer.");
  return body;
}

function fail(err) {
  $("#main").innerHTML = `<section class="card empty"><h2>Something went wrong</h2><p>${esc(err.message)}</p>
    <button class="btn" data-act="retry">Try again</button></section>`;
}

// The jobs and their totals. Everything fetched for the previous step or settings profile is dropped.
async function load(announce) {
  try {
    S.data = await api("state");
    S.lists = {}; S.opened = {}; S.coverage = null; S.compare.clear();
    S.evidence = { ...S.data.evidence };
    const jobs = S.data.jobs;
    if (!jobs.some((j) => j.key === S.job)) S.job = (jobs.find((j) => j.status === "open") || jobs[0]).key;
    S.toasts = announce ? S.data.changes.map(toastFor) : [];
    await showList();
    if (S.person && !(await open(S.person, false))) S.person = null;
    if (S.page === "coverage") S.coverage = await api("coverage");
    render();
  } catch (err) { fail(err); }
}

// One screenful of the list on screen. `more` adds the next screenful to what is already shown.
async function showList(more) {
  const job = currentJob();
  if (!job || job.status !== "open") return;
  const have = S.lists[S.filter];
  if (have && !more) return;
  const extra = {};
  if (S.weights) extra.w = Object.entries(S.weights).map(([k, v]) => `${k}:${v}`).join(",");
  if (S.flt.size) extra.f = [...S.flt].join("|");
  const page = await api("cards", { job: S.job, group: S.filter, offset: have ? have.items.length : 0, limit: S.data.meta.top_n, ...extra });   // ten at a time
  S.lists[S.filter] = { items: (have ? have.items : []).concat(page.cards), total: page.total, tiers: page.tiers };
}

// One person's full explanation, fetched the first time it is needed. False if they are not on this job's list.
async function open(person, show = true) {
  const key = S.job + "|" + person;
  if (!S.opened[key]) {
    try {
      const d = await api("detail", { job: S.job, person });
      Object.assign(S.evidence, d.evidence);
      S.opened[key] = { match: d.match, person: d.person };
    } catch (err) { return false; }
  }
  if (show) S.person = person;
  return true;
}

function toastFor(c) {
  const job = S.data.jobs.find((j) => j.key === c.job);
  if (c.kind === "job_added") return { title: "New job posted", text: `${job.title} · ${job.place}. ${countsText(job)}.`, job: c.job, person: null };
  if (c.kind === "job_closed") return { title: "Job closed", text: c.text, job: c.job, person: null };
  const change = (c.text.split("): ")[1] || c.text).replace(/ \(\d+\)/g, "");
  const reason = c.rules[0] ? c.rules[0].split(". ").slice(1).join(". ") : "";
  const title = c.kind === "person_added" ? `${c.name} is new on this list` : `${c.name} sent an update`;
  return { title, text: `${c.title}: ${change} ${reason}`, job: c.job, person: c.person, tier: c.tier, notMet: c.not_met || [] };
}

function render() {
  $("#activity-n").textContent = S.data.outbox.length || "";
  renderJobs();
  $("#main").innerHTML = S.page === "coverage" ? coverageView() : S.page === "plumbing" ? plumbingView() : jobView(currentJob());
  $("#drawer").innerHTML = drawer();
  $("#pop").innerHTML = popover();
  const sel = $("#overlay");
  if (sel) sel.value = S.overlay;
  $("#comparebar").innerHTML = S.compare.size && !S.comparing ? `<div class="bar"><span>${S.compare.size} selected</span>
      <button class="btn" data-act="compare" ${S.compare.size < 2 ? "disabled" : ""}>Compare</button>
      <button class="linkish" data-act="compare-clear">Clear</button></div>` : "";
  $("#modal").innerHTML = S.comparing ? compareModal() : "";
  renderToast();
}

function renderToast() {
  $("#toast").innerHTML = S.toasts.map((t, i) => `<div class="toast"><b>${esc(t.title)}</b>${esc(t.text)}
      <div class="acts"><button class="linkish" data-act="toast-view" data-i="${i}">View</button>
      <button class="linkish" data-act="toast-x" data-i="${i}">Dismiss</button></div></div>`).join("");
}

// --------------------------------------------------------------------- jobs
function renderJobs() {
  $("#jobs").innerHTML = `<div class="card"><div class="side-title">Jobs</div>${S.data.jobs.map((j) => `
    <button class="job ${j.key === S.job && S.page === "jobs" ? "on" : ""}" data-act="job" data-key="${esc(j.key)}">
      <div class="t">${esc(j.title)}${j.new ? '<span class="new">New</span>' : ""}</div>
      <div class="l">${esc([j.facility, j.place].filter(Boolean).join(" · "))}</div>
      <div class="f">${j.status === "open" ? esc(countsText(j)) : "Filled"}</div>
    </button>`).join("")}</div>`;
}

function reqLine(l) {
  const mark = l.status === "provisional" ? '<span class="tag">in use, not yet in the feed</span>'
    : l.status === "pending" ? '<span class="tag">feed value in use</span>' : "";
  const other = l.other ? `<div class="other">${esc(l.other.role)}${l.status === "pending" ? " (not used)" : " still says"}: ${esc(l.other.text)}<br>${srcOf(l.other.ref)}</div>` : "";
  return `<div class="k">${esc(l.label)}</div><div>${esc(l.text)}${mark}${l.ref ? "<br>" + srcOf(l.ref) : ""}${other}</div>`;
}

// One small block per rule the job checks, in the same order on every card, so two people can be told
// apart by looking down the column. The words beside it say the same thing for anyone who cannot see colour.
const MARK = { met: "met", caution: "met, with something to check", unknown: "not known yet", not_met: "not met" };
const ACTIONABLE = ["ready", "route", "clarify"];
function strip(m, job) {
  const cells = (job.rule_labels || []).map((lab, i) => [lab, m.marks[i]]).filter(([, o]) => MARK[o]);
  if (!cells.length) return "";
  const n = (o) => cells.filter(([, x]) => x === o).length;
  const met = n("met") + n("caution");
  const rest = [[n("not_met"), "not met"], [n("unknown"), "unknown"], [n("caution"), "to check"]].filter(([k]) => k).map(([k, t]) => `${k} ${t}`);
  const text = rest.length ? `${met} of ${cells.length} met · ${rest.join(" · ")}` : `All ${cells.length} rules met`;
  return `<div class="strip" role="img" aria-label="${esc(text)}">${cells.map(([lab, o]) => `<i class="${o}" title="${esc(lab)}: ${esc(MARK[o])}"></i>`).join("")}</div>
      <span class="scorenum">${esc(text)}</span>`;
}

function card(m, job, twin, rank) {
  const [icon, text] = nextLine(m);
  const same = m.identical > 1 && !twin ? `<span class="tag">same result as ${num(m.identical - 1)} other${m.identical > 2 ? "s" : ""}</span>` : "";
  const watch = m.watch.length ? `<div class="why watch"><span class="i" aria-hidden="true">!</span><span>${esc(cap(m.watch.join(" · ")))}</span></div>` : "";
  const good = m.good.length ? `<div class="why">${esc(cap(m.good.join(" · ")))}</div>` : "";
  const range = m.low !== m.high ? ` · ${m.low} to ${m.high}` : "";
  const score = m.tier === "not_fit" ? "" : `<span class="scorenum" title="${m.low !== m.high ? "The score would fall between these once the open questions are answered" : ""}">${m.reweighted ? "Your weights: " : "Score "}${m.score}${range}</span>`;
  return `<article class="cand${twin ? " twin" : ""}" data-act="person" data-person="${esc(m.person)}" tabindex="0">
      <div class="avatar" aria-hidden="true">${esc(initials(m))}</div>
      <div>
        <div class="name"><span class="rank">${num(rank)}</span>${esc(m.name)}${same}${m.namesakes ? `<span class="tag" title="${num(m.namesakes)} other ${m.namesakes > 1 ? "people have" : "person has"} this name. They are different people: no record links them.">${esc(m.ref)} · same name as ${num(m.namesakes)} other${m.namesakes > 1 ? "s" : ""}</span>` : ""}</div>
        <div class="head">${esc([m.headline, m.home].filter(Boolean).join(" · "))}</div>
        ${watch}${good}${watch || good ? "" : `<div class="why">${esc(m.summary.split(". ")[0])}</div>`}
        ${m.key && m.tier !== "not_fit" ? `<div class="keyev"><span class="q">“${esc(m.key.quote.length > 110 ? m.key.quote.slice(0, 108) + "…" : m.key.quote)}”</span> <span class="muted">${esc(m.key.source)}</span></div>` : ""}
        ${(m.found || []).map((f) => `<div class="keyev"><b>“${esc(f.phrase)}”</b> <span class="q">“${esc(f.quote.length > 110 ? f.quote.slice(0, 108) + "…" : f.quote)}”</span> <span class="muted">${esc(f.source)}</span></div>`).join("")}
        ${(m.links || []).length ? `<div class="keyev" data-stop="1">${m.links.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https?:\/\//, "").slice(0, 48))}</a>`).join(" · ")}</div>` : ""}
        ${m.also && m.also.length ? `<div class="also">Also on résumé: ${esc(m.also.slice(0, 3).join(" · "))}${m.also.length > 3 ? ` +${m.also.length - 3}` : ""}</div>` : ""}
        <div class="do ${m.action.status}"><span class="i" aria-hidden="true">${icon}</span>${esc(text)}</div>
      </div>
      <div class="right">${m.tier === "not_fit" ? "" : badge(m)}${score}${strip(m, job)}
        <label class="pick" data-stop="1"><input type="checkbox" data-act="pick" data-person="${esc(m.person)}" ${S.compare.has(m.person) ? "checked" : ""}>Compare</label>
      </div>
    </article>`;
}

// The cards of one list, with two kinds of divider: people who must not be contacted (listed last among the
// matches), and runs of people whose result is the same on every rule.
// The line above a run of people who are level on every rule: say so, say whether their documents are even
// the same words, and name what is missing for all of them that would tell them apart. Nothing is invented,
// and they stay separate people: each has its own record.
function sameAs(list, i) {
  const run = [];
  for (let k = i; k < list.length && list[k].same === list[i].same; k++) run.push(list[k]);
  const m = list[i], sameDocs = m.docs != null && run.every((x) => x.docs === m.docs);
  const ask = (m.apart || []).filter((d) => run.every((x) => (x.apart || []).some((y) => y.key === d.key))).map((d) => esc(d.text));
  return `<div class="sameas"><b>${num(m.identical)} people have the same result on every rule</b>${sameDocs ? ", and their documents are word for word the same" : ""}.
      Nothing on file tells them apart, so their order is arbitrary.
      ${ask.length ? `<span class="ask">To tell them apart, ask each for: ${ask.join(" · ")}.</span>` : ""}</div>`;
}

function cards(list, job) {
  let html = "", cut = false;
  const held = Object.entries(job.best_actions).filter(([st]) => !ACTIONABLE.includes(st)).reduce((a, [, k]) => a + k, 0);
  list.forEach((m, i) => {
    const above = list[i - 1], below = list[i + 1];
    if (S.filter === "best" && job.action_first && !cut && !ACTIONABLE.includes(m.action.status)) {
      cut = true;
      html += `<div class="sect">Must not be contacted <span>${num(held)}</span><small>They meet every required rule, and are listed last because a recruiter cannot act on them.</small></div>`;
    }
    const joinsAbove = m.same != null && above && above.same === m.same, joinsBelow = m.same != null && below && below.same === m.same;
    if (joinsBelow && !joinsAbove) html += sameAs(list, i);
    html += card(m, job, joinsAbove || joinsBelow, i + 1);
  });
  return html;
}

// How the list was made, under the list: the three counts, the points that decided it, the weights as a
// web, and (on request) every step from the sources to the next action.
const ACTION_WORD = { ready: "can be contacted now", route: "go through the recruiter who owns the relationship", clarify: "need a question answered first",
  blocked: "must not be contacted", closed: "are for a closed job", none: "need no action" };
const LEVEL_WORD = { hard: "Required", from_job: "Required when the job asks for it", soft: "Scored, never rules anyone out" };
const TIE_WORD = { action_readiness: "who can be acted on sooner", fewer_open_questions: "who has fewer open questions", most_recent_candidate_contact: "who was heard from more recently" };

function weightsWeb(job, size = 230) {
  const w = currentWeights(), dims = S.data.meta.dimensions, total = dims.reduce((a, d) => a + (w[d.name] || 0), 0) || 1;
  const share = (d) => Math.round(((w[d.name] || 0) / total) * 100), top = Math.max(...dims.map(share), 1);
  const desc = "Weights: " + dims.map((d) => `${d.label} ${share(d)}%`).join(", ");
  return radar([{ name: "Weights", values: dims.map((d) => (share(d) / top) * 100) }], dims.map((d) => ({ label: `${d.label} ${share(d)}%` })), size, desc);
}

function processView(job) {
  const p = job.process || { rules: [], gates: [] }, meta = S.data.meta, w = currentWeights();
  const total = meta.dimensions.reduce((a, d) => a + (w[d.name] || 0), 0) || 1;
  const used = p.rules.filter((r) => r.met + r.unknown + r.not_met > 0);
  const cell = (n, cls) => `<td class="num ${n ? cls : "zero"}">${num(n)}</td>`;
  const rules = used.map((r) => `<tr><td>${esc(r.label)}<small>${LEVEL_WORD[r.level] || "Required"}</small></td>${cell(r.met, "")}${cell(r.unknown, "warn")}${cell(r.not_met, "bad")}</tr>`).join("");
  const unused = p.rules.filter((r) => !used.includes(r)).map((r) => r.label.toLowerCase());
  const dims = meta.dimensions.map((d) => `<tr><td>${esc(d.label)}</td><td class="num">${Math.round(((w[d.name] || 0) / total) * 100)}%</td><td>${esc(d.means)}</td></tr>`).join("");
  const acts = Object.entries(job.best_actions).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<li><b>${num(n)}</b> ${ACTION_WORD[k] || esc(k)}</li>`).join("");
  const ties = (job.order.tie_breakers || []).map((t) => TIE_WORD[t] || t).join(", then ");
  const st = job.stats || { people: 0, evaluated: 0 };
  return `<ol class="process">
      <li><b>Read.</b> ${num(meta.sources.length)} sources are read as they are: ${meta.sources.map((s) => esc(s.label)).join(", ")}. Every fact keeps its source, its date and the words it came from. ${num(st.people)} people are on file.</li>
      <li><b>Check the required rules.</b> ${num(st.evaluated)} people are checked against each rule this job uses.
        <div class="tablewrap"><table class="grid tight"><thead><tr><th>Rule</th><th class="num">Met</th><th class="num">No answer yet</th><th class="num">Not met</th></tr></thead><tbody>${rules}</tbody></table></div>
        <span class="muted small">A rule not met means not a fit. No answer yet means needs an answer.${unused.length ? ` This job does not use: ${esc(unused.join(", "))}.` : ""}</span></li>
      <li><b>Score.</b> Everyone on the list gets six marks out of 100, combined with the weights${S.weights ? " you set" : ""}.
        <div class="tablewrap"><table class="grid tight"><thead><tr><th>Mark</th><th class="num">Weight</th><th>100 means</th></tr></thead><tbody>${dims}</tbody></table></div></li>
      <li><b>Order.</b> Highest score first. Scores within ${num(job.order.tie_margin)} points are marked comparable${ties ? `; between them the order goes to ${esc(ties)}` : ""}. People level on everything are grouped and their order is arbitrary. Anyone who must not be contacted is listed last.</li>
      <li><b>Next action.</b> Permission is checked separately from fit: is the job open, may the person be contacted, who owns the relationship. Of the ${num(job.groups.best)} on the list:<ul>${acts}</ul></li>
    </ol>`;
}

function flowView(job) {
  const st = job.stats || { people: 0, evaluated: 0, excluded_by_profession: 0 };
  const g = job.groups, a = job.best_actions, r = job.reasons || {};
  const why = (t) => Object.entries(r[t] || {}).sort((x, y) => y[1] - x[1]).map(([k, n]) => `${esc(k.toLowerCase())} ${num(n)}`).join(" · ");
  const pass = g.best - job.need_answer, held = (a.blocked || 0) + (a.none || 0);
  const aside = st.excluded_by_profession ? `<small>${num(st.excluded_by_profession)} in other professions were set aside first</small>` : "";
  const out = g.not_fit ? `<div class="fnode"><b>${num(g.not_fit)}</b><span>not a fit: a required rule is not met</span><small>${why("not_fit")}</small>
      <button class="linkish" data-act="filter" data-filter="not_fit">See them</button></div>` : "";
  const used = ((job.process || {}).rules || []).filter((x) => x.level !== "soft" && x.met + x.unknown + x.not_met > 0).map((x) => x.label.toLowerCase());
  const w = currentWeights(), dims = S.data.meta.dimensions, total = dims.reduce((s, d) => s + (w[d.name] || 0), 0) || 1;
  const heavy = dims.slice().sort((x, y) => (w[y.name] || 0) - (w[x.name] || 0)).slice(0, 2).map((d) => `${d.label.toLowerCase()} ${Math.round(((w[d.name] || 0) / total) * 100)}%`);
  const versions = job.requirements.filter((q) => q.status === "provisional" && q.other).map((q) =>
    `<li><b>${esc(q.label)}:</b> using “${esc(q.text)}” from the newer note. ${esc(q.other.role)} still says “${esc(q.other.text)}”.</li>`).join("");
  const points = [
    used.length ? `<li><b>Who is on the list</b> is decided by the required rules, never by weights: ${esc(used.join(", "))}.</li>` : "",
    job.need_answer ? `<li><b>${num(job.need_answer)} need an answer</b> because a required rule has no answer yet: ${why("clarify")}.</li>` : "",
    g.not_fit ? `<li><b>${num(g.not_fit)} are not a fit</b> because a required rule is not met: ${why("not_fit")}.</li>` : "",
    `<li><b>The order</b> is by score, the weighted average of six marks. Heaviest${S.weights ? " in your weights" : ""}: ${esc(heavy.join(", "))}.</li>`,
    versions,
    held ? `<li><b>${num(held)} must not be contacted</b> and ${held > 1 ? "are" : "is"} listed last, whatever the score.</li>` : "",
    `<li>${job.openings ? `<b>${num(job.openings)} opening${job.openings > 1 ? "s" : ""}.</b>` : "<b>No source says how many people this job needs</b>, so the list is not cut off."}</li>`,
  ].join("");
  return `<section class="card flowcard" aria-label="How this list was made">
      <div class="flowhead"><b>How this list was made</b>
        <button class="btn plain small" data-act="process" aria-expanded="${S.process ? "true" : "false"}">${S.process ? "Hide the whole process" : "Show the whole process"}</button></div>
      <div class="flow">
        <div class="fnode"><b>${num(st.people)}</b><span>people on file, each checked against every rule</span>${aside}</div>
        <div class="farrow" aria-hidden="true"></div>
        <div class="fnode win"><b>${num(g.best)}</b><span>on this list</span><small>${num(pass)} pass every required rule${job.need_answer ? ` · ${num(job.need_answer)} need an answer` : ""}</small></div>
        ${out ? '<div class="farrow off" aria-hidden="true"></div>' + out : ""}
      </div>
      <div class="decided">
        <div><div class="h">What decided it</div><ul class="points">${points}</ul></div>
        <figure class="web"><figcaption class="h">Weights${S.weights ? " (yours)" : ""}</figcaption>${weightsWeb(job)}</figure>
      </div>
      ${S.process ? processView(job) : ""}
    </section>`;
}

// How the list is ordered, and the recruiter's own say in it. Changing a weight or a filter re-ranks the
// whole list on the server; nobody moves between Matches, Needs an answer and Not a fit, because those are
// decided by the required rules, not by weights.
const currentWeights = () => ({ ...(S.weights || currentJob().weights) });
const FILTER_CHIPS = (job) => [["act_now", "Can act now"], ["recent:30", "Replied in the last 30 days"], ["no_questions", "Nothing to ask the candidate"],
  ["min:evidence:80", "Evidence holds up"]].concat(job.skills.map((s) => [`skill:${s.key}`, `Has ${s.label}`]));
const OWN_LABEL = { text: (v) => `Says “${v}”`, school: (v) => `Studied at “${v}”` };
function rankBar(job) {
  const w = currentWeights(), total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  const options = [`<option value="">As configured for this job</option>`].concat(S.data.meta.presets.map((p) =>
    `<option value="${esc(p.name)}" ${S.preset === p.name ? "selected" : ""}>${esc(p.label)}</option>`)).join("");
  const own = S.weights && !S.preset ? '<span class="tag">your weights</span>' : "";
  const sliders = `<div class="sliders">${S.data.meta.dimensions.map((d) => `
      <label class="slider"><span>${esc(d.label)}</span>
        <input type="range" min="0" max="50" step="5" value="${w[d.name]}" data-dim="${d.name}" id="w-${d.name}" aria-label="Weight for ${esc(d.label)}">
        <b data-share="${d.name}">${Math.round((100 * w[d.name]) / total)}%</b></label>`).join("")}
      <div class="muted small">Weights change the order and the score shown. They never move anyone past a required rule.</div></div>`;
  const chips = FILTER_CHIPS(job).map(([f, label]) => `<button class="chipbtn ${S.flt.has(f) ? "on" : ""}" data-act="flt" data-f="${esc(f)}" aria-pressed="${S.flt.has(f)}">${esc(label)}</button>`).join("");
  // Criteria the recruiter typed. Each is a chip; clicking it removes it.
  const mine = [...S.flt].filter((f) => OWN_LABEL[f.split(":")[0]]).map((f) => {
    const [kind, ...rest] = f.split(":");
    return `<button class="chipbtn on" data-act="flt" data-f="${esc(f)}" aria-pressed="true" title="Remove">${esc(OWN_LABEL[kind](rest.join(":")))} ✕</button>`;
  }).join("");
  const reset = S.weights || S.flt.size ? `<button class="linkish" data-act="rank-reset">Reset</button>` : "";
  return `<section class="card rankbar">
      <div class="rankrow"><b>Rank by</b><select id="preset" aria-label="Ranking preset">${options}</select>${own}${reset}</div>
      ${sliders}
      <div class="chiprow"><span class="muted small">Only show</span>${chips}${mine}</div>
      <div class="ownrow"><label for="own" class="muted small">Add your own</label>
        <input id="own" type="text" maxlength="60" placeholder="Type any skill or phrase, or any school or institution" autocomplete="off">
        <select id="ownkind" aria-label="Where to look"><option value="text">said in their own words</option><option value="school">school or institution they studied at</option></select>
        <button class="btn plain" data-act="own-add">Add</button></div>
    </section>`;
}

function jobView(job) {
  const open = job.status === "open";
  const facts = (job.when ? job.when.split(" · ") : []).concat(job.pay ? [job.pay] : []);
  const changed = job.requirements.filter((l) => l.status === "provisional" || l.status === "pending");
  const names = [...new Set(changed.map((l) => l.label.toLowerCase()))].join(" and ");
  const alert = !changed.length ? "" : `<div class="alert">${changed[0].status === "provisional"
    ? `The account manager's note is ahead of the vendor feed on ${esc(names)}. Matches that rely on it say so.`
    : `A note not yet in the vendor feed would change ${esc(names)}. It is not being used.`}
    <button class="linkish" data-act="details">${S.details ? "Hide" : "See"} details</button></div>`;
  const ids = Object.entries(job.ids).map(([k, v]) => `${k.toUpperCase()} ${v}`).join(" · ");
  const details = !S.details ? "" : `<div class="reqs">${job.requirements.map(reqLine).join("")}</div>
    ${job.notes.map((n) => `<div class="note">${esc(n.text)} ${srcOf(n.ref)}</div>`).join("")}
    ${open ? job.questions.map((q) => `<div class="ask">${esc(q)}</div>`).join("") : ""}
    <div class="muted small" style="margin-top:10px">${esc(ids)} · ${esc(job.division)} division</div>`;
  const head = `<section class="card jobhead">
      <h1>${esc(job.title)}${job.new ? '<span class="new">New</span>' : ""}</h1>
      <div class="sub">${esc([job.facility, job.place].filter(Boolean).join(" · "))}</div>
      <div class="facts">${facts.map((f) => `<span class="fact">${esc(f)}</span>`).join("")}</div>
      ${alert}
      ${changed.length ? "" : `<div style="margin-top:10px"><button class="linkish" data-act="details">${S.details ? "Hide" : "Job"} details</button></div>`}
      ${details}</section>`;
  const serious = S.data.issues.filter((i) => i.severity !== "info").length;
  const health = serious ? `<div class="alert" style="margin:0 0 12px">${serious} data problem${serious > 1 ? "s were" : " was"} worked around, so some results may be incomplete.
      <button class="linkish" data-act="page" data-page="plumbing">See what happened</button></div>` : "";
  if (!open) return health + head + `<div class="card empty" style="margin-top:16px">This job is ${esc(job.status)}. Nobody is recommended for it.</div>`;
  const totals = job.groups;
  const shown = S.lists[S.filter] || { items: [], total: totals[S.filter] };
  const list = shown.items;
  const legend = `<span class="legend"><i class="met"></i>met <i class="caution"></i>met, to check <i class="unknown"></i>unknown <i class="not_met"></i>not met</span>`;
  const title = S.filter === "not_fit"
    ? `<b>Not a fit · ${num(totals.not_fit)}</b> <button class="linkish" data-act="filter" data-filter="best">Back to the list</button>`
    : `<b>${num(totals.best)} ${totals.best === 1 ? "person" : "people"}</b>${job.need_answer ? ` · ${num(job.need_answer)} need${job.need_answer > 1 ? "" : "s"} an answer` : ""}`;
  const lead = S.filter === "not_fit" ? "A required rule is not met as posted. Each card says which."
    : "Best fit first. Someone who needs an answer sits where their score puts them, with what is missing.";
  const note = `<div class="listhead"><span>${title}</span>${legend}</div><div class="muted small" style="margin:0 2px 8px">${lead}</div>`;
  const left = shown.total - list.length;
  const more = left > 0 ? `<div class="morerow"><button class="btn plain" data-act="more">Show next ${num(Math.min(S.data.meta.top_n, left))}</button>
      <span class="muted small">Showing the top ${num(list.length)} of ${num(shown.total)}</span></div>` : "";
  const narrowed = S.flt.size && shown.total !== totals[S.filter] ? `<div class="muted small" style="margin:0 2px 8px">${num(shown.total)} of ${num(totals[S.filter])} pass what you asked for.</div>` : "";
  const empty = S.flt.size ? "Nobody on the list passes everything you asked for." : "Nobody on the list yet.";
  return health + head + rankBar(job) + note + narrowed
    + `<section class="card">${list.length ? cards(list, job) + more : `<div class="empty">${empty}</div>`}</section>` + flowView(job);
}

// ------------------------------------------------------------ person drawer
function ruleRow(r) {
  const open = S.everything || S.openRules.has(r.rule);
  let more = "";
  if (open) {
    const items = r.items.length ? `<ul class="sub-items">${r.items.map((i) => `<li><span class="ic ${i.outcome}">${OUT[i.outcome].i}</span><span>${esc(i.label)}${i.level !== "required" ? " (preferred)" : ""}: ${esc(i.shows)}</span></li>`).join("")}</ul>` : "";
    more = `<div class="more">`
      + (r.level !== "gate" && r.outcome !== "na" ? `<div class="k">The job asks for</div><div>${esc(r.needs)}</div>${r.job_refs.map(cite).join("")}` : "")
      + (r.refs.length || items ? `<div class="k">Evidence</div>${items}${r.refs.map(cite).join("")}` : "")
      + (r.alt ? `<div class="k">Under the ${esc(r.alt.version)}</div><div>${esc(cap(r.alt.text))}</div>` : "")
      + r.notes.map((n) => `<div class="note">${esc(n)}</div>`).join("")
      + r.questions.map((q) => `<div class="ask">${esc(q)}</div>`).join("")
      + (r.context.length ? `<details ${S.everything ? "open" : ""}><summary>Related notes (${r.context.length})</summary>${r.context.map(fileBlock).join("")}</details>` : "")
      + (r.outcome === "na" ? `<div class="muted">The rule ran and found nothing in the posting to check.</div>` : "")
      + `</div>`;
  }
  const caution = r.outcome === "met" && (r.flags.includes("disputed") || (r.credit != null && r.credit < 1));
  return `<div class="rule"><button data-act="rule" data-rule="${esc(r.rule)}" aria-expanded="${open}">
      <span class="ic ${r.outcome}${caution ? " caution" : ""}" title="${esc(caution ? "Met, with something to check" : OUT[r.outcome].label)}" aria-label="${esc(caution ? "Met, with something to check" : OUT[r.outcome].label)}">${caution ? "!" : OUT[r.outcome].i}</span>
      <span><span class="lab">${esc(r.label)}</span>${r.level === "soft" ? '<span class="tag">preferred</span>' : ""}${r.flags.map(flagTag).join("")}<br>
        <span class="txt">${esc(r.outcome === "na" ? r.needs : r.shows)}</span></span>
      <span class="muted" aria-hidden="true">${open ? "–" : "+"}</span></button>${more}</div>`;
}

// Everything the résumés and profiles list, as the candidate wrote it. A solid chip is something a rule checks;
// an outlined one is listed on the résumé and checked by no rule.
function backgroundView(b, name) {
  if (!b || !b.sources.length) return "";
  const chip = (i) => `<span class="chip ${i.checked.length ? "checked" : ""}" title="${i.checked.length ? "Checked by the rules: " + esc(i.checked.join(", ")) : "Listed on the résumé; no rule checks it"}">${esc(i.text)}</span>`;
  const list = (items) => items.map((i) => `<li>${esc(i.text)}${i.checked.length ? "" : ' <span class="muted small">· not checked</span>'}</li>`).join("");
  const roles = b.experience.map((r) => `<li><b>${esc(r.text)}</b>${r.dates ? ` <span class="muted">· ${esc(r.dates)}</span>` : ""}
      ${r.duties.length ? `<ul class="duties">${r.duties.map((d) => `<li>${esc(d.text)}</li>`).join("")}</ul>` : ""}</li>`).join("");
  const sec = (title, body) => (body ? `<div class="bgsec"><div class="k">${title}</div>${body}</div>` : "");
  const head = Object.keys(b.header).length ? `<div class="muted small">${esc([b.header.name, b.header.home, b.header.email].filter(Boolean).join(" · "))}</div>` : "";
  const src = b.sources.map((s) => `${esc(s.label)}, ${when(s.at, true)}`).join(" · ");
  return `<h3>Background</h3>
    <div class="bg">${head}
      ${sec("Links", (b.links || []).length ? `<ul>${b.links.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.url.replace(/^https?:\/\//, ""))}</a> <span class="muted small">· ${esc(l.source)}</span></li>`).join("")}</ul>` : "")}
      ${sec("Experience", roles ? `<ul class="roles">${roles}</ul>` : "")}
      ${sec("Skills", b.skills.length ? `<div class="chips">${b.skills.map(chip).join("")}</div>` : "")}
      ${sec("Education", b.education.length ? `<ul>${list(b.education)}</ul>` : "")}
      ${sec("Licenses and certificates, as reported", b.credentials.length ? `<ul>${list(b.credentials)}</ul>` : "")}
      ${b.summary.length ? sec("Summary", `<ul>${b.summary.map((i) => `<li>${esc(i.text)}</li>`).join("")}</ul>`) : ""}
      ${b.not_stated.length ? `<details ${S.everything ? "open" : ""}><summary>What these documents do not state (${b.not_stated.length})</summary><ul>${b.not_stated.map((i) => `<li>${esc(i.text)}</li>`).join("")}</ul></details>` : ""}
      <div class="muted small">As written by ${esc(name)} in: ${src}. Solid skills are checked by the rules; outlined ones are listed only.</div>
    </div>`;
}

// A radar of the quality dimensions: one polygon per person, the range shown dashed where answers are missing.
// Dimensions the job says nothing about are left off. Values are also listed beside it, so nothing relies on
// reading the shape.
const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)"];
function radar(people, labels, size = 230, said = null) {
  const axes = labels.map((l, i) => ({ ...l, i })).filter((l) => people.some((p) => p.values[l.i] != null));
  if (axes.length < 3) return "";
  const c = size / 2, R = size / 2 - 42, n = axes.length;
  const pt = (k, v) => { const a = -Math.PI / 2 + (2 * Math.PI * k) / n; return [c + Math.cos(a) * R * v / 100, c + Math.sin(a) * R * v / 100]; };
  const poly = (vals) => vals.map((v, k) => pt(k, v ?? 0).map((x) => x.toFixed(1)).join(",")).join(" ");
  const rings = [25, 50, 75, 100].map((v) => `<polygon points="${poly(axes.map(() => v))}" class="rring"/>`).join("");
  const spokes = axes.map((_, k) => { const [x, y] = pt(k, 100); return `<line x1="${c}" y1="${c}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="rring"/>`; }).join("");
  const text = axes.map((a, k) => { const [x, y] = pt(k, 122); return `<text x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="${Math.abs(x - c) < 4 ? "middle" : x > c ? "start" : "end"}" class="rlab">${esc(a.label)}</text>`; }).join("");
  const shapes = people.map((p, j) => {
    const vals = axes.map((a) => p.values[a.i]), highs = axes.map((a) => p.highs ? p.highs[a.i] : null);
    const range = highs.some((h, k) => h != null && h !== vals[k]) ? `<polygon points="${poly(highs)}" fill="none" stroke="${SERIES[j]}" stroke-width="1.5" stroke-dasharray="4 3"/>` : "";
    return `${range}<polygon points="${poly(vals)}" fill="${SERIES[j]}" fill-opacity="${people.length > 1 ? 0.12 : 0.22}" stroke="${SERIES[j]}" stroke-width="2"/>`;
  }).join("");
  const desc = said || people.map((p) => `${p.name}: ` + axes.map((a) => `${a.label} ${p.values[a.i] ?? "unknown"}`).join(", ")).join(". ");
  return `<svg viewBox="0 0 ${size} ${size}" class="radar" role="img" aria-label="${esc(desc)}">${rings}${spokes}${shapes}${text}</svg>`;
}

function scoreView(m) {
  const dims = m.dim_detail || [];
  if (!dims.length) return "";
  const labels = dims.map((d) => ({ label: d.label }));
  const pic = radar([{ name: m.name, values: dims.map((d) => d.value), highs: dims.map((d) => d.high) }], labels);
  const w = currentWeights(), total = dims.filter((d) => d.value != null).reduce((a, d) => a + (w[d.name] || 0), 0) || 1;
  const rows = dims.map((d) => d.value == null ? "" : `<li><span class="dl">${esc(d.label)}</span><b>${d.value}</b>${d.low !== d.high ? `<span class="muted small"> (${d.low}–${d.high})</span>` : ""}
      <span class="muted small"> · weight ${Math.round((100 * (w[d.name] || 0)) / total)}%</span><div class="small">${esc(d.why)}</div></li>`).join("");
  return `<h3>Why this score</h3><div class="scorebox">${pic}<ul class="dimlist">${rows}</ul></div>`;
}

function drawer() {
  const job = S.data && currentJob();
  const d = S.person && job && detailOf(S.person);
  if (!d) return "";
  const m = d.match, p = d.person;
  const applies = m.results.filter((r) => r.outcome !== "na"), na = m.results.filter((r) => r.outcome === "na");
  const range = m.low !== m.high ? ` Between ${m.low} and ${m.high} once the open questions are answered.` : "";
  const steps = m.action.steps.length ? `<ol>${m.action.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : "";
  const similar = m.band ? `<h3>Place in the list</h3><div class="small">${m.identical > 1 ? `Same result on every rule as ${m.identical - 1} other${m.identical > 2 ? "s" : ""}. ` : ""}${esc(m.decided_by || "Listed first among people with a similar score.")}</div>` : "";
  return `<div class="scrim" data-act="close"></div>
    <aside class="drawer" role="dialog" aria-label="${esc(m.name)}">
      <button class="x" data-act="close" aria-label="Close">✕</button>
      <div class="dhead"><div class="avatar" aria-hidden="true">${esc(initials(m))}</div>
        <div><h1>${esc(m.name)}${nameTag(p.name_basis)}</h1>
          <div class="muted">${esc([p.headline, p.home].filter(Boolean).join(" · "))}</div>
          <div class="muted small">${esc(p.records.join(" · "))}${p.other_names.length ? ` · also recorded as ${esc(p.other_names.join(", "))}` : ""}${p.namesakes ? ` · ${num(p.namesakes)} other ${p.namesakes > 1 ? "people share" : "person shares"} this name; no record links them` : ""}</div></div></div>
      <div style="margin-top:12px">${badge(m)} <span class="muted small">${m.tier === "not_fit" ? "Not a fit for" : `Score ${m.score} for`} ${esc(job.title)}.${m.tier === "not_fit" ? "" : range}</span></div>
      <div class="next ${m.action.status}"><b>${esc(m.action.label)}</b>${steps}</div>
      <div class="everything"><button class="linkish" data-act="everything" aria-pressed="${S.everything}">${S.everything ? "Show less" : "Show everything"}</button></div>
      ${m.tier === "not_fit" ? "" : scoreView(m)}
      ${similar}
      ${backgroundView(p.background, m.name)}
      <h3>Why, rule by rule</h3>
      ${applies.map(ruleRow).join("")}
      ${na.length ? (S.showNA || S.everything ? na.map(ruleRow).join("") : `<div style="border-top:1px solid var(--line);padding:10px 0"><button class="linkish" data-act="na">Show ${na.length} rules that do not apply to this job</button></div>`) : ""}
      <h3>Permission to act</h3>
      ${m.gates.map(ruleRow).join("")}
      <h3>On file</h3>
      <details ${S.everything ? "open" : ""}><summary>Everything on file for ${esc(m.name)} (${p.evidence_ids.length})</summary>
        <div class="muted small" style="margin-top:6px">${esc(p.records.join(" · "))}</div>${p.evidence_ids.map(fileBlock).join("")}</details>
    </aside>`;
}

function compareModal() {
  const job = currentJob();
  const picked = [...S.compare.keys()].map(detailOf).filter(Boolean).map((d) => d.match);
  if (picked.length < 2) return "";
  const all = (m) => m.results.concat(m.gates);
  const head = picked.map((m) => `<th>${esc(m.name)}${m.namesakes ? `<div class="muted small">${esc(m.ref)}</div>` : ""}<div style="margin:4px 0">${badge(m)}</div><div>Score ${m.score}</div><div>${esc(nextLine(m)[1])}</div></th>`).join("");
  const rows = all(picked[0]).map((r, i) => {
    const cells = picked.map((m) => all(m)[i]);
    if (cells.every((c) => c.outcome === "na")) return "";
    const diff = new Set(cells.map((c) => c.outcome + "|" + c.shows)).size > 1;
    return `<tr class="${diff ? "diff" : ""}"><td><b>${esc(r.label)}</b></td>${cells.map((c) => `<td><span class="ic ${c.outcome}" style="display:inline-flex;width:18px;height:18px;border-radius:50%;align-items:center;justify-content:center;font-size:11px;font-weight:800;margin-right:6px">${OUT[c.outcome].i}</span>${esc(c.shows || c.needs)}${c.refs[0] ? `<br>${srcOf(c.refs[0])}` : ""}</td>`).join("")}</tr>`;
  }).join("");
  const others = [...S.compare.keys()].map(detailOf).filter(Boolean).map((d) => (d.person.background ? d.person.background.skills.filter((s) => !s.checked.length).map((s) => s.text) : []));
  const alsoRow = others.some((o) => o.length) ? `<tr class="diff"><td><b>Also on résumé</b><div class="muted small">not checked by any rule</div></td>${others.map((o) => `<td>${o.length ? esc(o.join(" · ")) : '<span class="muted">nothing else listed</span>'}</td>`).join("")}</tr>` : "";
  return `<div class="scrim" style="z-index:60" data-act="close"></div><div class="modal" role="dialog" aria-label="Compare">
      <div class="top"><h2>Side by side · ${esc(job.title)}</h2><button class="btn plain" data-act="close">Close</button></div>
      <div class="cmpradar">${radar(picked.map((m) => ({ name: m.name, values: (m.dim_detail || []).map((d) => d.value) })), (picked[0].dim_detail || []).map((d) => ({ label: d.label })), 260)}
        <ul class="legend2">${picked.map((m, j) => `<li><i style="background:${SERIES[j]}"></i>${esc(m.name)} · ${m.score}</li>`).join("")}</ul></div>
      <div class="muted small" style="margin-bottom:8px">Highlighted rows are where they differ.</div>
      <table class="grid"><thead><tr><th></th>${head}</tr></thead><tbody>${rows}${alsoRow}</tbody></table></div>`;
}

// ----------------------------------------------------------------- popovers
function popover() {
  const m = S.data.meta;
  if (S.pop === "demo") {
    // Named by the kind of event, so a different set of events reads correctly without changing this page.
    const names = { snapshot: "Start of day", "job.created": "A new job arrives", "job.updated": "A job changes", "candidate.updated": "A candidate sends an update" };
    const steps = m.steps.map((s, i) => `<button class="opt ${i === m.step ? "on" : ""}" data-act="step" data-step="${i}">
        <span class="n">${i + 1}</span><span><b>${esc(names[s.kind] || s.label)}</b><br><span class="muted small">${hhmm(s.at)} UTC · ${esc(s.id ? s.label : `${m.sources.length} sources loaded, ${num(m.people)} people`)}</span></span></button>`).join("");
    const options = ['<option value="">Use account-manager notes, flagged</option>'].concat(m.overlays.map((o) => `<option value="${esc(o.name)}">${esc(o.label)}</option>`)).join("");
    return `<div class="popover"><h2>Demo</h2><div class="muted small">Replay the sample events. The page reacts as it would for a recruiter.</div>${steps}
      <h3>Settings profile</h3><select id="overlay" aria-label="Settings profile">${options}</select>
      <div class="muted small" style="margin-top:8px">Reference date ${esc(m.reference_label)}. The sample data is fictional.</div></div>`;
  }
  if (S.pop === "activity") {
    const rows = S.data.outbox.slice().reverse().map((n) => `<div class="row"><b>${hhmm(n.at)}</b> ${esc(n.text)}</div>`).join("");
    return `<div class="popover"><h2>Activity</h2><div class="muted small">What a notification channel would receive. Simulated.</div>${rows || '<div class="row muted">Nothing yet.</div>'}</div>`;
  }
  if (S.pop === "more") {
    return `<div class="popover"><h2>More</h2>
      <div class="row"><button class="linkish" data-act="page" data-page="coverage">Rule coverage</button><div class="muted small">Proof that every rule ran for every person and job.</div></div>
      <div class="row"><button class="linkish" data-act="page" data-page="plumbing">Sources and destinations</button><div class="muted small">Where data comes from and where results go.</div></div></div>`;
  }
  return "";
}

// ---------------------------------------------------- coverage and plumbing
const back = '<div style="margin-bottom:12px"><button class="linkish" data-act="home">← Back to jobs</button></div>';

function coverageView() {
  const c = S.coverage;
  if (!c) return back + '<section class="card empty">Counting…</section>';
  const rows = Object.entries(c.rules).map(([name, r]) => `<tr><td><b>${esc(r.label)}</b></td><td>${r.kind}</td><td>${r.evaluations}</td>
      <td>${Object.entries(r.counts).map(([o, n]) => `${esc(OUT[o].label)} ${n}`).join(" · ")}</td>
      <td class="muted">${r.not_seen.length ? esc(r.not_seen.map((o) => OUT[o].label.toLowerCase()).join(", ")) : "all seen"}</td></tr>`).join("");
  const reading = c.reading_rules.map((r) => `<tr><td class="mono">${esc(r.id)}</td><td>${esc(r.what)}</td><td>${r.fired}</td></tr>`).join("");
  return back + `<section class="card"><div class="pad"><h1>Every rule is touched</h1>
      <p>${c.every_rule_ran ? "✓ Every rule ran for every pair." : "✕ A rule was skipped."} ${num(c.pairs)} person and job pairs at this point, ${Object.keys(c.rules).length} rules each.
      A rule the job says nothing about returns “does not apply”. It is never skipped.</p>
      <table class="grid"><thead><tr><th>Rule</th><th>Kind</th><th>Runs</th><th>Outcomes on this data</th><th>Not seen in this data</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted small">The sample is small, so some outcomes do not occur in it. The test suite adds a synthetic case for every outcome of every rule and fails if one is missing.</p></div>
    <div class="pad"><h2>Text-reading rules</h2>
      <p>${c.evidence} pieces of evidence produced ${c.claims} dated facts. ${c.reading_rules.length - c.reading_never_fired.length} of ${c.reading_rules.length} reading rules fired on this data.
      ${c.unparsed_clauses} clauses were not turned into facts. They stay readable as context and are never guessed at.</p>
      <table class="grid"><thead><tr><th>Rule</th><th>What it reads</th><th>Times fired</th></tr></thead><tbody>${reading}</tbody></table></div>
    <div class="pad"><h2>Skills on résumés that no rule checks</h2>
      <p class="muted">They are shown on each person's panel, but no job can ask for them until they are added to the vocabulary in the settings. Most common first.</p>
      ${c.unrecognised.length ? `<table class="grid"><thead><tr><th>As written</th><th>People</th></tr></thead><tbody>${c.unrecognised.map((u) => `<tr><td>${esc(u.term)}</td><td>${num(u.people)}</td></tr>`).join("")}</tbody></table>` : "<p>None: every skill listed is in the vocabulary.</p>"}</div></section>`;
}

function plumbingView() {
  const m = S.data.meta;
  const src = m.sources.map((s) => `<tr><td><b>${esc(s.label)}</b></td><td class="mono">${esc(s.adapter)}</td><td class="mono">${esc(s.path)}</td></tr>`).join("");
  const snk = m.sinks.map((s) => `<tr><td><b>${esc(s.name)}</b> <span class="tag">simulated</span></td><td class="mono">${esc(s.adapter)}</td><td class="mono">${esc(s.path)}</td></tr>`).join("");
  const written = S.published ? `<div class="note">Wrote ${S.published.length} local files: <span class="mono">${esc(S.published.join(", "))}</span></div>` : "";
  const word = { error: "Left out", warning: "Fallback used", info: "Worth knowing" };
  const problems = S.data.issues.length
    ? `<table class="grid"><thead><tr><th>What happened</th><th>Source</th><th>Where</th><th>Detail</th></tr></thead><tbody>${S.data.issues.map((i) => `<tr><td><b>${word[i.severity] || esc(i.severity)}</b></td><td>${esc(i.source)}</td><td>${esc(i.where)}</td><td>${esc(i.message)}</td></tr>`).join("")}</tbody></table>`
    : `<p>✓ No data problems found. Every source loaded and every record was read.</p>`;
  return back + `<section class="card"><div class="pad"><h1>Data problems</h1>
      <p class="muted">A broken source, record or date never stops the tool. It is left out or a fallback is used, and it is listed here.</p>${problems}</div>
    <div class="pad"><h2>Where data comes from</h2>
      <p class="muted">Each source has one adapter that turns it into dated facts. The matching rules never see a source's format.</p>
      <table class="grid"><thead><tr><th>Source</th><th>Adapter</th><th>File</th></tr></thead><tbody>${src}</tbody></table></div>
    <div class="pad"><h2>Where results go</h2>
      <p class="muted">Each destination receives the same plain result. All three are simulated: they write local files and contact nobody.</p>
      <table class="grid"><thead><tr><th>Destination</th><th>Adapter</th><th>Writes to</th></tr></thead><tbody>${snk}</tbody></table>
      <p><button class="btn" data-act="publish">Write the shortlists now</button></p>${written}</div></section>`;
}

// ------------------------------------------------------------------- events
function closeAll() { S.person = null; S.comparing = false; S.pop = null; }

// Actions that need something from the tool first. Each returns once the page can be drawn.
const FETCHING = {
  async job(el) { closeAll(); S.page = "jobs"; S.job = el.dataset.key; S.filter = "best"; S.details = false; S.lists = {}; S.compare.clear();
    S.weights = null; S.preset = ""; S.flt.clear(); await showList(); },
  async flt(el) { const f = el.dataset.f; S.flt.has(f) ? S.flt.delete(f) : S.flt.add(f); S.lists = {}; await showList(); },
  async "own-add"() {                    // a criterion the recruiter typed: "said in their own words" or "where they studied"
    const box = $("#own"), value = (box ? box.value : "").replace(/[|]/g, " ").trim();
    if (value.length < 2) return;
    S.flt.add(`${$("#ownkind").value}:${value}`); S.lists = {}; await showList();
  },
  async "rank-reset"() { S.weights = null; S.preset = ""; S.flt.clear(); S.lists = {}; await showList(); },
  async filter(el) { S.filter = el.dataset.filter; await showList(); },
  async more() { await showList(true); },
  async person(el) { S.openRules.clear(); S.showNA = false; await open(el.dataset.person); },
  async compare() { await Promise.all([...S.compare.keys()].map((p) => open(p, false))); S.comparing = true; },
  async page(el) { closeAll(); S.page = el.dataset.page; if (S.page === "coverage" && !S.coverage) S.coverage = await api("coverage"); },
  async "toast-view"(el) {
    const t = S.toasts.splice(Number(el.dataset.i), 1)[0];
    S.page = "jobs"; S.details = false;
    if (S.job !== t.job) { S.job = t.job; S.lists = {}; S.compare.clear(); }
    S.filter = t.tier ? groupOf(t.tier) : "best";
    await showList();
    if (t.person && (await open(t.person))) S.openRules = new Set(t.notMet);
  },
};

document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-act]");
  if (S.pop && !e.target.closest(".popover") && !(el && el.dataset.act === "pop")) { S.pop = null; if (!el) { render(); return; } }
  if (!el) return;
  const act = el.dataset.act;
  if (act === "pick" || e.target.closest("[data-stop]")) return;          // the Compare tick box is handled on change
  if (FETCHING[act]) {
    try { await FETCHING[act](el); render(); } catch (err) { fail(err); }
    return;
  }
  if (act === "home") { closeAll(); S.page = "jobs"; }
  else if (act === "retry") { load(false); return; }
  else if (act === "pop") S.pop = S.pop === el.dataset.pop ? null : el.dataset.pop;
  else if (act === "details") S.details = !S.details;
  else if (act === "rule") { const r = el.dataset.rule; S.openRules.has(r) ? S.openRules.delete(r) : S.openRules.add(r); }
  else if (act === "na") S.showNA = true;
  else if (act === "everything") S.everything = !S.everything;
  else if (act === "process") S.process = !S.process;
  else if (act === "close") { S.person = null; S.comparing = false; }
  else if (act === "compare-clear") S.compare.clear();
  else if (act === "toast-x") S.toasts.splice(Number(el.dataset.i), 1);
  else if (act === "step") { const to = Number(el.dataset.step), forward = to > S.step; closeAll(); S.page = "jobs"; S.step = to; S.published = null; load(forward); return; }
  else if (act === "publish") {
    fetch(`/api/publish?step=${S.step}&overlay=${encodeURIComponent(S.overlay)}`, { method: "POST" })
      .then((r) => r.json()).then((d) => { S.published = d.written; render(); });
    return;
  }
  render();
});

document.addEventListener("input", (e) => {           // a slider moving: show the share it now has, before it is applied
  if (!e.target.dataset.dim) return;
  const w = currentWeights();
  w[e.target.dataset.dim] = Number(e.target.value);
  const total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  document.querySelectorAll("[data-share]").forEach((el) => { el.textContent = `${Math.round((100 * w[el.dataset.share]) / total)}%`; });
});

document.addEventListener("change", async (e) => {
  if (e.target.dataset.dim || e.target.id === "preset") {
    try {
      if (e.target.id === "preset") {
        const p = S.data.meta.presets.find((x) => x.name === e.target.value);
        S.preset = p ? p.name : ""; S.weights = p ? { ...p.weights } : null;
      } else {
        S.weights = { ...currentWeights(), [e.target.dataset.dim]: Number(e.target.value) }; S.preset = "";
      }
      S.lists = {}; await showList(); render();
    } catch (err) { fail(err); }
    return;
  }
  if (e.target.id === "overlay") { S.overlay = e.target.value; S.published = null; S.pop = null; load(false); return; }
  if (e.target.dataset.act === "pick") {
    const p = e.target.dataset.person;
    e.target.checked ? S.compare.set(p, true) : S.compare.delete(p);
    render();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && (S.person || S.comparing || S.pop)) { closeAll(); render(); }
  if (e.key === "Enter" && e.target.classList.contains("cand")) e.target.click();
  if (e.key === "Enter" && e.target.id === "own") { e.preventDefault(); const b = document.querySelector('[data-act="own-add"]'); if (b) b.click(); }
});

load(false);
