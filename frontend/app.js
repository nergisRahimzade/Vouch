"use strict";
// Vouch UI in the Lovable "sommerfest-case-compass" design. Every figure comes from the Vouch API.
const view = document.getElementById("view");
const I = name => (window.ICONS[name] || "");
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = new Intl.NumberFormat("de-DE", {style: "currency", currency: "EUR"});
const eur = v => v == null || v === "" ? "—" : fmt.format(Number(v));
const num = v => Number(v || 0);
const store = {get(k) { try { return localStorage.getItem(k); } catch { return null; } },
               set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window: keep in memory only */ } }};
const state = {run: null, timeline: [], scenarios: [], lastRunId: store.get("vouch.lastRun"), person: null, filter: "all"};

async function api(path, options) {
  const r = await fetch(path, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "The request could not be completed.");
  return body;
}

// -- shared pieces in the design's markup -----------------------------------------------------------------
const BADGE = {ai: "bg-ai-soft text-ai ring-ai/20", warning: "bg-warning-soft text-warning ring-warning/25",
               success: "bg-success-soft text-success ring-success/20", danger: "bg-danger-soft text-danger ring-danger/20",
               muted: "bg-muted text-muted-foreground ring-border"};
const DOT = {ai: "bg-ai", warning: "bg-warning", success: "bg-success", danger: "bg-danger", muted: "bg-muted-foreground"};
const badge = (text, tone = "muted", dot = false) =>
  `<span class="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${BADGE[tone]}">${dot ? `<span class="size-1.5 rounded-full ${DOT[tone]}"></span>` : ""}${esc(text)}</span>`;
const title = (h, sub, right = "") =>
  `<div class="flex flex-wrap items-end justify-between gap-4"><div><h1 class="text-xl font-semibold tracking-tight">${esc(h)}</h1><p class="mt-1 text-muted-foreground">${esc(sub)}</p></div>${right}</div>`;
const stats = items => `<div class="grid grid-cols-2 divide-x overflow-hidden rounded-lg border bg-card lg:grid-cols-4">${items.map(([label, value, sub]) =>
  `<div class="p-5"><div class="text-[12px] text-muted-foreground">${esc(label)}</div><div class="tabular mt-1.5 text-2xl font-semibold">${esc(value)}</div><div class="mt-0.5 text-[12px] text-muted-foreground">${esc(sub)}</div></div>`).join("")}</div>`;
const section = (h, body, right = "") =>
  `<section class="surface overflow-hidden"><header class="flex h-10 items-center justify-between border-b px-4"><h3 class="text-[12.5px] font-semibold tracking-tight">${esc(h)}</h3>${right}</header>${body}</section>`;
const empty = text => `<p class="px-4 py-3 text-muted-foreground">${esc(text)}</p>`;
const primaryBtn = (label, icon, attrs = "") =>
  `<button class="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-primary-foreground hover:opacity-90 disabled:opacity-50" ${attrs}>${I(icon)}${esc(label)}</button>`;
const borderBtn = (label, icon, attrs = "") =>
  `<button class="inline-flex h-8 items-center gap-1.5 rounded-md border bg-card px-3 hover:bg-muted" ${attrs}>${I(icon)}${esc(label)}</button>`;
const when = at => at ? `${at.slice(0, 10)} ${at.slice(11, 16)}` : "";
const human = action => String(action || "").replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
const actor = name => name === "Vouch" ? "Vouch Agent" : name;
const chip = cite => `<span class="inline-flex items-center rounded border border-ai/20 bg-ai-soft px-1 font-mono text-[10.5px] font-medium text-ai">${esc(cite)}</span>`;

const FLAG_TEXT = {
  event_cost_threshold_crossed: ["Per-head cost now exceeds 110 €", "Input VAT reclaimed earlier for the whole event is reversed."],
  third_event_allowance_reassigned: ["Allowance moved to the two best events", "A third staff event made an earlier event taxable."],
  gift_limit_exceeded: ["Gift limit exceeded for a recipient", "All of that person's gifts this year become non-deductible."],
  gift_recipients_missing: ["Gift recipients not named", "Recipients are required for gifts over 10 € net."],
  bewirtung_docs_missing: ["Hospitality documentation incomplete", "Participants, host or occasion are missing."],
  lumped_invoice: ["Package invoice not itemized", "The vendor must itemize; no split is guessed."],
  event_not_open_to_all: ["Event not open to all staff", "No allowance; the tax manager decides on flat tax."],
  classification_conflict: ["Organizer label contradicts the facts", "The facts decide the treatment."],
  working_meal_over_limit: ["Working meal over 60 € per person", "The full value is taxable pay."],
  flat_tax_cap_exceeded: ["Recipient over the §37b cap", "Flat tax is not available; escalated."],
  event_reference_mismatch: ["Invoice text names another event", "The delivery date decides the event."],
  duplicate_invoice: ["Invoice already received", "Rejected as a duplicate."],
  out_of_period: ["Belongs to a closed month", "Booked in the current open period."],
  election_change_request: ["Request to change the §37b election", "Refused and escalated; the election is fixed for the year."],
  attendance_missing: ["Attendance list missing", "Attendance is never estimated."],
  injection_in_document: ["Instruction-like text in the invoice", "Treated as data and ignored."],
  injection_in_email: ["Instruction-like text in an email", "Treated as data and ignored."],
  handover_draft_matches: ["A colleague's draft matches", "Continued rather than duplicated."],
  handover_draft_differs: ["A colleague's draft differs", "The drafter withdraws or explains it first."]};
const DECISION = {classify_and_book: ["Pending Approval", "ai"], post_corrections: ["Pending Approval", "ai"],
                  hold: ["Held", "warning"], escalate: ["Escalated", "warning"], reject_duplicate: ["Rejected", "danger"],
                  no_action: ["No action", "muted"]};

function banner(message) {
  return `<div class="rounded-lg border border-warning/30 bg-warning-soft p-4"><div class="flex items-center gap-2 font-medium text-warning">${I("triangle-alert")}${esc(message)}</div></div>`;
}

// -- the latest run ------------------------------------------------------------------------------------
async function loadRun() {
  if (!state.lastRunId) return null;
  try { state.run = await api(`/api/runs/${encodeURIComponent(state.lastRunId)}`); }
  catch { state.run = null; }        // the server restarted: runs live in memory
  return state.run;
}

async function startRun(form, payload = null) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const {run_id} = await api("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload || {scenario_id: form.scenario.value, mode: form.mode.value})});
    state.lastRunId = run_id; store.set("vouch.lastRun", run_id); state.timeline = [];
    const stream = new EventSource(`/api/runs/${encodeURIComponent(run_id)}/stream`);
    stream.onmessage = e => { state.timeline.push(JSON.parse(e.data)); const list = document.getElementById("live-steps"); if (list) list.innerHTML = steps(state.timeline); };
    location.hash = "#/cases";
    for (;;) {
      const run = await api(`/api/runs/${encodeURIComponent(run_id)}`);
      if (run.status !== "running") { stream.close(); break; }
      await new Promise(r => setTimeout(r, 400));
    }
    await render();
  } catch (e) { view.insertAdjacentHTML("afterbegin", banner(e.message)); }
  finally { button.disabled = false; }
}

function steps(timeline) {
  if (!timeline.length) return empty("Steps appear here while Vouch works.");
  const tone = {ask: "ai", approve: "ai", flag: "warning", decide: "success"};
  return `<ol class="divide-y">${timeline.map(s => `<li class="flex items-start gap-3 px-4 py-2.5"><span class="step-dot ${DOT[tone[s.kind]] || "bg-muted-foreground"}"></span><div class="flex-1"><div class="font-medium">${esc(human(s.title))}</div>${s.severity === "needs_person" ? `<div class="text-[12px] text-warning">Refused by a control and retried</div>` : ""}</div><span class="text-[12px] text-muted-foreground">${esc(s.kind)}</span></li>`).join("")}</ol>`;
}

// -- pages ---------------------------------------------------------------------------------------------
async function dashboard() {
  const [entries, approvals, audit, books] = await Promise.all([api("/api/entries"), api("/api/approvals"), api("/api/audit"), api("/api/books")]);
  const year = String(books.to_year);
  const live = entries.filter(e => !e.voided && e.entry_date.startsWith(year));
  const net = (accounts, side = 1) => live.flatMap(e => e.lines).filter(l => accounts.includes(l.account))
    .reduce((s, l) => s + side * (num(l.debit) - num(l.credit)), 0);
  const deductible = ["6400", "6420", "6440", "6445", "6450", "6470", "6480", "6490", "6500"];
  const cases = new Set(live.filter(e => e.lines.some(l => deductible.includes(l.account))).map(e => e.source_ref)).size;
  const drafts = live.filter(e => e.status === "draft").length;
  const escalated = approvals.filter(a => a.status === "escalated").length;
  const scenarioOptions = state.scenarios.map(s => `<option value="${esc(s.id)}">${esc(s.id)} · ${esc(s.title)}</option>`).join("");
  const runForm = `<form id="run-form" class="flex flex-wrap items-center gap-2"><select name="scenario" class="control" aria-label="Scenario">${scenarioOptions}</select><select name="mode" class="control" aria-label="Run mode"><option value="model">Live agent (configured provider)</option><option value="scripted">Scripted demo</option></select>${primaryBtn("Process event", "check", 'type="submit"')}</form>`;
  const review = approvals.slice(0, 5).map(a => `<li class="flex items-center justify-between px-4 py-3"><div><div class="font-medium">${esc(a.case_id || a.subject_ref)}</div><div class="text-[12px] text-muted-foreground">${esc(a.subject_ref)} · review by ${esc(a.assigned_role.replace("_", " "))}</div></div><div class="flex items-center gap-3"><span class="tabular">${eur(a.amount)}</span>${badge(a.status === "escalated" ? "Escalated" : "Pending Approval", a.status === "escalated" ? "warning" : "ai")}</div></li>`).join("");
  const activity = audit.rows.slice(-6).reverse().map(r => `<li class="flex items-start justify-between gap-4 px-4 py-3"><div><div>${esc(human(r.action))}</div><div class="text-[12px] text-muted-foreground">${esc(actor(r.actor))} · ${esc(r.target)}</div></div><span class="shrink-0 text-[12px] text-muted-foreground">${esc(when(r.at))}</span></li>`).join("");
  view.innerHTML = title("Dashboard", `Year to date · ${year}`, runForm) +
    stats([["VAT reclaimed", eur(net(["1400"])), `${drafts} drafts awaiting approval`],
           ["Deductible expenses", eur(net(deductible)), `${cases} cases`],
           ["Tax liability drafted", eur(net(["1750", "1760"], -1)), "Payroll + §37b"],
           ["Pending approvals", String(approvals.length), escalated ? `${escalated} escalated` : "none escalated"]]) +
    `<div class="grid gap-6 lg:grid-cols-2">` +
    section("Needs your review", review ? `<ul class="divide-y">${review}</ul>` : empty("Nothing is waiting for a decision."),
            `<a class="flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground" href="#/approvals">View all${I("arrow-right")}</a>`) +
    section("Recent activity", activity ? `<ul class="divide-y">${activity}</ul>` : empty("No activity yet."),
            `<span class="flex items-center gap-1 text-[12px] ${audit.verified ? "text-success" : "text-danger"}">${I("lock")}${audit.verified ? "Tamper-evident" : "Chain broken"}</span>`) +
    `</div>` + section("Try your own event", `<form id="custom-form" class="flex flex-wrap gap-2 p-4">
      <input name="name" class="control" placeholder="Event name" aria-label="Event name" required>
      <input name="date" type="date" class="control" aria-label="Event date" required>
      <input name="attendees" type="number" min="1" max="450" class="control" placeholder="Actual employees" aria-label="Actual employee count" required>
      <input name="net" class="control" placeholder="Invoice net, e.g. 100.00" aria-label="Invoice net" required>
      <input name="vat" class="control" placeholder="VAT, e.g. 19.00" aria-label="Invoice VAT" required>
      <input name="gross" class="control" placeholder="Gross, e.g. 119.00" aria-label="Invoice gross" required>
      ${primaryBtn("Process staff event live", "check", 'type="submit"')}</form>`);
  document.getElementById("custom-form").addEventListener("submit", e => {
    e.preventDefault(); const f = e.target.elements;
    startRun(e.target, {mode: "model", custom: {event_type: "staff_event", name: f.namedItem("name").value,
      date: f.namedItem("date").value, attendees: Number(f.namedItem("attendees").value),
      invoices: [{net: f.namedItem("net").value, vat: f.namedItem("vat").value, gross: f.namedItem("gross").value, vat_rate: "0.19"}]}});
  });
  document.getElementById("run-form").addEventListener("submit", e => { e.preventDefault(); startRun(e.target); });
}

async function approvalsPage() {
  const [items, questions] = await Promise.all([api("/api/approvals"), api("/api/questions")]);
  const tabs = [["all", "All"], ["pending", "Pending Approval"], ["escalated", "Escalated"]];
  const shown = items.filter(a => state.filter === "all" || a.status === state.filter);
  const rows = shown.map(a => `<tr class="hover:bg-muted/40"><td class="px-4 py-3"><a class="font-medium hover:underline" href="#/cases">${esc(a.case_id || a.subject_ref)}</a><div class="text-[12px] text-muted-foreground">${esc(a.subject_ref)}</div></td><td>${esc((a.needs || []).join(" → ").replace(/_/g, " "))}<div class="text-[12px] text-muted-foreground">now: ${esc(a.assigned_role.replace("_", " "))}</div></td><td class="tabular text-right">${eur(a.amount)}</td><td class="px-4">${badge(a.status === "escalated" ? "Escalated" : "Pending Approval", a.status === "escalated" ? "warning" : "ai", true)}</td><td class="px-4 text-right"><div class="inline-flex gap-1.5"><button class="inline-flex h-7 items-center gap-1 rounded-md border px-2.5 hover:bg-muted" data-decide="reject" data-id="${esc(a.request_id)}">${I("arrow-up-right")}Reject</button><button class="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2.5 text-primary-foreground hover:opacity-90" data-decide="approve" data-id="${esc(a.request_id)}">${I("check")}Approve</button></div></td></tr>`).join("");
  view.innerHTML = title("Approvals", "Drafts waiting for a person's decision. Vouch drafts; people approve.") +
    `<div class="flex gap-1">${tabs.map(([k, label]) => `<button class="h-8 rounded-md px-3 hover:bg-muted ${state.filter === k ? "bg-muted font-medium text-foreground" : "text-muted-foreground"}" data-filter="${k}">${label}</button>`).join("")}</div>` +
    `<div class="overflow-hidden rounded-lg border bg-card"><table class="w-full"><thead class="border-b text-left text-[12px] text-muted-foreground"><tr><th class="px-4 py-2.5 font-medium">Case</th><th class="font-medium">Review by</th><th class="text-right font-medium">Amount</th><th class="px-4 font-medium">Status</th><th class="px-4 text-right font-medium">Action</th></tr></thead><tbody class="divide-y">${rows || `<tr><td class="px-4 py-3 text-muted-foreground" colspan="5">Nothing is waiting for a decision.</td></tr>`}</tbody></table></div>`;
  view.insertAdjacentHTML("beforeend", section("Questions for the finance team", questions.length ? questions.map(q =>
    q.about === "nonqualifying_event" ? `<form class="p-4 flex flex-wrap gap-2" data-question="${esc(q.request_id)}" data-recipient="${esc(q.recipient)}">
      <p class="w-full">${esc(q.case_id)}: ${esc(q.recipient_name || q.recipient)} must choose how payroll taxes this event.</p>
      <select name="method" class="control" aria-label="Authorized payroll method" required><option value="">Choose treatment</option><option value="individual">Individual payroll taxation</option><option value="flat_40">Flat payroll tax</option></select>
      <input name="reason" class="control" placeholder="Reason for your ruling" aria-label="Reason for ruling" required>
      ${primaryBtn("Record tax-manager answer and continue", "check", 'type="submit"')}</form>`
    : `<p class="p-4">Waiting for ${esc(q.recipient_name || q.recipient)}: ${esc(human(q.about))} (${esc(q.case_id)})</p>`).join("")
    : empty("No unanswered questions.")));
  view.querySelectorAll("[data-question]").forEach(f => f.addEventListener("submit", async e => {
    e.preventDefault(); const b = f.querySelector("button"); b.disabled = true;
    try {
      if (!state.lastRunId) throw new Error("Open the current case before answering.");
      const r = await api(`/api/questions/${encodeURIComponent(f.dataset.question)}`, {method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({run_id: state.lastRunId, actor_id: f.dataset.recipient,
          data: {method: f.elements.namedItem("method").value, reason: f.elements.namedItem("reason").value}})});
      location.hash = "#/cases";
      for (;;) { const run = await api(`/api/runs/${encodeURIComponent(r.run_id)}`); if (run.status !== "running") break; await new Promise(resolve => setTimeout(resolve, 400)); }
      await render();
    } catch (error) { view.insertAdjacentHTML("afterbegin", banner(error.message)); b.disabled = false; }
  }));
  view.querySelectorAll("[data-filter]").forEach(b => b.addEventListener("click", () => { state.filter = b.dataset.filter; render(); }));
  view.querySelectorAll("[data-decide]").forEach(b => b.addEventListener("click", async () => {
    const reason = window.prompt(`Reason to ${b.dataset.decide} (required, recorded in the audit log):`);
    if (!reason || !reason.trim()) return;
    b.disabled = true;
    try { await api(`/api/approvals/${encodeURIComponent(b.dataset.id)}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision: b.dataset.decide, reason})}); await render(); }
    catch (e) { view.insertAdjacentHTML("afterbegin", banner(e.message)); b.disabled = false; }
  }));
}

async function casePage() {
  const run = await loadRun();
  const d = run && run.decision;
  if (!run) { view.innerHTML = title("Case File", "No case yet") + banner("Process a scenario on the Dashboard to open its case file."); return; }
  if (run.status === "running") { view.innerHTML = title("Case File", `${run.scenario_id} · Vouch is working`) + section("Vouch's steps", `<div id="live-steps">${steps(state.timeline)}</div>`); return; }
  if (!d) { view.innerHTML = title("Case File", run.scenario_id) + banner(run.error || "This run produced no decision."); return; }
  let event = null;
  if (d.matched_event) { try { event = await api(`/api/events/${encodeURIComponent(d.matched_event)}`); } catch { event = null; } }
  const invoices = event ? event.invoices : [];
  const gross = invoices.reduce((s, i) => s + num(i.gross), 0);
  const [label, tone] = DECISION[d.decision] || [d.decision, "muted"];
  const flags = d.flags || [];
  const checks = flags.length
    ? `<div class="rounded-lg border border-warning/30 bg-warning-soft p-4"><div class="flex items-center gap-2 font-medium text-warning">${I("triangle-alert")}${flags.length} ${flags.length === 1 ? "thing" : "things"} to check</div><ul class="mt-2 space-y-1 pl-6 text-foreground">${flags.map(f => { const [t, x] = FLAG_TEXT[f] || [human(f), ""]; return `<li class="list-disc">${esc(t)} — <span class="text-muted-foreground">${esc(x)}</span></li>`; }).join("")}</ul></div>`
    : `<div class="rounded-lg border bg-success-soft p-4"><div class="flex items-center gap-2 font-medium text-success">${I("shield-check")}No issues found</div></div>`;
  const invoiceTables = invoices.map(inv => section(`Invoice ${inv.id}`, `<table class="w-full"><tbody class="divide-y">${inv.lines.map(l => `<tr><td class="px-4 py-2.5">${esc(l.description || human(l.category))}</td><td class="tabular px-4 text-right">${eur(l.net)}</td></tr>`).join("")}<tr><td class="px-4 py-2.5 text-muted-foreground">VAT</td><td class="tabular px-4 text-right">${eur(inv.vat)}</td></tr><tr class="font-semibold"><td class="px-4 py-2.5">Total</td><td class="tabular px-4 text-right">${eur(inv.gross)}</td></tr></tbody></table>`)).join("");
  const entries = (d.entries || []).map(l => `<tr><td class="px-4 py-2.5">${esc(l.account)}<div class="text-[12px] text-muted-foreground">${esc(l.entry_id)}</div></td><td class="tabular text-right">${num(l.debit) ? eur(l.debit) : ""}</td><td class="tabular px-4 text-right">${num(l.credit) ? eur(l.credit) : ""}</td></tr>`).join("");
  const corrections = (d.corrections || []).map(c => `<li class="flex items-start gap-4 px-4 py-3"><p class="flex-1 leading-relaxed">${esc(human(c.type))} for ${esc(c.ref)}${c.reason ? ` — ${esc(c.reason)}` : ""}</p><span class="tabular">${eur(c.amount)}</span></li>`).join("");
  const provider = run.provider || run.result?.provider;
  view.innerHTML = title(event?.event?.name || d.matched_event || d.case_id,
      [invoices.map(i => i.vendor).filter(Boolean).join(", "), event?.event?.date, gross ? eur(gross) : ""].filter(Boolean).join(" · "),
      `<div class="flex items-center gap-2">${badge(label, tone, true)}<span class="text-[12px] text-muted-foreground">${esc(run.result?.mode === "scripted" ? "Scripted demo" : `${provider || ""} ${run.model || ""}`)}</span><a href="#/approvals">${primaryBtn("Review approvals", "check")}</a></div>`) +
    stats([["Income Tax Deductible", eur(d.deductible), `${eur(d.nondeductible)} non-deductible`],
           ["Input VAT Recovered", eur(d.vat_recoverable), `${eur(d.vat_blocked)} blocked`],
           ["Taxable Payroll Benefit", eur(d.wage_taxable), d.payroll_method === "none" ? "No payroll impact" : `${d.payroll_method === "flat_40" ? "§40 flat tax" : "Individually taxed"}: ${eur(d.flat_tax_40)}`],
           ["§37b Guest Flat Tax", eur(d.flat_tax_37b), d.per_participant ? `${eur(d.per_participant)} per participant` : "30 % on guest benefits"]]) +
    checks +
    `<div class="grid gap-6 lg:grid-cols-2">${invoiceTables || section("Invoices", empty("No invoice details."))}` +
    section("Draft entries", entries ? `<table class="w-full"><thead class="border-b text-left text-[12px] text-muted-foreground"><tr><th class="px-4 py-2 font-medium">Account</th><th class="text-right font-medium">Debit</th><th class="px-4 text-right font-medium">Credit</th></tr></thead><tbody class="divide-y">${entries}</tbody></table>` : empty(d.decision === "hold" || d.decision === "escalate" ? "Nothing is booked while the case is held." : "No entries drafted.")) +
    `</div>` +
    (corrections ? section("Corrections to earlier bookings", `<ol class="divide-y">${corrections}</ol>`) : "") +
    section("Why", `<ol class="divide-y"><li class="flex items-start gap-4 px-4 py-3"><p class="flex-1 leading-relaxed">${esc(d.rationale)}</p></li><li class="flex flex-wrap gap-1.5 px-4 py-3">${(d.cites || []).map(chip).join("")}</li></ol>`) +
    section("Vouch's steps", `<div id="live-steps">${steps(run.timeline || state.timeline)}</div>`);
}

async function peoplePage() {
  const payroll = await api("/api/payroll-export");
  const totals = new Map();
  for (const p of payroll) totals.set(p.employee, (totals.get(p.employee) || 0) + num(p.taxable_amount));
  const ids = [...totals.keys()].slice(0, 40);
  if (!state.person) state.person = ids[0] || "E001";
  let ledger = null, failure = "";
  try { ledger = await api(`/api/people/${encodeURIComponent(state.person)}/ledger`); } catch (e) { failure = e.message; }
  const list = ids.map(id => `<li><button class="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/40 ${id === state.person ? "bg-muted/60" : ""}" data-person="${esc(id)}"><div class="min-w-0 flex-1"><div class="font-medium">${esc(id)}</div><div class="text-[12px] text-muted-foreground">taxable event benefits</div></div>${badge("Employee")}<span class="tabular w-24 text-right">${eur(totals.get(id))}</span></button></li>`).join("");
  const used = ledger ? ledger.allowance_assignment.length : 0;
  const max = ledger ? ledger.limits.staff_event_count : 2;
  const rows = ledger ? ledger.rows.map(r => `<li class="flex items-center justify-between py-1.5"><span>${esc(r.event)}${r.allowance ? " · allowance" : ""}</span><span class="tabular">${eur(r.taxable)}</span></li>`).join("") : "";
  const detail = ledger ? `<div class="font-semibold">${esc(ledger.name || ledger.person_id)}</div><div class="text-[12px] text-muted-foreground">${esc(ledger.person_id)} · ${esc(ledger.unit || "")}</div>
    <div class="mt-5 text-[12px] text-muted-foreground">Taxable in ${esc(ledger.year)}</div><div class="tabular text-2xl font-semibold">${eur(ledger.totals.taxable)}</div>
    ${ledger.kind === "employee" ? `<div class="mt-4 text-[12px] text-muted-foreground">Tax-free events used: ${used} of ${max}</div><div class="mt-1.5 flex gap-1">${Array.from({length: max}, (_, n) => `<span class="h-1.5 flex-1 rounded-full ${n < used ? "bg-primary" : "bg-muted"}"></span>`).join("")}</div><ul class="mt-4 text-[12px]">${rows}</ul>`
      : `<div class="mt-4 text-[12px] text-muted-foreground">Gifts this year: ${eur(ledger.totals.gift_value)} of ${eur(ledger.limits.gift)} limit</div><div class="mt-1 text-[12px] text-muted-foreground">§37b benefits: ${eur(ledger.totals.benefit_37b_gross)} of ${eur(ledger.limits.flat_37b_cap)} cap</div>`}
    ${ledger.corrections.length ? `<div class="mt-5 rounded-md bg-ai-soft p-3 text-[12px] text-ai"><div class="flex items-center gap-1.5 font-medium">${I("history")}Retroactive adjustment</div><div class="mt-0.5">${ledger.corrections.map(c => `${esc(c.event)}: ${eur(c.taxable_amount)} (corrects ${esc(c.corrects)})`).join("<br>")}</div></div>` : ""}`
    : `<div class="text-muted-foreground">${esc(failure || "Choose a person.")}</div>`;
  view.innerHTML = title("People Ledger", "Event totals and allowance usage",
      `<form id="person-form" class="flex items-center gap-2"><input name="id" class="control" placeholder="E001 or BP-003" aria-label="Person id">${borderBtn("Look up", "users", 'type="submit"')}</form>`) +
    `<div class="grid gap-6 lg:grid-cols-[1fr_340px]"><div class="overflow-hidden rounded-lg border bg-card"><ul class="divide-y">${list || `<li class="px-4 py-3 text-muted-foreground">No taxable benefits yet. Look up any employee or business partner by id.</li>`}</ul></div><div class="h-fit rounded-lg border bg-card p-5">${detail}</div></div>`;
  view.querySelectorAll("[data-person]").forEach(b => b.addEventListener("click", () => { state.person = b.dataset.person; render(); }));
  document.getElementById("person-form").addEventListener("submit", e => { e.preventDefault(); const v = e.target.elements.namedItem("id").value.trim(); if (v) { state.person = v; render(); } });
}

async function booksPage() {
  const [books, entries, payroll] = await Promise.all([api("/api/books"), api("/api/entries"), api("/api/payroll-export")]);
  const years = books.years.map(y => `<div class="p-5"><div class="text-[12px] text-muted-foreground">${esc(y.year)}</div><div class="tabular mt-1.5 font-semibold">${y.status === "no_recorded_entries" ? "No recorded entries" : `${y.posted_entries} posted · ${y.draft_entries} drafts`}</div></div>`).join("");
  const rows = entries.slice().reverse().slice(0, 150).map(e => {
    const total = e.lines.reduce((s, l) => s + num(l.debit), 0);
    const [label, tone] = e.voided ? ["Voided", "danger"] : e.status === "posted" ? ["Posted", "success"] : ["Draft", "ai"];
    return `<tr class="hover:bg-muted/40"><td class="px-4 py-3 text-muted-foreground">${esc(e.entry_date)}</td><td><details><summary class="font-medium">${esc(e.entry_id)}</summary><div class="mt-2 text-[12px]">${e.lines.map(l => `${esc(l.account)} · ${num(l.debit) ? "Dr " + eur(l.debit) : "Cr " + eur(l.credit)} · ${esc(l.event || l.item || "")}`).join("<br>")}</div></details></td><td>${esc(human(e.type))}</td><td class="tabular text-right">${eur(total)}</td><td class="px-4">${badge(label, tone, true)}${e.status === "draft" && !e.voided ? `<button class="control" data-post="${esc(e.entry_id)}">Post as controller</button>` : ""}</td></tr>`;
  }).join("");
  const pay = payroll.slice(0, 100).map(p => `<tr><td class="px-4 py-2.5">${esc(p.employee)}</td><td>${esc(p.event)}</td><td>${esc(p.method === "flat_40" ? "§40 flat" : "Individual")}</td><td class="tabular text-right">${eur(p.taxable_amount)}</td><td class="px-4">${badge(p.status, p.status === "accepted" ? "success" : "ai")}</td></tr>`).join("");
  view.innerHTML = title("Books", `Last ten years · ${books.from_year}–${books.to_year}. Only recorded entries are shown; a year without records is not a zero balance.`) +
    `<div class="grid grid-cols-2 divide-x overflow-hidden rounded-lg border bg-card lg:grid-cols-4">${years}</div>` +
    `<div class="overflow-hidden rounded-lg border bg-card"><table class="w-full"><thead class="border-b text-left text-[12px] text-muted-foreground"><tr><th class="px-4 py-2.5 font-medium">Date</th><th class="font-medium">Entry</th><th class="font-medium">Type</th><th class="text-right font-medium">Amount</th><th class="px-4 font-medium">Status</th></tr></thead><tbody class="divide-y">${rows || `<tr><td class="px-4 py-3 text-muted-foreground" colspan="5">No entries.</td></tr>`}</tbody></table></div>` +
    section(`Payroll export drafts (${payroll.length})`, pay ? `<div class="overflow-x-auto"><table class="w-full"><thead class="border-b text-left text-[12px] text-muted-foreground"><tr><th class="px-4 py-2 font-medium">Employee</th><th class="font-medium">Event</th><th class="font-medium">Method</th><th class="text-right font-medium">Taxable</th><th class="px-4 font-medium">Status</th></tr></thead><tbody class="divide-y">${pay}</tbody></table></div>` : empty("No payroll rows. Payroll receives drafts; it runs separately."));
  view.querySelectorAll("[data-post]").forEach(b => b.addEventListener("click", async () => {
    const reason = window.prompt("Controller posting reason (all required approvals must already be complete):");
    if (!reason || !reason.trim()) return;
    b.disabled = true;
    try { await api(`/api/entries/${encodeURIComponent(b.dataset.post)}/post`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({reason})}); await render(); }
    catch (error) { view.insertAdjacentHTML("afterbegin", banner(error.message)); b.disabled = false; }
  }));

}

async function auditPage() {
  const audit = await api("/api/audit");
  const rows = audit.rows.slice(-200).reverse().map(r => `<tr><td class="px-4 py-3 text-muted-foreground">${esc(when(r.at))}</td><td>${esc(actor(r.actor))}</td><td><details><summary>${esc(human(r.action))}</summary><pre class="evidence whitespace-pre-wrap break-all mt-2">${esc(r.reason)}\n\n${esc(JSON.stringify(r.basis, null, 2))}</pre></details></td><td class="text-muted-foreground">${esc(r.target)}</td><td class="px-4 text-right font-mono text-[11px] text-muted-foreground">${esc(r.hash.slice(0, 4))}…${esc(r.hash.slice(-4))}</td></tr>`).join("");
  view.innerHTML = title("Audit Log", `Every action is recorded and chained · showing the latest ${Math.min(200, audit.rows.length)} of ${audit.rows.length}`,
      `<span class="flex items-center gap-1.5 ${audit.verified ? "text-success" : "text-danger"}">${I("shield-check")}${audit.verified ? "Chain verified" : "Chain broken"}</span>`) +
    `<div class="overflow-hidden rounded-lg border bg-card"><table class="w-full"><thead class="border-b text-left text-[12px] text-muted-foreground"><tr><th class="px-4 py-2.5 font-medium">Time</th><th class="font-medium">Actor</th><th class="font-medium">Action</th><th class="font-medium">Case</th><th class="px-4 text-right font-medium">Hash</th></tr></thead><tbody class="divide-y">${rows || `<tr><td class="px-4 py-3 text-muted-foreground" colspan="5">No activity yet.</td></tr>`}</tbody></table></div>`;
}

// -- routing -------------------------------------------------------------------------------------------
const ROUTES = {"#/": [dashboard, "Dashboard"], "#/approvals": [approvalsPage, "Approvals"], "#/cases": [casePage, "Case File"],
                "#/people": [peoplePage, "People Ledger"], "#/books": [booksPage, "Books"], "#/audit": [auditPage, "Audit Log"]};
async function render() {
  const key = ROUTES[location.hash] ? location.hash : "#/";
  const [page, name] = ROUTES[key];
  document.title = `${name} · Vouch`;
  document.querySelectorAll("[data-nav]").forEach(a => {
    const active = a.dataset.nav === key;
    a.classList.toggle("bg-muted", active); a.classList.toggle("font-medium", active); a.classList.toggle("text-foreground", active);
    if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  try { await page(); }
  catch (e) { view.innerHTML = banner("Vouch could not load this page. Is the API running? " + e.message); }
}
window.addEventListener("hashchange", render);
api("/api/scenarios").then(s => { state.scenarios = s; }).catch(() => { state.scenarios = []; }).finally(render);
