"""The `corpus` command: one entry point for the pipeline.

    corpus                          list the commands
    corpus status [--missing]       what the corpus holds, and what is missing
    corpus repair all [--apply]     every text-layer repair, one pass per paper
    corpus repair dash --key K      one repair (dash, symbols, accents, diaeresis,
                                    ref_links, fused_calls, control_glyphs)
    corpus intake acquire K ...     and the other named commands below
    corpus run SCRIPT [args]        any script in scripts/, by file name

Everything after the command name is passed to it unchanged, so
`corpus convert marker --help` shows that script's own options. The named
commands run the scripts in ROOT/scripts/ (the files the docs and skills
name); the repairs run from the package. A checkout need not hold every
script (the public repository leaves some out): commands whose script is
missing are not listed and say so when run.
"""
from __future__ import annotations

import runpy
import sys

# (group, name) -> script file name in scripts/, with a one-line summary.
COMMANDS: dict[tuple[str, ...], tuple[str, str]] = {
    ("intake", "discover"): ("intake_discover", "what is pending in pdfs/, with path hints"),
    ("intake", "acquire"): ("intake_acquire", "record dropped PDFs as acquired"),
    ("intake", "qc"): ("intake_qc", "bytes-per-page triage of a converted batch"),
    ("convert", "marker"): ("convert_with_marker", "PDF -> markdown via marker (text layer)"),
    ("convert", "vision"): ("convert_with_vision", "PDF -> markdown via Claude vision OCR"),
    ("convert", "spread"): ("ocr_spread_pdf", "vision OCR of two-page spreads"),
    ("convert", "poster"): ("tile_ocr_poster", "vision OCR of a single-canvas poster"),
    ("convert", "doc"): ("convert_doc_to_markdown", ".doc/.docx -> markdown"),
    ("convert", "bhl"): ("convert_bhl_to_markdown", "BHL page images -> markdown"),
    ("convert", "translate"): ("translate_markdown", "translate non-English markdown"),
    ("convert", "recover"): ("recover_blocked_pages", "re-OCR content-filter-blocked pages"),
    ("convert", "repair-truncated"): ("repair_truncated_pages", "re-OCR pages cut at the old token cap"),
    ("fetch", "unpaywall"): ("fetch_unpaywall", "tier 1: open-access PDFs via Unpaywall"),
    ("fetch", "unpaywall-by-doi"): ("fetch_unpaywall_by_doi", "Unpaywall for a key/DOI list"),
    ("fetch", "retry-unpaywall"): ("retry_unpaywall", "retry tier-1 download failures"),
    ("fetch", "oa-direct"): ("fetch_oa_direct", "tier 4: DOI landing page -> citation_pdf_url"),
    ("fetch", "pmc"): ("fetch_pmc_oa", "PMC Open Access subset (NCBI dataset) by DOI"),
    ("bhl", "setup"): ("bhl_setup", "download the BHL data dumps"),
    ("bhl", "index"): ("bhl_index", "build the BHL SQLite index"),
    ("bhl", "match"): ("bhl_match", "match citations to BHL parts"),
    ("bhl", "item-match"): ("bhl_item_match", "item-level BHL matching"),
    ("bhl", "fetch"): ("bhl_fetch", "fetch BHL OCR and page images"),
    ("bhl", "stitch"): ("stitch_bhl_pdfs", "stitch BHL page images into PDFs"),
    ("pdf", "extract-pages"): ("extract_pdf_pages", "cut a PDF down to a page range, in place"),
    ("pdf", "append-pages"): ("append_pdf_pages", "append pages from a source PDF"),
    ("pdf", "compress"): ("compress_pdf", "re-render an oversized scan to JPEG (drops text)"),
    ("pdf", "stitch"): ("stitch_source_images", "source_images/{key}/ -> pdfs/{key}.pdf"),
    ("pdf", "photocopier"): ("photocopier_stitch", "stitch phone photos of a volume"),
    ("pdf", "images"): ("extract_pdf_images", "rip embedded images into pdf_images/"),
    ("qc", "scan"): ("scan_markdown_quality", "PUA garbage, CJK, image-only stubs"),
    ("qc", "languages"): ("audit_languages", "non-English bodies (TRANSLATION_AUDIT.md)"),
    ("qc", "spillover"): ("scan_spillover", "markdown holding neighbouring articles"),
    ("qc", "textlayer"): ("audit_vision_textlayer", "vision output vs the PDF text layer"),
    ("qc", "vision"): ("eval_vision_quality", "heuristic flags on vision markdown"),
    ("clean", "frontmatter"): ("strip_frontmatter", "publisher cover boilerplate"),
    ("clean", "running-heads"): ("strip_running_heads", "repeated per-page heads and feet"),
    ("clean", "despace"): ("despace_ocr", "rejoin OCR word splits"),
    ("clean", "gesperrt"): ("despace_gesperrt", "rejoin letterspaced emphasis"),
    ("clean", "ligatures"): ("fix_dropped_ligatures", "restore f- and Th-ligatures a text layer dropped"),
    ("manifest", "refresh"): ("refresh_manifest", "merge the bib and YAMLs into the manifest"),
    ("manifest", "refresh-dois"): ("refresh_dois", "DOI-only refresh from the bib"),
    ("report", "missing"): ("generate_missing_report", "regenerate MISSING_PAPERS.md"),
    ("index",): ("indexer", "build/refresh the FTS5 search index"),
    ("search",): ("search", "query the search index"),
    ("web",): ("webui", "browse the index in a browser (127.0.0.1)"),
}

REPAIR_NAMES = ["dash", "symbols", "accents", "diaeresis", "ref_links", "fused_calls",
                "control_glyphs"]


def _available(script: str) -> bool:
    from corpus.config import ROOT
    return (ROOT / "scripts" / f"{script}.py").is_file()


def usage() -> str:
    lines = [__doc__.split("\n\n")[0], "", "Commands:",
             "  status                held / converted / indexed / missing counts (--missing lists)",
             "  repair all            every text-layer repair, one pass per paper"]
    lines += [f"  repair {n:<14} one repair" for n in REPAIR_NAMES]
    for words, (script, summary) in COMMANDS.items():
        if not _available(script):
            continue
        lines.append(f"  {' '.join(words):<21} {summary}")
    lines.append("  run SCRIPT            any script in scripts/ (file name, .py optional)")
    return "\n".join(lines)


def run_script(name: str, args: list[str]) -> int:
    """Run scripts/{name}.py as __main__ with args, as if from the shell."""
    from corpus.config import ROOT
    scripts = ROOT / "scripts"
    path = scripts / (name if name.endswith(".py") else f"{name}.py")
    if not path.is_file():
        print(f"corpus: no script {path}", file=sys.stderr)
        return 2
    # Scripts import their neighbours (scripts/ is sys.path[0] when run
    # directly), so give them the same path.
    sys.path.insert(0, str(scripts))
    sys.argv = [str(path), *args]
    try:
        runpy.run_path(str(path), run_name="__main__")
    except SystemExit as e:
        code = e.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help", "list"):
        print(usage())
        return 0
    if argv[0] == "status":
        from corpus.status import main as status_main
        return status_main(argv[1:])
    if argv[0] == "repair":
        if len(argv) < 2 or argv[1] in ("-h", "--help"):
            print(usage())
            return 0
        name, rest = argv[1], argv[2:]
        if name == "all":
            from corpus.repair import run_all
            return run_all(rest)
        if name not in REPAIR_NAMES:
            print(f"corpus: unknown repair {name!r} (one of: all, {', '.join(REPAIR_NAMES)})",
                  file=sys.stderr)
            return 2
        import importlib
        module = importlib.import_module(f"corpus.repair.{name}")
        sys.argv = [f"corpus repair {name}", *rest]
        return module.main() or 0
    if argv[0] == "run":
        if len(argv) < 2:
            print("usage: corpus run SCRIPT [args]", file=sys.stderr)
            return 2
        return run_script(argv[1], argv[2:])
    for n in (2, 1):
        words = tuple(argv[:n])
        if words in COMMANDS:
            script = COMMANDS[words][0]
            if not _available(script):
                print(f"corpus: {' '.join(words)} needs scripts/{script}.py, which this "
                      "checkout does not include", file=sys.stderr)
                return 2
            return run_script(script, argv[n:])
    print(f"corpus: unknown command {' '.join(argv[:2])!r}\n\n{usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
