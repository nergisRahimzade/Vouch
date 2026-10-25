"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let stream = null;
async function api(path, options) {
  const r = await fetch(path, options);
  const body = await r.json();
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  return body;
}
function error(message) { $("error").hidden = !message; $("error").textContent = message || ""; }
function euro(value) { return value == null ? "\u2014" : "\u20ac" + String(value); }
function showPage() {
  const page = ["overview","books","audit"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  for (const name of ["overview","books","audit"]) $("page-"+name).hidden = name !== page;
  document.querySelectorAll(".tabs a").forEach(a => a.setAttribute("aria-current", a.dataset.page === page ? "page" : "false"));
}
function showDecision(d) {
  $("vat").textContent = euro(d.vat_recoverable);
  $("deductible").textContent = euro(d.deductible);
  $("wages").textContent = euro(d.wage_taxable);
  $("decisions").innerHTML = `<article class="card"><div class="tags"><span class="tag blue">${esc(d.classification)}</span><span class="tag orange">${esc(d.decision)}</span></div><p class="summary">${esc(d.rationale)}</p><p>${(d.flags || []).map(esc).join(" \u00b7 ") || "No issue flags"}</p><details><summary>Why?</summary><p>Rules and policies: ${(d.cites || []).map(esc).join(", ")}</p><p>Payroll flat tax: ${esc(euro(d.flat_tax_40))}; guest flat tax: ${esc(euro(d.flat_tax_37b))}</p>${(d.corrections || []).map(c => `<p>${esc(c.type)} \u00b7 ${esc(euro(c.amount))} \u00b7 ${esc(c.ref)}</p>`).join("")}</details></article>`;
}
async function refresh() {
  const range = $("from-year").value && $("to-year").value ? `?from_year=${encodeURIComponent($("from-year").value)}&to_year=${encodeURIComponent($("to-year").value)}` : "";
  const [approvals, entries, payroll, audit, books] = await Promise.all([api("/api/approvals"),api("/api/entries"+range),api("/api/payroll-export"),api("/api/audit"),api("/api/books"+range)]);
  $("from-year").value = books.from_year;
  $("to-year").value = books.to_year;
  $("books-years").innerHTML = books.years.map(y => `<article class="card"><b>${esc(y.year)}</b><span>${y.status === "no_recorded_entries" ? "No recorded entries" : `${esc(y.posted_entries)} posted \u00b7 ${esc(y.draft_entries)} drafts`}</span></article>`).join("");
  $("pending-count").textContent = approvals.length;
  $("approvals").innerHTML = approvals.map(a => `<article class="card"><h3>${esc(a.subject_ref)}</h3><p>Review by ${esc(a.assigned_role)} \u00b7 ${esc(euro(a.amount))}</p><label>Reason <input data-reason="${esc(a.request_id)}" required placeholder="Explain your review"></label><div class="acts"><button class="btn go" data-decision="approve" data-id="${esc(a.request_id)}">Approve</button><button class="btn" data-decision="reject" data-id="${esc(a.request_id)}">Reject</button></div></article>`).join("") || '<p class="sub">No pending approvals.</p>';
  $("entries").innerHTML = entries.map(e => `<details class="entry"><summary>${esc(e.entry_date)} \u00b7 ${esc(e.type)} \u00b7 ${esc(e.status)} \u00b7 ${esc(e.entry_id)}</summary>${e.lines.map(l => `<p>${esc(l.account)} \u00b7 debit ${esc(euro(l.debit))} \u00b7 credit ${esc(euro(l.credit))} \u00b7 ${esc(l.event || l.item)}</p>`).join("")}</details>`).join("") || "No entries.";
  $("payroll").innerHTML = payroll.length ? `<p>${payroll.length} benefit rows; payroll runs separately.</p><details><summary>View rows</summary>${payroll.map(p => `<p>${esc(p.employee)} \u00b7 ${esc(p.event)} \u00b7 ${esc(euro(p.taxable_amount))} \u00b7 ${esc(p.method)} \u00b7 ${esc(p.status)}</p>`).join("")}</details>` : "No payroll rows.";
  $("audit-status").textContent = audit.verified ? "Audit chain verified." : "Audit chain verification failed.";
  $("audit").innerHTML = audit.rows.map(r => `<li><div class="when"><b>${esc(r.at.slice(0,10))}</b><span>${esc(r.at.slice(11,19))}</span></div><div class="body"><span class="tag blue">${esc(r.actor)}</span><h2>${esc(r.action)}</h2><p>${esc(r.reason)}</p><details><summary>Evidence</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(r.basis,null,2))}</pre></details></div></li>`).join("");
}
$("run-form").addEventListener("submit", async event => {
  event.preventDefault(); error(""); $("run-button").disabled = true; $("timeline").innerHTML = "";
  try {
    const mode = $("mode").value;
    const {run_id} = await api("/api/runs", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({scenario_id:$("scenario").value,mode,model:$("model-choice").value})});
    $("mode-label").textContent = mode === "scripted" ? "SCRIPTED demo" : $("model-choice").value;
    if (stream) stream.close();
    stream = new EventSource(`/api/runs/${encodeURIComponent(run_id)}/stream`);
    stream.onmessage = event => { const step = JSON.parse(event.data); const li = document.createElement("li"); li.textContent = step.title; $("timeline").append(li); };
    const poll = async () => {
      try { const run = await api(`/api/runs/${encodeURIComponent(run_id)}`);
        if (run.status === "running") { setTimeout(poll,300); return; }
        stream.close(); $("run-button").disabled = false;
        if (run.error) throw new Error(run.error);
        if (run.decision) showDecision(run.decision);
        await refresh();
      } catch(e) { error(e.message); $("run-button").disabled = false; }
    }; poll();
  } catch(e) { error(e.message); $("run-button").disabled = false; }
});
document.addEventListener("click", async event => {
  const button = event.target.closest("button[data-decision]"); if (!button) return;
  const reason = document.querySelector(`[data-reason="${CSS.escape(button.dataset.id)}"]`).value.trim();
  if (!reason) { error("Please enter a reason for your decision."); return; }
  button.disabled = true;
  try { await api(`/api/approvals/${encodeURIComponent(button.dataset.id)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({decision:button.dataset.decision,reason})}); error(""); await refresh(); }
  catch(e) { error(e.message); button.disabled = false; }
});
window.addEventListener("hashchange",showPage); showPage();
api("/api/scenarios").then(items => { $("scenario").innerHTML = items.map(s => `<option value="${esc(s.id)}">${esc(s.id)} \u00b7 ${esc(s.title)}</option>`).join(""); }).catch(e => error(e.message));
refresh().catch(e => error(e.message));

$("books-range").addEventListener("submit", event => { event.preventDefault(); error(""); refresh().catch(e => error(e.message)); });
