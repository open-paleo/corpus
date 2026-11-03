/**
 * The corpus web app: search, results and the reader. Everything shown is in the URL, so a
 * search or an open paper can be bookmarked and the Back button works.
 */

import { PdfView } from "./pdfview.js";

/**
 * @typedef {object} Hit
 * @property {string} key
 * @property {string} year
 * @property {string} title
 * @property {string} authors
 * @property {string} venue
 * @property {string} genera
 * @property {string} clades
 * @property {string} tool
 * @property {boolean} translated
 * @property {string} snippet - HTML, escaped by the server, with <mark> around the matches.
 */

/**
 * @typedef {object} SearchResponse
 * @property {number} total
 * @property {number} page
 * @property {number} per_page
 * @property {Array<Hit>} hits
 */

/**
 * @typedef {object} Paper
 * @property {string} key
 * @property {string | null} parent - The main paper's key, for a supplement.
 * @property {string | null} name - The supplement's name.
 * @property {string} title
 * @property {string} authors
 * @property {string} year
 * @property {string} venue
 * @property {string} doi
 * @property {Array<string>} genera
 * @property {Array<string>} clades
 * @property {object} conversion
 * @property {Array<{ key: string, name: string }>} supplements
 * @property {boolean} has_pdf
 * @property {boolean} has_original
 * @property {"text" | "original"} version
 * @property {string} html - Rendered and sanitised by the server.
 */

/**
 * @typedef {object} Facets
 * @property {number} papers
 * @property {Array<string>} genera
 * @property {Array<string>} clades
 * @property {Array<string>} tools
 * @property {[number | null, number | null]} years
 */

const textFields = ["q", "genus", "clade", "from", "to", "tool", "sort"];
const checkboxFields = ["syntax", "translated", "supplements"];
const filterFields = ["genus", "clade", "from", "to", "tool", "translated", "supplements", "syntax"];
const introduction = "Search the full text of every converted paper, or filter by the genus or clade whose data cites it.";

/**
 * Finds a required element in the page.
 *
 * @template {HTMLElement} T
 * @param {string} id - Element id.
 * @param {new () => T} type - The element's class.
 * @returns {T} The element.
 */
function getElement(id, type)
{
    const element = document.getElementById(id);

    if (!(element instanceof type))
    {
        throw new Error(`missing element #${id}`);
    }

    return element;
}

/**
 * Finds one of the search form's fields.
 *
 * @param {string} name - Field name.
 * @returns {HTMLInputElement | HTMLSelectElement} The field.
 */
function getField(name)
{
    const field = form.elements.namedItem(name);

    if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement))
    {
        throw new Error(`missing field ${name}`);
    }

    return field;
}

/**
 * Creates an element with a class and text.
 *
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag - Element name.
 * @param {string} [className] - Class to set.
 * @param {string} [text] - Text content.
 * @returns {HTMLElementTagNameMap[K]} The element.
 */
function createElement(tag, className, text)
{
    const element = document.createElement(tag);

    if (className)
    {
        element.className = className;
    }

    if (text !== undefined)
    {
        element.textContent = text;
    }

    return element;
}

/**
 * Creates a link that changes the app's state without reloading the page.
 *
 * @param {string} text - Link text.
 * @param {URLSearchParams} parameters - The state it leads to.
 * @returns {HTMLAnchorElement} The link.
 */
function createStateLink(text, parameters)
{
    const link = createElement("a", "", text);
    link.href = `?${parameters}`;
    link.addEventListener("click", (event) =>
    {
        if (!(event.metaKey || event.ctrlKey || event.shiftKey))
        {
            event.preventDefault();
            go(parameters);
        }
    });

    return link;
}

const form = getElement("search", HTMLFormElement);
const results = getElement("results", HTMLElement);
const reader = getElement("reader", HTMLElement);
const pdfPane = getElement("pdf", HTMLElement);
const pdfView = new PdfView(pdfPane, (page) => followPdf(page));

/** The search and the paper on screen, as the parts of the URL they came from. */
const shown = { search: "", paper: "" };

/** @type {string | null} The key of the paper whose PDF is open. */
let pdfShown = null;

/** @type {Array<HTMLElement>} The open paper's page markers. */
let pageMarks = [];

/** @type {"text" | "pdf"} The pane the reader last scrolled, clicked or typed in. */
let leader = "text";

/** The page highlighted among the page markers. */
let highlightedPage = 0;

/** Whether a scroll of the reader is waiting to be synchronised. */
let syncQueued = false;

/**
 * The app's state: the URL's query.
 *
 * @returns {URLSearchParams} The state.
 */
function getState()
{
    return new URLSearchParams(location.search);
}

/**
 * Moves to a new state, adding it to the browser's history.
 *
 * @param {URLSearchParams} parameters - The state.
 */
function go(parameters)
{
    for (const [name, value] of [...parameters])
    {
        if (!value || (name === "sort" && value === "relevance"))
        {
            parameters.delete(name);
        }
    }

    history.pushState(null, "", parameters.size ? `?${parameters}` : location.pathname);
    apply();
}

/**
 * Reads the search form into a state.
 *
 * @returns {URLSearchParams} The state.
 */
function readForm()
{
    const parameters = new URLSearchParams();

    for (const name of textFields)
    {
        parameters.set(name, getField(name).value.trim());
    }

    for (const name of checkboxFields)
    {
        const field = getField(name);

        if (field instanceof HTMLInputElement && field.checked)
        {
            parameters.set(name, "1");
        }
    }

    return parameters;
}

/**
 * Fills the search form from a state, opening the filters when any is set.
 *
 * @param {URLSearchParams} parameters - The state.
 */
function fillForm(parameters)
{
    for (const name of textFields)
    {
        getField(name).value = parameters.get(name) ?? (name === "sort" ? "relevance" : "");
    }

    for (const name of checkboxFields)
    {
        const field = getField(name);

        if (field instanceof HTMLInputElement)
        {
            field.checked = parameters.get(name) === "1";
        }
    }

    if (filterFields.some((name) => parameters.get(name)) || (parameters.get("sort") ?? "relevance") !== "relevance")
    {
        showFilters(true);
    }
}

/**
 * Shows or hides the filters.
 *
 * @param {boolean} open - Whether to show them.
 */
function showFilters(open)
{
    getElement("filters", HTMLElement).hidden = !open;
    getElement("filters-toggle", HTMLButtonElement).setAttribute("aria-expanded", String(open));
}

/**
 * Shows the state in the URL: runs its search and opens its paper and PDF, each only
 * when it changed.
 *
 * @returns {Promise<void>}
 */
async function apply()
{
    const state = getState();
    fillForm(state);
    const search = new URLSearchParams(state);

    for (const name of ["key", "v", "pdf", "pg"])
    {
        search.delete(name);
    }

    if (search.toString() !== shown.search)
    {
        shown.search = search.toString();
        await runSearch(search);
    }
    else
    {
        markCurrentHit(state.get("key"));
    }

    const paper = [state.get("key"), state.get("v")].join("|");

    if (paper !== shown.paper)
    {
        shown.paper = paper;
        await openPaper(state);
    }

    showPdf(state);
}

/**
 * Runs a search and lists its results.
 *
 * @param {URLSearchParams} search - The search's part of the state.
 * @returns {Promise<void>}
 */
async function runSearch(search)
{
    if ([...search.keys()].every((name) => name === "page"))
    {
        results.replaceChildren(createElement("div", "empty", introduction));

        return;
    }

    results.replaceChildren(createElement("div", "empty", "Searching…"));
    const response = await fetch(`/api/search?${search}`);
    const data = await response.json();

    if (!response.ok)
    {
        results.replaceChildren(createElement("div", "error", data.detail ?? "The search failed."));

        return;
    }

    /** @type {SearchResponse} */
    const found = data;
    const pages = Math.max(1, Math.ceil(found.total / found.per_page));
    const list = createElement("div");
    list.append(...found.hits.map((hit) => createHitCard(hit)));
    results.replaceChildren(
        createElement("div", "count", found.total === 1 ? "1 paper" : `${found.total.toLocaleString()} papers`),
        list,
    );

    if (pages > 1)
    {
        results.append(createPager(found.page, pages));
    }

    if (!found.hits.length)
    {
        results.append(createElement("div", "empty", "No matches."));
    }

    markCurrentHit(getState().get("key"));
    results.scrollTop = 0;
}

/**
 * Creates the card for one search result.
 *
 * @param {Hit} hit - The result.
 * @returns {HTMLAnchorElement} The card, a link that opens the paper.
 */
function createHitCard(hit)
{
    const state = getState();
    state.set("key", hit.key);
    state.delete("v");
    state.delete("pg");
    const card = createStateLink("", state);
    card.className = "hit";
    card.dataset.key = hit.key;
    const details = [hit.key, hit.year || "n.d.", hit.tool, hit.translated ? "translated" : ""];
    card.append(
        createElement("div", "key", details.filter(Boolean).join(" · ")),
        createElement("div", "title", hit.title || "(no title)"),
        createElement("div", "meta", [hit.authors, hit.venue].filter(Boolean).join(" — ")),
    );
    const citedBy = [hit.genera, hit.clades].filter(Boolean).join("; ");

    if (citedBy)
    {
        card.append(createElement("div", "cited", `Cited by ${citedBy}`));
    }

    if (hit.snippet)
    {
        const snippet = createElement("div", "snip");
        snippet.innerHTML = hit.snippet;
        card.append(snippet);
    }

    return card;
}

/**
 * Creates the previous/next controls under the results.
 *
 * @param {number} page - The page of results shown.
 * @param {number} pages - The number of pages.
 * @returns {HTMLDivElement} The controls.
 */
function createPager(page, pages)
{
    const pager = createElement("div", "pager");
    const previous = createElement("button", "quiet", "Previous");
    const next = createElement("button", "quiet", "Next");
    previous.disabled = page <= 1;
    next.disabled = page >= pages;

    for (const [button, target] of /** @type {Array<[HTMLButtonElement, number]>} */ ([[previous, page - 1], [next, page + 1]]))
    {
        button.addEventListener("click", () =>
        {
            const state = getState();
            state.set("page", String(target));
            go(state);
        });
    }

    pager.append(previous, `Page ${page} of ${pages}`, next);

    return pager;
}

/**
 * Marks the result card of the open paper.
 *
 * @param {string | null} key - The open paper's key.
 */
function markCurrentHit(key)
{
    for (const card of results.querySelectorAll(".hit"))
    {
        if (card instanceof HTMLElement)
        {
            card.setAttribute("aria-current", String(card.dataset.key === key));
        }
    }
}

/**
 * Opens the state's paper in the reader, or empties the reader.
 *
 * @param {URLSearchParams} state - The state.
 * @returns {Promise<void>}
 */
async function openPaper(state)
{
    const key = state.get("key");

    if (!key)
    {
        reader.hidden = true;
        reader.replaceChildren();

        return;
    }

    reader.hidden = false;
    reader.replaceChildren(createElement("div", "empty", "Loading…"));
    const version = state.get("v") === "original" ? "original" : "text";
    const response = await fetch(`/api/paper/${encodeURIComponent(key)}?version=${version}`);

    if (!response.ok)
    {
        reader.replaceChildren(createElement("div", "error", (await response.json()).detail ?? "Could not open that paper."));

        return;
    }

    /** @type {Paper} */
    const paper = await response.json();
    const title = createElement("h1", "title", paper.title || paper.key);

    if (paper.parent)
    {
        title.textContent = `Supplement “${paper.name}” — ${title.textContent}`;
    }

    const citation = createElement("div", "cite");
    citation.append(
        createElement("span", "key", paper.key),
        ` · ${[paper.authors, paper.year, paper.venue].filter(Boolean).join(" · ")}`,
    );

    if (paper.doi)
    {
        const link = createElement("a", "", `doi:${paper.doi}`);
        link.href = `https://doi.org/${paper.doi}`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        citation.append(" · ", link);
    }

    const conversion = createElement("details", "conversion");
    conversion.append(
        createElement("summary", "", "Conversion record"),
        createElement("pre", "", JSON.stringify(paper.conversion, null, 2)),
    );
    const body = createElement("div", "body");
    body.lang = version === "original" ? "" : "en";
    body.innerHTML = paper.html;
    reader.replaceChildren(
        title,
        citation,
        createChips("Cited by genus", "genus", paper.genera),
        createChips("Cited by clade", "clade", paper.clades),
        createTools(paper, state),
        conversion,
        body,
    );
    pageMarks = labelPageMarks(body, paper.has_pdf);
    reader.scrollTop = 0;
    const firstMatch = highlight(body, getTerms(state.get("q") ?? "", state.get("syntax") === "1"));
    firstMatch?.scrollIntoView({ block: "center" });
}

/**
 * Creates the links that filter by the genera or clades citing a paper.
 *
 * @param {string} label - What the names are.
 * @param {string} field - The filter they set.
 * @param {Array<string>} names - The names.
 * @returns {HTMLDivElement} The links, or an empty element.
 */
function createChips(label, field, names)
{
    const chips = createElement("div");

    if (names.length)
    {
        chips.className = "chips";
        chips.append(
            createElement("span", "label", label),
            ...names.map((name) => createStateLink(name, new URLSearchParams([[field, name]]))),
        );
    }

    return chips;
}

/**
 * Creates the reader's controls: English/original, the PDF, the markdown and supplements.
 *
 * @param {Paper} paper - The open paper.
 * @param {URLSearchParams} state - The state.
 * @returns {HTMLDivElement} The controls.
 */
function createTools(paper, state)
{
    const tools = createElement("div", "tools");

    if (paper.has_original)
    {
        const tabs = createElement("span", "tabs");

        for (const [version, label] of [["text", "English"], ["original", "Original"]])
        {
            const button = createElement("button", "", label);
            button.type = "button";
            button.setAttribute("aria-pressed", String(paper.version === version));
            button.addEventListener("click", () =>
            {
                const next = getState();
                next.set("v", version === "original" ? version : "");
                go(next);
            });
            tabs.append(button);
        }

        tools.append(tabs);
    }

    if (paper.has_pdf)
    {
        const open = state.get("pdf") === "1";
        const button = createElement("button", "quiet", open ? "Hide PDF" : "Show PDF");
        button.type = "button";
        button.addEventListener("click", () =>
        {
            const next = getState();
            next.set("pdf", open ? "" : "1");
            go(next);
        });
        tools.append(button);
    }
    else
    {
        tools.append(createElement("span", "quiet-text", "No PDF"));
    }

    const markdown = createElement("a", "", "Markdown");
    markdown.href = `/md/${encodeURIComponent(paper.key)}${paper.version === "original" ? "?version=original" : ""}`;
    markdown.target = "_blank";
    tools.append(markdown);

    for (const supplement of paper.supplements)
    {
        tools.append(createStateLink(`Supplement: ${supplement.name}`, new URLSearchParams([["key", supplement.key]])));
    }

    if (paper.parent)
    {
        tools.append(createStateLink("Main paper", new URLSearchParams([["key", paper.parent]])));
    }

    return tools;
}

/**
 * Shows or hides the PDF pane, opening the state's PDF when it changed.
 *
 * @param {URLSearchParams} state - The state.
 */
function showPdf(state)
{
    const key = state.get("key");
    const open = Boolean(key) && state.get("pdf") === "1";
    pdfPane.hidden = !open;
    getElement("layout", HTMLElement).classList.toggle("with-pdf", open);

    if (!open || key === null)
    {
        if (pdfShown)
        {
            pdfView.close();
            pdfPane.replaceChildren();
            pdfShown = null;
        }
    }
    else if (pdfShown !== key)
    {
        pdfShown = key;
        pdfView.open(`/pdf/${encodeURIComponent(key)}`, Number(state.get("pg")) || getTextPage() || 1);
    }
}

// Keeping the text and the PDF on the same page. The server turns each page marker into
// <div data-page="N">. Whichever pane the reader last scrolled, clicked or typed in leads,
// and the other follows.

/**
 * Labels the page markers in a paper's text, as links to the PDF page when there is a PDF.
 *
 * @param {HTMLElement} body - The paper's text.
 * @param {boolean} hasPdf - Whether the paper has a PDF.
 * @returns {Array<HTMLElement>} The markers, in order.
 */
function labelPageMarks(body, hasPdf)
{
    const marks = [...body.querySelectorAll("div[data-page]")].filter((mark) => mark instanceof HTMLElement);

    for (const mark of marks)
    {
        const page = Number(mark.dataset.page);
        mark.className = "page-mark";

        if (hasPdf)
        {
            const button = createElement("button", "", `PDF page ${page}`);
            button.type = "button";
            button.addEventListener("click", () => openPdfAt(page));
            mark.append(button);
        }
        else
        {
            mark.textContent = `PDF page ${page}`;
        }
    }

    return marks;
}

/**
 * The page of the text at the top of the reader: the last marker above it.
 *
 * @returns {number} The page, or 0 when the paper has no markers.
 */
function getTextPage()
{
    const top = reader.scrollTop + 24;
    let page = pageMarks.length ? Number(pageMarks[0].dataset.page) : 0;

    for (const mark of pageMarks)
    {
        if (mark.offsetTop > top)
        {
            break;
        }

        page = Number(mark.dataset.page);
    }

    return page;
}

/**
 * Highlights the marker of the page in view.
 *
 * @param {number} page - The page.
 */
function highlightPageMark(page)
{
    if (page !== highlightedPage)
    {
        highlightedPage = page;

        for (const mark of pageMarks)
        {
            mark.classList.toggle("current", Number(mark.dataset.page) === page);
        }
    }
}

/**
 * Shows a page of the PDF, opening the PDF pane if needed.
 *
 * @param {number} page - The page.
 */
function openPdfAt(page)
{
    leader = "text";

    if (!pdfPane.hidden && pdfShown)
    {
        pdfView.goTo(page);
    }
    else
    {
        const state = getState();
        state.set("pdf", "1");
        state.set("pg", String(page));
        go(state);
    }
}

/**
 * Scrolls the text to the page the PDF shows, when the PDF leads.
 *
 * @param {number} page - The page in view in the PDF.
 */
function followPdf(page)
{
    if (leader === "pdf" && pageMarks.length)
    {
        const target = pageMarks.find((mark) => Number(mark.dataset.page) >= page);

        if (target)
        {
            reader.scrollTo({ top: target.offsetTop - 8 });
        }

        highlightPageMark(page);
    }
}

/**
 * Moves the PDF to the page the text shows, when the text leads; at most once a frame.
 */
function followText()
{
    if (!syncQueued && pageMarks.length)
    {
        syncQueued = true;
        requestAnimationFrame(() =>
        {
            syncQueued = false;
            const page = getTextPage();
            highlightPageMark(page);

            if (leader === "text" && pdfShown && page && page !== pdfView.current)
            {
                pdfView.goTo(page);
            }
        });
    }
}

/**
 * The words to highlight in the paper: the query's words and phrases. A match is any word
 * starting with a term, which covers most of what the index's stemming matches
 * ("dinosaur" finds "dinosaurs").
 *
 * @param {string} query - The query.
 * @param {boolean} syntax - Whether the query uses search syntax, whose operators are dropped.
 * @returns {Array<string>} The terms.
 */
function getTerms(query, syntax)
{
    const text = syntax ? query.replace(/\b(NEAR|AND|OR|NOT)\b|[A-Za-z_]+:|[(),\d]+(?=\))/g, " ") : query;

    return [...text.matchAll(/"([^"]*)"|(\S+)/g)]
        .map((match) => (match[1] ?? match[2]).replace(/[*"]/g, "").trim())
        .filter((term) => term.length > 1);
}

/**
 * Wraps every occurrence of the terms in the text in <mark>.
 *
 * @param {HTMLElement} root - The text.
 * @param {Array<string>} terms - The terms.
 * @returns {HTMLElement | null} The first mark, if any.
 */
function highlight(root, terms)
{
    if (!terms.length)
    {
        return null;
    }

    const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"));
    const pattern = new RegExp(`(?<![\\p{L}\\p{N}])(?:${escaped.join("|")})`, "giu");
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];

    while (walker.nextNode())
    {
        nodes.push(walker.currentNode);
    }

    /** @type {HTMLElement | null} */
    let first = null;

    for (const node of nodes)
    {
        const text = node.nodeValue ?? "";
        const matches = [...text.matchAll(pattern)];

        if (matches.length)
        {
            const fragment = document.createDocumentFragment();
            let position = 0;

            for (const match of matches)
            {
                const mark = createElement("mark", "", match[0]);
                fragment.append(text.slice(position, match.index), mark);
                first ??= mark;
                position = match.index + match[0].length;
            }

            fragment.append(text.slice(position));
            node.parentNode?.replaceChild(fragment, node);
        }
    }

    return first;
}

/**
 * Fills the filters' lists and the header's paper count.
 *
 * @returns {Promise<void>}
 */
async function loadFacets()
{
    /** @type {Facets} */
    const facets = await (await fetch("/api/facets")).json();

    for (const [id, names] of /** @type {Array<[string, Array<string>]>} */ ([["genera-list", facets.genera], ["clades-list", facets.clades]]))
    {
        getElement(id, HTMLDataListElement).replaceChildren(...names.map((name) =>
        {
            const option = createElement("option");
            option.value = name;

            return option;
        }));
    }

    const tool = getField("tool");
    tool.append(...facets.tools.map((name) => createElement("option", "", name)));
    tool.value = getState().get("tool") ?? "";

    if (facets.papers)
    {
        const count = `${facets.papers.toLocaleString()} paper${facets.papers === 1 ? "" : "s"}`;
        getElement("stats", HTMLElement).textContent = `${count}, ${facets.years[0]}–${facets.years[1]}`;
    }
}

form.addEventListener("submit", (event) =>
{
    event.preventDefault();
    go(readForm());
});
getElement("filters-toggle", HTMLButtonElement).addEventListener("click", () =>
{
    // `hidden` is true, false or "until-found"; anything but false is closed
    showFilters(getElement("filters", HTMLElement).hidden !== false);
});
window.addEventListener("popstate", () => apply());
reader.addEventListener("scroll", () => followText(), { passive: true });

for (const [pane, name] of /** @type {Array<[HTMLElement, "text" | "pdf"]>} */ ([[reader, "text"], [pdfPane, "pdf"]]))
{
    for (const type of ["wheel", "pointerdown", "keydown", "touchstart"])
    {
        pane.addEventListener(type, () =>
        {
            leader = name;
        }, { passive: true });
    }
}

loadFacets();
apply();
