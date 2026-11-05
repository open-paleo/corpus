"""The `corpus` command: one entry point for the pipeline.

    corpus                          list the commands
    corpus status [--missing]       what the corpus holds, and what is missing
    corpus repair all [--apply]     every text-layer repair, one pass per paper
    corpus repair dash --key K      one repair (dash, symbols, accents, diaeresis,
                                    ref_links, fused_calls, control_glyphs)
    corpus intake acquire K ...     and the other commands the scripts declare
    corpus run SCRIPT [args]        any script in scripts/, by file name

Everything after the command name is passed to it unchanged, so
`corpus convert marker --help` shows that script's own options.

A script becomes a command by declaring it in a comment line near its top:

    # corpus-command: convert marker | PDF -> markdown via marker (text layer)

(one or two words, then "|" and a one-line summary). The command list is read
from the scripts present in ROOT/scripts/, without importing them, so a
checkout lists exactly what it can run, and a private repository built on this
one adds commands by adding scripts, without editing this file. `status`,
`repair` and `run` are built in.
"""
from __future__ import annotations

import re
import runpy
import sys
from pathlib import Path

DECLARATION = re.compile(r"^# corpus-command:\s*([a-z][a-z0-9-]*(?: [a-z][a-z0-9-]*)?)"
                         r"\s*\|\s*(.+?)\s*$", re.MULTILINE)
BUILT_IN = {"status", "repair", "run", "help", "list"}


def commands(scripts: Path | None = None) -> dict[tuple[str, ...], tuple[str, str]]:
    """{(group, name) or (name,): (script stem, summary)} declared by the
    scripts in `scripts` (default ROOT/scripts). Two scripts declaring the
    same command, or one shadowing a built-in, is an error."""
    if scripts is None:
        from corpus.config import ROOT
        scripts = ROOT / "scripts"
    found: dict[tuple[str, ...], tuple[str, str]] = {}
    for path in sorted(Path(scripts).glob("*.py")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
        m = DECLARATION.search(head)
        if not m:
            continue
        words = tuple(m.group(1).split())
        # `repair` takes script commands beside its modules (repairs that read
        # the PDF's text layer but are not corpus.repair modules), as long as
        # the name is not one of the modules or `all`.
        shares_repair = (len(words) == 2 and words[0] == "repair"
                         and words[1] not in ("all", *REPAIR_NAMES))
        if words[0] in BUILT_IN and not shares_repair:
            raise SystemExit(f"corpus: {path.name} declares built-in command {' '.join(words)!r}")
        if words in found:
            raise SystemExit(f"corpus: {' '.join(words)!r} declared by both "
                             f"{found[words][0]}.py and {path.name}")
        found[words] = (path.stem, m.group(2))
    return found


REPAIR_NAMES = ["dash", "symbols", "accents", "diaeresis", "ref_links", "fused_calls",
                "control_glyphs"]


def usage() -> str:
    rows = [("status", "held / converted / indexed / missing counts (--missing lists)"),
            ("repair all", "every text-layer repair, one pass per paper")]
    rows += [(f"repair {n}", "one repair") for n in REPAIR_NAMES]
    rows += [(" ".join(words), summary) for words, (_, summary) in sorted(commands().items())]
    rows.append(("run SCRIPT", "any script in scripts/ (file name, .py optional)"))
    width = max(len(name) for name, _ in rows)
    return "\n".join([__doc__.split("\n\n")[0], "", "Commands:"]
                     + [f"  {name:<{width}}  {summary}" for name, summary in rows])


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
            scripted = commands()
            if ("repair", name) in scripted:
                return run_script(scripted[("repair", name)][0], rest)
            names = ["all", *REPAIR_NAMES, *(w[1] for w in scripted if w[0] == "repair")]
            print(f"corpus: unknown repair {name!r} (one of: {', '.join(names)})", file=sys.stderr)
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
    table = commands()
    for n in (2, 1):
        words = tuple(argv[:n])
        if words in table:
            return run_script(table[words][0], argv[n:])
    print(f"corpus: unknown command {' '.join(argv[:2])!r}\n\n{usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
