// Search, results and the reader. Everything shown is in the URL, so a search
// or an open paper can be bookmarked and the Back button works.
import { PdfView } from "/static/pdfview.js";

const $ = (id) => document.getElementById(id);
const form = $("search"), results = $("results"), reader = $("reader"), pdfPane = $("pdf");
const FIELDS = ["q", "genus", "clade", "from", "to", "tool", "sort"];
const FLAGS = ["syntax", "translated", "supplements"];
const pdf = new PdfView(pdfPane, (n) => onPdfPage(n));
let shown = { search: null, paper: null };

const state = () => new URLSearchParams(location.search);

function go(params, replace = false) {
  for (const [k, v] of [...params]) if (!v || (k === "sort" && v === "relevance")) params.delete(k);
  const url = params.toString() ? `?${params}` : location.pathname;
  history[replace ? "replaceState" : "pushState"](null, "", url);
  apply();
}

function formParams() {
  const p = new URLSearchParams();
  for (const k of FIELDS) p.set(k, form.elements[k].value.trim());
  for (const k of FLAGS) if (form.elements[k].checked) p.set(k, "1");
  return p;
}

function fillForm(p) {
  for (const k of FIELDS) form.elements[k].value = p.get(k) || (k === "sort" ? "relevance" : "");
  for (const k of FLAGS) form.elements[k].checked = p.get(k) === "1";
  const open = ["genus", "clade", "from", "to", "tool", "translated", "supplements", "syntax"]
    .some((k) => p.get(k)) || (p.get("sort") || "relevance") !== "relevance";
  if (open) showFilters(true);
}

function showFilters(open) {
  $("filters").hidden = !open;
  $("filters-toggle").setAttribute("aria-expanded", String(open));
}

async function apply() {
  const p = state();
  fillForm(p);
  const search = new URLSearchParams(p);
  for (const k of ["key", "v", "pdf", "pg"]) search.delete(k);
  const searchKey = search.toString();
  if (searchKey !== shown.search) { shown.search = searchKey; await runSearch(search); }
  else markCurrent(p.get("key"));
  const paperKey = [p.get("key"), p.get("v")].join("|");
  if (paperKey !== shown.paper) { shown.paper = paperKey; await openPaper(p); }
  showPdf(p);
}

async function runSearch(search) {
  if (!search.toString() || [...search.keys()].every((k) => k === "page")) {
    results.replaceChildren(para("empty", "Search the full text of every converted paper, or filter by the genus or clade whose data cites it."));
    return;
  }
  results.replaceChildren(para("empty", "Searching…"));
  const res = await fetch(`/api/search?${search}`);
  const data = await res.json();
  if (!res.ok) { results.replaceChildren(para("error", data.detail || "The search failed.")); return; }
  const page = data.page, pages = Math.max(1, Math.ceil(data.total / data.per_page));
  const head = para("count", data.total === 1 ? "1 paper" : `${data.total.toLocaleString()} papers`);
  const list = document.createElement("div");
  for (const h of data.hits) list.append(hitCard(h));
  results.replaceChildren(head, list);
  if (pages > 1) results.append(pager(page, pages));
  if (!data.hits.length) results.append(para("empty", "No matches."));
  markCurrent(state().get("key"));
  results.scrollTop = 0;
}

function hitCard(h) {
  const p = state(); p.set("key", h.key); p.delete("v"); p.delete("pg");
  const a = Object.assign(document.createElement("a"), { className: "hit", href: `?${p}` });
  a.dataset.key = h.key;
  a.append(
    para("key", [h.key, h.year || "n.d.", h.tool || "", h.translated ? "translated" : ""].filter(Boolean).join(" · ")),
    para("title", h.title || "(no title)"),
    para("meta", [h.authors, h.venue].filter(Boolean).join(" — ")));
  const cited = [h.genera, h.clades].filter(Boolean).join("; ");
  if (cited) a.append(para("cited", `Cited by ${cited}`));
  if (h.snippet) { const s = para("snip"); s.innerHTML = h.snippet; a.append(s); }   // escaped by the server
  a.onclick = (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); go(p); };
  return a;
}

function pager(page, pages) {
  const box = document.createElement("div");
  box.className = "pager";
  const to = (n) => { const p = state(); p.set("page", n); go(p); };
  const prev = Object.assign(document.createElement("button"), { className: "quiet", textContent: "Previous", disabled: page <= 1 });
  const next = Object.assign(document.createElement("button"), { className: "quiet", textContent: "Next", disabled: page >= pages });
  prev.onclick = () => to(page - 1);
  next.onclick = () => to(page + 1);
  box.append(prev, `Page ${page} of ${pages}`, next);
  return box;
}

function markCurrent(key) {
  for (const a of results.querySelectorAll(".hit")) a.setAttribute("aria-current", String(a.dataset.key === key));
}

async function openPaper(p) {
  const key = p.get("key");
  if (!key) { reader.hidden = true; reader.replaceChildren(); return; }
  reader.hidden = false;
  reader.replaceChildren(para("empty", "Loading…"));
  const version = p.get("v") === "original" ? "original" : "text";
  const res = await fetch(`/api/paper/${encodeURIComponent(key)}?version=${version}`);
  if (!res.ok) { reader.replaceChildren(para("error", (await res.json()).detail || "Could not open that paper.")); return; }
  const d = await res.json();
  const title = Object.assign(document.createElement("h1"), { className: "title", textContent: d.title || d.key });
  if (d.parent) title.textContent = `Supplement “${d.name}” — ${title.textContent}`;
  const cite = para("cite");
  cite.append(Object.assign(document.createElement("span"), { className: "key", textContent: d.key }),
    ` · ${[d.authors, d.year, d.venue].filter(Boolean).join(" · ")}`);
  if (d.doi) {
    cite.append(" · ", Object.assign(document.createElement("a"), {
      href: `https://doi.org/${d.doi}`, textContent: `doi:${d.doi}`, target: "_blank", rel: "noopener noreferrer" }));
  }
  reader.replaceChildren(title, cite, chips("Cited by genus", "genus", d.genera), chips("Cited by clade", "clade", d.clades), tools(d, p));
  const conv = document.createElement("details");
  conv.className = "conversion";
  conv.append(Object.assign(document.createElement("summary"), { textContent: "Conversion record" }),
    Object.assign(document.createElement("pre"), { textContent: JSON.stringify(d.conversion, null, 2) }));
  const body = document.createElement("div");
  body.className = "body";
  body.lang = version === "original" ? "" : "en";
  body.innerHTML = d.html;                       // rendered and sanitised by the server
  reader.append(conv, body);
  marks = pageMarks(body, d.has_pdf);
  reader.scrollTop = 0;
  const first = highlight(body, terms(p.get("q") || "", p.get("syntax") === "1"));
  if (first) first.scrollIntoView({ block: "center" });
}

// --- Keeping the text and the PDF on the same page ---------------------------
// The server turns each page marker into <div data-page="N">. Whichever pane
// the reader last scrolled, clicked or keyed in leads; the other follows.

let marks = [];
let leader = "text";
let shownPage = 0;

function pageMarks(body, hasPdf) {
  const list = [...body.querySelectorAll("div[data-page]")];
  for (const m of list) {
    m.className = "page-mark";
    const n = Number(m.dataset.page);
    if (hasPdf) {
      const b = Object.assign(document.createElement("button"), { type: "button", textContent: `PDF page ${n}` });
      b.onclick = () => openPdfAt(n);
      m.append(b);
    } else m.textContent = `PDF page ${n}`;
  }
  return list;
}

function textPage() {
  // The page whose marker is the last one above the top of the reader.
  const top = reader.scrollTop + 24;
  let page = 0;
  for (const m of marks) { if (m.offsetTop <= top) page = Number(m.dataset.page); else break; }
  return page || (marks.length ? Number(marks[0].dataset.page) : 0);
}

function showCurrent(n) {
  if (n === shownPage) return;
  shownPage = n;
  for (const m of marks) m.classList.toggle("current", Number(m.dataset.page) === n);
}

function openPdfAt(n) {
  leader = "text";
  if (!pdfPane.hidden && pdfShown) { pdf.goTo(n); return; }
  const q = state(); q.set("pdf", "1"); q.set("pg", n); go(q);
}

function onPdfPage(n) {
  if (leader !== "pdf" || !marks.length) return;
  const target = marks.find((m) => Number(m.dataset.page) >= n);
  if (target) reader.scrollTo({ top: target.offsetTop - 8 });
  showCurrent(n);
}

let syncQueued = false;
reader.addEventListener("scroll", () => {
  if (syncQueued || !marks.length) return;
  syncQueued = true;
  requestAnimationFrame(() => {
    syncQueued = false;
    const n = textPage();
    showCurrent(n);
    if (leader === "text" && pdfShown && n && n !== pdf.current) pdf.goTo(n, "instant");
  });
}, { passive: true });
for (const [el, who] of [[reader, "text"], [pdfPane, "pdf"]]) {
  for (const ev of ["wheel", "pointerdown", "keydown", "touchstart"]) {
    el.addEventListener(ev, () => { leader = who; }, { passive: true });
  }
}

function chips(label, field, names) {
  const box = document.createElement("div");
  if (!names.length) return box;
  box.className = "chips";
  box.append(Object.assign(document.createElement("span"), { className: "label", textContent: label }));
  for (const n of names) {
    const p = new URLSearchParams(); p.set(field, n);
    const a = Object.assign(document.createElement("a"), { href: `?${p}`, textContent: n });
    a.onclick = (e) => { e.preventDefault(); go(p); };
    box.append(a);
  }
  return box;
}

function tools(d, p) {
  const box = document.createElement("div");
  box.className = "tools";
  if (d.has_original) {
    const tabs = document.createElement("span");
    tabs.className = "tabs";
    for (const [v, label] of [["text", "English"], ["original", "Original"]]) {
      const b = Object.assign(document.createElement("button"), { type: "button", textContent: label });
      b.setAttribute("aria-pressed", String(d.version === v));
      b.onclick = () => { const q = state(); if (v === "original") q.set("v", v); else q.delete("v"); go(q); };
      tabs.append(b);
    }
    box.append(tabs);
  }
  if (d.has_pdf) {
    const on = p.get("pdf") === "1";
    const b = Object.assign(document.createElement("button"), { type: "button", className: "quiet", textContent: on ? "Hide PDF" : "Show PDF" });
    b.onclick = () => { const q = state(); if (on) q.delete("pdf"); else q.set("pdf", "1"); go(q); };
    box.append(b);
  } else box.append(Object.assign(document.createElement("span"), { className: "quiet-text", textContent: "No PDF" }));
  const raw = new URLSearchParams(); if (d.version === "original") raw.set("version", "original");
  box.append(Object.assign(document.createElement("a"), { href: `/md/${encodeURIComponent(d.key)}?${raw}`, target: "_blank", textContent: "Markdown" }));
  for (const s of d.supplements) {
    const q = new URLSearchParams(); q.set("key", s.key);
    const a = Object.assign(document.createElement("a"), { href: `?${q}`, textContent: `Supplement: ${s.name}` });
    a.onclick = (e) => { e.preventDefault(); const r = state(); r.set("key", s.key); r.delete("v"); r.delete("pdf"); go(r); };
    box.append(a);
  }
  if (d.parent) {
    const a = Object.assign(document.createElement("a"), { href: `?key=${encodeURIComponent(d.parent)}`, textContent: "Main paper" });
    a.onclick = (e) => { e.preventDefault(); const r = state(); r.set("key", d.parent); r.delete("pdf"); go(r); };
    box.append(a);
  }
  return box;
}

let pdfShown = null;
function showPdf(p) {
  const key = p.get("key"), on = Boolean(key) && p.get("pdf") === "1";
  pdfPane.hidden = !on;
  $("layout").classList.toggle("with-pdf", on);
  if (!on) { if (pdfShown) { pdf.close(); pdfPane.replaceChildren(); pdfShown = null; } return; }
  if (pdfShown !== key) {
    pdfShown = key;
    pdf.open(`/pdf/${encodeURIComponent(key)}`, Number(p.get("pg")) || textPage() || 1);
  }
}

// Words to highlight in the paper: the plain query's words and phrases. A
// match is any word starting with a term, which covers most of what the
// index's stemming matches ("dinosaur" finds "dinosaurs").
function terms(q, syntax) {
  if (syntax) q = q.replace(/\b(NEAR|AND|OR|NOT)\b|[A-Za-z_]+:|[(),\d]+(?=\))/g, " ");
  const out = [];
  for (const m of q.matchAll(/"([^"]*)"|(\S+)/g)) {
    const t = (m[1] ?? m[2]).replace(/[*"]/g, "").trim();
    if (t.length > 1) out.push(t);
  }
  return out;
}

function highlight(root, words) {
  if (!words.length) return null;
  const esc = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"));
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${esc.join("|")})`, "giu");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  let first = null;
  for (const node of nodes) {
    const text = node.nodeValue;
    re.lastIndex = 0;
    if (!re.test(text)) continue;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let at = 0;
    for (const m of text.matchAll(re)) {
      frag.append(text.slice(at, m.index));
      const mark = Object.assign(document.createElement("mark"), { textContent: m[0] });
      first ??= mark;
      frag.append(mark);
      at = m.index + m[0].length;
    }
    frag.append(text.slice(at));
    node.replaceWith(frag);
  }
  return first;
}

function para(cls, text) {
  return Object.assign(document.createElement("div"), { className: cls, textContent: text ?? "" });
}

form.addEventListener("submit", (e) => { e.preventDefault(); go(formParams()); });
$("filters-toggle").onclick = () => showFilters($("filters").hidden);
window.addEventListener("popstate", apply);

fetch("/api/facets").then((r) => r.json()).then((f) => {
  for (const [id, names] of [["genera-list", f.genera], ["clades-list", f.clades]]) {
    $(id).replaceChildren(...names.map((n) => Object.assign(document.createElement("option"), { value: n })));
  }
  form.elements.tool.append(...f.tools.map((t) => Object.assign(document.createElement("option"), { value: t, textContent: t })));
  form.elements.tool.value = state().get("tool") || "";
  if (f.papers) $("stats").textContent = `${f.papers.toLocaleString()} paper${f.papers === 1 ? "" : "s"}, ${f.years[0]}–${f.years[1]}`;
});
apply();
