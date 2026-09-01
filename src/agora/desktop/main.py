"""Windows desktop entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from agora.application import AgoraApplication
from agora.desktop.window import AgoraMainWindow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-desktop")
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Folder containing KANBAN and AGENTS (default: current directory)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    qt_app = QApplication.instance() or QApplication(sys.argv[:1])
    qt_app.setApplicationName("Agora Desktop")
    qt_app.setOrganizationName("Agora")
    service = AgoraApplication(args.workspace)
    service.initialize()
    window = AgoraMainWindow(service)
    window.show()
    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
