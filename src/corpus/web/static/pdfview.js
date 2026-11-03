/**
 * The PDF pane: every page of the PDF as a canvas, drawn when it scrolls near the view.
 */

import * as pdfjs from "./pdfjs/pdf.min.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = "/static/pdfjs/pdf.worker.min.mjs";

/** Where PDF.js finds the files it loads on demand. */
const assets = {
    cMapUrl: "/static/pdfjs/cmaps/",
    cMapPacked: true,
    standardFontDataUrl: "/static/pdfjs/standard_fonts/",
    wasmUrl: "/static/pdfjs/wasm/",
    iccUrl: "/static/pdfjs/iccs/",
    isEvalSupported: false,
};

/**
 * Creates an element with an optional class.
 *
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag - Element name.
 * @param {string} [className] - Class to set.
 * @returns {HTMLElementTagNameMap[K]} The element.
 */
function createElement(tag, className)
{
    const element = document.createElement(tag);

    if (className)
    {
        element.className = className;
    }

    return element;
}

/**
 * Creates one of the pane's toolbar buttons.
 *
 * @param {string} text - Button text.
 * @param {string} label - Accessible name.
 * @returns {HTMLButtonElement} The button.
 */
function createButton(text, label)
{
    const button = createElement("button", "quiet");
    button.type = "button";
    button.textContent = text;
    button.ariaLabel = label;

    return button;
}

/**
 * Shows one PDF at a time in a scrolling pane, and reports the page in view.
 */
export class PdfView
{
    /**
     * @param {HTMLElement} root - The pane to fill.
     * @param {(page: number) => void} onPage - Called when the page in view changes.
     */
    constructor(root, onPage)
    {
        /** The pane. */
        this.root = root;

        /** Called when the page in view changes. */
        this.onPage = onPage;

        /** @type {import("./pdfjs/pdf.min.mjs").PDFDocumentLoadingTask | null} */
        this.task = null;

        /** @type {import("./pdfjs/pdf.min.mjs").PDFDocumentProxy | null} */
        this.document = null;

        /** @type {Array<HTMLDivElement>} */
        this.slots = [];

        /** The page in view. */
        this.current = 0;

        /** Height over width of the first page, for pages not yet drawn. */
        this.ratio = 1.4;

        /** @type {Map<HTMLElement, number>} Height over width of each drawn page. */
        this.ratios = new Map();

        /** @type {IntersectionObserver | null} */
        this.drawObserver = null;

        /** @type {IntersectionObserver | null} */
        this.trackObserver = null;

        /** @type {ResizeObserver | null} */
        this.resizeObserver = null;

        /** The toolbar's page number field. */
        this.pageInput = createElement("input");

        /** The toolbar's "of N". */
        this.total = createElement("span");

        /** The scrolling list of pages. */
        this.pages = createElement("div", "pdf-pages");
    }

    /**
     * Opens a PDF, replacing the one shown.
     *
     * @param {string} address - URL of the PDF.
     * @param {number} [page] - Page to show first.
     * @returns {Promise<void>}
     */
    async open(address, page = 1)
    {
        this.close();
        const toolbar = createElement("div", "pdf-bar");
        const previous = createButton("‹", "Previous page");
        const next = createButton("›", "Next page");
        const link = createElement("a");
        link.href = address;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = "Open PDF";
        this.pageInput = createElement("input");
        this.pageInput.type = "number";
        this.pageInput.min = "1";
        this.pageInput.value = String(page);
        this.pageInput.ariaLabel = "Page";
        this.total = createElement("span");
        this.pages = createElement("div", "pdf-pages");
        toolbar.append(previous, this.pageInput, this.total, next, createElement("span", "spacer"), link);
        this.root.replaceChildren(toolbar, this.pages);
        previous.addEventListener("click", () => this.goTo(this.current - 1));
        next.addEventListener("click", () => this.goTo(this.current + 1));
        this.pageInput.addEventListener("change", () => this.goTo(Number(this.pageInput.value)));

        const task = pdfjs.getDocument({ url: address, ...assets });
        this.task = task;
        let pdf;

        try
        {
            pdf = await task.promise;
        }
        catch (error)
        {
            if (task === this.task)
            {
                this.pages.textContent = `Could not open the PDF: ${error instanceof Error ? error.message : error}`;
            }

            return;
        }

        // Another PDF was opened meanwhile.
        if (task !== this.task)
        {
            return;
        }

        this.document = pdf;
        this.total.textContent = `of ${pdf.numPages}`;
        this.pageInput.max = String(pdf.numPages);
        const viewport = (await pdf.getPage(1)).getViewport({ scale: 1 });
        this.ratio = viewport.height / viewport.width;

        for (let number = 1; number <= pdf.numPages; number++)
        {
            const slot = createElement("div", "pdf-page");
            const label = createElement("span", "num");
            slot.dataset.page = String(number);
            label.textContent = String(number);
            slot.append(label);
            this.pages.append(slot);
            this.slots.push(slot);
        }

        this.size();
        this.observe();
        this.resizeObserver = new ResizeObserver(() => this.size());
        this.resizeObserver.observe(this.pages);
        this.goTo(page);
    }

    /**
     * Sizes every page to the pane's width.
     */
    size()
    {
        const width = Math.min(this.pages.clientWidth - 24, 1100);

        for (const slot of this.slots)
        {
            slot.style.width = `${width}px`;
            slot.style.height = `${Math.round(width * (this.ratios.get(slot) ?? this.ratio))}px`;
        }
    }

    /**
     * Draws pages as they come near the view, and tracks the page most in view.
     */
    observe()
    {
        /** @type {Map<Element, number>} */
        const visible = new Map();
        this.drawObserver = new IntersectionObserver((entries) =>
        {
            for (const entry of entries)
            {
                if (entry.isIntersecting && entry.target instanceof HTMLDivElement)
                {
                    this.render(entry.target);
                }
            }
        }, { root: this.pages, rootMargin: "150% 0px" });
        this.trackObserver = new IntersectionObserver((entries) =>
        {
            for (const entry of entries)
            {
                visible.set(entry.target, entry.intersectionRatio);
            }

            let best = null;
            let bestRatio = 0;

            for (const [slot, ratio] of visible)
            {
                if (ratio > bestRatio)
                {
                    best = slot;
                    bestRatio = ratio;
                }
            }

            if (best instanceof HTMLElement)
            {
                this.setCurrent(Number(best.dataset.page));
            }
        }, { root: this.pages, threshold: [0, 0.25, 0.5, 0.75, 1] });

        for (const slot of this.slots)
        {
            this.drawObserver.observe(slot);
            this.trackObserver.observe(slot);
        }
    }

    /**
     * Draws one page into its slot, once.
     *
     * @param {HTMLDivElement} slot - The page's slot.
     * @returns {Promise<void>}
     */
    async render(slot)
    {
        if (slot.dataset.drawn || this.document === null)
        {
            return;
        }

        slot.dataset.drawn = "1";
        const page = await this.document.getPage(Number(slot.dataset.page));
        const base = page.getViewport({ scale: 1 });
        this.ratios.set(slot, base.height / base.width);
        this.size();
        const scale = (slot.clientWidth / base.width) * (window.devicePixelRatio || 1);
        const viewport = page.getViewport({ scale });
        const canvas = createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        slot.prepend(canvas);
        await page.render({ canvas, viewport }).promise;
    }

    /**
     * Records the page in view and reports it.
     *
     * @param {number} page - Page number.
     */
    setCurrent(page)
    {
        if (page !== this.current)
        {
            this.current = page;
            this.pageInput.value = String(page);
            this.onPage(page);
        }
    }

    /**
     * Scrolls the pane to a page.
     *
     * @param {number} page - Page number; clamped to the PDF's pages.
     */
    goTo(page)
    {
        if (!this.slots.length)
        {
            return;
        }

        const number = Math.max(1, Math.min(this.slots.length, page || 1));
        // Scroll the pane itself: scrollIntoView would also scroll the page around it.
        this.pages.scrollTo({ top: this.slots[number - 1].offsetTop - 12 });
        this.setCurrent(number);
    }

    /**
     * Closes the PDF shown, if any.
     */
    close()
    {
        this.drawObserver?.disconnect();
        this.trackObserver?.disconnect();
        this.resizeObserver?.disconnect();
        this.task?.destroy();
        this.task = null;
        this.document = null;
        this.slots = [];
        this.ratios.clear();
        this.current = 0;
    }
}
