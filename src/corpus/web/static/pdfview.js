// The PDF pane: every page as a canvas, drawn when it scrolls near the view.
import * as pdfjs from "/static/pdfjs/pdf.min.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = "/static/pdfjs/pdf.worker.min.mjs";
const ASSETS = {
  cMapUrl: "/static/pdfjs/cmaps/", cMapPacked: true,
  standardFontDataUrl: "/static/pdfjs/standard_fonts/",
  wasmUrl: "/static/pdfjs/wasm/", iccUrl: "/static/pdfjs/iccs/",
  isEvalSupported: false,
};

export class PdfView {
  constructor(root, onPage) {
    this.root = root;
    this.onPage = onPage || (() => {});
    this.task = null;
    this.doc = null;
  }

  async open(url, page = 1) {
    this.close();
    this.root.replaceChildren();
    const bar = el("div", "pdf-bar");
    const prev = button("‹", "Previous page"), next = button("›", "Next page");
    this.input = Object.assign(el("input"), { type: "number", min: 1, value: page, ariaLabel: "Page" });
    this.total = el("span");
    const link = Object.assign(el("a"), { href: url, target: "_blank", rel: "noopener", textContent: "Open PDF" });
    bar.append(prev, this.input, this.total, next, el("span", "spacer"), link);
    this.pages = el("div", "pdf-pages");
    this.root.append(bar, this.pages);
    prev.onclick = () => this.goTo(this.current - 1);
    next.onclick = () => this.goTo(this.current + 1);
    this.input.onchange = () => this.goTo(Number(this.input.value));

    const task = (this.task = pdfjs.getDocument({ url, ...ASSETS }));
    let doc;
    try {
      doc = await task.promise;
    } catch (e) {
      if (task === this.task) this.pages.textContent = `Could not open the PDF: ${e.message}`;
      return;
    }
    if (task !== this.task) return;          // another PDF was opened meanwhile
    this.doc = doc;
    this.total.textContent = `of ${doc.numPages}`;
    this.input.max = doc.numPages;
    const first = await doc.getPage(1);
    const vp = first.getViewport({ scale: 1 });
    this.ratio = vp.height / vp.width;
    this.slots = [];
    for (let n = 1; n <= doc.numPages; n++) {
      const slot = el("div", "pdf-page");
      slot.dataset.page = n;
      slot.append(Object.assign(el("span", "num"), { textContent: n }));
      this.pages.append(slot);
      this.slots.push(slot);
    }
    this.size();
    this.observe();
    this.resize = new ResizeObserver(() => this.size());
    this.resize.observe(this.pages);
    this.goTo(page, "instant");
  }

  size() {
    const width = Math.min(this.pages.clientWidth - 24, 1100);
    for (const slot of this.slots) {
      slot.style.width = `${width}px`;
      slot.style.height = `${Math.round(width * (slot.ratio || this.ratio))}px`;
    }
  }

  observe() {
    const seen = new Map();
    this.draw = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) this.render(e.target);
    }, { root: this.pages, rootMargin: "150% 0px" });
    this.track = new IntersectionObserver((entries) => {
      for (const e of entries) seen.set(e.target, e.intersectionRatio);
      let best = null, ratio = 0;
      for (const [slot, r] of seen) if (r > ratio) { best = slot; ratio = r; }
      if (best) this.setCurrent(Number(best.dataset.page));
    }, { root: this.pages, threshold: [0, 0.25, 0.5, 0.75, 1] });
    for (const slot of this.slots) { this.draw.observe(slot); this.track.observe(slot); }
  }

  async render(slot) {
    if (slot.dataset.drawn) return;
    slot.dataset.drawn = "1";
    const page = await this.doc.getPage(Number(slot.dataset.page));
    const base = page.getViewport({ scale: 1 });
    slot.ratio = base.height / base.width;
    this.size();
    const scale = (slot.clientWidth / base.width) * (window.devicePixelRatio || 1);
    const vp = page.getViewport({ scale });
    const canvas = el("canvas");
    canvas.width = Math.floor(vp.width);
    canvas.height = Math.floor(vp.height);
    slot.prepend(canvas);
    await page.render({ canvas, viewport: vp }).promise;
  }

  setCurrent(n) {
    if (n === this.current) return;
    this.current = n;
    this.input.value = n;
    this.onPage(n);
  }

  goTo(n, behavior = "instant") {
    if (!this.slots) return;
    n = Math.max(1, Math.min(this.slots.length, n || 1));
    // scrollTo on the pane, not scrollIntoView, which would also scroll the page
    this.pages.scrollTo({ top: this.slots[n - 1].offsetTop - 12, behavior });
    this.setCurrent(n);
  }

  close() {
    this.draw?.disconnect();
    this.track?.disconnect();
    this.resize?.disconnect();
    this.task?.destroy();
    this.task = this.doc = this.slots = null;
    this.current = 0;
  }
}

function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function button(text, label) {
  return Object.assign(el("button", "quiet"), { type: "button", textContent: text, ariaLabel: label });
}
