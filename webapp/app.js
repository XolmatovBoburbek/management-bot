"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const inTelegram = !!(tg && tg.initData);
if (inTelegram) document.documentElement.classList.add("tg");

const STATUS_SHORT = {
  todo: "не начата", progress: "в работе", done: "выполнена", done_late: "выполнена с просрочкой",
  overdue: "просрочена", cancelled: "отменена",
};
const STATUS_OPTIONS = [
  ["todo", "задача не начата"], ["progress", "в процессе работы"], ["done", "выполнена"],
  ["done_late", "выполнена с просрочкой"], ["cancelled", "отменена"],
];
const PRIORITIES = ["Критично", "Высокий", "Средний", "Низкий"];
const PRIO_CLASS = { "Критично": "p-crit", "Высокий": "p-high", "Средний": "p-med", "Низкий": "p-low" };
const BUCKETS = ["overdue", "today", "tomorrow", "week", "later", "nodate", "done", "cancelled"];
const BUCKET_TITLE = {
  overdue: "🔴 Просрочено", today: "🟠 Сегодня", tomorrow: "🟡 Завтра", week: "На этой неделе",
  later: "Позже", nodate: "Без срока", done: "✅ Выполнено", cancelled: "Отменено",
};
const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const WEEKDAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const AVATAR_COLORS = ["#e5484d", "#f08c00", "#30a46c", "#2f7de1", "#8e4ec6", "#d6409f", "#12a594", "#6e56cf", "#0090ff"];
const EVENT_ICONS = {
  status: "🔄", comment: "💬", problem: "🆘", resolved: "✔️", checkin: "👍", eta: "🗓", edit: "✏️",
  created: "➕", archived: "🗑", call: "📞",
};

const state = {
  boot: null,
  pid: null,
  data: null,
  tab: null,
  calls: null,
  plan: null,
  showDone: false,
  filters: { q: "", member: "", block: "", status: "open", prio: "", mine: false },
};

// ---------- утилиты ----------
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else if (key === "value") el.value = value;
      else if (key === "checked" || key === "selected" || key === "disabled") el[key] = !!value;
      else el.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

async function api(path, opts = {}) {
  const headers = { "X-Init-Data": inTelegram ? tg.initData : "" };
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  let data = null;
  try { data = await res.json(); } catch (_) { /* пустой ответ */ }
  if (!res.ok) throw new Error((data && data.error) || `Ошибка ${res.status}`);
  return data;
}

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 2600);
}

function haptic(kind) {
  try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (_) { /* старый клиент */ }
}

function confirmDialog(text) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm && inTelegram) {
      try { tg.showConfirm(text, (ok) => resolve(!!ok)); return; } catch (_) { /* fallthrough */ }
    }
    resolve(window.confirm(text));
  });
}

async function run(action, success) {
  try {
    const result = await action();
    if (success) { toast(success); haptic("success"); }
    return result;
  } catch (err) {
    haptic("error");
    toast("⚠️ " + err.message);
    throw err;
  }
}

function parseISO(s) {
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

function fmtDate(s, withWeekday) {
  if (!s) return "без срока";
  const d = parseISO(s);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}` + (withWeekday ? ` (${WEEKDAYS[d.getDay()]})` : "");
}

function fmtDateTime(s) {
  const d = new Date(s);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function plural(n, one, few, many) {
  const a = Math.abs(n) % 100;
  if (a >= 11 && a <= 14) return many;
  const l = a % 10;
  if (l === 1) return one;
  if (l >= 2 && l <= 4) return few;
  return many;
}

function daysText(days) {
  if (days === null || days === undefined) return "без срока";
  if (days < 0) return `просрочено на ${-days} ${plural(-days, "день", "дня", "дней")}`;
  if (days === 0) return "сегодня";
  if (days === 1) return "завтра";
  return `через ${days} ${plural(days, "день", "дня", "дней")}`;
}

function isOpen(t) { return ["todo", "progress", "overdue"].includes(t.status); }

function dueLabel(t) {
  if (!t.deadline) return "без срока";
  if (!isOpen(t)) return fmtDate(t.deadline);
  return `${fmtDate(t.deadline)} · ${daysText(t.days_left)}`;
}

function member(id) { return state.boot.members.find((m) => m.id === id); }
function me() { return state.boot.me; }
function isAdmin() { return !!state.boot.me.is_admin; }
function project() { return state.data && state.data.project; }
function tasks() { return (state.data && state.data.tasks) || []; }
function initials(name) { return (name || "?").trim().slice(0, 1).toUpperCase(); }
function avatarColor(id) { return AVATAR_COLORS[(id || 0) % AVATAR_COLORS.length]; }
function localISO(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// ---------- загрузка ----------
async function init() {
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.BackButton) tg.BackButton.onClick(closeSheet);
  }
  const params = new URLSearchParams(location.search);
  try {
    state.boot = await api("/api/bootstrap");
  } catch (err) {
    renderError(err.message);
    return;
  }
  const saved = Number(safeStorage("get", "pid"));
  const projects = state.boot.projects;
  state.pid = (projects.find((p) => p.id === saved) || projects[0] || {}).id || null;
  const requested = params.get("tab");
  const allowed = tabs().map((t) => t.id);
  state.tab = allowed.includes(requested) ? requested : (isAdmin() ? "home" : "my");
  await loadProject();
  render();
  const taskId = Number(params.get("task"));
  if (taskId) openTask(taskId);
}

function safeStorage(op, key, value) {
  try {
    if (op === "get") return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (_) { /* приватный режим */ }
  return null;
}

async function loadProject() {
  state.data = state.pid ? await api(`/api/projects/${state.pid}`) : null;
  state.plan = null;
  if (state.tab === "calls") await loadCalls();
}

async function loadCalls() {
  state.calls = state.pid ? await api(`/api/projects/${state.pid}/calls`) : null;
}

async function reloadBoot() {
  state.boot = await api("/api/bootstrap");
  if (!state.boot.projects.find((p) => p.id === state.pid)) {
    state.pid = state.boot.projects.length ? state.boot.projects[0].id : null;
  }
}

async function refresh() {
  await loadProject();
  render();
}

function renderError(message) {
  document.getElementById("app").replaceChildren(
    h("div", { class: "error-screen" },
      h("h2", null, "Не получилось открыть"),
      h("p", null, message),
      !inTelegram ? h("p", { class: "hint", style: "margin-top:12px" }, "Откройте приложение кнопкой «Открыть» в боте.") : null));
}

// ---------- каркас ----------
function tabs() {
  const list = [
    { id: "my", ico: "👤", label: "Мои" },
    { id: "home", ico: "📊", label: "Обзор" },
    { id: "tasks", ico: "📋", label: "Задачи" },
    { id: "calls", ico: "📞", label: "Обзвон" },
  ];
  if (isAdmin()) list.push({ id: "admin", ico: "⚙️", label: "Панель" });
  return list;
}

async function switchTab(id) {
  state.tab = id;
  if (id === "calls" && state.pid) {
    try { await loadCalls(); } catch (err) { toast("⚠️ " + err.message); }
  }
  render();
  window.scrollTo(0, 0);
}

function render() {
  const views = { my: viewMy, home: viewHome, tasks: viewTasks, calls: viewCalls, admin: viewAdmin };
  const page = h("div", { class: "page" });
  if (!state.pid && state.tab !== "admin") page.append(noProject());
  else page.append(views[state.tab]());
  document.getElementById("app").replaceChildren(topbar(), page, tabbar());
}

function noProject() {
  return h("div", { class: "card empty" },
    h("div", { style: "font-size:40px" }, "🗂"),
    h("p", null, "Проектов пока нет."),
    isAdmin()
      ? h("button", { class: "btn", onclick: () => switchTab("admin") }, "Загрузить Excel или создать проект")
      : h("p", { class: "hint" }, "Администратор скоро загрузит таблицу задач."));
}

function topbar() {
  const projects = state.boot.projects;
  const p = project();
  let title;
  if (projects.length > 1) {
    title = h("select", {
      class: "project-select",
      onchange: async (e) => {
        state.pid = Number(e.target.value);
        safeStorage("set", "pid", state.pid);
        await refresh();
      },
    }, projects.map((x) => h("option", { value: x.id, selected: x.id === state.pid }, x.name)));
  } else {
    title = h("div", { class: "project-title" }, p ? p.name : "IAC PM");
  }
  let countdown = null;
  if (p) {
    if (!p.event_date) countdown = h("div", { class: "countdown warn" }, "⚠️ Дата мероприятия не указана");
    else {
      const days = Math.round((parseISO(p.event_date) - parseISO(state.boot.today)) / 86400000);
      const text = days > 0 ? `До мероприятия ${days} ${plural(days, "день", "дня", "дней")} · ${fmtDate(p.event_date, true)}`
        : days === 0 ? "🎉 Мероприятие сегодня" : `Мероприятие прошло ${fmtDate(p.event_date)}`;
      countdown = h("div", { class: "countdown" + (days >= 0 && days <= 3 ? " warn" : "") }, text);
    }
  }
  return h("div", { class: "topbar" }, title, countdown);
}

function tabbar() {
  const mine = tasks().filter((t) => t.mine && ["overdue", "today"].includes(t.bucket));
  return h("nav", { class: "tabbar" }, tabs().map((t) => {
    const icon = h("span", { class: "ico" + (t.id === "my" && mine.length ? " badge" : ""), "data-n": mine.length || null }, t.ico);
    return h("button", { class: state.tab === t.id ? "active" : "", onclick: () => switchTab(t.id) }, icon, t.label);
  }));
}

// ---------- элементы ----------
function statusChip(status) {
  if (status === "todo") return null;
  return h("span", { class: `chip s-${status}` }, STATUS_SHORT[status]);
}

function taskRow(t, opts = {}) {
  const owner = opts.owner ? h("span", null, t.responsible || "без ответственного") : null;
  return h("div", { class: "task" + (["done", "done_late", "cancelled"].includes(t.status) ? " done" : ""), onclick: () => openTask(t.id) },
    h("div", { class: "prio " + (PRIO_CLASS[t.priority] || "") }),
    h("div", { class: "body" },
      h("div", { class: "title" }, t.title),
      h("div", { class: "meta" },
        owner,
        h("span", { class: "due " + t.bucket }, dueLabel(t)),
        t.status === "overdue" ? null : statusChip(t.status),
        t.blocked ? h("span", { class: "chip help" }, "нужна помощь") : null,
        t.eta && isOpen(t) ? h("span", { class: "chip warn" }, "обещано к " + fmtDate(t.eta)) : null)));
}

function groupedList(list, opts = {}) {
  const groups = {};
  for (const t of list) (groups[t.bucket] = groups[t.bucket] || []).push(t);
  const out = [];
  for (const b of BUCKETS) {
    const items = groups[b];
    if (!items || !items.length) continue;
    if ((b === "done" || b === "cancelled") && !state.showDone) continue;
    out.push(h("div", { class: "section" + (b === "overdue" ? " red" : b === "today" ? " orange" : "") },
      BUCKET_TITLE[b], h("span", { class: "count" }, items.length)));
    out.push(h("div", { class: "card flush" }, items.map((t) => taskRow(t, opts))));
  }
  const closed = (groups.done || []).length + (groups.cancelled || []).length;
  if (closed) {
    out.push(h("button", {
      class: "btn secondary block", style: "margin-top:8px",
      onclick: () => { state.showDone = !state.showDone; render(); },
    }, state.showDone ? "Скрыть выполненные" : `Показать выполненные (${closed})`));
  }
  if (!out.length) out.push(h("div", { class: "card empty" }, opts.empty || "Задач нет"));
  return out;
}

function tile(value, label, cls, onclick) {
  return h("div", { class: "tile " + (cls || ""), onclick }, h("b", null, value), h("span", null, label));
}

function progressBar(pct) {
  return h("div", { class: "progress" }, h("i", { style: `width:${Math.max(0, Math.min(100, pct))}%` }));
}

function openTasksWith(filters) {
  state.filters = { q: "", member: "", block: "", status: "open", prio: "", mine: false, ...filters };
  switchTab("tasks");
}

// ---------- «Мои» — личный кабинет ----------
function viewMy() {
  const mine = tasks().filter((t) => t.mine);
  const open = mine.filter(isOpen);
  const done = mine.filter((t) => t.status === "done" || t.status === "done_late").length;
  const counted = mine.filter((t) => t.status !== "cancelled").length;
  const pct = counted ? Math.round((100 * done) / counted) : 0;
  const m = me();
  const head = h("div", { class: "card" },
    h("div", { class: "row" },
      h("div", { class: "avatar", style: `background:${avatarColor(m.id)}` }, initials(m.name)),
      h("div", { class: "grow" },
        h("div", { class: "name", style: "font-weight:700" }, m.name),
        h("div", { class: "hint" }, m.role || (m.username ? "@" + m.username : ""))),
      h("div", { style: "text-align:right" }, h("b", { style: "font-size:20px" }, pct + "%"), h("div", { class: "hint" }, "готово"))),
    h("div", { style: "margin-top:10px" }, progressBar(pct)));
  const tilesEl = h("div", { class: "tiles" },
    tile(open.filter((t) => t.bucket === "overdue").length, "просрочено", "red"),
    tile(open.filter((t) => t.bucket === "today").length, "сегодня", "orange"),
    tile(open.length, "открыто"),
    tile(open.filter((t) => !t.deadline).length, "без срока", "gray"));
  if (!mine.length) {
    return h("div", null, head, h("div", { class: "card empty" }, "За вами пока нет задач 🙌"));
  }
  return h("div", null, head, tilesEl, groupedList(mine, { empty: "Все задачи закрыты 🎉" }));
}

// ---------- «Обзор» ----------
function viewHome() {
  const d = state.data;
  const s = d.stats;
  const p = d.project;
  const hero = h("div", { class: "card" },
    h("div", { class: "hero" },
      h("div", null, h("div", { class: "big" }, s.pct + "%"), h("div", { class: "label" }, "готовность")),
      h("div", { class: "grow", style: "flex:1" },
        h("div", { class: "hint", style: "margin-bottom:6px" }, `Выполнено ${s.done} из ${s.total} · в работе ${s.progress}`),
        progressBar(s.pct))));
  const tilesEl = h("div", { class: "tiles" },
    tile(s.overdue, "просрочено", "red", () => openTasksWith({ status: "overdue" })),
    tile(s.due_today, "срок сегодня", "orange", () => openTasksWith({})),
    tile(s.blocked, "нужна помощь", s.blocked ? "red" : "", () => openTasksWith({ status: "blocked" })),
    tile(s.nodate, "без срока", "gray", () => openTasksWith({ status: "nodate" })));

  const byId = Object.fromEntries(d.tasks.map((t) => [t.id, t]));
  const attention = d.attention.map((id) => byId[id]).filter(Boolean);
  const out = [hero, tilesEl];
  if (!p.event_date && isAdmin()) {
    out.push(h("div", { class: "card" }, h("b", null, "⚠️ Укажите дату мероприятия"),
      h("p", { class: "hint", style: "margin:6px 0 10px" }, "Тогда бот посчитает обратный отсчёт и предложит сроки для задач без дедлайна."),
      h("button", { class: "btn small", onclick: () => switchTab("admin") }, "Открыть панель")));
  }
  out.push(h("div", { class: "section" }, "🔥 Требует внимания", h("span", { class: "count" }, attention.length)));
  out.push(attention.length
    ? h("div", { class: "card flush" }, attention.map((t) => taskRow(t, { owner: true })))
    : h("div", { class: "card empty" }, s.nodate === s.total - s.done
      ? "Горящих сроков нет — но у задач не заполнены дедлайны"
      : "Горящих задач нет 👌"));

  out.push(h("div", { class: "section" }, "👥 Команда"));
  out.push(h("div", { class: "card flush" }, d.workload.filter((w) => w.total || isAdmin()).map((w) =>
    h("div", { class: "list-item", style: "cursor:pointer", onclick: () => openTasksWith({ member: String(w.member_id) }) },
      h("div", { class: "avatar", style: `background:${avatarColor(w.member_id)}` }, initials(w.name)),
      h("div", { class: "grow" },
        h("div", { class: "row wrap" }, h("span", { class: "name" }, w.name),
          w.overloaded ? h("span", { class: "chip warn" }, "перегруз") : null,
          !w.connected ? h("span", { class: "chip" }, "не в боте") : null),
        h("div", { class: "hint" }, w.role),
        h("div", { class: "hint", style: "margin:4px 0" },
          `открыто ${w.open} · ${w.overdue ? "просрочено " + w.overdue + " · " : ""}на неделе ${w.due_week} · готово ${w.done}/${w.total}`),
        progressBar(w.pct))))));

  out.push(h("div", { class: "section" }, "📦 Блоки"));
  out.push(h("div", { class: "card" }, d.blocks.map((b) =>
    h("div", { class: "bar-row", style: "cursor:pointer", onclick: () => openTasksWith({ block: b.block }) },
      h("div", { class: "row" }, h("span", { style: "flex:1" }, b.block),
        b.overdue ? h("span", { class: "chip s-overdue" }, b.overdue) : null,
        h("span", { class: "hint" }, `${b.done}/${b.total}`)),
      progressBar(b.pct)))));

  if (d.milestones.length) {
    const today = parseISO(state.boot.today);
    out.push(h("div", { class: "section" }, "🏁 Вехи"));
    out.push(h("div", { class: "card" }, h("div", { class: "timeline" }, d.milestones.map((m) => {
      let cls = "";
      const done = /выполн|готов|закрыт/i.test(m.status);
      if (done) cls = "ok";
      else if (m.date) {
        const diff = Math.round((parseISO(m.date) - today) / 86400000);
        cls = diff < 0 ? "past" : diff <= 7 ? "soon" : "";
      }
      return h("div", { class: "ms " + cls },
        h("div", { class: "when" }, (m.date ? fmtDate(m.date, true) : (m.date_raw || "дата не указана")) + (m.status ? " · " + m.status : "")),
        h("div", { style: "font-weight:600" }, m.title),
        m.criteria ? h("div", { class: "hint" }, m.criteria) : null);
    }))));
  }

  const risks = d.risks.filter((r) => r.is_open);
  if (risks.length) {
    out.push(h("details", { class: "card" },
      h("summary", null, `⚠️ Риски (${risks.length})`),
      risks.map((r) => h("div", { class: "issue" },
        h("div", { class: "row wrap" },
          h("span", { class: "chip" + (/crit/i.test(r.impact) ? " s-overdue" : "") }, `влияние: ${r.impact || "—"}`),
          h("span", { class: "chip" }, `вероятность: ${r.probability || "—"}`),
          h("span", { class: "hint" }, r.owner)),
        h("div", { style: "font-weight:600;margin-top:6px" }, r.title),
        r.mitigation ? h("div", { class: "hint" }, "Что делать: " + r.mitigation) : null))));
  }
  return h("div", null, out);
}

// ---------- «Задачи» ----------
function filteredTasks() {
  const f = state.filters;
  const q = f.q.trim().toLowerCase();
  return tasks().filter((t) => {
    if (f.mine && !t.mine) return false;
    if (f.member && !t.assignee_ids.includes(Number(f.member))) return false;
    if (f.block && t.block !== f.block) return false;
    if (f.prio && t.priority !== f.prio) return false;
    if (f.status === "open" && !isOpen(t)) return false;
    if (f.status === "overdue" && t.status !== "overdue") return false;
    if (f.status === "blocked" && !t.blocked) return false;
    if (f.status === "nodate" && (t.deadline || !isOpen(t))) return false;
    if (f.status === "progress" && t.status !== "progress") return false;
    if (f.status === "done" && !["done", "done_late"].includes(t.status)) return false;
    if (q && !`${t.title} ${t.description} ${t.responsible} ${t.contractor} ${t.block}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function viewTasks() {
  const f = state.filters;
  const listEl = h("div");
  const counter = h("div", { class: "hint", style: "padding:0 4px 4px" });
  const redraw = () => {
    const list = filteredTasks();
    const prevShow = state.showDone;
    if (f.status === "done") state.showDone = true;
    listEl.replaceChildren(...groupedList(list, { owner: true, empty: "Ничего не найдено" }));
    state.showDone = prevShow;
    counter.textContent = `Найдено: ${list.length}`;
  };
  const set = (key) => (e) => { f[key] = e.target.value; redraw(); };
  const blocks = [...new Set(tasks().map((t) => t.block).filter(Boolean))];
  const people = state.boot.members.filter((m) => m.active);
  const search = h("input", { class: "search", type: "search", placeholder: "Поиск по задачам, подрядчикам…", value: f.q,
    oninput: (e) => { f.q = e.target.value; redraw(); } });
  const filters = h("div", { class: "filters" },
    h("button", { class: "pill" + (f.mine ? " on" : ""), onclick: (e) => { f.mine = !f.mine; e.target.classList.toggle("on"); redraw(); } }, "Мои"),
    h("select", { onchange: set("status") },
      [["open", "Открытые"], ["overdue", "Просроченные"], ["blocked", "Нужна помощь"], ["nodate", "Без срока"],
        ["progress", "В работе"], ["done", "Выполненные"], ["", "Все"]]
        .map(([v, l]) => h("option", { value: v, selected: f.status === v }, l))),
    h("select", { onchange: set("member") }, h("option", { value: "" }, "Все люди"),
      people.map((m) => h("option", { value: m.id, selected: String(m.id) === f.member }, m.name))),
    h("select", { onchange: set("block") }, h("option", { value: "" }, "Все блоки"),
      blocks.map((b) => h("option", { value: b, selected: b === f.block }, b))),
    h("select", { onchange: set("prio") }, h("option", { value: "" }, "Любой приоритет"),
      PRIORITIES.map((p) => h("option", { value: p, selected: p === f.prio }, p))));
  redraw();
  return h("div", null, search, filters, counter,
    isAdmin() ? h("button", { class: "btn secondary block", style: "margin-bottom:8px", onclick: () => openTaskForm(null) }, "➕ Новая задача") : null,
    listEl);
}

// ---------- «Обзвон» ----------
function viewCalls() {
  const c = state.calls;
  if (!c) return h("div", { class: "card empty" }, "Загрузка…");
  const out = [];
  const items = c.items;
  const doneCount = items.filter((i) => i.check).length;
  out.push(h("div", { class: "card" },
    h("div", { class: "row" }, h("b", { style: "flex:1" }, `Обзвон на ${fmtDate(c.today, true)}`),
      h("span", { class: "hint" }, `${doneCount}/${items.length}`)),
    h("div", { class: "hint", style: "margin-top:4px" }, isAdmin()
      ? "Бот сам собирает, кому позвонить: просрочки, «нужна помощь», сроки сегодня/завтра без старта, подрядчики по ближайшим задачам."
      : "Ваши запланированные звонки. Бот напомнит в назначенное время."),
    items.length ? h("div", { style: "margin-top:10px" }, progressBar(items.length ? (100 * doneCount) / items.length : 0)) : null));

  if (items.length) {
    out.push(h("div", { class: "card flush" }, items.map(callItem)));
  } else {
    out.push(h("div", { class: "card empty" }, "На сегодня звонить некому 👌"));
  }

  const planned = c.planned.filter((p) => p.status === "planned");
  out.push(h("div", { class: "section" }, "🗓 Запланированные звонки", h("span", { class: "count" }, planned.length)));
  if (planned.length) {
    out.push(h("div", { class: "card flush" }, planned.map((p) => {
      const who = p.member_id ? member(p.member_id) : null;
      return h("div", { class: "list-item" },
        h("div", { class: "grow" },
          h("div", { class: "name" }, p.contact),
          h("div", { class: "hint" }, fmtDateTime(p.due_at) + (who ? " · звонит " + who.name : "")),
          p.phone ? h("a", { href: "tel:" + p.phone }, p.phone) : null,
          p.note ? h("div", { class: "hint" }, p.note) : null),
        h("div", { class: "row" },
          h("button", { class: "btn small green", onclick: () => run(() => api(`/api/calls/${p.id}/done`, { json: {} }), "Отмечено").then(() => switchTab("calls")) }, "✓"),
          h("button", { class: "btn small secondary", onclick: () => run(() => api(`/api/calls/${p.id}/cancel`, { json: {} }), "Отменено").then(() => switchTab("calls")) }, "✕")));
    })));
  }
  out.push(h("button", { class: "btn block", style: "margin-top:8px", onclick: () => openCallForm(null) }, "📞 Запланировать звонок"));
  return h("div", null, out);
}

function callItem(item) {
  const done = !!item.check;
  const toggle = async () => {
    await run(() => api(`/api/projects/${state.pid}/calls/check`, { json: { key: item.key, checked: !done } }), done ? "Снято" : "Созвонились ✓");
    await loadCalls();
    render();
  };
  const contacts = [];
  if (item.username) contacts.push(h("a", { href: `https://t.me/${item.username}` }, "@" + item.username));
  if (item.phone) contacts.push(h("a", { href: "tel:" + item.phone }, item.phone));
  return h("div", { class: "list-item call-item" + (done ? " done" : "") },
    h("button", { class: "call-check" + (done ? " on" : ""), onclick: toggle, "aria-label": "Отметить" }, done ? "✓" : ""),
    h("div", { class: "grow" },
      h("div", { class: "name" }, item.title),
      h("div", { class: "hint" }, [item.subtitle, item.caller ? "звонит " + item.caller : "", item.due_at ? fmtDateTime(item.due_at) : ""].filter(Boolean).join(" · ")),
      contacts.length ? h("div", { class: "row wrap", style: "font-size:13px;margin-top:2px" }, contacts) : null,
      item.owners && item.owners.length ? h("div", { class: "hint" }, "ответственные: " + item.owners.join(", ")) : null,
      item.tasks.length ? h("ul", { class: "call-tasks" }, item.tasks.slice(0, 5).map((t) =>
        h("li", { style: "cursor:pointer", onclick: () => openTask(t.task_id) }, `${t.title} — ${t.reason}`))) : null,
      done && item.check.member_name ? h("div", { class: "hint" }, "✓ " + item.check.member_name + (item.check.note ? ": " + item.check.note : "")) : null));
}

// ---------- нижний лист ----------
function showSheet(content) {
  const root = document.getElementById("sheet-root");
  root.replaceChildren(
    h("div", { class: "sheet-backdrop", onclick: closeSheet }),
    h("div", { class: "sheet" }, h("div", { class: "grabber" }), h("button", { class: "close", onclick: closeSheet, "aria-label": "Закрыть" }, "✕"), content));
  document.body.style.overflow = "hidden";
  if (tg && tg.BackButton) tg.BackButton.show();
}

function closeSheet() {
  document.getElementById("sheet-root").replaceChildren();
  document.body.style.overflow = "";
  if (tg && tg.BackButton) tg.BackButton.hide();
}

async function openTask(id) {
  let t;
  try { t = await api(`/api/tasks/${id}`); } catch (err) { toast("⚠️ " + err.message); return; }
  showSheet(taskSheet(t));
}

function taskSheet(t) {
  const reopen = async () => { await refresh(); openTask(t.id); };
  const act = (fn, msg) => async () => { await run(fn, msg); await reopen(); };
  const owners = t.assignee_ids.map(member).filter(Boolean);
  const kv = [
    ["Срок", t.deadline ? `${fmtDate(t.deadline, true)}${isOpen(t) ? " · " + daysText(t.days_left) : ""}` : "без срока"],
    ["Начало", t.start_date ? fmtDate(t.start_date, true) : null],
    ["Ответственный", owners.length ? owners.map((m) => m.name + (m.username ? ` (@${m.username})` : "")).join(", ") : (t.responsible || "—")],
    ["Блок", t.block],
    ["Подрядчик", t.contractor],
    ["Обещано к", t.eta && isOpen(t) ? fmtDate(t.eta, true) : null],
    ["Выполнено", t.fact_date ? fmtDate(t.fact_date, true) : null],
    ["Результат", t.proof],
  ].filter(([, v]) => v);

  const inputArea = h("div");
  const openInput = (kind) => {
    const ta = h("textarea", { placeholder: kind === "problem" ? "Что мешает? Какая помощь нужна?" : "Комментарий…" });
    const send = h("button", { class: "btn block", onclick: act(async () => {
      if (!ta.value.trim()) throw new Error("Напишите текст");
      await api(`/api/tasks/${t.id}/${kind}`, { json: { text: ta.value } });
    }, kind === "problem" ? "Передано руководителю 🆘" : "Сохранено 💬") }, kind === "problem" ? "Отправить руководителю" : "Сохранить");
    inputArea.replaceChildren(h("div", { class: "card" }, h("label", { class: "field" }, ta), send));
    ta.focus();
  };

  const actions = [];
  if (t.can_edit) {
    const row = [];
    if (isOpen(t)) {
      if (t.raw_status !== "progress") row.push(h("button", { class: "btn secondary", onclick: act(() => api(`/api/tasks/${t.id}/status`, { json: { status: "progress" } }), "Взято в работу") }, "▶️ В работу"));
      row.push(h("button", { class: "btn green", onclick: act(() => api(`/api/tasks/${t.id}/status`, { json: { status: "done" } }), "Выполнено ✅") }, "✅ Выполнено"));
    } else {
      row.push(h("button", { class: "btn secondary", onclick: act(() => api(`/api/tasks/${t.id}/status`, { json: { status: "progress" } }), "Возвращено в работу") }, "↩️ Вернуть в работу"));
    }
    actions.push(h("div", { class: "btns" }, row));
    actions.push(h("div", { class: "btns" },
      t.blocked
        ? h("button", { class: "btn secondary", onclick: act(() => api(`/api/tasks/${t.id}/resolve`, { json: {} }), "Блокер снят") }, "✔️ Проблема решена")
        : h("button", { class: "btn danger", onclick: () => openInput("problem") }, "🆘 Нужна помощь"),
      h("button", { class: "btn secondary", onclick: () => openInput("comment") }, "💬 Комментарий")));
  }
  if (isAdmin()) {
    actions.push(h("div", { class: "btns three" },
      h("button", { class: "btn secondary small", onclick: () => openTaskForm(t) }, "✏️ Изменить"),
      h("button", { class: "btn secondary small", onclick: () => openCallForm({ task_id: t.id, contact: t.contractor || "" }) }, "📞 Звонок"),
      h("button", { class: "btn danger small", onclick: async () => {
        if (!(await confirmDialog("Удалить задачу? Из Excel/таблицы она не удалится."))) return;
        await run(() => api(`/api/tasks/${t.id}`, { method: "DELETE" }), "Удалено");
        closeSheet();
        await refresh();
      } }, "🗑 Удалить")));
  } else if (t.can_edit) {
    actions.push(h("button", { class: "btn secondary block small", style: "margin-top:8px", onclick: () => openCallForm({ task_id: t.id, contact: t.contractor || "" }) }, "📞 Напомнить позвонить"));
  }

  return h("div", null,
    h("h2", null, t.title),
    h("div", { class: "row wrap", style: "margin:0 4px 12px" },
      t.priority ? h("span", { class: "chip" }, t.priority) : null,
      h("span", { class: `chip s-${t.status}` }, STATUS_SHORT[t.status]),
      t.source === "manual" ? h("span", { class: "chip" }, "создана в боте") : null),
    t.blocked ? h("div", { class: "blocker" }, h("b", null, "🆘 Нужна помощь"), h("div", null, t.blocked_reason)) : null,
    h("div", { class: "card" },
      h("dl", { class: "kv", style: "margin:0" }, kv.map(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
      t.description ? h("p", { style: "margin:12px 0 0;white-space:pre-wrap" }, t.description) : null,
      t.sheet_comment ? h("p", { class: "hint", style: "margin:10px 0 0;white-space:pre-wrap" }, "Комментарий из таблицы: " + t.sheet_comment) : null),
    actions,
    inputArea,
    t.events.length ? h("div", null,
      h("div", { class: "section" }, "История"),
      h("div", { class: "card" }, t.events.map((e) => h("div", { class: "event" },
        h("div", null, `${EVENT_ICONS[e.kind] || "•"} ${e.member_name ? e.member_name + ": " : ""}${e.text}`),
        h("div", { class: "when" }, fmtDateTime(e.created_at)))))) : null);
}

function field(label, input) {
  return h("label", { class: "field" }, h("span", null, label), input);
}

function openTaskForm(t) {
  const isNew = !t;
  const blocks = [...new Set(tasks().map((x) => x.block).filter(Boolean))];
  const people = state.boot.members.filter((m) => m.active);
  const f = {
    title: h("input", { value: t ? t.title : "", placeholder: "Что сделать" }),
    block: h("input", { value: t ? t.block : "", list: "blocks-list", placeholder: "Например, ДИЗАЙН / БРЕНДИНГ" }),
    description: h("textarea", { placeholder: "Подробности, критерий готовности" }),
    priority: h("select", null, h("option", { value: "" }, "—"), PRIORITIES.map((p) => h("option", { value: p, selected: t && t.priority === p }, p))),
    responsible: h("input", { value: t ? t.responsible : "", list: "people-list", placeholder: "Имя из команды" }),
    start_date: h("input", { type: "date", value: t && t.start_date ? t.start_date : "" }),
    deadline: h("input", { type: "date", value: t && t.deadline ? t.deadline : "" }),
    contractor: h("input", { value: t ? t.contractor : "", placeholder: "Подрядчик / поставщик" }),
    status: h("select", null, STATUS_OPTIONS.map(([v, l]) => h("option", { value: v, selected: t ? t.raw_status === v : v === "todo" }, l))),
  };
  f.description.value = t ? t.description : "";
  const save = h("button", { class: "btn block", onclick: async () => {
    const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
    const result = await run(() => isNew
      ? api(`/api/projects/${state.pid}/tasks`, { json: body })
      : api(`/api/tasks/${t.id}`, { method: "PATCH", json: body }), isNew ? "Задача создана" : "Сохранено");
    await refresh();
    openTask(isNew ? result.data.id : t.id);
  } }, isNew ? "➕ Создать задачу" : "💾 Сохранить");
  showSheet(h("div", null,
    h("h2", null, isNew ? "Новая задача" : "Редактирование"),
    h("datalist", { id: "blocks-list" }, blocks.map((b) => h("option", { value: b }))),
    h("datalist", { id: "people-list" }, people.map((m) => h("option", { value: m.name }, m.role))),
    h("div", { class: "card" },
      field("Название *", f.title),
      field("Ответственный", f.responsible),
      h("div", { class: "grid2" }, field("Начало", f.start_date), field("Срок (дедлайн)", f.deadline)),
      h("div", { class: "grid2" }, field("Приоритет", f.priority), field("Статус", f.status)),
      field("Блок", f.block),
      field("Описание", f.description),
      field("Подрядчик", f.contractor),
      isNew ? h("p", { class: "hint", style: "margin:0 0 10px" }, "Ответственный получит уведомление в личку.") : null,
      save)));
  f.title.focus();
}

function openCallForm(preset) {
  const now = new Date();
  now.setMinutes(0, 0, 0);
  now.setHours(now.getHours() + 1);
  const pad = (n) => String(n).padStart(2, "0");
  const dt = `${localISO(now)}T${pad(now.getHours())}:00`;
  const people = state.boot.members.filter((m) => m.active);
  const f = {
    contact: h("input", { value: (preset && preset.contact) || "", placeholder: "Кому звонить (имя / компания)" }),
    phone: h("input", { type: "tel", placeholder: "+998 …" }),
    due_at: h("input", { type: "datetime-local", value: dt }),
    member_id: h("select", { disabled: !isAdmin() }, people.map((m) => h("option", { value: m.id, selected: m.id === me().id }, m.name))),
    task_id: h("select", null, h("option", { value: "" }, "— без задачи —"),
      tasks().filter(isOpen).map((t) => h("option", { value: t.id, selected: preset && preset.task_id === t.id }, t.title))),
    note: h("textarea", { placeholder: "О чём договориться" }),
  };
  const save = h("button", { class: "btn block", onclick: async () => {
    const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
    await run(() => api(`/api/projects/${state.pid}/calls`, { json: body }), "Напомню в назначенное время 📞");
    closeSheet();
    if (state.tab === "calls") await switchTab("calls");
  } }, "Запланировать");
  showSheet(h("div", null,
    h("h2", null, "📞 Напоминание о звонке"),
    h("div", { class: "card" },
      field("Кому *", f.contact),
      h("div", { class: "grid2" }, field("Телефон", f.phone), field("Когда *", f.due_at)),
      field("Кто звонит", f.member_id),
      field("По задаче", f.task_id),
      field("Заметка", f.note),
      save)));
}

// ---------- «Панель» (админ) ----------
function viewAdmin() {
  const p = project();
  const out = [];
  if (p) {
    out.push(projectCard(p));
    out.push(h("details", { class: "card", open: true },
      h("summary", null, "➕ Добавить задачу"),
      h("p", { class: "hint", style: "margin:0 0 10px" }, "Или в чате: /add @ник 30.09 Название"),
      h("button", { class: "btn block", onclick: () => openTaskForm(null) }, "Открыть форму")));
  }
  out.push(uploadCard());
  out.push(sheetCard(p));
  if (p) {
    out.push(planCard(p));
    out.push(auditCard());
  }
  out.push(teamCard());
  out.push(settingsCard());
  out.push(newProjectCard());
  return h("div", null, out);
}

function projectCard(p) {
  const name = h("input", { value: p.name });
  const date = h("input", { type: "date", value: p.event_date || "" });
  const source = p.source_type === "gsheet"
    ? [h("div", { class: "hint" }, "Источник: Google Таблица"),
      h("a", { href: p.source_url, target: "_blank", style: "font-size:13px;overflow-wrap:anywhere" }, p.source_url),
      h("div", { class: "hint" }, "Последняя синхронизация: " + (p.last_sync_at ? fmtDateTime(p.last_sync_at) : "—")),
      p.last_sync_error ? h("div", { class: "hint", style: "color:var(--red)" }, "Ошибка: " + p.last_sync_error) : null]
    : [h("div", { class: "hint" }, p.source_type === "upload" ? "Источник: загруженный Excel" : "Проект создан в боте")];
  return h("div", { class: "card" },
    h("h3", null, "📁 Проект"),
    field("Название", name),
    field("Дата мероприятия", date),
    source,
    h("div", { class: "btns" },
      h("button", { class: "btn", onclick: async () => {
        await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { name: name.value, event_date: date.value || null } }), "Сохранено");
        await reloadBoot();
        await refresh();
      } }, "Сохранить"),
      h("button", { class: "btn secondary", onclick: () => run(() => api(`/api/projects/${p.id}/export`, { json: {} }), "Файл отправлен в чат с ботом 📥") }, "📥 Excel в чат")),
    p.source_type === "gsheet" ? h("button", { class: "btn secondary block", style: "margin-top:8px", onclick: async () => {
      const r = await run(() => api(`/api/projects/${p.id}/sync`, { json: {} }));
      toast(`Синхронизировано: ${r.data.tasks} задач, новых ${r.data.added}`);
      await refresh();
    } }, "🔄 Синхронизировать сейчас") : null,
    h("button", { class: "btn danger block small", style: "margin-top:8px", onclick: async () => {
      if (!(await confirmDialog("Архивировать проект? Напоминания по нему прекратятся."))) return;
      await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { archived: true } }), "Проект в архиве");
      await reloadBoot();
      await refresh();
    } }, "Архивировать проект"));
}

function uploadCard() {
  const input = h("input", { type: "file", accept: ".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const result = h("div");
  const btn = h("button", { class: "btn block", onclick: async () => {
    if (!input.files.length) { toast("Выберите файл .xlsx"); return; }
    const form = new FormData();
    form.append("file", input.files[0]);
    btn.disabled = true;
    try {
      const r = await run(() => api("/api/upload", { body: form }), "Таблица загружена 📥");
      state.pid = r.data.project_id;
      safeStorage("set", "pid", state.pid);
      await reloadBoot();
      await loadProject();
      render();
      toast(`Задач: ${r.data.tasks} (новых ${r.data.added}, обновлено ${r.data.updated})`);
    } finally {
      btn.disabled = false;
    }
  } }, "Загрузить");
  return h("details", { class: "card" },
    h("summary", null, "📎 Загрузить Excel"),
    h("p", { class: "hint", style: "margin:0 0 10px" },
      "Колонки ищутся по заголовкам: «Процедура», «Ответственный», «Статус процедуры», «Окончание работы»… " +
      "Повторная загрузка того же проекта обновит задачи, не потеряв статусы из бота. Можно просто прислать файл боту в личку."),
    field("Файл .xlsx", input), btn, result);
}

function sheetCard(p) {
  const url = h("input", { type: "url", placeholder: "https://docs.google.com/spreadsheets/d/…", value: p && p.source_type === "gsheet" ? p.source_url : "" });
  const email = state.boot.sheets_service_email;
  return h("details", { class: "card" },
    h("summary", null, "🔗 Google Таблица"),
    h("p", { class: "hint", style: "margin:0 0 10px" },
      "Откройте доступ «Все, у кого есть ссылка — Читатель». Бот будет забирать изменения каждые " +
      (state.boot.settings.sync_minutes || 10) + " мин."),
    email
      ? h("p", { class: "hint", style: "margin:0 0 10px" }, "✍️ Чтобы статусы из бота записывались в таблицу, выдайте доступ «Редактор» адресу: ", h("b", null, email))
      : h("p", { class: "hint", style: "margin:0 0 10px" }, "Запись статусов обратно в таблицу выключена (нужен сервисный аккаунт Google, см. README). Актуальный Excel всегда можно выгрузить кнопкой «Excel в чат»."),
    field("Ссылка", url),
    h("button", { class: "btn block", onclick: async () => {
      const r = await run(() => api("/api/sheet", { json: { url: url.value } }), "Таблица подключена 🔗");
      state.pid = r.data.project_id;
      safeStorage("set", "pid", state.pid);
      await reloadBoot();
      await refresh();
    } }, "Подключить"));
}

function planCard(p) {
  const box = h("div");
  const nodate = tasks().filter((t) => isOpen(t) && !t.deadline).length;
  const drawPlan = () => {
    if (!state.plan) return;
    const items = state.plan;
    const checks = items.map(() => h("input", { type: "checkbox", checked: true }));
    const apply = h("button", { class: "btn block", style: "margin-top:10px", onclick: async () => {
      const chosen = items.filter((_, i) => checks[i].checked).map((x) => ({ task_id: x.task_id, deadline: x.deadline }));
      if (!chosen.length) { toast("Ничего не выбрано"); return; }
      const r = await run(() => api(`/api/projects/${p.id}/plan`, { json: { items: chosen } }));
      toast(`Сроки проставлены: ${r.data.applied}`);
      state.plan = null;
      await refresh();
    } }, `Применить выбранные`);
    box.replaceChildren(
      h("div", { class: "hint", style: "margin:8px 0" }, `Предложено сроков: ${items.length}. Снимите галочки там, где срок не подходит — потом его можно поправить в задаче.`),
      h("div", { class: "card flush", style: "background:var(--bg)" }, items.map((x, i) =>
        h("label", { class: "list-item", style: "cursor:pointer" }, checks[i],
          h("div", { class: "grow" },
            h("div", { class: "name" }, x.title),
            h("div", { class: "hint" }, `${fmtDate(x.deadline, true)} (T${x.offset >= 0 ? "+" : ""}${x.offset}) · ${x.responsible || "—"}`),
            h("div", { class: "hint" }, x.rule + (x.note ? " · " + x.note : "")))))),
      apply);
  };
  const load = h("button", { class: "btn block", disabled: !p.event_date || !nodate, onclick: async () => {
    const r = await run(() => api(`/api/projects/${p.id}/plan`));
    state.plan = r.items;
    drawPlan();
  } }, "Предложить сроки");
  drawPlan();
  return h("details", { class: "card", open: !!state.plan },
    h("summary", null, `🗓 Черновик сроков (${nodate} без срока)`),
    h("p", { class: "hint", style: "margin:0 0 10px" }, p.event_date
      ? "Бот расставит дедлайны задачам без срока обратным отсчётом от дня мероприятия (KV за 18 дней, производство за 10, монтаж T-1, отчёт T+7…) и разнесёт их так, чтобы у человека было не больше 3 дедлайнов в день."
      : "Сначала укажите дату мероприятия в карточке проекта."),
    load, box);
}

function auditCard() {
  const issues = state.data.audit;
  return h("details", { class: "card" },
    h("summary", null, `🧹 Проверка таблицы (${issues.length})`),
    issues.length ? h("div", { class: "card flush", style: "background:var(--bg)" }, issues.map((i) => h("div", { class: "issue" },
      h("div", { class: "lvl " + i.level }, i.level === "critical" ? "Важно" : i.level === "warning" ? "Проверить" : "Инфо"),
      h("div", { style: "font-weight:600" }, `${i.title}: ${i.count}`),
      i.hint ? h("div", { class: "hint" }, i.hint) : null,
      h("ul", null, i.items.slice(0, 8).map((x) => h("li", null, x)), i.items.length > 8 ? h("li", null, `…ещё ${i.count - 8}`) : null))))
      : h("div", { class: "empty" }, "Всё заполнено 👍"));
}

function teamCard() {
  const members = state.boot.members;
  return h("details", { class: "card" },
    h("summary", null, `👥 Команда (${members.filter((m) => m.active).length})`),
    h("div", { class: "card flush", style: "background:var(--bg)" }, members.map((m) =>
      h("div", { class: "list-item", style: "cursor:pointer" + (m.active ? "" : ";opacity:.5"), onclick: () => openMemberForm(m) },
        h("div", { class: "avatar", style: `background:${avatarColor(m.id)}` }, initials(m.name)),
        h("div", { class: "grow" },
          h("div", { class: "row wrap" }, h("span", { class: "name" }, m.name),
            m.is_admin ? h("span", { class: "chip" }, "админ") : null,
            m.is_pm ? h("span", { class: "chip" }, "PM") : null,
            m.connected ? h("span", { class: "chip s-done" }, "в боте") : h("span", { class: "chip warn" }, "не нажал Start")),
          h("div", { class: "hint" }, `${m.role || ""}${m.username ? " · @" + m.username : ""}`))))),
    h("button", { class: "btn secondary block", style: "margin-top:10px", onclick: () => openMemberForm(null) }, "➕ Добавить участника"));
}

function openMemberForm(m) {
  const f = {
    name: h("input", { value: m ? m.name : "", placeholder: "Как в колонке «Ответственный»" }),
    username: h("input", { value: m ? m.username : "", placeholder: "ник без @" }),
    role: h("input", { value: m ? m.role : "" }),
    aliases: h("input", { value: m ? m.aliases : "", placeholder: "Другие написания через запятую" }),
    phone: h("input", { type: "tel", value: m ? m.phone : "" }),
  };
  const admin = h("input", { type: "checkbox", checked: m && m.is_admin });
  const pm = h("input", { type: "checkbox", checked: m && m.is_pm });
  const active = h("input", { type: "checkbox", checked: !m || m.active });
  showSheet(h("div", null,
    h("h2", null, m ? m.name : "Новый участник"),
    h("div", { class: "card" },
      field("Имя *", f.name), field("Telegram-ник", f.username), field("Роль", f.role),
      field("Другие написания", f.aliases), field("Телефон (для обзвона)", f.phone),
      h("label", { class: "check" }, admin, "Администратор (загрузка таблиц, любые правки)"),
      h("label", { class: "check" }, pm, "Получает эскалации и сводку PM"),
      h("label", { class: "check" }, active, "Активен"),
      h("button", { class: "btn block", style: "margin-top:8px", onclick: async () => {
        const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
        Object.assign(body, { id: m ? m.id : null, is_admin: admin.checked, is_pm: pm.checked, active: active.checked });
        await run(() => api("/api/members", { json: body }), "Сохранено");
        closeSheet();
        await reloadBoot();
        await refresh();
      } }, "Сохранить"))));
}

function settingsCard() {
  const s = state.boot.settings;
  const f = {
    morning_time: h("input", { type: "time", value: s.morning_time }),
    pm_time: h("input", { type: "time", value: s.pm_time }),
    evening_time: h("input", { type: "time", value: s.evening_time }),
    standup_time: h("input", { type: "time", value: s.standup_time }),
    checks_per_day: h("input", { type: "number", min: 1, max: 20, value: s.checks_per_day }),
    sync_minutes: h("input", { type: "number", min: 0, max: 1440, value: s.sync_minutes }),
    weekly_day: h("select", null, WEEKDAY_NAMES.map((n, i) => h("option", { value: i, selected: String(i) === s.weekly_day }, n))),
  };
  return h("details", { class: "card" },
    h("summary", null, "⏰ Напоминания"),
    h("div", { class: "grid2" },
      field("Утро: задачи + «выполнено?»", f.morning_time),
      field("Сводка PM и группе", f.pm_time),
      field("Вечер: «успеваете?»", f.evening_time),
      field("Статус-созвон (пусто = нет)", f.standup_time),
      field("Вопросов «выполнено?» в день", f.checks_per_day),
      field("Синхронизация, мин", f.sync_minutes)),
    field("Недельный обзор (задачи без срока)", f.weekly_day),
    h("p", { class: "hint", style: "margin:0 0 10px" }, s.group_chat_id
      ? "✅ Рабочая группа подключена — сводки и закрытые задачи уходят туда."
      : "Группа не подключена: добавьте бота в рабочую группу и отправьте там /bind_group."),
    h("button", { class: "btn block", onclick: async () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
      await run(() => api("/api/settings", { json: body }), "Сохранено");
      await reloadBoot();
      render();
    } }, "Сохранить"));
}

function newProjectCard() {
  const name = h("input", { placeholder: "Например, Открытие шоурума" });
  const date = h("input", { type: "date" });
  return h("details", { class: "card", open: !state.pid },
    h("summary", null, "🆕 Новый проект без Excel"),
    field("Название", name),
    field("Дата мероприятия", date),
    h("button", { class: "btn block", onclick: async () => {
      const r = await run(() => api("/api/projects", { json: { name: name.value, event_date: date.value || null } }), "Проект создан");
      state.pid = r.data.id;
      safeStorage("set", "pid", state.pid);
      await reloadBoot();
      await refresh();
    } }, "Создать"));
}

init();
