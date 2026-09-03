from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTreeWidgetItem

import agora.desktop.window as window_module
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.desktop.window import AgoraMainWindow
from conftest import create_card, write_profile


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    yield application


def _child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    child = item.child(index)
    assert child is not None, f"el nodo no tiene hijo {index}"
    return child


def _state_item(window: AgoraMainWindow, state: BoardState) -> QTreeWidgetItem:
    for index in range(window.board_tree.topLevelItemCount()):
        item = window.board_tree.topLevelItem(index)
        if item is not None and item.data(0, Qt.ItemDataRole.UserRole) == state.value:
            return item
    raise AssertionError(f"state not rendered: {state.value}")


def test_window_refresh_reflects_real_board_movement(tmp_path: Path, qt_app: QApplication) -> None:
    service = AgoraApplication(tmp_path)
    service.initialize()
    create_card(service.board)
    window = AgoraMainWindow(service, refresh_interval_ms=0)

    assert _state_item(window, BoardState.PENDING).childCount() == 1
    service.board.claim("task.md", "worker")
    window.refresh()
    qt_app.processEvents()

    assert _state_item(window, BoardState.PENDING).childCount() == 0
    assert _state_item(window, BoardState.IN_PROGRESS).childCount() == 1
    window.close()


def test_corrupt_card_is_visible_in_board_and_errors(tmp_path: Path, qt_app: QApplication) -> None:
    service = AgoraApplication(tmp_path)
    service.initialize()
    path = service.board.directory(BoardState.PENDING) / "broken.md"
    path.write_text("---\nfunction: [broken\n---\n", encoding="utf-8")
    window = AgoraMainWindow(service, refresh_interval_ms=0)
    qt_app.processEvents()

    pending = _state_item(window, BoardState.PENDING)
    assert pending.childCount() == 1
    assert "⚠" in _child(pending, 0).text(0)
    assert window.error_summary.text() == "1 error(es) visible(s)"
    assert "broken.md" in window.error_view.toPlainText()
    window.close()


def test_dispatch_button_completes_card_and_renders_record(
    tmp_path: Path, qt_app: QApplication
) -> None:
    service = AgoraApplication(tmp_path)
    service.initialize()
    write_profile(
        tmp_path / "AGENTS",
        "summarizer",
        handles=["summarize"],
        skills=["summarize-text"],
    )
    create_card(service.board, destination="artifacts/from-desktop.txt")
    window = AgoraMainWindow(service, refresh_interval_ms=0)
    window.show()
    dispatch = window.findChild(QPushButton, "dispatchButton")
    assert dispatch is not None

    QTest.mouseClick(dispatch, Qt.MouseButton.LeftButton)
    qt_app.processEvents()

    assert _state_item(window, BoardState.PENDING).childCount() == 0
    done = _state_item(window, BoardState.DONE)
    assert done.childCount() == 1
    window.board_tree.setCurrentItem(_child(done, 0))
    qt_app.processEvents()
    assert "CARD closed." in window.record_view.toPlainText()
    assert (tmp_path / "artifacts" / "from-desktop.txt").is_file()
    window.close()


def test_closing_and_reopening_window_preserves_board_state(
    tmp_path: Path, qt_app: QApplication
) -> None:
    first_service = AgoraApplication(tmp_path)
    first_service.initialize()
    create_card(first_service.board)
    first = AgoraMainWindow(first_service, refresh_interval_ms=0)
    first.close()

    second_service = AgoraApplication(tmp_path)
    second_service.initialize()
    second = AgoraMainWindow(second_service, refresh_interval_ms=0)
    qt_app.processEvents()

    assert _state_item(second, BoardState.PENDING).childCount() == 1
    assert _child(_state_item(second, BoardState.PENDING), 0).text(0) == "task.md"
    second.close()


def test_window_module_has_no_direct_filesystem_write_surface() -> None:
    tree = ast.parse(Path(window_module.__file__).read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert imported_roots.isdisjoint({"os", "pathlib", "shutil"})
    assert calls.isdisjoint({"open", "write", "remove", "rename", "unlink"})
