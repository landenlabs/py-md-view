#!/usr/bin/env python3
# ----------------------------------------------------------------------
# Copyright (c) 2026 LanDen Labs - Dennis Lang
# https://landenlabs.com
# ----------------------------------------------------------------------
"""md-view - View Markdown files (including embedded HTML and mermaid
diagrams) in a Qt window."""

from __future__ import annotations

import json
import re
import signal
import sys
from datetime import datetime
from pathlib import Path

import markdown
from PyQt6.QtCore import QSettings, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImageReader,
    QKeySequence,
    QMovie,
    QPalette,
    QPixmap,
)
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "MD View"
VERSION = "v1.00.00"
WINDOW_TITLE = "%s - %s - LanDen Labs 2026" % (APP_NAME, VERSION)

MD_EXTENSIONS = [
    "extra",
    "tables",
    "sane_lists",
    "toc",
    # pymdownx.superfences replaces the stock fenced_code/codehilite extensions:
    # it correctly recognizes ``` fences nested inside list items and blockquotes,
    # which python-markdown's built-in fenced_code silently fails to convert.
    "pymdownx.superfences",
    "pymdownx.highlight",
]

SETTINGS_ORG = "LanDenLabs"
SETTINGS_APP = "MdView"
DEFAULT_THEME = "Light"

_ZOOM_MIN_PCT = 30
_ZOOM_MAX_PCT = 300
_ZOOM_STEP_PCT = 10
_DEFAULT_ZOOM_PCT = 100

# pymdownx.superfences renders ```mermaid fences as <pre class="highlight">
# <code class="language-mermaid">...</code></pre>. Swap that wrapper for
# <pre class="mermaid">, which mermaid.js finds and replaces with a rendered
# SVG diagram. The HTML-escaped entities inside are left untouched -- the
# browser decodes them into the raw diagram source when parsing the text
# node, same as it would for any other element, so no manual unescaping
# is needed (and none of it gets re-parsed as markup).
_MERMAID_BLOCK_RE = re.compile(
    r'<pre[^>]*><code class="language-mermaid">(.*?)</code></pre>',
    re.DOTALL,
)


def _convert_mermaid_blocks(html: str) -> str:
    return _MERMAID_BLOCK_RE.sub(r'<pre class="mermaid">\1</pre>', html)


_COMMON_DOC_CSS = """
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    line-height: 1.5;
    margin: 16px;
}
table { border-collapse: collapse; }
th, td { border: 1px solid currentColor; padding: 4px 8px; }
pre.mermaid, div.mermaid { text-align: center; }
"""

_DOC_CSS_LIGHT = """
body { background: #ffffff; color: #1f2328; }
a { color: #0969da; }
h1, h2 { border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
pre:not(.mermaid) { background-color: #f0f0f0; padding: 8px; border-radius: 4px; overflow-x: auto; }
code { background-color: #f0f0f0; padding: 1px 3px; border-radius: 3px; }
pre code { background-color: transparent; padding: 0; }
hr { border: none; border-top: 1px solid #d0d7de; }
"""

_DOC_CSS_DARK = """
body { background: #1e1e1e; color: #d4d4d4; }
a { color: #4ea1ff; }
h1, h2 { border-bottom: 1px solid #444c56; padding-bottom: 0.3em; }
pre:not(.mermaid) { background-color: #2d333b; padding: 8px; border-radius: 4px; overflow-x: auto; }
code { background-color: #2d333b; padding: 1px 3px; border-radius: 3px; }
pre code { background-color: transparent; padding: 0; }
hr { border: none; border-top: 1px solid #545d68; }
"""


def _mermaid_script_url() -> str:
    return QUrl.fromLocalFile(str(resource_path("mermaid.min.js"))).toString()


def _wrap_html(body_html: str, theme: str) -> str:
    """Wrap markdown-derived body HTML into a full page: theme-aware CSS,
    and (only if the document actually contains one) the mermaid.js script
    plus the call that turns <pre class="mermaid"> blocks into diagrams."""
    css = _COMMON_DOC_CSS + (_DOC_CSS_DARK if theme == "Dark" else _DOC_CSS_LIGHT)
    mermaid_block = ""
    if 'class="mermaid"' in body_html:
        mermaid_theme = "dark" if theme == "Dark" else "default"
        mermaid_block = (
            '<script src="%s"></script>\n'
            '<script>\n'
            '  mermaid.initialize({ startOnLoad: false, theme: "%s" });\n'
            '  mermaid.run();\n'
            '</script>\n' % (_mermaid_script_url(), mermaid_theme)
        )
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<style>%s</style></head><body>\n%s\n%s</body></html>"
        % (css, body_html, mermaid_block)
    )


def _apply_theme(theme: str) -> None:
    """Apply ``theme`` ("Light" or "Dark") to the running QApplication's
    chrome (menus, dialogs, status bar) -- the document view is a separate
    web page and gets its own theme-aware CSS from _wrap_html()."""
    app = QApplication.instance()
    if app is None:
        return
    app.setStyle("Fusion")
    if theme == "Dark":
        p = QPalette()
        p.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        p.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        p.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        p.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        p.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        p.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        p.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        p.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
        p.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text, QColor(127, 127, 127),
        )
        p.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText, QColor(127, 127, 127),
        )
        app.setPalette(p)
    else:
        app.setPalette(app.style().standardPalette())


def _build_date() -> str:
    """Release/build date, derived from this file's mtime -- set-version.bash
    rewrites the VERSION line on every release, so this tracks the last publish."""
    try:
        return datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def resource_path(name: str) -> Path:
    """Locate a bundled resource (e.g. icon.png) both when run from source
    and when frozen by PyInstaller, which unpacks --add-data into _MEIPASS."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def app_icon() -> QIcon:
    path = resource_path("icon.png")
    return QIcon(str(path)) if path.is_file() else QIcon()


def _bold_label(text: str) -> QLabel:
    lbl = QLabel(text)
    f = lbl.font()
    f.setBold(True)
    lbl.setFont(f)
    return lbl


_DIALOG_WIDTH = 420
_ANIM_MAX_W = _DIALOG_WIDTH - 32


def _animation_path() -> Path:
    return Path(__file__).parent / "screens" / "landenlabs_400.webp"


def _animation_display_size(path: Path) -> QSize:
    """Return display size that preserves the animation's native aspect ratio."""
    native = QImageReader(str(path)).size()
    if not native.isValid() or native.width() == 0:
        return QSize(_ANIM_MAX_W, _ANIM_MAX_W)
    scale = min(1.0, _ANIM_MAX_W / native.width())
    return QSize(int(native.width() * scale), int(native.height() * scale))


class AboutDialog(QDialog):
    """About box for MD View."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About %s" % APP_NAME)
        self.setModal(True)
        self.setFixedWidth(_DIALOG_WIDTH)

        self._movie: QMovie | None = None
        self._final_pixmap: QPixmap | None = None
        self._last_frame_num: int = -1

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        self._anim_label = QLabel()
        self._anim_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        anim_path = _animation_path()
        if anim_path.exists():
            display_size = _animation_display_size(anim_path)
            self._anim_label.setFixedSize(display_size)
            self._movie = QMovie(str(anim_path))
            self._movie.setScaledSize(display_size)
            self._anim_label.setMovie(self._movie)
            self._movie.frameChanged.connect(self._on_frame_changed)
            root.addWidget(self._anim_label, alignment=Qt.AlignmentFlag.AlignCenter)

        header = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(app_icon().pixmap(48, 48))
        header.addWidget(icon_lbl)

        name_font = QFont()
        name_font.setPointSize(15)
        name_font.setBold(True)
        name_lbl = QLabel(APP_NAME)
        name_lbl.setFont(name_font)
        header.addWidget(name_lbl)
        header.addStretch(1)
        root.addLayout(header)

        desc = QLabel(
            "%s  —  A Markdown viewer that also renders embedded raw HTML "
            "and mermaid diagrams."
            % VERSION
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        root.addSpacing(4)

        form = QFormLayout()
        form.setSpacing(5)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.addRow(_bold_label("Author:"), QLabel("Dennis Lang"))
        form.addRow(_bold_label("Built:"), QLabel(_build_date()))
        form.addRow(QLabel(""), QLabel("Created by LanDen Labs (2026)"))

        link = QLabel('<a href="https://landenlabs.com">https://landenlabs.com</a>')
        link.setOpenExternalLinks(True)
        link.setTextFormat(Qt.TextFormat.RichText)
        form.addRow(_bold_label("Web:"), link)

        root.addLayout(form)
        root.addSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        if self._movie is not None:
            self._last_frame_num = -1
            self._final_pixmap = None
            self._movie.start()

    def _on_frame_changed(self, frame_num: int):
        # QMovie doesn't expose a reliable "play once" flag for animated WebP,
        # and frameCount() returns 0 for some encoders. Detect the wrap from
        # the last frame back to frame 0 and freeze on the previously cached
        # final frame.
        if self._movie is None:
            return
        if frame_num == 0 and self._last_frame_num > 0:
            self._movie.stop()
            if self._final_pixmap is not None:
                self._anim_label.setMovie(None)
                self._anim_label.setPixmap(self._final_pixmap)
            return
        self._final_pixmap = self._movie.currentPixmap()
        self._last_frame_num = frame_num


class _FindLineEdit(QLineEdit):
    """QLineEdit that turns Enter/Shift+Enter into find-next/find-previous
    and Escape into a close request, matching common browser find bars."""

    findNext = pyqtSignal()
    findPrevious = pyqtSignal()
    closeRequested = pyqtSignal()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.closeRequested.emit()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.findPrevious.emit()
            else:
                self.findNext.emit()
            return
        super().keyPressEvent(event)


class _LinkHandlingPage(QWebEnginePage):
    """Local .md links load in-place; everything else opens in the system
    browser instead of navigating the embedded view away from the document."""

    def __init__(self, main_window: "MainWindow", parent=None):
        super().__init__(parent)
        self._main_window = main_window

    def acceptNavigationRequest(self, url, nav_type, is_main_frame) -> bool:
        if nav_type != QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            return True
        if url.isLocalFile() and url.path().lower().endswith((".md", ".markdown", ".mdown", ".mkd")):
            self._main_window.load_file(url.toLocalFile())
        else:
            QDesktopServices.openUrl(url)
        return False


class MainWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(900, 700)

        self._current_path: Path | None = None
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        self._theme = self._settings.value("theme", DEFAULT_THEME, type=str)
        if self._theme not in ("Light", "Dark"):
            self._theme = DEFAULT_THEME

        self._zoom_pct = self._settings.value("zoom_pct", _DEFAULT_ZOOM_PCT, type=int)
        self._zoom_pct = max(_ZOOM_MIN_PCT, min(_ZOOM_MAX_PCT, self._zoom_pct))

        self._viewer = QWebEngineView()
        self._page = _LinkHandlingPage(self, self._viewer)
        self._viewer.setPage(self._page)
        self._apply_zoom()

        find_bar = self._build_find_bar()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(find_bar)
        central_layout.addWidget(self._viewer)
        self.setCentralWidget(central)

        self._build_menu()
        self._build_status_bar()
        self.statusBar().showMessage("No file loaded")

        if initial_path:
            self.load_file(initial_path)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")

        open_act = QAction("&Open...", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._browse_open)
        file_menu.addAction(open_act)

        reload_act = QAction("&Reload", self)
        reload_act.setShortcut(QKeySequence("Ctrl+R"))
        reload_act.triggered.connect(self._reload)
        file_menu.addAction(reload_act)

        file_menu.addSeparator()

        exit_act = QAction("E&xit", self)
        exit_act.setShortcut(QKeySequence.StandardKey.Quit)
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        edit_menu = self.menuBar().addMenu("&Edit")

        find_act = QAction("&Find...", self)
        find_act.setShortcut(QKeySequence.StandardKey.Find)
        find_act.triggered.connect(self._open_find_bar)
        edit_menu.addAction(find_act)

        view_menu = self.menuBar().addMenu("&View")

        zoom_in_act = QAction("Zoom In", self)
        zoom_in_act.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        zoom_in_act.triggered.connect(self._zoom_in)
        view_menu.addAction(zoom_in_act)

        zoom_out_act = QAction("Zoom Out", self)
        zoom_out_act.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out_act.triggered.connect(self._zoom_out)
        view_menu.addAction(zoom_out_act)

        zoom_reset_act = QAction("Reset Zoom", self)
        zoom_reset_act.setShortcut(QKeySequence("Ctrl+0"))
        zoom_reset_act.triggered.connect(self._zoom_reset)
        view_menu.addAction(zoom_reset_act)

        view_menu.addSeparator()

        self._dark_mode_act = QAction("&Dark Mode", self)
        self._dark_mode_act.setCheckable(True)
        self._dark_mode_act.setChecked(self._theme == "Dark")
        self._dark_mode_act.toggled.connect(self._on_dark_mode_toggled)
        view_menu.addAction(self._dark_mode_act)

        help_menu = self.menuBar().addMenu("&Help")
        about_act = QAction("&About", self)
        about_act.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(about_act)

    def _build_status_bar(self):
        sb = self.statusBar()

        zoom_frame = QWidget()
        zl = QHBoxLayout(zoom_frame)
        zl.setContentsMargins(2, 2, 8, 2)
        zl.setSpacing(2)

        self._btn_zoom_out = QPushButton("−")  # U+2212 proper minus
        self._btn_zoom_out.setFixedSize(24, 24)
        self._btn_zoom_out.setToolTip("Zoom out  (Ctrl-)")
        self._btn_zoom_out.clicked.connect(self._zoom_out)
        zl.addWidget(self._btn_zoom_out)

        self._lbl_zoom = QPushButton()
        self._lbl_zoom.setFixedWidth(54)
        self._lbl_zoom.setFixedHeight(24)
        self._lbl_zoom.setToolTip("Reset to 100%  (Ctrl+0)")
        self._lbl_zoom.clicked.connect(self._zoom_reset)
        zl.addWidget(self._lbl_zoom)

        self._btn_zoom_in = QPushButton("+")
        self._btn_zoom_in.setFixedSize(24, 24)
        self._btn_zoom_in.setToolTip("Zoom in  (Ctrl++)")
        self._btn_zoom_in.clicked.connect(self._zoom_in)
        zl.addWidget(self._btn_zoom_in)

        sb.addPermanentWidget(zoom_frame)
        self._update_zoom_label()

    def _build_find_bar(self) -> QWidget:
        bar = QWidget()
        bar.setVisible(False)
        # Without a Fixed vertical policy, the surrounding QVBoxLayout treats
        # this bar as free to grow and hands it most of the leftover space
        # instead of the web view, ballooning it to ~10x its sizeHint.
        bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        layout.addWidget(QLabel("Find:"))

        self._find_edit = _FindLineEdit()
        self._find_edit.setClearButtonEnabled(True)
        self._find_edit.textChanged.connect(self._on_find_text_changed)
        self._find_edit.findNext.connect(lambda: self._find(1))
        self._find_edit.findPrevious.connect(lambda: self._find(-1))
        self._find_edit.closeRequested.connect(self._close_find_bar)
        layout.addWidget(self._find_edit, 1)

        self._find_match_lbl = QLabel()
        self._find_match_lbl.setMinimumWidth(80)
        layout.addWidget(self._find_match_lbl)

        btn_prev = QToolButton()
        btn_prev.setText("↑")
        btn_prev.setToolTip("Previous match  (Shift+Enter)")
        btn_prev.clicked.connect(lambda: self._find(-1))
        layout.addWidget(btn_prev)

        btn_next = QToolButton()
        btn_next.setText("↓")
        btn_next.setToolTip("Next match  (Enter)")
        btn_next.clicked.connect(lambda: self._find(1))
        layout.addWidget(btn_next)

        self._find_case_chk = QCheckBox("Case")
        self._find_case_chk.setToolTip("Match case")
        self._find_case_chk.toggled.connect(lambda _checked: self._find(1))
        layout.addWidget(self._find_case_chk)

        btn_close = QToolButton()
        btn_close.setText("✕")
        btn_close.setToolTip("Close  (Esc)")
        btn_close.clicked.connect(self._close_find_bar)
        layout.addWidget(btn_close)

        self._find_bar = bar
        return bar

    def _open_find_bar(self):
        self._find_bar.setVisible(True)
        self._find_edit.setFocus()
        self._find_edit.selectAll()

    def _close_find_bar(self):
        self._viewer.page().runJavaScript("window.getSelection().removeAllRanges();")
        self._find_bar.setVisible(False)
        self._viewer.setFocus()

    def _on_find_text_changed(self, _text: str):
        self._find(1)

    def _find(self, direction: int = 1):
        text = self._find_edit.text()
        if not text:
            self._viewer.page().runJavaScript("window.getSelection().removeAllRanges();")
            self._set_find_feedback(None)
            return

        # QWebEnginePage.findText()'s resultCallback receives a
        # QWebEngineFindTextResult whose numberOfMatches() came back 0 even
        # for confirmed matches in this PyQt6-WebEngine build -- unreliable.
        # window.find() is a simpler native primitive: it searches, selects
        # and scrolls to the match, wraps around, and returns a plain bool.
        args = [
            text,
            self._find_case_chk.isChecked(),  # caseSensitive
            direction < 0,  # backwards
            True,   # wrapAround
            False,  # wholeWord
            True,   # searchInFrames
            False,  # showDialog
        ]
        js = "window.find.apply(window, %s)" % json.dumps(args)
        self._viewer.page().runJavaScript(js, self._set_find_feedback)

    def _set_find_feedback(self, found: bool | None):
        if found or found is None:
            self._find_edit.setStyleSheet("")
            self._find_match_lbl.setText("")
        else:
            # Explicit text color needed here: in dark theme the app palette's
            # white text color would otherwise show through on this light-pink
            # background (the stylesheet only overrides what it sets).
            self._find_edit.setStyleSheet("background-color: #f8d7da; color: #000000;")
            self._find_match_lbl.setText("No matches")

    def _on_dark_mode_toggled(self, checked: bool):
        self._theme = "Dark" if checked else "Light"
        _apply_theme(self._theme)
        self._settings.setValue("theme", self._theme)
        # Doc CSS and the mermaid theme are baked into the wrapped HTML at
        # render time, so the current document needs a fresh load to pick
        # up the new colors.
        if self._current_path:
            self._reload()

    def _zoom_in(self):
        if self._zoom_pct < _ZOOM_MAX_PCT:
            self._zoom_pct = min(_ZOOM_MAX_PCT, self._zoom_pct + _ZOOM_STEP_PCT)
            self._apply_zoom()
            self._on_zoom_changed()

    def _zoom_out(self):
        if self._zoom_pct > _ZOOM_MIN_PCT:
            self._zoom_pct = max(_ZOOM_MIN_PCT, self._zoom_pct - _ZOOM_STEP_PCT)
            self._apply_zoom()
            self._on_zoom_changed()

    def _zoom_reset(self):
        if self._zoom_pct == _DEFAULT_ZOOM_PCT:
            return
        self._zoom_pct = _DEFAULT_ZOOM_PCT
        self._apply_zoom()
        self._on_zoom_changed()

    def _on_zoom_changed(self):
        self._update_zoom_label()
        self._settings.setValue("zoom_pct", self._zoom_pct)

    def _update_zoom_label(self):
        self._lbl_zoom.setText("%d%%" % self._zoom_pct)

    def _apply_zoom(self):
        self._viewer.setZoomFactor(self._zoom_pct / 100.0)

    def _browse_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Markdown File",
            str(self._current_path.parent) if self._current_path else "",
            "Markdown Files (*.md *.markdown *.mdown *.mkd);;All Files (*)",
        )
        if path:
            self.load_file(path)

    def _reload(self):
        if self._current_path:
            self.load_file(str(self._current_path))

    def load_file(self, path: str):
        file_path = Path(path).expanduser().resolve()
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.critical(self, "Error", "Could not read file:\n%s" % exc)
            return

        body_html = markdown.markdown(text, extensions=MD_EXTENSIONS)
        body_html = _convert_mermaid_blocks(body_html)
        full_html = _wrap_html(body_html, self._theme)
        self._viewer.setHtml(full_html, baseUrl=QUrl.fromLocalFile(str(file_path.parent) + "/"))
        self._apply_zoom()

        self._current_path = file_path
        self.setWindowTitle("%s - %s - LanDen Labs 2026" % (file_path.name, VERSION))
        self.statusBar().showMessage(str(file_path))


def _handle_sigint(*_args):
    print("\nControl+c detected, closing app")
    QApplication.quit()


def main():
    app = QApplication(sys.argv)
    QApplication.setOrganizationName(SETTINGS_ORG)
    QApplication.setApplicationName(SETTINGS_APP)
    # Apply persisted theme before any windows are created so there's no
    # flash of the default style.
    theme = QSettings(SETTINGS_ORG, SETTINGS_APP).value("theme", DEFAULT_THEME, type=str)
    if theme not in ("Light", "Dark"):
        theme = DEFAULT_THEME
    _apply_theme(theme)
    app.setWindowIcon(app_icon())

    # Qt's event loop blocks Python's signal handling, so a bare SIGINT
    # handler would never run until the next Qt event; a periodic timer
    # keeps the interpreter ticking so Ctrl+C is caught promptly.
    signal.signal(signal.SIGINT, _handle_sigint)
    sigint_timer = QTimer()
    sigint_timer.timeout.connect(lambda: None)
    sigint_timer.start(200)

    initial_path = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(initial_path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
