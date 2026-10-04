"use strict";
/* Блочный редактор страниц в стиле Notion.
 * Модель — массив блоков {id, type, rich|text, indent, checked, open, icon, page_id}.
 * Текст хранится сегментами {t, b, i, u, s, c, href} и рисуется через DOM — без innerHTML. */

const BLOCK_DEFS = [
  { type: "p", label: "Текст", icon: "Aa", hint: "Обычный абзац" },
  { type: "h1", label: "Заголовок 1", icon: "H1", hint: "#" },
  { type: "h2", label: "Заголовок 2", icon: "H2", hint: "##" },
  { type: "h3", label: "Заголовок 3", icon: "H3", hint: "###" },
  { type: "todo", label: "Чек-лист", icon: "☑", hint: "[]" },
  { type: "bullet", label: "Маркированный список", icon: "•", hint: "-" },
  { type: "number", label: "Нумерованный список", icon: "1.", hint: "1." },
  { type: "toggle", label: "Раскрывающийся список", icon: "▸", hint: ">" },
  { type: "quote", label: "Цитата", icon: "❝", hint: "\"" },
  { type: "callout", label: "Выноска", icon: "💡", hint: "" },
  { type: "code", label: "Код", icon: "</>", hint: "```" },
  { type: "divider", label: "Разделитель", icon: "—", hint: "---" },
  { type: "page", label: "Страница", icon: "📄", hint: "вложенная" },
];
const EDITOR_TEXT_TYPES = new Set(["p", "h1", "h2", "h3", "bullet", "number", "todo", "quote", "callout", "toggle"]);
const LIST_TYPES = new Set(["bullet", "number", "todo", "toggle"]);
const PLACEHOLDERS = {
  p: "Введите текст или «/» для команд", h1: "Заголовок 1", h2: "Заголовок 2", h3: "Заголовок 3",
  bullet: "Список", number: "Список", todo: "Задача", toggle: "Раскрывающийся список", quote: "Цитата", callout: "Выноска",
};
const MD_PREFIX = {
  "# ": "h1", "## ": "h2", "### ": "h3", "- ": "bullet", "* ": "bullet", "• ": "bullet", "1. ": "number",
  "1) ": "number", "[] ": "todo", "[ ] ": "todo", "[x] ": "todo", "> ": "toggle", "\" ": "quote", "« ": "quote",
};
const MARK_KEYS = ["b", "i", "u", "s", "c", "href"];

// ---------- размеченный текст ----------
function richLen(rich) { return rich.reduce((n, s) => n + s.t.length, 0); }
function richText(rich) { return rich.map((s) => s.t).join(""); }
function sameMarks(a, b) { return MARK_KEYS.every((k) => (a[k] || 0) === (b[k] || 0)); }

function normRich(rich) {
  const out = [];
  for (const seg of rich) {
    if (!seg.t) continue;
    const last = out[out.length - 1];
    if (last && sameMarks(last, seg)) last.t += seg.t;
    else out.push({ ...seg });
  }
  return out;
}

function sliceRich(rich, start, end = Infinity) {
  const out = [];
  let pos = 0;
  for (const seg of rich) {
    const segEnd = pos + seg.t.length;
    const a = Math.max(start, pos);
    const b = Math.min(end, segEnd);
    if (a < b) out.push({ ...seg, t: seg.t.slice(a - pos, b - pos) });
    pos = segEnd;
  }
  return normRich(out);
}

function concatRich(a, b) { return normRich([...a, ...b]); }

function renderRich(el, rich) {
  el.replaceChildren();
  for (const seg of rich) {
    let node = document.createTextNode(seg.t);
    for (const [mark, tagName] of [["c", "code"], ["s", "s"], ["u", "u"], ["i", "i"], ["b", "b"]]) {
      if (seg[mark]) { const w = document.createElement(tagName); w.append(node); node = w; }
    }
    if (seg.href) {
      const a = document.createElement("a");
      a.href = seg.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.append(node);
      node = a;
    }
    el.append(node);
  }
}

function readRich(root) {
  const out = [];
  const walk = (node, marks) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const t = node.data.replace(/​/g, "");
      if (t) out.push({ t, ...marks });
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const tagName = node.tagName;
    if (tagName === "BR") {
      if (node !== root.lastChild) out.push({ t: "\n", ...marks });
      return;
    }
    const m = { ...marks };
    if (tagName === "B" || tagName === "STRONG") m.b = 1;
    if (tagName === "I" || tagName === "EM") m.i = 1;
    if (tagName === "U") m.u = 1;
    if (tagName === "S" || tagName === "STRIKE" || tagName === "DEL") m.s = 1;
    if (tagName === "CODE") m.c = 1;
    if (tagName === "A" && node.getAttribute("href")) m.href = node.getAttribute("href");
    const st = node.style;
    if (st) {
      if (st.fontWeight === "bold" || Number(st.fontWeight) >= 600) m.b = 1;
      if (st.fontStyle === "italic") m.i = 1;
      const deco = `${st.textDecoration || ""} ${st.textDecorationLine || ""}`;
      if (deco.includes("underline")) m.u = 1;
      if (deco.includes("line-through")) m.s = 1;
    }
    if ((tagName === "DIV" || tagName === "P") && out.length) out.push({ t: "\n" });
    node.childNodes.forEach((c) => walk(c, m));
  };
  root.childNodes.forEach((c) => walk(c, {}));
  return normRich(out);
}

// ---------- каретка ----------
function selectionOffsets(el) {
  const sel = window.getSelection();
  if (!sel.rangeCount) return [0, 0];
  const r = sel.getRangeAt(0);
  if (!el.contains(r.startContainer)) return [0, 0];
  const pre = document.createRange();
  pre.selectNodeContents(el);
  pre.setEnd(r.startContainer, r.startOffset);
  const start = pre.toString().length;
  pre.setEnd(r.endContainer, r.endOffset);
  return [start, pre.toString().length];
}

function setCaret(el, offset) {
  el.focus({ preventScroll: true });
  const sel = window.getSelection();
  const range = document.createRange();
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let node;
  let remaining = offset === "end" ? Infinity : offset;
  let last = null;
  while ((node = walker.nextNode())) {
    last = node;
    if (remaining <= node.data.length) {
      range.setStart(node, remaining);
      range.collapse(true);
      sel.removeAllRanges();
      sel.addRange(range);
      return;
    }
    remaining -= node.data.length;
  }
  if (last) range.setStart(last, last.data.length);
  else range.selectNodeContents(el);
  range.collapse(!last);
  if (last) range.collapse(true);
  sel.removeAllRanges();
  sel.addRange(range);
}

function caretRect() {
  const sel = window.getSelection();
  if (!sel.rangeCount) return null;
  const range = sel.getRangeAt(0).cloneRange();
  range.collapse(true);
  const rects = range.getClientRects();
  return rects.length ? rects[0] : null;
}

function parseLine(line) {
  for (const [prefix, type] of Object.entries(MD_PREFIX)) {
    if (line.startsWith(prefix)) return { type, text: line.slice(prefix.length), checked: prefix === "[x] " };
  }
  return { type: "p", text: line };
}

// ---------- редактор ----------
class BlockEditor {
  constructor(root, opts) {
    this.root = root;
    this.opts = opts;
    this.editable = !!opts.editable;
    this.blocks = (opts.blocks || []).map((b) => JSON.parse(JSON.stringify(b)));
    if (!this.blocks.length) this.blocks.push(this.newBlock("p"));
    this.rows = new Map();
    this.slash = null;
    this.toolbar = null;
    this.onSelection = () => this.updateToolbar();
    if (this.editable) document.addEventListener("selectionchange", this.onSelection);
    this.render();
  }

  destroy() {
    document.removeEventListener("selectionchange", this.onSelection);
    this.closeSlash();
    this.hideToolbar();
  }

  newBlock(type, extra = {}) {
    const b = { id: uid(), type, ...extra };
    if (EDITOR_TEXT_TYPES.has(type) && !b.rich) b.rich = [];
    if (type === "code" && b.text === undefined) b.text = "";
    if (type === "callout" && !b.icon) b.icon = "💡";
    if (type === "toggle" && b.open === undefined) b.open = true;
    return b;
  }

  getBlocks() { return JSON.parse(JSON.stringify(this.blocks)); }
  emit() { if (this.editable && this.opts.onChange) this.opts.onChange(this.getBlocks()); }
  index(b) { return this.blocks.indexOf(b); }
  byId(id) { return this.blocks.find((b) => b.id === id); }
  textEl(b) { const row = this.rows.get(b.id); return row && row.querySelector(".blk-text"); }

  // ---------- отрисовка ----------
  render() {
    this.root.replaceChildren();
    this.rows.clear();
    this.root.classList.add("editor");
    this.root.classList.toggle("readonly", !this.editable);
    for (const b of this.blocks) this.root.append(this.renderBlock(b));
    if (this.editable) {
      this.root.append(h("div", { class: "editor-tail", onclick: () => this.focusTail() }));
    }
    this.refresh();
  }

  rerender(b) {
    const old = this.rows.get(b.id);
    const row = this.renderBlock(b);
    if (old) old.replaceWith(row);
    this.refresh();
    return row;
  }

  insertAfter(ref, b) {
    const i = ref ? this.index(ref) : this.blocks.length - 1;
    this.blocks.splice(i + 1, 0, b);
    const row = this.renderBlock(b);
    const refRow = ref ? this.rows.get(ref.id) : null;
    if (refRow) refRow.after(row);
    else this.root.insertBefore(row, this.root.querySelector(".editor-tail"));
    this.refresh();
    return b;
  }

  remove(b) {
    const row = this.rows.get(b.id);
    if (row) row.remove();
    this.rows.delete(b.id);
    this.blocks.splice(this.index(b), 1);
    if (!this.blocks.length) this.insertAfter(null, this.newBlock("p"));
    this.refresh();
  }

  refresh() {
    // нумерация списков и скрытие содержимого свёрнутых блоков
    const counters = [];
    let hideBelow = Infinity;
    for (const b of this.blocks) {
      const row = this.rows.get(b.id);
      if (!row) continue;
      const indent = b.indent || 0;
      if (indent <= hideBelow) hideBelow = Infinity;
      row.hidden = indent > hideBelow;
      if (b.type === "toggle" && !b.open && hideBelow === Infinity) hideBelow = indent;
      counters.length = indent + 1;
      if (b.type === "number") {
        counters[indent] = (counters[indent] || 0) + 1;
        const marker = row.querySelector(".blk-marker");
        if (marker) marker.textContent = counters[indent] + ".";
      } else {
        counters[indent] = 0;
      }
    }
  }

  renderBlock(b) {
    const row = h("div", { class: `blk blk-${b.type}` + (b.type === "todo" && b.checked ? " checked" : ""), dataset: { id: b.id } });
    row.style.marginLeft = (b.indent || 0) * 26 + "px";
    this.rows.set(b.id, row);
    if (this.editable) {
      row.append(h("div", { class: "blk-handle" },
        h("button", { class: "blk-btn", title: "Добавить блок ниже", onmousedown: (e) => e.preventDefault(), onclick: () => this.addBelow(b) }, icon("plus")),
        h("button", { class: "blk-btn drag", title: "Перетащите или нажмите для меню", onpointerdown: (e) => this.startDrag(e, b) }, icon("drag"))));
    }
    if (b.type === "divider") {
      row.append(h("div", { class: "blk-body" }, h("hr")));
      return row;
    }
    if (b.type === "page") {
      const info = this.opts.pageInfo ? this.opts.pageInfo(b.page_id) : null;
      row.append(h("div", { class: "blk-body" }, h("a", {
        class: "blk-pagelink" + (info ? "" : " missing"), href: "#",
        onclick: (e) => { e.preventDefault(); if (info && this.opts.onOpenPage) this.opts.onOpenPage(b.page_id); },
      }, h("span", { class: "pl-icon" }, (info && info.icon) || "📄"), h("span", { class: "pl-title" }, info ? (info.title || "Без названия") : "Страница удалена"))));
      return row;
    }
    if (b.type === "code") {
      const pre = h("div", { class: "blk-text blk-code-text", spellcheck: "false" });
      pre.textContent = b.text || "";
      this.bindText(pre, b);
      row.append(h("div", { class: "blk-body" }, pre));
      return row;
    }
    const text = h("div", { class: "blk-text", "data-placeholder": PLACEHOLDERS[b.type] || "" });
    renderRich(text, b.rich || []);
    this.bindText(text, b);
    let marker = null;
    if (b.type === "bullet") marker = h("span", { class: "blk-marker" }, ["•", "◦", "▪"][(b.indent || 0) % 3]);
    if (b.type === "number") marker = h("span", { class: "blk-marker" }, "1.");
    if (b.type === "todo") {
      marker = h("span", { class: "blk-marker" }, h("input", {
        type: "checkbox", checked: b.checked, disabled: !this.editable,
        onchange: (e) => { b.checked = e.target.checked; row.classList.toggle("checked", b.checked); this.emit(); },
      }));
    }
    if (b.type === "toggle") {
      marker = h("button", { class: "blk-marker toggle-arrow" + (b.open ? " open" : ""), onmousedown: (e) => e.preventDefault(),
        onclick: (e) => { b.open = !b.open; e.currentTarget.classList.toggle("open", b.open); this.refresh(); this.emit(); } }, icon("chevron"));
    }
    if (b.type === "callout") {
      marker = h("button", { class: "blk-marker callout-icon", disabled: !this.editable,
        onclick: (e) => emojiPicker(e.currentTarget, (emoji) => { b.icon = emoji || "💡"; this.rerender(b); this.emit(); }) }, b.icon || "💡");
    }
    row.append(h("div", { class: "blk-body" }, marker, text));
    return row;
  }

  bindText(el, b) {
    if (!this.editable) return;
    el.contentEditable = b.type === "code" ? "plaintext-only" : "true";
    if (b.type === "code" && el.contentEditable !== "plaintext-only") el.contentEditable = "true";
    el.addEventListener("input", (e) => this.onInput(e, b, el));
    el.addEventListener("keydown", (e) => this.onKey(e, b, el));
    el.addEventListener("paste", (e) => this.onPaste(e, b, el));
    el.addEventListener("blur", () => setTimeout(() => { if (this.slash && !this.slash.el.contains(document.activeElement)) this.closeSlash(); }, 150));
  }

  sync(b, el) {
    if (b.type === "code") b.text = el.textContent;
    else if (EDITOR_TEXT_TYPES.has(b.type)) b.rich = readRich(el);
  }

  focus(b, offset = "end") {
    const el = this.textEl(b);
    if (el) setCaret(el, offset);
  }

  focusTail() {
    const last = this.blocks[this.blocks.length - 1];
    if (last && last.type === "p" && !richLen(last.rich)) { this.focus(last); return; }
    this.focus(this.insertAfter(last, this.newBlock("p")), 0);
  }

  focusFirst() { this.focus(this.blocks[0], 0); }

  addBelow(b) {
    let target = b;
    if (!(b.type === "p" && !richLen(b.rich || []))) target = this.insertAfter(b, this.newBlock("p", { indent: b.indent || 0 }));
    this.focus(target, 0);
    document.execCommand("insertText", false, "/");
  }

  // ---------- ввод ----------
  onInput(e, b, el) {
    this.sync(b, el);
    if (b.type === "p") {
      const [off] = selectionOffsets(el);
      const text = richText(b.rich);
      const prefix = text.slice(0, off);
      if (MD_PREFIX[prefix]) {
        this.convert(b, MD_PREFIX[prefix], prefix.length, { checked: prefix === "[x] " });
        return;
      }
      if (text === "---") {
        b.type = "divider";
        delete b.rich;
        this.rerender(b);
        this.focus(this.insertAfter(b, this.newBlock("p", { indent: b.indent || 0 })), 0);
        this.emit();
        return;
      }
      if (prefix === "```") {
        const rest = richText(b.rich).slice(3);
        b.type = "code";
        b.text = rest;
        delete b.rich;
        this.rerender(b);
        this.focus(b, 0);
        this.emit();
        return;
      }
    }
    this.updateSlash(b, el, e);
    this.emit();
  }

  convert(b, type, cut, extra = {}) {
    b.rich = sliceRich(b.rich || [], cut);
    b.type = type;
    if (type === "todo") b.checked = !!extra.checked;
    if (type === "toggle") b.open = true;
    if (type === "callout") b.icon = b.icon || "💡";
    this.rerender(b);
    this.focus(b, 0);
    this.emit();
  }

  setType(b, type) {
    const el = this.textEl(b);
    if (el) this.sync(b, el);
    if (type === "divider" || type === "page") return;
    if (type === "code") {
      b.text = b.rich ? richText(b.rich) : b.text || "";
      delete b.rich;
    } else if (b.type === "code") {
      b.rich = b.text ? [{ t: b.text }] : [];
      delete b.text;
    }
    b.type = type;
    if (type === "todo" && b.checked === undefined) b.checked = false;
    if (type === "toggle" && b.open === undefined) b.open = true;
    if (type === "callout" && !b.icon) b.icon = "💡";
    if (EDITOR_TEXT_TYPES.has(type) && !b.rich) b.rich = [];
    this.rerender(b);
    this.focus(b);
    this.emit();
  }

  prevText(b) {
    for (let i = this.index(b) - 1; i >= 0; i--) {
      const p = this.blocks[i];
      if (this.rows.get(p.id) && !this.rows.get(p.id).hidden && (EDITOR_TEXT_TYPES.has(p.type) || p.type === "code")) return p;
    }
    return null;
  }

  nextText(b) {
    for (let i = this.index(b) + 1; i < this.blocks.length; i++) {
      const n = this.blocks[i];
      if (this.rows.get(n.id) && !this.rows.get(n.id).hidden && (EDITOR_TEXT_TYPES.has(n.type) || n.type === "code")) return n;
    }
    return null;
  }

  onKey(e, b, el) {
    if (this.slash && this.slashKey(e)) return;
    if (e.isComposing) return;
    const [start, end] = selectionOffsets(el);
    const len = b.type === "code" ? el.textContent.length : richLen(readRich(el));
    if (e.key === "Enter" && !e.shiftKey) {
      if (b.type === "code" && !(e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        document.execCommand("insertText", false, "\n");
        return;
      }
      e.preventDefault();
      this.sync(b, el);
      if (b.type === "code") {
        this.focus(this.insertAfter(b, this.newBlock("p", { indent: b.indent || 0 })), 0);
        this.emit();
        return;
      }
      if (LIST_TYPES.has(b.type) && !richLen(b.rich) && b.type !== "toggle") {
        if (b.indent) b.indent -= 1;
        else b.type = "p";
        this.rerender(b);
        this.focus(b, 0);
        this.emit();
        return;
      }
      const right = sliceRich(b.rich, end);
      b.rich = sliceRich(b.rich, 0, start);
      let type = ["bullet", "number", "todo"].includes(b.type) ? b.type : "p";
      let indent = b.indent || 0;
      if (b.type === "toggle" && b.open) indent += 1;
      if (b.type === "toggle" && !richLen(b.rich) && !richLen(right)) { type = "p"; }
      const nb = this.newBlock(type, { rich: right, indent: indent || undefined });
      if (type === "todo") nb.checked = false;
      renderRich(el, b.rich);
      this.insertAfter(b, nb);
      this.focus(nb, 0);
      this.emit();
      return;
    }
    if (e.key === "Enter" && e.shiftKey && b.type !== "code") {
      e.preventDefault();
      document.execCommand("insertText", false, "\n");
      return;
    }
    if (e.key === "Backspace" && start === 0 && end === 0) {
      this.sync(b, el);
      if (b.type !== "p" && b.type !== "code") {
        e.preventDefault();
        this.setType(b, "p");
        this.focus(b, 0);
        return;
      }
      if (b.indent) {
        e.preventDefault();
        b.indent -= 1;
        this.rerender(b);
        this.focus(b, 0);
        this.emit();
        return;
      }
      const i = this.index(b);
      const prev = this.blocks[i - 1];
      if (!prev) return;
      e.preventDefault();
      if (prev.type === "divider" || prev.type === "page") {
        this.remove(prev);
        this.focus(b, 0);
        this.emit();
        return;
      }
      if (prev.type === "code") {
        if (b.type === "p" && !richLen(b.rich)) { this.remove(b); this.focus(prev); this.emit(); }
        return;
      }
      if (b.type === "code") {
        if (!b.text) { this.remove(b); this.focus(prev); this.emit(); }
        return;
      }
      const at = richLen(prev.rich);
      prev.rich = concatRich(prev.rich, b.rich);
      this.remove(b);
      this.rerender(prev);
      this.focus(prev, at);
      this.emit();
      return;
    }
    if (e.key === "Delete" && start === len && end === len && b.type !== "code") {
      const next = this.blocks[this.index(b) + 1];
      if (!next) return;
      e.preventDefault();
      this.sync(b, el);
      if (EDITOR_TEXT_TYPES.has(next.type)) {
        b.rich = concatRich(b.rich, next.rich);
        this.remove(next);
        this.rerender(b);
        this.focus(b, start);
      } else {
        this.remove(next);
      }
      this.emit();
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      this.sync(b, el);
      const prev = this.blocks[this.index(b) - 1];
      const maxIndent = prev ? (prev.indent || 0) + 1 : 0;
      const value = Math.max(0, Math.min(maxIndent, 8, (b.indent || 0) + (e.shiftKey ? -1 : 1)));
      if (value === (b.indent || 0)) return;
      b.indent = value || undefined;
      this.rerender(b);
      this.focus(b, start);
      this.emit();
      return;
    }
    if (e.key === "ArrowUp" && start === end && this.onEdgeLine(el, "top")) {
      const prev = this.prevText(b);
      if (prev) { e.preventDefault(); this.focus(prev); }
      return;
    }
    if (e.key === "ArrowDown" && start === end && this.onEdgeLine(el, "bottom")) {
      const next = this.nextText(b);
      if (next) { e.preventDefault(); this.focus(next, 0); }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k" && start !== end) {
      e.preventDefault();
      e.stopPropagation();
      this.linkSelection();
    }
  }

  onEdgeLine(el, edge) {
    const rect = caretRect();
    const box = el.getBoundingClientRect();
    if (!rect || (!rect.top && !rect.bottom)) return true;
    const line = parseFloat(getComputedStyle(el).lineHeight) || 24;
    return edge === "top" ? rect.top - box.top < line * 0.8 : box.bottom - rect.bottom < line * 0.8;
  }

  onPaste(e, b, el) {
    e.preventDefault();
    const text = ((e.clipboardData || window.clipboardData).getData("text/plain") || "").replace(/\r\n?/g, "\n");
    if (!text) return;
    if (b.type === "code" || !text.includes("\n")) {
      document.execCommand("insertText", false, text);
      return;
    }
    const lines = text.split("\n");
    document.execCommand("insertText", false, lines[0]);
    this.sync(b, el);
    const [off] = selectionOffsets(el);
    const tail = sliceRich(b.rich, off);
    b.rich = sliceRich(b.rich, 0, off);
    renderRich(el, b.rich);
    let after = b;
    for (const line of lines.slice(1)) {
      const parsed = parseLine(line);
      after = this.insertAfter(after, this.newBlock(parsed.type, {
        rich: parsed.text ? [{ t: parsed.text }] : [], indent: b.indent || undefined,
        checked: parsed.type === "todo" ? parsed.checked : undefined,
      }));
    }
    const at = richLen(after.rich);
    after.rich = concatRich(after.rich, tail);
    this.rerender(after);
    this.focus(after, at);
    this.emit();
  }

  // ---------- меню «/» ----------
  updateSlash(b, el, e) {
    const [off] = selectionOffsets(el);
    const text = b.type === "code" ? "" : richText(b.rich);
    if (this.slash) {
      if (this.slash.block !== b || off <= this.slash.start || text[this.slash.start] !== "/") { this.closeSlash(); return; }
      const query = text.slice(this.slash.start + 1, off);
      if (/\s{2}|\n/.test(query) || query.length > 24) { this.closeSlash(); return; }
      this.slash.query = query;
      this.drawSlash();
      return;
    }
    if (e && e.data === "/" && text[off - 1] === "/" && (off === 1 || /\s/.test(text[off - 2]))) {
      this.openSlash(b, el, off - 1);
    }
  }

  openSlash(b, el, start) {
    const box = h("div", { class: "menu slash-menu" });
    document.body.append(box);
    this.slash = { block: b, el: box, textEl: el, start, query: "", active: 0, items: [] };
    this.drawSlash();
    const rect = caretRect() || el.getBoundingClientRect();
    box.style.position = "fixed";
    const top = rect.bottom + 6;
    box.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 300)) + "px";
    box.style.top = (top + 320 > window.innerHeight ? Math.max(8, rect.top - 326) : top) + "px";
  }

  drawSlash() {
    const s = this.slash;
    const q = s.query.trim().toLowerCase();
    s.items = BLOCK_DEFS.filter((d) => !q || d.label.toLowerCase().includes(q) || d.type.includes(q) || (d.hint && d.hint.includes(q)))
      .filter((d) => d.type !== "page" || this.opts.onCreateSubpage);
    s.active = Math.min(s.active, Math.max(0, s.items.length - 1));
    fill(s.el, h("div", { class: "menu-title" }, "Блоки"),
      s.items.length ? s.items.map((d, i) => h("button", {
        class: "menu-item" + (i === s.active ? " active" : ""),
        onmousedown: (ev) => { ev.preventDefault(); this.pickSlash(d); },
        onmouseenter: () => { s.active = i; s.el.querySelectorAll(".menu-item").forEach((x, j) => x.classList.toggle("active", j === i)); },
      }, h("span", { class: "slash-ico" }, d.icon), h("span", { class: "menu-label" }, d.label),
      d.hint ? h("span", { class: "menu-hint" }, d.hint) : null))
        : h("div", { class: "menu-empty" }, "Ничего не найдено"));
    const active = s.el.querySelector(".menu-item.active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  slashKey(e) {
    const s = this.slash;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (s.items.length) s.active = (s.active + (e.key === "ArrowDown" ? 1 : -1) + s.items.length) % s.items.length;
      this.drawSlash();
      return true;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      if (!s.items.length) { this.closeSlash(); return false; }
      e.preventDefault();
      this.pickSlash(s.items[s.active]);
      return true;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      this.closeSlash();
      return true;
    }
    return false;
  }

  closeSlash() {
    if (this.slash) this.slash.el.remove();
    this.slash = null;
  }

  async pickSlash(def) {
    const s = this.slash;
    this.closeSlash();
    const b = s.block;
    const el = s.textEl;
    this.sync(b, el);
    const [off] = selectionOffsets(el);
    b.rich = concatRich(sliceRich(b.rich, 0, s.start), sliceRich(b.rich, Math.max(off, s.start + 1 + s.query.length)));
    const empty = !richLen(b.rich);
    renderRich(el, b.rich);
    if (def.type === "page") {
      const page = await this.opts.onCreateSubpage();
      if (!page) return;
      const block = this.newBlock("page", { page_id: page.id, indent: b.indent || undefined });
      if (empty) { this.blocks.splice(this.index(b), 1, block); this.rows.get(b.id).replaceWith(this.renderBlock(block)); this.rows.delete(b.id); this.refresh(); }
      else this.insertAfter(b, block);
      this.emit();
      if (this.opts.onOpenPage) this.opts.onOpenPage(page.id);
      return;
    }
    if (def.type === "divider") {
      const divider = this.newBlock("divider", { indent: b.indent || undefined });
      if (empty) {
        Object.assign(b, { type: "divider" });
        delete b.rich;
        this.rerender(b);
        this.focus(this.insertAfter(b, this.newBlock("p", { indent: b.indent || undefined })), 0);
      } else {
        this.insertAfter(b, divider);
        this.focus(this.insertAfter(divider, this.newBlock("p", { indent: b.indent || undefined })), 0);
      }
      this.emit();
      return;
    }
    if (empty) {
      this.setType(b, def.type);
      this.focus(b, 0);
      return;
    }
    const nb = this.newBlock(def.type, { indent: b.indent || undefined });
    this.insertAfter(b, nb);
    this.focus(nb, 0);
    this.emit();
  }

  // ---------- меню блока и перетаскивание ----------
  blockMenu(anchor, b) {
    const items = [
      { label: "Удалить", icon: "trash", danger: true, onClick: () => { this.remove(b); this.emit(); } },
      { label: "Дублировать", icon: "📑", onClick: () => { const copy = JSON.parse(JSON.stringify(b)); copy.id = uid(); this.insertAfter(b, copy); this.emit(); } },
      { divider: true },
    ];
    if (EDITOR_TEXT_TYPES.has(b.type) || b.type === "code") {
      for (const d of BLOCK_DEFS) {
        if (d.type === "divider" || d.type === "page") continue;
        items.push({ label: d.label, icon: d.icon, checked: d.type === b.type, onClick: () => this.setType(b, d.type) });
      }
    }
    popMenu(anchor, items, { title: "Блок" });
  }

  subtree(b) {
    const i = this.index(b);
    const base = b.indent || 0;
    let j = i + 1;
    while (j < this.blocks.length && (this.blocks[j].indent || 0) > base) j++;
    return this.blocks.slice(i, j);
  }

  startDrag(e, b) {
    if (e.button !== undefined && e.button !== 0) return;
    const handle = e.currentTarget;
    const startY = e.clientY;
    let dragging = false;
    let indicator = null;
    let target = null;
    const moved = (ev) => {
      if (!dragging && Math.abs(ev.clientY - startY) < 5) return;
      if (!dragging) {
        dragging = true;
        this.subtree(b).forEach((x) => this.rows.get(x.id).classList.add("dragging"));
        indicator = h("div", { class: "drop-line" });
        this.root.append(indicator);
      }
      ev.preventDefault();
      target = null;
      for (const x of this.blocks) {
        const row = this.rows.get(x.id);
        if (!row || row.hidden) continue;
        const r = row.getBoundingClientRect();
        if (ev.clientY < r.top + r.height / 2) { target = x; break; }
      }
      const rootBox = this.root.getBoundingClientRect();
      const ref = target ? this.rows.get(target.id) : this.root.querySelector(".editor-tail");
      const y = ref ? ref.getBoundingClientRect().top - rootBox.top : rootBox.height;
      indicator.style.top = y - 2 + "px";
    };
    const up = () => {
      window.removeEventListener("pointermove", moved);
      window.removeEventListener("pointerup", up);
      if (!dragging) { this.blockMenu(handle, b); return; }
      indicator.remove();
      const group = this.subtree(b);
      group.forEach((x) => { const row = this.rows.get(x.id); if (row) row.classList.remove("dragging"); });
      if (target && group.includes(target)) return;
      this.blocks = this.blocks.filter((x) => !group.includes(x));
      const at = target ? this.blocks.indexOf(target) : this.blocks.length;
      const shift = (target ? target.indent || 0 : 0) - (b.indent || 0);
      if (target) group.forEach((x) => { x.indent = Math.max(0, (x.indent || 0) + shift) || undefined; });
      this.blocks.splice(at, 0, ...group);
      this.render();
      this.emit();
    };
    window.addEventListener("pointermove", moved);
    window.addEventListener("pointerup", up);
  }

  // ---------- панель форматирования ----------
  updateToolbar() {
    const sel = window.getSelection();
    if (!sel.rangeCount || sel.isCollapsed) { this.hideToolbar(); return; }
    const range = sel.getRangeAt(0);
    const node = range.commonAncestorContainer;
    const el = (node.nodeType === 1 ? node : node.parentElement).closest(".blk-text");
    if (!el || !this.root.contains(el) || el.classList.contains("blk-code-text")) { this.hideToolbar(); return; }
    const rect = range.getBoundingClientRect();
    if (!this.toolbar) {
      const btn = (label, title, cls, action) => h("button", { class: "tb-btn " + cls, title, onmousedown: (ev) => { ev.preventDefault(); action(); } }, label);
      this.toolbar = h("div", { class: "format-bar" },
        btn("B", "Жирный (Ctrl+B)", "b", () => this.format("bold")),
        btn("I", "Курсив (Ctrl+I)", "i", () => this.format("italic")),
        btn("U", "Подчёркнутый (Ctrl+U)", "u", () => this.format("underline")),
        btn("S", "Зачёркнутый", "s", () => this.format("strikeThrough")),
        btn("</>", "Код", "c", () => this.toggleCode()),
        btn("🔗", "Ссылка (Ctrl+K)", "l", () => this.linkSelection()));
      document.body.append(this.toolbar);
    }
    this.toolbar.style.left = Math.max(8, Math.min(rect.left + rect.width / 2 - 110, window.innerWidth - 228)) + "px";
    this.toolbar.style.top = Math.max(8, rect.top - 44) + "px";
    this.toolbar.hidden = false;
  }

  hideToolbar() { if (this.toolbar) this.toolbar.hidden = true; }

  activeBlock() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return null;
    const node = sel.getRangeAt(0).startContainer;
    const row = (node.nodeType === 1 ? node : node.parentElement).closest(".blk");
    return row ? { b: this.byId(row.dataset.id), el: row.querySelector(".blk-text") } : null;
  }

  afterFormat() {
    const active = this.activeBlock();
    if (!active) return;
    this.sync(active.b, active.el);
    this.emit();
  }

  format(cmd) {
    document.execCommand(cmd);
    this.afterFormat();
  }

  toggleCode() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const inside = (range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement).closest("code");
    if (inside) {
      inside.replaceWith(document.createTextNode(inside.textContent));
    } else {
      const code = document.createElement("code");
      code.textContent = range.toString();
      range.deleteContents();
      range.insertNode(code);
      sel.removeAllRanges();
      const r = document.createRange();
      r.selectNodeContents(code);
      sel.addRange(r);
    }
    this.afterFormat();
  }

  async linkSelection() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const saved = sel.getRangeAt(0).cloneRange();
    const active = this.activeBlock();
    const node = saved.commonAncestorContainer;
    const existing = (node.nodeType === 1 ? node : node.parentElement).closest("a");
    const url = await promptDialog("Ссылка", "Адрес (https://…)", existing ? existing.getAttribute("href") : "", "Применить");
    if (url === null || !active) return;
    setCaret(active.el, 0);
    sel.removeAllRanges();
    sel.addRange(saved);
    if (!url.trim()) document.execCommand("unlink");
    else document.execCommand("createLink", false, /^[a-z]+:/i.test(url.trim()) ? url.trim() : "https://" + url.trim());
    this.sync(active.b, active.el);
    renderRich(active.el, active.b.rich);
    this.emit();
  }
}
