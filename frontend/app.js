/* Vouch front end prototype. Standalone: no backend calls. The decisions, numbers and wording come from the
   "Vouch Merged Dashboard" design and are simulated. */
"use strict";

const INK = "#0E1428", WHITE = "#FFFFFF";
const TAG = { blue: ["#2B5CFF", WHITE], lime: ["#BDEB3E", INK], orange: ["#FFA514", INK], red: ["#E5232E", WHITE] };

const DECISIONS = [
  {
    id: "harbor", badge: "Dispute", badgeTag: "orange", meta: "Dev Summit · June", vendor: "Harbor Catering", amount: "$27,000",
    summary: "Billed for 340 guests, badges show 280, the contract minimum is 300. Vouch will pay for 300 and dispute the other 40.",
    primary: "Approve", secondary: "Reject",
    signoff: "Marcus Lee (event owner) approved. It needs you because it is disputed.", signDot: "#0E9F5B",
    decision: "Pay for 300 guests, the contract minimum. Not 340 (the invoice) and not 280 (the badge scans). The 40 extra guests ($3,600) go into dispute.",
    clauseRef: "Clause 4.2 · Guaranteed minimum",
    clauseText: "The client pays for the greater of guests attended or 300 guests, at $90 per guest.",
    rows: [
      { label: "Invoice", value: "340 guests × $90 = $30,600", tag: "Too high", color: "red" },
      { label: "Badge scans", value: "280 guests came", tag: "Under minimum", color: "orange" },
      { label: "Contract minimum", value: "300 guests × $90 = $27,000", tag: "Pay this", color: "blue" },
    ],
    approvedTag: "Approved", approvedText: "Queued for Friday’s run. The 40 guests ($3,600) stay in dispute with Harbor.",
    rejectedTag: "Sent back", rejectedText: "Vouch will re-plan, ask Harbor and Marcus again, and record why you said no.",
  },
  {
    id: "pier", badge: "Frozen", badgeTag: "red", meta: "Dev Summit · June", vendor: "Pier 27 Venue", amount: "$84,500",
    summary: "A bank-change email does not match the vendor record, so this payment is frozen. Please verify by phone using the number on file.",
    primary: "I verified by phone", secondary: "Keep frozen",
    signoff: "Marcus Lee (event owner) approved the invoice. The bank check failed.", signDot: "#E5232E",
    decision: "Freeze only this payment and do not change the bank account. Call the number on file, not the one in the email. The other 11 payments in Friday’s run are not affected.",
    clauseRef: "Payment terms · Venue contract",
    clauseText: "Final payment is due after the event, paid to the account named in the contract.",
    rows: [
      { label: "Email domain", value: "pier27-events.co, but the record says pier27venue.com", tag: "Mismatch", color: "red" },
      { label: "Bank account", value: "Ends 8841, but the contract says 2290", tag: "Mismatch", color: "red" },
      { label: "Amount due", value: "$124,500 − $40,000 deposit = $84,500", tag: "Matches", color: "lime" },
    ],
    approvedTag: "Verified", approvedText: "Your call is recorded. The payment goes back into Friday’s run to the account in the contract.",
    rejectedTag: "Kept frozen", rejectedText: "Vouch will not draft this payment until a phone check is recorded.",
  },
  {
    id: "bright", badge: "Ready", badgeTag: "lime", meta: "Customer Day · July", vendor: "Brightline AV", amount: "$18,240",
    summary: "Matches the contract, PO and crew logs. Includes 3 hours of overtime, and the $9,000 deposit is already taken off.",
    primary: "Approve", secondary: "Reject",
    signoff: "Dana Ortiz (event owner) approved. It needs you because it is over $15K.", signDot: "#0E9F5B",
    decision: "Pay $18,240. The contract, PO, crew logs and bank details all agree.",
    clauseRef: "Clause 7.1 · Overtime",
    clauseText: "Hours beyond a 10-hour show day cost $1,080 each.",
    rows: [
      { label: "Invoice", value: "$24,000 + 3 overtime hours ($3,240) = $27,240", tag: "Matches PO", color: "lime" },
      { label: "Crew logs", value: "13 hours worked, 3 over the 10-hour day", tag: "Matches", color: "lime" },
      { label: "To pay", value: "$27,240 − $9,000 deposit = $18,240", tag: "Pay this", color: "blue" },
    ],
    approvedTag: "Approved", approvedText: "Queued for Friday’s run.",
    rejectedTag: "Sent back", rejectedText: "Vouch will ask Dana what is wrong and record your reason.",
  },
];

const state = { open: null, status: { harbor: "pending", pier: "pending", bright: "pending" } };
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tagStyle = (name) => `background:${TAG[name][0]};color:${TAG[name][1]}`;
const CHECK = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#0E1428" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

function pendingCard(d) {
  const open = state.open === d.id;
  return `
    <div class="row1">
      <div class="tags"><span class="tag" style="${tagStyle(d.badgeTag)}">${esc(d.badge)}</span><span class="meta">${esc(d.meta)}</span></div>
      <div class="amt">${esc(d.amount)}</div>
    </div>
    <div class="vendor">${esc(d.vendor)}</div>
    <p class="summary">${esc(d.summary)}</p>
    <div class="sign"><span class="dot" style="background:${d.signDot}"></span>${esc(d.signoff)}</div>
    <div class="acts">
      <button type="button" class="btn go" data-act="approve" data-id="${d.id}">${esc(d.primary)}</button>
      <button type="button" class="btn" data-act="reject" data-id="${d.id}">${esc(d.secondary)}</button>
      <button type="button" class="why-btn" data-act="why" data-id="${d.id}" aria-expanded="${open}">${open ? "Hide" : "Why?"}</button>
    </div>
    ${open ? `
    <div class="why">
      <div class="call"><div class="label">VOUCH’S CALL</div><p>${esc(d.decision)}</p></div>
      <div class="two">
        <div><div class="label">THE RULE</div><div class="rule">${esc(d.clauseRef)}</div><p class="rule-text">${esc(d.clauseText)}</p></div>
        <div><div class="label">THE NUMBERS</div><div class="nums">${d.rows.map((r) => `
          <div class="num-row"><div><div class="k">${esc(r.label)}</div><div class="vv">${esc(r.value)}</div></div>
          <span class="small-tag" style="${tagStyle(r.color)}">${esc(r.tag)}</span></div>`).join("")}</div></div>
      </div>
      <div class="foot-line"><span>Vouch only drafts. It can’t approve its own work or change bank details.</span><a href="#audit">See the audit trail</a></div>
    </div>` : ""}`;
}

function resolvedCard(d) {
  const approved = state.status[d.id] === "approved";
  return `
    <div class="done">
      <span class="ok" style="background:${approved ? "#BDEB3E" : "#FFA514"}">${CHECK}</span>
      <div>
        <h3>${esc((approved ? d.approvedTag : d.rejectedTag) + ": " + d.vendor + ", " + d.amount)}</h3>
        <p>${esc(approved ? d.approvedText : d.rejectedText)}</p>
        <p class="m">Saved to the audit trail with your name and the evidence.</p>
      </div>
      <button type="button" class="btn small" data-act="undo" data-id="${d.id}">Undo (demo)</button>
    </div>`;
}

function renderOverview() {
  $("decisions").innerHTML = DECISIONS.map((d) =>
    `<article class="card">${state.status[d.id] === "pending" ? pendingCard(d) : resolvedCard(d)}</article>`).join("");
  const st = state.status;
  const pending = DECISIONS.filter((d) => st[d.id] === "pending").length;
  $("pendingLabel").textContent = pending === 0 ? "no decisions" : pending === 1 ? "1 decision" : `${pending} decisions`;
  $("pendingCount").textContent = pending;
  $("oldestLabel").textContent = pending === 0 ? "All clear" : st.bright === "pending" ? "Oldest: 2 days" : "Oldest: 1 day";
  const frozen = st.pier === "approved" ? 0 : 1;
  $("frozenCount").textContent = frozen;
  $("frozenSub").textContent = frozen ? "Bank-change check" : "Phone check recorded";
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.dataset.id;
  if (b.dataset.act === "why") state.open = state.open === id ? null : id;
  else if (b.dataset.act === "approve") { state.status[id] = "approved"; state.open = null; }
  else if (b.dataset.act === "reject") { state.status[id] = "rejected"; state.open = null; }
  else if (b.dataset.act === "undo") state.status[id] = "pending";
  renderOverview();
});

function showPage() {
  const page = (location.hash || "#overview").slice(1);
  const known = ["overview", "books", "audit"].includes(page) ? page : "overview";
  ["overview", "books", "audit"].forEach((p) => { $("page-" + p).hidden = p !== known; });
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.page === known) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", showPage);
renderOverview();
showPage();
