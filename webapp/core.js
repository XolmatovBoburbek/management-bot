"use strict";
/* Общие утилиты кабинета: состояние, API, даты, всплывающие окна, меню, иконки. */

const tg = window.Telegram && window.Telegram.WebApp;
const inTelegram = !!(tg && tg.initData);

const STATUS = {
  todo: { label: "Не начата", color: "gray" },
  progress: { label: "В работе", color: "blue" },
  overdue: { label: "Просрочена", color: "red" },
  done: { label: "Выполнена", color: "green" },
  done_late: { label: "С просрочкой", color: "yellow" },
  cancelled: { label: "Отменена", color: "default" },
};
const STATUS_OPTIONS = ["todo", "progress", "done", "done_late", "cancelled"];
const PRIORITIES = ["Критично", "Высокий", "Средний", "Низкий"];
const PRIORITY_COLOR = { "Критично": "red", "Высокий": "orange", "Средний": "yellow", "Низкий": "gray" };
const BUCKETS = ["overdue", "today", "tomorrow", "week", "later", "nodate", "done", "cancelled"];
const BUCKET_TITLE = {
  overdue: "Просрочено", today: "Сегодня", tomorrow: "Завтра", week: "На этой неделе",
  later: "Позже", nodate: "Без срока", done: "Выполнено", cancelled: "Отменено",
};
const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const MONTHS_FULL = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const WEEKDAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const AVATAR_COLORS = ["#e16259", "#d9730d", "#cb912f", "#448361", "#337ea9", "#9065b0", "#c14c8a", "#787774"];
const EVENT_ICONS = {
  status: "🔄", comment: "💬", problem: "🆘", resolved: "✔️", checkin: "👍", eta: "🗓", edit: "✏️",
  created: "➕", archived: "🗑", call: "📞", stage: "🗂",
};
const ROLE_LABEL = { admin: "Администратор", member: "Участник", viewer: "Наблюдатель" };

const S = {
  boot: null,
  token: null,
  wid: null,
  ws: null,
  project: null,       // {pid, data}
  calls: null,
  route: { name: "loading", params: {} },
  sidebarOpen: false,
  peek: null,          // id открытой задачи
  overlays: [],
  embeds: new Map(),   // доски задач внутри открытой страницы: id блока → перезагрузка
};

// ---------- DOM ----------
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (key === "value") el.value = value;
      else if (key === "checked" || key === "selected" || key === "disabled") el[key] = !!value;
      else if (key === "dataset") Object.assign(el.dataset, value);
      else el.setAttribute(key, value === true ? "" : value);
    }
  }
  append(el, children);
  return el;
}

/** Заменить содержимое: как replaceChildren, но понимает массивы и пропускает null. */
function fill(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

function append(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  check: '<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="m8 12 3 3 5-6"/>',
  tick: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  page: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  more: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.5 3.3-5.5 6.5-5.5s5.9 2 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.8c2 .8 3.2 2.6 3.5 5.2"/>',
  lock: '<rect x="4.5" y="10.5" width="15" height="10" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M4.2 5.6l2.1 2.1M17.7 16.3l2.1 2.1M2.5 12h3M18.5 12h3M4.2 18.4l2.1-2.1M17.7 7.7l2.1-2.1"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/>',
  board: '<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="11" rx="1.5"/><rect x="17" y="4" width="4" height="7" rx="1.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  phone: '<path d="M5 3.5h4l1.5 4.5-2.5 1.5a11 11 0 0 0 6.5 6.5l1.5-2.5 4.5 1.5v4a1.5 1.5 0 0 1-1.5 1.5A17.5 17.5 0 0 1 3.5 5 1.5 1.5 0 0 1 5 3.5z"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
  logout: '<path d="M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5M10 16l-4-4 4-4M6 12h10"/>',
  sync: '<path d="M20 12a8 8 0 0 1-14 5.3M4 12a8 8 0 0 1 14-5.3M18 3v4h-4M6 21v-4h4"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  drag: '<circle cx="9" cy="6" r="1.3"/><circle cx="15" cy="6" r="1.3"/><circle cx="9" cy="12" r="1.3"/><circle cx="15" cy="12" r="1.3"/><circle cx="9" cy="18" r="1.3"/><circle cx="15" cy="18" r="1.3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c.8-4.2 4-7 8-7s7.2 2.8 8 7"/>',
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  status: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.2" fill="currentColor" stroke="none"/>',
  flag: '<path d="M5 21V4.5M5 4.5h11l-2 4 2 4H5"/>',
  tag: '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1 1 0 0 1 0 1.4l-7.3 7.3a1 1 0 0 1-1.4 0z"/><circle cx="8" cy="8" r="1.3"/>',
  building: '<rect x="4" y="3.5" width="11" height="17" rx="1"/><path d="M15 9.5h4a1 1 0 0 1 1 1v10H4M7.5 7.5h1M10.5 7.5h1M7.5 11h1M10.5 11h1M7.5 14.5h1M10.5 14.5h1"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>',
  text: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
  expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>',
  filter: '<path d="M4 6h16M7 12h10M10 18h4"/>',
  sort: '<path d="M7 4v16M3.5 16.5 7 20l3.5-3.5M17 20V4M13.5 7.5 17 4l3.5 3.5"/>',
  group: '<rect x="3.5" y="4" width="7" height="7" rx="1.5"/><rect x="13.5" y="4" width="7" height="7" rx="1.5"/><rect x="3.5" y="14" width="7" height="6" rx="1.5"/><rect x="13.5" y="14" width="7" height="6" rx="1.5"/>',
  comment: '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v10a1.5 1.5 0 0 1-1.5 1.5H9l-5 4z"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  done: '<circle cx="12" cy="12" r="8.5"/><path d="m8.2 12.2 2.6 2.6 5-5.3"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.4M12 16.8v.2"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5"/><path d="M3.5 4v4.5H8M12 7.5V12l3 2"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 8.5V5a1.5 1.5 0 0 0-1.5-1.5H5A1.5 1.5 0 0 0 3.5 5v9A1.5 1.5 0 0 0 5 15.5h3.5"/>',
  edit: '<path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="m14.5 7.5 3 3"/>',
  dleft: '<path d="m11 17-5-5 5-5M18 17l-5-5 5-5"/>',
  dright: '<path d="m13 17 5-5-5-5M6 17l5-5-5-5"/>',
  left: '<path d="m15 6-6 6 6 6"/>',
  sparkle: '<path d="M12 3.5 13.8 10l6.7 2-6.7 2L12 20.5 10.2 14l-6.7-2 6.7-2z"/>',
  archive: '<rect x="3.5" y="4" width="17" height="4.5" rx="1"/><path d="M5 8.5V19a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8.5M10 12.5h4"/>',
  workspace: '<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="M8 15.5V8.5l4 4 4-4v7"/>',
};

function icon(name, cls) {
  const span = document.createElement("span");
  span.className = "ico" + (cls ? " " + cls : "");
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  return span;
}

// ---------- сеть ----------
class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data || {};
  }
}

async function api(path, opts = {}) {
  const headers = {};
  if (S.token) headers.Authorization = "Bearer " + S.token;
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  let res;
  try {
    res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body, credentials: "same-origin" });
  } catch (_) {
    throw new ApiError("Нет связи с сервером. Проверьте интернет.", 0);
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* пустой ответ */ }
  if (!res.ok) {
    const err = new ApiError((data && data.error) || `Ошибка ${res.status}`, res.status, data);
    if (res.status === 401 && data && data.auth && !opts.noAuthRedirect && window.onAuthLost) window.onAuthLost(err);
    if (res.status === 403 && data && data.must_change_password && window.onMustChangePassword) window.onMustChangePassword();
    throw err;
  }
  return data;
}

function toast(message, kind) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast" + (kind ? " " + kind : "");
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 3200);
}

function haptic(kind) {
  try { inTelegram && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (_) { /* старый клиент */ }
}

async function run(action, success) {
  try {
    const result = await action();
    if (success) { toast(success, "ok"); haptic("success"); }
    return result;
  } catch (err) {
    haptic("error");
    if (err.status !== 409) toast(err.message, "error");
    throw err;
  }
}

function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("pm:" + key));
    localStorage.setItem("pm:" + key, JSON.stringify(value));
  } catch (_) { /* приватный режим */ }
  return null;
}

// ---------- даты и текст ----------
function parseISO(s) {
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

function localISO(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function today() { return parseISO(S.boot.today); }

function fmtDate(s, withWeekday) {
  if (!s) return "без срока";
  const d = parseISO(s);
  const year = d.getFullYear() !== today().getFullYear() ? " " + d.getFullYear() : "";
  return `${d.getDate()} ${MONTHS[d.getMonth()]}${year}` + (withWeekday ? `, ${WEEKDAYS[d.getDay()]}` : "");
}

function fmtDateTime(s) {
  if (!s) return "";
  const d = new Date(s);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function relTime(s) {
  if (!s) return "";
  const diff = (Date.now() - new Date(s).getTime()) / 1000;
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return fmtDateTime(s);
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

function initials(name) {
  const parts = (name || "?").trim().split(/\s+/);
  return (parts[0][0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

function avatarColor(id) { return AVATAR_COLORS[Math.abs(id || 0) % AVATAR_COLORS.length]; }

function avatar(name, id, size) {
  return h("span", { class: "avatar" + (size ? " " + size : ""), style: { background: avatarColor(id) }, title: name }, initials(name));
}

function tag(text, color, extra) {
  return h("span", { class: `tag c-${color || "default"}` + (extra ? " " + extra : "") }, text);
}

const TAG_COLORS = ["gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"];

function statusTag(status) {
  const s = STATUS[status] || STATUS.todo;
  return h("span", { class: `tag status c-${s.color}` }, h("i", { class: "dot" }), s.label);
}

function statusPill(color, label) {
  return h("span", { class: `tag status c-${color}` }, h("i", { class: "dot" }), label);
}

/** Цвет «тега» для произвольного значения (блок, пространство): стабильный по названию. */
function colorFor(text) {
  let hash = 0;
  for (const ch of String(text || "")) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return TAG_COLORS[hash % TAG_COLORS.length];
}
function priorityTag(p) { return p ? tag(p, PRIORITY_COLOR[p] || "default") : null; }
function isOpen(t) { return ["todo", "progress", "overdue"].includes(t.status); }

function member(id) { return S.boot.members.find((m) => m.id === id); }
function me() { return S.boot.me; }
function isSuperadmin() { return !!S.boot.me.is_superadmin; }
function role() { return S.ws ? S.ws.role : null; }
function isWsAdmin() { return role() === "admin"; }
function canWrite() { return role() === "admin" || role() === "member"; }
function seesAll() { return role() === "admin" || role() === "viewer"; }

// ---------- оверлеи: окна, меню, подтверждения ----------
function pushOverlay(el, onClose) {
  const entry = { el, onClose };
  S.overlays.push(entry);
  document.getElementById("overlay-root").append(el);
  syncBackButton();
  return () => closeOverlay(entry);
}

function closeOverlay(entry) {
  const i = S.overlays.indexOf(entry || S.overlays[S.overlays.length - 1]);
  if (i < 0) return false;
  const [item] = S.overlays.splice(i, 1);
  item.el.remove();
  if (item.onClose) item.onClose();
  syncBackButton();
  return true;
}

function closeAllOverlays() { while (S.overlays.length) closeOverlay(); }

function syncBackButton() {
  if (!inTelegram || !tg.BackButton) return;
  if (S.overlays.length || S.peek || S.sidebarOpen || (history.length > 1 && S.route.name !== "home")) tg.BackButton.show();
  else tg.BackButton.hide();
}

function modal(title, content, opts = {}) {
  let close;
  const box = h("div", { class: "modal" + (opts.wide ? " wide" : "") },
    h("div", { class: "modal-head" }, h("div", { class: "modal-title" }, title),
      h("button", { class: "icon-btn", onclick: () => close(), "aria-label": "Закрыть" }, icon("close"))),
    h("div", { class: "modal-body" }, content));
  const wrap = h("div", { class: "modal-wrap", onmousedown: (e) => { if (e.target === wrap) close(); } }, box);
  close = pushOverlay(wrap, opts.onClose);
  const first = box.querySelector("input:not([type=checkbox]):not([type=file]), textarea, select");
  if (first && !opts.noFocus && window.matchMedia("(pointer: fine)").matches) setTimeout(() => first.focus(), 30);
  return close;
}

function confirmDialog(text, okLabel = "Да", danger = true) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => { if (!done) { done = true; resolve(value); close(); } };
    const close = modal("Подтвердите", h("div", null,
      h("p", { class: "confirm-text" }, text),
      h("div", { class: "btn-row end" },
        h("button", { class: "btn ghost", onclick: () => finish(false) }, "Отмена"),
        h("button", { class: "btn " + (danger ? "danger" : "primary"), onclick: () => finish(true) }, okLabel))),
    { onClose: () => { if (!done) { done = true; resolve(false); } } });
  });
}

function promptDialog(title, label, value = "", okLabel = "Сохранить") {
  return new Promise((resolve) => {
    let done = false;
    const input = h("input", { class: "input", value });
    const finish = (v) => { if (!done) { done = true; resolve(v); close(); } };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(input.value); });
    const close = modal(title, h("div", null, field(label, input),
      h("div", { class: "btn-row end" },
        h("button", { class: "btn ghost", onclick: () => finish(null) }, "Отмена"),
        h("button", { class: "btn primary", onclick: () => finish(input.value) }, okLabel))),
    { onClose: () => { if (!done) { done = true; resolve(null); } } });
  });
}

/** Всплывающее меню у элемента. items: [{label, icon, value, danger, checked, hint, divider, onClick}] */
function popMenu(anchor, items, opts = {}) {
  let close;
  const list = h("div", { class: "menu" + (opts.cls ? " " + opts.cls : "") });
  if (opts.title) list.append(h("div", { class: "menu-title" }, opts.title));
  if (opts.search) {
    const input = h("input", { class: "menu-search", placeholder: opts.search });
    list.append(input);
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      list.querySelectorAll(".menu-item").forEach((el) => { el.hidden = q && !el.textContent.toLowerCase().includes(q); });
    });
    setTimeout(() => input.focus(), 20);
  }
  for (const item of items) {
    if (!item) continue;
    if (item.divider) { list.append(h("div", { class: "menu-sep" })); continue; }
    list.append(h("button", {
      class: "menu-item" + (item.danger ? " danger" : "") + (item.checked ? " checked" : ""),
      onclick: async (e) => {
        e.stopPropagation();
        close();
        if (item.onClick) await item.onClick();
        if (opts.onSelect) await opts.onSelect(item.value, item);
      },
    }, item.icon ? (item.icon instanceof Node ? item.icon
      : ICONS[item.icon] ? icon(item.icon) : h("span", { class: "menu-emoji" }, item.icon)) : null,
    h("span", { class: "menu-label" }, item.label), item.hint ? h("span", { class: "menu-hint" }, item.hint) : null,
    item.checked ? h("span", { class: "menu-check" }, "✓") : null));
  }
  const layer = h("div", { class: "menu-layer", onmousedown: (e) => { if (e.target === layer) close(); } }, list);
  close = pushOverlay(layer, opts.onClose);
  placeNear(list, anchor);
  return close;
}

function placeNear(el, anchor) {
  const r = anchor.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  el.style.position = "fixed";
  el.style.visibility = "hidden";
  requestAnimationFrame(() => {
    const w = el.offsetWidth;
    const hgt = el.offsetHeight;
    let left = Math.min(r.left, vw - w - 8);
    let top = r.bottom + 4;
    if (top + hgt > vh - 8) top = Math.max(8, r.top - hgt - 4);
    el.style.left = Math.max(8, left) + "px";
    el.style.top = top + "px";
    el.style.visibility = "";
  });
}

function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label), input,
    hint ? h("span", { class: "field-hint" }, hint) : null);
}

/** Делает элемент редактируемой строкой: Enter — сохранить, Esc — отменить. */
function editableText(el, value, onSave, opts = {}) {
  el.contentEditable = "plaintext-only";
  if (el.contentEditable !== "plaintext-only") el.contentEditable = "true";
  el.spellcheck = false;
  if (opts.placeholder) el.dataset.placeholder = opts.placeholder;
  let current = value || "";
  el.textContent = current;
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); el.blur(); }
    if (e.key === "Escape") { el.textContent = current; el.blur(); }
  });
  el.addEventListener("paste", (e) => {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData("text").replace(/\s+/g, " ");
    document.execCommand("insertText", false, text);
  });
  if (opts.onInput) el.addEventListener("input", () => opts.onInput(el.textContent));
  el.addEventListener("blur", async () => {
    const next = el.textContent.replace(/\s+/g, " ").trim();
    if (next === current) return;
    if (!next && !opts.allowEmpty) { el.textContent = current; return; }
    try {
      await onSave(next);
      current = next;
    } catch (_) {
      el.textContent = current;
    }
  });
  return el;
}

function copyText(text) {
  const done = () => toast("Скопировано", "ok");
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(done, () => fallback());
  return fallback();
  function fallback() {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }, text);
    document.body.append(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (_) { toast("Не удалось скопировать", "error"); }
    ta.remove();
  }
}

function emptyState(emoji, title, text, action) {
  return h("div", { class: "empty" }, h("div", { class: "empty-emoji" }, emoji), h("div", { class: "empty-title" }, title),
    text ? h("div", { class: "empty-text" }, text) : null, action || null);
}

function progressBar(pct, color) {
  return h("div", { class: "progress" }, h("i", { style: { width: Math.max(0, Math.min(100, pct)) + "%", background: color || "" } }));
}

function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  wrapped.flush = (...args) => { clearTimeout(t); return fn(...args); };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

function uid() { return Math.random().toString(16).slice(2, 10); }

const EMOJI = ["📄", "📝", "📌", "📋", "📊", "📈", "📅", "🗓", "✅", "💡", "🎯", "🚀", "⭐", "🔥", "⚠️", "❗", "📞", "💬",
  "👥", "🤝", "🏢", "🎉", "🎤", "🎬", "📷", "🎨", "🖥", "💰", "🧾", "📦", "🚚", "🔧", "🧩", "🔒", "📚", "🗂", "🗒", "🏁",
  "🌟", "❤️", "🟢", "🟡", "🔴", "🔵", "🟣"];

function emojiPicker(anchor, onPick, allowRemove) {
  let close;
  const grid = h("div", { class: "emoji-grid" }, EMOJI.map((e) => h("button", { class: "emoji-btn", onclick: () => { close(); onPick(e); } }, e)));
  const box = h("div", { class: "menu emoji-menu" }, grid,
    allowRemove ? h("button", { class: "menu-item", onclick: () => { close(); onPick(""); } }, h("span", { class: "menu-label" }, "Убрать иконку")) : null);
  const layer = h("div", { class: "menu-layer", onmousedown: (e) => { if (e.target === layer) close(); } }, box);
  close = pushOverlay(layer);
  placeNear(box, anchor);
}
