"""Keyboard shortcuts for the whole software (0.21).

Two kinds:
  * Ctrl + key and function keys are normal window shortcuts (they never get in the way of typing).
  * Delete, Enter and Ctrl+C on a list or table are handled by one key filter, only when you are NOT typing in a text box
    and only for the list that has the focus, so a key press can never delete something by accident while you write.
Press F1 (or Ctrl+/) to see the full list inside the software.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QAbstractSpinBox

# (keys, page index, name) - page index is the position in the software's page stack
PAGE_KEYS = (("Ctrl+1", 0, "Studio"), ("Ctrl+2", 6, "Library"), ("Ctrl+3", 7, "Design"), ("Ctrl+4", 8, "Training"), ("Ctrl+5", 1, "Queue"),
             ("Ctrl+6", 2, "Clones"), ("Ctrl+7", 11, "Check words"), ("Ctrl+8", 3, "Models"), ("Ctrl+9", 10, "Channels"), ("Ctrl+0", 4, "Settings"))

HELP = (
    ("Go to a page", [(k, name) for k, _i, name in PAGE_KEYS]),
    ("Everywhere", [("F1  or  Ctrl+/", "Show this list"), ("F5", "Refresh the page you are on")]),
    ("Studio", [("Ctrl+Enter", "Generate voice over")]),
    ("Queue", [("Delete", "Remove the selected job from the list (audio stays)"), ("Enter", "Open the finished audio"), ("Ctrl+Enter", "Generate the selected job")]),
    ("Library", [("Ctrl+F", "Search voices")]),
    ("Channels", [("Ctrl+N", "New channel (Channel Manager) or new script (Scripts)"), ("Ctrl+S", "Save the script"), ("Ctrl+F", "Search words in scripts"),
                  ("Delete", "Delete the selected script / competitor / country / voice / list item"), ("Enter", "Open a script for writing, edit a competitor, play a voice over"),
                  ("Ctrl+C", "Copy the selected scripts or competitors (when the list has the focus)"), ("Ctrl+Enter", "Generate VO (Voice Overs tab)"),
                  ("Ctrl+PageUp / PageDown", "Previous / next Channels tab"), ("Ctrl+L", "Lock the Channels section now")]),
)


def typing() -> bool:
    """True when the cursor is in a box where letters are being typed."""
    w = QApplication.focusWidget()
    if isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)):
        return True
    return isinstance(w, QComboBox) and w.isEditable()


def inside(focus, widget) -> bool:
    return widget is not None and focus is not None and (focus is widget or widget.isAncestorOf(focus))


class _Keys(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window

    def eventFilter(self, obj, event):          # noqa: N802 (Qt name)
        if event.type() != QEvent.Type.KeyPress:
            return False
        w = self.w
        focus = QApplication.focusWidget()
        if obj is not focus or focus is None or not w.isAncestorOf(focus) or QApplication.activeModalWidget() is not None:
            return False
        key, mods = event.key(), event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        try:
            if mods == Qt.KeyboardModifier.NoModifier and not typing():
                if key == Qt.Key.Key_Delete:
                    return bool(on_delete(w, focus))
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if isinstance(focus, QPushButton):
                        return False
                    return bool(on_enter(w, focus))
            if mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_C and not typing():
                return bool(on_copy(w, focus))
        except Exception as exc:                  # a shortcut must never crash the window
            try:
                w.ch_say(f"Shortcut problem: {exc}")
            except Exception:
                pass
            return True
        return False


def on_delete(w, focus) -> bool:
    page = w.stack.currentIndex()
    if page == 1 and inside(focus, getattr(w, "queue_table", None)):
        w.clear_selected()
        return True
    if page == 10:
        for name, action in (("ch_table", w.ch_del_script), ("ch_comp_table", w.ch_competitor_remove), ("ch_vo_scripts", w.ch_vo_remove),
                             ("ch_voice_list", w.ch_remove_voice), ("ch_country_list", w.ch_country_remove)):
            if inside(focus, getattr(w, name, None)):
                action()
                return True
    return False


def on_enter(w, focus) -> bool:
    page = w.stack.currentIndex()
    if page == 1 and inside(focus, getattr(w, "queue_table", None)):
        w.open_output()
        return True
    if page == 10:
        if inside(focus, getattr(w, "ch_table", None)):
            w.ch_text.setFocus()
            return True
        if inside(focus, getattr(w, "ch_comp_table", None)):
            w.ch_competitor_edit()
            return True
        if inside(focus, getattr(w, "ch_vo_table", None)):
            w.ch_play_vo()
            return True
        if inside(focus, getattr(w, "ch_results", None)):
            item = w.ch_results.currentItem()
            if item:
                w.ch_result_clicked(item)
            return True
    return False


def on_copy(w, focus) -> bool:
    if w.stack.currentIndex() != 10:
        return False
    if inside(focus, getattr(w, "ch_table", None)):
        w.ch_copy_selected_scripts()
        return True
    if inside(focus, getattr(w, "ch_comp_table", None)):
        w.ch_competitor_copy_selected()
        return True
    if inside(focus, getattr(w, "ch_vo_scripts", None)):
        w.ch_vo_copy()
        return True
    return False


def go_page(w, index):
    button = w.nav_buttons[index] if 0 <= index < len(w.nav_buttons) else None
    if button is None or button.isHidden():          # a page that is hidden for this person cannot be opened with the keyboard either
        return
    w.change_page(index)


def generate(w):
    page = w.stack.currentIndex()
    if page == 0 and w.generate_button.isEnabled():
        w.generate_button.click()
    elif page == 1:
        w.run_selected()
    elif page == 10 and w.ch_stack.currentIndex() == 3:
        w.ch_generate_vo()


def refresh(w):
    page = w.stack.currentIndex()
    if page == 1:
        w.refresh_queue()
    elif page == 6:
        w.refresh_voice_library()
    elif page == 10:
        w.refresh_channels()
    if hasattr(w, "ch_say") and page == 10:
        w.ch_say("Refreshed.")


def new_item(w):
    if w.stack.currentIndex() != 10:
        return
    if w.ch_stack.currentIndex() == 0:
        w.ch_new()
    elif w.ch_stack.currentIndex() == 1:
        w.ch_new_script()


def save(w):
    if w.stack.currentIndex() == 10 and w.ch_stack.currentIndex() == 1:
        w.ch_save_script()


def find(w):
    page = w.stack.currentIndex()
    if page == 10 and w.ch_stack.currentIndex() != 1:
        w.ch_show(1)
    if page == 10:
        w.ch_search.setFocus()
        w.ch_search.selectAll()
    elif page == 6 and hasattr(w, "lib_search"):
        w.lib_search.setFocus()
        w.lib_search.selectAll()


def channel_tab(w, step):
    if w.stack.currentIndex() == 10:
        w.ch_show((w.ch_stack.currentIndex() + step) % w.ch_stack.count())


def lock_channels(w):
    if w.stack.currentIndex() == 10:
        w.ch_lock_everything()


def show_help(w):
    dialog = QDialog(w)
    dialog.setWindowTitle("Keyboard shortcuts")
    dialog.setMinimumWidth(560)
    lay = QVBoxLayout(dialog)
    html = []
    for title, rows in HELP:
        html.append(f"<h3>{title}</h3><table cellspacing='6'>" + "".join(f"<tr><td><b>{k}</b></td><td>&nbsp;&nbsp;{d}</td></tr>" for k, d in rows) + "</table>")
    text = QLabel("".join(html))
    text.setTextFormat(Qt.TextFormat.RichText)
    text.setWordWrap(True)
    lay.addWidget(text)
    close = QPushButton("Close")
    close.clicked.connect(dialog.accept)
    lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
    dialog.exec()


def install(w):
    """Attach every shortcut to the main window."""
    def add(keys, fn):
        QShortcut(QKeySequence(keys), w, activated=lambda: fn(w))
    for keys, index, _name in PAGE_KEYS:
        QShortcut(QKeySequence(keys), w, activated=lambda i=index: go_page(w, i))
    add("F1", lambda win: show_help(win))
    add("Ctrl+/", lambda win: show_help(win))
    add("F5", refresh)
    add("Ctrl+Return", generate)
    add("Ctrl+Enter", generate)
    add("Ctrl+N", new_item)
    add("Ctrl+S", save)
    add("Ctrl+F", find)
    add("Ctrl+L", lock_channels)
    QShortcut(QKeySequence("Ctrl+PgUp"), w, activated=lambda: channel_tab(w, -1))
    QShortcut(QKeySequence("Ctrl+PgDown"), w, activated=lambda: channel_tab(w, 1))
    w._key_filter = _Keys(w)
    QApplication.instance().installEventFilter(w._key_filter)
