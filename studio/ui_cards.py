"""Card-style pages: Library, Clones (drop zone, microphone, noise removal) and the History list."""
from __future__ import annotations
import hashlib
import os
import threading
import uuid
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .ui import label, button
from .core import models, subdir, is_audio
from . import clones, favorites, training, perf, capabilities, audio_fx, samples
from .recorder import Recorder, MAX_SECONDS
from .widgets import CardGrid, DropZone

MIN_SAMPLE, MAX_SAMPLE, MAX_MB = 3.0, 30.0, 200
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".mpeg", ".mpg"}
MAX_VIDEO_MB = 8000
COLORS = ("#7c5cff", "#14b8a6", "#f59e0b", "#ef4b6c", "#3b82f6", "#22c55e")


def chip(text: str) -> QLabel:
    c = QLabel(text)
    c.setObjectName("chiplabel")
    c.setWordWrap(False)
    return c


def chip_row(texts) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for t in texts:
        if t:
            lay.addWidget(chip(t))
    lay.addStretch(1)
    return box


def icon_button(text: str, tip: str, slot=None, size: int = 38) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("icon")
    b.setToolTip(tip)
    b.setFixedSize(size, size)
    if slot:
        b.clicked.connect(slot)
    return b


class CardPages:
    # ------------------------------------------------------------------ History (inside the Studio side panel)
    def build_history_page(self):
        from .ui_v5 import MiniPlayer
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.history_player = MiniPlayer(self)
        self.history_player.set_note("Press ▶ on a finished voice-over to hear it here.")
        lay.addWidget(self.history_player)
        self.history_host = QWidget()
        self.history_box = QVBoxLayout(self.history_host)
        self.history_box.setContentsMargins(0, 0, 4, 0)
        self.history_box.setSpacing(10)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.history_host)
        area.setMinimumHeight(560)
        lay.addWidget(area, 1)
        return page

    def history_state(self, job) -> str:
        if job["id"] in self.running:
            return "running"
        if job["id"] in getattr(self, "pending_jobs", []):
            return "waiting"
        return {"completed": "completed", "failed": "failed", "error": "failed"}.get(job["status"], "waiting")

    def refresh_history(self, *_):
        if not hasattr(self, "history_box"):
            return
        while self.history_box.count():
            item = self.history_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        jobs = self.store.jobs()[:30]
        if not jobs:
            empty = label("Nothing here yet. Your finished voice-overs appear in this list.", "muted")
            self.history_box.addWidget(empty)
        for job in jobs:
            self.history_box.addWidget(self.history_card(job))
        self.history_box.addStretch(1)

    @staticmethod
    def clock(seconds) -> str:
        seconds = int(seconds)
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02d}"

    def history_info(self, f, state) -> str:
        if state == "completed":
            made = int(f["options"].get("made_seconds", 0) or 0)
            bits = [f"Length {f['length']}" if f["length"] else "", f"Generated in {self.clock(made)}" if made else ""]
            return "  ·  ".join(b for b in bits if b) or "Done"
        return {"running": "Generating…", "waiting": "Waiting", "failed": "Did not finish"}.get(state, "")

    def history_card(self, job) -> QFrame:
        f = self.job_facts(job)
        state = self.history_state(job)
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        status = QLabel({"completed": "Done", "running": "Generating", "waiting": "Waiting", "failed": "Failed"}[state])
        status.setObjectName({"completed": "statusdone", "failed": "statusbad"}.get(state, "statuswait"))
        status.setWordWrap(False)
        head.addWidget(status)
        head.addWidget(label(datetime.fromtimestamp(job["created"]).strftime("%d %b, %H:%M"), "muted"))
        head.addStretch(1)
        head.addWidget(icon_button("🗑", "Remove from history", lambda _=False, j=job: self.delete_history(j), 34))
        lay.addLayout(head)
        text = " ".join(job["script"].split())
        lay.addWidget(label(text[:80] + ("…" if len(text) > 80 else ""), ""))
        controls = QHBoxLayout()
        controls.setSpacing(8)
        play = icon_button("▶", "Play", lambda _=False, j=job: self.play_history(j), 38)
        play.setEnabled(f["ready"])
        controls.addWidget(play)
        controls.addWidget(label(self.history_info(f, state), "muted"))
        controls.addStretch(1)
        if state == "running":
            stop = QPushButton("■ Stop")
            stop.setObjectName("danger")
            stop.clicked.connect(lambda _=False, j=job["id"]: self.stop_generation(j))
            controls.addWidget(stop)
        else:
            save = icon_button("⬇", "Save a copy as…", lambda _=False, j=job: self.export_history(j), 38)
            save.setEnabled(f["ready"])
            controls.addWidget(save)
            more = QPushButton("Details")
            more.clicked.connect(lambda _=False, j=job: self.history_details(j))
            controls.addWidget(more)
        lay.addLayout(controls)
        return frame

    def play_history(self, job):
        out = Path(job["output"] or "")
        if is_audio(out):
            self.history_player.load(out, play=True)
            self.history_player.set_note(job["title"])

    def export_history(self, job):
        from .reference_dialog import export_dialog
        try:
            export_dialog(self, job["output"])
        except Exception as exc:
            self.alert(str(exc))

    def delete_history(self, job):
        if self.confirm("Remove from history", "Remove this entry from the list? The audio file stays in your outputs folder."):
            if job["id"] in self.running:
                return self.alert("Wait until this voice-over has finished.")
            self.store.delete_job(job["id"])
            self.refresh_history()
            self.refresh_queue()

    def history_details(self, job):
        f = self.job_facts(job)
        o = f["options"]
        lines = [f"Name: {job['title']}", f"Created: {datetime.fromtimestamp(job['created']).strftime('%d %b %Y, %H:%M')}",
                 f"Status: {self.history_state(job).title()}", f"Source: {f['source']}",
                 f"Script: {f['words']:,} words, {f['chars']:,} characters",
                 f"Model: {models().get(job['model'], {}).get('name', job['model'])}",
                 f"Voice: {job['voice'] or o.get('clone_name') or 'cloned voice'}", f"Mode: {self.job_mode(o)}",
                 f"Parts: {f['done']} of {f['parts']} done"]
        if f["ready"]:
            lines += [f"File: {f['out']}", f"Type: {f['formats']}  ·  size {f['out'].stat().st_size / 1e6:.1f} MB  ·  length {f['length']}"]
        if o.get("made_seconds"):
            lines.append(f"Generated in: {self.clock(o['made_seconds'])}")
        extras = [x for x in (f"speed {o['output_speed']}×" if o.get("output_speed", 1) != 1 else "", f"pitch {o['pitch_semitones']}" if o.get("pitch_semitones") else "",
                              f"volume {o['volume_pct']}%" if o.get("volume_pct", 100) != 100 else "", "even volume" if o.get("normalize") else "",
                              "trim silences" if o.get("trim_silence") else "", "fade" if o.get("fade_ms") else "", "MP3 copy" if o.get("auto_mp3") else "",
                              "transcript" if o.get("export_transcript") else "", "pause tags" if o.get("gaps") else "") if x]
        lines.append("Options: " + (", ".join(extras) or "defaults"))
        summary = perf.summary(job["id"])
        if summary:
            lines.append(f"Speed: {summary['avg_s']} s per part. {perf.verdict(summary['rtf'])}")
        if job.get("error"):
            lines.append("Last problem: " + job["error"].splitlines()[0][:300])
        d = QDialog(self)
        d.setWindowTitle("Details")
        d.resize(620, 420)
        v = QVBoxLayout(d)
        box = QPlainTextEdit("\n".join(lines))
        box.setReadOnly(True)
        v.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(d.reject)
        bb.accepted.connect(d.accept)
        bb.button(QDialogButtonBox.StandardButton.Close).clicked.connect(d.accept)
        v.addWidget(bb)
        d.exec()

    # ------------------------------------------------------------------ Library as cards
    def make_voice_library(self):
        from .ui_v5 import card, MiniPlayer
        page = QWidget()
        page.setObjectName("page")
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 18, 22, 16)
        v.setSpacing(12)
        v.addWidget(label("Library", "title"))
        v.addWidget(label("Find a voice, press ▶ to listen, ♡ to keep it, then Use to send it to the Studio.", "muted"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.lib_search = QLineEdit()
        self.lib_search.setPlaceholderText("Search by name, language or model")
        self.lib_fav = QCheckBox("Favourites only")
        row.addWidget(self.lib_search, 1)
        row.addWidget(self.lib_fav)
        row.addWidget(icon_button("⟳", "Refresh", self.refresh_voice_library, 40))
        v.addLayout(row)
        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.lib_kind = QComboBox()
        for text, data in (("All voices", ""), ("Built-in", "tts"), ("My clones", "clone")):
            self.lib_kind.addItem(text, data)
        self.lib_gender = QComboBox()
        for text, data in (("Any gender", ""), ("Female", "Female"), ("Male", "Male")):
            self.lib_gender.addItem(text, data)
        self.lib_family = QComboBox()
        self.lib_model = QComboBox()
        self.lib_avail = QComboBox()
        for text, data in (("Any status", ""), ("Ready", "ready"), ("Needs setup", "setup")):
            self.lib_avail.addItem(text, data)
        for combo in (self.lib_kind, self.lib_gender, self.lib_family, self.lib_model, self.lib_avail):
            filters.addWidget(combo, 1)
        v.addLayout(filters)
        self.lib_count = label("", "muted")
        v.addWidget(self.lib_count)
        self.lib_grid = CardGrid(200, max_cols=7)      # 5 to 7 voices in a row
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.lib_grid)
        v.addWidget(area, 1)
        self.lib_more = QPushButton("Show more")
        self.lib_more.clicked.connect(self.library_more)
        v.addWidget(self.lib_more, 0, Qt.AlignmentFlag.AlignCenter)
        player_card, pc = card()
        self.library_player = MiniPlayer(self)
        self.library_player.set_note("Press ▶ on a ready voice.")
        pc.addWidget(self.library_player)
        v.addWidget(player_card)
        self.lib_limit = 60
        self.lib_search.textChanged.connect(self.library_filter_changed)
        self.lib_fav.toggled.connect(self.library_filter_changed)
        for combo in (self.lib_kind, self.lib_gender, self.lib_family, self.lib_model, self.lib_avail):
            combo.currentIndexChanged.connect(self.library_filter_changed)
        self.stack.addWidget(page)

    def library_filter_changed(self, *_):
        self.lib_limit = 60
        self.refresh_voice_library()

    def library_more(self):
        self.lib_limit += 60
        self.refresh_voice_library()

    def voice_card(self, row) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)
        parts = row["name"].split(" · ")
        title = label(parts[0], "sectiontitle")
        title.setWordWrap(False)
        lay.addWidget(title)
        model_short = row["model_name"].split(" · ")[0].replace(" CustomVoice", "")
        lay.addWidget(label(" · ".join(parts[1:]) if len(parts) > 1 else model_short, "muted"))
        lay.addWidget(chip_row([row["family"] if row["kind"] == "tts" else "My clone", row.get("gender")]))     # the model is already in the line above
        actions = QHBoxLayout()
        actions.setSpacing(8)
        fav = icon_button("♥" if row["favorite"] else "♡", "Favourite", partial(self.library_toggle, row))
        saved = samples.has(samples.row_path(row))
        listen = icon_button("▶", "Listen" if (row["ready"] or saved) else "Download this model in Models first", partial(self.library_listen, row))
        listen.setEnabled((row["ready"] or saved) and not self.preview_busy)
        use = QPushButton("Use")
        use.setObjectName("primary")
        use.setMinimumHeight(38)
        use.setToolTip("Use this voice in the Studio")
        use.clicked.connect(partial(self.library_use, row))
        actions.addWidget(fav)
        actions.addWidget(listen)
        actions.addStretch(1)
        actions.addWidget(use)
        lay.addLayout(actions)
        if not row["ready"] and not saved:
            lay.addWidget(label("Needs setup in Models", "muted"))
        self.lib_listen_buttons.append(listen)
        return frame

    def refresh_voice_library(self, *_):
        if not hasattr(self, "lib_grid") or self._lib_updating:
            return
        self._lib_updating = True
        try:
            everything = favorites.all_voices(self.store, self.engine_ready)
            self.fill_filter(self.lib_family, "Any language", [(f, f) for f in sorted({r["family"] for r in everything if r["family"]})])
            self.fill_filter(self.lib_model, "Any model", sorted({(r["model_name"].split(" · ")[0], r["model"]) for r in everything}))
            rows = [r for r in everything if favorites.matches(
                r, self.lib_search.text(), self.lib_fav.isChecked(), self.lib_kind.currentData(), self.lib_gender.currentData(),
                self.lib_family.currentData(), self.lib_model.currentData(), self.lib_avail.currentData())]
            rows.sort(key=lambda r: (not r["favorite"], not r["ready"], r["kind"], r["name"].lower()))
            self.lib_listen_buttons = []
            shown = rows[: self.lib_limit]
            self.lib_grid.set_cards([self.voice_card(r) for r in shown])
            have = sum(1 for r in rows if samples.has(samples.row_path(r)))
            self._lib_base = f"Showing {len(shown)} of {len(rows)} voices · {have} play instantly"
            self.lib_count.setText(self._lib_base + (" · preparing more…" if getattr(self, "sample_building", False) else ""))
            self.lib_more.setVisible(len(rows) > len(shown))
        finally:
            self._lib_updating = False

    # ------------------------------------------------------------------ Clones page
    def make_library(self):
        super().make_library()                       # the older table page stays alive (hidden) for the code that reads it
        from .ui_v5 import card, field, new_form, actions, MiniPlayer
        old = self.stack.widget(2)
        self.recorder = Recorder()
        self.clone_sample = None
        self.clone_original = None
        self.record_timer = QTimer(self)
        self.record_timer.setInterval(150)
        self.record_timer.timeout.connect(self.record_tick)
        page = QWidget()
        page.setObjectName("page")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(14)
        frame, fl = card("New voice")
        form = new_form()
        self.clone_name = QLineEdit()
        self.clone_name.setPlaceholderText("A new name is chosen for you. You can change it.")
        form.addRow(field("Name"), self.clone_name)
        fl.addLayout(form)
        self.clone_drop = DropZone("Drop an audio or video file here", f"Audio, or a video (MP4, MOV …): the sound is taken out and you choose the part to use ({MIN_SAMPLE:.0f} to {MAX_SAMPLE:.0f} seconds).")
        self.clone_drop.chosen.connect(self.load_sample)
        fl.addWidget(self.clone_drop)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.record_button = QPushButton("🎙  Record")
        self.record_button.clicked.connect(self.toggle_record)
        buttons.addWidget(button("Select file", self.choose_sample, True))
        buttons.addWidget(self.record_button)
        buttons.addWidget(button("Crop…", self.crop_sample))
        buttons.addStretch(1)
        fl.addLayout(buttons)
        self.clone_info = label("No sample yet.", "muted")
        fl.addWidget(self.clone_info)
        self.clone_preview = MiniPlayer(self)
        fl.addWidget(self.clone_preview)
        self.denoise_check = QCheckBox("Remove background noise")
        self.denoise_check.setToolTip("Cleans hiss and low rumble from the sample. Try it when the recording is not perfectly quiet.")
        self.denoise_check.toggled.connect(lambda _on: self.clone_original and self.load_sample(self.clone_original))
        fl.addWidget(self.denoise_check)
        left.addWidget(frame)

        frame, tl = card("Tips for a good copy")
        for line in ("One person speaking, nothing else.", "Quiet room, no music, no echo.", "A clear sample makes a clear clone.",
                     "The words spoken are filled in for you. Change them only if you know the exact words."):
            tl.addWidget(label("💡  " + line, ""))
        left.addWidget(frame)

        frame, sl = card("Save")
        form = new_form()
        self.clone_engine = QComboBox()
        self.clone_words = QLineEdit()
        self.clone_words.setPlaceholderText("Two lines are added for you. Change them if you know the exact words.")
        form.addRow(field("Save with"), self.clone_engine)
        form.addRow(field("Words spoken"), self.clone_words)
        sl.addLayout(form)
        self.clone_consent = QCheckBox("I have permission to use this voice.")
        sl.addWidget(self.clone_consent)
        self.clone_save = button("Save voice", self.save_clone_voice, True)
        self.clone_save.setMinimumHeight(42)
        sl.addWidget(self.clone_save)
        self.clone_note = label("", "muted")
        sl.addWidget(self.clone_note)
        left.addWidget(frame)
        left.addStretch(1)
        left_box = QWidget()
        left_box.setFixedWidth(430)
        left_box.setLayout(left)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setWidget(left_box)
        scroller.setFixedWidth(452)
        outer.addWidget(scroller)

        right = QVBoxLayout()
        right.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("My voices", "title"))
        top.addStretch(1)
        self.clone_count = label("", "muted")
        top.addWidget(self.clone_count)
        right.addLayout(top)
        self.clone_grid = CardGrid(300)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.clone_grid)
        right.addWidget(area, 1)
        pframe, pl = card()
        self.clone_player = MiniPlayer(self)
        self.clone_player.set_note("Press ▶ on a voice to hear its original sample.")
        pl.addWidget(self.clone_player)
        right.addWidget(pframe)
        outer.addLayout(right, 1)
        self.stack.removeWidget(old)
        old.setParent(self._holder)
        self.stack.insertWidget(2, page)
        self.fill_clone_engines()

    def fill_clone_engines(self):
        self.clone_engine.clear()
        for m in models().values():
            if m["kind"] == "clone" and not capabilities.unavailable_reason(m):
                self.clone_engine.addItem(m["name"].split(" · ")[0].replace(" (experimental)", ""), m["id"])
        self.clone_engine.setCurrentIndex(max(0, self.clone_engine.findData("chatterbox")))

    def refresh_library(self):
        super().refresh_library()
        if not hasattr(self, "clone_grid"):
            return
        items = clones.profiles(self.store)
        self.clone_count.setText(f"{len(items)} voice{'s' if len(items) != 1 else ''}")
        cards = [self.clone_card(p) for p in sorted(items, key=lambda p: p["name"].lower())]
        if not cards:
            hint = QFrame()
            hint.setObjectName("card")
            hl = QVBoxLayout(hint)
            hl.addWidget(label("No saved voices yet. Add a sample on the left.", "muted"))
            cards = [hint]
        self.clone_grid.set_cards(cards)

    def clone_card(self, p) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        avatar = QLabel(p["name"][:1].upper())
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = COLORS[int(hashlib.md5(p["id"].encode()).hexdigest(), 16) % len(COLORS)]
        avatar.setStyleSheet(f"background:{color};color:#ffffff;border-radius:20px;font-weight:700;font-size:17px;")
        head.addWidget(avatar)
        name = label(p["name"], "sectiontitle")
        name.setWordWrap(False)
        head.addWidget(name, 1)
        lay.addLayout(head)
        seconds = p.get("reference_analysis", {}).get("seconds")
        made = models().get(p["model"], {}).get("name", "").split(" · ")[0]
        lay.addWidget(chip_row(["Clone", made, f"{seconds:.0f} s" if seconds else ""]))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(icon_button("🗑", "Remove", partial(self.remove_clone_card, p)))
        row.addWidget(icon_button("Aa", "Change the name", partial(self.rename_clone_card, p)))
        row.addWidget(icon_button("✎", "Edit", partial(self.create_clone, p)))
        row.addWidget(icon_button("▶", "Hear the original sample", partial(self.play_clone, p)))
        row.addStretch(1)
        use = QPushButton("Use")
        use.setObjectName("primary")
        use.setMinimumHeight(38)
        use.clicked.connect(partial(self.use_clone_card, p))
        row.addWidget(use)
        lay.addLayout(row)
        return frame

    def play_clone(self, p, *_):
        """Plays the saved sample of the clone at once; before one exists it plays the original recording."""
        saved = samples.clone_path(p["id"])
        if samples.has(saved):
            self.clone_player.load(saved, play=True)
            self.clone_player.set_note(p["name"] + " · sample")
        else:
            self.clone_player.load(p["reference"], play=True)
            self.clone_player.set_note(p["name"] + " · original recording (the sample is being prepared)")
            QTimer.singleShot(300, self.scan_samples)

    def rename_clone_card(self, p, *_):
        name, ok = QInputDialog.getText(self, "Change the name", "New name for this voice:", text=p["name"])
        if not ok or not name.strip():
            return
        try:
            clones.rename_profile(self.store, p["id"], name)
        except ValueError as exc:
            return self.clone_note.setText(str(exc))
        self.refresh_library()
        self.refresh_voices()
        self.refresh_favorites()

    def use_clone_card(self, p, *_):
        self.select_voice(p["model"], p["id"])
        self.change_page(0)

    def remove_clone_card(self, p, *_):
        if self.confirm("Remove voice", f"Remove '{p['name']}'? Jobs already in the queue keep their sample."):
            clones.remove_profile(self.store, p["id"])
            samples.remove_clone(p["id"])
            self.refresh_library()
            self.refresh_voices()
            self.refresh_favorites()

    # ---- sample handling
    def choose_sample(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a voice sample", str(Path.home()),
                                              "Audio and video (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.mpeg *.mp2 *.wma);;All files (*)")
        if path:
            self.load_sample(path)

    def load_sample(self, path):
        """Convert any audio file to a clean mono WAV, check its length, and show it."""
        path = Path(path)
        try:
            limit = MAX_VIDEO_MB if path.suffix.lower() in VIDEO_EXT else MAX_MB
            if path.stat().st_size > limit * 1024 * 1024:
                raise ValueError(f"This file is larger than {limit} MB. Choose a shorter clip.")
            target = subdir("temp") / f"clone-{uuid.uuid4().hex[:8]}.wav"
            denoise = self.denoise_check.isChecked()

            def prepare():                       # runs in the background: a long video never freezes the window
                training._convert(path, target, 24000)
                if denoise:
                    audio_fx.denoise(target)
                return training.analyze(target)

            if os.environ.get("AVS_NO_PROMPTS"):
                info = prepare()
            else:
                from .reference_dialog import run_dialog
                info = run_dialog(self, "Taking the sound out… a long video can take a minute", prepare)
        except Exception as exc:
            self.clone_info.setText(str(exc).splitlines()[0][:240])
            return
        self.clone_original = path
        self.clone_sample = target
        seconds = info["seconds"]
        note = ""
        if seconds < MIN_SAMPLE:
            note = f"  ⚠ Too short. Use at least {MIN_SAMPLE:.0f} seconds."
        elif seconds > MAX_SAMPLE:
            note = f"  ⚠ Longer than {MAX_SAMPLE:.0f} seconds. Choose the part to keep."
        elif info["clipped"]:
            note = "  ⚠ Sounds distorted. A quieter recording is better."
        elif info["rms_dbfs"] < -40:
            note = "  ⚠ Very quiet. Record closer to the microphone."
        self.clone_info.setText(f"{path.name} · {seconds:.1f} s{note}")
        self.clone_preview.load(target)
        # A new name and two lines of words spoken are filled in at once, so nobody is asked for either (both can be changed).
        if not self.clone_name.text().strip():
            self.clone_name.setText(clones.new_name(self.store))
        if not self.clone_words.text().strip():
            self.clone_words.setText(clones.DEFAULT_TRANSCRIPT)
        # A video, or anything longer than the best length: open the part chooser straight away (the original file is never changed).
        if (path.suffix.lower() in VIDEO_EXT or seconds > MAX_SAMPLE) and not os.environ.get("AVS_NO_PROMPTS"):
            QTimer.singleShot(0, self.crop_sample)

    def crop_sample(self):
        if not self.clone_sample:
            return self.clone_info.setText("Add a sample first, then crop it.")
        from .reference_dialog import ReferenceDialog
        editor = ReferenceDialog(self, self.clone_sample)
        if editor.exec() and editor.result_file:
            edited = Path(editor.result_file)
            self.load_sample(edited)

    def toggle_record(self):
        if self.recorder._source is not None:
            return self.finish_record()
        try:
            self.record_path = subdir("temp") / f"recording-{uuid.uuid4().hex[:6]}.wav"
            self.recorder.start(self.record_path)
        except Exception as exc:
            self.clone_info.setText(str(exc)[:260])
            return
        self.record_button.setText("■  Stop")
        self.record_timer.start()

    def record_tick(self):
        seconds = self.recorder.seconds
        bars = "▮" * int(self.recorder.level * 12)
        self.clone_info.setText(f"Recording… {int(seconds // 60):02d}:{int(seconds % 60):02d}  {bars}")
        if seconds >= MAX_SECONDS:
            self.finish_record()

    def finish_record(self):
        self.record_timer.stop()
        path = self.recorder.stop()
        self.record_button.setText("🎙  Record")
        if path and Path(path).is_file():
            self.load_sample(path)

    def save_clone_voice(self):
        name = self.clone_name.text().strip() or clones.new_name(self.store)
        if not self.clone_sample:
            return self.clone_note.setText("Add or record a sample first.")
        seconds = training.analyze(self.clone_sample)["seconds"]
        if not MIN_SAMPLE <= seconds <= MAX_SAMPLE:
            return self.clone_note.setText(f"The sample must be {MIN_SAMPLE:.0f} to {MAX_SAMPLE:.0f} seconds. Use Crop.")
        if not self.clone_consent.isChecked():
            return self.clone_note.setText("Please confirm you have permission to use this voice.")
        try:
            saved = clones.save_profile(self.store, name, self.clone_engine.currentData(), self.clone_sample, self.clone_words.text(), True, {})
        except Exception as exc:
            return self.clone_note.setText(str(exc))
        self.clone_note.setText(f"Saved '{name}'. It works with every cloning model. Making a sample so you can hear it…")
        self.clone_name.clear()
        self.clone_words.clear()
        self.clone_consent.setChecked(False)
        self.clone_sample = self.clone_original = None
        self.clone_info.setText("No sample yet.")
        self.refresh_library()
        self.refresh_voices()
        self.refresh_favorites()
        if self.engine_ready(saved["model"]):
            self._clone_sample_id = saved["id"]
            self.start_preview(saved["model"], saved["id"], "", "clone")
        else:
            self.clone_note.setText(f"Saved '{name}'. Download its model in Models to hear it.")
