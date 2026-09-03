"""PySide6 presentation layer; all domain access goes through AgoraApplication."""

from __future__ import annotations

import json
from typing import Protocol

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QFrame,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agora import __version__
from agora.application import AgoraSnapshot, CardDetail
from agora.board import BoardState
from agora.dispatcher import DispatchOutcome

_STATE_LABELS = {
    BoardState.PENDING: "Pendientes",
    BoardState.IN_PROGRESS: "En curso",
    BoardState.DONE: "Terminadas",
    BoardState.BLOCKED: "Bloqueadas",
    BoardState.SCHEDULED: "Programadas",
    BoardState.ARCHIVE: "Archivo",
}

_STATE_COLORS = {
    BoardState.PENDING: "#d89b42",
    BoardState.IN_PROGRESS: "#4d96ff",
    BoardState.DONE: "#47b881",
    BoardState.BLOCKED: "#e05d5d",
    BoardState.SCHEDULED: "#9b7ede",
    BoardState.ARCHIVE: "#7b8494",
}


class DesktopService(Protocol):
    def snapshot(self) -> AgoraSnapshot: ...

    def card_detail(self, state: BoardState, filename: str) -> CardDetail: ...

    def dispatch_once(self, *, dry_run: bool = False) -> tuple[DispatchOutcome, ...]: ...


class AgoraMainWindow(QMainWindow):
    def __init__(
        self,
        service: DesktopService,
        *,
        refresh_interval_ms: int = 1500,
    ) -> None:
        super().__init__()
        self.service = service
        self._snapshot: AgoraSnapshot | None = None
        self.setObjectName("agoraMainWindow")
        self.setWindowTitle(f"Agora Desktop {__version__}")
        self.resize(1240, 780)
        self._build_ui()
        self._apply_style()
        self.refresh()
        self._timer = QTimer(self)
        self._timer.setInterval(max(250, refresh_interval_ms))
        self._timer.timeout.connect(self.refresh)
        if refresh_interval_ms > 0:
            self._timer.start()

    def _build_ui(self) -> None:
        toolbar = self.addToolBar("Acciones")
        toolbar.setMovable(False)
        refresh_action = QAction("Actualizar", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.refresh)
        toolbar.addAction(refresh_action)
        toolbar.addSeparator()

        self.dry_run = QCheckBox("Simular")
        self.dry_run.setObjectName("dryRunCheck")
        toolbar.addWidget(self.dry_run)
        dispatch_button = QPushButton("Ejecutar ronda")
        dispatch_button.setObjectName("dispatchButton")
        dispatch_button.clicked.connect(self.run_dispatcher)
        toolbar.addWidget(dispatch_button)

        self.dispatcher_badge = QLabel("Dispatcher: idle")
        self.dispatcher_badge.setObjectName("dispatcherStatus")
        toolbar.addSeparator()
        toolbar.addWidget(self.dispatcher_badge)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._board_panel())
        splitter.addWidget(self._detail_panel())
        splitter.setSizes([500, 740])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Preparando Agora…")

    def _board_panel(self) -> QWidget:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        heading = QLabel("BOARD")
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        self.board_tree = QTreeWidget()
        self.board_tree.setObjectName("boardTree")
        self.board_tree.setHeaderLabels(["CARD", "Función", "Prioridad"])
        self.board_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.board_tree.itemSelectionChanged.connect(self._show_selected_card)
        self.board_tree.header().setStretchLastSection(False)
        self.board_tree.header().resizeSection(0, 260)
        self.board_tree.header().resizeSection(1, 120)
        layout.addWidget(self.board_tree)
        return panel

    def _detail_panel(self) -> QWidget:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailTabs")
        self.tabs.addTab(self._card_tab(), "CARD")
        self.tabs.addTab(self._record_tab(), "Record")
        self.tabs.addTab(self._profiles_tab(), "Perfiles")
        self.tabs.addTab(self._activity_tab(), "Actividad")
        self.tabs.addTab(self._errors_tab(), "Errores")
        layout.addWidget(self.tabs)
        return panel

    def _card_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        identity = QFrame()
        form = QFormLayout(identity)
        self.card_title = QLabel("Selecciona una CARD")
        self.card_title.setObjectName("cardTitle")
        self.card_location = QLabel("—")
        self.card_agent = QLabel("—")
        form.addRow("Solicitud", self.card_title)
        form.addRow("Ubicación", self.card_location)
        form.addRow("Agente", self.card_agent)
        layout.addWidget(identity)
        self.card_document = QPlainTextEdit()
        self.card_document.setObjectName("cardDocument")
        self.card_document.setReadOnly(True)
        self.card_document.setPlaceholderText("El contrato CARD aparecerá aquí.")
        layout.addWidget(self.card_document)
        return panel

    def _record_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.record_view = QPlainTextEdit()
        self.record_view.setObjectName("recordView")
        self.record_view.setReadOnly(True)
        self.record_view.setPlaceholderText("Selecciona una CARD para leer su Record.")
        layout.addWidget(self.record_view)
        return panel

    def _profiles_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.profile_table = QTableWidget(0, 4)
        self.profile_table.setObjectName("profileTable")
        self.profile_table.setHorizontalHeaderLabels(["Perfil", "Versión", "Función", "Skills"])
        self.profile_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.profile_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.profile_table.itemSelectionChanged.connect(self._show_selected_profile)
        self.profile_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.profile_table)
        self.profile_detail = QPlainTextEdit()
        self.profile_detail.setObjectName("profileDetail")
        self.profile_detail.setReadOnly(True)
        self.profile_detail.setMaximumHeight(190)
        layout.addWidget(self.profile_detail)
        return panel

    def _activity_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.activity_view = QPlainTextEdit()
        self.activity_view.setObjectName("activityView")
        self.activity_view.setReadOnly(True)
        layout.addWidget(self.activity_view)
        return panel

    def _errors_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.error_summary = QLabel("Sin errores")
        self.error_summary.setObjectName("errorSummary")
        layout.addWidget(self.error_summary)
        self.error_view = QPlainTextEdit()
        self.error_view.setObjectName("errorView")
        self.error_view.setReadOnly(True)
        layout.addWidget(self.error_view)
        return panel

    def refresh(self) -> None:
        selected = self._selected_card_key()
        try:
            snapshot = self.service.snapshot()
        except Exception as exc:  # the UI must keep running and expose unexpected failures
            self.error_summary.setText("No se pudo actualizar")
            self.error_view.setPlainText(f"{type(exc).__name__}: {exc}")
            self.statusBar().showMessage("Error al actualizar")
            return
        self._snapshot = snapshot
        self._render_board(snapshot, selected)
        self._render_profiles(snapshot)
        self._render_activity(snapshot)
        self._render_errors(snapshot)
        self.dispatcher_badge.setText(f"Dispatcher: {snapshot.dispatcher_status}")
        total = len(snapshot.cards)
        blocked = len(snapshot.cards_in(BoardState.BLOCKED))
        self.statusBar().showMessage(
            f"{total} CARD{'s' if total != 1 else ''} · {blocked} bloqueada(s) · "
            f"actualizado {snapshot.captured_at}"
        )

    def run_dispatcher(self) -> None:
        self.dispatcher_badge.setText("Dispatcher: ejecutando…")
        try:
            self.service.dispatch_once(dry_run=self.dry_run.isChecked())
        except Exception as exc:
            self.error_summary.setText("Fallo del dispatcher")
            self.error_view.setPlainText(f"{type(exc).__name__}: {exc}")
            container = self.error_view.parentWidget()
            if container is not None:
                self.tabs.setCurrentWidget(container)
        self.refresh()

    def _render_board(
        self, snapshot: AgoraSnapshot, selected: tuple[BoardState, str] | None
    ) -> None:
        self.board_tree.blockSignals(True)
        self.board_tree.clear()
        restored: QTreeWidgetItem | None = None
        for state in BoardState:
            cards = snapshot.cards_in(state)
            parent = QTreeWidgetItem([f"{_STATE_LABELS[state]}  ·  {len(cards)}", "", ""])
            parent.setData(0, Qt.ItemDataRole.UserRole, state.value)
            font = QFont(parent.font(0))
            font.setBold(True)
            parent.setFont(0, font)
            parent.setForeground(0, QColor(_STATE_COLORS[state]))
            self.board_tree.addTopLevelItem(parent)
            for card in cards:
                label = f"⚠ {card.filename}" if card.corrupt else card.filename
                item = QTreeWidgetItem([label, card.function, card.priority])
                item.setData(0, Qt.ItemDataRole.UserRole, state.value)
                item.setData(1, Qt.ItemDataRole.UserRole, card.filename)
                item.setToolTip(0, card.request)
                if card.corrupt:
                    item.setForeground(0, QColor("#e05d5d"))
                parent.addChild(item)
                if selected == (state, card.filename):
                    restored = item
            parent.setExpanded(True)
        self.board_tree.blockSignals(False)
        if restored is not None:
            self.board_tree.setCurrentItem(restored)

    def _render_profiles(self, snapshot: AgoraSnapshot) -> None:
        self.profile_table.setRowCount(len(snapshot.profiles))
        for row, profile in enumerate(snapshot.profiles):
            values = (profile.name, profile.version, profile.function, ", ".join(profile.skills))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value or "—")
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.profile_table.setItem(row, column, item)
        self.profile_table.resizeColumnsToContents()

    def _render_activity(self, snapshot: AgoraSnapshot) -> None:
        text = "\n".join(
            f"{entry.timestamp}  [{entry.level.upper()}]  {entry.message}"
            for entry in snapshot.activity
        )
        self.activity_view.setPlainText(text or "Aún no hay actividad en esta sesión.")

    def _render_errors(self, snapshot: AgoraSnapshot) -> None:
        count = len(snapshot.errors)
        self.error_summary.setText("Sin errores" if not count else f"{count} error(es) visible(s)")
        self.error_view.setPlainText(
            "\n\n".join(f"{error.source}\n{error.message}" for error in snapshot.errors)
            or "No se detectaron contratos corruptos."
        )

    def _show_selected_card(self) -> None:
        key = self._selected_card_key()
        if key is None:
            return
        state, filename = key
        try:
            detail = self.service.card_detail(state, filename)
        except Exception as exc:
            self.card_title.setText(filename)
            self.card_location.setText(state.value)
            self.card_agent.setText("—")
            self.card_document.setPlainText(f"CARD ilegible\n\n{type(exc).__name__}: {exc}")
            self.record_view.setPlainText("No se puede mostrar el Record.")
            self.tabs.setCurrentIndex(0)
            return
        self._show_card(detail)

    def _show_card(self, detail: CardDetail) -> None:
        self.card_title.setText(detail.summary.request)
        self.card_location.setText(f"{detail.summary.state.value} / {detail.summary.filename}")
        self.card_agent.setText(detail.summary.agent or "Sin asignar")
        metadata = json.dumps(detail.metadata, ensure_ascii=False, indent=2, default=str)
        self.card_document.setPlainText(f"METADATOS\n{metadata}\n\nCUERPO\n{detail.body}")
        self.record_view.setPlainText(detail.record or "El Record todavía está vacío.")

    def _show_selected_profile(self) -> None:
        if self._snapshot is None:
            return
        rows = self.profile_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if row >= len(self._snapshot.profiles):
            return
        profile = self._snapshot.profiles[row]
        self.profile_detail.setPlainText(
            f"{profile.name}@{profile.version}\n"
            f"{profile.description}\n\n"
            f"Función: {profile.function}\n"
            f"Handles: {', '.join(profile.handles) or '—'}\n"
            f"Refuses: {', '.join(profile.refuses) or '—'}\n"
            f"Skills: {', '.join(profile.skills) or '—'}\n"
            f"Harness: {profile.harness or '—'}\n"
            f"Contrato: {profile.source}"
        )

    def _selected_card_key(self) -> tuple[BoardState, str] | None:
        item = self.board_tree.currentItem()
        if item is None or item.parent() is None:
            return None
        state_value = item.data(0, Qt.ItemDataRole.UserRole)
        filename = item.data(1, Qt.ItemDataRole.UserRole)
        if not isinstance(state_value, str) or not isinstance(filename, str):
            return None
        return BoardState(state_value), filename

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #171a21; color: #e7eaf0; }
            QToolBar { background: #20242d; border: 0; spacing: 8px; padding: 7px; }
            QPushButton { background: #4d7cff; color: white; border: 0; border-radius: 5px;
                          padding: 7px 14px; font-weight: 600; }
            QPushButton:hover { background: #638cff; }
            QTreeWidget, QTableWidget, QPlainTextEdit {
                background: #20242d; border: 1px solid #303642; border-radius: 6px;
                selection-background-color: #365a9d; padding: 4px;
            }
            QHeaderView::section { background: #282d38; color: #bfc6d4; border: 0;
                                   padding: 7px; font-weight: 600; }
            QTabWidget::pane { border: 1px solid #303642; border-radius: 6px; }
            QTabBar::tab { background: #20242d; padding: 9px 14px; margin-right: 2px; }
            QTabBar::tab:selected { background: #365a9d; }
            QLabel#sectionHeading, QLabel#cardTitle { font-size: 16px; font-weight: 700; }
            QLabel#dispatcherStatus { color: #9fc0ff; padding-left: 8px; }
            QLabel#errorSummary { color: #ef8c8c; font-weight: 600; }
            QStatusBar { background: #20242d; color: #aeb6c5; }
            """
        )
