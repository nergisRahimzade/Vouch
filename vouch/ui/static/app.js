/* Vouch dashboard. Everything shown comes from the API, which reads what the close left in the
   database; every action goes through the controls as the person in the chosen role. */
"use strict";

const $ = (id) => document.getElementById(id);
const S = {
  tab: "overview", role: "controller", people: [], status: null, overview: null, lastJob: null,
  audit: { who: "all", q: "", rows: [], total: 0, verify: null },
  review: { filter: "mine", outcomes: {} },
  cases: { list: [], sel: null, why: null },
  ledger: { period: "", entity: "", status: "", origin: "", sel: null },
  mode: "scripted",
};
const ROLE_NAMES = {
  vouch: "Vouch (agent)", finance_manager: "Finance manager", controller: "Controller", hr: "HR",
  treasury: "Treasury", stock_admin: "Stock admin", department_head: "Department head",
};
const STEP_NAMES = {
  load: "Load files", payroll: "Run payroll per entity", stock_expense: "Run stock expense", reconcile: "Reconcile",
  work_cases: "Work each case", draft_entries: "Draft entries", request_approvals: "Request approvals", report: "Report",
};
const PALETTE = ["#fb7185", "#fbbf24", "#a78bfa", "#2fd9a4", "#38bdf8", "#f472b6", "#facc15", "#60a5fa", "#34d399", "#fb923c"];
const ENTITY_COLORS = { US: "#2fd9a4", UK: "#a78bfa", DE: "#fbbf24", All: "#38bdf8" };

// ---------- helpers ----------
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function human(text) { return String(text ?? "").replace(/_/g, " "); }
function cap(text) { const t = human(text); return t.charAt(0).toUpperCase() + t.slice(1); }
function money(amount, currency) {
  if (amount === null || amount === undefined || amount === "") return "";
  const n = Number(amount);
  if (!Number.isFinite(n)) return esc(amount);
  try {
    return currency ? new Intl.NumberFormat("en-US", { style: "currency", currency }).format(n)
      : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  } catch { return `${amount} ${currency || ""}`; }
}
function when(iso) { return iso ? String(iso).replace("T", " ").slice(0, 16) : ""; }
const PILLS = {
  open: "p-info", held: "p-warn", waiting: "p-acc", resolved: "p-ok", pending: "p-warn", escalated: "p-bad",
  approved: "p-ok", rejected: "p-bad", draft: "p-warn", posted: "p-ok", done: "p-ok", refused: "p-bad",
  failed: "p-bad", "not written": "p-bad", requested: "p-info", reminded: "p-warn", answered: "p-ok", running: "p-info", accepted: "p-ok",
  locked: "p-mute",
};
function pill(status, text) { return `<span class="pill ${PILLS[status] || "p-mute"}">${esc(text ?? cap(status))}</span>`; }
function roleLabel(role) { return ROLE_NAMES[role] || cap(role); }
function personFor(role) {
  if (role === "vouch") return "Vouch";
  const p = S.people.find((x) => x.role === role);
  return p ? p.name : roleLabel(role);
}
function shortId(id) { return String(id || "").replace(/^F-/, "F-").slice(0, 14); }
function store(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } }
function load(key) { try { return localStorage.getItem(key); } catch { return null; } }

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let body = null;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const error = new Error(body?.reason || body?.detail || `${response.status} ${response.statusText}`);
    error.status = response.status; error.body = body;
    throw error;
  }
  return body;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

function notice(text, kind = "info") {
  const n = $("notice");
  n.className = `notice ${kind}`; n.innerHTML = text; n.hidden = false;
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => { n.hidden = true; }, kind === "bad" ? 12000 : 6000);
}
function refusalText(error) {
  if (error.body?.refused) return `Refused by the controls (${esc(error.body.rule)}): ${esc(error.body.reason)}. The attempt is logged in the audit trail.`;
  return esc(error.message);
}
function emptyState(message, withRebuild) {
  return `<div class="card"><div class="empty">${message}${withRebuild
    ? `<div style="margin-top:14px"><button class="btn pri" data-go="run">Open Run &amp; sandbox</button></div>` : ""}</div></div>`;
}
function bindGo(root) {
  root.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
}

// ---------- navigation ----------
const TITLES = { overview: "Overview", audit: "Audit trail", review: "Human review", cases: "Cases", ledger: "Ledger", results: "Results", run: "Run & sandbox" };
function go(tab) {
  S.tab = tab;
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== `p-${tab}`; });
  document.querySelectorAll(".nav").forEach((n) => n.setAttribute("aria-selected", String(n.dataset.tab === tab)));
  $("title").textContent = TITLES[tab];
  store("vouch.tab", tab);
  refresh();
}
document.querySelectorAll(".nav").forEach((n) => n.addEventListener("click", () => go(n.dataset.tab)));

async function refresh() {
  try {
    await ({ overview: renderOverview, audit: renderAudit, review: renderReview, cases: renderCases,
      ledger: renderLedger, results: renderResults, run: renderRun })[S.tab]();
  } catch (error) {
    const panel = $(`p-${S.tab}`);
    if (error.status === 409 && /no database/.test(error.message)) {
      panel.innerHTML = emptyState("No company is loaded yet. Rebuild it from the fixed seed to start.", true);
    } else if (error.status === 503) {
      panel.innerHTML = emptyState(esc(error.message), false);
    } else {
      panel.innerHTML = emptyState(`Could not load this view: ${esc(error.message)}`, false);
    }
    bindGo(panel);
  }
}

// ---------- overview ----------
function donut(kinds) {
  const entries = Object.entries(kinds).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((a, [, n]) => a + n, 0);
  const R = 60, C = 2 * Math.PI * R, gap = total > 1 ? 4 : 0;
  let off = 0;
  let svg = `<circle cx="80" cy="80" r="${R}" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="16"/>`;
  entries.forEach(([, n], i) => {
    const len = C * n / total, seg = Math.max(len - gap, 1);
    svg += `<circle cx="80" cy="80" r="${R}" fill="none" stroke="${PALETTE[i % PALETTE.length]}" stroke-width="16" stroke-linecap="round" stroke-dasharray="${seg} ${C - seg}" stroke-dashoffset="${-off}" transform="rotate(-90 80 80)"/>`;
    off += len;
  });
  svg += `<text x="80" y="84" text-anchor="middle" fill="#f2f5fb" font-size="32" font-weight="700" font-family="Plus Jakarta Sans, sans-serif">${total}</text>
          <text x="80" y="104" text-anchor="middle" fill="#9099b3" font-size="11" font-family="Plus Jakarta Sans, sans-serif">open cases</text>`;
  const legend = entries.map(([k, n], i) => `<li><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(cap(k))}<b class="num">${n}</b></li>`).join("");
  return total ? `<div class="donutwrap"><svg id="donut" viewBox="0 0 160 160" width="190" height="190" role="img" aria-label="Open cases by kind">${svg}</svg><ul class="legend">${legend}</ul></div>`
    : `<div class="empty">No open cases.</div>`;
}
function meters(byEntity) {
  const entries = Object.entries(byEntity).sort();
  const max = Math.max(1, ...entries.map(([, n]) => n));
  if (!entries.length) return `<div class="empty">No open cases.</div>`;
  return `<div class="meter">${entries.map(([e, n]) => {
    const color = ENTITY_COLORS[e] || "#38bdf8";
    return `<div><div class="row2"><span>${esc(e === "All" ? "Company-wide" : e)}</span><span class="num">${n} case${n === 1 ? "" : "s"}</span></div>
      <div class="track"><div class="fill" style="width:${(n / max) * 100}%;background:${color};box-shadow:0 0 10px ${color}80"></div></div></div>`;
  }).join("")}</div>`;
}
function stepDot(step) {
  if (step.state === "running") return "running";
  if (step.state === "todo") return "todo";
  if (step.outcome === "refused" || step.outcome === "failed") return "refused";
  return "";
}
function runLabel(run) {
  if (!run) return "No close has run yet";
  const how = run.mode === "scripted" ? "SCRIPTED playbook (not the model)" : `live model ${run.model}`;
  return `${run.run_id} · ${how} · started ${when(run.started_at)} UTC · cost $${run.cost_usd}`;
}

async function renderOverview() {
  const o = await api("/api/overview");
  S.overview = o;
  updateBadges(o);
  $("subtitle").textContent = `${o.company.name || ""} · open month ${o.company.open_month} · simulated clock ${when(o.clock)}`;
  const st = o.cases.by_status, open = o.cases.total - (st.resolved || 0);
  const stepsDone = o.steps.filter((s) => s.state === "done").length;
  const waitingCount = o.requests.length + o.questions.length;
  const needs = [
    ...o.requests.map((r) => ({ what: `${cap(r.kind)} · ${r.subject_ref}`, id: r.request_id, role: r.assigned_role, since: r.created_at, amount: money(r.amount, r.currency), status: r.status })),
    ...o.questions.map((q) => ({ what: q.fact_needed, id: q.question_id, role: q.to_role, since: q.asked_at, amount: "", status: "open", person: q.to_person })),
  ];
  const panel = $("p-overview");
  panel.innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="l">Close of ${esc(o.company.open_month)}</div><div class="v num">${stepsDone} of 8</div>
        <div class="s">${o.run ? `${esc(o.run.run_id)} · ${o.run.mode === "scripted" ? '<span class="warn">scripted</span>' : `model ${esc(o.run.model)}`}` : "not run yet"}</div></div>
      <div class="kpi"><div class="l">Open cases</div><div class="v num">${open}</div>
        <div class="s">${st.held || 0} held · ${st.waiting || 0} waiting · ${st.resolved || 0} resolved</div></div>
      <div class="kpi"><div class="l">Waiting on a person</div><div class="v num ${waitingCount ? "warn" : ""}">${waitingCount}</div>
        <div class="s">${o.requests.length} approvals · ${o.questions.length} questions · ${o.holds} holds</div></div>
      <div class="kpi"><div class="l">Audit chain</div><div class="v ${o.audit.ok ? "ok" : "bad"}">${o.audit.ok ? "Intact" : "Broken"}</div>
        <div class="s num">${o.audit.rows.toLocaleString()} rows verified${o.audit.ok ? "" : ` · first bad row ${o.audit.first_bad_seq}`}</div></div>
    </div>
    <div class="grid2">
      <div class="card"><h2>Open cases by kind</h2><div class="body">${donut(o.cases.open_by_kind)}</div></div>
      <div class="card"><h2>Open cases by entity <span class="pill p-mute">${o.refusals} refusals logged · ${o.suspicious_emails} suspicious emails</span></h2>
        <div class="body">${meters(o.cases.open_by_entity)}
          <div style="margin-top:22px" class="why">Periods</div>
          <div class="periods" style="margin-top:6px">${o.periods.map((p) => `<span class="${p.status === "open" ? "open" : ""}" title="${esc(p.status)}">${esc(p.period)}</span>`).join("")}</div>
        </div></div>
    </div>
    <div class="grid2">
      <div class="card"><h2>Close timeline <span class="muted mono" style="font-weight:400">${esc(runLabel(o.run))}</span></h2>
        <div class="body"><ul class="steps">${o.steps.map((s) => `
          <li><button class="head" data-step="${s.number}" aria-expanded="false">
            <span class="dot ${stepDot(s)}"></span>
            <span><b>${s.number}. ${esc(STEP_NAMES[s.name])}</b> <span class="muted">${esc(s.detail || "")}</span></span>
            <span class="mono muted">${s.at ? esc(when(s.at).slice(11)) : s.state === "running" ? "running" : ""}</span></button>
            <div class="more" hidden>${s.basis ? `<pre class="json">${esc(JSON.stringify(s.basis, null, 2))}</pre>` : '<span class="muted">Not reached yet.</span>'}</div></li>`).join("")}
        </ul></div></div>
      <div class="card"><h2>Close report ${o.run ? pill(o.run.report_status || o.run.status, o.run.report_status === "accepted" ? "Claim check passed" : o.run.report_status === "rejected" ? "Claim check rejected" : cap(o.run.status)) : ""}</h2>
        <div class="body">${o.run?.report ? `<div class="report">${esc(o.run.report)}</div>
          <div class="why" style="margin-top:10px">Written by ${o.run.mode === "scripted" ? "the scripted playbook" : esc(o.run.model)}; every number, date, rule and name was checked against tool results.</div>`
          : o.run?.report_status === "not written"
            ? `<div class="refusal">${esc((JSON.parse(o.run.report_problems || "[]")[0]) || "The report was not written.")}. Everything else this run did is kept; run the close again to finish it.</div>`
            : `<div class="empty">The report appears here when a close finishes.</div>`}</div></div>
    </div>
    <div class="grid2">
      <div class="card"><h2>Needs a human <button class="btn sm" data-go="review">Open review</button></h2>
        ${needs.length ? `<div class="scroll"><table><thead><tr><th>Waiting for</th><th>What</th><th>Since</th><th class="r">Amount</th></tr></thead><tbody>
          ${needs.map((n) => `<tr class="row" data-go-row="review"><td class="nowrap">${pill(n.status === "escalated" ? "escalated" : "pending", roleLabel(n.role))}<div class="why">${esc(n.person || personFor(n.role))}</div></td>
            <td>${esc(n.what)} <span class="mono muted">${esc(n.id)}</span></td><td class="mono nowrap">${esc(when(n.since))}</td><td class="r mono nowrap">${n.amount}</td></tr>`).join("")}
        </tbody></table></div>` : `<div class="empty">Nothing is waiting on a person.</div>`}</div>
      <div class="card"><h2>Recent activity <button class="btn sm" data-go="audit">Open audit trail</button></h2>
        <div class="body"><ul class="trail">${o.recent.map((r) => `<li><span class="mono muted">#${r.seq}</span>
          <span>${pill(r.outcome, r.actor)} <span class="mono">${esc(r.action)}</span> <span class="muted">${esc(r.target)}</span><div class="why">${esc(r.reason)}</div></span></li>`).join("") || '<li class="muted">No activity yet.</li>'}</ul></div></div>
    </div>`;
  panel.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
    const more = b.nextElementSibling; more.hidden = !more.hidden; b.setAttribute("aria-expanded", String(!more.hidden));
  }));
  panel.querySelectorAll("[data-go-row]").forEach((r) => r.addEventListener("click", () => go(r.dataset.goRow)));
  bindGo(panel);
}

function updateBadges(o) {
  const mine = o.requests.filter((r) => r.assigned_role === S.role).length + o.questions.filter((q) => q.to_role === S.role).length;
  const rb = $("reviewBadge"), cb = $("casesBadge");
  rb.textContent = mine; rb.hidden = !mine;
  const open = o.cases.total - (o.cases.by_status.resolved || 0);
  cb.textContent = open; cb.hidden = !open;
}

// ---------- audit trail ----------
async function loadAudit(more) {
  const params = new URLSearchParams({ who: S.audit.who, q: S.audit.q, limit: "100" });
  if (more && S.audit.rows.length) params.set("before", S.audit.rows[S.audit.rows.length - 1].seq);
  const data = await api(`/api/audit?${params}`);
  S.audit.rows = more ? S.audit.rows.concat(data.rows) : data.rows;
  S.audit.total = data.total;
}
async function renderAudit() {
  await loadAudit(false);
  const panel = $("p-audit");
  const v = S.audit.verify;
  const filters = [["all", "All"], ["vouch", "Vouch decisions"], ["tools", "Tool calls"], ["people", "People"], ["refused", "Refused or failed"]];
  panel.innerHTML = `
    <div class="bar">
      <div class="chips" role="group" aria-label="Filter">${filters.map(([k, t]) => `<button class="chip" data-who="${k}" aria-pressed="${S.audit.who === k}">${t}</button>`).join("")}</div>
      <div class="row-actions">
        <input type="search" id="auditQ" placeholder="Search case, action, person, reason" value="${esc(S.audit.q)}" aria-label="Search the audit trail" style="min-width:240px">
        <button class="btn pri" id="verifyBtn">Verify chain</button>
      </div>
    </div>
    <div class="banner ${v ? (v.ok ? "ok" : "bad") : "info"}" role="status">${v
      ? (v.ok ? `<b>Chain intact.</b> ${v.rows.toLocaleString()} rows recomputed from the database; every hash matches the row before it.`
        : `<b>Chain broken at row ${v.first_bad_seq}.</b> ${esc(v.problem)}. Every row after it is untrusted.`)
      : `<b>Append-only and hash-chained.</b> Each row stores the hash of the row before it, so changing any past row breaks the chain. The database refuses updates and deletes.`}</div>
    <div class="card"><h2>${S.audit.total.toLocaleString()} rows <span class="muted" style="font-weight:400;font-size:12px">newest first · click a row for its basis</span></h2>
      <div class="scroll"><table><thead><tr><th>#</th><th>At (simulated)</th><th>Who</th><th>Action</th><th>Target</th><th>Outcome</th><th>Why</th></tr></thead>
      <tbody id="auditBody"></tbody></table></div>
      <div class="body" style="text-align:center" ${S.audit.rows.length >= S.audit.total ? "hidden" : ""}><button class="btn" id="auditMore">Load older rows</button></div>
    </div>`;
  fillAudit();
  panel.querySelectorAll("[data-who]").forEach((b) => b.addEventListener("click", () => { S.audit.who = b.dataset.who; renderAudit(); }));
  let timer;
  $("auditQ").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { S.audit.q = e.target.value; renderAudit().then(() => { const q = $("auditQ"); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }); }, 300); });
  $("verifyBtn").addEventListener("click", async () => { S.audit.verify = await api("/api/audit/verify"); renderAudit(); });
  $("auditMore")?.addEventListener("click", async () => { await loadAudit(true); renderAudit(); });
}
function fillAudit() {
  const body = $("auditBody");
  body.innerHTML = S.audit.rows.map((r) => `
    <tr class="row" data-seq="${r.seq}" tabindex="0"><td class="mono num">${r.seq}</td><td class="mono nowrap">${esc(when(r.at))}</td>
      <td class="nowrap">${pill(r.role === "vouch" ? "requested" : "approved", r.actor)}<div class="why">${esc(roleLabel(r.role))}</div></td>
      <td class="mono">${esc(r.action)}</td><td class="mono">${esc(r.target)}</td><td>${pill(r.outcome)}</td><td>${esc(r.reason.length > 160 ? r.reason.slice(0, 160) + "…" : r.reason)}</td></tr>
    <tr class="detail" hidden><td colspan="7"><dl class="kv"><dt>Reason</dt><dd>${esc(r.reason)}</dd><dt>Basis</dt><dd><pre class="json">${esc(JSON.stringify(r.basis, null, 2))}</pre></dd>
      <dt>Previous hash</dt><dd class="mono">${esc(r.prev_hash)}</dd><dt>This hash</dt><dd class="mono">${esc(r.hash)}</dd></dl></td></tr>`).join("")
    || `<tr><td colspan="7" class="empty">No rows match.</td></tr>`;
  body.querySelectorAll("tr.row").forEach((tr) => {
    const toggle = () => { tr.nextElementSibling.hidden = !tr.nextElementSibling.hidden; };
    tr.addEventListener("click", toggle);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") toggle(); });
  });
}

// ---------- human review ----------
function needsChain(r) {
  return r.needs.map((role, i) => {
    const done = r.approvals[i];
    const state = done ? "approved" : r.status === "rejected" ? "rejected" : i === r.approvals.length ? "pending" : "locked";
    return pill(state, `${i + 1}. ${roleLabel(role)}${done ? " ✓" : ""}`);
  }).join(" ");
}
async function renderReview() {
  const data = await api("/api/inbox");
  const role = S.role, open = (r) => r.status === "pending" || r.status === "escalated";
  const f = S.review.filter;
  const requests = data.requests.filter((r) => f === "mine" ? open(r) && r.assigned_role === role : f === "open" ? open(r) : !open(r));
  const questions = data.questions.filter((q) => q.status === "open" && (f !== "mine" || q.to_role === role || role === "controller"));
  const panel = $("p-review");
  const outcome = (key) => S.review.outcomes[key] ? `<div class="${S.review.outcomes[key].ok ? "banner ok" : "refusal"}">${S.review.outcomes[key].text}</div>` : "";
  const canTry = (r) => open(r) && (r.assigned_role === role || role === "vouch");
  panel.innerHTML = `
    <div class="banner ${role === "vouch" ? "warn" : "info"}">${role === "vouch"
      ? `<b>Acting as Vouch.</b> The agent can open requests, ask and draft, but it cannot approve, post, release a hold or move money. Try it: the controls refuse in code and log the attempt.`
      : `<b>Acting as ${esc(personFor(role))}, ${esc(roleLabel(role))}.</b> Approvals need a note. Whoever requested or keyed a change can never approve it, and large entries need two different approvers including the controller.`}</div>
    <div class="bar"><div class="chips" role="group" aria-label="Show">${[["mine", "Waiting for this role"], ["open", "All open"], ["closed", "Decided"]].map(([k, t]) => `<button class="chip" data-filter="${k}" aria-pressed="${f === k}">${t}</button>`).join("")}</div></div>
    <div class="card"><h2>Approval requests <span class="muted" style="font-weight:400">${requests.length}</span></h2>
      ${requests.map((r) => `
        <div class="req ${open(r) ? "" : "closed"}">
          <div><h3>${esc(cap(r.kind))} · <span class="mono">${esc(r.subject_ref)}</span> ${pill(r.status)} <span class="mono muted">${esc(r.request_id)}</span></h3>
            ${r.case_summary ? `<div>${esc(r.case_summary)}</div>` : ""}
            <div class="why" style="margin-top:6px">${r.amount ? `<b class="mono" style="color:var(--fg)">${money(r.amount, r.currency)}</b> · ` : ""}requested by ${esc(r.requested_by)} · ${esc(when(r.created_at))}${r.reminded_at ? " · reminded" : ""}${r.escalated_at ? " · escalated to the controller" : ""}${r.case_id ? ` · case <a href="#" data-case="${esc(r.case_id)}" class="mono">${esc(r.case_id)}</a>` : ""}</div>
            <div style="margin-top:8px" class="row-actions">${needsChain(r)}</div>
            ${r.approvals.map((a) => `<div class="why">✓ ${esc(a.actor)} (${esc(roleLabel(a.role))}), ${esc(when(a.at))}: “${esc(a.note)}”</div>`).join("")}
            ${r.status === "rejected" ? `<div class="why bad">Rejected: “${esc(r.note)}”. This is now a precedent; Vouch will not resend it unchanged.</div>` : ""}
          </div>
          <div class="acts">${canTry(r) ? `
            <textarea id="note-${esc(r.request_id)}" placeholder="Note (required)" aria-label="Note for ${esc(r.request_id)}"></textarea>
            ${r.kind === "bank_change" ? `<label class="why"><input type="checkbox" id="cb-${esc(r.request_id)}"> I called back the number on file</label>` : ""}
            <div class="row-actions"><button class="btn pri" data-decide="approve" data-id="${esc(r.request_id)}">${role === "vouch" ? "Try to approve" : "Approve"}</button>
              <button class="btn dng" data-decide="reject" data-id="${esc(r.request_id)}">Reject</button></div>`
            : open(r) ? `<span class="why">Waiting for ${esc(personFor(r.assigned_role))} (${esc(roleLabel(r.assigned_role))}).</span>` : ""}
            ${outcome(r.request_id)}</div>
        </div>`).join("") || `<div class="empty">${f === "mine" ? `Nothing is waiting for the ${esc(roleLabel(role))}.` : "Nothing here."}</div>`}
    </div>
    ${f !== "closed" ? `<div class="card"><h2>Questions from Vouch <span class="muted" style="font-weight:400">${questions.length}</span></h2>
      ${questions.map((q) => `
        <div class="req"><div><h3>${esc(q.fact_needed)} <span class="mono muted">${esc(q.question_id)}</span></h3>
          <div class="why">To ${esc(q.to_person)} (${esc(roleLabel(q.to_role))}) · asked ${esc(when(q.asked_at))}${q.reminded_at ? " · reminded" : ""}${q.escalated_at ? " · escalated" : ""} · case <a href="#" data-case="${esc(q.case_id)}" class="mono">${esc(q.case_id)}</a></div>
          ${q.case_summary ? `<div style="margin-top:6px">${esc(q.case_summary)}</div>` : ""}
          <dl class="kv" style="margin-top:8px"><dt>Sources disagree</dt><dd class="mono">${q.sources.map(esc).join("<br>")}</dd>
            <dt>If yes</dt><dd>${esc(human(q.if_yes))}</dd><dt>If no</dt><dd>${esc(human(q.if_no))}</dd><dt>If partial</dt><dd>${esc(human(q.if_partial))}</dd></dl></div>
          <div class="acts">${q.to_role === role || role === "controller" || role === "vouch" ? `
            <textarea id="qnote-${esc(q.question_id)}" placeholder="Your answer (required)" aria-label="Answer for ${esc(q.question_id)}"></textarea>
            <div class="row-actions">${["yes", "no", "partial"].map((a) => `<button class="btn ${a === "yes" ? "pri" : ""}" data-answer="${a}" data-id="${esc(q.question_id)}">${cap(a)}</button>`).join("")}</div>`
            : `<span class="why">For ${esc(roleLabel(q.to_role))}.</span>`}${outcome(q.question_id)}</div></div>`).join("") || `<div class="empty">No open questions${f === "mine" ? " for this role" : ""}.</div>`}
    </div>` : ""}
    <div class="grid2 even">
      <div class="card"><h2>Approved, ready to post</h2>
        ${data.ready_to_post.map((e) => `<div class="req"><div><h3 class="mono">${esc(e)}</h3><div class="why">Every approval is in. Only the controller posts.</div></div>
          <div class="acts"><button class="btn pri" data-post="${esc(e)}">${role === "controller" ? "Post entry" : `Try to post as ${esc(roleLabel(role))}`}</button>${outcome(e)}</div></div>`).join("") || `<div class="empty">No approved drafts waiting.</div>`}</div>
      <div class="card"><h2>Payment run</h2><div class="body">
        <p class="why" style="margin-top:0">Release every open-month payment that is not held. Held payments are refused one by one by the controls.</p>
        <button class="btn pri" id="payRun">${role === "treasury" ? "Release unheld payments" : `Try as ${esc(roleLabel(role))}`}</button>${outcome("payrun")}</div></div>
    </div>
    <div class="card"><h2>Active holds <span class="muted" style="font-weight:400">${data.holds.length}</span></h2>
      ${data.holds.length ? `<div class="scroll"><table><thead><tr><th>Hold</th><th>On</th><th>Reason</th><th>Placed</th><th></th></tr></thead><tbody>
        ${data.holds.map((h) => `<tr><td class="mono">#${h.hold_id}<div class="why"><a href="#" data-case="${esc(h.case_id)}">${esc(h.case_id)}</a></div></td><td class="mono">${esc(h.target_kind)}:${esc(h.target_ref)}</td>
          <td>${esc(h.reason)}</td><td class="nowrap">${esc(h.placed_by)}<div class="why">${esc(when(h.placed_at))}</div></td>
          <td style="min-width:220px"><input type="text" id="hnote-${h.hold_id}" placeholder="Release note" aria-label="Release note for hold ${h.hold_id}" style="width:100%">
            <div style="margin-top:6px"><button class="btn sm" data-release="${h.hold_id}">Release as ${esc(roleLabel(role))}</button></div>${outcome(`hold-${h.hold_id}`)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No active holds.</div>`}</div>`;

  panel.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => { S.review.filter = b.dataset.filter; renderReview(); }));
  panel.querySelectorAll("[data-case]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); S.cases.sel = a.dataset.case; go("cases"); }));
  const act = async (key, fn, okText) => {
    try { const result = await fn(); S.review.outcomes[key] = { ok: true, text: okText(result) }; notice(okText(result), "ok"); }
    catch (error) { S.review.outcomes[key] = { ok: false, text: refusalText(error) }; notice(refusalText(error), "bad"); }
    renderReview(); refreshBadges();
  };
  panel.querySelectorAll("[data-decide]").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.id;
    act(id, () => post(`/api/requests/${encodeURIComponent(id)}/decide`, {
      role, decision: b.dataset.decide, note: $(`note-${id}`).value, callback_confirmed: !!$(`cb-${id}`)?.checked,
    }), (r) => `${esc(r.by)} ${b.dataset.decide === "approve" ? "approved" : "rejected"} ${esc(id)}; it is now ${esc(r.status)}${r.status === "pending" ? `, next ${esc(roleLabel(r.assigned_role))}` : ""}.`);
  }));
  panel.querySelectorAll("[data-answer]").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.id;
    act(id, () => post(`/api/questions/${encodeURIComponent(id)}/answer`, { role, answer: b.dataset.answer, note: $(`qnote-${id}`).value }),
      (r) => `${esc(r.by)} answered ${esc(r.answer)}; next step: ${esc(human(r.next_step))}.`);
  }));
  panel.querySelectorAll("[data-post]").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.post;
    act(id, () => post(`/api/entries/${encodeURIComponent(id)}/post`, { role }), () => `${esc(id)} posted to the ledger.`);
  }));
  panel.querySelectorAll("[data-release]").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.release;
    act(`hold-${id}`, () => post(`/api/holds/${id}/release`, { role, note: $(`hnote-${id}`).value }), () => `Hold #${esc(id)} released.`);
  }));
  $("payRun").addEventListener("click", () => act("payrun", () => post("/api/payments/release", { role }),
    (r) => `${r.released} payments released; ${r.refused.length} refused because they are held.`));
}
async function refreshBadges() { try { updateBadges(await api("/api/overview")); } catch { /* shown on the tab itself */ } }

// ---------- cases ----------
async function renderCases() {
  const data = await api("/api/cases");
  S.cases.list = data.cases;
  if (!S.cases.sel && data.cases.length) S.cases.sel = data.cases[0].case_id;
  const panel = $("p-cases");
  if (!data.cases.length) { panel.innerHTML = emptyState("No cases yet. Run the close to reconcile the sources.", true); bindGo(panel); return; }
  panel.innerHTML = `<div class="split">
    <div class="card"><h2>${data.cases.length} cases in the ${esc(data.close_period)} close</h2><div class="scroll"><table>
      <thead><tr><th>Case</th><th>Entity</th><th class="r">Amount</th><th>Status</th></tr></thead><tbody>
      ${data.cases.map((c) => `<tr class="row ${c.case_id === S.cases.sel ? "sel" : ""}" data-id="${esc(c.case_id)}" tabindex="0">
        <td><b>${esc(cap(c.kind))}</b><div class="mono muted">${esc(c.case_id)}${c.period && c.period !== data.close_period ? ` · from ${esc(c.period)}` : ""}</div></td>
        <td>${esc(c.entity || "—")}</td><td class="r mono nowrap">${money(c.amount, c.currency)}</td><td>${pill(c.status)}</td></tr>`).join("")}
      </tbody></table></div></div>
    <div class="sticky" id="caseDetail"><div class="card"><div class="empty">Loading…</div></div></div></div>`;
  panel.querySelectorAll("tr.row").forEach((tr) => {
    const pick = () => { S.cases.sel = tr.dataset.id; S.cases.why = null; panel.querySelectorAll("tr.row").forEach((x) => x.classList.toggle("sel", x === tr)); renderCaseDetail(); };
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
  });
  renderCaseDetail();
}
async function renderCaseDetail() {
  const box = $("caseDetail");
  const d = await api(`/api/cases/${encodeURIComponent(S.cases.sel)}`);
  const f = d.finding || {}, c = d.case;
  const trusted = String(f.trusted_source || "");
  const evidence = (f.evidence || []).map((e) => `
    <div class="srcbox ${trusted.includes(e.source) ? "trusted" : ""}"><div class="t"><span>${esc(human(e.source))}</span>${trusted.includes(e.source) ? '<span class="ok">Trusted</span>' : ""}</div>
      <div class="big">${esc(e.says)}</div><div class="mono muted" style="margin-top:4px">${esc(e.path)}${e.rows?.length ? ` · row${e.rows.length > 1 ? "s" : ""} ${esc(e.rows.slice(0, 6).join(", "))}${e.rows.length > 6 ? "…" : ""}` : ""}</div></div>`).join("");
  const why = S.cases.why;
  box.innerHTML = `<div class="card"><h2><span>${esc(cap(c.kind))} <span class="mono muted" style="font-weight:400">${esc(c.case_id)}</span></span>${pill(c.status)}</h2>
    <div class="body" style="display:flex;flex-direction:column;gap:16px">
      <div>${esc(c.summary)}</div>
      ${c.note ? `<div class="banner info"><span><b>Latest:</b> ${esc(c.note)}</span></div>` : ""}
      ${evidence ? `<div><div class="why" style="margin-bottom:6px">Sources compared</div><div class="src">${evidence}</div></div>` : ""}
      <dl class="kv">
        ${f.trusted_source ? `<dt>Trusted source</dt><dd><b>${esc(human(f.trusted_source))}</b><div class="why">${esc(f.trust_reason || "")}</div></dd>` : ""}
        ${f.rules?.length ? `<dt>Rules</dt><dd class="mono">${f.rules.map(esc).join(", ")}</dd>` : ""}
        ${f.precedents?.length ? `<dt>Precedents</dt><dd>${f.precedents.map((p) => `<div><span class="mono">${esc(p.ruling_id)}</span> ${pill(p.decision === "rejected" ? "rejected" : "approved", cap(p.decision))} ${esc(p.precedent)} <span class="why">· ${esc(p.decided_by)}, ${esc(p.period)}</span></div>`).join("")}</dd>` : ""}
        ${f.amount ? `<dt>Amount</dt><dd><b class="mono">${money(f.amount, f.currency)}</b><div class="why">${esc(f.amount_basis || "")}</div></dd>` : ""}
        ${f.proposed_action ? `<dt>Proposed action</dt><dd>${esc(human(f.proposed_action))}</dd>` : ""}
        ${f.ask_person ? `<dt>Who to ask</dt><dd>${esc(f.ask_person)} (${esc(roleLabel(f.ask_role))})</dd>` : ""}
        <dt>Opened</dt><dd class="mono">${esc(when(c.opened_at))} · updated ${esc(when(c.updated_at))}</dd>
      </dl>
      ${d.holds.length ? `<div><div class="why">Holds</div>${d.holds.map((h) => `<div>${pill(h.released_at ? "resolved" : "held", h.released_at ? "Released" : "Held")} <span class="mono">${esc(h.target_kind)}:${esc(h.target_ref)}</span> ${esc(h.reason)}${h.released_at ? ` <span class="why">released by ${esc(h.released_by)}: ${esc(h.release_note)}</span>` : ""}</div>`).join("")}</div>` : ""}
      ${d.questions.length ? `<div><div class="why">Asked</div>${d.questions.map((q) => `<div style="margin-top:4px">${pill(q.status)} <b>${esc(q.to_person)}</b>: ${esc(q.fact_needed)}${q.answer ? `<div class="why">Answered <b>${esc(q.answer)}</b> by ${esc(q.answered_by)}: “${esc(q.note)}”</div>` : ""}</div>`).join("")}</div>` : ""}
      ${d.requests.length ? `<div><div class="why">Approval requests</div>${d.requests.map((r) => `<div style="margin-top:4px">${pill(r.status)} <span class="mono">${esc(r.request_id)}</span> ${esc(cap(r.kind))} ${esc(r.subject_ref)} ${r.amount ? `<span class="mono">${money(r.amount, r.currency)}</span>` : ""} <span class="why">· needs ${r.needs.map(roleLabel).map(esc).join(" then ")}</span></div>`).join("")}</div>` : ""}
      ${d.drafts.map((e) => `<div><div class="why">Entry ${esc(e.entry_id)} ${pill(e.status)} · ${esc(e.reason)}</div><div class="scroll"><table><thead><tr><th>Account</th><th>Dept</th><th class="r">Debit</th><th class="r">Credit</th></tr></thead><tbody>
        ${e.lines.map((l) => `<tr><td class="mono">${esc(l.account)}</td><td>${esc(l.department || "")}</td><td class="r mono">${Number(l.debit) ? money(l.debit, e.currency) : ""}</td><td class="r mono">${Number(l.credit) ? money(l.credit, e.currency) : ""}</td></tr>`).join("")}</tbody></table></div></div>`).join("")}
      <div class="card" style="background:var(--surface2)"><div class="body">
        <div class="why" style="margin-bottom:6px">Ask Vouch to defend this case. It answers only from this case's audit trail, and the claim check rejects anything the trail does not show.</div>
        <div class="form"><input type="text" id="whyQ" value="Why did you do this, and what did you rely on?" aria-label="Question" style="flex:1;min-width:200px">
          ${modeSelect("whyMode")}<button class="btn pri" id="whyBtn">Ask why</button></div>
        ${why ? `<div style="margin-top:10px">${why.ok ? `<div class="answer">${esc(why.answer)}</div><div class="why" style="margin-top:4px">${pill("accepted", "Claim check passed")} ${why.mode === "scripted" ? "scripted answer" : "answered by the model"} · cites audit rows ${esc((why.rows || []).slice(0, 12).join(", "))}${(why.rows || []).length > 12 ? "…" : ""}</div>`
          : `<div class="refusal">${why.error ? esc(why.error) : `The answer was rejected by the claim check: ${esc((why.problems || []).join("; "))}`}</div>`}</div>` : ""}
      </div></div>
      <div><div class="why" style="margin-bottom:6px">Audit trail for this case (${d.trail.length} rows)</div>
        <ul class="trail">${d.trail.slice(-40).map((r) => `<li><span class="mono muted">#${r.seq}</span><span>${pill(r.outcome, r.actor)} <span class="mono">${esc(r.action)}</span> <span class="mono muted">${esc(when(r.at))}</span><div class="why">${esc(r.reason.length > 220 ? r.reason.slice(0, 220) + "…" : r.reason)}</div></span></li>`).join("")}</ul></div>
    </div></div>`;
  $("whyBtn").addEventListener("click", async () => {
    $("whyBtn").disabled = true; $("whyBtn").textContent = "Asking…";
    try { S.cases.why = await post(`/api/cases/${encodeURIComponent(c.case_id)}/why`, { question: $("whyQ").value, mode: $("whyMode").value }); }
    catch (error) { S.cases.why = { ok: false, error: error.message }; }
    renderCaseDetail();
  });
}
function modeSelect(id) {
  const live = S.status?.model_available;
  return `<select id="${id}" aria-label="Who answers"><option value="scripted" ${S.mode === "scripted" ? "selected" : ""}>Scripted</option>
    <option value="model" ${S.mode === "model" ? "selected" : ""} ${live ? "" : "disabled"}>Live model${live ? "" : " (no API key)"}</option></select>`;
}

// ---------- ledger ----------
async function renderLedger() {
  const L = S.ledger;
  const params = new URLSearchParams({ entity: L.entity, status: L.status, origin: L.origin });
  if (L.period) params.set("period", L.period);
  const data = await api(`/api/ledger?${params}`);
  L.period = data.period;
  const panel = $("p-ledger");
  const sel = (id, label, value, options) => `<label>${label} <select id="${id}">${options.map(([v, t]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>`;
  panel.innerHTML = `
    <div class="form">
      ${sel("lPeriod", "Period", data.period, data.periods.map((p) => [p.period, `${p.period} (${p.status})`]))}
      ${sel("lEntity", "Entity", L.entity, [["", "All"], ["US", "US"], ["UK", "UK"], ["DE", "DE"]])}
      ${sel("lStatus", "Status", L.status, [["", "All"], ["draft", "Draft"], ["posted", "Posted"]])}
      ${sel("lOrigin", "Booked by", L.origin, [["", "All"], ["vouch", "Vouch drafts"], ["booked_ledger", "History as received"]])}
    </div>
    <div class="split">
      <div class="card"><h2>${data.entries.length} entries in ${esc(data.period)}</h2>
        ${data.entries.length ? `<div class="scroll"><table><thead><tr><th>Entry</th><th>Entity</th><th>State</th><th class="r">Total</th></tr></thead><tbody>
        ${data.entries.map((e) => `<tr class="row ${e.entry_id === L.sel ? "sel" : ""}" data-id="${esc(e.entry_id)}" tabindex="0"><td><span class="mono">${esc(e.entry_id)}</span><div class="why">${esc(e.reason)}</div></td>
          <td>${esc(e.entity)}</td><td>${pill(e.status)}${e.approval ? `<div class="why">approval ${esc(e.approval)}</div>` : ""}${e.corrects_period ? `<div class="why">corrects ${esc(e.corrects_period)}</div>` : ""}</td>
          <td class="r mono nowrap">${money(e.total, e.currency)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No entries match.</div>`}</div>
      <div class="sticky" id="entryDetail">${L.sel ? "" : `<div class="card"><div class="empty">Pick an entry to see its lines and the source rows behind each one.</div></div>`}</div>
    </div>`;
  const bindSel = (id, key) => $(id).addEventListener("change", (e) => { L[key] = e.target.value; if (key === "period") L.sel = null; renderLedger(); });
  bindSel("lPeriod", "period"); bindSel("lEntity", "entity"); bindSel("lStatus", "status"); bindSel("lOrigin", "origin");
  panel.querySelectorAll("tr.row").forEach((tr) => {
    const pick = () => { L.sel = tr.dataset.id; panel.querySelectorAll("tr.row").forEach((x) => x.classList.toggle("sel", x === tr)); renderEntry(); };
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
  });
  if (L.sel) renderEntry();
}
async function renderEntry() {
  const d = await api(`/api/ledger/${encodeURIComponent(S.ledger.sel)}`);
  const e = d.entry;
  const debit = d.lines.reduce((a, l) => a + Number(l.debit), 0), credit = d.lines.reduce((a, l) => a + Number(l.credit), 0);
  $("entryDetail").innerHTML = `<div class="card"><h2><span class="mono">${esc(e.entry_id)}</span>${pill(e.status)}</h2><div class="body" style="display:flex;flex-direction:column;gap:12px">
    <dl class="kv"><dt>Reason</dt><dd>${esc(e.reason)}</dd><dt>Entity · period</dt><dd>${esc(e.entity)} · ${esc(e.period)} · dated ${esc(e.entry_date)} · ${esc(e.currency)}</dd>
      <dt>Booked by</dt><dd>${esc(e.origin === "vouch" ? "Vouch (draft)" : "history as received")} · ${esc(e.created_by)}</dd>
      ${d.requests.map((r) => `<dt>Approval</dt><dd>${pill(r.status)} ${esc(r.request_id)} ${r.approvals.map((a) => `· ${esc(a.actor)}`).join(" ")}</dd>`).join("")}</dl>
    <div class="scroll"><table><thead><tr><th>#</th><th>Account</th><th>Dept</th><th class="r">Debit</th><th class="r">Credit</th><th>Sources</th></tr></thead><tbody>
      ${d.lines.map((l) => `<tr><td class="mono">${l.line_no}</td><td class="mono">${esc(l.account)}<div class="why">${esc(l.memo || "")}</div></td><td>${esc(l.department || "")}</td>
        <td class="r mono">${Number(l.debit) ? money(l.debit, e.currency) : ""}</td><td class="r mono">${Number(l.credit) ? money(l.credit, e.currency) : ""}</td>
        <td>${l.sources.length ? `<details><summary class="mono">${l.sources.length}${l.sources.length === 25 ? "+" : ""} source${l.sources.length === 1 ? "" : "s"}</summary>${l.sources.map((s) => `<div class="mono muted" style="margin-top:4px">${esc(s.role)} · ${esc(s.path)}${s.row_no ? `#${s.row_no}` : ""}</div>`).join("")}</details>` : '<span class="muted">—</span>'}
          ${l.fx_rate ? `<div class="why">FX ${esc(l.fx_rate)} · ${esc(l.fx_basis)}</div>` : ""}</td></tr>`).join("")}
      <tr><td></td><td colspan="2"><b>Total</b> ${Math.abs(debit - credit) < 0.005 ? pill("approved", "Balanced") : pill("rejected", "Unbalanced")}</td><td class="r mono"><b>${money(debit, e.currency)}</b></td><td class="r mono"><b>${money(credit, e.currency)}</b></td><td></td></tr>
    </tbody></table></div></div></div>`;
}

// ---------- results ----------
async function renderResults() {
  const data = await api("/api/results");
  const panel = $("p-results");
  if (!data.reports.length) {
    panel.innerHTML = emptyState("No scores yet. Every number here comes from a saved evaluator report: run <b>Score</b> in Run &amp; sandbox, or <span class='mono'>python -m vouch score</span>.", true);
    bindGo(panel); return;
  }
  const latest = {};
  data.reports.forEach((r) => { if (!latest[r.set]) latest[r.set] = r; });
  const metric = (m, key) => {
    if (!m) return "";
    if (key === "never") return String(m.never.count);
    const x = m[key];
    if (key !== "false_holds" && x.total === 0) return "n/a";
    return `${x.good}/${x.total}${x.percent !== null && x.percent !== undefined ? ` · ${x.percent}%` : ""}`;
  };
  const rows = [["caught", "Caught"], ["false_holds", "False holds (worst run)"], ["evidence_complete", "Evidence complete"], ["money_correct", "Money correct"], ["never", "Never (forbidden actions)"]];
  panel.innerHTML = `
    <div class="grid2 even">${["main", "heldout", "clean"].filter((s) => latest[s]).map((s) => {
      const r = latest[s];
      if (r.status !== "scored") return `<div class="card"><h2>${esc(cap(s))} set ${pill("locked", "Not scored")}</h2><div class="body"><p class="why">${esc(r.reason)}</p><p class="why">${esc(r.date)} · no numbers are shown for a set that was not scored.</p></div></div>`;
      return `<div class="card"><h2>${esc(cap(s))} set ${r.mode === "scripted" ? pill("pending", "SCRIPTED") : pill("approved", "Live model")}</h2>
        <div class="body"><div class="banner ${r.mode === "scripted" ? "warn" : "info"}" style="margin-bottom:12px">${esc(r.label)} · ${esc(r.date)} · ${r.runs} runs · seed ${esc(r.seed)} · cost $${esc(r.cost_usd)}</div>
        <div class="scroll"><table><thead><tr><th>Measure</th><th class="r">Result</th><th>Target</th><th></th></tr></thead><tbody>
          ${rows.map(([k, t]) => `<tr><td>${t}</td><td class="r mono">${metric(r.metrics, k)}</td><td class="muted">${esc(r.targets?.[k] || "")}</td><td>${r.at_target?.[k] ? pill("approved", "At target") : pill("rejected", "Below target")}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="why" style="margin-top:10px">Books balance: ${r.metrics.money_correct.books_balance ? "yes" : "no"} · runs at target: ${r.metrics.consistency.runs_at_target.filter(Boolean).length}/${r.runs}
          · flaky: ${esc(r.metrics.consistency.flaky.join(", ") || "none")}${r.metrics.caught.missed?.length ? ` · missed: ${esc(r.metrics.caught.missed.join(", "))}` : ""}</div>
        ${s === "main" && r.demo ? `<div style="margin-top:8px">${pill(r.demo.passed ? "approved" : "rejected", r.demo.passed ? "Demo scenario passed" : "Demo scenario not passed")} <span class="why">${r.demo.longest_in_a_row} run(s) in a row at target, needs ${r.demo.needed}</span></div>` : ""}
        </div></div>`;
    }).join("")}</div>
    <div class="card"><h2>All saved reports</h2><div class="scroll"><table><thead><tr><th>Report</th><th>Set</th><th>Produced by</th><th>Date</th><th class="r">Runs</th><th class="r">Cost</th></tr></thead><tbody>
      ${data.reports.map((r) => `<tr><td class="mono">${esc(r.file)}</td><td>${esc(r.set)}</td><td>${esc(r.label)}</td><td class="mono">${esc(r.scored_at || r.date)}</td><td class="r mono">${r.runs}</td><td class="r mono">$${esc(r.cost_usd)}</td></tr>`).join("")}
    </tbody></table></div></div>`;
}

// ---------- run & sandbox ----------
async function renderRun() {
  const kept = {};                                   // keep what the person typed across live refreshes
  ["runEffort", "fuDays", "scoreSet", "scoreRuns"].forEach((id) => { if ($(id)) kept[id] = $(id).value; });
  const status = S.status || await api("/api/status");
  const job = status.job, live = status.model_available, busy = status.running;
  const panel = $("p-run");
  panel.innerHTML = `
    <div class="banner ${live ? "info" : "warn"}">${live ? `<b>Live model available</b> (${esc(status.model)}). Scripted mode stays available for fast checks and is labelled everywhere it is used.`
      : `<b>No ANTHROPIC_API_KEY found</b> (environment or .env), so only the scripted playbook can run. Everything it produces is labelled scripted.`}</div>
    <div class="form"><label>Who works the close <select id="runMode"><option value="scripted" ${S.mode === "scripted" ? "selected" : ""}>Scripted playbook</option><option value="model" ${S.mode === "model" ? "selected" : ""} ${live ? "" : "disabled"}>Live model</option></select></label>
      <label>Effort <select id="runEffort">${["low", "medium", "high", "max"].map((e) => `<option ${e === "medium" ? "selected" : ""}>${e}</option>`).join("")}</select></label></div>
    <div class="grid2 even">
      <div class="card"><h2>Run the month</h2><div class="body" style="display:flex;flex-direction:column;gap:14px">
        <div class="row-actions"><button class="btn pri" data-job="close" ${busy ? "disabled" : ""}>Run the close</button><span class="why">Load, payroll, stock, reconcile, work each case, draft, request approvals, report.</span></div>
        <div class="row-actions"><button class="btn" data-job="colleagues" ${busy ? "disabled" : ""}>Colleagues round</button><span class="why">Simulated approvers answer, decide, post and release, through the same controls.</span></div>
        <div class="row-actions"><button class="btn" data-job="follow-up" ${busy ? "disabled" : ""}>Advance the clock</button>
          <label class="why">by <input type="number" id="fuDays" value="2" min="1" max="10" style="width:64px"> business days</label><span class="why">Unanswered requests are reminded, then escalated to the controller.</span></div>
      </div></div>
      <div class="card"><h2>Judge sandbox</h2><div class="body" style="display:flex;flex-direction:column;gap:14px">
        <div class="row-actions"><button class="btn pri" data-rebuild="main" ${busy ? "disabled" : ""}>Reset to the demo state</button><span class="why">Rebuild the company from the fixed seed with the main planted mistakes.</span></div>
        <div class="row-actions"><button class="btn" data-rebuild="heldout" ${busy ? "disabled" : ""}>Plant the held-out mistakes</button><span class="why">Same company, with the held-out mistakes in the open month. Then run the close.</span></div>
        <div class="row-actions"><button class="btn" data-rebuild="clean" ${busy ? "disabled" : ""}>Clean company</button><span class="why">No planted mistakes, same noise. A good close opens no case.</span></div>
      </div></div>
    </div>
    <div class="card"><h2>Score against the hidden key</h2><div class="body">
      <div class="form"><label>Set <select id="scoreSet"><option value="main">Main</option><option value="heldout">Held-out</option><option value="clean">Clean</option></select></label>
        <label>Runs <input type="number" id="scoreRuns" value="3" min="3" max="10" style="width:64px"></label>
        <button class="btn pri" data-job="score" ${busy ? "disabled" : ""}>Score</button>
        <span class="why">Each run uses a fresh copy of the company. The dashboard's own database is not touched. A live-model score takes a while and costs money.</span></div>
    </div></div>
    <div class="card"><h2>${job ? `${esc(job.label)} ${pill(job.status)}` : "No job has run in this session"}${job ? `<span class="mono muted" style="font-weight:400">${esc(when(job.started_at))}${job.finished_at ? ` → ${esc(when(job.finished_at).slice(11))}` : ""} UTC</span>` : ""}</h2>
      <div class="body">${job ? `${job.error ? `<div class="refusal" style="margin-bottom:10px">${esc(job.error)}</div>` : ""}<pre class="log" id="jobLog">${esc(job.log.map((l) => l.text).join("\n"))}</pre>` : '<span class="muted">Job output appears here.</span>'}</div></div>`;
  Object.entries(kept).forEach(([id, value]) => { if ($(id)) $(id).value = value; });
  const log = $("jobLog"); if (log) log.scrollTop = log.scrollHeight;
  $("runMode").addEventListener("change", (e) => { S.mode = e.target.value; store("vouch.mode", S.mode); });
  const run = async (path, body) => {
    try { await post(path, body); notice("Started. Progress shows here and on the Overview.", "info"); await poll(); }
    catch (error) { notice(esc(error.message), "bad"); }
  };
  panel.querySelectorAll("[data-job]").forEach((b) => b.addEventListener("click", () => {
    const kind = b.dataset.job, mode = $("runMode").value, effort = $("runEffort").value;
    if (kind === "close") run("/api/jobs/close", { mode, effort });
    if (kind === "colleagues") run("/api/jobs/colleagues", { mode });
    if (kind === "follow-up") run("/api/jobs/follow-up", { business_days: Number($("fuDays").value) });
    if (kind === "score") run("/api/jobs/score", { set: $("scoreSet").value, runs: Number($("scoreRuns").value), mode, effort });
  }));
  panel.querySelectorAll("[data-rebuild]").forEach((b) => b.addEventListener("click", () => {
    S.audit.verify = null; S.cases.sel = null; S.ledger.sel = null; S.review.outcomes = {};
    run("/api/jobs/rebuild", { variant: b.dataset.rebuild });
  }));
}

// ---------- roles and polling ----------
async function loadPeople() {
  try { S.people = (await api("/api/people")).people; } catch { S.people = []; }
  const sel = $("role");
  sel.innerHTML = Object.keys(ROLE_NAMES).map((r) => `<option value="${r}" ${r === S.role ? "selected" : ""}>${esc(roleLabel(r))}${r === "vouch" || r === "department_head" ? "" : ` · ${esc(personFor(r))}`}</option>`).join("");
}
$("role").addEventListener("change", (e) => {
  S.role = e.target.value; store("vouch.role", S.role); S.review.outcomes = {};
  if (S.overview) updateBadges(S.overview);
  if (S.tab === "review" || S.tab === "overview") refresh();
});

async function poll() {
  let status;
  try { status = await api("/api/status"); } catch { return; }
  S.status = status;
  const pillEl = $("jobPill"), job = status.job;
  if (job && (status.running || job.id !== S.lastJob?.id || S.lastJob?.status !== job.status)) {
    pillEl.hidden = false;
    pillEl.className = `jobpill ${job.status === "running" ? "" : job.status}`;
    pillEl.textContent = job.status === "running" ? `${job.label}…` : `${job.label}: ${job.status}`;
  }
  const finished = S.lastJob && S.lastJob.status === "running" && job && job.id === S.lastJob.id && job.status !== "running";
  if (finished) {
    notice(job.status === "done" ? `${esc(job.label)} finished.` : `${esc(job.label)} failed: ${esc(job.error)}`, job.status === "done" ? "ok" : "bad");
    if (job.kind === "rebuild") await loadPeople();
  }
  S.lastJob = job ? { id: job.id, status: job.status } : null;
  if (status.running || finished) {
    if (S.tab === "run" || S.tab === "overview" || finished) refresh();
    else if (S.tab === "audit" && !finished) refresh();
  }
}

(async function start() {
  S.role = load("vouch.role") || "controller";
  S.mode = load("vouch.mode") || "scripted";
  await poll();
  if (S.mode === "model" && !S.status?.model_available) S.mode = "scripted";
  await loadPeople();
  const tab = load("vouch.tab");
  go(TITLES[tab] ? tab : "overview");
  setInterval(() => { poll(); }, 2500);
})();
