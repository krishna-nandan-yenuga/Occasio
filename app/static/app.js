// Occasio front end: vanilla JS, no build step, no third-party code.
// All user and server text is inserted with textContent (never innerHTML), and nothing is
// inline, so the page runs under the strict Content-Security-Policy set by the server.
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const REQUIRED = ["event_type", "event_date", "guest_count", "total_budget", "location_tier", "formality"];
const EXTRAS = { wedding: ["ceremonies"], birthday: ["honoree_age", "kids_party"], community: ["ticketed", "permits_needed"] };
const LISTS = ["preferences", "dietary", "accessibility_needs"];
const FIELD_LABELS = {
  event_type: "Type of event", event_date: "Date", guest_count: "Number of guests",
  total_budget: "Total budget", currency: "Currency", location_tier: "Location", formality: "Style",
  ticketed: "Tickets",
};
const CATEGORY_LABELS = {
  av_equipment: "Sound & stage", gifts_favors: "Gifts & favours", permits_insurance: "Permits & insurance",
  bar: "Bar & drinks",
};
const STATUS = {
  todo: ["○", "To do"], done: ["✓", "Done"], late: ["!", "Late"],
  contacted: ["…", "Contacted"], booked: ["✓", "Booked"], overdue: ["!", "Book now"],
  critical: ["⛔", "Critical"], warn: ["⚠", "Warning"], info: ["ℹ", "Note"],
};
const PLAN_HASH = /^#plan=([0-9a-f]{32})$/;

const state = { plan: null, queue: Promise.resolve() };

// ---------- small DOM helpers ----------

/** Create an element. Attributes starting with "on" are not supported on purpose. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key in node && typeof value !== "string") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat().filter((c) => c != null && c !== false));
  return node;
}

const label = (category) =>
  CATEGORY_LABELS[category] || category.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function chip(kind) {
  const [icon, text] = STATUS[kind];
  return el("span", { class: `chip chip-${kind}` }, el("span", { "aria-hidden": "true" }, icon), text);
}

function money(amount, currency) {
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency, maximumFractionDigits: 0 }).format(amount);
  } catch {
    return `${Number(amount).toLocaleString("en-IN")} ${currency}`;
  }
}

const parseDate = (iso) => new Date(`${iso}T00:00:00Z`);
const formatDate = (iso, opts = { day: "numeric", month: "short", year: "numeric" }) =>
  new Intl.DateTimeFormat("en-IN", { ...opts, timeZone: "UTC" }).format(parseDate(iso));
const timeEl = (iso) => el("time", { datetime: iso }, formatDate(iso));

function todayIso() {
  const d = new Date();
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, "0")).join("-");
}

/** Announce politely; clearing first makes repeated messages announce again. */
function announce(message) {
  const status = $("#status");
  status.textContent = "";
  window.setTimeout(() => { status.textContent = message; }, 50);
  window.clearTimeout(announce.timer);
  announce.timer = window.setTimeout(() => { status.textContent = ""; }, 6000);
}

/** The alert stays in the accessibility tree (collapsed when empty) so changes are announced. */
function showError(message) {
  $("#alert").textContent = message;
}

// ---------- API ----------

class ApiError extends Error {
  constructor(status, message, detail, retryAfter) {
    super(message);
    Object.assign(this, { status, detail, retryAfter });
  }
}

async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Could not reach Occasio. Check your connection and try again.");
  }
  const data = await response.json().catch(() => null);
  if (response.ok) return data;
  const detail = data && data.detail;
  if (response.status === 429) {
    const wait = response.headers.get("Retry-After") || "a few";
    throw new ApiError(429, `You're going a little fast. Please try again in ${wait} seconds.`);
  }
  const message = typeof detail === "string" ? detail : "Something went wrong. Please try again.";
  throw new ApiError(response.status, message, Array.isArray(detail) ? detail : null);
}

/** Run `task` with `button` disabled and a busy message; reports errors in the alert. */
async function withBusy(button, busyText, task) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  showError("");
  try {
    return await task();
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    showError(error.message);
    return undefined;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

// ---------- screen 1: intake ----------

const field = (name) => $(`#f-${name}`);
const fieldBox = (name) => $(`.field[data-field="${name}"]`);

function setNote(name, kind, text) {
  const box = fieldBox(name);
  if (!box) return;
  const input = field(name);
  const noteId = `note-${name}`;
  $(`#${noteId}`)?.remove();
  box.classList.remove("is-missing", "is-invalid");
  input.removeAttribute("aria-invalid");
  const describedBy = (input.getAttribute("aria-describedby") || "").split(" ").filter((id) => id && id !== noteId);
  if (kind) {
    box.classList.add(`is-${kind}`);
    box.append(el("p", { id: noteId, class: "field-note" }, text));
    describedBy.push(noteId);
    if (kind === "invalid") input.setAttribute("aria-invalid", "true");
  }
  if (describedBy.length) input.setAttribute("aria-describedby", describedBy.join(" "));
  else input.removeAttribute("aria-describedby");
}

function fillSummary(summaryId, listId, names, messages = {}) {
  const summary = $(summaryId);
  const items = names.map((name) => {
    const link = el("a", { href: `#f-${name}` }, messages[name] || FIELD_LABELS[name] || label(name));
    link.addEventListener("click", (event) => { event.preventDefault(); field(name)?.focus(); });
    return el("li", {}, link);
  });
  $(listId).replaceChildren(...items);
  summary.hidden = names.length === 0;
  return summary;
}

function showExtras() {
  const type = field("event_type").value;
  for (const set of $$(".extras")) set.hidden = set.dataset.for !== type;
}

function fillForm(fields) {
  const extras = fields.extras || {};
  for (const [name, value] of Object.entries({ ...fields, ...extras })) {
    const input = field(name);
    if (!input || value == null || typeof value === "object" && !Array.isArray(value)) continue;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = Array.isArray(value) ? value.join(", ") : String(value);
  }
  showExtras();
}

function markMissing(missing) {
  for (const name of Object.keys(FIELD_LABELS)) setNote(name, null);
  for (const name of missing) setNote(name, "missing", "Not in your description – please fill this in.");
  return fillSummary("#missing-summary", "#missing-list", missing);
}

function collectContext() {
  const value = (name) => field(name).value.trim();
  const context = {
    event_type: value("event_type"),
    event_date: value("event_date"),
    guest_count: Number(value("guest_count")),
    total_budget: value("total_budget"),
    currency: value("currency").toUpperCase() || "INR",
    location_tier: value("location_tier"),
    formality: value("formality"),
    extras: {},
  };
  for (const name of EXTRAS[context.event_type] || []) {
    const input = field(name);
    if (input.type === "checkbox") context.extras[name] = input.checked;
    else if (input.tagName === "SELECT" && input.value !== "") context.extras[name] = input.value === "true";
    else if (input.value !== "") context.extras[name] = Number(input.value);
  }
  for (const name of LISTS) {
    context[name] = value(name).split(",").map((item) => item.trim()).filter(Boolean);
  }
  return context;
}

function validateContext(context) {
  const errors = {};
  for (const name of REQUIRED) {
    if (!field(name).value.trim()) errors[name] = `${FIELD_LABELS[name]} is required`;
  }
  if (context.event_date && context.event_date < todayIso()) errors.event_date = "Date must be today or later";
  if (field("guest_count").value && !(context.guest_count >= 1)) errors.guest_count = "Number of guests must be at least 1";
  if (field("total_budget").value && !(Number(context.total_budget) >= 0)) errors.total_budget = "Budget cannot be negative";
  if (!/^[A-Z]{3}$/.test(context.currency)) errors.currency = "Currency must be a three-letter code";
  return errors;
}

/** Field errors from FastAPI's 422 detail list, keyed by field name. */
function serverFieldErrors(detail) {
  const errors = {};
  for (const item of detail || []) {
    const name = (item.loc || []).filter((part) => typeof part === "string" && part !== "body").pop();
    if (name && fieldBox(name)) errors[name] = `${FIELD_LABELS[name] || label(name)}: ${item.msg}`;
  }
  return errors;
}

function showFieldErrors(errors) {
  for (const name of Object.keys(FIELD_LABELS)) {
    if (!fieldBox(name).classList.contains("is-missing") || errors[name]) setNote(name, null);
  }
  for (const [name, message] of Object.entries(errors)) setNote(name, "invalid", message);
  const summary = fillSummary("#error-summary", "#error-list", Object.keys(errors), errors);
  if (!summary.hidden) summary.focus();
}

async function onDescribe(event) {
  event.preventDefault();
  const text = $("#describe").value.trim();
  if (!text) {
    showError("Describe your event first, or fill in the form below.");
    $("#describe").focus();
    return;
  }
  const button = event.submitter || $("button", event.target);
  const result = await withBusy(button, "Reading your description…", () =>
    api("/api/plans/intake", { method: "POST", body: { text } }));
  if (!result) return;
  fillForm(result.fields);
  const summary = markMissing(result.missing_fields);
  if (result.degraded) showError("We couldn't read the description automatically. Please fill in the form.");
  announce(result.missing_fields.length
    ? `Form filled in. ${result.missing_fields.length} detail(s) still needed.`
    : "Form filled in. Check the details, then create your plan.");
  (summary.hidden ? $("#details-heading") : summary).focus();
}

async function onCreatePlan(event) {
  event.preventDefault();
  const context = collectContext();
  const errors = validateContext(context);
  showFieldErrors(errors);
  if (Object.keys(errors).length) return;
  const button = event.submitter || $("button[type=submit]", event.target);
  try {
    button.disabled = true;
    button.textContent = "Creating your plan…";
    showError("");
    const plan = await api("/api/plans", { method: "POST", body: context });
    history.pushState(null, "", `#plan=${plan.id}`);
    showPlan(plan);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    const fieldErrors = serverFieldErrors(error.detail);
    if (Object.keys(fieldErrors).length) showFieldErrors(fieldErrors);
    else showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Create my plan";
  }
}

// ---------- screen 2: plan dashboard ----------

function showPlan(plan) {
  $("#intake-view").hidden = true;
  $("#plan-view").hidden = false;
  $("#new-plan").hidden = false;
  renderPlan(plan);
  announce("Your plan is ready.");
  $("#plan-heading").focus();
}

function showIntake() {
  state.plan = null;
  $("#plan-view").hidden = true;
  $("#new-plan").hidden = true;
  $("#intake-view").hidden = false;
  $("#chat-log").replaceChildren();
  $("#intake-heading").focus();
}

/** Re-render everything, keeping keyboard focus on the same control. */
function renderPlan(plan) {
  state.plan = plan;
  const focusKey = document.activeElement?.dataset?.key;
  const ctx = plan.context;
  const days = Math.round((parseDate(ctx.event_date) - parseDate(todayIso())) / 86400000);
  $("#plan-heading").textContent = `Your ${ctx.event_type === "community" ? "community event" : ctx.event_type} plan`;
  $("#plan-summary").textContent = [
    formatDate(ctx.event_date), `${ctx.guest_count} guests`, money(ctx.total_budget, ctx.currency),
    days >= 0 ? `${days} days to go` : "event date has passed",
  ].join(" · ");
  renderBudget(plan);
  renderTimeline(plan.timeline);
  renderVendors(plan.vendors);
  renderIdeas(plan.ideas, ctx.preferences);
  renderAdvisories(plan.advisories);
  if (focusKey) $(`[data-key="${CSS.escape(focusKey)}"]`)?.focus();
}

function renderBudget(plan) {
  const { currency, total_budget: total } = plan.context;
  $("#budget-caption").textContent = `Budget split of ${money(total, currency)}`;
  $("#budget-total").textContent = money(total, currency);
  $("#budget-rows").replaceChildren(...plan.budget.map((line) => {
    const name = label(line.category);
    const amount = el("input", {
      type: "number", min: "0", step: "0.01", inputmode: "decimal", value: String(Number(line.amount)),
      "aria-label": `${name} amount in ${currency}`, dataset: { key: `amount-${line.category}` },
    });
    amount.addEventListener("change", () => onAmountChange(line, amount));
    amount.addEventListener("keydown", (event) => {
      if (event.key === "Escape") amount.value = String(Number(line.amount));
    });
    const lockId = `lock-${line.category}`;
    const lock = el("input", { type: "checkbox", id: lockId, checked: line.is_locked, dataset: { key: lockId } });
    lock.addEventListener("change", () =>
      patchPlan({ budget: [{ category: line.category, is_locked: lock.checked }] },
        `${name} ${lock.checked ? "locked" : "unlocked"}; other lines rebalanced.`));
    return el("tr", { class: line.is_locked ? "is-locked" : "" },
      el("th", { scope: "row" }, name, line.notes && el("span", { class: "note" }, line.notes)),
      el("td", {}, amount, el("div", { class: "amount-meta" },
        el("span", { class: "share" }, `${line.percent}%`),
        el("span", { class: "lock" }, lock,
          el("label", { for: lockId }, "Lock", el("span", { class: "visually-hidden" }, ` ${name}`))))));
  }));
}

function onAmountChange(line, input) {
  const value = Number(input.value);
  if (input.value === "" || !(value >= 0)) {
    input.value = String(Number(line.amount));
    showError(`Enter an amount of zero or more for ${label(line.category)}.`);
    return;
  }
  const amount = value.toFixed(2);
  patchPlan({ budget: [{ category: line.category, is_locked: true, amount }] },
    `${label(line.category)} set to ${money(amount, state.plan.context.currency)} and locked; other lines rebalanced.`);
}

function renderTimeline(milestones) {
  const months = new Map();
  for (const m of milestones) {
    const key = m.due_date.slice(0, 7);
    if (!months.has(key)) months.set(key, []);
    months.get(key).push(m);
  }
  $("#timeline").replaceChildren(...[...months].map(([month, items]) => el("section", { class: "month" },
    el("h3", {}, formatDate(`${month}-01`, { month: "long", year: "numeric" })),
    el("ul", { class: "milestones" }, items.map(renderMilestone)))));
}

function renderMilestone(m) {
  const id = `ms-${m.title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const box = el("input", { type: "checkbox", id, checked: m.status === "done", dataset: { key: id } });
  box.addEventListener("change", () =>
    patchPlan({ milestones: [{ title: m.title, status: box.checked ? "done" : "todo" }] },
      `${m.title} marked ${box.checked ? "done" : "not done"}.`));
  return el("li", { class: `milestone${m.status === "done" ? " is-done" : ""}` },
    el("div", { class: "check" }, box, el("label", { for: id }, m.title)),
    el("span", { class: "meta" }, "Due ", timeEl(m.due_date)),
    chip(m.status));
}

function renderVendors(vendors) {
  const today = todayIso();
  $("#vendors").replaceChildren(...vendors.map((v) => {
    const name = label(v.category);
    const selectId = `vendor-${v.category}`;
    const select = el("select", { id: selectId, dataset: { key: selectId } },
      ["todo", "contacted", "booked"].map((s) => el("option", { value: s, selected: v.status === s }, STATUS[s][1])));
    select.addEventListener("change", () =>
      patchPlan({ vendors: [{ category: v.category, status: select.value }] },
        `${name} vendor marked ${STATUS[select.value][1].toLowerCase()}.`));
    const overdue = v.status !== "booked" && v.booked_by < today;
    return el("li", {},
      chip(overdue ? "overdue" : v.status),
      el("h3", {}, name),
      el("p", {}, "Book by ", timeEl(v.booked_by)),
      el("p", { class: "hint" }, v.suggested_vendor_types.join(", ")),
      el("label", { for: selectId, class: "label" }, "Status", el("span", { class: "visually-hidden" }, ` of ${name}`)),
      select,
      el("details", {}, el("summary", {}, `Questions to ask (${v.questions_to_ask.length})`),
        el("ul", {}, v.questions_to_ask.map((q) => el("li", {}, q)))));
  }));
}

function renderIdeas(ideas, preferences) {
  const input = $("#idea-preferences");
  if (document.activeElement !== input) input.value = preferences.join(", ");
  const list = ideas.length
    ? ideas.map((idea) => el("li", {},
      el("span", { class: "chip chip-category" }, label(idea.category)),
      el("h3", {}, idea.title),
      el("p", {}, idea.description)))
    : [el("li", {}, el("p", {}, "No ideas yet. Use “Suggest new ideas” to try again."))];
  $("#ideas").replaceChildren(...list);
}

function renderAdvisories(advisories) {
  const serious = advisories.filter((a) => a.severity !== "info").length;
  $("#advisory-count").replaceChildren(
    ...(serious ? [String(serious), el("span", { class: "visually-hidden" }, " need attention")] : []));
  $("#advisories").replaceChildren(...(advisories.length
    ? advisories.map((a) => el("li", { class: `sev-${a.severity}` },
      chip(a.severity), el("p", {}, a.message), a.suggested_action && el("p", { class: "action" }, a.suggested_action)))
    : [el("li", {}, el("p", {}, "No issues found. Your plan looks workable."))]));
}

/** Send edits one at a time, so responses can't arrive out of order. */
function patchPlan(body, message) {
  const id = state.plan.id;
  state.queue = state.queue.then(async () => {
    try {
      showError("");
      renderPlan(await api(`/api/plans/${id}`, { method: "PATCH", body }));
      announce(message);
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      showError(error.message);
      renderPlan(state.plan); // put the controls back to the saved values
    }
  });
}

async function onRegenerate(event) {
  event.preventDefault();
  const preferences = $("#idea-preferences").value.split(",").map((p) => p.trim()).filter(Boolean);
  const button = event.submitter || $("button", event.currentTarget);
  const plan = await withBusy(button, "Thinking up ideas…", () =>
    api(`/api/plans/${state.plan.id}/regenerate-ideas`, { method: "POST", body: { preferences } }));
  if (!plan) return;
  renderPlan(plan);
  announce(`${plan.ideas.length} new ideas ready.`);
}

// Tabs: WAI-ARIA tabs pattern with automatic activation and roving tabindex.
function selectTab(tab, focus = true) {
  for (const other of $$('[role="tab"]')) {
    const selected = other === tab;
    other.setAttribute("aria-selected", String(selected));
    other.tabIndex = selected ? 0 : -1;
    $(`#${other.getAttribute("aria-controls")}`).hidden = !selected;
  }
  if (focus) tab.focus();
}

function onTabKey(event) {
  const tabs = $$('[role="tab"]');
  const index = tabs.indexOf(event.currentTarget);
  const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 }[event.key];
  if (next === undefined) return;
  event.preventDefault();
  selectTab(tabs[(next + tabs.length) % tabs.length]);
}

// ---------- screen 3: assistant ----------

function addMessage(who, text, extra) {
  const message = el("div", { class: `message from-${who === "You" ? "user" : "assistant"}` },
    el("span", { class: "who" }, `${who}:`), text, extra && el("p", { class: "hint" }, extra));
  $("#chat-log").append(message);
  message.scrollIntoView({ block: "nearest" });
}

async function ask(question) {
  addMessage("You", question);
  const suggestions = $$(".suggestions button");
  for (const button of suggestions) button.disabled = true;
  const reply = await withBusy($("#chat-form button"), "Asking…", () =>
    api(`/api/plans/${state.plan.id}/chat`, { method: "POST", body: { question } }))
    .finally(() => { for (const button of suggestions) button.disabled = false; });
  if (!reply) return;
  addMessage("Occasio", reply.answer, reply.degraded ? "The assistant is offline, so this is a summary of your plan." : null);
}

async function onChat(event) {
  event.preventDefault();
  const input = $("#question");
  const question = input.value.trim();
  if (!question) { input.focus(); return; }
  input.value = "";
  await ask(question);
  input.focus();
}

// ---------- start-up ----------

async function loadFromHash() {
  const match = PLAN_HASH.exec(location.hash);
  if (!match) { if (state.plan) showIntake(); return; }
  if (state.plan?.id === match[1]) return;
  try {
    showPlan(await api(`/api/plans/${match[1]}`));
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    showError(error.status === 404 ? "That plan could not be found. Start a new one below." : error.message);
    showIntake();
  }
}

function init() {
  field("event_date").min = todayIso();
  $("#describe-form").addEventListener("submit", onDescribe);
  $("#context-form").addEventListener("submit", onCreatePlan);
  field("event_type").addEventListener("change", showExtras);
  for (const name of Object.keys(FIELD_LABELS)) {
    field(name).addEventListener("change", () => {
      if (field(name).value) setNote(name, null);
    });
  }
  for (const tab of $$('[role="tab"]')) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", onTabKey);
  }
  $("#ideas-form").addEventListener("submit", onRegenerate);
  $("#chat-form").addEventListener("submit", onChat);
  for (const button of $$(".suggestions button")) {
    button.addEventListener("click", () => ask(button.textContent));
  }
  $("#new-plan").addEventListener("click", () => {
    history.pushState(null, "", location.pathname);
    $("#describe-form").reset();
    $("#context-form").reset();
    markMissing([]);
    showFieldErrors({});
    showExtras();
    showIntake();
  });
  window.addEventListener("popstate", loadFromHash);
  loadFromHash();
}

init();
