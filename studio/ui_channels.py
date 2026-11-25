"""Channels page (Manage > Channels): channels, scripts, prompts, voice overs and a batch list for each channel.

0.21: channel logo, competitors, countries, optional sections inside a script, keyword search, voice-over selection with a voice per script,
three passcodes (whole section / chosen channels / Prompts tab only), copy buttons everywhere and keyboard shortcuts.
"""
from __future__ import annotations
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QImage, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from .ui import label, button
from .ui_extra import scroll_page
from .core import models, root
from .importers import DOC_FILTER, read_script
from .widgets import FlowLayout
from . import channels as ch, managed, samples, favorites

TABS = ("Channel Manager", "Scripts", "Prompts", "Voice Overs", "Batch Queue")
STATUS_COLOURS = {"Done": "#3fbf7f", "Processing": "#a78bfa", "In queue": "#4aa8d8", "Failed": "#e0584f"}
IMAGE_FILTER = "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*.*)"
LOGO_PX = 160


def counter(text: str, limit: int = 0) -> str:
    """Only the count, never a limit: the text can be as long as you like."""
    n = ch.word_count(text)
    return f"{n:,} words  |  {len(text):,} characters"


# ----------------------------------------------------------------------------- small helpers
def logo_dir() -> Path:
    folder = root() / "state" / "channel_logos"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def logo_file(channel: dict | None) -> Path | None:
    name = (channel or {}).get("logo") or ""
    path = logo_dir() / name if name else None
    return path if path and path.is_file() else None


def make_logo(cid: str, source: str) -> str:
    """Cut the chosen picture to a square, shrink it and keep it with the channel. Returns the file name."""
    image = QImage(str(source))
    if image.isNull():
        raise ValueError("This picture cannot be opened. Choose a PNG or JPG file.")
    image = image.scaled(LOGO_PX, LOGO_PX, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    x, y = max(0, (image.width() - LOGO_PX) // 2), max(0, (image.height() - LOGO_PX) // 2)
    image = image.copy(x, y, LOGO_PX, LOGO_PX)
    name = f"{cid}.png"
    if not image.save(str(logo_dir() / name), "PNG"):
        raise ValueError("The logo could not be saved.")
    return name


def logo_icon(channel: dict | None) -> QIcon | None:
    path = logo_file(channel)
    return QIcon(QPixmap(str(path))) if path else None


def short_model(model: str) -> str:
    return models().get(model, {}).get("name", model).split(" · ")[0]


def link_of(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return text if "://" in text else "https://" + text


def parse_competitor_line(line: str) -> tuple[str, str]:
    """'Name - https://link', 'https://link' or 'Name' -> (name, link)."""
    line = (line or "").strip()
    if not line:
        return "", ""
    match = re.search(r"(https?://\S+|www\.\S+)", line)
    if not match:
        return line, ""
    link = match.group(1).rstrip(".,;)")
    name = (line[:match.start()] + line[match.end():]).strip(" -–—:|,;\t")
    return name, link


def flow(*widgets) -> QWidget:
    """A row of buttons that wraps onto a second line instead of being cut off."""
    box = QWidget()
    lay = FlowLayout(box)
    for w in widgets:
        lay.addWidget(w)
    return box


class PairDialog(QDialog):
    """Two optional boxes: a name and a link."""

    def __init__(self, parent, title, name="", link=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Type a name, a link, or both. Nothing is required.", "muted"))
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("Channel name (optional)")
        self.link = QLineEdit(link)
        self.link.setPlaceholderText("Link (optional)")
        lay.addWidget(self.name)
        lay.addWidget(self.link)
        row = QHBoxLayout()
        row.addStretch(1)
        ok, cancel = QPushButton("Save"), QPushButton("Cancel")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)


class NewChannelDialog(QDialog):
    """Name (required) and a logo (optional)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New channel")
        self.setMinimumWidth(420)
        self.logo_path = ""
        lay = QVBoxLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Channel name")
        lay.addWidget(label("Channel name", "fieldlabel"))
        lay.addWidget(self.name)
        row = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(56, 56)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setText("🎙")
        pick = QPushButton("Choose logo (optional)")
        pick.clicked.connect(self.pick)
        row.addWidget(self.preview)
        row.addWidget(pick)
        row.addStretch(1)
        lay.addLayout(row)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok, cancel = QPushButton("Create channel"), QPushButton("Cancel")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        lay.addLayout(buttons)

    def pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a logo", str(Path.home()), IMAGE_FILTER)
        if path:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.logo_path = path
                self.preview.setPixmap(pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))


class ChannelPages:
    # ================================================================== the page
    def make_channels_page(self):
        self._ch_open = set()                  # what is unlocked in this session ('section', 'prompts', 'ch:<id>')
        self._ch_reason = None
        self._ch_sections = []
        self._ch_sec_index = 0
        self._ch_hl_words = []
        self.ch_country_filter = ""
        self._ch_loading = False

        def build(lay):
            lay.addWidget(label("Channels", "title"))
            lay.addWidget(label("Manage your channels, their scripts, prompts and voice overs in one place.", "muted"))
            self.ch_header = QWidget()
            top = QHBoxLayout(self.ch_header)
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(8)
            self.ch_tabs = []
            for i, text in enumerate(TABS):
                tab = QPushButton(text)
                tab.setObjectName("tab")
                tab.setCheckable(True)
                tab.clicked.connect(lambda _=False, n=i: self.ch_show(n))
                top.addWidget(tab)
                self.ch_tabs.append(tab)
            top.addStretch(1)
            top.addWidget(label("Active channel", "muted"))
            self.ch_combo = QComboBox()
            self.ch_combo.setMinimumWidth(240)
            self.ch_combo.setIconSize(QSize(22, 22))
            self.ch_combo.activated.connect(self.ch_pick)
            top.addWidget(self.ch_combo)
            self.ch_pass_button = QPushButton("🔒 Passcode")
            self.ch_pass_button.setToolTip("Lock the channel section, chosen channels, or only the Prompts tab")
            self.ch_pass_button.clicked.connect(self.ch_passcode_dialog)
            top.addWidget(self.ch_pass_button)
            self.ch_lock_now = QPushButton("Lock now")
            self.ch_lock_now.setToolTip("Lock everything again (Ctrl+L)")
            self.ch_lock_now.clicked.connect(self.ch_lock_everything)
            top.addWidget(self.ch_lock_now)
            lay.addWidget(self.ch_header)
            self.ch_gate = QStackedWidget()
            self.ch_gate.addWidget(self._ch_lock_screen())
            self.ch_stack = QStackedWidget()
            for builder in (self._ch_manager_tab, self._ch_scripts_tab, self._ch_prompts_tab, self._ch_voiceovers_tab, self._ch_batch_tab):
                self.ch_stack.addWidget(builder())
            self.ch_gate.addWidget(self.ch_stack)
            self.ch_gate.setCurrentIndex(1)
            lay.addWidget(self.ch_gate, 1)

        self.stack.addWidget(scroll_page(build))
        self.ch_show(0)

    def ch_show(self, index):
        self.ch_stack.setCurrentIndex(index)
        for i, tab in enumerate(self.ch_tabs):
            tab.setChecked(i == index)
        if index == 3:
            self.ch_fill_voice_pickers()
        self.refresh_channels()

    def ch_ask_text(self, title, text, password=False, value=""):
        """One place for every question box (tests replace it)."""
        mode = QLineEdit.EchoMode.Password if password else QLineEdit.EchoMode.Normal
        return QInputDialog.getText(self, title, text, mode, value)

    # ================================================================== passcodes
    def _ch_lock_screen(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 30, 0, 30)
        from .ui_v5 import card
        frame, fl = card("🔒 Locked")
        frame.setMaximumWidth(460)
        self.ch_lock_text = label("", "muted")
        self.ch_pass_edit = QLineEdit()
        self.ch_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ch_pass_edit.setPlaceholderText("Passcode")
        self.ch_pass_edit.returnPressed.connect(self.ch_unlock)
        fl.addWidget(self.ch_lock_text)
        fl.addWidget(self.ch_pass_edit)
        row = QHBoxLayout()
        row.addWidget(button("Unlock", self.ch_unlock, True))
        row.addWidget(button("Forgot passcode?", self.ch_forgot))
        row.addStretch(1)
        fl.addLayout(row)
        lay.addWidget(frame, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        return page

    def ch_locked_reason(self):
        """Why the page is locked right now: 'section', 'channel', 'prompts' or None."""
        data = self.channel_data
        if data.lock_on("section") and "section" not in self._ch_open:
            return "section"
        active = data.active()
        if active and active["id"] in data.locked_channels() and "ch:" + active["id"] not in self._ch_open:
            return "channel"
        if self.ch_stack.currentIndex() == 2 and data.lock_on("prompts") and "prompts" not in self._ch_open:
            return "prompts"
        return None

    def ch_apply_gate(self):
        reason = self.ch_locked_reason()
        self._ch_reason = reason
        data = self.channel_data
        self.ch_header.setVisible(reason != "section")
        any_lock = any(data.lock_on(k) for k in ch.LOCK_KINDS)
        self.ch_lock_now.setVisible(any_lock)
        if reason:
            active = data.active()
            self.ch_lock_text.setText({"section": "The Channels section is locked. Type the passcode to open it.",
                                       "channel": f"The channel “{active['name'] if active else ''}” is locked. Type its passcode, or choose another channel above.",
                                       "prompts": "The Prompts tab is locked. Type the passcode to open it."}[reason])
            self.ch_pass_edit.clear()
            self.ch_gate.setCurrentIndex(0)
        else:
            self.ch_gate.setCurrentIndex(1)

    def ch_lock_kind(self):
        return {"section": "section", "channel": "channels", "prompts": "prompts"}.get(self._ch_reason or "")

    def ch_unlock(self):
        kind = self.ch_lock_kind()
        if not kind:
            return
        if not self.channel_data.check_passcode(kind, self.ch_pass_edit.text()):
            self.ch_pass_edit.clear()
            return self.toast("Wrong passcode. Try again.")
        if kind == "channels":
            active = self.channel_data.active()
            self._ch_open.add("ch:" + active["id"])
        else:
            self._ch_open.add(kind)
        self.ch_pass_edit.clear()
        self.refresh_channels()

    def ch_lock_everything(self):
        self._ch_open.clear()
        self.refresh_channels()
        self.toast("Locked.")

    def ch_owner_key_ok(self, text):
        from .owner import decode_key
        from . import ed25519
        try:
            public = ed25519.public_key(decode_key(text)).hex()
        except Exception:
            return False
        return public in getattr(self.access, "key_publics", [])

    def ch_forgot(self):
        kind = self.ch_lock_kind()
        if not kind:
            return
        text, ok = self.ch_ask_text("Forgot passcode", "Paste your owner key to remove this passcode:", True)
        if not ok:
            return
        if not self.ch_owner_key_ok(text):
            return self.alert("That is not the owner key, so the passcode was not removed.")
        self.channel_data.remove_passcode(kind)
        self.toast("Passcode removed. Set a new one with the Passcode button.")
        self.refresh_channels()

    def ch_passcode_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Passcodes")
        dialog.setMinimumWidth(560)
        lay = QVBoxLayout(dialog)
        lay.addWidget(label("Three separate passcodes. Use any of them, or none.", "muted"))
        status = {}

        def refresh():
            for kind, lab in status.items():
                extra = ""
                if kind == "channels" and self.channel_data.lock_on("channels"):
                    extra = f" · {len(self.channel_data.locked_channels())} channel(s) chosen"
                lab.setText(("On" if self.channel_data.lock_on(kind) else "Off") + extra)

        for kind, title, hint in (("section", "1 · Channels section", "Everything in Channels needs the passcode."),
                                  ("channels", "2 · Chosen channels", "Only the channels you tick are locked. Tick none and no channel is locked."),
                                  ("prompts", "3 · Prompts tab only", "Only the Prompts tab is locked.")):
            lay.addWidget(label(title, "fieldlabel"))
            lay.addWidget(label(hint, "muted"))
            row = QHBoxLayout()
            lab = label("", "muted")
            status[kind] = lab
            row.addWidget(lab, 1)
            row.addWidget(button("Set / change passcode", lambda _=False, k=kind: (self.ch_pass_set(k), refresh())))
            if kind == "channels":
                row.addWidget(button("Choose channels…", lambda _=False: (self.ch_pass_choose_channels(), refresh())))
            row.addWidget(button("Remove", lambda _=False, k=kind: (self.ch_pass_remove(k), refresh())))
            lay.addLayout(row)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        refresh()
        dialog.exec()
        self.refresh_channels()

    def ch_pass_current_ok(self, kind):
        data = self.channel_data
        if not data.lock_on(kind):
            return True
        old, ok = self.ch_ask_text("Current passcode", "Type the current passcode:", True)
        if not ok:
            return False
        if not data.check_passcode(kind, old):
            self.alert("That is not the current passcode.")
            return False
        return True

    def ch_pass_set(self, kind):
        data = self.channel_data
        if not self.ch_pass_current_ok(kind):
            return
        new, ok = self.ch_ask_text("New passcode", "Type the new passcode (at least 3 characters):", True)
        if not ok:
            return
        again, ok = self.ch_ask_text("New passcode", "Type it again to confirm:", True)
        if not ok:
            return
        if new != again:
            return self.alert("The two passcodes are different. Nothing was changed.")
        try:
            data.set_passcode(kind, new, data.locked_channels() if kind == "channels" else None)
        except ValueError as exc:
            return self.alert(str(exc))
        if kind == "channels":
            self.ch_pass_choose_channels()
        else:
            self._ch_open.add(kind)          # you just typed it, so you are not locked out of your own work
        self.toast("Passcode saved.")

    def ch_pass_choose_channels(self):
        data = self.channel_data
        if not data.lock_on("channels"):
            return self.toast("Set the passcode for chosen channels first.")
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose channels to lock")
        lay = QVBoxLayout(dialog)
        lay.addWidget(label("Tick the channels that need the passcode.", "muted"))
        lst = QListWidget()
        chosen = set(data.locked_channels())
        for c in data.all():
            item = QListWidgetItem(c["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c["id"] in chosen else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            lst.addItem(item)
        lay.addWidget(lst)
        ok = QPushButton("Save")
        ok.setObjectName("primary")
        ok.clicked.connect(dialog.accept)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)
        if dialog.exec():
            ids = [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.CheckState.Checked]
            data.set_locked_channels(ids)
            self._ch_open |= {"ch:" + i for i in ids}      # chosen just now by you: stay open in this session

    def ch_pass_remove(self, kind):
        if not self.channel_data.lock_on(kind):
            return self.toast("This passcode is already off.")
        if not self.ch_pass_current_ok(kind):
            return
        self.channel_data.remove_passcode(kind)
        self.toast("Passcode removed.")

    # ---------------------------------------------------------------- Channel Manager tab
    def _ch_manager_tab(self):
        from .ui_v5 import card, actions
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(14)
        copy_button = QPushButton("Copy")
        copy_menu = QMenu(copy_button)
        copy_menu.addAction("Competitor links", lambda: self.ch_copy(self.ch_competitor_text(links_only=True)))
        copy_menu.addAction("Competitors (names and links)", lambda: self.ch_copy(self.ch_competitor_text()))
        copy_menu.addAction("All scripts", self.ch_copy_all_scripts)
        copy_menu.addAction("All prompts", self.ch_copy_all_prompts)
        copy_button.setMenu(copy_menu)
        lay.addWidget(flow(button("＋ New channel", self.ch_new, True), button("Duplicate", self.ch_duplicate), button("Delete", self.ch_delete), copy_button))
        self.ch_cards_box = QWidget()
        self.ch_cards_flow = FlowLayout(self.ch_cards_box)
        lay.addWidget(self.ch_cards_box)

        details, dl = card("Channel details")
        row = QHBoxLayout()
        row.setSpacing(12)
        self.ch_logo_view = QLabel("🎙")
        self.ch_logo_view.setFixedSize(72, 72)
        self.ch_logo_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ch_logo_view.setStyleSheet("font-size:34px;border-radius:14px;")
        self.ch_title_label = label("", "sectiontitle")
        self.ch_status = QComboBox()
        self.ch_status.addItems(["Active", "Paused"])
        self.ch_status.activated.connect(self.ch_save_status)
        side = QVBoxLayout()
        side.addWidget(self.ch_title_label)
        side.addWidget(flow(button("Change logo", self.ch_change_logo), button("Remove logo", self.ch_remove_logo), button("Rename", self.ch_rename), self.ch_status))
        row.addWidget(self.ch_logo_view)
        row.addLayout(side, 1)
        dl.addLayout(row)
        self.ch_stats = label("", "muted")
        dl.addWidget(self.ch_stats)
        lay.addWidget(details)

        comp, cl = card("Competitors")
        cl.addWidget(label("Optional. Add a channel name, a link, or both.", "muted"))
        self.ch_comp_table = QTableWidget(0, 2)
        self.ch_comp_table.setHorizontalHeaderLabels(["Channel name", "Link"])
        self.ch_comp_table.verticalHeader().hide()
        self.ch_comp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ch_comp_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ch_comp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ch_comp_table.setMinimumHeight(150)
        self.ch_comp_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.ch_comp_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ch_comp_table.doubleClicked.connect(lambda *_: self.ch_competitor_edit())
        self.ch_comp_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ch_comp_table.customContextMenuRequested.connect(self.ch_competitor_menu)
        cl.addWidget(self.ch_comp_table)
        cl.addWidget(flow(button("＋ Add", self.ch_competitor_add, True), button("Add many…", self.ch_competitor_many), button("Edit", self.ch_competitor_edit),
                          button("Remove", self.ch_competitor_remove), button("Open link", self.ch_competitor_open), button("Copy selected", self.ch_competitor_copy_selected),
                          button("Copy all links", lambda: self.ch_copy(self.ch_competitor_text(links_only=True))),
                          button("Copy all", lambda: self.ch_copy(self.ch_competitor_text()))))
        lay.addWidget(comp)

        countries, kl = card("Countries")
        kl.addWidget(label("Optional. Add the countries you make videos for, then sort your scripts by country.", "muted"))
        self.ch_country_list = QListWidget()
        self.ch_country_list.setMaximumHeight(110)
        kl.addWidget(self.ch_country_list)
        kl.addWidget(flow(button("＋ Add country", self.ch_country_add, True), button("Remove", self.ch_country_remove)))
        lay.addWidget(countries)
        lay.addStretch(1)
        return page

    def ch_new_dialog(self):
        """(name, logo path) or None. Tests replace this."""
        dialog = NewChannelDialog(self)
        if not dialog.exec():
            return None
        return dialog.name.text(), dialog.logo_path

    def ch_new(self):
        asked = self.ch_new_dialog()
        if not asked:
            return
        name, logo = asked
        try:
            channel = self.channel_data.add_channel(name)
            if logo:
                try:
                    self.channel_data.update_channel(channel["id"], logo=make_logo(channel["id"], logo))
                except ValueError as exc:
                    self.alert(str(exc))
        except ValueError as exc:
            return self.alert(str(exc))
        self.channel_data.set_active(channel["id"])
        self._ch_shown = None
        self.refresh_channels()

    def ch_duplicate(self):
        active = self.channel_data.active()
        if active:
            clone = self.channel_data.duplicate_channel(active["id"])
            src = logo_file(active)
            if src:
                try:
                    shutil.copyfile(src, logo_dir() / f"{clone['id']}.png")
                    self.channel_data.update_channel(clone["id"], logo=f"{clone['id']}.png")
                except OSError:
                    pass
            self.channel_data.set_active(clone["id"])
            self.refresh_channels()

    def ch_delete(self):
        active = self.channel_data.active()
        if active and self.confirm("Delete channel", f"Delete '{active['name']}' with its scripts and prompts? Voice overs already made stay in your audio folder."):
            path = logo_file(active)
            self.channel_data.delete_channel(active["id"])
            if path:
                try:
                    path.unlink()
                except OSError:
                    pass
            self._ch_shown = None
            self.refresh_channels()

    def ch_pick(self, index):
        cid = self.ch_combo.itemData(index)
        if cid:
            self.ch_save_script(quiet=True)
            self._ch_shown = None
            self.channel_data.set_active(cid)
            self.refresh_channels()

    def ch_rename(self):
        active = self.channel_data.active()
        if not active:
            return
        name, ok = self.ch_ask_text("Rename channel", "Channel name:", False, active["name"])
        if not ok:
            return
        try:
            self.channel_data.update_channel(active["id"], name=name)
        except ValueError as exc:
            return self.alert(str(exc))
        self.refresh_channels()

    def ch_save_status(self, *_):
        active = self.channel_data.active()
        if active:
            self.channel_data.update_channel(active["id"], status=self.ch_status.currentText())

    def ch_change_logo(self):
        active = self.channel_data.active()
        if not active:
            return self.toast("Create a channel first.")
        path, _ = QFileDialog.getOpenFileName(self, "Choose a logo", str(Path.home()), IMAGE_FILTER)
        if not path:
            return
        try:
            self.channel_data.update_channel(active["id"], logo=make_logo(active["id"], path))
        except ValueError as exc:
            return self.alert(str(exc))
        self.refresh_channels()

    def ch_remove_logo(self):
        active = self.channel_data.active()
        if not active:
            return
        path = logo_file(active)
        self.channel_data.update_channel(active["id"], logo="")
        if path:
            try:
                path.unlink()
            except OSError:
                pass
        self.refresh_channels()

    # ---- copy helpers (copy buttons are everywhere in Channels)
    def ch_copy(self, text):
        if not (text or "").strip():
            return self.toast("There is nothing to copy yet.")
        QGuiApplication.clipboard().setText(text)
        self.toast("Copied.")

    def ch_copy_all_scripts(self):
        active = self.channel_data.active()
        if not active:
            return
        self.ch_save_script(quiet=True)
        self.ch_copy("\n\n".join(f"{s['title']}\n\n{s['text']}" for s in active["scripts"] if s["text"].strip()))

    def ch_copy_all_prompts(self):
        active = self.channel_data.active()
        if not active:
            return
        if self.channel_data.lock_on("prompts") and "prompts" not in self._ch_open:
            return self.toast("The Prompts tab is locked. Open it with the passcode first.")
        self.ch_copy("\n\n".join(f"{ch.KINDS.get(p['kind'], p['kind'])} · {p['title']}\n\n{p['text']}" for p in active["prompts"] if p["text"].strip()))

    # ---- competitors
    def ch_competitor_text(self, links_only=False):
        active = self.channel_data.active()
        return self.channel_data.competitors_text(active["id"], links_only) if active else ""

    def ch_competitor_dialog(self, title, name="", link=""):
        """(name, link) or None. Tests replace this."""
        dialog = PairDialog(self, title, name, link)
        return (dialog.name.text(), dialog.link.text()) if dialog.exec() else None

    def ch_competitor_ids(self):
        rows = sorted({i.row() for i in self.ch_comp_table.selectedIndexes()})
        return [self.ch_comp_table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows if self.ch_comp_table.item(r, 0)]

    def ch_competitor_add(self):
        active = self.channel_data.active()
        if not active:
            return self.toast("Create a channel first.")
        asked = self.ch_competitor_dialog("Add competitor")
        if not asked:
            return
        try:
            self.channel_data.add_competitor(active["id"], *asked)
        except ValueError as exc:
            return self.alert(str(exc))
        self.refresh_channels()

    def ch_competitor_many(self):
        active = self.channel_data.active()
        if not active:
            return self.toast("Create a channel first.")
        text, ok = QInputDialog.getMultiLineText(self, "Add many competitors", "One per line: a name, a link, or “name - link”.")
        if not ok:
            return
        added = 0
        for line in text.splitlines():
            name, link = parse_competitor_line(line)
            if name or link:
                self.channel_data.add_competitor(active["id"], name, link)
                added += 1
        self.toast(f"{added} competitor(s) added." if added else "Nothing to add.")
        self.refresh_channels()

    def ch_competitor_edit(self):
        active, ids = self.channel_data.active(), self.ch_competitor_ids()
        if not active or not ids:
            return self.toast("Select a competitor first.")
        entry = next((c for c in active.get("competitors", []) if c["id"] == ids[0]), None)
        asked = self.ch_competitor_dialog("Edit competitor", entry.get("name", ""), entry.get("link", "")) if entry else None
        if not asked:
            return
        try:
            self.channel_data.update_competitor(active["id"], ids[0], *asked)
        except ValueError as exc:
            return self.alert(str(exc))
        self.refresh_channels()

    def ch_competitor_remove(self):
        active, ids = self.channel_data.active(), self.ch_competitor_ids()
        if not active or not ids:
            return self.toast("Select a competitor first.")
        for cid in ids:
            self.channel_data.remove_competitor(active["id"], cid)
        self.refresh_channels()

    def ch_competitor_open(self):
        active, ids = self.channel_data.active(), self.ch_competitor_ids()
        entry = next((c for c in (active or {}).get("competitors", []) if ids and c["id"] == ids[0]), None)
        if not entry or not entry.get("link"):
            return self.toast("Select a competitor that has a link.")
        QDesktopServices.openUrl(QUrl(link_of(entry["link"])))

    def ch_competitor_copy_selected(self):
        active, ids = self.channel_data.active(), self.ch_competitor_ids()
        rows = [c for c in (active or {}).get("competitors", []) if c["id"] in ids]
        self.ch_copy("\n".join(" - ".join(x for x in (c.get("name", ""), c.get("link", "")) if x) for c in rows))

    def ch_competitor_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Copy selected", self.ch_competitor_copy_selected)
        menu.addAction("Copy all links", lambda: self.ch_copy(self.ch_competitor_text(links_only=True)))
        menu.addAction("Open link", self.ch_competitor_open)
        menu.addSeparator()
        menu.addAction("Edit", self.ch_competitor_edit)
        menu.addAction("Remove", self.ch_competitor_remove)
        menu.exec(self.ch_comp_table.viewport().mapToGlobal(pos))

    # ---- countries
    def ch_country_add(self):
        active = self.channel_data.active()
        if not active:
            return self.toast("Create a channel first.")
        name, ok = self.ch_ask_text("Add country", "Country name:")
        if not ok:
            return
        try:
            self.channel_data.add_country(active["id"], name)
        except ValueError as exc:
            return self.alert(str(exc))
        self.refresh_channels()

    def ch_country_remove(self):
        active, item = self.channel_data.active(), self.ch_country_list.currentItem()
        if not active or not item:
            return self.toast("Select a country first.")
        if self.confirm("Remove country", f"Remove '{item.text()}'? Its scripts stay, they just have no country."):
            self.channel_data.remove_country(active["id"], item.text())
            if self.ch_country_filter == item.text():
                self.ch_country_filter = ""
            self.refresh_channels()

    # ---------------------------------------------------------------- Scripts tab
    def _ch_scripts_tab(self):
        from .ui_v5 import card, actions
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(14)
        left, ll = card("Scripts")
        # search
        srow = QHBoxLayout()
        self.ch_search = QLineEdit()
        self.ch_search.setPlaceholderText("Search words in scripts. Several words: separate with spaces, or commas for phrases")
        self.ch_search.setClearButtonEnabled(True)
        self.ch_search_timer = QTimer(self)
        self.ch_search_timer.setSingleShot(True)
        self.ch_search_timer.setInterval(250)
        self.ch_search_timer.timeout.connect(self.ch_search_run)
        self.ch_search.textChanged.connect(lambda *_: self.ch_search_timer.start())
        self.ch_search.returnPressed.connect(self.ch_search_run)
        self.ch_search_mode = QComboBox()
        self.ch_search_mode.addItem("Any word", False)
        self.ch_search_mode.addItem("All words", True)
        self.ch_search_mode.activated.connect(lambda *_: self.ch_search_run())
        srow.addWidget(self.ch_search, 1)
        srow.addWidget(self.ch_search_mode)
        ll.addLayout(srow)
        self.ch_search_info = label("", "muted")
        self.ch_search_info.hide()
        ll.addWidget(self.ch_search_info)
        self.ch_results = QListWidget()
        self.ch_results.hide()
        self.ch_results.itemClicked.connect(self.ch_result_clicked)
        ll.addWidget(self.ch_results)
        # country chips (only when the channel has countries)
        self.ch_country_box = QWidget()
        self.ch_country_flow = FlowLayout(self.ch_country_box)
        ll.addWidget(self.ch_country_box)
        self.ch_table = QTableWidget(0, 5)
        self.ch_table.setHorizontalHeaderLabels(["Title", "Country", "Length", "Status", "Voice over"])
        self.ch_table.verticalHeader().hide()
        self.ch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ch_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ch_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ch_table.setMinimumHeight(340)
        header = self.ch_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.ch_table.itemSelectionChanged.connect(self.ch_load_script)
        self.ch_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ch_table.customContextMenuRequested.connect(self.ch_script_menu)
        ll.addWidget(self.ch_table)
        ll.addWidget(flow(button("＋ New", self.ch_new_script, True), button("Import", self.ch_import_script), button("Delete", self.ch_del_script),
                          button("＋ Country", self.ch_country_add)))
        ll.addWidget(flow(button("Merge selected", self.ch_merge), button("Split by length…", self.ch_split),
                          button("Copy selected", self.ch_copy_selected_scripts)))
        ll.addWidget(flow(button("➜ Send to Voice Overs", lambda: self.ch_send_vo(True), True), button("Remove from Voice Overs", lambda: self.ch_send_vo(False))))
        outer.addWidget(left, 2)

        right, rl = card("Script")
        self.ch_title = QLineEdit()
        self.ch_title.setPlaceholderText("Title")
        crow = QHBoxLayout()
        self.ch_country_label = label("Country", "fieldlabel")
        self.ch_country_combo = QComboBox()
        crow.addWidget(self.ch_country_label)
        crow.addWidget(self.ch_country_combo, 1)
        self.ch_country_row = QWidget()
        self.ch_country_row.setLayout(crow)
        crow.setContentsMargins(0, 0, 0, 0)
        # sections (hidden until the script has one)
        self.ch_sec_box = QWidget()
        self.ch_sec_flow = FlowLayout(self.ch_sec_box)
        sec_button = QPushButton("Sections ▾")
        sec_menu = QMenu(sec_button)
        sec_menu.addAction("＋ Add section", self.ch_section_add)
        sec_menu.addAction("Rename section", self.ch_section_rename)
        sec_menu.addAction("Delete section", self.ch_section_delete)
        sec_menu.addAction("Join into one text", self.ch_section_join)
        sec_button.setMenu(sec_menu)
        self.ch_text = QPlainTextEdit()
        self.ch_text.setPlaceholderText("Write or paste the script here.")
        self.ch_text.setMinimumHeight(260)
        self.ch_text.textChanged.connect(self.ch_text_changed)
        self.ch_counts = label("", "muted")
        self.ch_notes = QPlainTextEdit()
        self.ch_notes.setPlaceholderText("Notes (not spoken)")
        self.ch_notes.setMaximumHeight(70)
        self.ch_direction = QPlainTextEdit()
        self.ch_direction.setPlaceholderText("Voice direction, for example: calm, clear and friendly. Used by voices that can follow a description.")
        self.ch_direction.setMaximumHeight(80)
        rl.addWidget(self.ch_title)
        rl.addWidget(self.ch_country_row)
        rl.addWidget(self.ch_sec_box)
        rl.addWidget(self.ch_text)
        rl.addWidget(self.ch_counts)
        for w in (label("Notes", "fieldlabel"), self.ch_notes, label("Prompt (voice direction)", "fieldlabel"), self.ch_direction):
            rl.addWidget(w)
        self.ch_autostart = QCheckBox("Start the voice-over right away")
        self.ch_autostart.setChecked(True)
        rl.addWidget(self.ch_autostart)
        rl.addWidget(flow(button("Save script", self.ch_save_script, True), button("Copy script", self.ch_copy_script), sec_button,
                          button("▶ Generate Voice Over", self.ch_generate_current), button("Add selected to queue", self.ch_queue_selected)))
        outer.addWidget(right, 3)
        return page

    def ch_ids(self):
        rows = sorted({i.row() for i in self.ch_table.selectedIndexes()})
        return [self.ch_table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows if self.ch_table.item(r, 0)]

    def ch_text_changed(self):
        if self._ch_loading:
            return
        if self._ch_sections:
            self._ch_sections[self._ch_sec_index]["text"] = self.ch_text.toPlainText()
        self.ch_update_counts()

    def ch_full_text(self):
        return self.channel_data.sections_text(self._ch_sections) if self._ch_sections else self.ch_text.toPlainText()

    def ch_update_counts(self):
        text = self.ch_full_text()
        self.ch_counts.setText(f"{ch.word_count(text)} words  |  {len(text):,} characters  |  Est. duration: {ch.clock(ch.duration_seconds(text))}")

    def ch_new_script(self):
        active = self.channel_data.active()
        if not active:
            return self.toast("Create a channel first.")
        self.ch_save_script(quiet=True)
        script = self.channel_data.add_script(active["id"], "New script", "", country=self.ch_country_filter)
        self.refresh_channels(select=script["id"])
        self.ch_title.setFocus()
        self.ch_title.selectAll()

    def ch_import_script(self):
        active = self.channel_data.active()
        if not active:
            return self.toast("Create a channel first.")
        paths, _ = QFileDialog.getOpenFileNames(self, "Import scripts", str(Path.home()), DOC_FILTER)
        last = None
        for path in paths:
            try:
                last = self.channel_data.add_script(active["id"], Path(path).stem, read_script(path), country=self.ch_country_filter)["id"]
            except Exception as exc:
                self.alert(f"{Path(path).name}: {exc}")
        if last:
            self.refresh_channels(select=last)

    def ch_current(self):
        ids = self.ch_ids()
        active = self.channel_data.active()
        return (active, self.channel_data.script(active["id"], ids[0])) if active and ids else (active, None)

    def ch_save_script(self, quiet=False):
        if self._ch_loading or not hasattr(self, "ch_title"):
            return
        shown = getattr(self, "_ch_shown", None)
        active = self.channel_data.active()
        if not active or not shown or not self.channel_data.script(active["id"], shown):
            return
        country = self.ch_country_combo.currentData() or ""
        self.channel_data.update_script(active["id"], shown, title=self.ch_title.text() or "Untitled", notes=self.ch_notes.toPlainText(), country=country)
        if self._ch_sections:
            self.channel_data.set_sections(active["id"], shown, self._ch_sections)
        else:
            self.channel_data.update_script(active["id"], shown, text=self.ch_text.toPlainText(), sections=[])
        self.channel_data.update_channel(active["id"], voice_direction=self.ch_direction.toPlainText())
        if not quiet:
            self.toast("Script saved.")
            self.refresh_channels(select=shown)

    def ch_load_script(self):
        if self._ch_loading:
            return
        self.ch_save_script(quiet=True)
        active, script = self.ch_current()
        self._ch_loading = True
        try:
            self._ch_shown = script["id"] if script else None
            self.ch_title.setText(script["title"] if script else "")
            self._ch_sections = [dict(x) for x in (script or {}).get("sections", [])]
            self._ch_sec_index = 0
            if self._ch_sections and self._ch_hl_words:          # after a search, open the first section that has the word
                for i, sec in enumerate(self._ch_sections):
                    if any(w.lower() in sec["text"].lower() for w in self._ch_hl_words):
                        self._ch_sec_index = i
                        break
            self.ch_text.setPlainText(self._ch_sections[self._ch_sec_index]["text"] if self._ch_sections else (script["text"] if script else ""))
            self.ch_notes.setPlainText(script.get("notes", "") if script else "")
            self.ch_direction.setPlainText(active["voice_direction"] if active else "")
            self.ch_country_combo.setCurrentIndex(max(0, self.ch_country_combo.findData((script or {}).get("country", ""))))
            self.ch_build_sections()
        finally:
            self._ch_loading = False
        self.ch_update_counts()
        self.ch_highlight()

    def ch_copy_script(self):
        text = self.ch_full_text()
        if self._ch_sections:
            self.ch_copy(text)
        else:
            self.ch_copy(text)

    def ch_copy_selected_scripts(self):
        active, ids = self.channel_data.active(), self.ch_ids()
        if not active or not ids:
            return self.toast("Select one or more scripts first.")
        self.ch_save_script(quiet=True)
        parts = [self.channel_data.script(active["id"], i) for i in ids]
        self.ch_copy("\n\n".join(f"{s['title']}\n\n{s['text']}" for s in parts if s))

    def ch_script_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Copy script text", self.ch_copy_selected_scripts)
        menu.addAction("Copy title", lambda: self.ch_copy(self.ch_title.text()))
        menu.addSeparator()
        menu.addAction("Send to Voice Overs", lambda: self.ch_send_vo(True))
        menu.addAction("Delete", self.ch_del_script)
        menu.exec(self.ch_table.viewport().mapToGlobal(pos))

    def ch_del_script(self):
        active = self.channel_data.active()
        ids = self.ch_ids()
        if active and ids and self.confirm("Delete scripts", f"Delete {len(ids)} script(s)? Voice overs already made stay."):
            for sid in ids:
                self.channel_data.delete_script(active["id"], sid)
            self._ch_shown = None
            self.refresh_channels()

    def ch_merge(self):
        active, ids = self.channel_data.active(), self.ch_ids()
        try:
            merged = self.channel_data.merge_scripts(active["id"], ids)
        except (ValueError, TypeError) as exc:
            return self.alert(str(exc) if isinstance(exc, ValueError) else "Create a channel first.")
        self.refresh_channels(select=merged["id"])

    def ch_split(self):
        active, script = self.ch_current()
        if not script:
            return self.toast("Select a script first.")
        words, ok = QInputDialog.getInt(self, "Split by length", "Longest part (words):", 300, 50, 3000, 50)
        if not ok:
            return
        try:
            parts = self.channel_data.split_script(active["id"], script["id"], words)
        except ValueError as exc:
            return self.alert(str(exc))
        self.refresh_channels(select=parts[0]["id"])

    def ch_send_vo(self, on):
        active, ids = self.channel_data.active(), self.ch_ids()
        if not active or not ids:
            return self.toast("Select one or more scripts first.")
        self.ch_save_script(quiet=True)
        n = self.channel_data.set_vo(active["id"], ids, on)
        self.refresh_channels()
        self.toast((f"{n} script(s) sent to Voice Overs." if on else f"{n} script(s) removed from Voice Overs.") if n else "Nothing changed.")

    # ---- sections inside one script (hidden until the first one is added)
    def ch_build_sections(self):
        while self.ch_sec_flow.count():
            item = self.ch_sec_flow.takeAt(0)
            gone = item.widget() if item else None
            if gone is not None:
                gone.setParent(None)
                gone.deleteLater()
        self.ch_sec_box.setVisible(bool(self._ch_sections))
        for i, sec in enumerate(self._ch_sections):
            b = QPushButton(sec["title"])
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(i == self._ch_sec_index)
            b.clicked.connect(lambda _=False, n=i: self.ch_section_show(n))
            self.ch_sec_flow.addWidget(b)

    def ch_section_show(self, index):
        if not (0 <= index < len(self._ch_sections)):
            return
        self._ch_sec_index = index
        self._ch_loading = True
        try:
            self.ch_text.setPlainText(self._ch_sections[index]["text"])
            self.ch_build_sections()
        finally:
            self._ch_loading = False
        self.ch_highlight()

    def ch_section_add(self):
        if not getattr(self, "_ch_shown", None):
            return self.toast("Select a script first.")
        name, ok = self.ch_ask_text("Add section", "Section name:", False, f"Section {len(self._ch_sections) + 1 if self._ch_sections else 2}")
        if not ok:
            return
        if not self._ch_sections:                              # the text written so far becomes the first section
            self._ch_sections = [{"title": "Section 1", "text": self.ch_text.toPlainText()}]
        self._ch_sections.append({"title": name.strip()[:60] or "Section", "text": ""})
        self.ch_section_show(len(self._ch_sections) - 1)
        self.ch_text.setFocus()

    def ch_section_rename(self):
        if not self._ch_sections:
            return self.toast("This script has no sections.")
        sec = self._ch_sections[self._ch_sec_index]
        name, ok = self.ch_ask_text("Rename section", "Section name:", False, sec["title"])
        if ok and name.strip():
            sec["title"] = name.strip()[:60]
            self._ch_loading = True
            try:
                self.ch_build_sections()
            finally:
                self._ch_loading = False

    def ch_section_delete(self):
        if not self._ch_sections:
            return self.toast("This script has no sections.")
        sec = self._ch_sections[self._ch_sec_index]
        if not self.confirm("Delete section", f"Delete the section '{sec['title']}' and its text?"):
            return
        del self._ch_sections[self._ch_sec_index]
        self._ch_sec_index = max(0, min(self._ch_sec_index, len(self._ch_sections) - 1))
        self._ch_loading = True
        try:
            self.ch_text.setPlainText(self._ch_sections[self._ch_sec_index]["text"] if self._ch_sections else "")
            self.ch_build_sections()
        finally:
            self._ch_loading = False
        self.ch_update_counts()

    def ch_section_join(self):
        if not self._ch_sections:
            return self.toast("This script has no sections.")
        joined = self.channel_data.sections_text(self._ch_sections)
        self._ch_sections = []
        self._ch_sec_index = 0
        self._ch_loading = True
        try:
            self.ch_text.setPlainText(joined)
            self.ch_build_sections()
        finally:
            self._ch_loading = False
        self.ch_update_counts()

    # ---- search in the scripts of this channel
    def ch_search_run(self):
        active = self.channel_data.active()
        text = self.ch_search.text() if hasattr(self, "ch_search") else ""
        words = self.channel_data.keywords(text)
        self._ch_hl_words = words
        if not active or not words:
            self.ch_results.clear()
            self.ch_results.hide()
            self.ch_search_info.hide()
            self.ch_highlight()
            return
        res = self.channel_data.search(active["id"], text, bool(self.ch_search_mode.currentData()), self.ch_country_filter)
        total = len([s for s in active["scripts"] if not self.ch_country_filter or s.get("country", "") == self.ch_country_filter])
        per = "  ·  ".join(f"{w}: {n} script{'s' if n != 1 else ''}" for w, n in res["per_keyword"].items())
        self.ch_search_info.setText(f"{len(res['hits'])} of {total} scripts match.   {per}   (click a result to open it)")
        self.ch_search_info.show()
        self.ch_results.clear()
        for hit in res["hits"]:
            found = ", ".join(f"{w} ×{n}" for w, n in hit["counts"].items() if n)
            item = QListWidgetItem(f"{hit['title']}   ({found})")
            item.setData(Qt.ItemDataRole.UserRole, hit["id"])
            self.ch_results.addItem(item)
        self.ch_results.setVisible(bool(res["hits"]))
        self.ch_results.setMaximumHeight(min(200, 38 * len(res["hits"]) + 12))
        self.ch_highlight()

    def ch_result_clicked(self, item):
        sid = item.data(Qt.ItemDataRole.UserRole)
        for row in range(self.ch_table.rowCount()):
            cell = self.ch_table.item(row, 0)
            if cell and cell.data(Qt.ItemDataRole.UserRole) == sid:
                self.ch_table.clearSelection()
                self.ch_table.selectRow(row)
                self.ch_table.scrollToItem(cell)
                break
        self.ch_load_script()

    def ch_highlight(self):
        if not hasattr(self, "ch_text"):
            return
        selections = []
        document = self.ch_text.document()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#f5c542"))
        fmt.setForeground(QColor("#10131a"))
        for word in self._ch_hl_words:
            cursor = QTextCursor(document)
            while len(selections) < 600:
                cursor = document.find(word, cursor)
                if cursor.isNull():
                    break
                extra = QTextEdit.ExtraSelection()
                extra.cursor, extra.format = cursor, fmt
                selections.append(extra)
        self.ch_text.setExtraSelections(selections)

    # ---------------------------------------------------------------- making voice overs
    def ch_generate_current(self):
        self.ch_save_script(quiet=True)
        ids = self.ch_ids()
        if ids:
            self.ch_generate(ids[:1], start=self.ch_autostart.isChecked())
        else:
            self.toast("Select a script first.")

    def ch_queue_selected(self):
        self.ch_save_script(quiet=True)
        ids = self.ch_ids()
        if ids:
            self.ch_generate(ids, start=self.ch_autostart.isChecked())
        else:
            self.toast("Select one or more scripts first.")

    def ch_voice_for(self, channel, script=None):
        """The voice chosen for this script, else the channel's main voice, else the voice chosen in the Studio."""
        v = (script or {}).get("voice") or self.channel_data.main_voice(channel["id"])
        return (v["model"], v["voice"]) if v else (self.model_combo.currentData(), self.voice_combo.currentData())

    def ch_generate(self, script_ids, start=True):
        channel = self.channel_data.active()
        if not channel:
            return []
        if self.busy_installer or self.move_pending:
            self.alert("Finish the current update first.")
            return []
        made = []
        try:
            for sid in script_ids:
                script = self.channel_data.script(channel["id"], sid)
                if not script or not script["text"].strip():
                    continue
                model, voice = self.ch_voice_for(channel, script)
                entry = models().get(model)
                if not entry:
                    raise ValueError("Choose a voice for this channel first (Voice Overs tab) or pick one in the Studio.")
                v, reference, ref_text, profile = self.resolve_voice(model, voice)
                if not managed.downloaded(model, v):
                    raise ValueError("Download this voice model in Models first.")
                options = self.batch_options(entry, "channel.txt")
                options.update(source_type="Channel", channel_id=channel["id"], script_id=sid, clone_name=(profile or {}).get("name", ""))
                if channel["voice_direction"].strip() and "emotion_text" in entry.get("supports", []):
                    options["emotion_text"] = channel["voice_direction"].strip()
                with self.pronunciation_applied():
                    jid = self.store.new_job(f"{channel['name']} – {script['title']}", script["text"], model, v if entry["kind"] != "clone" else "", reference, ref_text, options)
                made.append(jid)
        except Exception as exc:
            self.alert(str(exc))
        if made and start:
            self.pending_jobs = getattr(self, "pending_jobs", []) + made
            self.pump_jobs()
        if made:
            self.refresh_queue()
            self.toast(f"{len(made)} voice-over(s) " + ("started." if start else "added to the queue."))
        self.refresh_channels(select=script_ids[0] if script_ids else None)
        return made

    # ---------------------------------------------------------------- Prompts tab
    def _ch_prompts_tab(self):
        from .ui_v5 import card, actions
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(14)
        self.ch_prompt_ui = {}
        for kind, title in ch.KINDS.items():
            frame, fl = card(title)
            combo = QComboBox()
            edit = QPlainTextEdit()
            edit.setMinimumHeight(110)
            count = label("", "muted")
            edit.textChanged.connect(lambda e=edit, c=count: c.setText(counter(e.toPlainText())))
            templates = QPushButton("Templates ▾")
            menu = QMenu(templates)
            for t in ch.TEMPLATES:
                if t["kind"] == kind:
                    menu.addAction(t["title"], lambda txt=t["text"], e=edit: e.setPlainText(txt))
            templates.setMenu(menu)
            combo.activated.connect(lambda _i, k=kind: self.ch_prompt_choose(k))
            fl.addWidget(combo)
            fl.addWidget(edit)
            fl.addWidget(count)
            fl.addWidget(flow(button("Save", lambda _=False, k=kind: self.ch_prompt_save(k), True), button("Save as new", lambda _=False, k=kind: self.ch_prompt_new(k)),
                              button("Apply", lambda _=False, k=kind: self.ch_prompt_apply(k)), templates,
                              button("Copy", lambda _=False, e=edit: self.ch_copy(e.toPlainText())), button("Delete", lambda _=False, k=kind: self.ch_prompt_delete(k))))
            lay.addWidget(frame)
            self.ch_prompt_ui[kind] = {"combo": combo, "edit": edit}
        lay.addWidget(flow(button("Copy all prompts", self.ch_copy_all_prompts)))
        lay.addStretch(1)
        return page

    def ch_prompt_id(self, kind):
        return self.ch_prompt_ui[kind]["combo"].currentData()

    def ch_prompt_choose(self, kind):
        active, pid = self.channel_data.active(), self.ch_prompt_id(kind)
        prompt = next((p for p in (active or {}).get("prompts", []) if p["id"] == pid), None)
        self.ch_prompt_ui[kind]["edit"].setPlainText(prompt["text"] if prompt else "")

    def ch_prompt_save(self, kind):
        active, pid, text = self.channel_data.active(), self.ch_prompt_id(kind), self.ch_prompt_ui[kind]["edit"].toPlainText()
        if not active:
            return
        if pid:
            self.channel_data.update_prompt(active["id"], pid, text=text)
        else:
            self.channel_data.add_prompt(active["id"], kind, ch.KINDS[kind], text)
        self.refresh_channels()
        self.toast("Prompt saved.")

    def ch_prompt_new(self, kind):
        active = self.channel_data.active()
        if not active:
            return
        name, ok = self.ch_ask_text("Save as new prompt", "Name:", False, ch.KINDS[kind])
        if ok:
            self.channel_data.add_prompt(active["id"], kind, name, self.ch_prompt_ui[kind]["edit"].toPlainText())
            self.refresh_channels()

    def ch_prompt_apply(self, kind):
        active, pid = self.channel_data.active(), self.ch_prompt_id(kind)
        if active and pid:
            self.channel_data.apply_prompt(active["id"], pid)
            self.refresh_channels()
            self.toast("This prompt is now the one in use.")

    def ch_prompt_delete(self, kind):
        active, pid = self.channel_data.active(), self.ch_prompt_id(kind)
        if active and pid and self.confirm("Delete prompt", "Delete this prompt?"):
            self.channel_data.delete_prompt(active["id"], pid)
            self.refresh_channels()

    # ---------------------------------------------------------------- Voice Overs and Batch tabs
    def ch_jobs(self):
        active = self.channel_data.active()
        out = []
        for job in self.store.jobs():
            try:
                options = json.loads(job["options"] or "{}")
            except ValueError:
                continue
            if active and options.get("channel_id") == active["id"]:
                out.append((job, options))
        return out

    def ch_script_state(self, channel, script, jobs):
        mine = [j for j, o in jobs if o.get("script_id") == script["id"]]
        if not mine:
            return script["status"]
        job = max(mine, key=lambda j: j["created"])
        if job["id"] in self.running:
            return "Processing"
        if job["status"] == "completed":
            return "Done"
        if job["status"] in ("failed", "error"):
            return "Failed"
        return "In queue"

    def _ch_voiceovers_tab(self):
        from .ui_v5 import card, actions
        from .ui_v5 import MiniPlayer
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(14)

        # --- which voice belongs to this channel (moved here from the Channel Manager)
        voices, vl = card("Voices of this channel")
        vl.addWidget(label("The ★ main voice is used for every script unless you pick another voice for it below.", "muted"))
        self.ch_voice_list = QListWidget()
        self.ch_voice_list.setMaximumHeight(110)
        vl.addWidget(self.ch_voice_list)
        pick = QHBoxLayout()
        self.ch_vm_model, self.ch_vm_voice = QComboBox(), QComboBox()
        self.ch_vm_model.setMinimumWidth(190)
        self.ch_vm_voice.setMinimumWidth(220)
        self.ch_vm_model.activated.connect(lambda *_: self.ch_fill_voice_list())
        pick.addWidget(self.ch_vm_model)
        pick.addWidget(self.ch_vm_voice, 1)
        vl.addLayout(pick)
        vl.addWidget(flow(button("Set as main", lambda: self.ch_add_voice("main", from_picker=True), True), button("Add as alternative", lambda: self.ch_add_voice("alt", from_picker=True)),
                          button("Use Studio voice as main", lambda: self.ch_add_voice("main")), button("▶ Listen", self.ch_listen_voice), button("Remove", self.ch_remove_voice)))
        lay.addWidget(voices)

        # --- scripts chosen for voice over, with a voice for each
        scripts, sl = card("Scripts for voice over")
        sl.addWidget(label("Send scripts here from the Scripts tab. Choose a voice for each one (or one voice for all), then press Generate VO.", "muted"))
        allrow = QHBoxLayout()
        allrow.addWidget(label("Voice for all", "fieldlabel"))
        self.ch_all_voice = QComboBox()
        self.ch_all_voice.setMinimumWidth(260)
        self.ch_all_voice.activated.connect(self.ch_voice_for_all)
        allrow.addWidget(self.ch_all_voice)
        allrow.addStretch(1)
        sl.addLayout(allrow)
        self.ch_vo_scripts = QTableWidget(0, 5)
        self.ch_vo_scripts.setHorizontalHeaderLabels(["Script", "Country", "Length", "Voice", "Status"])
        self.ch_vo_scripts.verticalHeader().hide()
        self.ch_vo_scripts.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ch_vo_scripts.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ch_vo_scripts.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ch_vo_scripts.setMinimumHeight(240)
        header = self.ch_vo_scripts.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.ch_vo_scripts.setColumnWidth(3, 280)
        self.ch_vo_scripts.verticalHeader().setDefaultSectionSize(44)
        sl.addWidget(self.ch_vo_scripts)
        sl.addWidget(flow(button("▶ Generate VO", self.ch_generate_vo, True), button("Remove from list", self.ch_vo_remove), button("Copy list", self.ch_vo_copy)))
        lay.addWidget(scripts)

        # --- finished voice overs
        frame, fl = card("Finished voice overs of this channel")
        self.ch_vo_table = QTableWidget(0, 4)
        self.ch_vo_table.setHorizontalHeaderLabels(["Voice over", "Length", "Generated in", "Date"])
        self.ch_vo_table.verticalHeader().hide()
        self.ch_vo_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ch_vo_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ch_vo_table.setMinimumHeight(220)
        self.ch_vo_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.ch_vo_table.doubleClicked.connect(lambda *_: self.ch_play_vo())
        fl.addWidget(self.ch_vo_table)
        self.ch_player = MiniPlayer(self)
        fl.addWidget(self.ch_player)
        fl.addWidget(flow(button("▶ Play", self.ch_play_vo, True), button("Open folder", self.open_output_folder)))
        lay.addWidget(frame)
        lay.addStretch(1)
        return page

    # ---- the voices that can be picked
    def ch_fill_voice_pickers(self):
        """Every voice that is ready to use, grouped by model (looked up only when the Voice Overs tab is opened)."""
        if not hasattr(self, "ch_vm_model"):
            return
        try:
            rows = [r for r in favorites.all_voices(self.store, self.engine_ready) if r.get("ready")]
        except Exception:
            rows = []
        self._ch_voice_rows = rows
        keep = self.ch_vm_model.currentData()
        self.ch_vm_model.clear()
        seen = []
        for r in sorted(rows, key=lambda r: (r["kind"], r["model_name"])):
            if r["model"] not in seen:
                seen.append(r["model"])
                self.ch_vm_model.addItem(r["model_name"].split(" · ")[0], r["model"])
        if not seen:
            self.ch_vm_model.addItem("No voice model is ready yet (see Models)", None)
        if keep in seen:
            self.ch_vm_model.setCurrentIndex(self.ch_vm_model.findData(keep))
        self.ch_fill_voice_list()

    def ch_fill_voice_list(self):
        self.ch_vm_voice.clear()
        model = self.ch_vm_model.currentData()
        for r in sorted((r for r in getattr(self, "_ch_voice_rows", []) if r["model"] == model), key=lambda r: (not r.get("favorite"), r["name"].lower())):
            extra = f"  ·  {r['language']}" if r.get("language") and r["kind"] != "clone" else ""
            self.ch_vm_voice.addItem(("★ " if r.get("favorite") else "") + r["name"] + extra, r)

    def ch_voice_options(self, channel):
        """[(label, voice or None)]: the channel's voices; None means 'the Studio voice' when the channel has none."""
        out = []
        for v in channel.get("voices", []):
            out.append((("★ " if v["role"] == "main" else "") + f"{v['name']}  ·  {short_model(v['model'])}", v))
        if not out:
            out.append(("Studio voice (the one chosen in the Studio)", None))
        return out

    def ch_same_voice(self, a, b):
        return bool(a and b and a["model"] == b["model"] and a["voice"] == b["voice"])

    def ch_add_voice(self, role, from_picker=False):
        active = self.channel_data.active()
        if not active:
            return self.toast("Create a channel first.")
        if from_picker:
            row = self.ch_vm_voice.currentData()
            if not row:
                return self.toast("Choose a model and a voice first.")
            model, voice, name = row["model"], row["voice"], row["name"]
        else:
            model, voice = self.model_combo.currentData(), self.voice_combo.currentData()
            name = self.voice_combo.currentText().split("  ·  ")[0]
        if not model or not voice:
            return self.toast("Pick a voice in the Studio first.")
        try:
            self.channel_data.assign_voice(active["id"], model, voice, name, role)
        except ValueError as exc:
            return self.alert(str(exc))
        self.refresh_channels()

    def ch_selected_voice(self):
        item = self.ch_voice_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def ch_remove_voice(self):
        v, active = self.ch_selected_voice(), self.channel_data.active()
        if v and active:
            self.channel_data.remove_voice(active["id"], v["model"], v["voice"])
            self.refresh_channels()
        else:
            self.toast("Choose a voice in the list first.")

    def ch_listen_voice(self):
        v = self.ch_selected_voice()
        if not v:
            row = self.ch_vm_voice.currentData()
            v = {"model": row["model"], "voice": row["voice"], "name": row["name"]} if row else None
        if not v:
            return self.toast("Choose a voice first.")
        entry = models().get(v["model"], {})
        row = {"kind": entry.get("kind", "tts"), "model": v["model"], "voice": v["voice"], "name": v["name"], "language": ""}
        self.library_listen(row)

    # ---- the scripts waiting for a voice over
    def ch_vo_ids(self):
        rows = sorted({i.row() for i in self.ch_vo_scripts.selectedIndexes()})
        return [self.ch_vo_scripts.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows if self.ch_vo_scripts.item(r, 0)]

    def ch_row_voice(self, sid, combo):
        active = self.channel_data.active()
        if not active:
            return
        v = combo.currentData()
        main = self.channel_data.main_voice(active["id"])
        self.channel_data.update_script(active["id"], sid, voice=None if (v is None or self.ch_same_voice(v, main)) else dict(v))

    def ch_voice_for_all(self, *_):
        active = self.channel_data.active()
        if not active:
            return
        v = self.ch_all_voice.currentData()
        main = self.channel_data.main_voice(active["id"])
        value = None if (v is None or self.ch_same_voice(v, main)) else dict(v)
        for s in self.channel_data.vo_scripts(active["id"]):
            self.channel_data.update_script(active["id"], s["id"], voice=value)
        self.refresh_channels()
        self.toast("Voice set for all scripts in the list.")

    def ch_generate_vo(self):
        active = self.channel_data.active()
        if not active:
            return
        ids = self.ch_vo_ids()
        if not ids:
            jobs = self.ch_jobs()
            ids = [s["id"] for s in self.channel_data.vo_scripts(active["id"]) if self.ch_script_state(active, s, jobs) not in ("Done", "Processing", "In queue")]
        if not ids:
            return self.toast("No script is waiting. Send scripts from the Scripts tab, or select the ones to make again.")
        self.ch_generate(ids, start=True)

    def ch_vo_remove(self):
        active, ids = self.channel_data.active(), self.ch_vo_ids()
        if not active or not ids:
            return self.toast("Select one or more scripts in the list first.")
        self.channel_data.set_vo(active["id"], ids, False)
        self.refresh_channels()

    def ch_vo_copy(self):
        active = self.channel_data.active()
        if active:
            self.ch_copy("\n".join(s["title"] for s in self.channel_data.vo_scripts(active["id"])))

    def ch_play_vo(self):
        row = self.ch_vo_table.currentRow()
        item = self.ch_vo_table.item(row, 0) if row >= 0 else None
        job = self.store.job(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if not job or not job.get("output") or not Path(job["output"]).is_file():
            return self.toast("Select a finished voice over first.")
        self.ch_player.load(job["output"], play=True)
        self.ch_player.set_note(job["title"])

    def _ch_batch_tab(self):
        from .ui_v5 import card, actions
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        frame, fl = card("Batch queue of this channel")
        self.ch_batch_table = QTableWidget(0, 3)
        self.ch_batch_table.setHorizontalHeaderLabels(["Script / task", "Status", "Progress"])
        self.ch_batch_table.verticalHeader().hide()
        self.ch_batch_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ch_batch_table.setMinimumHeight(300)
        self.ch_batch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        fl.addWidget(self.ch_batch_table)
        fl.addWidget(flow(button("▶ Start all waiting", self.ch_start_waiting, True), button("⏸ Pause", self.pause_jobs),
                          button("■ Stop", self.stop_generation), button("Clear finished", self.ch_clear_finished)))
        lay.addWidget(frame)
        lay.addStretch(1)
        return page

    def ch_start_waiting(self):
        waiting = [j["id"] for j, _ in self.ch_jobs() if j["status"] != "completed" and j["id"] not in self.running and j["id"] not in getattr(self, "pending_jobs", [])]
        if not waiting:
            return self.toast("Nothing is waiting for this channel.")
        self.pending_jobs = getattr(self, "pending_jobs", []) + waiting
        self.pump_jobs()

    def ch_clear_finished(self):
        for job, _ in self.ch_jobs():
            if job["status"] == "completed" and job["id"] not in self.running:
                self.store.delete_job(job["id"])
        self.refresh_queue()
        self.refresh_channels()

    # ================================================================== refresh everything
    def ch_clear_flow(self, flow_layout):
        while flow_layout.count():
            item = flow_layout.takeAt(0)
            gone = item.widget() if item else None
            if gone is not None:
                gone.setParent(None)
                gone.deleteLater()

    def refresh_channels(self, select=None):
        if not hasattr(self, "ch_combo") or self._ch_loading:
            return
        self._ch_loading = True
        try:
            data = self.channel_data
            active = data.active()
            has = active is not None
            countries = list(active.get("countries", [])) if has else []
            if self.ch_country_filter and self.ch_country_filter not in countries:
                self.ch_country_filter = ""
            self.ch_combo.clear()
            for c in data.all():
                icon = logo_icon(c)
                text = f"{c['name']}" if icon else f"{c['emoji']}  {c['name']}"
                if icon:
                    self.ch_combo.addItem(icon, text, c["id"])
                else:
                    self.ch_combo.addItem(text, c["id"])
            if active:
                self.ch_combo.setCurrentIndex(max(0, self.ch_combo.findData(active["id"])))
            # channel cards
            self.ch_clear_flow(self.ch_cards_flow)
            jobs = self.ch_jobs()
            locked_ids = data.locked_channels()
            for c in data.all():
                lock = "🔒 " if c["id"] in locked_ids else ""
                icon = logo_icon(c)
                card_button = QPushButton(f"{lock}{c['name']}\n{len(c['scripts'])} scripts · {len(c['voices'])} voices" if icon
                                          else f"{lock}{c['emoji']}  {c['name']}\n{len(c['scripts'])} scripts · {len(c['voices'])} voices")
                if icon:
                    card_button.setIcon(icon)
                    card_button.setIconSize(QSize(36, 36))
                card_button.setObjectName("chip")
                card_button.setCheckable(True)
                card_button.setChecked(bool(active and c["id"] == active["id"]))
                card_button.setMinimumSize(190, 62)
                card_button.clicked.connect(lambda _=False, i=c["id"]: self.ch_pick_id(i))
                self.ch_cards_flow.addWidget(card_button)
            if not data.all():
                self.ch_cards_flow.addWidget(label("No channels yet. Press “New channel” to create your first one.", "muted"))
            # details
            self.ch_title_label.setText(active["name"] if has else "No channel yet")
            pix = QPixmap(str(logo_file(active))) if has and logo_file(active) else None
            if pix and not pix.isNull():
                self.ch_logo_view.setText("")
                self.ch_logo_view.setPixmap(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                self.ch_logo_view.setPixmap(QPixmap())
                self.ch_logo_view.setText(active["emoji"] if has else "🎙")
            self.ch_status.setCurrentText(active["status"] if has else "Active")
            if has:
                st = data.stats(active["id"], voice_overs=sum(1 for j, _ in jobs if j["status"] == "completed"))
                self.ch_stats.setText(f"{st['scripts']} scripts · {st['voice_overs']} voice overs · {st['prompts']} prompts · {st['voices']} voices")
            else:
                self.ch_stats.setText("")
            # competitors and countries
            self.ch_comp_table.setRowCount(0)
            for c in (active.get("competitors", []) if has else []):
                r = self.ch_comp_table.rowCount()
                self.ch_comp_table.insertRow(r)
                first = QTableWidgetItem(c.get("name", ""))
                first.setData(Qt.ItemDataRole.UserRole, c["id"])
                self.ch_comp_table.setItem(r, 0, first)
                self.ch_comp_table.setItem(r, 1, QTableWidgetItem(c.get("link", "")))
            self.ch_country_list.clear()
            for name in countries:
                self.ch_country_list.addItem(name)
            # voices of the channel
            self.ch_voice_list.clear()
            for v in (active["voices"] if has else []):
                item = QListWidgetItem(("★ Main   " if v["role"] == "main" else "Alt     ") + f"{v['name']}  ·  {short_model(v['model'])}")
                item.setData(Qt.ItemDataRole.UserRole, v)
                self.ch_voice_list.addItem(item)
            # country chips, the column and the combo in the script editor (only when the channel has countries)
            self.ch_clear_flow(self.ch_country_flow)
            self.ch_country_box.setVisible(bool(countries))
            for name in [""] + countries:
                chip = QPushButton(name or "All countries")
                chip.setObjectName("chip")
                chip.setCheckable(True)
                chip.setChecked(name == self.ch_country_filter)
                chip.clicked.connect(lambda _=False, n=name: self.ch_set_country_filter(n))
                self.ch_country_flow.addWidget(chip)
            self.ch_country_row.setVisible(bool(countries))
            kept_country = self.ch_country_combo.currentData() or ""
            self.ch_country_combo.clear()
            self.ch_country_combo.addItem("No country", "")
            for name in countries:
                self.ch_country_combo.addItem(name, name)
            self.ch_country_combo.setCurrentIndex(max(0, self.ch_country_combo.findData(kept_country)))
            self.ch_table.setColumnHidden(1, not countries)
            self.ch_vo_scripts.setColumnHidden(1, not countries)
            # scripts
            keep = select or getattr(self, "_ch_shown", None)
            self.ch_table.setRowCount(0)
            visible = [s for s in (active["scripts"] if has else []) if not self.ch_country_filter or s.get("country", "") == self.ch_country_filter]
            for s in visible:
                row = self.ch_table.rowCount()
                self.ch_table.insertRow(row)
                state = self.ch_script_state(active, s, jobs)
                cells = (s["title"], s.get("country", ""), ch.clock(ch.duration_seconds(s["text"])), state, "✓" if s.get("vo") else "")
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, s["id"])
                    if col == 3 and state in STATUS_COLOURS:
                        item.setForeground(Qt.GlobalColor.white)
                        item.setBackground(QColor(STATUS_COLOURS[state]).darker(160))
                    self.ch_table.setItem(row, col, item)
                if keep and s["id"] == keep:
                    self.ch_table.selectRow(row)
            # scripts waiting for a voice over, each with its voice
            options = self.ch_voice_options(active) if has else []
            self.ch_all_voice.clear()
            for text, v in options:
                self.ch_all_voice.addItem(text, v)
            vo_list = data.vo_scripts(active["id"]) if has else []
            self.ch_vo_scripts.setRowCount(0)
            for s in vo_list:
                r = self.ch_vo_scripts.rowCount()
                self.ch_vo_scripts.insertRow(r)
                first = QTableWidgetItem(s["title"])
                first.setData(Qt.ItemDataRole.UserRole, s["id"])
                state = self.ch_script_state(active, s, jobs)
                status_item = QTableWidgetItem(state)
                if state in STATUS_COLOURS:
                    status_item.setForeground(Qt.GlobalColor.white)
                    status_item.setBackground(QColor(STATUS_COLOURS[state]).darker(160))
                self.ch_vo_scripts.setItem(r, 0, first)
                self.ch_vo_scripts.setItem(r, 1, QTableWidgetItem(s.get("country", "")))
                self.ch_vo_scripts.setItem(r, 2, QTableWidgetItem(ch.clock(ch.duration_seconds(s["text"]))))
                combo = QComboBox()
                for text, v in options:
                    combo.addItem(text, v)
                chosen = s.get("voice")
                index = next((i for i, (_t, v) in enumerate(options) if chosen and self.ch_same_voice(v, chosen)), 0)
                combo.setCurrentIndex(index)
                combo.activated.connect(lambda _i, sid=s["id"], c=combo: self.ch_row_voice(sid, c))
                self.ch_vo_scripts.setCellWidget(r, 3, combo)
                self.ch_vo_scripts.setItem(r, 4, status_item)
            # prompts
            for kind, parts in self.ch_prompt_ui.items():
                prompts = [p for p in (active["prompts"] if has else []) if p["kind"] == kind]
                parts["combo"].clear()
                for p in prompts:
                    parts["combo"].addItem(("✓ " if p["applied"] else "") + p["title"], p["id"])
                if not prompts:
                    parts["combo"].addItem("No saved prompt yet. Write one below and press Save.", None)
                applied = next((p for p in prompts if p["applied"]), prompts[0] if prompts else None)
                if applied:
                    parts["combo"].setCurrentIndex(parts["combo"].findData(applied["id"]))
                parts["edit"].setPlainText(applied["text"] if applied else "")
            # finished voice overs and batch
            done = [(j, o) for j, o in jobs if j["status"] == "completed"]
            self.ch_vo_table.setRowCount(len(done))
            for r, (j, o) in enumerate(sorted(done, key=lambda t: t[0]["created"], reverse=True)):
                f = self.job_facts(j)
                made = int(o.get("made_seconds", 0) or 0)
                for col, text in enumerate((j["title"], f["length"] or "—", self.clock(made) if made else "—", datetime.fromtimestamp(j["created"]).strftime("%d %b %H:%M"))):
                    item = QTableWidgetItem(text)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, j["id"])
                    self.ch_vo_table.setItem(r, col, item)
            self.ch_batch_table.setRowCount(len(jobs))
            for r, (j, o) in enumerate(sorted(jobs, key=lambda t: t[0]["created"], reverse=True)):
                f = self.job_facts(j)
                state = "Processing" if j["id"] in self.running else "Done" if j["status"] == "completed" else "Failed" if j["status"] in ("failed", "error") else "Queued"
                self.ch_batch_table.setItem(r, 0, QTableWidgetItem(j["title"]))
                self.ch_batch_table.setItem(r, 1, QTableWidgetItem(state))
                bar = QProgressBar()
                bar.setRange(0, max(1, f["parts"]))
                bar.setValue(f["done"])
                bar.setFormat("%p%" if f["parts"] else "")
                self.ch_batch_table.setCellWidget(r, 2, bar)
        finally:
            self._ch_loading = False
        self.ch_load_script() if select or getattr(self, "_ch_shown", None) else self.ch_update_counts()
        self.ch_apply_gate()
        if self.ch_search.text().strip():
            self.ch_search_run()

    def ch_set_country_filter(self, name):
        self.ch_save_script(quiet=True)
        self.ch_country_filter = name
        self._ch_shown = None
        self.refresh_channels()

    def ch_pick_id(self, cid):
        self.ch_save_script(quiet=True)
        self._ch_shown = None
        self.channel_data.set_active(cid)
        self.refresh_channels()
