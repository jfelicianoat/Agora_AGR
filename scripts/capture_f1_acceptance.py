"""Exercise the F1 desktop with QtTest and capture the resulting visible window."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from agora.application import AgoraApplication
from agora.board import BoardState
from agora.desktop.window import AgoraMainWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("screenshot", type=Path)
    args = parser.parse_args()
    qt_app = QApplication([])
    window = AgoraMainWindow(AgoraApplication(args.workspace), refresh_interval_ms=0)
    window.show()
    qt_app.processEvents()

    dispatch = window.findChild(QPushButton, "dispatchButton")
    if dispatch is None:
        raise RuntimeError("dispatch button not found")
    QTest.mouseClick(dispatch, Qt.MouseButton.LeftButton)
    qt_app.processEvents()

    done = _state_item(window, BoardState.DONE)
    if done.childCount() < 1:
        raise RuntimeError("no completed CARD rendered")
    child = done.child(done.childCount() - 1)
    index = window.board_tree.indexFromItem(child)
    QTest.mouseClick(
        window.board_tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=window.board_tree.visualRect(index).center(),
    )
    qt_app.processEvents()
    QTest.mouseClick(
        window.tabs.tabBar(),
        Qt.MouseButton.LeftButton,
        pos=window.tabs.tabBar().tabRect(1).center(),
    )
    qt_app.processEvents()

    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
    captured = window.grab().save(str(args.screenshot))
    result = {
        "completed_cards": done.childCount(),
        "record_visible": "CARD closed." in window.record_view.toPlainText(),
        "screenshot": captured,
        "screenshot_path": str(args.screenshot.resolve()),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if captured and result["record_visible"] else 1


def _state_item(window: AgoraMainWindow, state: BoardState):
    for index in range(window.board_tree.topLevelItemCount()):
        item = window.board_tree.topLevelItem(index)
        if item.data(0, Qt.ItemDataRole.UserRole) == state.value:
            return item
    raise RuntimeError(f"state not rendered: {state.value}")


if __name__ == "__main__":
    raise SystemExit(main())
