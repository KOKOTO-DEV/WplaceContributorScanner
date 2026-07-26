from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wplace_scanner import APP_VERSION
from wplace_scanner.console_i18n import console_text, detect_console_language
from wplace_scanner.webapp import serve


def _explicit_language(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("--lang="):
            return value.split("=", 1)[1]
    return None


def main() -> None:
    detected_language = detect_console_language(_explicit_language(sys.argv[1:]))
    parser = argparse.ArgumentParser(description=console_text("description", detected_language))
    parser.add_argument("--version", action="version", version=f"Wplace Contributor Scanner {APP_VERSION}")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lang", default=None, help="Console language: ko, en, ja, zh-CN")
    parser.add_argument("--no-browser", dest="no_browser", action="store_true")
    parser.add_argument("--browser", dest="no_browser", action="store_false")
    parser.set_defaults(no_browser=True)
    args = parser.parse_args()
    serve(
        Path(__file__).resolve().parent,
        args.host,
        args.port,
        not args.no_browser,
        console_language=args.lang or detected_language,
    )


if __name__ == "__main__":
    main()
