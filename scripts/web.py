#!/usr/bin/env python3
# corpus-command: web | search, read and check papers in a browser (127.0.0.1)
"""
Start the corpus web app: search the index, read a paper's markdown, and check
it against its PDF side by side.

It reads the index, markdown/ and pdfs/ and changes nothing. It is for one
person on this machine: there is no login, so it listens on 127.0.0.1 only.

Usage:
  ./.runtime/venv/bin/python scripts/web.py [--port 8765] [--open]
"""
from __future__ import annotations

import argparse
import threading
import webbrowser


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open it in the default browser")
    args = ap.parse_args()

    import uvicorn
    from corpus.web.app import create_app, loopback_hosts

    host = "127.0.0.1"
    url = f"http://{host}:{args.port}/"
    app = create_app(allowed_hosts=loopback_hosts(host, args.port))
    print(f"corpus web: {url}  (Ctrl-C to stop)")
    if args.open:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(app, host=host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
