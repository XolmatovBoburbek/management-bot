"use strict";
/* Задачи проекта как база данных Notion: доска (карточки и перетаскивание как в Trello), таблица,
 * календарь и список; карточка задачи в боковой панели; обзор, обзвон и управление проектом. */

const DB_VIEWS = [
  { id: "board", label: "Доска", icon: "board" },
  { id: "table", label: "Таблица", icon: "table" },
  { id: "calendar", label: "Календарь", icon: "calendar" },
  { id: "list", label: "Список", icon: "list" },
];
const GROUP_BY = [
  { id: "status", label: "Статус", icon: "status" },
  { id: "assignee", label: "Ответственный", icon: "user" },
  { id: "block", label: "Блок", icon: "tag" },
  { id: "priority", label: "Приоритет", icon: "flag" },
];
const SORTS = [
  { id: "", label: "По срочности" },
  { id: "deadline", label: "По сроку" },
  { id: "priority", label: "По приоритету" },
  { id: "title", label: "По названию" },
];
const STATUS_FILTERS = [
  ["all", "Все"], ["open", "Открытые"], ["overdue", "Просроченные"], ["blocked", "Нужна помощь"],
  ["nodate", "Без срока"], ["progress", "В работе"], ["done", "Выполненные"],
];
const STATUS_COLUMNS = [
  { key: "todo", label: "Не начата", color: "gray", statuses: ["todo"], value: "todo", always: true, create: true },
  { key: "progress", label: "В работе", color: "blue", statuses: ["progress"], value: "progress", always: true, create: true },
  { key: "overdue", label: "Просрочена", color: "red", statuses: ["overdue"], value: null },
  { key: "done", label: "Выполнена", color: "green", statuses: ["done", "done_late"], value: "done", always: true },
  { key: "cancelled", label: "Отменена", color: "default", statuses: ["cancelled"], value: "cancelled" },
];
const STATUS_TOAST = { progress: "Взято в работу", done: "Выполнено ✅", todo: "Статус изменён", cancelled: "Задача отменена",
  done_late: "Выполнено" };
const WEEK_HEAD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function projectHref(pid, section) { return `#/w/${S.wid}/p/${pid}` + (section ? "/" + section : ""); }
function norm(s) { return String(s || "").toLowerCase().replace(/ё/g, "е").trim(); }
function uniq(list) { return [...new Set(list)]; }
function myMemberId() { const m = S.boot.me.member; return m ? m.id : -S.boot.me.id; }

// ---------- данные ----------
async function ensureProject(pid, force) {
  if (!force && S.project && S.project.pid === pid) return S.project.data;
  const data = await api(`/api/projects/${pid}`);
  S.project = { pid, data };
  return data;
}

/** После любого изменения задачи: перечитать текущий экран и открытую карточку. */
async function taskChanged() {
  await reloadView();
  if (S.peek) await openPeek(S.peek, true);
}

function dbConfig(key, defaults) {
  const saved = store("db:" + key) || {};
  return { view: "board", group: "status", sort: "", status: "all", member: "", block: "", prio: "", mine: false,
    month: "", ...(defaults || {}), ...saved, q: "" };
}

function saveDb(key, cfg) {
  const { q, ...rest } = cfg;
  store("db:" + key, rest);
}

function filterTasks(tasks, f) {
  const q = norm(f.q);
  return tasks.filter((t) => {
    if (f.mine && !t.mine) return false;
    if (f.member && !(t.assignee_ids || []).includes(Number(f.member))) return false;
    if (f.block && (f.block === "__none" ? t.block : t.block !== f.block)) return false;
    if (f.prio && (f.prio === "__none" ? t.priority : t.priority !== f.prio)) return false;
    switch (f.status) {
      case "open": if (!isOpen(t)) return false; break;
      case "overdue": if (t.status !== "overdue") return false; break;
      case "blocked": if (!t.blocked || !isOpen(t)) return false; break;
      case "nodate": if (t.deadline || !isOpen(t)) return false; break;
      case "progress": if (t.raw_status !== "progress" || !isOpen(t)) return false; break;
      case "done": if (!["done", "done_late"].includes(t.status)) return false; break;
      default: break;
    }
    if (q && !norm(`${t.title} ${t.description} ${t.responsible} ${t.contractor} ${t.block} ${t.project_name || ""}`).includes(q)) {
      return false;
    }
    return true;
  });
}

function sortTasks(list, sort) {
  if (!sort) return list;
  const arr = [...list];
  const prio = (t) => { const i = PRIORITIES.indexOf(t.priority); return i < 0 ? 9 : i; };
  if (sort === "deadline") arr.sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"));
  if (sort === "priority") arr.sort((a, b) => prio(a) - prio(b));
  if (sort === "title") arr.sort((a, b) => a.title.localeCompare(b.title, "ru"));
  return arr;
}

function countFilters(cfg) {
  return (cfg.mine ? 1 : 0) + (cfg.status !== "all" ? 1 : 0) + (cfg.member ? 1 : 0) + (cfg.block ? 1 : 0) + (cfg.prio ? 1 : 0);
}

function blocksOf(tasks) { return uniq(tasks.map((t) => t.block).filter(Boolean)); }

function peopleOf(tasks) {
  const ids = uniq(tasks.flatMap((t) => t.assignee_ids || []));
  return ids.map(member).filter(Boolean).sort((a, b) => a.name.localeCompare(b.name, "ru"));
}

// ---------- мелкие элементы ----------
function dueBadge(t, withIcon = true) {
  if (!t.deadline) return null;
  let cls = "";
  if (!isOpen(t)) cls = "done";
  else if (t.days_left < 0) cls = "overdue";
  else if (t.days_left === 0) cls = "today";
  else if (t.days_left <= 2) cls = "soon";
  return h("span", { class: "due " + cls, title: isOpen(t) ? daysText(t.days_left) : "срок" },
    withIcon ? icon("clock") : null, fmtDate(t.deadline));
}

function people(t) { return (t.assignee_ids || []).map(member).filter(Boolean); }

function avatarStack(t, max = 3) {
  const list = people(t);
  if (!list.length) return t.responsible ? h("span", { class: "resp-text", title: t.responsible }, t.responsible) : null;
  return h("span", { class: "avatars", title: list.map((m) => m.name).join(", ") },
    list.slice(0, max).map((m) => avatar(m.name, m.id, "xs")),
    list.length > max ? h("span", { class: "avatar xs more" }, "+" + (list.length - max)) : null);
}

function personChips(t) {
  const list = people(t);
  if (!list.length) return t.responsible ? h("span", null, t.responsible) : null;
  return h("span", { class: "person-chips" }, list.map((m) => h("span", { class: "person" }, avatar(m.name, m.id, "xs"), m.name)));
}

function statusDot(status) {
  const s = STATUS[status] || STATUS.todo;
  return h("i", { class: `sdot c-${s.color}`, title: s.label });
}

function sectionTitle(text, count, extra) {
  return h("div", { class: "section-title" }, h("span", null, text),
    count !== undefined && count !== null ? h("span", { class: "section-count" }, count) : null, extra || null);
}

function callout(emoji, content, color, action) {
  return h("div", { class: "callout c-" + (color || "gray") }, h("div", { class: "callout-emoji" }, emoji),
    h("div", { class: "callout-body" }, content, action ? h("div", { class: "callout-action" }, action) : null));
}

function toggleBlock(title, content, open, key) {
  const saved = key ? store("toggle:" + key) : null;
  const isOpenNow = saved === null || saved === undefined ? !!open : !!saved;
  const el = h("details", { class: "toggle-block", open: isOpenNow },
    h("summary", null, icon("chevron", "toggle-ico"), h("span", null, title)), h("div", { class: "toggle-body" }, content));
  if (key) el.addEventListener("toggle", () => store("toggle:" + key, el.open));
  return el;
}

/** Строки задач в духе списка Notion — для главной, обзора и «Требует внимания». */
function taskLines(list, opts = {}) {
  return h("div", { class: "lines" }, list.map((t) => h("div", { class: "line", onclick: () => openPeek(t.id) },
    statusDot(t.status),
    h("span", { class: "line-title" }, t.title),
    t.blocked && isOpen(t) ? tag("Нужна помощь", "red", "sm") : null,
    opts.project && t.project_name ? h("span", { class: "line-sub" }, t.project_name) : null,
    h("span", { class: "grow" }),
    opts.owner ? avatarStack(t, 2) : null,
    dueBadge(t, false))));
}

// ---------- выбор даты ----------
function datePicker(anchor, value, onPick, opts = {}) {
  let close;
  const selected = value ? parseISO(value) : null;
  const month = selected ? new Date(selected.getFullYear(), selected.getMonth(), 1)
    : new Date(today().getFullYear(), today().getMonth(), 1);
  const box = h("div", { class: "menu datepicker" });
  const pick = (iso) => { close(); onPick(iso); };
  const input = h("input", { class: "menu-search", placeholder: "Дата, например 25.10", value: selected ? fmtDate(value) : "" });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const iso = parseDateInput(input.value);
    if (iso) pick(iso); else toast("Не понял дату — например, 25.10 или 25.10.2026", "error");
  });
  const draw = () => {
    const first = new Date(month);
    const start = new Date(first);
    start.setDate(1 - ((first.getDay() + 6) % 7));
    const cells = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const iso = localISO(d);
      cells.push(h("button", {
        class: "dp-day" + (d.getMonth() !== month.getMonth() ? " other" : "") + (iso === S.boot.today ? " today" : "")
          + (selected && iso === localISO(selected) ? " selected" : ""),
        onclick: () => pick(iso),
      }, d.getDate()));
    }
    fill(box, input,
      h("div", { class: "dp-head" }, h("span", { class: "dp-title" }, `${MONTHS_FULL[month.getMonth()]} ${month.getFullYear()}`),
        h("button", { class: "icon-btn sm", onclick: () => { month.setMonth(month.getMonth() - 1); draw(); }, "aria-label": "Назад" }, icon("left")),
        h("button", { class: "icon-btn sm", onclick: () => { month.setMonth(month.getMonth() + 1); draw(); }, "aria-label": "Вперёд" }, icon("chevron"))),
      h("div", { class: "dp-grid" }, WEEK_HEAD.map((d) => h("span", { class: "dp-wd" }, d)), cells),
      h("div", { class: "menu-sep" }),
      h("button", { class: "menu-item", onclick: () => pick(S.boot.today) }, h("span", { class: "menu-label" }, "Сегодня")),
      opts.noClear ? null : h("button", { class: "menu-item", onclick: () => pick(null) }, h("span", { class: "menu-label" }, "Очистить")));
  };
  draw();
  const layer = h("div", { class: "menu-layer", onmousedown: (e) => { if (e.target === layer) close(); } }, box);
  close = pushOverlay(layer);
  placeNear(box, anchor);
}

function parseDateInput(text) {
  const s = text.trim();
  let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) return localISO(new Date(+m[1], +m[2] - 1, +m[3]));
  m = s.match(/^(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?$/);
  if (!m) return null;
  let year = m[3] ? +m[3] : today().getFullYear();
  if (year < 100) year += 2000;
  const d = new Date(year, +m[2] - 1, +m[1]);
  return d.getDate() === +m[1] ? localISO(d) : null;
}

// ---------- правка свойств задачи ----------
async function patchTask(t, fields, message) {
  await run(() => api(`/api/tasks/${t.id}`, { method: "PATCH", json: fields }), message || "Сохранено");
  await taskChanged();
}

async function setStatus(t, status) {
  await run(() => api(`/api/tasks/${t.id}/status`, { json: { status } }), STATUS_TOAST[status] || "Статус изменён");
  await taskChanged();
}

function editStatus(anchor, t) {
  popMenu(anchor, STATUS_OPTIONS.map((s) => ({
    label: statusTag(s), checked: t.raw_status === s || (s === "done" && t.raw_status === "done_late"),
    onClick: () => setStatus(t, s),
  })), { title: "Статус" });
}

function editAssignee(anchor, t) {
  const items = S.boot.members.filter((m) => m.active).map((m) => ({
    label: h("span", { class: "menu-person" }, avatar(m.name, m.id, "xs"), m.name),
    hint: m.role || "", checked: (t.assignee_ids || []).includes(m.id),
    onClick: () => patchTask(t, { responsible: m.name }, "Ответственный назначен"),
  }));
  items.push({ divider: true },
    { label: "Ввести вручную…", icon: "edit", onClick: async () => {
      const value = await promptDialog("Ответственный", "Имя или несколько через запятую", t.responsible || "");
      if (value !== null) await patchTask(t, { responsible: value });
    } },
    t.responsible ? { label: "Убрать ответственного", icon: "close", onClick: () => patchTask(t, { responsible: "" }) } : null);
  popMenu(anchor, items, { title: "Ответственный", search: "Найти человека…" });
}

function editPriority(anchor, t) {
  popMenu(anchor, [
    ...PRIORITIES.map((p) => ({ label: tag(p, PRIORITY_COLOR[p]), checked: t.priority === p, onClick: () => patchTask(t, { priority: p }) })),
    { divider: true },
    { label: "Без приоритета", checked: !t.priority, onClick: () => patchTask(t, { priority: "" }) },
  ], { title: "Приоритет" });
}

function editBlock(anchor, t, allTasks) {
  const blocks = blocksOf(allTasks || []);
  popMenu(anchor, [
    ...blocks.map((b) => ({ label: tag(b, colorFor(b)), checked: t.block === b, onClick: () => patchTask(t, { block: b }) })),
    blocks.length ? { divider: true } : null,
    { label: "Новый блок…", icon: "plus", onClick: async () => {
      const value = await promptDialog("Новый блок", "Название блока", "");
      if (value) await patchTask(t, { block: value });
    } },
    t.block ? { label: "Без блока", icon: "close", onClick: () => patchTask(t, { block: "" }) } : null,
  ], { title: "Блок", search: blocks.length > 6 ? "Найти блок…" : null });
}

function editDate(anchor, t, key) {
  datePicker(anchor, t[key], (iso) => patchTask(t, { [key]: iso || "" }, iso ? "Дата сохранена" : "Дата убрана"));
}

async function editText(t, key, label) {
  const value = await promptDialog(label, label, t[key] || "");
  if (value !== null) await patchTask(t, { [key]: value });
}

// ---------- перетаскивание (мышь и палец) ----------
/** Перетаскивание на pointer-событиях: мышью — сразу после сдвига, пальцем — после удержания. */
function enableDrag(el, handlers) {
  el.addEventListener("contextmenu", (e) => { if (el.classList.contains("drag-armed")) e.preventDefault(); });
  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (e.target.closest("button, a, input, textarea, [contenteditable]")) return;
    const touch = e.pointerType !== "mouse";
    const allowed = handlers.canDrag();
    const start = { x: e.clientX, y: e.clientY };
    const last = { x: e.clientX, y: e.clientY };
    let dragging = false;
    let moved = false;
    let timer = null;
    const preventScroll = (ev) => { if (dragging && ev.cancelable) ev.preventDefault(); };
    const begin = () => {
      dragging = true;
      el.classList.remove("drag-armed");
      document.addEventListener("touchmove", preventScroll, { passive: false });
      if (touch) { try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred("medium"); } catch (_) { /* старый клиент */ } }
      handlers.onStart(last.x, last.y);
    };
    if (touch && allowed) {
      el.classList.add("drag-armed");
      timer = setTimeout(begin, 260);
    }
    const move = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      last.x = ev.clientX;
      last.y = ev.clientY;
      const dist = Math.hypot(last.x - start.x, last.y - start.y);
      if (!dragging) {
        if (dist > 6) moved = true;
        if (touch) {
          if (dist > 8) { clearTimeout(timer); el.classList.remove("drag-armed"); }
          return;
        }
        if (allowed && dist > 5) begin();
        if (!dragging) return;
      }
      ev.preventDefault();
      handlers.onMove(last.x, last.y);
    };
    const up = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      clearTimeout(timer);
      el.classList.remove("drag-armed");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.removeEventListener("touchmove", preventScroll);
      if (dragging) handlers.onDrop(last.x, last.y, ev.type === "pointercancel");
      else if (!moved && ev.type === "pointerup") handlers.onClick();
    };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  });
}

/** Автопрокрутка доски и страницы, когда карточку подносят к краю. */
function autoScroll(boardEl) {
  let x = 0;
  let y = 0;
  let raf = null;
  const scroller = document.querySelector(".scroller");
  const tick = () => {
    if (boardEl) {
      const r = boardEl.getBoundingClientRect();
      if (x < r.left + 60) boardEl.scrollLeft -= Math.ceil((r.left + 60 - x) / 4);
      else if (x > r.right - 60) boardEl.scrollLeft += Math.ceil((x - r.right + 60) / 4);
    }
    if (scroller) {
      const r = scroller.getBoundingClientRect();
      if (y < r.top + 50) scroller.scrollTop -= Math.ceil((r.top + 50 - y) / 4);
      else if (y > r.bottom - 50) scroller.scrollTop += Math.ceil((y - r.bottom + 50) / 4);
    }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return { update(nx, ny) { x = nx; y = ny; }, stop() { cancelAnimationFrame(raf); } };
}

function makeGhost(el, x, y) {
  const r = el.getBoundingClientRect();
  const ghost = el.cloneNode(true);
  ghost.classList.add("drag-ghost");
  ghost.style.width = r.width + "px";
  document.body.append(ghost);
  const offset = { x: x - r.left, y: y - r.top };
  return {
    el: ghost,
    move(nx, ny) { ghost.style.transform = `translate(${nx - offset.x}px, ${ny - offset.y}px) rotate(2.5deg)`; },
    remove() { ghost.remove(); },
  };
}

// ---------- база задач ----------
/**
 * ctx: {key, tasks, project, showProject, canCreate, extraTabs, tasksHref, noMine}
 * Возвращает блок с вкладками видов, панелью фильтров и самим видом.
 */
function renderTaskDB(ctx) {
  const cfg = dbConfig(ctx.key, ctx.defaults);
  const root = h("div", { class: "db" });
  const body = h("div", { class: "db-body" });
  const drawBody = () => {
    const visible = sortTasks(filterTasks(ctx.tasks, cfg), cfg.sort);
    body.replaceChildren(dbBody(visible, ctx, cfg, draw));
  };
  function draw() {
    saveDb(ctx.key, cfg);
    fill(root, dbBar(ctx, cfg, "tasks", draw, drawBody), filterRow(ctx, cfg, draw), body);
    drawBody();
  }
  draw();
  return root;
}

function dbBar(ctx, cfg, section, draw, drawBody) {
  const tabs = h("div", { class: "db-tabs" },
    DB_VIEWS.map((v) => h("button", {
      class: "db-tab" + (section === "tasks" && cfg.view === v.id ? " active" : ""),
      onclick: () => {
        cfg.view = v.id;
        saveDb(ctx.key, cfg);
        if (section === "tasks" && draw) draw();
        else go(ctx.tasksHref);
      },
    }, icon(v.icon), h("span", null, v.label))),
    (ctx.extraTabs || []).map((t) => h("a", { class: "db-tab" + (section === t.id ? " active" : ""), href: t.href },
      icon(t.icon), h("span", null, t.label))));
  return h("div", { class: "db-bar" }, tabs, section === "tasks" ? dbTools(ctx, cfg, draw, drawBody) : null);
}

function dbTools(ctx, cfg, draw, drawBody) {
  const input = h("input", { class: "db-search-input", placeholder: "Поиск…", value: cfg.q });
  const wrap = h("div", { class: "db-search" + (cfg.q ? " open" : "") },
    h("button", { class: "db-tool icon-only", title: "Поиск", onclick: () => { wrap.classList.add("open"); input.focus(); } }, icon("search")),
    input);
  input.addEventListener("input", () => { cfg.q = input.value; drawBody(); });
  input.addEventListener("blur", () => { if (!input.value) wrap.classList.remove("open"); });
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") { input.value = ""; cfg.q = ""; drawBody(); input.blur(); } });
  const filters = countFilters(cfg);
  const group = GROUP_BY.find((g) => g.id === cfg.group) || GROUP_BY[0];
  const sort = SORTS.find((x) => x.id === cfg.sort);
  return h("div", { class: "db-tools" },
    h("button", { class: "db-tool" + (filters ? " active" : ""), title: "Фильтр", onclick: (e) => filterMenu(e.currentTarget, ctx, cfg, draw) },
      icon("filter"), filters ? h("span", null, `Фильтр · ${filters}`) : null),
    h("button", { class: "db-tool" + (cfg.sort ? " active" : ""), title: "Сортировка", onclick: (e) => popMenu(e.currentTarget,
      SORTS.map((x) => ({ label: x.label, checked: cfg.sort === x.id, onClick: () => { cfg.sort = x.id; draw(); } })), { title: "Сортировка" }) },
    icon("sort"), cfg.sort && sort ? h("span", null, sort.label) : null),
    cfg.view === "board" ? h("button", { class: "db-tool", title: "Группировать карточки", onclick: (e) => popMenu(e.currentTarget,
      GROUP_BY.map((g) => ({ label: g.label, icon: g.icon, checked: cfg.group === g.id, onClick: () => { cfg.group = g.id; draw(); } })),
      { title: "Группировать по" }) }, icon("group"), h("span", null, group.label)) : null,
    wrap,
    ctx.canCreate ? h("button", { class: "btn primary sm new-btn", onclick: () => openTaskForm(null, ctx.project) }, "Новая") : null);
}

function filterMenu(anchor, ctx, cfg, draw) {
  popMenu(anchor, [
    ctx.noMine ? null : { label: "Только мои задачи", icon: "user", checked: cfg.mine, onClick: () => { cfg.mine = !cfg.mine; draw(); } },
    { label: "Статус", icon: "status", hint: cfg.status !== "all" ? statusFilterLabel(cfg.status) : "", onClick: () => statusFilterMenu(anchor, cfg, draw) },
    { label: "Ответственный", icon: "users", hint: cfg.member && member(Number(cfg.member)) ? member(Number(cfg.member)).name : "",
      onClick: () => memberFilterMenu(anchor, ctx, cfg, draw) },
    { label: "Блок", icon: "tag", hint: blockLabel(cfg.block), onClick: () => blockFilterMenu(anchor, ctx, cfg, draw) },
    { label: "Приоритет", icon: "flag", hint: prioLabel(cfg.prio), onClick: () => prioFilterMenu(anchor, cfg, draw) },
    countFilters(cfg) ? { divider: true } : null,
    countFilters(cfg) ? { label: "Сбросить фильтры", icon: "close", danger: true, onClick: () => { resetFilters(cfg); draw(); } } : null,
  ], { title: "Фильтр" });
}

function statusFilterLabel(v) { return (STATUS_FILTERS.find(([k]) => k === v) || ["", ""])[1]; }
function blockLabel(v) { return v === "__none" ? "Без блока" : v || ""; }
function prioLabel(v) { return v === "__none" ? "Без приоритета" : v || ""; }
function resetFilters(cfg) { Object.assign(cfg, { mine: false, status: "all", member: "", block: "", prio: "" }); }

function statusFilterMenu(anchor, cfg, draw) {
  popMenu(anchor, STATUS_FILTERS.map(([v, label]) => ({ label, checked: cfg.status === v, onClick: () => { cfg.status = v; draw(); } })),
    { title: "Статус" });
}

function memberFilterMenu(anchor, ctx, cfg, draw) {
  popMenu(anchor, [
    { label: "Все", checked: !cfg.member, onClick: () => { cfg.member = ""; draw(); } },
    ...peopleOf(ctx.tasks).map((m) => ({ label: h("span", { class: "menu-person" }, avatar(m.name, m.id, "xs"), m.name),
      checked: cfg.member === String(m.id), onClick: () => { cfg.member = String(m.id); draw(); } })),
  ], { title: "Ответственный", search: "Найти человека…" });
}

function blockFilterMenu(anchor, ctx, cfg, draw) {
  const blocks = blocksOf(ctx.tasks);
  popMenu(anchor, [
    { label: "Все", checked: !cfg.block, onClick: () => { cfg.block = ""; draw(); } },
    ...blocks.map((b) => ({ label: tag(b, colorFor(b)), checked: cfg.block === b, onClick: () => { cfg.block = b; draw(); } })),
    { label: "Без блока", checked: cfg.block === "__none", onClick: () => { cfg.block = "__none"; draw(); } },
  ], { title: "Блок", search: blocks.length > 6 ? "Найти блок…" : null });
}

function prioFilterMenu(anchor, cfg, draw) {
  popMenu(anchor, [
    { label: "Любой", checked: !cfg.prio, onClick: () => { cfg.prio = ""; draw(); } },
    ...PRIORITIES.map((p) => ({ label: tag(p, PRIORITY_COLOR[p]), checked: cfg.prio === p, onClick: () => { cfg.prio = p; draw(); } })),
    { label: "Без приоритета", checked: cfg.prio === "__none", onClick: () => { cfg.prio = "__none"; draw(); } },
  ], { title: "Приоритет" });
}

function filterRow(ctx, cfg, draw) {
  const pills = [];
  const pill = (label, clear, open) => h("span", { class: "filter-pill" },
    h("button", { class: "fp-main", onclick: (e) => open && open(e.currentTarget) }, label, open ? icon("down") : null),
    h("button", { class: "fp-x", onclick: () => { clear(); draw(); }, "aria-label": "Убрать фильтр" }, icon("close")));
  if (cfg.mine) pills.push(pill("Только мои", () => { cfg.mine = false; }));
  if (cfg.status !== "all") pills.push(pill("Статус: " + statusFilterLabel(cfg.status), () => { cfg.status = "all"; },
    (a) => statusFilterMenu(a, cfg, draw)));
  if (cfg.member) {
    const m = member(Number(cfg.member));
    pills.push(pill("Ответственный: " + (m ? m.name : "—"), () => { cfg.member = ""; }, (a) => memberFilterMenu(a, ctx, cfg, draw)));
  }
  if (cfg.block) pills.push(pill("Блок: " + blockLabel(cfg.block), () => { cfg.block = ""; }, (a) => blockFilterMenu(a, ctx, cfg, draw)));
  if (cfg.prio) pills.push(pill("Приоритет: " + prioLabel(cfg.prio), () => { cfg.prio = ""; }, (a) => prioFilterMenu(a, cfg, draw)));
  if (!pills.length) return null;
  return h("div", { class: "filter-row" }, pills,
    h("button", { class: "filter-reset", onclick: () => { resetFilters(cfg); draw(); } }, "Сбросить"));
}

function dbBody(tasks, ctx, cfg, draw) {
  if (!ctx.tasks.length) {
    return emptyState("🗂", "Задач пока нет", ctx.canCreate
      ? "Добавьте первую задачу или загрузите Excel с задачами проекта." : "Когда появятся задачи, они будут здесь.",
    ctx.canCreate ? h("button", { class: "btn primary", onclick: () => openTaskForm(null, ctx.project) }, "Новая задача") : null);
  }
  if (cfg.view === "board") return boardView(tasks, ctx, cfg);
  if (cfg.view === "calendar") return calendarView(tasks, ctx, cfg, draw);
  if (!tasks.length) return emptyState("🔍", "Ничего не найдено", "Измените фильтры или поиск.");
  if (cfg.view === "table") return tableView(tasks, ctx, cfg);
  return listView(tasks, ctx, cfg);
}

// ---------- доска ----------
function boardColumns(tasks, ctx, cfg) {
  const all = ctx.tasks;
  if (cfg.group === "priority") {
    return [...PRIORITIES.map((p) => ({ key: p, label: p, color: PRIORITY_COLOR[p], value: p, always: true })),
      { key: "", label: "Без приоритета", color: "default", value: "" }]
      .map((c) => ({ ...c, create: true, tasks: tasks.filter((t) => (t.priority || "") === c.key) }))
      .filter((c) => c.always || c.tasks.length);
  }
  if (cfg.group === "block") {
    return [...blocksOf(all).map((b) => ({ key: b, label: b, color: colorFor(b), value: b })),
      { key: "", label: "Без блока", color: "default", value: "" }]
      .map((c) => ({ ...c, create: true, tasks: tasks.filter((t) => (t.block || "") === c.key) }))
      .filter((c) => c.key || c.tasks.length);
  }
  if (cfg.group === "assignee") {
    const cols = peopleOf(all).map((m) => ({ key: String(m.id), label: m.name, color: "default", value: m.name, person: m,
      create: true, tasks: tasks.filter((t) => (t.assignee_ids || []).includes(m.id)) }));
    const nobody = tasks.filter((t) => !(t.assignee_ids || []).length);
    if (nobody.length) cols.push({ key: "", label: "Без ответственного", color: "default", value: "", create: true, tasks: nobody });
    return cols;
  }
  return STATUS_COLUMNS.map((c) => ({ ...c, tasks: tasks.filter((t) => c.statuses.includes(t.status)) }))
    .filter((c) => c.always || c.tasks.length);
}

function boardView(tasks, ctx, cfg) {
  const cols = boardColumns(tasks, ctx, cfg);
  const board = h("div", { class: "board", dataset: { keepScroll: "board-" + ctx.key } });
  for (const col of cols) board.append(boardColumn(col, ctx, cfg, board, cols));
  if (!tasks.length) board.append(h("div", { class: "board-empty" }, "По фильтрам ничего не найдено"));
  return board;
}

function canDropInto(col, cfg, ctx) {
  if (cfg.group === "status") return col.value !== null && col.value !== undefined;
  return isWsAdmin() && !ctx.readOnly;
}

function boardColumn(col, ctx, cfg, board, cols) {
  const creatable = ctx.canCreate && col.create;
  const label = col.person
    ? h("span", { class: "col-person" }, avatar(col.person.name, col.person.id, "xs"), col.person.name)
    : cfg.group === "status" ? statusPill(col.color, col.label) : tag(col.label, col.color);
  const list = h("div", { class: "board-cards" });
  const colEl = h("div", { class: `board-col c-${col.color}`, dataset: { key: col.key, drop: canDropInto(col, cfg, ctx) ? "1" : "" } },
    h("div", { class: "board-col-head" }, label, h("span", { class: "board-count" }, col.tasks.length), h("span", { class: "grow" }),
      creatable ? h("button", { class: "icon-btn sm", title: "Добавить карточку", onclick: () => colEl.querySelector(".board-add") && colEl.querySelector(".board-add").click() }, icon("plus")) : null),
    list);
  for (const t of col.tasks) list.append(boardCard(t, ctx, cfg, colEl, board, cols));
  if (creatable) colEl.append(cardComposer(col, ctx, cfg));
  return colEl;
}

function taskCard(t, ctx, cfg) {
  const labels = [];
  if (t.priority && cfg.group !== "priority") labels.push(tag(t.priority, PRIORITY_COLOR[t.priority], "sm"));
  if (t.block && cfg.group !== "block") labels.push(tag(t.block, colorFor(t.block), "sm"));
  if (t.blocked && isOpen(t)) labels.push(tag("🆘 Нужна помощь", "red", "sm"));
  const badges = [];
  if (cfg.group !== "status") badges.push(statusTag(t.status));
  badges.push(dueBadge(t));
  if (t.eta && isOpen(t)) badges.push(h("span", { class: "badge", title: "Обещано к" }, "обещано " + fmtDate(t.eta)));
  if (t.comments) badges.push(h("span", { class: "badge", title: "Комментарии" }, icon("comment"), t.comments));
  return h("div", { class: "card" + (isOpen(t) ? "" : " closed") + (t.blocked && isOpen(t) ? " blocked" : ""), dataset: { id: t.id }, tabindex: "0",
    onkeydown: (e) => { if (e.key === "Enter") openPeek(t.id); } },
  labels.length ? h("div", { class: "card-labels" }, labels) : null,
  h("div", { class: "card-title" }, t.title),
  ctx.showProject && t.project_name ? h("div", { class: "card-sub" }, icon("folder"), t.project_name) : null,
  t.contractor ? h("div", { class: "card-sub" }, icon("building"), t.contractor) : null,
  h("div", { class: "card-foot" }, h("div", { class: "card-badges" }, badges), cfg.group === "assignee" ? null : avatarStack(t)));
}

function boardCard(t, ctx, cfg, colEl, board, cols) {
  const card = taskCard(t, ctx, cfg);
  let ghost = null;
  let placeholder = null;
  let scroll = null;
  enableDrag(card, {
    canDrag: () => !ctx.readOnly && (cfg.group === "status" ? t.can_edit : isWsAdmin()),
    onClick: () => openPeek(t.id),
    onStart(x, y) {
      ghost = makeGhost(card, x, y);
      placeholder = h("div", { class: "card-placeholder", style: { height: card.offsetHeight + "px" } });
      card.after(placeholder);
      card.classList.add("drag-src");
      document.body.classList.add("is-dragging");
      scroll = autoScroll(board);
      this.onMove(x, y);
    },
    onMove(x, y) {
      ghost.move(x, y);
      scroll.update(x, y);
      const under = document.elementFromPoint(x, y);
      const target = under && under.closest(".board-col");
      board.querySelectorAll(".board-col.drop-target").forEach((c) => c.classList.remove("drop-target"));
      if (!target || (target.dataset.drop !== "1" && target !== colEl)) return;
      if (target !== colEl) target.classList.add("drop-target");
      const list = target.querySelector(".board-cards");
      const before = [...list.querySelectorAll(".card:not(.drag-src)")]
        .find((c) => { const r = c.getBoundingClientRect(); return y < r.top + r.height / 2; });
      if (before) list.insertBefore(placeholder, before);
      else list.append(placeholder);
    },
    async onDrop(x, y, cancelled) {
      scroll.stop();
      ghost.remove();
      document.body.classList.remove("is-dragging");
      board.querySelectorAll(".board-col.drop-target").forEach((c) => c.classList.remove("drop-target"));
      const targetEl = placeholder.closest(".board-col");
      card.classList.remove("drag-src");
      if (cancelled || !targetEl || targetEl === colEl) { placeholder.remove(); return; }
      placeholder.replaceWith(card);
      card.classList.add("just-moved");
      const col = cols.find((c) => c.key === targetEl.dataset.key);
      try {
        await moveTask(t, col, cfg.group);
      } catch (_) {
        await reloadView();
      }
    },
  });
  return card;
}

async function moveTask(t, col, group) {
  if (group === "status") {
    await run(() => api(`/api/tasks/${t.id}/status`, { json: { status: col.value } }), STATUS_TOAST[col.value]);
    if (t.status === "overdue" && ["todo", "progress"].includes(col.value)) {
      setTimeout(() => toast("Срок уже прошёл — задача останется в «Просрочена», пока её не выполнят или не перенесут срок"), 1600);
    }
  } else {
    const key = { priority: "priority", block: "block", assignee: "responsible" }[group];
    await run(() => api(`/api/tasks/${t.id}`, { method: "PATCH", json: { [key]: col.value } }), "Карточка перенесена");
  }
  await taskChanged();
}

function groupFields(group, col) {
  if (group === "status") return { status: col.value };
  if (group === "priority") return { priority: col.value };
  if (group === "block") return { block: col.value };
  if (group === "assignee") return { responsible: col.value };
  return {};
}

/** «+ Добавить карточку» как в Trello: поле прямо в колонке, после добавления остаётся открытым. */
function cardComposer(col, ctx, cfg) {
  const slot = h("div", { class: "board-add-wrap" });
  const reopenKey = `${ctx.key}:${cfg.group}:${col.key}`;
  const showButton = () => slot.replaceChildren(h("button", { class: "board-add", onclick: open }, icon("plus"), "Добавить карточку"));
  function open() {
    const ta = h("textarea", { class: "composer-input", rows: 2, placeholder: "Название карточки…" });
    const submit = async () => {
      const title = ta.value.trim();
      if (!title) { ta.focus(); return; }
      ta.disabled = true;
      try {
        await run(() => api(`/api/projects/${ctx.project.id}/tasks`, { json: { title, ...groupFields(cfg.group, col) } }), "Карточка добавлена");
        S.reopenComposer = reopenKey;
        await taskChanged();
      } catch (_) {
        ta.disabled = false;
        ta.focus();
      }
    };
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
      if (e.key === "Escape") showButton();
    });
    ta.addEventListener("blur", () => setTimeout(() => { if (!ta.value.trim() && !slot.contains(document.activeElement)) showButton(); }, 150));
    slot.replaceChildren(h("div", { class: "composer" }, h("div", { class: "card composer-card" }, ta),
      h("div", { class: "composer-actions" },
        h("button", { class: "btn primary sm", onmousedown: (e) => e.preventDefault(), onclick: submit }, "Добавить карточку"),
        h("button", { class: "icon-btn", onclick: showButton, "aria-label": "Отмена" }, icon("close")))));
    setTimeout(() => ta.focus(), 0);
  }
  if (S.reopenComposer === reopenKey) { S.reopenComposer = null; open(); } else showButton();
  return slot;
}

// ---------- таблица ----------
function tableColumns(ctx) {
  return [
    { id: "title", label: "Название", icon: "text", w: 300 },
    { id: "status", label: "Статус", icon: "status", w: 150 },
    { id: "assignee", label: "Ответственный", icon: "user", w: 190 },
    { id: "deadline", label: "Срок", icon: "calendar", w: 130 },
    { id: "priority", label: "Приоритет", icon: "flag", w: 120 },
    { id: "block", label: "Блок", icon: "tag", w: 170 },
    ctx.showProject ? { id: "project", label: "Проект", icon: "folder", w: 170 } : null,
    { id: "contractor", label: "Подрядчик", icon: "building", w: 170 },
    { id: "start_date", label: "Начало", icon: "calendar", w: 120 },
  ].filter(Boolean);
}

function tableView(tasks, ctx) {
  const cols = tableColumns(ctx);
  const admin = isWsAdmin() && !ctx.readOnly;
  const cell = (t, c) => {
    const td = h("td", { class: "cell cell-" + c.id });
    let value = null;
    let edit = null;
    switch (c.id) {
      case "title":
        td.append(h("div", { class: "cell-title" }, h("span", { class: "row-ico" }, statusDot(t.status)),
          h("span", { class: "title-text" }, t.title),
          t.comments ? h("span", { class: "cell-comments" }, icon("comment"), t.comments) : null,
          h("button", { class: "open-btn", onclick: (e) => { e.stopPropagation(); openPeek(t.id); } }, icon("expand"), "Открыть")));
        td.addEventListener("click", () => openPeek(t.id));
        return td;
      case "status": value = statusTag(t.status); if (t.can_edit && !ctx.readOnly) edit = (a) => editStatus(a, t); break;
      case "assignee": value = personChips(t); if (admin) edit = (a) => editAssignee(a, t); break;
      case "deadline": value = t.deadline ? h("span", { class: t.status === "overdue" ? "text-red" : "" }, fmtDate(t.deadline)) : null;
        if (admin) edit = (a) => editDate(a, t, "deadline"); break;
      case "start_date": value = t.start_date ? fmtDate(t.start_date) : null; if (admin) edit = (a) => editDate(a, t, "start_date"); break;
      case "priority": value = priorityTag(t.priority); if (admin) edit = (a) => editPriority(a, t); break;
      case "block": value = t.block ? tag(t.block, colorFor(t.block)) : null; if (admin) edit = (a) => editBlock(a, t, ctx.tasks); break;
      case "project": value = t.project_name; break;
      case "contractor": value = t.contractor; if (admin) edit = () => editText(t, "contractor", "Подрядчик"); break;
      default: break;
    }
    td.append(h("div", { class: "cell-inner" }, value));
    if (edit) {
      td.classList.add("editable");
      td.addEventListener("click", () => edit(td));
    } else {
      td.addEventListener("click", () => openPeek(t.id));
    }
    return td;
  };
  const table = h("table", { class: "db-table" },
    h("colgroup", null, cols.map((c) => h("col", { style: { width: c.w + "px" } }))),
    h("thead", null, h("tr", null, cols.map((c) => h("th", null, h("div", { class: "th" }, icon(c.icon), h("span", null, c.label)))))),
    h("tbody", null, tasks.map((t) => h("tr", { class: isOpen(t) ? "" : "closed", dataset: { id: t.id } }, cols.map((c) => cell(t, c))))));
  const newRow = ctx.canCreate ? inlineNewRow(ctx) : null;
  return h("div", { class: "table-wrap", dataset: { keepScroll: "table-" + ctx.key } },
    table, newRow,
    h("div", { class: "table-foot" }, h("span", { class: "calc" }, h("span", { class: "calc-label" }, "Количество"), tasks.length)));
}

function inlineNewRow(ctx) {
  const slot = h("div", { class: "new-row" });
  const button = () => slot.replaceChildren(h("button", { class: "new-row-btn", onclick: open }, icon("plus"), "Новая задача"));
  function open() {
    const input = h("input", { class: "new-row-input", placeholder: "Название задачи и Enter" });
    input.addEventListener("keydown", async (e) => {
      if (e.key === "Escape") button();
      if (e.key !== "Enter" || !input.value.trim()) return;
      input.disabled = true;
      try {
        await run(() => api(`/api/projects/${ctx.project.id}/tasks`, { json: { title: input.value.trim() } }), "Задача добавлена");
        await taskChanged();
      } catch (_) { input.disabled = false; }
    });
    input.addEventListener("blur", () => { if (!input.value.trim()) button(); });
    slot.replaceChildren(h("div", { class: "new-row-edit" }, icon("plus"), input));
    input.focus();
  }
  button();
  return slot;
}

// ---------- список ----------
function listView(tasks, ctx) {
  const groups = {};
  for (const t of tasks) (groups[t.bucket] = groups[t.bucket] || []).push(t);
  const out = h("div", { class: "list-view" });
  for (const b of BUCKETS) {
    const items = groups[b];
    if (!items || !items.length) continue;
    const color = { overdue: "red", today: "orange", tomorrow: "yellow", done: "green", cancelled: "default" }[b] || "gray";
    const body = h("div", { class: "list-rows" }, items.map((t) => listRow(t, ctx)));
    out.append(toggleBlock(h("span", { class: "group-head" }, tag(BUCKET_TITLE[b], color), h("span", { class: "group-count" }, items.length)),
      body, !["done", "cancelled"].includes(b), `list:${ctx.key}:${b}`));
  }
  return out;
}

function listRow(t, ctx) {
  const done = !isOpen(t);
  const canToggle = t.can_edit && !ctx.readOnly && t.status !== "cancelled";
  return h("div", { class: "list-row" + (done ? " closed" : ""), onclick: () => openPeek(t.id) },
    h("button", {
      class: "check-circle" + (done ? " on" : ""), disabled: !canToggle, title: done ? "Вернуть в работу" : "Отметить выполненной",
      onclick: (e) => { e.stopPropagation(); if (canToggle) setStatus(t, done ? "progress" : "done"); },
    }, done ? icon("tick") : null),
    h("div", { class: "list-main" },
      h("div", { class: "list-title" }, t.title),
      h("div", { class: "list-meta" },
        ctx.showProject && t.project_name ? h("span", null, t.project_name) : null,
        t.block ? h("span", null, t.block) : null,
        t.contractor ? h("span", null, t.contractor) : null)),
    h("div", { class: "list-props" },
      t.blocked && isOpen(t) ? tag("Нужна помощь", "red", "sm") : null,
      t.raw_status === "progress" && isOpen(t) ? statusTag("progress") : null,
      t.priority ? tag(t.priority, PRIORITY_COLOR[t.priority], "sm") : null,
      t.comments ? h("span", { class: "badge" }, icon("comment"), t.comments) : null,
      dueBadge(t),
      avatarStack(t, 2)));
}

// ---------- календарь ----------
function calendarView(tasks, ctx, cfg, draw) {
  const base = cfg.month ? parseISO(cfg.month + "-01") : new Date(today().getFullYear(), today().getMonth(), 1);
  const month = new Date(base.getFullYear(), base.getMonth(), 1);
  const shift = (n) => { const d = new Date(month); d.setMonth(d.getMonth() + n); cfg.month = localISO(d).slice(0, 7); draw(); };
  const byDate = {};
  for (const t of tasks) if (t.deadline) (byDate[t.deadline] = byDate[t.deadline] || []).push(t);
  const start = new Date(month);
  start.setDate(1 - ((month.getDay() + 6) % 7));
  const last = new Date(month.getFullYear(), month.getMonth() + 1, 0);
  const weeks = Math.ceil((((month.getDay() + 6) % 7) + last.getDate()) / 7);
  const admin = isWsAdmin() && !ctx.readOnly;
  const grid = h("div", { class: "cal-grid" }, WEEK_HEAD.map((d) => h("div", { class: "cal-wd" }, d)));
  for (let i = 0; i < weeks * 7; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const iso = localISO(d);
    const items = byDate[iso] || [];
    const cell = h("div", { class: "cal-day" + (d.getMonth() !== month.getMonth() ? " other" : "") + (d.getDay() === 0 || d.getDay() === 6 ? " weekend" : ""), dataset: { date: iso } },
      h("div", { class: "cal-day-head" },
        ctx.canCreate ? h("button", { class: "cal-add", title: "Задача на этот день", onclick: () => openTaskForm({ deadline: iso }, ctx.project) }, icon("plus")) : null,
        h("span", { class: "cal-num" + (iso === S.boot.today ? " today" : "") }, d.getDate() === 1 ? `${d.getDate()} ${MONTHS[d.getMonth()]}` : d.getDate())));
    const shown = items.slice(0, 3);
    for (const t of shown) cell.append(calChip(t, admin, grid));
    if (items.length > shown.length) {
      cell.append(h("button", { class: "cal-more", onclick: (e) => popMenu(e.currentTarget, items.map((t) => ({
        label: t.title, icon: statusDot(t.status), onClick: () => openPeek(t.id) })), { title: fmtDate(iso, true) }) }, `ещё ${items.length - shown.length}`));
    }
    grid.append(cell);
  }
  const nodate = tasks.filter((t) => !t.deadline && isOpen(t));
  return h("div", { class: "calendar" },
    h("div", { class: "cal-head" }, h("div", { class: "cal-title" }, `${MONTHS_FULL[month.getMonth()]} ${month.getFullYear()}`),
      h("span", { class: "grow" }),
      h("button", { class: "icon-btn", onclick: () => shift(-1), "aria-label": "Предыдущий месяц" }, icon("left")),
      h("button", { class: "btn ghost sm", onclick: () => { cfg.month = ""; draw(); } }, "Сегодня"),
      h("button", { class: "icon-btn", onclick: () => shift(1), "aria-label": "Следующий месяц" }, icon("chevron"))),
    h("div", { class: "cal-scroll", dataset: { keepScroll: "cal-" + ctx.key } }, grid),
    nodate.length ? toggleBlock(h("span", { class: "group-head" }, "Без срока", h("span", { class: "group-count" }, nodate.length)),
      taskLines(nodate, { owner: true, project: ctx.showProject }), false, `cal-nodate:${ctx.key}`) : null);
}

function calChip(t, admin, grid) {
  const chip = h("div", { class: "cal-chip" + (isOpen(t) ? "" : " closed") + (t.status === "overdue" ? " overdue" : ""), title: t.title },
    statusDot(t.status), h("span", { class: "cal-chip-text" }, t.title));
  let ghost = null;
  let scroll = null;
  let over = null;
  enableDrag(chip, {
    canDrag: () => admin,
    onClick: () => openPeek(t.id),
    onStart(x, y) {
      ghost = makeGhost(chip, x, y);
      chip.classList.add("drag-faded");
      document.body.classList.add("is-dragging");
      scroll = autoScroll(document.querySelector(".cal-scroll"));
      this.onMove(x, y);
    },
    onMove(x, y) {
      ghost.move(x, y);
      scroll.update(x, y);
      const under = document.elementFromPoint(x, y);
      const cell = under && under.closest(".cal-day");
      if (over && over !== cell) over.classList.remove("drop-target");
      over = cell;
      if (cell) cell.classList.add("drop-target");
    },
    async onDrop(x, y, cancelled) {
      scroll.stop();
      ghost.remove();
      document.body.classList.remove("is-dragging");
      chip.classList.remove("drag-faded");
      if (over) over.classList.remove("drop-target");
      if (cancelled || !over || over.dataset.date === t.deadline) return;
      over.append(chip);
      await patchTask(t, { deadline: over.dataset.date }, "Срок перенесён на " + fmtDate(over.dataset.date));
    },
  });
  return chip;
}

// ---------- карточка задачи (боковая панель) ----------
async function openPeek(id, keep) {
  S.peek = id;
  let t;
  try {
    t = await api(`/api/tasks/${id}`);
  } catch (err) {
    if (!keep) toast(err.message, "error");
    if (S.peek === id) closePeek();
    return;
  }
  if (S.peek !== id) return;
  let root = document.getElementById("peek");
  const scrollTop = root && keep ? root.querySelector(".peek-scroll").scrollTop : 0;
  if (!root) {
    root = h("aside", { id: "peek", class: "peek", role: "dialog", "aria-label": "Задача" });
    document.body.append(root, h("div", { id: "peek-scrim", class: "peek-scrim", onclick: closePeek }));
  }
  root.replaceChildren(peekContent(t));
  root.querySelector(".peek-scroll").scrollTop = scrollTop;
  document.body.classList.add("peek-open");
  markActiveTask();
  syncBackButton();
}

function closePeek() {
  S.peek = null;
  const root = document.getElementById("peek");
  if (root) root.remove();
  const scrim = document.getElementById("peek-scrim");
  if (scrim) scrim.remove();
  document.body.classList.remove("peek-open");
  markActiveTask();
  syncBackButton();
}

function markActiveTask() {
  document.querySelectorAll(".peek-active").forEach((el) => el.classList.remove("peek-active"));
  if (S.peek) document.querySelectorAll(`[data-id="${S.peek}"]`).forEach((el) => el.classList.add("peek-active"));
}

function prop(iconName, label, value, onEdit) {
  return h("div", { class: "prop" },
    h("div", { class: "prop-name" }, icon(iconName), h("span", null, label)),
    h("div", { class: "prop-value" + (onEdit ? " editable" : ""), onclick: onEdit ? (e) => onEdit(e.currentTarget) : null },
      value || h("span", { class: "prop-empty" }, "Пусто")));
}

function peekContent(t) {
  const admin = isWsAdmin();
  const owners = people(t);
  const title = h("h1", { class: "peek-title" });
  if (admin) editableText(title, t.title, (v) => patchTask(t, { title: v }, "Переименовано"));
  else title.textContent = t.title;

  const props = h("div", { class: "props" },
    prop("status", "Статус", statusTag(t.status), t.can_edit ? (a) => editStatus(a, t) : null),
    prop("user", "Ответственный", owners.length
      ? h("span", { class: "person-chips" }, owners.map((m) => h("span", { class: "person" }, avatar(m.name, m.id, "xs"), m.name,
        m.username ? h("a", { class: "person-link", href: "https://t.me/" + m.username, target: "_blank", rel: "noopener", onclick: (e) => e.stopPropagation() }, "@" + m.username) : null)))
      : t.responsible || null, admin ? (a) => editAssignee(a, t) : null),
    prop("calendar", "Срок", t.deadline ? h("span", null, fmtDate(t.deadline, true),
      isOpen(t) ? h("span", { class: "prop-note" + (t.days_left < 0 ? " text-red" : "") }, " · " + daysText(t.days_left)) : null) : null,
    admin ? (a) => editDate(a, t, "deadline") : null),
    prop("flag", "Приоритет", priorityTag(t.priority), admin ? (a) => editPriority(a, t) : null),
    prop("tag", "Блок", t.block ? tag(t.block, colorFor(t.block)) : null,
      admin ? (a) => editBlock(a, t, S.project && S.project.pid === t.project_id ? S.project.data.tasks : [t]) : null),
    prop("building", "Подрядчик", t.contractor || null, admin ? () => editText(t, "contractor", "Подрядчик") : null),
    prop("calendar", "Начало", t.start_date ? fmtDate(t.start_date, true) : null, admin ? (a) => editDate(a, t, "start_date") : null),
    t.eta && isOpen(t) ? prop("clock", "Обещано к", fmtDate(t.eta, true)) : null,
    t.fact_date ? prop("done", "Выполнено", fmtDate(t.fact_date, true)) : null,
    t.proof ? prop("link", "Результат", linkify(t.proof)) : null,
    prop("folder", "Проект", h("a", { href: projectHref(t.project_id), onclick: () => { if (window.innerWidth < 900) closePeek(); } }, t.project_name)));

  const actions = [];
  if (t.can_edit) {
    if (isOpen(t)) {
      if (t.raw_status !== "progress") actions.push(h("button", { class: "btn sm", onclick: () => setStatus(t, "progress") }, icon("play"), "В работу"));
      actions.push(h("button", { class: "btn sm primary", onclick: () => setStatus(t, "done") }, icon("tick"), "Выполнено"));
      if (!t.blocked) actions.push(h("button", { class: "btn sm", onclick: () => problemDialog(t) }, icon("help"), "Нужна помощь"));
    } else {
      actions.push(h("button", { class: "btn sm", onclick: () => setStatus(t, "progress") }, icon("undo"), "Вернуть в работу"));
    }
  }
  if (canWrite() && (t.can_edit || admin)) {
    actions.push(h("button", { class: "btn sm", onclick: () => openCallForm({ task_id: t.id, contact: t.contractor || "", project_id: t.project_id }) },
      icon("phone"), "Звонок"));
  }

  const comments = t.events.filter((e) => e.kind === "comment" || e.kind === "problem").slice().reverse();
  const history = t.events;
  const composer = t.can_edit ? commentComposer(t) : null;

  return h("div", { class: "peek-inner" },
    h("div", { class: "peek-top" },
      h("button", { class: "icon-btn", onclick: closePeek, title: "Закрыть (Esc)", "aria-label": "Закрыть" }, icon("dright")),
      h("a", { class: "peek-crumb", href: projectHref(t.project_id) }, icon("folder"), h("span", null, t.project_name)),
      h("span", { class: "grow" }),
      h("button", { class: "icon-btn", title: "Скопировать ссылку", onclick: () => copyText(taskLink(t)) }, icon("link")),
      h("button", { class: "icon-btn", title: "Ещё", onclick: (e) => taskMenu(e.currentTarget, t) }, icon("more"))),
    h("div", { class: "peek-scroll" },
      h("div", { class: "peek-body" },
        title,
        props,
        t.blocked && isOpen(t) ? callout("🆘", h("div", null, h("b", null, "Нужна помощь"), t.blocked_reason ? h("div", null, t.blocked_reason) : null), "red",
          t.can_edit ? h("button", { class: "btn sm", onclick: async () => {
            await run(() => api(`/api/tasks/${t.id}/resolve`, { json: {} }), "Блокер снят");
            await taskChanged();
          } }, icon("tick"), "Проблема решена") : null) : null,
        actions.length ? h("div", { class: "peek-actions" }, actions) : null,
        h("div", { class: "peek-sep" }),
        descriptionBlock(t, admin),
        t.sheet_comment ? callout("📝", h("div", null, h("div", { class: "muted small" }, "Комментарий из таблицы"), t.sheet_comment), "gray") : null,
        t.calls.length ? h("div", { class: "peek-section" }, sectionTitle("Звонки", t.calls.length),
          t.calls.map((c) => h("div", { class: "mini-row" }, icon("phone"), h("span", null, c.contact), h("span", { class: "muted" }, fmtDateTime(c.due_at)),
            h("span", { class: "grow" }), tag({ planned: "запланирован", done: "состоялся", cancelled: "отменён" }[c.status] || c.status,
              { planned: "blue", done: "green" }[c.status] || "default", "sm")))) : null,
        h("div", { class: "peek-section" }, sectionTitle("Комментарии", comments.length || null),
          composer,
          comments.map((e) => commentItem(e))),
        history.length ? toggleBlock(h("span", null, "История изменений ", h("span", { class: "muted" }, history.length)),
          h("div", { class: "history" }, history.map((e) => h("div", { class: "history-item" },
            h("span", { class: "history-ico" }, EVENT_ICONS[e.kind] || "•"),
            h("div", null, h("span", null, e.member_name ? h("b", null, e.member_name + " ") : null, e.text),
              h("div", { class: "muted small" }, relTime(e.created_at)))))), false, "peek-history") : null)));
}

function linkify(text) {
  const m = String(text).match(/https?:\/\/\S+/);
  if (!m) return text;
  return h("a", { href: m[0], target: "_blank", rel: "noopener" }, text);
}

function taskLink(t) {
  const ws = t.workspace_id || S.wid;
  return `${location.origin}${location.pathname}#/w/${ws}/p/${t.project_id}?task=${t.id}`;
}

function descriptionBlock(t, admin) {
  if (!admin) {
    return t.description ? h("div", { class: "peek-desc" }, t.description) : null;
  }
  const ta = h("textarea", { class: "peek-desc-input", placeholder: "Добавьте описание, критерий готовности…", rows: 1 });
  ta.value = t.description || "";
  const fit = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; };
  ta.addEventListener("input", fit);
  ta.addEventListener("blur", async () => {
    if (ta.value.trim() === (t.description || "").trim()) return;
    await patchTask(t, { description: ta.value }, "Описание сохранено");
  });
  requestAnimationFrame(fit);
  return ta;
}

function commentItem(e) {
  return h("div", { class: "comment" + (e.kind === "problem" ? " problem" : "") },
    avatar(e.member_name || "?", e.member_id || 0, "sm"),
    h("div", { class: "comment-body" },
      h("div", { class: "comment-head" }, h("b", null, e.member_name || "Без имени"), h("span", { class: "muted small" }, relTime(e.created_at)),
        e.kind === "problem" ? tag("нужна помощь", "red", "sm") : null),
      h("div", { class: "comment-text" }, e.text)));
}

function commentComposer(t) {
  const ta = h("textarea", { class: "comment-input", rows: 1, placeholder: "Добавить комментарий…" });
  const send = h("button", { class: "send-btn", disabled: true, "aria-label": "Отправить" }, icon("send"));
  const fit = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; send.disabled = !ta.value.trim(); };
  const submit = async () => {
    const text = ta.value.trim();
    if (!text) return;
    send.disabled = true;
    try {
      await run(() => api(`/api/tasks/${t.id}/comment`, { json: { text } }), "Комментарий добавлен");
      await taskChanged();
    } catch (_) { send.disabled = false; }
  };
  ta.addEventListener("input", fit);
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
  send.addEventListener("click", submit);
  return h("div", { class: "comment-composer" }, avatar(S.boot.me.name, myMemberId(), "sm"), ta, send);
}

function problemDialog(t) {
  const ta = h("textarea", { class: "input", rows: 4, placeholder: "Что мешает? Какая помощь нужна?" });
  const close = modal("🆘 Нужна помощь", h("div", null,
    h("p", { class: "muted" }, "Руководитель получит сообщение в Telegram, а задача отметится красным."),
    field("Описание проблемы", ta),
    h("div", { class: "btn-row end" },
      h("button", { class: "btn ghost", onclick: () => close() }, "Отмена"),
      h("button", { class: "btn danger", onclick: async () => {
        if (!ta.value.trim()) { toast("Опишите проблему", "error"); return; }
        await run(() => api(`/api/tasks/${t.id}/problem`, { json: { text: ta.value } }), "Передано руководителю 🆘");
        close();
        await taskChanged();
      } }, "Отправить"))));
}

function taskMenu(anchor, t) {
  const admin = isWsAdmin();
  popMenu(anchor, [
    { label: "Скопировать ссылку", icon: "link", onClick: () => copyText(taskLink(t)) },
    admin ? { label: "Редактировать все поля", icon: "edit", onClick: () => openTaskForm(t) } : null,
    t.can_edit ? { label: "Комментарий", icon: "comment", onClick: () => { const el = document.querySelector(".comment-input"); if (el) el.focus(); } } : null,
    canWrite() ? { label: "Запланировать звонок", icon: "phone", onClick: () => openCallForm({ task_id: t.id, contact: t.contractor || "", project_id: t.project_id }) } : null,
    admin && isOpen(t) ? { label: "Отменить задачу", icon: "close", onClick: () => setStatus(t, "cancelled") } : null,
    admin ? { divider: true } : null,
    admin ? { label: "Удалить", icon: "trash", danger: true, onClick: async () => {
      if (!(await confirmDialog(`Удалить задачу «${t.title}»? В исходной таблице она останется.`, "Удалить"))) return;
      await run(() => api(`/api/tasks/${t.id}`, { method: "DELETE" }), "Задача удалена");
      closePeek();
      await reloadView();
    } } : null,
  ]);
}

// ---------- формы ----------
function openTaskForm(t, project) {
  const isNew = !t || !t.id;
  const preset = t || {};
  const pid = isNew ? (project ? project.id : null) : t.project_id;
  if (!pid) return;
  const tasks = S.project && S.project.pid === pid ? S.project.data.tasks : [];
  const people = S.boot.members.filter((m) => m.active);
  const listId = "dl-" + uid();
  const f = {
    title: h("input", { class: "input title-input", value: preset.title || "", placeholder: "Без названия" }),
    responsible: h("input", { class: "input", value: preset.responsible || "", list: listId + "p", placeholder: "Имя из команды" }),
    start_date: h("input", { class: "input", type: "date", value: preset.start_date || "" }),
    deadline: h("input", { class: "input", type: "date", value: preset.deadline || "" }),
    priority: h("select", { class: "input" }, h("option", { value: "" }, "—"),
      PRIORITIES.map((p) => h("option", { value: p, selected: preset.priority === p }, p))),
    status: h("select", { class: "input" }, STATUS_OPTIONS.map((s) => h("option", {
      value: s, selected: isNew ? s === (preset.status || "todo") : preset.raw_status === s }, STATUS[s].label))),
    block: h("input", { class: "input", value: preset.block || "", list: listId + "b", placeholder: "Например, ДИЗАЙН" }),
    contractor: h("input", { class: "input", value: preset.contractor || "", placeholder: "Подрядчик или поставщик" }),
    description: h("textarea", { class: "input", rows: 4, placeholder: "Подробности, критерий готовности" }),
  };
  f.description.value = preset.description || "";
  const row = (iconName, label, input) => h("label", { class: "form-prop" }, h("span", { class: "prop-name" }, icon(iconName), h("span", null, label)), input);
  const save = h("button", { class: "btn primary" }, isNew ? "Создать" : "Сохранить");
  const close = modal(isNew ? "Новая задача" : "Редактирование задачи", h("div", { class: "task-form" },
    h("datalist", { id: listId + "p" }, people.map((m) => h("option", { value: m.name }, m.role))),
    h("datalist", { id: listId + "b" }, blocksOf(tasks).map((b) => h("option", { value: b }))),
    f.title,
    h("div", { class: "form-props" },
      row("user", "Ответственный", f.responsible),
      row("calendar", "Срок", f.deadline),
      row("calendar", "Начало", f.start_date),
      row("status", "Статус", f.status),
      row("flag", "Приоритет", f.priority),
      row("tag", "Блок", f.block),
      row("building", "Подрядчик", f.contractor)),
    f.description,
    isNew ? h("p", { class: "muted small" }, "Ответственный получит уведомление в Telegram.") : null,
    h("div", { class: "btn-row end" }, h("button", { class: "btn ghost", onclick: () => close() }, "Отмена"), save)), { wide: true });
  save.addEventListener("click", async () => {
    const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
    save.disabled = true;
    try {
      const result = await run(() => isNew
        ? api(`/api/projects/${pid}/tasks`, { json: body })
        : api(`/api/tasks/${t.id}`, { method: "PATCH", json: body }), isNew ? "Задача создана" : "Сохранено");
      close();
      await reloadView();
      openPeek(isNew ? result.data.id : t.id);
    } catch (_) { save.disabled = false; }
  });
  f.title.addEventListener("keydown", (e) => { if (e.key === "Enter") save.click(); });
}

function openCallForm(preset = {}) {
  const pid = preset.project_id || (S.project && S.project.pid);
  if (!pid) return;
  const now = new Date();
  now.setMinutes(0, 0, 0);
  now.setHours(now.getHours() + 1);
  const pad = (n) => String(n).padStart(2, "0");
  const tasks = S.project && S.project.pid === pid ? S.project.data.tasks.filter(isOpen) : [];
  const admin = isWsAdmin();
  const mine = myMemberId();
  const f = {
    contact: h("input", { class: "input", value: preset.contact || "", placeholder: "Имя или компания" }),
    phone: h("input", { class: "input", type: "tel", placeholder: "+998 …" }),
    due_at: h("input", { class: "input", type: "datetime-local", value: `${localISO(now)}T${pad(now.getHours())}:00` }),
    member_id: h("select", { class: "input", disabled: !admin },
      S.boot.members.filter((m) => m.active).map((m) => h("option", { value: m.id, selected: m.id === mine }, m.name))),
    task_id: h("select", { class: "input" }, h("option", { value: "" }, "— без задачи —"),
      tasks.map((t) => h("option", { value: t.id, selected: preset.task_id === t.id }, t.title))),
    note: h("textarea", { class: "input", rows: 3, placeholder: "О чём договориться" }),
  };
  if (preset.task_id && !tasks.find((t) => t.id === preset.task_id)) {
    f.task_id.append(h("option", { value: preset.task_id, selected: true }, "Текущая задача"));
  }
  const close = modal("📞 Напоминание о звонке", h("div", null,
    field("Кому звонить", f.contact),
    h("div", { class: "grid2" }, field("Телефон", f.phone), field("Когда", f.due_at)),
    field("Кто звонит", f.member_id, admin ? null : "Звонок назначается на вас"),
    field("По задаче", f.task_id),
    field("Заметка", f.note),
    h("div", { class: "btn-row end" },
      h("button", { class: "btn ghost", onclick: () => close() }, "Отмена"),
      h("button", { class: "btn primary", onclick: async () => {
        const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
        if (!admin) delete body.member_id;
        await run(() => api(`/api/projects/${pid}/calls`, { json: body }), "Бот напомнит в назначенное время 📞");
        close();
        await taskChanged();
      } }, "Запланировать"))));
}

// ---------- страница проекта ----------
function projectTabsFor(pid) {
  return [
    { id: "overview", label: "Обзор", icon: "chart", href: projectHref(pid, "overview") },
    { id: "calls", label: "Обзвон", icon: "phone", href: projectHref(pid, "calls") },
    isWsAdmin() ? { id: "settings", label: "Управление", icon: "gear", href: projectHref(pid, "settings") } : null,
  ].filter(Boolean);
}

function eventInfo(p) {
  if (!p.event_date) return null;
  const days = Math.round((parseISO(p.event_date) - today()) / 86400000);
  if (days > 0) return { days, text: `через ${days} ${plural(days, "день", "дня", "дней")}`, warn: days <= 3 };
  if (days === 0) return { days, text: "сегодня 🎉", warn: true };
  return { days, text: "прошло", warn: false };
}

function pickEventDate(anchor, p) {
  datePicker(anchor, p.event_date, async (iso) => {
    await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { event_date: iso } }), iso ? "Дата сохранена" : "Дата убрана");
    await refreshWorkspace();
    await reloadView();
  });
}

function projectHeader(d) {
  const p = d.project;
  const admin = isWsAdmin();
  const title = h("h1", { class: "page-title" });
  if (admin) {
    editableText(title, p.name, async (v) => {
      await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { name: v } }), "Переименовано");
      await refreshWorkspace();
      await reloadView();
    });
  } else title.textContent = p.name;
  const ev = eventInfo(p);
  const chips = h("div", { class: "head-props" },
    h("button", { class: "head-prop" + (ev && ev.warn ? " warn" : ""), disabled: !admin, onclick: (e) => pickEventDate(e.currentTarget, p) },
      icon("calendar"), p.event_date ? h("span", null, "Мероприятие ", h("b", null, fmtDate(p.event_date, true)), ev ? " · " + ev.text : "")
        : h("span", { class: "muted" }, admin ? "Указать дату мероприятия" : "Дата мероприятия не указана")),
    h("span", { class: "head-prop static" }, h("span", { class: "mini-progress" }, h("i", { style: { width: d.stats.pct + "%" } })),
      h("span", null, `${d.stats.pct}% · ${d.stats.done} из ${d.stats.total}`)),
    p.source_type === "gsheet"
      ? h("a", { class: "head-prop", href: p.source_url, target: "_blank", rel: "noopener" }, icon("link"),
        h("span", null, "Google Таблица" + (p.last_sync_at ? " · " + relTime(p.last_sync_at) : "")),
        p.last_sync_error ? tag("ошибка", "red", "sm") : null)
      : h("span", { class: "head-prop static" }, icon("page"), h("span", null, p.source_type === "upload" ? "Из Excel" : "Создан вручную")));
  return h("div", { class: "page-head" }, h("div", { class: "page-icon" }, "📁"), title, chips);
}

async function viewProject(params) {
  const pid = Number(params.pid);
  const d = await ensureProject(pid, params.force);
  if (d.project.workspace_id !== S.wid) {
    go(`#/w/${d.project.workspace_id}/p/${pid}` + (params.section ? "/" + params.section : ""));
    return null;
  }
  const section = params.section || "tasks";
  if (section === "settings" && !isWsAdmin()) { go(projectHref(pid)); return null; }
  const ctx = {
    key: "p:" + pid, tasks: d.tasks, project: d.project, canCreate: isWsAdmin(), readOnly: d.role === "viewer",
    extraTabs: projectTabsFor(pid), tasksHref: projectHref(pid), noMine: d.role === "viewer" && !S.boot.me.member,
  };
  let body;
  if (section === "tasks") body = renderTaskDB(ctx);
  else {
    const cfg = dbConfig(ctx.key);
    const content = section === "overview" ? projectOverview(d)
      : section === "calls" ? await projectCalls(d) : projectSettings(d);
    body = h("div", { class: "db" }, dbBar(ctx, cfg, section), h("div", { class: "section-body" }, content));
  }
  const sectionLabel = { tasks: "Задачи", overview: "Обзор", calls: "Обзвон", settings: "Управление" }[section];
  return {
    title: d.project.name,
    crumbs: [{ icon: "📁", label: d.project.name, href: projectHref(pid) }, section !== "tasks" ? { label: sectionLabel } : null],
    body: h("div", { class: "page full" }, projectHeader(d), body),
    full: true,
  };
}

function openFiltered(pid, filters) {
  const key = "p:" + pid;
  const cfg = dbConfig(key);
  Object.assign(cfg, { mine: false, status: "all", member: "", block: "", prio: "", sort: "" }, filters);
  if (cfg.view === "board" || cfg.view === "calendar") cfg.view = "table";
  saveDb(key, cfg);
  go(projectHref(pid));
}

// ---------- обзор ----------
function statCard(label, value, sub, opts = {}) {
  return h(opts.onclick ? "button" : "div", { class: "stat" + (opts.color ? " c-" + opts.color : ""), onclick: opts.onclick },
    h("div", { class: "stat-label" }, label),
    h("div", { class: "stat-value" }, value),
    sub ? h("div", { class: "stat-sub" }, sub) : null,
    opts.bar || null);
}

function projectOverview(d) {
  const s = d.stats;
  const p = d.project;
  const pid = p.id;
  const byId = Object.fromEntries(d.tasks.map((t) => [t.id, t]));
  const attention = d.attention.map((id) => byId[id]).filter(Boolean);
  const out = [];
  if (!p.event_date && isWsAdmin()) {
    out.push(callout("📅", h("div", null, h("b", null, "Укажите дату мероприятия."),
      " Появится обратный отсчёт, а бот предложит сроки задачам без дедлайна."), "yellow",
    h("button", { class: "btn sm", onclick: (e) => pickEventDate(e.currentTarget, p) }, "Указать дату")));
  }
  out.push(h("div", { class: "stat-grid" },
    statCard("Готовность", s.pct + "%", `выполнено ${s.done} из ${s.total}`, { bar: progressBar(s.pct, "var(--c-green-text)") }),
    statCard("Просрочено", s.overdue, "открытых задач", { color: s.overdue ? "red" : "", onclick: () => openFiltered(pid, { status: "overdue" }) }),
    statCard("Срок сегодня", s.due_today, `на неделе ${s.due_week}`, { color: s.due_today ? "orange" : "", onclick: () => openFiltered(pid, { status: "open", sort: "deadline" }) }),
    statCard("Нужна помощь", s.blocked, "блокеры", { color: s.blocked ? "red" : "", onclick: () => openFiltered(pid, { status: "blocked" }) }),
    statCard("Без срока", s.nodate, "открытых задач", { onclick: () => openFiltered(pid, { status: "nodate" }) })));

  out.push(h("h2", { class: "h2" }, "🔥 Требует внимания"));
  out.push(attention.length ? taskLines(attention, { owner: true })
    : h("p", { class: "muted" }, s.nodate && s.nodate === s.total - s.done ? "Горящих сроков нет, но у задач не заполнены дедлайны." : "Горящих задач нет 👌"));

  if (seesAll() && d.audit.length) out.push(auditBlock(d.audit));

  const team = d.workload.filter((w) => w.total);
  if (team.length) {
    out.push(h("h2", { class: "h2" }, "👥 Команда"));
    out.push(h("div", { class: "table-wrap" }, h("table", { class: "db-table compact" },
      h("thead", null, h("tr", null, ["Участник", "Открыто", "Просрочено", "На неделе", "Готово"].map((x) => h("th", null, h("div", { class: "th" }, x))))),
      h("tbody", null, team.map((w) => h("tr", { onclick: () => openFiltered(pid, { member: String(w.member_id), status: "open" }) },
        h("td", null, h("div", { class: "cell-inner" }, h("span", { class: "person" }, avatar(w.name, w.member_id, "xs"), w.name),
          w.overloaded ? tag("перегруз", "orange", "sm") : null, !w.connected ? tag("не в боте", "gray", "sm") : null)),
        h("td", null, h("div", { class: "cell-inner num" }, w.open)),
        h("td", null, h("div", { class: "cell-inner num" + (w.overdue ? " text-red" : "") }, w.overdue || "—")),
        h("td", null, h("div", { class: "cell-inner num" }, w.due_week || "—")),
        h("td", null, h("div", { class: "cell-inner" }, h("span", { class: "mini-progress" }, h("i", { style: { width: w.pct + "%" } })),
          h("span", { class: "muted small" }, `${w.done}/${w.total}`)))))))));
  }

  if (d.blocks.length) {
    out.push(h("h2", { class: "h2" }, "📦 Блоки"));
    out.push(h("div", { class: "blocks-grid" }, d.blocks.map((b) => h("button", { class: "block-card", onclick: () => openFiltered(pid, { block: b.block || "__none" }) },
      h("div", { class: "block-card-head" }, tag(b.block || "Без блока", b.block ? colorFor(b.block) : "default"), h("span", { class: "grow" }),
        b.overdue ? tag(`${b.overdue} просрочено`, "red", "sm") : null),
      progressBar(b.pct),
      h("div", { class: "muted small" }, `${b.done} из ${b.total} · ${b.pct}%`)))));
  }

  if (d.milestones.length) {
    out.push(h("h2", { class: "h2" }, "🏁 Вехи"));
    out.push(h("div", { class: "timeline" }, d.milestones.map((m) => {
      const done = /выполн|готов|закрыт/i.test(m.status);
      let cls = done ? "ok" : "";
      if (!done && m.date) {
        const diff = Math.round((parseISO(m.date) - today()) / 86400000);
        cls = diff < 0 ? "past" : diff <= 7 ? "soon" : "";
      }
      return h("div", { class: "ms " + cls },
        h("div", { class: "ms-dot" }),
        h("div", null,
          h("div", { class: "muted small" }, (m.date ? fmtDate(m.date, true) : m.date_raw || "дата не указана") + (m.status ? " · " + m.status : "")),
          h("div", { class: "ms-title" }, m.title),
          m.criteria ? h("div", { class: "muted small" }, m.criteria) : null));
    })));
  }

  const risks = d.risks.filter((r) => r.is_open);
  if (risks.length) {
    out.push(toggleBlock(`⚠️ Риски (${risks.length})`, h("div", { class: "risk-list" }, risks.map((r) => h("div", { class: "risk" },
      h("div", { class: "risk-title" }, r.title),
      h("div", { class: "tags" }, tag("влияние: " + (r.impact || "—"), /крит|высок|crit|high/i.test(r.impact) ? "red" : "gray", "sm"),
        tag("вероятность: " + (r.probability || "—"), "gray", "sm"), r.owner ? h("span", { class: "muted small" }, r.owner) : null),
      r.mitigation ? h("div", { class: "muted small" }, "Что делать: " + r.mitigation) : null))), false, "risks:" + pid));
  }
  return h("div", { class: "overview" }, out);
}

function auditBlock(issues) {
  const label = { critical: ["Важно", "red"], warning: ["Проверить", "yellow"], info: ["Инфо", "gray"] };
  return toggleBlock(`🧹 Проверка таблицы (${issues.length})`, h("div", { class: "audit" }, issues.map((i) => {
    const [text, color] = label[i.level] || label.info;
    return h("div", { class: "audit-item" },
      h("div", { class: "audit-head" }, tag(text, color, "sm"), h("b", null, `${i.title}: ${i.count}`)),
      i.hint ? h("div", { class: "muted small" }, i.hint) : null,
      h("ul", null, i.items.slice(0, 8).map((x) => h("li", null, x)), i.items.length > 8 ? h("li", { class: "muted" }, `…ещё ${i.count - 8}`) : null));
  })), false, "audit");
}

// ---------- обзвон ----------
async function projectCalls(d) {
  const pid = d.project.id;
  const c = await api(`/api/projects/${pid}/calls`);
  const items = c.items;
  const done = items.filter((i) => i.check).length;
  const readOnly = d.role === "viewer";
  const mine = myMemberId();
  const out = [];
  out.push(callout("📞", h("div", null, h("b", null, `Обзвон на ${fmtDate(c.today, true)}`),
    h("div", null, seesAll()
      ? "Бот сам собирает, кому позвонить: просрочки, «нужна помощь», сроки сегодня и завтра без старта, подрядчики по ближайшим задачам."
      : "Ваши запланированные звонки. Бот напомнит в назначенное время.")), "blue"));
  if (items.length) {
    out.push(h("div", { class: "calls-progress" }, progressBar((100 * done) / items.length, "var(--c-green-text)"),
      h("span", { class: "muted small" }, `${done} из ${items.length}`)));
    out.push(h("div", { class: "todo-list" }, items.map((item) => callItem(item, pid, readOnly))));
  } else {
    out.push(h("p", { class: "muted" }, "На сегодня звонить некому 👌"));
  }
  const planned = c.planned.filter((p) => p.status === "planned");
  out.push(h("div", { class: "h2-row" }, h("h2", { class: "h2" }, "🗓 Запланированные звонки"), h("span", { class: "grow" }),
    !readOnly ? h("button", { class: "btn sm primary", onclick: () => openCallForm({ project_id: pid }) }, icon("plus"), "Запланировать") : null));
  if (planned.length) {
    out.push(h("div", { class: "table-wrap" }, h("table", { class: "db-table compact" },
      h("thead", null, h("tr", null, ["Кому", "Когда", "Звонит", "Телефон", "Заметка", ""].map((x) => h("th", null, h("div", { class: "th" }, x))))),
      h("tbody", null, planned.map((p) => {
        const who = p.member_id ? member(p.member_id) : null;
        const canAct = isWsAdmin() || p.member_id === mine;
        return h("tr", null,
          h("td", null, h("div", { class: "cell-inner" }, h("b", null, p.contact))),
          h("td", null, h("div", { class: "cell-inner" }, fmtDateTime(p.due_at))),
          h("td", null, h("div", { class: "cell-inner" }, who ? h("span", { class: "person" }, avatar(who.name, who.id, "xs"), who.name) : "—")),
          h("td", null, h("div", { class: "cell-inner" }, p.phone ? h("a", { href: "tel:" + p.phone }, p.phone) : "—")),
          h("td", null, h("div", { class: "cell-inner muted" }, p.note || "")),
          h("td", null, h("div", { class: "cell-inner actions" }, canAct ? [
            h("button", { class: "btn sm", title: "Созвонились", onclick: async () => {
              const result = await promptDialog("Чем закончился звонок?", "Результат (необязательно)", "", "Отметить");
              if (result === null) return;
              await run(() => api(`/api/calls/${p.id}/done`, { json: { result } }), "Отмечено ✓");
              await reloadView();
            } }, icon("tick")),
            h("button", { class: "btn sm ghost", title: "Отменить", onclick: async () => {
              await run(() => api(`/api/calls/${p.id}/cancel`, { json: {} }), "Звонок отменён");
              await reloadView();
            } }, icon("close"))] : null)));
      })))));
  } else {
    out.push(h("p", { class: "muted" }, "Запланированных звонков нет."));
  }
  return h("div", { class: "calls" }, out);
}

function callItem(item, pid, readOnly) {
  const done = !!item.check;
  const toggle = async () => {
    await run(() => api(`/api/projects/${pid}/calls/check`, { json: { key: item.key, checked: !done } }), done ? "Отметка снята" : "Созвонились ✓");
    await reloadView();
  };
  const contacts = [];
  if (item.username) contacts.push(h("a", { href: `https://t.me/${item.username}`, target: "_blank", rel: "noopener" }, "@" + item.username));
  if (item.phone) contacts.push(h("a", { href: "tel:" + item.phone }, item.phone));
  return h("div", { class: "todo" + (done ? " checked" : "") },
    h("button", { class: "todo-box" + (done ? " on" : ""), onclick: toggle, disabled: readOnly, "aria-label": "Отметить" }, done ? icon("tick") : null),
    h("div", { class: "todo-body" },
      h("div", { class: "todo-title" }, item.title),
      h("div", { class: "muted small" }, [item.subtitle, item.caller ? "звонит " + item.caller : "", item.due_at ? fmtDateTime(item.due_at) : ""].filter(Boolean).join(" · ")),
      contacts.length ? h("div", { class: "todo-contacts" }, contacts) : null,
      item.owners && item.owners.length ? h("div", { class: "muted small" }, "ответственные: " + item.owners.join(", ")) : null,
      item.tasks.length ? h("ul", { class: "todo-tasks" }, item.tasks.slice(0, 5).map((t) =>
        h("li", null, h("a", { href: "#", onclick: (e) => { e.preventDefault(); openPeek(t.task_id); } }, t.title), h("span", { class: "muted" }, " — " + t.reason)))) : null,
      done && item.check.member_name ? h("div", { class: "muted small" }, "✓ " + item.check.member_name + (item.check.note ? ": " + item.check.note : "")) : null));
}

// ---------- управление проектом ----------
function settingsRow(title, text, control) {
  return h("div", { class: "set-row" }, h("div", { class: "set-text" }, h("div", { class: "set-title" }, title),
    text ? h("div", { class: "set-desc" }, text) : null), control ? h("div", { class: "set-control" }, control) : null);
}

function projectSettings(d) {
  const p = d.project;
  const name = h("input", { class: "input", value: p.name });
  const date = h("input", { class: "input", type: "date", value: p.event_date || "" });
  const adminSpaces = S.boot.workspaces.filter((w) => w.role === "admin");
  const out = [];
  out.push(h("h2", { class: "h2" }, "Проект"));
  out.push(settingsRow("Название", null, name));
  out.push(settingsRow("Дата мероприятия", "От неё считается обратный отсчёт и черновик сроков.", date));
  out.push(h("div", { class: "btn-row" }, h("button", { class: "btn primary sm", onclick: async () => {
    await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { name: name.value, event_date: date.value || null } }), "Сохранено");
    await refreshWorkspace();
    await reloadView();
  } }, "Сохранить")));
  if (adminSpaces.length > 1) {
    const select = h("select", { class: "input" }, adminSpaces.map((w) => h("option", { value: w.id, selected: w.id === p.workspace_id }, `${w.icon || "🏢"} ${w.name}`)));
    out.push(settingsRow("Пространство", "Перенесите проект в другое пространство, где вы администратор.", h("div", { class: "inline" }, select,
      h("button", { class: "btn sm", onclick: async () => {
        const wid = Number(select.value);
        if (wid === p.workspace_id) return;
        await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { workspace_id: wid } }), "Проект перенесён");
        S.project = null;
        go(`#/w/${wid}/p/${p.id}`);
      } }, "Перенести"))));
  }

  out.push(h("h2", { class: "h2" }, "Источник и выгрузка"));
  if (p.source_type === "gsheet") {
    out.push(settingsRow("Google Таблица", h("span", null, h("a", { href: p.source_url, target: "_blank", rel: "noopener", class: "break" }, p.source_url),
      h("br"), "Последняя синхронизация: " + (p.last_sync_at ? fmtDateTime(p.last_sync_at) : "—"),
      p.last_sync_error ? h("div", { class: "text-red" }, "Ошибка: " + p.last_sync_error) : null),
    h("button", { class: "btn sm", onclick: async () => {
      const r = await run(() => api(`/api/projects/${p.id}/sync`, { json: {} }));
      toast(`Синхронизировано: задач ${r.data.tasks}, новых ${r.data.added}, обновлено ${r.data.updated}`, "ok");
      await reloadView();
    } }, icon("sync"), "Синхронизировать")));
  } else {
    out.push(settingsRow("Источник", p.source_type === "upload"
      ? "Загруженный Excel. Чтобы обновить задачи, загрузите новую версию файла — статусы из бота сохранятся."
      : "Проект создан вручную.", h("a", { class: "btn sm", href: `#/w/${S.wid}/import` }, icon("upload"), "Загрузить Excel")));
  }
  out.push(settingsRow("Выгрузить в Excel", "Актуальные статусы, сроки и комментарии в формате исходной таблицы.", h("div", { class: "inline" },
    h("button", { class: "btn sm", onclick: () => downloadExport(p) }, icon("download"), "Скачать"),
    S.boot.me.member && S.boot.me.member.connected ? h("button", { class: "btn sm ghost", onclick: () => run(() => api(`/api/projects/${p.id}/export`, { json: {} }), "Файл отправлен в чат с ботом 📥") }, "В Telegram") : null)));

  out.push(h("h2", { class: "h2" }, "Черновик сроков"));
  out.push(planBlock(d));

  if (d.audit.length) {
    out.push(h("h2", { class: "h2" }, "Проверка таблицы"));
    out.push(auditBlock(d.audit));
  }

  out.push(h("h2", { class: "h2 danger" }, "Опасная зона"));
  out.push(settingsRow("Архивировать проект", "Проект пропадёт из списков, напоминания по нему прекратятся. Вернуть можно на странице импорта.",
    h("button", { class: "btn sm danger", onclick: async () => {
      if (!(await confirmDialog("Архивировать проект? Напоминания по нему прекратятся.", "Архивировать"))) return;
      await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { archived: true } }), "Проект в архиве");
      S.project = null;
      await refreshWorkspace();
      go(`#/w/${S.wid}`);
    } }, "Архивировать")));
  return h("div", { class: "settings" }, out);
}

async function downloadExport(p) {
  try {
    const res = await fetch(`/api/projects/${p.id}/export.xlsx`, { headers: S.token ? { Authorization: "Bearer " + S.token } : {}, credentials: "same-origin" });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Не удалось выгрузить");
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const name = decodeURIComponent((disposition.split("''")[1] || "").trim()) || `${p.name}.xlsx`;
    const a = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
  } catch (err) {
    toast(err.message, "error");
  }
}

function planBlock(d) {
  const p = d.project;
  const nodate = d.tasks.filter((t) => isOpen(t) && !t.deadline).length;
  const box = h("div");
  const load = h("button", { class: "btn sm", disabled: !p.event_date || !nodate, onclick: async () => {
    const r = await run(() => api(`/api/projects/${p.id}/plan`));
    draw(r.items);
  } }, icon("sparkle"), "Предложить сроки");
  const draw = (items) => {
    if (!items.length) { box.replaceChildren(h("p", { class: "muted" }, "Предложить нечего.")); return; }
    const checks = items.map(() => h("input", { type: "checkbox", checked: true }));
    box.replaceChildren(
      h("p", { class: "muted small" }, `Предложено сроков: ${items.length}. Снимите галочки там, где срок не подходит.`),
      h("div", { class: "plan-list" }, items.map((x, i) => h("label", { class: "plan-item" }, checks[i],
        h("div", null, h("div", { class: "plan-title" }, x.title),
          h("div", { class: "muted small" }, `${fmtDate(x.deadline, true)} (T${x.offset >= 0 ? "+" : ""}${x.offset}) · ${x.responsible || "—"}`),
          h("div", { class: "muted small" }, x.rule + (x.note ? " · " + x.note : "")))))),
      h("div", { class: "btn-row" }, h("button", { class: "btn primary sm", onclick: async () => {
        const chosen = items.filter((_, i) => checks[i].checked).map((x) => ({ task_id: x.task_id, deadline: x.deadline }));
        if (!chosen.length) { toast("Ничего не выбрано"); return; }
        const r = await run(() => api(`/api/projects/${p.id}/plan`, { json: { items: chosen } }));
        toast(`Сроки проставлены: ${r.data.applied}`, "ok");
        await reloadView();
      } }, "Применить выбранные")));
  };
  return h("div", null,
    settingsRow(`Задач без срока: ${nodate}`, p.event_date
      ? "Бот расставит дедлайны обратным отсчётом от дня мероприятия (KV за 18 дней, производство за 10, монтаж T-1, отчёт T+7…) — не больше 3 дедлайнов в день на человека."
      : "Сначала укажите дату мероприятия.", load),
    box);
}
