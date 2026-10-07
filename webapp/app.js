"use strict";
/* Каркас кабинета в стиле Notion: вход, боковая панель, маршруты, главная, «Мои задачи», страницы,
 * поиск, корзина, импорт проектов и администрирование (доступ, команда, напоминания). */

const VIEW_HANDLERS = {
  home: viewHome,
  my: viewMy,
  project: viewProject,
  page: viewPage,
  trash: viewTrash,
  import: viewImport,
  admin: viewAdmin,
  team: viewTeam,
  reminders: viewReminders,
  account: viewAccount,
};
let routeSeq = 0;

// ---------- запуск и вход ----------
async function start() {
  applyTheme();
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  if (inTelegram) {
    tg.ready();
    tg.expand();
    if (tg.BackButton) tg.BackButton.onClick(goBack);
    tg.onEvent("themeChanged", applyTheme);
    document.documentElement.classList.add("tg");
  }
  window.onAuthLost = () => showLogin("Сессия закончилась — войдите снова.");
  window.onMustChangePassword = () => showChangePassword();
  document.addEventListener("keydown", globalKeys);
  window.addEventListener("hashchange", () => { if (S.boot) renderRoute(); });
  window.addEventListener("beforeunload", flushPageOnUnload);
  await boot();
}

function applyTheme() {
  const pref = store("theme") || "system";
  let dark;
  if (pref === "system") {
    dark = inTelegram ? tg.colorScheme === "dark" : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  } else dark = pref === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (inTelegram) {
    const bg = dark ? "#191919" : "#ffffff";
    try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } catch (_) { /* старый клиент */ }
  }
}

async function boot() {
  try {
    S.boot = await api("/api/bootstrap", { noAuthRedirect: true });
  } catch (err) {
    if (err.status === 401) {
      if (inTelegram) {
        try {
          await telegramLogin();
          return boot();
        } catch (loginErr) {
          return showLogin(loginErr.message);
        }
      }
      return showLogin();
    }
    return fatal(err.message);
  }
  if (S.boot.me.must_change_password && S.boot.me.session_kind === "password") return showChangePassword();
  if (!S.boot.workspaces.length) return noWorkspaces();
  await handleDeepLink();
  renderRoute();
}

async function telegramLogin() {
  const r = await api("/api/auth/telegram", { json: { init_data: tg.initData }, noAuthRedirect: true });
  S.token = r.token;
}

function authScreen(...children) {
  closeAllOverlays();
  closePeek();
  document.getElementById("app").replaceChildren(h("div", { class: "auth" },
    h("div", { class: "auth-box" }, h("div", { class: "auth-logo" }, "🏢"), children)));
}

function showLogin(message) {
  S.boot = null;
  S.token = null;
  const login = h("input", { class: "input", autocomplete: "username", autocapitalize: "none", spellcheck: "false", placeholder: "Логин" });
  const password = h("input", { class: "input", type: "password", autocomplete: "current-password", placeholder: "Пароль" });
  const error = h("div", { class: "auth-error", hidden: !message }, message || "");
  const submit = h("button", { class: "btn primary block", type: "submit" }, "Продолжить");
  const form = h("form", { class: "auth-form", onsubmit: async (e) => {
    e.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    try {
      const r = await api("/api/auth/login", { json: { login: login.value, password: password.value }, noAuthRedirect: true });
      S.token = r.token;
      await boot();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      submit.disabled = false;
      password.select();
    }
  } },
  field("Логин", login), field("Пароль", password), error, submit);
  authScreen(
    h("h1", { class: "auth-title" }, "Вход в IAC PM"),
    h("p", { class: "auth-sub" }, "Рабочее пространство команды: задачи проектов, доски и документы."),
    form,
    h("p", { class: "auth-hint" }, inTelegram
      ? "Ваш Telegram пока не связан с аккаунтом. Войдите по логину и паролю или попросите администратора связать аккаунт."
      : "Логин и пароль выдаёт администратор. Из Telegram вход происходит автоматически — кнопкой «Открыть» в боте."));
  setTimeout(() => login.focus(), 50);
}

function showChangePassword() {
  const current = h("input", { class: "input", type: "password", autocomplete: "current-password" });
  const next = h("input", { class: "input", type: "password", autocomplete: "new-password" });
  const repeat = h("input", { class: "input", type: "password", autocomplete: "new-password" });
  const error = h("div", { class: "auth-error", hidden: true });
  const submit = h("button", { class: "btn primary block", type: "submit" }, "Сохранить пароль");
  authScreen(
    h("h1", { class: "auth-title" }, "Придумайте пароль"),
    h("p", { class: "auth-sub" }, "Вы вошли с временным паролем от администратора. Задайте свой — минимум 8 символов."),
    h("form", { class: "auth-form", onsubmit: async (e) => {
      e.preventDefault();
      error.hidden = true;
      if (next.value !== repeat.value) { error.textContent = "Пароли не совпадают"; error.hidden = false; return; }
      submit.disabled = true;
      try {
        await api("/api/auth/password", { json: { current: current.value, new: next.value }, noAuthRedirect: true });
        toast("Пароль сохранён", "ok");
        await boot();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        submit.disabled = false;
      }
    } }, field("Временный пароль", current), field("Новый пароль", next), field("Ещё раз", repeat), error, submit),
    h("button", { class: "btn ghost block", onclick: logout }, "Выйти"));
  setTimeout(() => current.focus(), 50);
}

function noWorkspaces() {
  authScreen(h("h1", { class: "auth-title" }, "Пока нет доступа"),
    h("p", { class: "auth-sub" }, `${S.boot.me.name}, вас ещё не добавили ни в одно рабочее пространство. Попросите администратора открыть доступ.`),
    h("button", { class: "btn block", onclick: () => location.reload() }, "Обновить"),
    h("button", { class: "btn ghost block", onclick: logout }, "Выйти"));
}

function fatal(message) {
  authScreen(h("h1", { class: "auth-title" }, "Не получилось открыть"), h("p", { class: "auth-sub" }, message),
    h("button", { class: "btn block", onclick: () => location.reload() }, "Попробовать снова"));
}

async function logout() {
  try { await api("/api/auth/logout", { json: {}, noAuthRedirect: true }); } catch (_) { /* уже вышли */ }
  S.token = null;
  S.ws = null;
  S.project = null;
  history.replaceState(null, "", location.pathname);
  showLogin();
}

/** Ссылки из бота: ?task=ID, ?tab=my|home|calls. */
async function handleDeepLink() {
  const params = new URLSearchParams(location.search);
  if (!params.has("task") && !params.has("tab")) return;
  history.replaceState(null, "", location.pathname + location.hash);
  const wid = defaultWid();
  const taskId = Number(params.get("task"));
  if (taskId) {
    try {
      const t = await api(`/api/tasks/${taskId}`);
      location.hash = `#/w/${t.workspace_id}/p/${t.project_id}?task=${taskId}`;
      return;
    } catch (err) {
      toast(err.message, "error");
    }
  }
  const tab = params.get("tab");
  if (tab === "my") location.hash = `#/w/${wid}/my`;
  else if (tab === "calls") { S.pendingCalls = true; location.hash = `#/w/${wid}`; }
  else if (tab === "home") location.hash = `#/w/${wid}`;
}

function defaultWid() {
  const saved = store("wid");
  const list = S.boot.workspaces;
  return (list.find((w) => w.id === saved) || list[0]).id;
}

// ---------- маршруты ----------
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  const q = new URLSearchParams(query || "");
  const r = { name: "root", params: {}, wid: null, query: q };
  if (parts[0] === "w" && Number(parts[1])) {
    r.wid = Number(parts[1]);
    const rest = parts.slice(2);
    if (!rest.length) r.name = "home";
    else if (rest[0] === "my") r.name = "my";
    else if (rest[0] === "p" && Number(rest[1])) { r.name = "project"; r.params = { pid: rest[1], section: rest[2] || "" }; }
    else if (rest[0] === "page" && Number(rest[1])) { r.name = "page"; r.params = { id: Number(rest[1]) }; }
    else if (rest[0] === "trash") r.name = "trash";
    else if (rest[0] === "import") r.name = "import";
    else r.name = "home";
  } else if (parts[0] === "admin") { r.name = "admin"; r.params = { tab: parts[1] || "users" }; }
  else if (["team", "reminders", "account"].includes(parts[0])) r.name = parts[0];
  return r;
}

function go(hash, replace) {
  if (location.hash === hash || (location.hash === "" && hash === "#")) { renderRoute(); return; }
  if (replace) { history.replaceState(null, "", hash); renderRoute(); } else location.hash = hash;
}

function wsHref(path) { return `#/w/${S.wid}` + (path ? "/" + path : ""); }
function pageHref(id) { return wsHref("page/" + id); }

async function loadWorkspace() {
  S.ws = await api(`/api/w/${S.wid}`);
}

async function refreshWorkspace() {
  if (!S.wid) return;
  try { await loadWorkspace(); } catch (_) { return; }
  renderSidebar();
}

/** Перечитать данные текущего экрана, сохранив прокрутку. Страницу-документ не трогаем — у неё свой редактор. */
async function reloadView() {
  await refreshWorkspace();
  if (S.route.name === "page") {
    for (const reload of [...S.embeds.values()]) await reload();
    return;
  }
  await renderRoute({ force: true, keepScroll: true });
}

async function renderRoute(opts = {}) {
  if (!S.boot) return;
  const seq = ++routeSeq;
  const r = parseHash();
  if (r.name === "root") { go(`#/w/${defaultWid()}`, true); return; }
  if (r.wid && r.wid !== S.wid) {
    if (!S.boot.workspaces.find((w) => w.id === r.wid)) {
      toast("Нет доступа к этому пространству", "error");
      go(`#/w/${defaultWid()}`, true);
      return;
    }
    S.wid = r.wid;
    S.ws = null;
    S.project = null;
    store("wid", r.wid);
  }
  if (!S.wid) S.wid = defaultWid();
  const leavingPage = S.pageState && !(r.name === "page" && r.params.id === S.pageState.id && opts.keepPage);
  if (leavingPage) await leavePage();
  ensureLayout();
  if (!opts.keepScroll && window.innerWidth < 900) setSidebar(false);
  try {
    if (!S.ws) await loadWorkspace();
  } catch (err) {
    if (err.status === 404) { go(`#/w/${defaultWid()}`, true); return; }
    showError(err);
    return;
  }
  if (seq !== routeSeq) return;
  S.route = r;
  if (!opts.keepScroll) renderSidebar();
  let view;
  try {
    view = await VIEW_HANDLERS[r.name]({ ...r.params, force: opts.force });
  } catch (err) {
    if (err.status === 401) return;
    if (seq !== routeSeq) return;
    view = errorView(err);
  }
  if (seq !== routeSeq || !view) return;
  mount(view, opts);
  const taskId = Number(r.query.get("task"));
  if (taskId) {
    history.replaceState(null, "", location.hash.split("?")[0]);
    openPeek(taskId);
  }
}

function errorView(err) {
  return {
    title: "Ошибка",
    crumbs: [{ label: "Не найдено" }],
    body: h("div", { class: "page" }, emptyState(err.status === 404 ? "🔍" : "⚠️", err.status === 404 ? "Не найдено" : "Не получилось открыть",
      err.message, h("a", { class: "btn", href: wsHref() }, "На главную"))),
  };
}

function showError(err) {
  ensureLayout();
  mount(errorView(err), {});
}

// ---------- каркас ----------
function ensureLayout() {
  if (document.querySelector(".layout")) return;
  const collapsed = !!store("sb:collapsed");
  document.getElementById("app").replaceChildren(h("div", { class: "layout" + (collapsed ? " sb-collapsed" : "") },
    h("aside", { class: "sidebar", id: "sidebar" }),
    h("div", { class: "sidebar-scrim", onclick: () => setSidebar(false) }),
    h("main", { class: "main" },
      h("header", { class: "topbar", id: "topbar" }),
      h("div", { class: "scroller", id: "scroller" }))));
}

function mount(view, opts) {
  const scroller = document.getElementById("scroller");
  const keep = opts.keepScroll ? {
    top: scroller.scrollTop,
    inner: [...scroller.querySelectorAll("[data-keep-scroll]")].map((el) => [el.dataset.keepScroll, el.scrollLeft, el.scrollTop]),
  } : null;
  if (opts.keepScroll) renderSidebar();
  renderTopbar(view);
  scroller.replaceChildren(view.body);
  document.title = view.title ? `${view.title} · IAC PM` : "IAC PM";
  if (keep) {
    scroller.scrollTop = keep.top;
    for (const [key, left, top] of keep.inner) {
      const el = scroller.querySelector(`[data-keep-scroll="${key}"]`);
      if (el) { el.scrollLeft = left; el.scrollTop = top; }
    }
  } else {
    scroller.scrollTop = 0;
  }
  if (view.afterMount) view.afterMount();
  markActiveTask();
  syncBackButton();
}

function renderTopbar(view) {
  const crumbs = (view.crumbs || []).filter(Boolean);
  const nav = h("nav", { class: "crumbs" });
  crumbs.forEach((c, i) => {
    if (i) nav.append(h("span", { class: "crumb-sep" }, "/"));
    const inner = [c.icon ? h("span", { class: "crumb-icon" }, c.icon) : null, h("span", { class: "crumb-text" }, c.label || "Без названия")];
    nav.append(c.href && i < crumbs.length - 1 ? h("a", { class: "crumb", href: c.href }, inner) : h("span", { class: "crumb current" }, inner));
  });
  document.getElementById("topbar").replaceChildren(
    h("button", { class: "icon-btn tb-menu", onclick: () => setSidebar(!sidebarVisible()), "aria-label": "Меню" }, icon("menu")),
    nav,
    h("div", { class: "tb-actions" }, view.actions || null));
}

function sidebarVisible() {
  const layout = document.querySelector(".layout");
  if (window.innerWidth < 900) return S.sidebarOpen;
  return layout && !layout.classList.contains("sb-collapsed");
}

function setSidebar(open) {
  const layout = document.querySelector(".layout");
  if (!layout) return;
  if (window.innerWidth < 900) {
    S.sidebarOpen = open;
    layout.classList.toggle("sb-open", open);
  } else {
    layout.classList.toggle("sb-collapsed", !open);
    store("sb:collapsed", !open);
  }
  syncBackButton();
}

function goBack() {
  if (S.overlays.length) { closeOverlay(); return; }
  if (S.peek) { closePeek(); return; }
  if (S.sidebarOpen) { setSidebar(false); return; }
  history.back();
}

function globalKeys(e) {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (S.boot && S.wid) openSearch();
    return;
  }
  if (mod && e.key === "\\") { e.preventDefault(); setSidebar(!sidebarVisible()); return; }
  if (e.key === "Escape") {
    if (S.overlays.length) { closeOverlay(); e.preventDefault(); return; }
    const editing = document.activeElement && (document.activeElement.isContentEditable || /INPUT|TEXTAREA/.test(document.activeElement.tagName));
    if (S.peek && !editing) { closePeek(); e.preventDefault(); return; }
    if (S.sidebarOpen) setSidebar(false);
  }
}

// ---------- боковая панель ----------
function wsBadge(ws, size) {
  return h("span", { class: "ws-badge" + (size ? " " + size : "") }, ws && ws.icon ? ws.icon : initials(ws ? ws.name : "?").slice(0, 1));
}

function sbItem(iconName, label, href, opts = {}) {
  const tagName = href ? "a" : "button";
  return h(tagName, {
    class: "sb-item" + (opts.active ? " active" : "") + (opts.cls ? " " + opts.cls : ""), href,
    onclick: opts.onclick,
    style: opts.depth ? { paddingLeft: 8 + opts.depth * 14 + "px" } : null,
  },
  h("span", { class: "sb-icon" }, typeof iconName === "string" && ICONS[iconName] ? icon(iconName) : iconName),
  h("span", { class: "sb-label" }, label),
  opts.badge ? h("span", { class: "sb-badge" }, opts.badge) : null,
  opts.hint ? h("span", { class: "sb-hint" }, opts.hint) : null);
}

function sbSection(title, add, children, key) {
  const collapsed = key ? !!store("sbsec:" + key) : false;
  const body = h("div", { class: "sb-section-body", hidden: collapsed }, children);
  const head = h("div", { class: "sb-section-head" },
    h("button", { class: "sb-section-title", onclick: () => {
      if (!key) return;
      body.hidden = !body.hidden;
      store("sbsec:" + key, body.hidden);
    } }, title),
    add ? h("button", { class: "sb-section-add", title: add.title, onclick: add.onclick }, icon("plus")) : null);
  return h("div", { class: "sb-section" }, head, body);
}

function renderSidebar() {
  const sb = document.getElementById("sidebar");
  if (!sb || !S.ws) return;
  const r = S.route;
  const ws = S.boot.workspaces.find((w) => w.id === S.wid) || S.ws.workspace;
  const urgent = S.ws.projects.reduce((n, p) => n + (p.my_urgent || 0), 0);
  const admin = isWsAdmin();
  sb.replaceChildren(
    h("div", { class: "sb-top" },
      h("button", { class: "sb-switcher", onclick: (e) => workspaceMenu(e.currentTarget) },
        wsBadge(ws), h("span", { class: "sb-ws-name" }, ws.name), icon("down", "sb-caret")),
      h("button", { class: "icon-btn sb-hide", onclick: () => setSidebar(false), title: "Скрыть панель (Ctrl+\\)" }, icon("dleft"))),
    h("div", { class: "sb-scroll" },
      h("div", { class: "sb-group" },
        sbItem("search", "Поиск", null, { onclick: openSearch, hint: navigator.platform.includes("Mac") ? "⌘K" : "Ctrl K" }),
        sbItem("home", "Главная", wsHref(), { active: r.name === "home" }),
        sbItem("check", "Мои задачи", wsHref("my"), { active: r.name === "my", badge: urgent || null })),
      sbSection("Проекты", admin ? { title: "Новый проект", onclick: () => go(wsHref("import")) } : null, projectsTree(), "projects"),
      sbSection("Страницы", canWrite() ? { title: "Новая страница", onclick: () => createPage(null) } : null, pagesTree(), "pages"),
      h("div", { class: "sb-group" },
        admin ? sbItem("upload", "Импорт и архив", wsHref("import"), { active: r.name === "import" }) : null,
        sbItem("trash", "Корзина", wsHref("trash"), { active: r.name === "trash" })),
      isSuperadmin() ? sbSection("Администрирование", null, [
        sbItem("lock", "Доступ", "#/admin/users", { active: r.name === "admin" }),
        sbItem("users", "Команда", "#/team", { active: r.name === "team" }),
        sbItem("bell", "Напоминания", "#/reminders", { active: r.name === "reminders" }),
      ], "admin") : null),
    h("div", { class: "sb-bottom" },
      h("button", { class: "sb-profile" + (r.name === "account" ? " active" : ""), onclick: (e) => profileMenu(e.currentTarget) },
        avatar(me().name, myMemberId(), "sm"),
        h("span", { class: "sb-profile-text" }, h("span", { class: "sb-profile-name" }, me().name),
          h("span", { class: "sb-profile-sub" }, ROLE_LABEL[role()] || "")),
        icon("more"))));
}

function treeToggle(open, onToggle, hasChildren) {
  return h("button", { class: "sb-toggle" + (open ? " open" : "") + (hasChildren ? "" : " empty"), onclick: (e) => { e.preventDefault(); e.stopPropagation(); onToggle(); }, "aria-label": open ? "Свернуть" : "Развернуть" }, icon("chevron"));
}

function projectsTree() {
  const r = S.route;
  const projects = S.ws.projects;
  if (!projects.length) return h("div", { class: "sb-empty" }, isWsAdmin() ? "Загрузите Excel или создайте проект" : "Проектов пока нет");
  return projects.map((p) => {
    const activeProject = r.name === "project" && Number(r.params.pid) === p.id;
    const key = "sb:p:" + p.id;
    const saved = store(key);
    const open = saved === null ? activeProject : !!saved;
    const section = activeProject ? r.params.section || "tasks" : null;
    const wrap = h("div", { class: "sb-node" });
    const row = h("a", { class: "sb-item tree" + (activeProject && section === "tasks" ? " active" : ""), href: projectHref(p.id) },
      h("span", { class: "sb-icon has-toggle" }, h("span", { class: "sb-emoji" }, "📁"), treeToggle(open, () => { store(key, !open); renderSidebar(); }, true)),
      h("span", { class: "sb-label" }, p.name),
      p.my_urgent ? h("span", { class: "sb-badge red", title: "Мои срочные задачи" }, p.my_urgent) : null);
    wrap.append(row);
    if (open) {
      const sub = [
        ["tasks", "board", "Задачи", ""],
        ["overview", "chart", "Обзор", "overview"],
        ["calls", "phone", "Обзвон", "calls"],
        isWsAdmin() ? ["settings", "gear", "Управление", "settings"] : null,
      ].filter(Boolean);
      wrap.append(h("div", { class: "sb-children" }, sub.map(([id, ic, label, path]) =>
        sbItem(ic, label, projectHref(p.id, path), { active: section === id, depth: 1, cls: "sub" }))));
    }
    return wrap;
  });
}

function pagesTree() {
  const pages = S.ws.pages;
  if (!pages.length) return h("div", { class: "sb-empty" }, canWrite() ? "Создайте первую страницу" : "Страниц пока нет");
  const byParent = {};
  for (const p of pages) (byParent[p.parent_id || 0] = byParent[p.parent_id || 0] || []).push(p);
  for (const list of Object.values(byParent)) list.sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
  const activeId = S.route.name === "page" ? S.route.params.id : null;
  const ancestors = new Set();
  if (activeId) {
    const byId = Object.fromEntries(pages.map((p) => [p.id, p]));
    let cur = byId[activeId];
    while (cur && cur.parent_id) { ancestors.add(cur.parent_id); cur = byId[cur.parent_id]; }
  }
  const node = (p, depth) => {
    const children = byParent[p.id] || [];
    const key = "sb:pg:" + p.id;
    const saved = store(key);
    const open = saved === null ? ancestors.has(p.id) : !!saved || ancestors.has(p.id);
    const row = h("a", { class: "sb-item tree" + (p.id === activeId ? " active" : ""), href: pageHref(p.id), style: { paddingLeft: 8 + depth * 14 + "px" } },
      h("span", { class: "sb-icon has-toggle" }, h("span", { class: "sb-emoji" }, p.icon || "📄"),
        treeToggle(open, () => { store(key, !open); renderSidebar(); }, children.length)),
      h("span", { class: "sb-label" }, p.title || "Без названия"),
      canWrite() ? h("span", { class: "sb-actions" },
        h("button", { class: "sb-act", title: "Ещё", onclick: (e) => { e.preventDefault(); e.stopPropagation(); pageMenu(e.currentTarget, p); } }, icon("more")),
        h("button", { class: "sb-act", title: "Вложенная страница", onclick: (e) => { e.preventDefault(); e.stopPropagation(); createPage(p.id); } }, icon("plus"))) : null);
    return h("div", { class: "sb-node" }, row,
      open ? (children.length ? children.map((c) => node(c, depth + 1))
        : h("div", { class: "sb-empty", style: { paddingLeft: 30 + (depth + 1) * 14 + "px" } }, "Нет вложенных страниц")) : null);
  };
  return (byParent[0] || []).map((p) => node(p, 0));
}

function workspaceMenu(anchor) {
  popMenu(anchor, [
    ...S.boot.workspaces.map((w) => ({
      label: h("span", { class: "menu-ws" }, wsBadge(w, "sm"), h("span", null, w.name)), hint: ROLE_LABEL[w.role] || "",
      checked: w.id === S.wid, onClick: () => { if (w.id !== S.wid) go(`#/w/${w.id}`); },
    })),
    isSuperadmin() ? { divider: true } : null,
    isSuperadmin() ? { label: "Управление пространствами", icon: "gear", onClick: () => go("#/admin/workspaces") } : null,
  ], { title: me().login ? me().login : "Пространства", cls: "ws-menu" });
}

function profileMenu(anchor) {
  popMenu(anchor, [
    { label: "Мой аккаунт", icon: "user", onClick: () => go("#/account") },
    { label: "Тема оформления", icon: "sparkle", onClick: () => themeMenu(anchor) },
    { divider: true },
    { label: "Выйти", icon: "logout", onClick: logout },
  ], { title: me().name });
}

function themeMenu(anchor) {
  const cur = store("theme") || "system";
  popMenu(anchor, [["system", "Как в системе"], ["light", "Светлая"], ["dark", "Тёмная"]].map(([v, label]) => ({
    label, checked: cur === v, onClick: () => { store("theme", v); applyTheme(); },
  })), { title: "Тема" });
}

// ---------- недавнее ----------
function remember(kind, id, title, emoji) {
  const key = "recent:" + S.wid;
  const list = (store(key) || []).filter((x) => !(x.kind === kind && x.id === id));
  list.unshift({ kind, id, title, icon: emoji, at: Date.now() });
  store(key, list.slice(0, 12));
}

function recentItems() {
  const list = store("recent:" + S.wid) || [];
  const pages = Object.fromEntries(S.ws.pages.map((p) => [p.id, p]));
  const projects = Object.fromEntries(S.ws.projects.map((p) => [p.id, p]));
  return list.map((x) => {
    if (x.kind === "page" && pages[x.id]) return { ...x, title: pages[x.id].title || "Без названия", icon: pages[x.id].icon || "📄", href: pageHref(x.id) };
    if (x.kind === "project" && projects[x.id]) return { ...x, title: projects[x.id].name, icon: "📁", href: projectHref(x.id) };
    return null;
  }).filter(Boolean).slice(0, 8);
}

// ---------- главная ----------
function greeting() {
  const hour = new Date().getHours();
  const name = me().name.split(/\s+/)[0];
  const text = hour < 5 ? "Доброй ночи" : hour < 12 ? "Доброе утро" : hour < 18 ? "Добрый день" : "Добрый вечер";
  return `${text}, ${name}`;
}

async function viewHome() {
  const my = await api(`/api/w/${S.wid}/my`);
  if (S.pendingCalls) {
    S.pendingCalls = false;
    if (S.ws.projects.length) { go(projectHref(S.ws.projects[0].id, "calls"), true); return null; }
  }
  const ws = S.ws;
  const open = my.tasks.filter(isOpen);
  const soon = open.filter((t) => ["overdue", "today", "tomorrow"].includes(t.bucket) || t.blocked).slice(0, 8);
  const recent = recentItems();
  const out = [];
  out.push(h("h1", { class: "home-greeting" }, greeting()));
  if (recent.length) {
    out.push(h("div", { class: "home-section" }, sectionTitle("🕘 Недавние"),
      h("div", { class: "recent-row" }, recent.map((x) => h("a", { class: "recent-card", href: x.href },
        h("div", { class: "recent-cover c-" + colorFor(x.title) }), h("div", { class: "recent-icon" }, x.icon),
        h("div", { class: "recent-title" }, x.title), h("div", { class: "recent-sub" }, x.kind === "page" ? "Страница" : "Проект"))))));
  }
  out.push(h("div", { class: "home-section" },
    sectionTitle("☑️ Мои задачи", open.length || null, h("a", { class: "section-link", href: wsHref("my") }, "Все →")),
    !my.linked ? callout("🔗", "Аккаунт не связан с участником команды, поэтому личных задач нет. Администратор может связать его в разделе «Доступ».", "gray")
      : soon.length ? taskLines(soon, { project: true })
        : h("p", { class: "muted" }, open.length ? "Горящих задач нет — всё по плану 👌" : "За вами нет открытых задач 🙌")));
  out.push(h("div", { class: "home-section" },
    sectionTitle("📁 Проекты", ws.projects.length || null),
    h("div", { class: "boards-grid" }, ws.projects.map(projectTile),
      isWsAdmin() ? h("a", { class: "board-tile add", href: wsHref("import") }, icon("plus"), h("span", null, "Новый проект")) : null),
    !ws.projects.length && !isWsAdmin() ? h("p", { class: "muted" }, "Проектов пока нет.") : null));
  const roots = ws.pages.filter((p) => !p.parent_id).sort((a, b) => a.sort_order - b.sort_order);
  out.push(h("div", { class: "home-section" },
    sectionTitle("📄 Страницы", roots.length || null),
    h("div", { class: "pages-grid" }, roots.map((p) => h("a", { class: "page-tile", href: pageHref(p.id) },
      h("span", { class: "page-tile-icon" }, p.icon || "📄"), h("span", { class: "page-tile-title" }, p.title || "Без названия"),
      h("span", { class: "page-tile-sub" }, relTime(p.updated_at)))),
    canWrite() ? h("button", { class: "page-tile add", onclick: () => createPage(null) }, icon("plus"), h("span", null, "Новая страница")) : null)));
  return {
    title: "Главная",
    crumbs: [{ icon: wsBadge(S.boot.workspaces.find((w) => w.id === S.wid)), label: ws.workspace.name }, { label: "Главная" }],
    body: h("div", { class: "page home" }, out),
  };
}

function projectTile(p) {
  const s = p.stats;
  const ev = eventInfo(p);
  return h("a", { class: "board-tile", href: projectHref(p.id) },
    h("div", { class: "tile-cover c-" + colorFor(p.name) }, h("span", { class: "tile-icon" }, "📁")),
    h("div", { class: "tile-body" },
      h("div", { class: "tile-title" }, p.name),
      h("div", { class: "tile-meta" }, p.event_date ? `${fmtDate(p.event_date, true)}${ev ? " · " + ev.text : ""}` : "дата не указана"),
      progressBar(s.pct),
      h("div", { class: "tile-tags" },
        h("span", { class: "muted small" }, `${s.pct}% · ${s.done}/${s.total}`), h("span", { class: "grow" }),
        s.overdue ? tag(`${s.overdue} просроч.`, "red", "sm") : null,
        p.my_open ? tag(`мои: ${p.my_open}`, "blue", "sm") : null)));
}

// ---------- мои задачи ----------
async function viewMy() {
  const data = await api(`/api/w/${S.wid}/my`);
  const open = data.tasks.filter(isOpen);
  const overdue = open.filter((t) => t.status === "overdue").length;
  const todayCount = open.filter((t) => t.bucket === "today").length;
  const head = h("div", { class: "page-head" }, h("div", { class: "page-icon" }, "☑️"), h("h1", { class: "page-title" }, "Мои задачи"),
    h("div", { class: "head-props" },
      h("span", { class: "head-prop static" }, `Открыто ${open.length}`),
      overdue ? h("span", { class: "head-prop static warn" }, `Просрочено ${overdue}`) : null,
      todayCount ? h("span", { class: "head-prop static" }, `Срок сегодня ${todayCount}`) : null));
  const body = !data.linked
    ? callout("🔗", "Аккаунт не связан с участником команды — задачи назначаются по имени в колонке «Ответственный». Попросите администратора связать аккаунт в разделе «Доступ».", "gray")
    : renderTaskDB({ key: "my:" + S.wid, tasks: data.tasks, showProject: true, canCreate: false, noMine: true,
      defaults: { view: "list" }, readOnly: role() === "viewer" });
  return { title: "Мои задачи", crumbs: [{ icon: "☑️", label: "Мои задачи" }], body: h("div", { class: "page full" }, head, body) };
}

// ---------- страницы-документы ----------
function pagesById() { return Object.fromEntries(S.ws.pages.map((p) => [p.id, p])); }

async function createPage(parentId, opts = {}) {
  try {
    const r = await run(() => api(`/api/w/${S.wid}/pages`, { json: { title: "", parent_id: parentId } }));
    if (parentId) store("sb:pg:" + parentId, true);
    await refreshWorkspace();
    if (opts.navigate !== false) go(pageHref(r.data.id));
    return r.data;
  } catch (_) {
    return null;
  }
}

function pageMenu(anchor, p) {
  popMenu(anchor, [
    { label: "Открыть", icon: "page", onClick: () => go(pageHref(p.id)) },
    { label: "Вложенная страница", icon: "plus", onClick: () => createPage(p.id) },
    { label: "Переместить в…", icon: "arrow", onClick: () => movePageMenu(anchor, p) },
    { label: "Скопировать ссылку", icon: "link", onClick: () => copyText(`${location.origin}${location.pathname}${pageHref(p.id)}`) },
    { divider: true },
    { label: "Удалить", icon: "trash", danger: true, onClick: () => trashPage(p) },
  ], { title: p.title || "Без названия" });
}

function movePageMenu(anchor, p) {
  const pages = S.ws.pages;
  const blocked = new Set([p.id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const x of pages) if (x.parent_id && blocked.has(x.parent_id) && !blocked.has(x.id)) { blocked.add(x.id); grew = true; }
  }
  popMenu(anchor, [
    { label: "В корень (без родителя)", icon: "home", checked: !p.parent_id, onClick: () => movePage(p, null) },
    { divider: true },
    ...pages.filter((x) => !blocked.has(x.id)).map((x) => ({ label: x.title || "Без названия", icon: x.icon || "📄",
      checked: p.parent_id === x.id, onClick: () => movePage(p, x.id) })),
  ], { title: "Переместить в", search: "Найти страницу…" });
}

async function movePage(p, parentId) {
  await run(() => api(`/api/pages/${p.id}/move`, { json: { parent_id: parentId } }), "Страница перемещена");
  if (parentId) store("sb:pg:" + parentId, true);
  await refreshWorkspace();
  if (S.route.name === "page" && S.route.params.id === p.id) renderTopbar(pageTopbar(p.id));
}

async function trashPage(p) {
  const children = S.ws.pages.filter((x) => x.parent_id === p.id).length;
  if (children && !(await confirmDialog(`Переместить «${p.title || "Без названия"}» и вложенные страницы в корзину?`, "В корзину"))) return;
  await run(() => api(`/api/pages/${p.id}`, { method: "DELETE" }), "Перемещено в корзину");
  const wasOpen = S.route.name === "page" && (S.route.params.id === p.id || isDescendant(S.route.params.id, p.id));
  if (wasOpen && S.pageState) { S.pageState.save.cancel(); S.pageState.dirty = false; }
  await refreshWorkspace();
  if (wasOpen) go(wsHref());
}

function isDescendant(id, ancestorId) {
  const byId = pagesById();
  let cur = byId[id];
  while (cur && cur.parent_id) {
    if (cur.parent_id === ancestorId) return true;
    cur = byId[cur.parent_id];
  }
  return false;
}

function pageCrumbs(id) {
  const byId = pagesById();
  const chain = [];
  let cur = byId[id];
  while (cur) { chain.unshift(cur); cur = cur.parent_id ? byId[cur.parent_id] : null; }
  return chain.map((p) => ({ icon: p.icon || "📄", label: p.title || "Без названия", href: pageHref(p.id) }));
}

function pageTopbar(id) {
  const st = S.pageState;
  const p = pagesById()[id];
  return {
    crumbs: pageCrumbs(id),
    actions: [
      h("span", { class: "save-state", id: "save-state" }, st ? st.stateText || "" : ""),
      p ? h("button", { class: "icon-btn", title: "Ещё", onclick: (e) => (canWrite() ? pageMenu(e.currentTarget, p)
        : popMenu(e.currentTarget, [{ label: "Скопировать ссылку", icon: "link", onClick: () => copyText(location.href) }])) }, icon("more")) : null,
    ],
  };
}

function setSaveState(text, error) {
  if (!S.pageState) return;
  S.pageState.stateText = text;
  const el = document.getElementById("save-state");
  if (el) { el.textContent = text; el.classList.toggle("error", !!error); }
}

async function leavePage() {
  S.embeds.clear();
  const st = S.pageState;
  if (!st) return;
  if (st.dirty) await st.save.flush();
  if (st.editor) st.editor.destroy();
  S.pageState = null;
}

function flushPageOnUnload() {
  const st = S.pageState;
  if (!st || !st.dirty) return;
  const headers = { "Content-Type": "application/json" };
  if (S.token) headers.Authorization = "Bearer " + S.token;
  try {
    fetch(`/api/pages/${st.id}`, { method: "PUT", headers, keepalive: true, credentials: "same-origin",
      body: JSON.stringify({ title: st.title, icon: st.icon, content: st.content, version: st.version }) });
  } catch (_) { /* браузер закрывается */ }
}

async function viewPage(params) {
  const page = await api(`/api/pages/${params.id}`);
  if (page.workspace_id !== S.wid) { go(`#/w/${page.workspace_id}/page/${page.id}`, true); return null; }
  const editable = page.can_edit;
  const st = {
    id: page.id, version: page.version, title: page.title, icon: page.icon, content: page.content,
    dirty: false, saving: false, again: false, stateText: "", editor: null,
  };
  S.pageState = st;
  remember("page", page.id, page.title, page.icon);
  const banner = h("div", { class: "conflict-slot" });

  st.save = debounce(async () => {
    if (S.pageState !== st || !st.dirty) return;
    if (st.saving) { st.again = true; return; }
    st.saving = true;
    st.dirty = false;
    setSaveState("Сохранение…");
    try {
      const r = await api(`/api/pages/${st.id}`, { method: "PUT", json: { title: st.title, icon: st.icon, content: st.content, version: st.version } });
      st.version = r.data.version;
      setSaveState("Сохранено");
      const entry = S.ws.pages.find((p) => p.id === st.id);
      if (entry && (entry.title !== r.data.title || entry.icon !== r.data.icon)) {
        entry.title = r.data.title;
        entry.icon = r.data.icon;
        renderSidebar();
        if (S.route.name === "page" && S.route.params.id === st.id) renderTopbar(pageTopbar(st.id));
      }
    } catch (err) {
      st.dirty = true;
      if (err.status === 409) showConflict(st, err.data.page, banner);
      else setSaveState("Не сохранено", true);
    } finally {
      st.saving = false;
      if (st.again) { st.again = false; st.save(); }
    }
  }, 700);
  const changed = () => { st.dirty = true; setSaveState("Изменено"); st.save(); };

  const iconEl = h("button", { class: "page-emoji" + (st.icon ? "" : " hidden"), disabled: !editable,
    onclick: (e) => emojiPicker(e.currentTarget, setIcon, true) }, st.icon || "");
  function setIcon(emoji) {
    st.icon = emoji;
    iconEl.textContent = emoji;
    iconEl.classList.toggle("hidden", !emoji);
    addIcon.hidden = !!emoji || !editable;
    changed();
  }
  const addIcon = h("button", { class: "page-add-icon", hidden: !!st.icon || !editable,
    onclick: (e) => emojiPicker(e.currentTarget, setIcon) }, "☺ Добавить иконку");
  const title = h("h1", { class: "page-title doc", "data-placeholder": "Без названия" });
  title.textContent = st.title;
  if (editable) {
    title.contentEditable = "true";
    title.spellcheck = false;
    title.addEventListener("input", () => {
      st.title = title.textContent.replace(/\s+/g, " ").trim();
      if (!title.textContent) title.replaceChildren();
      changed();
    });
    title.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || (e.key === "ArrowDown" && !e.shiftKey)) { e.preventDefault(); st.editor.focusFirst(); }
    });
    title.addEventListener("paste", (e) => {
      e.preventDefault();
      document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text").replace(/\s+/g, " "));
    });
  }
  const meta = h("div", { class: "page-meta" },
    page.updated_by ? `Изменено: ${page.updated_by}, ${relTime(page.updated_at)}` : `Создано ${relTime(page.created_at)}`,
    !editable ? h("span", { class: "tag c-gray sm" }, page.archived ? "в корзине" : "только чтение") : null);
  const editorEl = h("div", { class: "page-editor" });
  st.editor = new BlockEditor(editorEl, {
    blocks: page.content, editable,
    onChange: (blocks) => { st.content = blocks; changed(); },
    pageInfo: (id) => pagesById()[id],
    onOpenPage: (id) => go(pageHref(id)),
    onCreateSubpage: () => createPage(st.id, { navigate: false }),
    pickProject: editable ? pickProject : null,
    renderEmbed: renderTaskEmbed,
  });
  const linked = new Set(page.content.filter((b) => b.type === "page").map((b) => b.page_id));
  const children = S.ws.pages.filter((p) => p.parent_id === page.id && !linked.has(p.id)).sort((a, b) => a.sort_order - b.sort_order);
  const subpages = children.length ? h("div", { class: "subpages" }, children.map((c) =>
    h("a", { class: "blk-pagelink", href: pageHref(c.id) }, h("span", { class: "pl-icon" }, c.icon || "📄"), h("span", { class: "pl-title" }, c.title || "Без названия")))) : null;
  const view = {
    title: page.title || "Без названия",
    ...pageTopbar(page.id),
    body: h("div", { class: "page doc" }, banner,
      h("div", { class: "page-head doc" }, iconEl, h("div", { class: "page-head-tools" }, addIcon), title, meta),
      editorEl, subpages),
    afterMount: () => {
      if (editable && !page.title && page.content.length <= 1 && !richLen((page.content[0] || {}).rich || [])) title.focus();
    },
  };
  return view;
}

// ---------- доска задач внутри страницы ----------
function pickProject(anchor) {
  return new Promise((resolve) => {
    const projects = S.ws.projects;
    if (!projects.length) { toast("В пространстве пока нет проектов", "error"); resolve(null); return; }
    let picked = false;
    popMenu(anchor, projects.map((p) => ({ label: p.name, icon: "📁", onClick: () => { picked = true; resolve(p); } })), {
      title: "Доска какого проекта?", search: projects.length > 6 ? "Найти проект…" : null,
      onClose: () => setTimeout(() => { if (!picked) resolve(null); }, 0),
    });
  });
}

function renderTaskEmbed(block, el) {
  const project = S.ws.projects.find((p) => p.id === block.project_id);
  const title = h("a", { class: "embed-title", href: projectHref(block.project_id) },
    h("span", { class: "embed-icon" }, "📁"), h("span", null, project ? project.name : "Проект недоступен"), icon("arrow"));
  const body = h("div", { class: "embed-body" }, h("div", { class: "embed-loading" }, h("div", { class: "spinner" })));
  fill(el, h("div", { class: "embed-head" }, title), body);
  if (!project) {
    fill(body, h("p", { class: "muted small" }, "Проект в архиве или удалён — доску можно убрать через меню блока ⋮⋮."));
    return;
  }
  let loaded = false;
  const load = async () => {
    // блок убрали со страницы — больше не обновляем (при первой загрузке страница ещё не в документе)
    if (loaded && !body.isConnected) { if (S.embeds.get(block.id) === load) S.embeds.delete(block.id); return; }
    loaded = true;
    const keep = [...body.querySelectorAll("[data-keep-scroll]")].map((x) => [x.dataset.keepScroll, x.scrollLeft]);
    try {
      const d = await api(`/api/projects/${block.project_id}`);
      fill(body, renderTaskDB({
        key: "embed:" + block.id, tasks: d.tasks, project: d.project, stages: d.stages || [], canCreate: d.role === "admin",
        readOnly: d.role === "viewer", tasksHref: projectHref(d.project.id),
        defaults: { view: block.view || "board", group: (d.stages || []).length ? "stage" : "status" },
      }));
      for (const [key, left] of keep) {
        const x = body.querySelector(`[data-keep-scroll="${key}"]`);
        if (x) x.scrollLeft = left;
      }
    } catch (err) {
      fill(body, h("p", { class: "muted small" }, err.message));
    }
  };
  S.embeds.set(block.id, load);
  load();
}

function showConflict(st, server, slot) {
  setSaveState("Конфликт", true);
  slot.replaceChildren(callout("⚠️", h("div", null,
    h("b", null, `Страницу изменил(а) ${server.updated_by || "другой человек"}.`),
    " Ваши последние правки ещё не сохранены — выберите, что оставить."), "yellow",
  h("div", { class: "inline" },
    h("button", { class: "btn sm", onclick: async () => {
      st.dirty = false;
      slot.replaceChildren();
      await leavePage();
      renderRoute({ force: true });
    } }, "Загрузить их версию"),
    h("button", { class: "btn sm primary", onclick: () => {
      st.version = server.version;
      slot.replaceChildren();
      st.dirty = true;
      st.save.flush();
    } }, "Оставить мою"))));
}

// ---------- поиск ----------
function openSearch() {
  if (!S.ws) return;
  let close;
  let seq = 0;
  let items = [];
  let active = 0;
  const input = h("input", { class: "search-input", placeholder: `Поиск в «${S.ws.workspace.name}»…` });
  const results = h("div", { class: "search-results" });
  const pick = (item) => { close(); item.go(); };
  const draw = (groups) => {
    items = [];
    results.replaceChildren();
    for (const [title, list] of groups) {
      if (!list.length) continue;
      results.append(h("div", { class: "search-group" }, title));
      for (const item of list) {
        const index = items.length;
        items.push(item);
        results.append(h("button", { class: "search-item" + (index === active ? " active" : ""), dataset: { index },
          onmouseenter: () => setActive(index), onclick: () => pick(item) },
        h("span", { class: "search-icon" }, item.icon), h("span", { class: "search-title" }, item.title),
        item.sub ? h("span", { class: "search-sub" }, item.sub) : null));
      }
    }
    if (!items.length) results.append(h("div", { class: "search-empty" }, input.value.trim().length >= 2 ? "Ничего не найдено" : "Начните вводить название страницы, проекта или задачи"));
  };
  const setActive = (i) => {
    active = Math.max(0, Math.min(items.length - 1, i));
    results.querySelectorAll(".search-item").forEach((el) => el.classList.toggle("active", Number(el.dataset.index) === active));
    const el = results.querySelector(".search-item.active");
    if (el) el.scrollIntoView({ block: "nearest" });
  };
  const initial = () => {
    active = 0;
    const recent = recentItems().map((x) => ({ icon: x.icon, title: x.title, sub: x.kind === "page" ? "страница" : "проект", go: () => go(x.href) }));
    draw([["Недавние", recent], ["Проекты", S.ws.projects.slice(0, 6).map((p) => ({ icon: "📁", title: p.name, go: () => go(projectHref(p.id)) }))]]);
  };
  const search = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { initial(); return; }
    const my = ++seq;
    try {
      const r = await api(`/api/w/${S.wid}/search?q=${encodeURIComponent(q)}`);
      if (my !== seq) return;
      active = 0;
      const byId = pagesById();
      draw([
        ["Страницы", r.pages.map((p) => ({ icon: p.icon || "📄", title: p.title || "Без названия",
          sub: p.parent_id && byId[p.parent_id] ? byId[p.parent_id].title : "", go: () => go(pageHref(p.id)) }))],
        ["Проекты", r.projects.map((p) => ({ icon: "📁", title: p.name, go: () => go(projectHref(p.id)) }))],
        ["Задачи", r.tasks.map((t) => ({ icon: statusDot(t.status), title: t.title, sub: t.project_name,
          go: () => { go(projectHref(t.project_id)); openPeek(t.id); } }))],
      ]);
    } catch (_) { /* ошибка уже показана */ }
  }, 220);
  input.addEventListener("input", search);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    if (e.key === "Enter" && items[active]) { e.preventDefault(); pick(items[active]); }
  });
  const box = h("div", { class: "search-box" }, h("div", { class: "search-head" }, icon("search"), input,
    h("button", { class: "icon-btn search-close", onclick: () => close(), "aria-label": "Закрыть" }, icon("close"))), results,
  h("div", { class: "search-foot" }, h("span", null, "↑↓ выбрать"), h("span", null, "↵ открыть"), h("span", null, "esc закрыть")));
  const wrap = h("div", { class: "search-wrap", onmousedown: (e) => { if (e.target === wrap) close(); } }, box);
  close = pushOverlay(wrap);
  initial();
  input.focus();
}

// ---------- корзина ----------
async function viewTrash() {
  const r = await api(`/api/w/${S.wid}/trash`);
  const admin = isWsAdmin();
  const body = r.pages.length ? h("div", { class: "trash-list" }, r.pages.map((p) => h("div", { class: "trash-row" },
    h("span", { class: "trash-icon" }, p.icon || "📄"),
    h("div", { class: "trash-main" }, h("div", { class: "trash-title" }, p.title || "Без названия"),
      h("div", { class: "muted small" }, `Удалено ${relTime(p.updated_at)}${p.updated_by ? " · " + p.updated_by : ""}`)),
    canWrite() ? h("button", { class: "btn sm", onclick: async () => {
      await run(() => api(`/api/pages/${p.id}/restore`, { json: {} }), "Страница восстановлена");
      await refreshWorkspace();
      renderRoute({ force: true, keepScroll: true });
    } }, icon("undo"), "Восстановить") : null,
    admin ? h("button", { class: "btn sm ghost danger-text", onclick: async () => {
      if (!(await confirmDialog(`Удалить «${p.title || "Без названия"}» навсегда? Вложенные страницы тоже удалятся.`, "Удалить навсегда"))) return;
      await run(() => api(`/api/pages/${p.id}/purge`, { json: {} }), "Удалено навсегда");
      renderRoute({ force: true, keepScroll: true });
    } }, "Удалить навсегда") : null)))
    : emptyState("🗑", "Корзина пуста", "Удалённые страницы попадают сюда — их можно восстановить.");
  return {
    title: "Корзина",
    crumbs: [{ icon: "🗑", label: "Корзина" }],
    body: h("div", { class: "page" }, h("div", { class: "page-head" }, h("div", { class: "page-icon" }, "🗑"), h("h1", { class: "page-title" }, "Корзина")), body),
  };
}

// ---------- импорт и новый проект ----------
async function viewImport() {
  if (!isWsAdmin()) return errorView({ status: 403, message: "Импорт доступен администратору пространства" });
  const ws = S.ws;
  const out = [];
  // загрузка Excel
  const fileInput = h("input", { type: "file", accept: ".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", hidden: true });
  const drop = h("div", { class: "dropzone", tabindex: "0", onclick: () => fileInput.click(), onkeydown: (e) => { if (e.key === "Enter") fileInput.click(); } },
    icon("upload"), h("div", { class: "dz-title" }, "Перетащите .xlsx сюда или нажмите, чтобы выбрать"),
    h("div", { class: "muted small" }, "Колонки ищутся по заголовкам: «Процедура», «Ответственный», «Статус процедуры», «Окончание работы»…"));
  const upload = async (file) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    drop.classList.add("busy");
    try {
      const r = await run(() => api(`/api/w/${S.wid}/upload`, { body: form }), "Таблица загружена 📥");
      await refreshWorkspace();
      importSummary(r.data, r.data.project_id);
    } catch (_) { /* показано */ } finally {
      drop.classList.remove("busy");
      fileInput.value = "";
    }
  };
  fileInput.addEventListener("change", () => upload(fileInput.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files[0]); });
  out.push(h("div", { class: "import-card" }, h("div", { class: "import-head" }, h("span", { class: "import-emoji" }, "📎"),
    h("div", null, h("div", { class: "import-title" }, "Загрузить Excel"),
      h("div", { class: "muted small" }, "Повторная загрузка того же проекта обновит задачи, не потеряв статусы из бота. Файл можно и просто прислать боту."))),
  drop, fileInput));

  // Google Таблица
  const url = h("input", { class: "input", type: "url", placeholder: "https://docs.google.com/spreadsheets/d/…" });
  const email = S.boot.sheets_service_email;
  out.push(h("div", { class: "import-card" }, h("div", { class: "import-head" }, h("span", { class: "import-emoji" }, "🔗"),
    h("div", null, h("div", { class: "import-title" }, "Подключить Google Таблицу"),
      h("div", { class: "muted small" }, `Откройте доступ «Все, у кого есть ссылка — Читатель». Изменения подтягиваются автоматически.`))),
  email ? callout("✍️", h("span", null, "Чтобы статусы из кабинета записывались в таблицу, выдайте доступ «Редактор» адресу ", h("b", { class: "break" }, email)), "blue") : null,
  h("div", { class: "inline grow-first" }, url, h("button", { class: "btn primary", onclick: async () => {
    const r = await run(() => api(`/api/w/${S.wid}/sheet`, { json: { url: url.value } }), "Таблица подключена 🔗");
    await refreshWorkspace();
    importSummary(r.data, r.data.project_id);
  } }, "Подключить"))));

  // пустой проект
  const name = h("input", { class: "input", placeholder: "Например, Открытие шоурума" });
  const date = h("input", { class: "input", type: "date" });
  out.push(h("div", { class: "import-card" }, h("div", { class: "import-head" }, h("span", { class: "import-emoji" }, "✨"),
    h("div", null, h("div", { class: "import-title" }, "Пустой проект"), h("div", { class: "muted small" }, "Задачи добавите на доске или в таблице."))),
  h("div", { class: "grid2" }, field("Название", name), field("Дата мероприятия", date)),
  h("div", { class: "btn-row" }, h("button", { class: "btn primary", onclick: async () => {
    const r = await run(() => api(`/api/w/${S.wid}/projects`, { json: { name: name.value, event_date: date.value || null } }), "Проект создан");
    await refreshWorkspace();
    go(projectHref(r.data.id));
  } }, "Создать проект"))));

  if (ws.archived_projects.length) {
    out.push(h("h2", { class: "h2" }, "🗄 Архив проектов"));
    out.push(h("div", { class: "trash-list" }, ws.archived_projects.map((p) => h("div", { class: "trash-row" },
      h("span", { class: "trash-icon" }, "📁"), h("div", { class: "trash-main" }, h("div", { class: "trash-title" }, p.name),
        h("div", { class: "muted small" }, p.event_date ? fmtDate(p.event_date) : "дата не указана")),
      h("button", { class: "btn sm", onclick: async () => {
        await run(() => api(`/api/projects/${p.id}`, { method: "PATCH", json: { archived: false } }), "Проект возвращён");
        await refreshWorkspace();
        renderRoute({ force: true, keepScroll: true });
      } }, icon("undo"), "Вернуть")))));
  }
  return {
    title: "Импорт и новый проект",
    crumbs: [{ icon: "📥", label: "Импорт и архив" }],
    body: h("div", { class: "page" }, h("div", { class: "page-head" }, h("div", { class: "page-icon" }, "📥"),
      h("h1", { class: "page-title" }, "Новый проект"),
      h("p", { class: "page-lead" }, `Проект появится в пространстве «${ws.workspace.name}».`)), out),
  };
}

function importSummary(data, pid) {
  const warnings = data.warnings || [];
  const close = modal("Готово", h("div", null,
    h("div", { class: "summary-grid" },
      h("div", { class: "stat" }, h("div", { class: "stat-label" }, "Задач"), h("div", { class: "stat-value" }, data.tasks)),
      h("div", { class: "stat" }, h("div", { class: "stat-label" }, "Новых"), h("div", { class: "stat-value" }, data.added)),
      h("div", { class: "stat" }, h("div", { class: "stat-label" }, "Обновлено"), h("div", { class: "stat-value" }, data.updated)),
      data.removed ? h("div", { class: "stat" }, h("div", { class: "stat-label" }, "Убрано"), h("div", { class: "stat-value" }, data.removed)) : null),
    warnings.length ? callout("⚠️", h("div", null, h("b", null, "Обратите внимание"), h("ul", null, warnings.map((w) => h("li", null, w)))), "yellow") : null,
    data.audit && data.audit.length ? h("p", { class: "muted small" }, `Проверка таблицы нашла замечаний: ${data.audit.length}. Подробности — в разделе «Обзор».`) : null,
    h("div", { class: "btn-row end" }, h("button", { class: "btn primary", onclick: () => { close(); S.project = null; go(projectHref(pid)); } }, "Открыть проект"))));
}

// ---------- администрирование: доступ ----------
function randomPassword() {
  const alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => alphabet[b % alphabet.length]).join("");
}

async function viewAdmin(params) {
  if (!isSuperadmin()) return errorView({ status: 403, message: "Раздел доступен главному администратору" });
  const data = await api("/api/admin");
  const tab = params.tab === "workspaces" ? "workspaces" : "users";
  const tabs = h("div", { class: "db-bar" }, h("div", { class: "db-tabs" },
    h("a", { class: "db-tab" + (tab === "users" ? " active" : ""), href: "#/admin/users" }, icon("users"), h("span", null, `Пользователи · ${data.users.length}`)),
    h("a", { class: "db-tab" + (tab === "workspaces" ? " active" : ""), href: "#/admin/workspaces" }, icon("workspace"), h("span", null, `Пространства · ${data.workspaces.length}`))),
  h("div", { class: "db-tools" }, tab === "users"
    ? h("button", { class: "btn primary sm", onclick: () => userModal(null, data) }, "Добавить пользователя")
    : h("button", { class: "btn primary sm", onclick: () => workspaceModal(null) }, "Новое пространство")));
  const body = tab === "users" ? usersTable(data) : workspacesTable(data);
  return {
    title: "Доступ",
    crumbs: [{ icon: "🔒", label: "Доступ" }, { label: tab === "users" ? "Пользователи" : "Пространства" }],
    body: h("div", { class: "page full" }, h("div", { class: "page-head" }, h("div", { class: "page-icon" }, "🔒"), h("h1", { class: "page-title" }, "Доступ"),
      h("p", { class: "page-lead" }, "Кто может входить в кабинет и что видит в каждом пространстве. Люди видят только те пространства, куда вы их добавили.")),
    tabs, h("div", { class: "section-body" }, body)),
  };
}

function usersTable(data) {
  const wsById = Object.fromEntries(data.workspaces.map((w) => [w.id, w]));
  const memberById = Object.fromEntries(data.members.map((m) => [m.id, m]));
  return h("div", { class: "table-wrap" }, h("table", { class: "db-table" },
    h("colgroup", null, [260, 300, 190, 170, 120].map((w) => h("col", { style: { width: w + "px" } }))),
    h("thead", null, h("tr", null, [["user", "Пользователь"], ["workspace", "Пространства"], ["phone", "Telegram"], ["lock", "Вход"], ["status", "Статус"]]
      .map(([ic, label]) => h("th", null, h("div", { class: "th" }, icon(ic), h("span", null, label)))))),
    h("tbody", null, data.users.map((u) => {
      const m = u.member_id ? memberById[u.member_id] : null;
      const roles = Object.entries(u.roles || {}).map(([wid, r]) => wsById[wid] ? tag(`${wsById[wid].icon || ""} ${wsById[wid].name}: ${ROLE_LABEL[r]}`.trim(),
        r === "admin" ? "purple" : r === "member" ? "blue" : "gray", "sm") : null);
      return h("tr", { onclick: () => userModal(u, data), class: u.active ? "" : "closed" },
        h("td", null, h("div", { class: "cell-inner" }, h("span", { class: "person" }, avatar(u.name, u.member_id || -u.id, "sm"),
          h("span", null, h("b", null, u.name), h("span", { class: "muted small block" }, u.login))))),
        h("td", null, h("div", { class: "cell-inner wrap" }, u.is_superadmin ? tag("Главный администратор", "red", "sm") : null,
          roles.length ? roles : (!u.is_superadmin ? h("span", { class: "muted" }, "нет доступа") : null))),
        h("td", null, h("div", { class: "cell-inner" }, m ? h("span", null, m.name, m.connected ? "" : h("span", { class: "muted small" }, " · не в боте")) : h("span", { class: "muted" }, "—"))),
        h("td", null, h("div", { class: "cell-inner" }, u.has_password ? (u.must_change_password ? tag("временный пароль", "yellow", "sm") : tag("пароль задан", "green", "sm"))
          : tag("только Telegram", "gray", "sm"))),
        h("td", null, h("div", { class: "cell-inner" }, u.active ? statusPill("green", "Активен") : statusPill("default", "Отключён"))));
    }))));
}

function userModal(u, data) {
  const isNew = !u;
  const linkedMembers = new Set(data.users.filter((x) => x.member_id && (!u || x.id !== u.id)).map((x) => x.member_id));
  const f = {
    name: h("input", { class: "input", value: u ? u.name : "", placeholder: "Имя и фамилия" }),
    login: h("input", { class: "input", value: u ? u.login : "", placeholder: "латиницей, например ivan", autocapitalize: "none", spellcheck: "false" }),
    member_id: h("select", { class: "input" }, h("option", { value: "" }, "— не связан —"),
      data.members.filter((m) => m.active || (u && u.member_id === m.id)).map((m) => h("option", { value: m.id, selected: u && u.member_id === m.id,
        disabled: linkedMembers.has(m.id) }, m.name + (m.username ? ` (@${m.username})` : "") + (linkedMembers.has(m.id) ? " — уже связан" : "")))),
  };
  const superadmin = h("input", { type: "checkbox", checked: u && u.is_superadmin });
  const active = h("input", { type: "checkbox", checked: !u || u.active });
  const password = h("input", { class: "input mono", value: isNew ? randomPassword() : "", placeholder: "не менять" });
  f.member_id.addEventListener("change", () => {
    const m = data.members.find((x) => String(x.id) === f.member_id.value);
    if (m && !f.name.value) f.name.value = m.name;
    if (m && !f.login.value && m.username) f.login.value = m.username.toLowerCase();
  });
  const roleSelects = data.workspaces.map((w) => {
    const sel = h("select", { class: "input" }, [["", "Нет доступа"], ["viewer", "Наблюдатель"], ["member", "Участник"], ["admin", "Администратор"]]
      .map(([v, label]) => h("option", { value: v, selected: ((u && u.roles[w.id]) || "") === v }, label)));
    return [w, sel];
  });
  const body = h("div", { class: "user-form" },
    h("div", { class: "grid2" }, field("Имя", f.name), field("Логин", f.login)),
    field("Участник команды в Telegram", f.member_id, "Связанный аккаунт входит из бота без пароля и видит свои задачи"),
    h("div", { class: "form-section" }, "Доступ к пространствам"),
    h("div", { class: "role-list" }, roleSelects.map(([w, sel]) => h("label", { class: "role-row" }, wsBadge(w, "sm"), h("span", { class: "grow" }, w.name), sel))),
    h("p", { class: "muted small" }, "Администратор ведёт проекты и любые задачи, участник меняет свои задачи и пишет страницы, наблюдатель только смотрит."),
    h("div", { class: "form-section" }, "Права и вход"),
    h("label", { class: "check" }, superadmin, h("span", null, h("b", null, "Главный администратор"), h("span", { class: "muted small block" }, "Управляет пользователями, пространствами, командой и напоминаниями; видит все пространства"))),
    h("label", { class: "check" }, active, h("span", null, h("b", null, "Аккаунт активен"), h("span", { class: "muted small block" }, "Отключённый человек сразу теряет доступ"))),
    isNew ? field("Временный пароль", h("div", { class: "inline grow-first" }, password,
      h("button", { class: "btn sm", type: "button", onclick: () => { password.value = randomPassword(); } }, icon("sync"))),
    "При первом входе человек сменит его на свой. Можно оставить пустым, если вход только из Telegram") : null,
    !isNew ? h("div", { class: "inline" }, h("button", { class: "btn sm", onclick: () => resetPassword(u) }, icon("lock"), "Сбросить пароль"),
      h("span", { class: "muted small" }, u.has_password ? "Выдаст новый временный пароль" : "Пароль ещё не задан — задайте, чтобы человек мог войти из браузера")) : null);
  const save = h("button", { class: "btn primary" }, isNew ? "Создать" : "Сохранить");
  const close = modal(isNew ? "Новый пользователь" : u.name, h("div", null, body,
    h("div", { class: "btn-row end" }, h("button", { class: "btn ghost", onclick: () => close() }, "Отмена"), save)), { wide: true });
  save.addEventListener("click", async () => {
    const payload = {
      name: f.name.value, login: f.login.value, member_id: f.member_id.value ? Number(f.member_id.value) : null,
      is_superadmin: superadmin.checked, active: active.checked,
      roles: Object.fromEntries(roleSelects.map(([w, sel]) => [w.id, sel.value])),
    };
    if (isNew) payload.password = password.value.trim();
    save.disabled = true;
    try {
      const r = await run(() => (isNew ? api("/api/admin/users", { json: payload })
        : api(`/api/admin/users/${u.id}`, { method: "PATCH", json: payload })), isNew ? "Пользователь создан" : "Сохранено");
      close();
      if (r.data.id === S.boot.me.id) S.boot = await api("/api/bootstrap");
      if (isNew && payload.password) credentialsModal(r.data, payload.password);
      renderRoute({ force: true, keepScroll: true });
    } catch (_) { save.disabled = false; }
  });
}

async function resetPassword(u) {
  if (!(await confirmDialog(`Выдать ${u.name} новый временный пароль? Старый перестанет работать.`, "Сбросить", false))) return;
  const r = await run(() => api(`/api/admin/users/${u.id}/password`, { json: {} }), "Пароль сброшен");
  closeAllOverlays();
  credentialsModal(r.data.user, r.data.password);
  renderRoute({ force: true, keepScroll: true });
}

function credentialsModal(u, password) {
  const link = location.origin + location.pathname;
  const text = `Кабинет IAC PM: ${link}\nЛогин: ${u.login}\nВременный пароль: ${password}\nПри первом входе нужно задать свой пароль.`;
  modal("Данные для входа", h("div", null,
    h("p", { class: "muted" }, "Передайте человеку эти данные. Пароль показывается один раз."),
    h("div", { class: "creds" },
      h("div", null, h("span", { class: "muted small" }, "Адрес"), h("div", { class: "mono break" }, link)),
      h("div", null, h("span", { class: "muted small" }, "Логин"), h("div", { class: "mono" }, u.login)),
      h("div", null, h("span", { class: "muted small" }, "Временный пароль"), h("div", { class: "mono big" }, password))),
    h("div", { class: "btn-row end" }, h("button", { class: "btn primary", onclick: () => copyText(text) }, icon("copy"), "Скопировать всё"))));
}

function workspacesTable(data) {
  const counts = {};
  for (const u of data.users) for (const wid of Object.keys(u.roles || {})) counts[wid] = (counts[wid] || 0) + 1;
  return h("div", { class: "ws-list" }, data.workspaces.map((w) => h("div", { class: "ws-row", onclick: () => workspaceModal(w) },
    wsBadge(w, "lg"), h("div", { class: "grow" }, h("div", { class: "ws-row-title" }, w.name),
      h("div", { class: "muted small" }, `${counts[w.id] || 0} ${plural(counts[w.id] || 0, "человек", "человека", "человек")} с доступом`)),
    h("a", { class: "btn sm ghost", href: `#/w/${w.id}`, onclick: (e) => e.stopPropagation() }, "Открыть"),
    h("button", { class: "icon-btn", onclick: (e) => { e.stopPropagation(); workspaceModal(w); } }, icon("edit")))));
}

function workspaceModal(w) {
  const isNew = !w;
  let emoji = w ? w.icon : "🏢";
  const iconBtn = h("button", { class: "emoji-pick", type: "button", onclick: (e) => emojiPicker(e.currentTarget, (x) => { emoji = x; iconBtn.textContent = x || "🏢"; }, true) }, emoji || "🏢");
  const name = h("input", { class: "input", value: w ? w.name : "", placeholder: "Например, Ивенты 2026" });
  const close = modal(isNew ? "Новое пространство" : "Пространство", h("div", null,
    h("div", { class: "inline grow-last" }, iconBtn, name),
    h("p", { class: "muted small" }, isNew ? "После создания добавьте в него людей в разделе «Пользователи»." : ""),
    h("div", { class: "btn-row end" }, h("button", { class: "btn ghost", onclick: () => close() }, "Отмена"),
      h("button", { class: "btn primary", onclick: async () => {
        await run(() => (isNew ? api("/api/admin/workspaces", { json: { name: name.value, icon: emoji } })
          : api(`/api/admin/workspaces/${w.id}`, { method: "PATCH", json: { name: name.value, icon: emoji } })), "Сохранено");
        close();
        S.boot = await api("/api/bootstrap");
        renderRoute({ force: true, keepScroll: true });
      } }, isNew ? "Создать" : "Сохранить"))));
}

// ---------- команда ----------
async function viewTeam() {
  if (!isSuperadmin()) return errorView({ status: 403, message: "Раздел доступен главному администратору" });
  S.boot = await api("/api/bootstrap");
  const members = S.boot.members;
  const flags = (m) => [m.is_admin ? tag("админ", "purple", "sm") : null, m.is_pm ? tag("PM", "blue", "sm") : null,
    m.is_observer ? tag("наблюдатель", "gray", "sm") : null];
  return {
    title: "Команда",
    crumbs: [{ icon: "👥", label: "Команда" }],
    body: h("div", { class: "page full" },
      h("div", { class: "page-head" }, h("div", { class: "page-icon" }, "👥"), h("h1", { class: "page-title" }, "Команда"),
        h("p", { class: "page-lead" }, "Участники, которым бот пишет в Telegram. Имя должно совпадать с колонкой «Ответственный» в таблице.")),
      h("div", { class: "db-bar" }, h("div", { class: "db-tabs" }, h("span", { class: "db-tab active" }, icon("table"), h("span", null, `Участники · ${members.filter((m) => m.active).length}`))),
        h("div", { class: "db-tools" }, h("button", { class: "btn primary sm", onclick: () => memberModal(null) }, "Добавить участника"))),
      h("div", { class: "table-wrap" }, h("table", { class: "db-table" },
        h("colgroup", null, [240, 200, 160, 160, 220].map((w) => h("col", { style: { width: w + "px" } }))),
        h("thead", null, h("tr", null, [["user", "Имя"], ["text", "Роль"], ["phone", "Telegram"], ["status", "Бот"], ["tag", "Права"]]
          .map(([ic, label]) => h("th", null, h("div", { class: "th" }, icon(ic), h("span", null, label)))))),
        h("tbody", null, members.map((m) => h("tr", { class: m.active ? "" : "closed", onclick: () => memberModal(m) },
          h("td", null, h("div", { class: "cell-inner" }, h("span", { class: "person" }, avatar(m.name, m.id, "xs"), m.name))),
          h("td", null, h("div", { class: "cell-inner muted" }, m.role || "")),
          h("td", null, h("div", { class: "cell-inner" }, m.username ? "@" + m.username : h("span", { class: "muted" }, "—"))),
          h("td", null, h("div", { class: "cell-inner" }, !m.active ? statusPill("default", "неактивен")
            : m.connected ? statusPill("green", "подключён") : statusPill("yellow", "не нажал Start"))),
          h("td", null, h("div", { class: "cell-inner wrap" }, flags(m),
            m.assists_id && member(m.assists_id) ? h("span", { class: "muted small" }, "помогает: " + member(m.assists_id).name) : null)))))))),
  };
}

function memberModal(m) {
  const f = {
    name: h("input", { class: "input", value: m ? m.name : "", placeholder: "Как в колонке «Ответственный»" }),
    username: h("input", { class: "input", value: m ? m.username : "", placeholder: "ник без @" }),
    role: h("input", { class: "input", value: m ? m.role : "", placeholder: "Например, дизайнер" }),
    aliases: h("input", { class: "input", value: m ? m.aliases : "", placeholder: "Другие написания через запятую" }),
    phone: h("input", { class: "input", type: "tel", value: m ? m.phone : "" }),
    assists_id: h("select", { class: "input" }, h("option", { value: "" }, "— ни за кого —"),
      S.boot.members.filter((x) => x.active && (!m || x.id !== m.id)).map((x) => h("option", { value: x.id, selected: m && m.assists_id === x.id }, x.name))),
  };
  const checks = {
    is_admin: [h("input", { type: "checkbox", checked: m && m.is_admin }), "Администратор в боте", "Загрузка таблиц и любые правки из Telegram"],
    is_pm: [h("input", { type: "checkbox", checked: m && m.is_pm }), "PM", "Получает эскалации и утреннюю сводку"],
    is_observer: [h("input", { type: "checkbox", checked: m && m.is_observer }), "Наблюдатель", "Видит весь проект и получает сводку, ничего не меняет"],
    active: [h("input", { type: "checkbox", checked: !m || m.active }), "Активен", "Неактивным бот не пишет"],
  };
  const close = modal(m ? m.name : "Новый участник", h("div", null,
    h("div", { class: "grid2" }, field("Имя", f.name), field("Telegram-ник", f.username)),
    h("div", { class: "grid2" }, field("Роль", f.role), field("Телефон (для обзвона)", f.phone)),
    field("Другие написания", f.aliases),
    field("Выполняет задачи за", f.assists_id, "Получит те же задачи и напоминания"),
    Object.values(checks).map(([input, title, hint]) => h("label", { class: "check" }, input, h("span", null, h("b", null, title), h("span", { class: "muted small block" }, hint)))),
    h("div", { class: "btn-row end" }, h("button", { class: "btn ghost", onclick: () => close() }, "Отмена"),
      h("button", { class: "btn primary", onclick: async () => {
        const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
        Object.assign(body, { id: m ? m.id : null }, Object.fromEntries(Object.entries(checks).map(([k, [input]]) => [k, input.checked])));
        await run(() => api("/api/members", { json: body }), "Сохранено");
        close();
        renderRoute({ force: true, keepScroll: true });
      } }, "Сохранить"))), { wide: true });
}

// ---------- напоминания ----------
async function viewReminders() {
  if (!isSuperadmin()) return errorView({ status: 403, message: "Раздел доступен главному администратору" });
  S.boot = await api("/api/bootstrap");
  const s = S.boot.settings;
  const f = {
    morning_time: h("input", { class: "input", type: "time", value: s.morning_time }),
    pm_time: h("input", { class: "input", type: "time", value: s.pm_time }),
    evening_time: h("input", { class: "input", type: "time", value: s.evening_time }),
    standup_time: h("input", { class: "input", type: "time", value: s.standup_time }),
    checks_per_day: h("input", { class: "input", type: "number", min: 1, max: 20, value: s.checks_per_day }),
    sync_minutes: h("input", { class: "input", type: "number", min: 0, max: 1440, value: s.sync_minutes }),
    weekly_day: h("select", { class: "input" }, WEEKDAY_NAMES.map((n, i) => h("option", { value: i, selected: String(i) === String(s.weekly_day) }, n))),
  };
  return {
    title: "Напоминания",
    crumbs: [{ icon: "🔔", label: "Напоминания" }],
    body: h("div", { class: "page" },
      h("div", { class: "page-head" }, h("div", { class: "page-icon" }, "🔔"), h("h1", { class: "page-title" }, "Напоминания"),
        h("p", { class: "page-lead" }, "Когда бот пишет команде в Telegram. Время — по часовому поясу сервера.")),
      h("div", { class: "settings" },
        settingsRow("Утро", "Список задач на день и вопрос «выполнено?» по вчерашним срокам", f.morning_time),
        settingsRow("Сводка PM", "Сводка руководителю, наблюдателям и в рабочую группу", f.pm_time),
        settingsRow("Вечер", "«Успеваете?» по задачам со сроком завтра", f.evening_time),
        settingsRow("Статус-созвон", "Напоминание о созвоне; пусто — не напоминать", f.standup_time),
        settingsRow("Вопросов «выполнено?» в день", "Сколько задач максимум спрашивать у человека за раз", f.checks_per_day),
        settingsRow("Синхронизация с Google, мин", "0 — не синхронизировать автоматически", f.sync_minutes),
        settingsRow("Недельный обзор", "День, когда бот напомнит про задачи без срока", f.weekly_day),
        callout(s.group_chat_id ? "✅" : "💬", s.group_chat_id ? "Рабочая группа подключена — сводки и закрытые задачи уходят туда."
          : "Группа не подключена: добавьте бота в рабочую группу и отправьте там /bind_group.", s.group_chat_id ? "green" : "gray"),
        h("div", { class: "btn-row" }, h("button", { class: "btn primary", onclick: async () => {
          const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
          await run(() => api("/api/settings", { json: body }), "Сохранено");
          S.boot = await api("/api/bootstrap");
        } }, "Сохранить")))),
  };
}

// ---------- аккаунт ----------
async function viewAccount() {
  const u = S.boot.me;
  const current = h("input", { class: "input", type: "password", autocomplete: "current-password", placeholder: u.has_password ? "" : "пароль ещё не задан" });
  const next = h("input", { class: "input", type: "password", autocomplete: "new-password" });
  const repeat = h("input", { class: "input", type: "password", autocomplete: "new-password" });
  const theme = store("theme") || "system";
  const themeSel = h("select", { class: "input", onchange: (e) => { store("theme", e.target.value); applyTheme(); } },
    [["system", "Как в системе"], ["light", "Светлая"], ["dark", "Тёмная"]].map(([v, label]) => h("option", { value: v, selected: theme === v }, label)));
  const workspaces = S.boot.workspaces.map((w) => h("div", { class: "role-row" }, wsBadge(w, "sm"), h("span", { class: "grow" }, w.name), tag(ROLE_LABEL[w.role] || w.role, "gray", "sm")));
  return {
    title: "Мой аккаунт",
    crumbs: [{ icon: "👤", label: "Мой аккаунт" }],
    body: h("div", { class: "page" },
      h("div", { class: "page-head" }, h("div", { class: "page-icon" }, avatar(u.name, myMemberId(), "xl")), h("h1", { class: "page-title" }, u.name),
        h("p", { class: "page-lead" }, `Логин: ${u.login}`, u.is_superadmin ? " · главный администратор" : "")),
      h("div", { class: "settings" },
        h("h2", { class: "h2" }, "Профиль"),
        settingsRow("Telegram", u.member ? `Связан с участником «${u.member.name}»${u.member.username ? " (@" + u.member.username + ")" : ""}. Из бота вход без пароля.`
          : "Не связан. Попросите администратора связать аккаунт, чтобы входить из бота и видеть свои задачи.", null),
        settingsRow("Оформление", "Светлая или тёмная тема на этом устройстве", themeSel),
        h("h2", { class: "h2" }, "Пространства"),
        h("div", { class: "role-list" }, workspaces),
        h("h2", { class: "h2" }, "Пароль"),
        h("p", { class: "muted small" }, u.has_password ? "Смена пароля завершит вход на других устройствах." : "Пароль ещё не задан — его выдаёт администратор."),
        u.has_password ? h("div", { class: "pw-form" }, field("Текущий пароль", current), field("Новый пароль", next, "Минимум 8 символов"), field("Ещё раз", repeat),
          h("div", { class: "btn-row" }, h("button", { class: "btn primary", onclick: async () => {
            if (next.value !== repeat.value) { toast("Пароли не совпадают", "error"); return; }
            await run(() => api("/api/auth/password", { json: { current: current.value, new: next.value } }), "Пароль изменён");
            current.value = next.value = repeat.value = "";
          } }, "Сменить пароль"))) : null,
        h("h2", { class: "h2" }, "Выход"),
        settingsRow("Выйти из кабинета", "На этом устройстве", h("button", { class: "btn", onclick: logout }, icon("logout"), "Выйти")))),
  };
}

start();
