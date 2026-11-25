"""AI Voice Studio 0.5 interface.

Modern layout, Voice Library with favourites and listen-before-use samples, owner/user
controls, one-click dependency check, Portable Pack backup and easy updates.

This layer sits on top of the proven engine/queue code (ui_v3) and re-uses its widgets, so
generation, resume, downloads and repair behave exactly as before.
"""
from __future__ import annotations
import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QMenu, QPlainTextEdit, QSizePolicy, QSlider, QSpinBox, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .ui_v3 import StudioWindow as V3, SeekSlider
from .ui import label, button, layout_row
from .core import models, root, subdir, APP_VERSION, is_audio, split_sentences, safe_chunks, bundled, strip_pause_tags, PAUSE_TAG
from . import clones, favorites, preview, backup, health, theme, managed, capabilities, emotions
from .ui_extra import ExtraPages
from .widgets import StudioMeter, OnAir, SliderRow, FlowLayout, ChoiceBox, NeonBar, DropPick
from .ui_cards import CardPages
from .ui_updates import UpdatePages
from .ui_channels import ChannelPages
from . import channels as channels_mod
from . import shortcuts
from . import perf, warm_worker, scripttools, autofix, guard, samples, autoresolve, load_control, model_info, wordcheck
from .importers import DOC_FILTER, read_script
from .voice_catalog import QWEN_LANG
from .owner import Access, FEATURES, STATES, STATE_LABELS, SHOW, LOCK, HIDE, PRESETS

DEFAULT_FOOTER = "Whatsapp: +92 3104768426"
PAGE_KEYS = {1: "page.batch", 2: "page.clone", 3: "page.setup", 4: "page.settings", 5: "page.logs", 6: "page.voices",
             7: "page.design", 8: "page.training", 9: "page.sources", 10: "page.channels", 11: "page.check"}
LOCKED_TIP = "Locked by the owner"
MODES = [
    ("Automatic · best for my PC", "auto", "The software chooses the part size for your PC and the voice model. Long text is cut into safe parts and joined into ONE audio."),
    ("Fast · small parts", "fast", "Short parts: results appear sooner, the bar moves more often and weak PCs stay responsive. One finished audio."),
    ("Natural · paragraph groups", "group", "Several paragraphs together (choose 2 to 4). The voice flows between sentences. Slower on weak PCs."),
]


# ----------------------------------------------------------------------------- small building blocks
def card(title: str = "", hint: str = ""):
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    if title:
        lay.addWidget(label(title, "sectiontitle"))
    if hint:
        lay.addWidget(label(hint, "muted"))
    return frame, lay


def field(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("fieldlabel")
    lab.setWordWrap(False)
    lab.setMinimumWidth(112)
    return lab


class FormRows(QVBoxLayout):
    """Label + field rows. Each row can be hidden as a whole; labels line up with their fields."""

    def __init__(self, label_width: int = 130):
        super().__init__()
        self.label_width = label_width
        self.setSpacing(10)
        self.setContentsMargins(0, 0, 0, 0)
        self.rows = {}

    def addRow(self, lab, widget):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        lab.setFixedWidth(self.label_width)
        h.addWidget(lab, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(widget, 1, Qt.AlignmentFlag.AlignVCenter)
        self.addWidget(row)
        self.rows[widget] = row

    def setRowVisible(self, widget, visible):
        self.rows[widget].setVisible(visible)


def new_form(label_width: int = 130) -> FormRows:
    return FormRows(label_width)


def actions(*widgets) -> QHBoxLayout:
    """A row of buttons that keep their natural size."""
    row = QHBoxLayout()
    row.setSpacing(10)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    return row


def fmt_time(ms: int) -> str:
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}"


def make_collapsible(frame, store, key, default_open=True):
    """Turn a card into a section that opens and closes with an arrow. The choice is remembered."""
    lay = frame.layout()
    if lay is None or lay.count() < 2 or lay.itemAt(0).widget() is None:
        return None
    title = lay.itemAt(0).widget()
    text = title.text()
    body = QWidget()
    bl = QVBoxLayout(body)
    bl.setContentsMargins(0, 0, 0, 0)
    bl.setSpacing(lay.spacing())
    moved = []
    while lay.count() > 1:
        moved.append(lay.takeAt(1))
    for item in moved:
        if item.widget() is not None:
            bl.addWidget(item.widget())
        elif item.layout() is not None:
            bl.addLayout(item.layout())
        else:
            bl.addItem(item)
    lay.addWidget(body)
    head = QPushButton()
    head.setObjectName("fold")
    head.setCursor(Qt.CursorShape.PointingHandCursor)
    title.hide()
    lay.insertWidget(0, head)
    state = {"open": bool(store.setting("fold_" + key, default_open))}

    def show():
        body.setVisible(state["open"])
        head.setText(("▾   " if state["open"] else "▸   ") + text)
        head.setToolTip("Click to hide" if state["open"] else "Click to show")

    def toggle():
        state["open"] = not state["open"]
        store.set_setting("fold_" + key, state["open"])
        show()

    head.clicked.connect(toggle)
    show()
    return head, body


class MiniPlayer(QWidget):
    """Small play / pause / seek player used for voice samples."""

    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self.path = None
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.9)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.play_button = QPushButton("▶")
        self.play_button.setFixedWidth(46)
        self.play_button.setToolTip("Play / pause")
        self.play_button.clicked.connect(self.toggle)
        self.slider = SeekSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.time = label("00:00 / 00:00", "muted")
        self.time.setWordWrap(False)
        row.addWidget(self.play_button)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time)
        lay.addLayout(row)
        self.note = label("", "muted")
        lay.addWidget(self.note)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.player.durationChanged.connect(lambda n: self.slider.setRange(0, n))
        self.player.positionChanged.connect(self._position)
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(lambda _e, msg: self.set_note(msg or "This sample could not be played."))

    def set_note(self, text: str):
        self.note.setText(text)

    def _position(self, n):
        if not self.slider.isSliderDown():
            self.slider.setValue(n)
        self.time.setText(f"{fmt_time(n)} / {fmt_time(self.player.duration())}")

    def _state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("⏸" if playing else "▶")

    def load(self, path, play=False):
        path = Path(path)
        if not is_audio(path):
            self.set_note("That sample is not a valid audio file.")
            return
        self.path = path
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.set_note("")
        if play:
            self.window_ref.stop_other_players(self)
            self.player.play()

    def toggle(self):
        if self.path is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.window_ref.stop_other_players(self)
            self.player.play()


# ----------------------------------------------------------------------------- the window
class StudioWindow(ChannelPages, UpdatePages, CardPages, ExtraPages, V3):
    preview_ready = Signal(str, str)      # wav path, target ('studio' / 'library')
    python_checked = Signal(bool)          # is Python 3.11 on this PC?
    preview_failed = Signal(str, str)     # message, target
    pack_progress = Signal(float, str)
    pack_done = Signal(str)
    pack_failed = Signal(str)
    health_ready = Signal(object, object)
    train_progress = Signal(float, str)
    train_done = Signal(object)
    sources_ready = Signal(object)
    bench_progress = Signal(str)
    bench_done = Signal(object)
    preload_done = Signal(str)
    update_checked = Signal(object)
    update_downloaded = Signal(str)
    update_progress = Signal(float)
    bk_sizes_ready = Signal(object)
    backups_changed = Signal()
    sample_progress = Signal(int, int)
    sample_done = Signal()
    models_found = Signal(object)
    models_progress = Signal(float, str)
    models_connected = Signal(object)
    build_tools_done = Signal(object)
    gpu_names_ready = Signal()
    pc_info_ready = Signal(object)
    word_checked = Signal(str, object)           # job id or '', comparison result
    sizes_ready = Signal(str, object)           # model key (empty = finished), saved size row
    model_updates_ready = Signal(object, int)   # {key: what can be updated}, how many could not be checked

    def __init__(self):
        self.access = Access()
        self.access.open_to_all = True          # 0.22: everything is shown to everyone, no owner key needed
        self.gates = {}
        self.rows = {}
        self.engine_visible = {}
        self.preview_busy = False
        self.pack_busy = False
        self.pack_cancel = None
        self.lib_listen_buttons = []
        self.visible_gates = {}
        self._filter_key = None
        self._preloaded = None
        self._lib_updating = False
        self.preloading = False
        self.sample_building = False
        self._sample_failed = set()
        self._clone_sample_id = None
        self._library_row = None
        self._lib_base = ""
        self._last_pack_emit = 0.0
        self.channel_data = channels_mod.Channels()
        self._ch_shown = None
        self.studio_locked = False
        self._lock_released = False
        self.lock_widgets = []
        self.update_flags = {}
        self._size_cache = model_info.load()
        self._size_busy = False
        self._update_busy = False
        super().__init__()
        self.stack.addWidget(self.build_check_page())      # page 11: Check words
        self.move_logs_into_settings()
        if not self.store.setting("samples_manual_022", False):      # voice samples only when you ask for them
            self.store.set_setting("auto_samples", False)
            self.store.set_setting("samples_manual_022", True)
        self.migrate_settings()
        self.preview_ready.connect(self.on_preview_ready)
        self.preview_failed.connect(self.on_preview_failed)
        self.pack_progress.connect(self.on_pack_progress)
        self.pack_done.connect(self.on_pack_done)
        self.pack_failed.connect(self.on_pack_failed)
        self.health_ready.connect(self.show_health)
        self.train_progress.connect(self.on_train_progress)
        self.train_done.connect(self.on_train_done)
        self.sources_ready.connect(self.on_sources_ready)
        self.bench_progress.connect(lambda text: self.speed_report.setText(text))
        self.bench_done.connect(self.show_bench)
        self.preload_done.connect(self.on_preload_done)
        self.update_checked.connect(self.on_update_checked)
        self.update_downloaded.connect(self.on_update_downloaded)
        self.update_progress.connect(self.on_update_progress)
        self.bk_sizes_ready.connect(self.on_backup_sizes)
        self.backups_changed.connect(self.refresh_backup_list)
        self.sample_progress.connect(self.on_sample_progress)
        self.sample_done.connect(self.on_sample_done)
        self.models_found.connect(self.on_models_found)
        self.models_progress.connect(self.on_models_progress)
        self.models_connected.connect(self.on_models_connected)
        self.build_tools_done.connect(self.on_build_tools_done)
        self.sizes_ready.connect(self.on_sizes_ready)
        self.gpu_names_ready.connect(self.sync_speed_note)
        self.pc_info_ready.connect(self.on_pc_info)
        QTimer.singleShot(1500, self.load_pc_info)
        QTimer.singleShot(7000, self.auto_prepare_restored)
        self.word_checked.connect(self.on_word_checked)
        self.model_updates_ready.connect(self.on_model_updates_ready)
        self.script.textChanged.connect(self.stats_timer.start)
        self.script.textChanged.connect(self.update_counts)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=self.toggle_owner_mode)
        shortcuts.install(self)
        QTimer.singleShot(3000, self.show_rollback_notice)
        self.python_checked.connect(self.on_python_checked)
        if os.name == "nt" and not os.environ.get("AVS_NO_PROMPTS"):
            QTimer.singleShot(9000, self.python_startup_check)
        self.apply_theme()
        self.apply_access()
        self.refresh_favorites()
        self.refresh_design_engines()
        self.refresh_training_engines()
        self.refresh_sources()
        self.refresh_updates_view()
        self.refresh_backup_list()
        self.update_backup_size()
        self.start_update_timers()
        self.start_sample_timers()
        self.update_script_stats()
        if not os.environ.get("AVS_NO_PROMPTS"):
            QTimer.singleShot(9000, self.auto_check_sources)
        self.setWindowTitle("AI Voice Studio")
        self.air_timer = QTimer(self)
        self.air_timer.setInterval(500)
        self.air_timer.timeout.connect(self.update_on_air)
        self.air_timer.start()
        self.preload_timer = QTimer(self)
        self.preload_timer.setSingleShot(True)
        self.preload_timer.setInterval(3500)
        self.preload_timer.timeout.connect(self.preload_now)
        warm_worker.set_idle(int(self.store.setting("warm_minutes", 5)) * 60)
        self.start_load_control()
        threading.Thread(target=lambda: (load_control.gpu_names(), self.gpu_names_ready.emit()), daemon=True).start()
        self.progress_timer = QTimer(self)          # keeps the progress bars moving while a voice-over is made
        self.progress_timer.setInterval(1000)
        self.progress_timer.timeout.connect(self.tick_progress)
        self.progress_timer.start()
        os.environ["AVS_AUTO_RESOLVE"] = "1" if self.store.setting("auto_resolve", True) else "0"
        from .resource_policy import set_threads_override
        set_threads_override(int(self.store.setting("cpu_threads_override", 0)))
        self.restore_settings()
        self.apply_playback_volume()
        for combo in (self.model_combo, self.voice_combo):
            combo.currentIndexChanged.connect(lambda _i: self.preload_timer.start())
        if not os.environ.get("AVS_NO_PROMPTS"):
            QTimer.singleShot(6000, self.preload_now)

    # ------------------------------------------------------------------------- saved settings
    def migrate_settings(self):
        """One-time upgrade of text sizes so older installs get the clearer defaults."""
        if self.store.setting("text_migrated_07", False):
            self.migrate_mode()
            return
        if int(self.store.setting("ui_text_px", 15)) == 14:
            self.store.set_setting("ui_text_px", 15)
        if int(self.store.setting("script_px", 18)) == 17:
            self.store.set_setting("script_px", 18)
        self.store.set_setting("text_migrated_07", True)
        if not self.store.setting("mode_migrated_08", False):
            self.store.set_setting("segment_mode", "auto")  # older versions always cut long text into safe parts
            self.store.set_setting("mode_migrated_08", True)

    def migrate_mode(self):
        if not self.store.setting("mode_migrated_08", False):
            self.store.set_setting("segment_mode", "auto")
            self.store.set_setting("mode_migrated_08", True)

    def persisted_widgets(self):
        return [("s_speed", self.output_speed), ("s_pitch", self.pitch), ("s_temperature", self.temperature), ("s_guidance", self.guidance),
                ("s_emotion_weight", self.emotion_weight), ("s_exaggeration", self.opt_widgets["exaggeration"][1]),
                ("s_cfg", self.opt_widgets["cfg_weight"][1]), ("s_alpha", self.style_alpha), ("s_beta", self.style_beta),
                ("s_steps", self.diffusion_steps), ("s_escale", self.embedding_scale), ("s_volume", self.volume_spin),
                ("s_emotion_text", self.emotion_text)]

    def restore_settings(self):
        """Bring back the last engine, voice and settings. The script text is deliberately not saved."""
        st = self.store
        kind = st.setting("s_voice_type")
        if kind and self.voice_type.findData(kind) >= 0:
            self.voice_type.setCurrentIndex(self.voice_type.findData(kind))
        model = st.setting("s_model")
        if model and self.model_combo.findData(model) >= 0:
            self.model_combo.setCurrentIndex(self.model_combo.findData(model))
        for key, combo in (("s_gender", self.gender_combo), ("s_vlang", self.vlang_combo)):
            index = combo.findData(st.setting(key, ""))
            if index >= 0:
                combo.setCurrentIndex(index)
        voice = st.setting("voice_" + (self.model_combo.currentData() or ""))
        if voice and self.voice_combo.findData(voice) >= 0:
            self.voice_combo.setCurrentIndex(self.voice_combo.findData(voice))
        for key, widget in self.persisted_widgets():
            value = st.setting(key)
            if value is None:
                continue
            if isinstance(widget, QLineEdit):
                widget.setText(str(value))
            else:
                try:
                    widget.setValue(type(widget.value())(value))
                except (TypeError, ValueError):
                    pass
        for key, widget in self.persisted_widgets():
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda text, k=key: st.set_setting(k, text))
            else:
                widget.valueChanged.connect(lambda value, k=key: st.set_setting(k, value))
        for key, combo in (("s_voice_type", self.voice_type), ("s_model", self.model_combo), ("s_gender", self.gender_combo), ("s_vlang", self.vlang_combo)):
            combo.currentIndexChanged.connect(lambda _i, k=key, c=combo: st.set_setting(k, c.currentData() or ""))

    # ------------------------------------------------------------------------- playback volume
    def apply_playback_volume(self, *_):
        level = int(self.store.setting("play_volume", 80))
        for name in ("studio_player", "queue_player", "sample_player", "library_player", "design_player"):
            player = getattr(self, name, None)
            if player is not None:
                player.audio.setVolume(level / 100)

    def change_playback_volume(self, value):
        self.store.set_setting("play_volume", int(value))
        self.apply_playback_volume()

    # ------------------------------------------------------------------------- background preparation
    def preload_now(self):
        """Load the selected voice engine in the background so the first Generate is quick."""
        if not load_control.preload_allowed(self.store) or self.store.setting("low_end_mode", False):
            return
        if self.preloading or self.preview_busy or self.running or getattr(self, "pending_jobs", []) or self.busy_installer or self.move_pending or self.tasks:
            return
        model, voice = self.model_combo.currentData(), self.voice_combo.currentData()
        if not model or (not voice and models()[model]["kind"] != "clone") or not self.engine_ready(model):
            return
        self._quiet_resolve = True
        try:
            v, reference, ref_text, profile = self.resolve_voice(model, voice)
        except ValueError:
            return
        finally:
            self._quiet_resolve = False
        key = (model, v, reference)
        if key == self._preloaded:
            return
        opts = self.options()
        device = self.preferred_device()
        self.preloading = True
        self.sample_player.set_note("Preparing the voice engine in the background…")

        def work():
            from . import preview
            try:
                preview.warm_up(self.store, model, v, reference, ref_text, opts, device)
                self._preloaded = key
                self.preload_done.emit("ready")
            except Exception:
                self.preload_done.emit("")

        threading.Thread(target=work, daemon=True).start()

    def on_preload_done(self, status):
        self.preloading = False
        self.sample_player.set_note("Voice engine ready. Generation will start quickly." if status else "")

    # ------------------------------------------------------------------------- access helpers
    def gate(self, key, *widgets):
        self.gates.setdefault(key, []).extend(widgets)

    @staticmethod
    def mark_locked(widget, locked):
        """Show 'Locked by the owner' while locked and restore the normal tooltip afterwards."""
        if locked:
            if widget.property("avs_tip") is None:
                widget.setProperty("avs_tip", widget.toolTip())
            widget.setToolTip(LOCKED_TIP)
        else:
            tip = widget.property("avs_tip")
            if tip is not None:
                widget.setToolTip(tip)
                widget.setProperty("avs_tip", None)

    def stop_generation(self, jid=None):
        """Stop right now, in the middle of a part if needed. Finished parts are kept."""
        from . import services
        targets = [jid] if jid else list(self.running)
        if not jid:
            self.pending_jobs = []
        if not targets:
            return self.toast("Nothing is generating right now.")
        for job_id in targets:
            flag = self.stop_flags.get(job_id)
            if flag:
                flag.set()
            services.stop_job(job_id)
        self.toast("Stopping… finished parts are kept.")

    def clear_selected(self):
        jid = self.selected_job()
        if not jid:
            return self.toast("Select a job in the list first.")
        if jid in self.running:
            return self.alert("This voice-over is being made. Press Stop first, then Clear.")
        self.pending_jobs = [j for j in getattr(self, "pending_jobs", []) if j != jid]
        self.store.delete_job(jid)
        if self.studio_job == jid:
            self.studio_job = None
        self.refresh_queue()
        self.refresh_history()

    def clear_queue(self):
        if self.running:
            return self.alert("Voice-overs are being made. Press Stop first, then Clear all.")
        super().clear_queue()
        self.refresh_history()

    def show_failure(self, jid, error):
        if os.environ.get("AVS_NO_PROMPTS"):
            return
        job = self.store.job(jid)
        model = job["model"] if job else ""
        diagnosis = autofix.diagnose(error, model)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("A voice-over could not be finished")
        box.setText(diagnosis.title + ".")
        box.setInformativeText(diagnosis.advice + (f"\n\nJob: {job['title']}" if job else ""))
        fix = box.addButton(diagnosis.action_label, QMessageBox.ButtonRole.AcceptRole) if diagnosis.action else None
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if fix is not None and box.clickedButton() is fix:
            self.run_fix(diagnosis.action, job)

    def run_fix(self, action, job):
        model = job["model"] if job else self.model_combo.currentData()
        if action in ("retry_auto", "split") and job:
            options = json.loads(job["options"] or "{}")
            options.update(segment_mode="auto", auto_chunk=True, exact_parts=False)
            self.store.set_job_options(job["id"], options)
            self.store.rechunk_failed(job["id"])
            self.change_page(1)
            self.refresh_queue()
            for row in range(self.queue_table.rowCount()):
                item = self.queue_table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == job["id"]:
                    self.queue_table.selectRow(row)
            self.run_selected()
        elif action == "cpu":
            self.store.set_setting("compute_mode", "cpu")
            self.store.set_setting("compute_cpu_temp", True)      # set by the automatic fix: reset to Automatic at the next start
            self.compute.blockSignals(True)
            self.compute.setCurrentIndex(max(0, self.compute.findData("cpu")))
            self.compute.blockSignals(False)
            self.toast("Now using the processor. Run the voice-over again.")
        elif action == "repair" and model:
            self.change_page(3)
            self.install_card(model)
        elif action == "transcript" and job:
            profile = next((p for p in clones.profiles(self.store) if p["reference"] == job["reference"]), None)
            if profile and self.ask_transcript(profile):
                self.toast("Saved. Run the voice-over again.")
        elif action == "health":
            self.change_page(4)
            self.settings_tabs.setCurrentIndex(4)
            self.run_health_check()

    def note_failure_streak(self, error):
        """After 3 failures in a row the queue stops, so a broken setup does not keep hammering the hardware."""
        self._fail_streak = (getattr(self, "_fail_streak", 0) + 1) if error else 0
        if error and self._fail_streak >= 3 and self.store.setting("guard_breaker", True):
            self.pending_jobs = []
            self._fail_streak = 0
            self.toast("The queue was stopped after 3 failed voice-overs in a row. Check the problem, then press Generate.")

    def handle_job_end(self, jid, error):
        super().handle_job_end(jid, error)
        self.note_failure_streak(error)
        if error:
            QTimer.singleShot(0, lambda: self.show_failure(jid, error))
            return
        try:
            job = self.store.job(jid)
            if job and job["status"] == "completed":
                if self.store.setting("history_autoplay", True):
                    self.play_history(job)
                elif self.store.setting("beep_done", True):
                    QApplication.beep()
                self.refresh_history()
                if json.loads(job["options"] or "{}").get("word_check"):
                    self.run_word_check_job(job)
        except Exception:
            pass

    def update_on_air(self):
        busy = bool(self.running) or bool(getattr(self, "pending_jobs", [])) or self.preview_busy
        self.on_air.set_active(busy)

    def job_progress(self, jid):
        """(parts finished plus the part being made, number of parts, finished parts). The bar moves inside a long part too."""
        from . import services
        parts = self.store.sections(jid)
        total, done = len(parts), sum(1 for x in parts if x["status"] == "complete")
        running = jid in self.running
        effective = min(float(total), done + (services.part_fraction(jid) if running else 0.0))
        return effective, total, done

    def running_now_text(self, jid, done, total):
        from . import services
        if done >= total:
            return "Joining the parts into one audio…"
        return f"Part {done + 1} of {total} · {perf.fmt_duration(services.job_elapsed(jid))} so far"

    def queue_now_text(self, job, state, facts, reason):
        """The plain sentence in the 'Now' column."""
        if state == "running":
            effective, total, done = self.job_progress(job["id"])
            text = self.running_now_text(job["id"], done, total)
            return (reason + " · " + text) if reason and reason not in ("Generating",) and "voice" in reason.lower() else text
        if state == "waiting":
            return reason or "Waiting for its turn"
        if state == "completed":
            checked = (facts["options"].get("word_check_result") or {}).get("summary", "")
            return ("Done · " + checked) if checked else "Done · ready to play"
        err = (job.get("error") or "").strip().splitlines()
        return "Failed" + (": " + err[0][:90] if err else "")

    def parts_text(self, jid, done, total):
        from . import services
        text = f"{done} / {total} parts"
        if jid in self.running and done < total:
            text += f" · part {done + 1} in progress · {perf.fmt_duration(services.job_elapsed(jid))}"
        return text

    def tick_progress(self):
        """Once a second while something is made: move the studio bar and the bars in the queue list (without rebuilding the list)."""
        if not self.running:
            if getattr(self, "queue_hint", None) is not None and self.queue_hint.text() and not getattr(self, "pending_jobs", []):
                self.update_queue_hint()
            return
        self.update_queue_hint()
        try:
            if self.studio_job in self.running:
                self.refresh_studio_job()
            if self.stack.currentIndex() == 1 and hasattr(self, "queue_table"):
                table = self.queue_table
                for row in range(table.rowCount()):
                    item = table.item(row, 0)
                    jid = item.data(Qt.ItemDataRole.UserRole) if item else None
                    bar = table.cellWidget(row, 5)
                    if jid in self.running and bar is not None:
                        effective, total, done = self.job_progress(jid)
                        bar.setRange(0, 1000)
                        bar.setValue(int(1000 * effective / max(1, total)))
                        bar.setFormat(f"{done} / {total} parts")
                        now_item = table.item(row, 6)
                        if now_item is not None:
                            now_item.setText(self.running_now_text(jid, done, total))
        except Exception:
            pass

    def refresh_studio_job(self):
        super().refresh_studio_job()
        if hasattr(self, "studio_stop"):
            self.studio_stop.setEnabled(bool(self.running))
        try:
            job = self.store.job(self.studio_job) if self.studio_job else None
            if job and job["status"] == "running":
                parts = self.store.sections(self.studio_job)
                left = sum(1 for x in parts if x["status"] != "complete")
                extra = perf.eta_text(self.studio_job, left)
                if extra:
                    self.studio_status.setText(self.studio_status.text() + " · " + extra)
            elif job and job["status"] == "completed":
                self.show_speed_report()
        except Exception:
            pass
        try:
            job = self.store.job(self.studio_job) if self.studio_job else None
            if job:
                effective, total, done = self.job_progress(job["id"])
                self.studio_progress.setRange(0, 1000)
                if job["status"] == "completed":
                    self.studio_progress.setValue(1000)
                    made = float(json.loads(job["options"] or "{}").get("made_seconds", 0) or 0)
                    if made:
                        self.studio_status.setText(f"Completed in {perf.fmt_duration(made)} · listen or save below")
                else:
                    self.studio_progress.setValue(int(1000 * effective / max(1, total)))
                    if job["id"] in self.running and done < total:
                        from . import services
                        self.studio_status.setText(f"Generating · {self.parts_text(job['id'], done, total)}")
        except Exception:
            pass
        self.sync_studio_lock()

    def stop_other_players(self, keep=None):
        for attr in ("sample_player", "library_player"):
            p = getattr(self, attr, None)
            if p is not None and p is not keep:
                p.player.pause()
        for attr in ("studio_player", "queue_player"):
            p = getattr(self, attr, None)
            if p is not None and p is not keep:
                p.player.pause()

    # ------------------------------------------------------------------------- shell
    def make_shell(self):
        root_widget = QWidget()
        root_widget.setObjectName("root")
        self.setCentralWidget(root_widget)
        overall = QVBoxLayout(root_widget)
        overall.setContentsMargins(0, 0, 0, 0)
        overall.setSpacing(0)

        top = QFrame()
        top.setObjectName("topbar")
        top.setFixedHeight(66)
        bar = QHBoxLayout(top)
        bar.setContentsMargins(22, 0, 22, 0)
        bar.setSpacing(14)
        self.side_toggle = QPushButton("☰")
        self.side_toggle.setObjectName("icon")
        self.side_toggle.setFixedSize(40, 40)
        self.side_toggle.setToolTip("Show or hide the menu text")
        self.side_toggle.clicked.connect(self.toggle_sidebar)
        bar.addWidget(self.side_toggle)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(bundled("resources/logo_small.png"))).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        logo.setFixedSize(42, 42)
        bar.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.setContentsMargins(0, 0, 0, 0)
        brand = label("AI Voice Studio", "brand")
        brand.setWordWrap(False)
        brand.setFixedHeight(24)
        brand.setContentsMargins(0, 0, 0, 0)
        by = label("by Unzip Spark", "muted")
        by.setWordWrap(False)
        by.setFixedHeight(15)
        by.setContentsMargins(0, 0, 0, 0)
        titles.addWidget(brand, 0, Qt.AlignmentFlag.AlignBottom)
        titles.addWidget(by, 0, Qt.AlignmentFlag.AlignTop)
        self.mode_badge = label("Owner", "badge")
        self.mode_badge.setWordWrap(False)
        self.mode_badge.setFixedHeight(26)
        self.mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addLayout(titles)
        bar.addWidget(self.mode_badge)
        self.theme_toggle = QPushButton("🌙")
        self.theme_toggle.setObjectName("icon")
        self.theme_toggle.setFixedSize(40, 40)
        self.theme_toggle.setToolTip("Switch between dark and light")
        self.theme_toggle.clicked.connect(self.toggle_theme)
        bar.addWidget(self.theme_toggle)
        bar.addStretch()
        self.resources_label = label("", "muted")
        self.resources_label.hide()
        self.on_air = OnAir()
        bar.addWidget(self.on_air)
        bar.addSpacing(10)
        deck = QFrame()
        deck.setObjectName("meterdeck")
        dl = QHBoxLayout(deck)
        dl.setContentsMargins(8, 4, 8, 4)
        dl.setSpacing(6)
        self.meters = {}
        self.meter_animations = {}
        for key in ("CPU", "RAM", "GPU"):
            meter = StudioMeter(key)
            dl.addWidget(meter)
            self.meters[key] = (meter, meter)
            anim = QPropertyAnimation(meter, b"level", self)
            anim.setDuration(350)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.meter_animations[key] = anim
        bar.addWidget(deck)
        overall.addWidget(top)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        overall.addLayout(body, 1)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        self.side_frame = side
        self.nav_heads = []
        nav = QVBoxLayout(side)
        nav.setContentsMargins(12, 16, 12, 14)
        nav.setSpacing(3)
        names = {0: "🎙  Studio", 6: "🎧  Library", 7: "✨  Design", 8: "🎓  Training", 1: "📚  Queue",
                 2: "🧬  Clones", 3: "⬇  Models", 10: "📺  Channels", 9: "🔗  Sources", 4: "⚙  Settings", 5: "📝  Logs", 11: "✅  Check words"}
        self.nav_buttons = [None] * 12
        self.nav_groups = {}
        for head, indexes in (("CREATE", (0, 6, 7, 8, 1, 2, 11)), ("MANAGE", (3, 10, 9, 4, 5))):
            if head == "MANAGE":
                nav.addSpacing(8)
            head_label = QPushButton()
            head_label.setObjectName("navfold")
            head_label.setCursor(Qt.CursorShape.PointingHandCursor)
            head_label.clicked.connect(partial(self.toggle_nav_group, head))
            self.nav_heads.append(head_label)
            nav.addWidget(head_label)
            group = QWidget()
            gl = QVBoxLayout(group)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(3)
            for idx in indexes:
                b = QPushButton(names[idx])
                b.setObjectName("nav")
                b.setCheckable(True)
                b.clicked.connect(partial(self.change_page, idx))
                gl.addWidget(b)
                self.nav_buttons[idx] = b
            nav.addWidget(group)
            self.nav_groups[head] = (head_label, group)
        nav.addStretch()
        self.owner_button = QPushButton()
        self.owner_button.setObjectName("ghost")
        self.owner_button.clicked.connect(self.toggle_owner_mode)
        nav.addWidget(self.owner_button)
        self.version_label = label(f"Version {APP_VERSION}", "muted")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.version_label)
        body.addWidget(side)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.footer = label(self.access.footer_text(DEFAULT_FOOTER), "footer")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overall.addWidget(self.footer)

    def toggle_sidebar(self, *_):
        collapsed = not bool(self.store.setting("side_collapsed", False))
        self.store.set_setting("side_collapsed", collapsed)
        self.apply_sidebar()

    def toggle_nav_group(self, head, *_):
        self.store.set_setting("nav_fold_" + head, not bool(self.store.setting("nav_fold_" + head, False)))
        self.apply_nav_folds()

    def apply_nav_folds(self):
        icons_only = bool(self.store.setting("side_collapsed", False))
        for head, (button_, group) in self.nav_groups.items():
            folded = bool(self.store.setting("nav_fold_" + head, False))
            button_.setText(("▸  " if folded else "▾  ") + head)
            button_.setToolTip("Click to show" if folded else "Click to hide")
            group.setVisible(icons_only or not folded)      # in the narrow icon bar nothing is hidden

    def apply_sidebar(self):
        collapsed = bool(self.store.setting("side_collapsed", False))
        self.apply_nav_folds()
        self.side_frame.setFixedWidth(70 if collapsed else 232)
        for b in self.nav_buttons:
            full = b.property("full") or b.text()
            b.setProperty("full", full)
            b.setText(full.split("  ")[0] if collapsed else full)
            b.setToolTip(full.split("  ", 1)[-1] if collapsed else b.toolTip())
        for head in self.nav_heads:
            head.setVisible(not collapsed)
        self.version_label.setVisible(not collapsed)
        self.owner_button.setText(("🔓" if self.access.is_owner else "🔒") if collapsed else ("🔓  Lock for users" if self.access.is_owner else "🔒  Owner login"))
        self.owner_button.setVisible(not getattr(self.access, "open_to_all", False))      # no owner login when everything is open (0.22)

    def toggle_theme(self, *_):
        new = "light" if self.store.setting("ui_theme", "dark") == "dark" else "dark"
        self.store.set_setting("ui_theme", new)
        if hasattr(self, "theme_combo"):
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(new)))
            self.theme_combo.blockSignals(False)
        self.apply_theme()

    def change_page(self, idx):
        key = PAGE_KEYS.get(idx)
        if key and self.access.state(key) != SHOW:
            idx = 0
        super().change_page(idx)
        if idx == 6:
            self.refresh_voice_library()
            QTimer.singleShot(500, self.scan_samples)
        if idx == 7:
            self.refresh_design_engines()
        if idx == 8:
            self.refresh_training_engines()
        if idx == 9:
            self.refresh_sources()
        if idx == 10:
            self.refresh_channels()
        if idx == 11:
            self.check_status.setText("" if wordcheck.installed() else "The Word checker is not installed yet: open Models, find 'Word checker' and press Download.")

    # ------------------------------------------------------------------------- theme
    def apply_theme(self):
        mode = self.store.setting("ui_theme", "dark")
        accent = self.store.setting("ui_accent", "Teal")
        px = int(self.store.setting("ui_text_px", 15))
        script_px = int(self.store.setting("script_px", 14))
        self.setStyleSheet(theme.stylesheet(mode, accent, px))
        if hasattr(self, "theme_toggle"):
            self.theme_toggle.setText("🌙" if mode == "dark" else "☀")
        theme.apply_palette(QApplication.instance(), mode, accent)
        self.script.setStyleSheet(f"font-size:{script_px}px;")
        colors = theme.palette(mode, accent)
        for meter, _same in self.meters.values():
            meter.set_colors(colors["track"], colors["text"], colors["muted"])

    # ------------------------------------------------------------------------- Voice Studio page
    def make_studio(self):
        self.stats_timer = QTimer(self)
        self.stats_timer.setSingleShot(True)
        self.stats_timer.setInterval(350)
        self.stats_timer.timeout.connect(self.update_script_stats)
        super().make_studio()
        old = self.stack.widget(0)
        self._holder = QWidget(self)
        self._holder.hide()
        self.studio_progress = NeonBar(height=10)      # moves while parts are being made
        self.studio_progress.setRange(0, 1000)

        # Controls that the older code still reads, kept alive but hidden.
        self.output_speed.setSuffix(" ×")
        self.output_speed.setToolTip("1.00 is normal speed")
        self.pitch.setToolTip("Raise or lower the voice by musical steps. 0 = unchanged")
        self.temperature.setToolTip("Higher = livelier and less predictable. Lower = steadier.")
        self.guidance.setToolTip("How strictly the voice follows the text and the reference. Higher = more exact.")
        self.emotion_weight.setToolTip("How strongly the emotion description is applied.")
        self.emotion_text.setToolTip("Describe the feeling, for example: calm and warm")
        self.style_alpha = QDoubleSpinBox()
        self.style_beta = QDoubleSpinBox()
        self.embedding_scale = QDoubleSpinBox()
        self.diffusion_steps = QSpinBox()
        for widget in (self.style_alpha, self.style_beta, self.embedding_scale):
            widget.setSingleStep(0.05)
        self.embedding_scale.setSingleStep(0.1)
        self.style_alpha.setToolTip("Lower keeps the sound of the reference voice. Higher follows the text more.")
        self.style_beta.setToolTip("Lower keeps the reference speaking style. Higher follows the text more.")
        self.diffusion_steps.setToolTip("More steps give a more varied delivery but take longer.")
        self.embedding_scale.setToolTip("Higher = more expressive, but can sound less natural.")
        self.emotion_preset = QComboBox()
        self.emotion_preset.setToolTip("Quick emotion styles. Only offered for engines that can follow them.")
        self.emotion_preset.activated.connect(self.emotion_chosen)
        expression = self.opt_widgets["exaggeration"][1]
        pacing = self.opt_widgets["cfg_weight"][1]
        expression.setToolTip("Higher = more dramatic delivery.")
        pacing.setToolTip("Fine control of pacing and reference strength. Default 0.5.")
        self.pause.setRange(0, 2500)
        self.pause.setSingleStep(50)
        self.pause.setSuffix(" ms")
        self.pause.setValue(int(self.store.setting("pol_pause", 220)))
        self.pause.valueChanged.connect(lambda n: self.store.set_setting("pol_pause", n))
        self.pause.show()

        self.script.setPlaceholderText(
            "Paste or type your complete voice-over script here.\n\n"
            "Long scripts are fine. They are made in small parts and joined into ONE finished audio.")
        self.script.setMinimumHeight(340)
        self.script.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.segment_mode.blockSignals(True)
        self.segment_mode.clear()
        for text, data, tip in MODES:
            self.segment_mode.addItem(text, data)
            self.segment_mode.setItemData(self.segment_mode.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        saved_mode = {"whole": "auto", "paragraph": "group", "sentence": "fast"}.get(self.store.setting("segment_mode", "auto"), self.store.setting("segment_mode", "auto"))
        self.segment_mode.setCurrentIndex(max(0, self.segment_mode.findData(saved_mode)))
        self.segment_mode.blockSignals(False)
        self.segment_mode.setToolTip("Every choice gives one finished audio file.")
        self.segment_mode.currentIndexChanged.connect(lambda _i: self.store.set_setting("segment_mode", self.segment_mode.currentData()))
        self.segment_mode.currentIndexChanged.connect(self.stats_timer.start)

        import_button = batch_button = None
        for b in old.findChildren(QPushButton):
            if b.text() == "Import TXT / DOCX":
                import_button = b
            elif b.text() == "Import multiple scripts to batch":
                batch_button = b
        import_button.setText("Import file")
        batch_button.setText("Batch import")

        # ---- left column
        left = QVBoxLayout()
        left.setSpacing(14)
        script_card, sl = card()
        head = QHBoxLayout()
        head.addWidget(label("Script", "sectiontitle"))
        head.addStretch()
        self.clean_button = QPushButton("Clean")
        self.clean_button.setToolTip("Tidy spaces, blank lines and curly quotes. The words stay the same.")
        self.clean_button.clicked.connect(self.clean_script)
        head.addWidget(self.clean_button)
        head.addWidget(import_button)
        head.addWidget(batch_button)
        sl.addLayout(head)
        sl.addWidget(self.script, 1)
        self.script_counts = label("", "stat")
        self.script_counts.setWordWrap(False)
        sl.addWidget(self.script_counts)
        self.script_stats = label("", "muted")
        sl.addWidget(self.script_stats)
        mode_row = QHBoxLayout()
        mode_label = field("Mode")
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.segment_mode, 1)
        self.group_combo = QComboBox()
        for n in (2, 3, 4):
            self.group_combo.addItem(f"{n} paragraphs at once", n)
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findData(int(self.store.setting("group_size", 3)))))
        self.group_combo.currentIndexChanged.connect(lambda _i: (self.store.set_setting("group_size", self.group_combo.currentData()), self.stats_timer.start()))
        self.segment_mode.currentIndexChanged.connect(self.sync_group_combo)
        mode_row.addWidget(self.group_combo)
        sl.addLayout(mode_row)
        self.gate("ctl.segment", self.group_combo)
        self.sync_group_combo()
        left.addWidget(script_card, 1)
        self.gate("ctl.segment", mode_label, self.segment_mode)
        self.gate("ctl.import", import_button, batch_button, self.clean_button)

        make_card, ml = card()
        self.lock_note = label("🔒 The model and mode are locked while this voice-over is being made. Press “Add to queue” to line up the next text, then you can change them.", "muted")
        self.lock_note.hide()
        ml.addWidget(self.lock_note)
        self.word_check_box = QCheckBox("Check that every word was spoken after this voice-over (slower)")
        self.word_check_box.setToolTip("Off by default. When ticked, the finished audio is turned back into text and compared with your script, "
                                       "and you get a list of missing words. Needs the Word checker (Models page).")
        self.word_check_box.toggled.connect(self.word_check_toggled)
        ml.addWidget(self.word_check_box)
        self.queue_hint = label("", "muted")
        self.queue_hint.setWordWrap(False)
        self.generate_button.setMinimumHeight(46)
        ml.addWidget(self.generate_button)
        ml.addWidget(self.queue_hint)
        prog_row = QHBoxLayout()
        prog_row.setSpacing(12)
        self.studio_status.setWordWrap(False)
        prog_row.addWidget(self.studio_status)
        prog_row.addWidget(self.studio_progress, 1)
        prog_row.addWidget(self.studio_pause)
        self.studio_stop = QPushButton("■ Stop")
        self.studio_stop.setObjectName("danger")
        self.studio_stop.setToolTip("Stop right now, even in the middle of a part. Finished parts are kept.")
        self.studio_stop.setEnabled(False)
        self.studio_stop.clicked.connect(lambda: self.stop_generation(self.studio_job if self.studio_job in self.running else None))
        prog_row.addWidget(self.studio_stop)
        ml.addLayout(prog_row)
        ml.addWidget(self.studio_player)
        vol = QHBoxLayout()
        vol.setSpacing(10)
        vol.addWidget(field("🔊 Playback volume"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.store.setting("play_volume", 80)))
        self.volume_slider.valueChanged.connect(self.change_playback_volume)
        vol.addWidget(self.volume_slider, 1)
        ml.addLayout(vol)
        left.addWidget(make_card)

        # ---- right column
        right = QVBoxLayout()
        right.setSpacing(14)

        voice_card, vl = card("Voice")
        form = new_form(104)
        vl.addLayout(form)

        def add_row(target_form, key, text, widget, gate_key, engine=False):
            lab = field(text)
            target_form.addRow(lab, widget)
            self.rows[key] = (lab, widget, gate_key, engine, target_form)
            return lab

        self.voice_type.setParent(self._holder)   # still the real value holder; the buttons below drive it
        type_box = QWidget()
        tb = QHBoxLayout(type_box)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(8)
        self.type_buttons = {}
        for kind, text in (("tts", "Built-in voices"), ("clone", "My cloned voices")):
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=kind: self.voice_type.setCurrentIndex(max(0, self.voice_type.findData(k))))
            tb.addWidget(b)
            self.type_buttons[kind] = b
        tb.addStretch(1)
        vl.insertWidget(1, type_box)
        self.gate("ctl.voice_type", type_box)
        self.voice_type.currentIndexChanged.connect(self.sync_type_buttons)
        self.sync_type_buttons()
        add_row(form, "engine", "Model", self.model_combo, "ctl.engine")
        self.gender_combo = QComboBox()
        for text, value in (("Any gender", ""), ("Female", "Female"), ("Male", "Male")):
            self.gender_combo.addItem(text, value)
        self.vlang_combo = QComboBox()
        self.vlang_combo.addItem("Any language", "")
        add_row(form, "vlang", "Language", self.vlang_combo, "ctl.search", engine=True)
        add_row(form, "gender", "Gender", self.gender_combo, "ctl.search", engine=True)
        self.gender_combo.currentIndexChanged.connect(self.refresh_voices)
        self.vlang_combo.currentIndexChanged.connect(self.refresh_voices)
        self.voice_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.star_button = QPushButton("☆")
        self.star_button.setObjectName("star")
        self.star_button.setCheckable(True)
        self.star_button.setToolTip("Add this voice to your favourites")
        self.star_button.clicked.connect(self.toggle_favorite)
        self.preview_button = QPushButton("▶ Listen")
        self.preview_button.setToolTip("Hear a short sample of this voice")
        self.preview_button.clicked.connect(self.preview_current)
        add_row(form, "voice", "Voice", self.voice_combo, None)
        actions_box = QWidget()
        ab = QHBoxLayout(actions_box)
        ab.setContentsMargins(0, 0, 0, 0)
        ab.setSpacing(8)
        ab.addWidget(self.preview_button, 1)
        ab.addWidget(self.star_button)
        add_row(form, "voice_actions", "", actions_box, None)
        add_row(form, "search", "Find", self.voice_search, "ctl.search")
        self.fav_combo = QComboBox()
        self.fav_combo.activated.connect(self.favorite_chosen)
        add_row(form, "favorites", "Favourites", self.fav_combo, "ctl.favorites")
        self.language_label = add_row(form, "language", "Speak in", self.language_combo, "ctl.language", engine=True)
        self.sample_player = MiniPlayer(self)
        self.sample_player.set_note("Press Listen to hear this voice. Samples are saved, so the next time is instant.")
        vl.addWidget(self.sample_player)
        self.gate("ctl.preview", self.preview_button, self.sample_player)
        self.gate("ctl.favorites", self.star_button)
        right.addWidget(voice_card)

        self.speed_card, spl = card("Speed")
        qform = new_form(104)
        spl.addLayout(qform)
        self.speed_combo = QComboBox()
        for text, value in (("Fast · bigger groups, graphics card", "fast"), ("Balanced", "balanced"), ("Best quality · smaller groups", "quality")):
            self.speed_combo.addItem(text, value)
        self.speed_combo.setCurrentIndex(max(0, self.speed_combo.findData(self.store.setting("studio_speed", "balanced"))))
        self.speed_combo.setToolTip("One choice that sets the group size, parts at once and the graphics card use for the best speed.")
        self.speed_combo.currentIndexChanged.connect(self.speed_changed)
        self.run_on_combo = QComboBox()
        has_card = bool(load_control.gpu_profile())
        has_apple = bool(load_control.apple_profile())
        choices = [("Automatic · best for my Mac" if sys.platform == "darwin" else "Automatic · best for my PC", "auto")]
        if has_card:
            choices += [("Graphics card + processor together", "hybrid"), ("Graphics card only", "cuda")]
        if has_apple:
            choices.append(("Apple GPU (Metal)", "mps"))
        choices.append(("Processor only", "cpu"))
        for text, value in choices:
            self.run_on_combo.addItem(text, value)
        self.run_on_combo.setCurrentIndex(max(0, self.run_on_combo.findData(self.store.setting("compute_mode", "auto"))))
        self.run_on_combo.currentIndexChanged.connect(self.run_on_changed)
        qform.addRow(field("Speed"), self.speed_combo)
        qform.addRow(field("Run on"), self.run_on_combo)
        self.speed_note = label("", "muted")
        spl.addWidget(self.speed_note)
        self.sync_speed_note()
        right.addWidget(self.speed_card)

        settings_card, tl = card("Settings")
        sform = new_form(104)
        tl.addLayout(sform)
        self.output_speed.setRange(0.5, 2.0)
        self.output_speed.setSingleStep(0.05)
        self.pitch.setRange(-12.0, 12.0)
        self.pitch.setSingleStep(0.5)
        self.slider_rows, self.state_widgets = {}, {}

        def slide(key, spin, scale, left="", right=""):
            row = SliderRow(spin, left, right, scale)
            self.slider_rows[key] = row
            if key not in ("output_speed", "pitch_semitones"):
                self.state_widgets[key] = spin
            return row

        speed_row = slide("output_speed", self.output_speed, 100, "Slower", "Faster")
        pitch_row = slide("pitch_semitones", self.pitch, 20, "Lower", "Higher")
        for key, text, widget, gate_key in (
                ("output_speed", "Speed", speed_row, "ctl.speed"),
                ("pitch_semitones", "Pitch", pitch_row, "ctl.pitch"),
                ("temperature", "Variation", slide("temperature", self.temperature, 100), "ctl.style"),
                ("guidance", "Voice match", slide("guidance", self.guidance, 10), "ctl.style"),
                ("exaggeration", "Expressiveness", slide("exaggeration", expression, 100), "ctl.style"),
                ("cfg_weight", "Pacing guide", slide("cfg_weight", pacing, 100), "ctl.style"),
                ("emotion_weight", "Emotion strength", slide("emotion_weight", self.emotion_weight, 100), "ctl.style"),
                ("emotion_text", "Emotion", self.emotion_text, "ctl.style"),
                ("emotion_preset", "Emotion style", self.emotion_preset, "ctl.emotion"),
                ("style_alpha", "Voice likeness", slide("style_alpha", self.style_alpha, 100), "ctl.style"),
                ("style_beta", "Delivery likeness", slide("style_beta", self.style_beta, 100), "ctl.style"),
                ("diffusion_steps", "Variety steps", slide("diffusion_steps", self.diffusion_steps, 1), "ctl.style"),
                ("embedding_scale", "Expressiveness+", slide("embedding_scale", self.embedding_scale, 20), "ctl.style")):
            add_row(sform, key, text, widget, gate_key, engine=True)
        self.pron_combo = QComboBox()
        self.pron_combo.addItem("None", False)
        self.pron_combo.addItem("My list", True)
        manage = QPushButton("Manage")
        manage.clicked.connect(self.edit_pronunciations)
        pron_box = QWidget()
        pb = QHBoxLayout(pron_box)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(8)
        pb.addWidget(self.pron_combo, 1)
        pb.addWidget(manage)
        add_row(sform, "pron", "Pronounce", pron_box, None)
        self.pron_combo.currentIndexChanged.connect(lambda _i: self.store.set_setting("pronounce_on", bool(self.pron_combo.currentData())))
        self.refresh_pron_combo()
        tags_box = QWidget()
        tg = QHBoxLayout(tags_box)
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setSpacing(8)
        self.pause_button = QPushButton("Pause ▾")
        self.pause_button.setToolTip("Insert a silent pause into the script at the cursor.")
        menu = QMenu(self.pause_button)
        for text, seconds in (("0.5 seconds", "0.5"), ("1 second", "1"), ("2 seconds", "2"), ("3 seconds", "3"), ("5 seconds", "5")):
            menu.addAction(text, lambda sec=seconds: self.insert_tag(f"[pause {sec}s]"))
        self.pause_button.setMenu(menu)
        self.expr_button = QPushButton("Expression ▾")
        self.expr_button.setToolTip("Insert a sound the voice acts out. Chatterbox Turbo only.")
        emenu = QMenu(self.expr_button)
        for tag in ("[laugh]", "[chuckle]", "[cough]"):
            emenu.addAction(tag, lambda t=tag: self.insert_tag(t))
        self.expr_button.setMenu(emenu)
        tg.addWidget(self.pause_button)
        tg.addWidget(self.expr_button)
        tg.addStretch(1)
        add_row(sform, "tags", "Insert tag", tags_box, None)
        reset = QPushButton("↺  Reset values")
        reset.setObjectName("ghost")
        reset.clicked.connect(self.reset_values)
        tl.addWidget(reset, 0, Qt.AlignmentFlag.AlignRight)
        right.addWidget(settings_card)

        polish_card, pl = card("Polish")
        pform = new_form(104)
        pl.addLayout(pform)
        add_row(pform, "pause", "Pause", SliderRow(self.pause, "", "", 0.02), "ctl.polish")
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(25, 200)
        self.volume_spin.setSuffix(" %")
        self.volume_spin.setValue(100)
        self.volume_spin.setToolTip("Loudness of the finished audio. 100 % keeps it unchanged. Above 100 % is limited so it cannot distort.")
        add_row(pform, "volume", "Volume", SliderRow(self.volume_spin, "Quieter", "Louder", 1), "ctl.polish")
        self.chk_norm = QCheckBox("Even volume")
        self.chk_trim = QCheckBox("Trim silences")
        self.chk_fade = QCheckBox("Fade in/out")
        self.transcript_check = QCheckBox("Save transcript (.txt)")
        self.transcript_check.setToolTip("Saves the script as a text file next to the audio.")
        self.mp3_check = QCheckBox("Also save MP3")
        self.mp3_check.setToolTip("Saves an MP3 copy next to the WAV when the audio is finished.")
        for box, key in ((self.chk_norm, "pol_norm"), (self.chk_trim, "pol_trim"), (self.chk_fade, "pol_fade"), (self.mp3_check, "pol_mp3"), (self.transcript_check, "pol_transcript")):
            box.setChecked(bool(self.store.setting(key, False)))
            box.toggled.connect(lambda on, k=key: self.store.set_setting(k, bool(on)))
            pl.addWidget(box)
        self.gate("ctl.polish", polish_card)
        right.addWidget(polish_card)
        right.addStretch(1)
        for frame_, key_, open_ in ((voice_card, "voice", True), (self.speed_card, "speed", True),
                                    (settings_card, "settings", False), (polish_card, "polish", False)):
            make_collapsible(frame_, self.store, "studio_" + key_, open_)

        page_widget = QWidget()
        page_widget.setObjectName("page")
        outer = QHBoxLayout(page_widget)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(16)
        outer.addLayout(left, 1)
        right_box = QWidget()
        right_box.setFixedWidth(450)
        settings_page = QWidget()
        settings_page.setLayout(right)
        self.panel_stack = QStackedWidget()
        self.panel_stack.addWidget(settings_page)
        self.panel_stack.addWidget(self.build_history_page())
        outer_right = QVBoxLayout()
        outer_right.setContentsMargins(0, 0, 0, 0)
        outer_right.setSpacing(12)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.panel_tabs = []
        for index, text in enumerate(("⚙  Settings", "🕘  History")):
            tab = QPushButton(text)
            tab.setObjectName("tab")
            tab.setCheckable(True)
            tab.clicked.connect(lambda _=False, i=index: self.show_panel(i))
            tabs.addWidget(tab)
            self.panel_tabs.append(tab)
        tabs.addStretch(1)
        self.history_tools = QWidget()
        ht = QHBoxLayout(self.history_tools)
        ht.setContentsMargins(0, 0, 0, 0)
        ht.setSpacing(8)
        self.autoplay_check = QCheckBox("Autoplay")
        self.autoplay_check.setToolTip("Play a voice-over as soon as it is finished.")
        self.autoplay_check.setChecked(bool(self.store.setting("history_autoplay", True)))
        self.autoplay_check.toggled.connect(lambda on: self.store.set_setting("history_autoplay", bool(on)))
        refresh = QPushButton("⟳")
        refresh.setObjectName("icon")
        refresh.setFixedSize(36, 36)
        refresh.setToolTip("Refresh the list")
        refresh.clicked.connect(self.refresh_history)
        ht.addWidget(self.autoplay_check)
        ht.addWidget(refresh)
        tabs.addWidget(self.history_tools)
        outer_right.addLayout(tabs)
        outer_right.addWidget(self.panel_stack, 1)
        right_box.setLayout(outer_right)
        outer.addWidget(right_box, 0)
        self.show_panel(0)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setWidget(page_widget)
        self.stack.removeWidget(old)
        old.setParent(self._holder)
        self.stack.insertWidget(0, scroller)

        self.voice_combo.currentIndexChanged.connect(self.sync_star)
        self.model_combo.currentIndexChanged.connect(self.sync_star)
        self.model_combo.currentIndexChanged.connect(self.sync_speed_note)
        self.lock_widgets = [self.model_combo, self.voice_combo, self.segment_mode, self.group_combo, self.language_combo,
                             self.gender_combo, self.vlang_combo, self.speed_combo, self.run_on_combo, self.fav_combo,
                             *self.type_buttons.values()]
        self.refresh_voices()

    # ---- speed presets, locking while a voice-over is made
    SPEED_PRESETS = {
        "fast": dict(group_size=4, rest_ms=0, retry_parts=1, keep_model_warm=True, fast_precision=True, parts="auto"),
        "balanced": dict(group_size=3, rest_ms=0, retry_parts=1, keep_model_warm=True, fast_precision=False, parts="auto"),
        "quality": dict(group_size=2, rest_ms=0, retry_parts=2, keep_model_warm=True, fast_precision=False, parts=1),
    }

    def speed_changed(self, *_):
        name = self.speed_combo.currentData()
        preset = self.SPEED_PRESETS.get(name, self.SPEED_PRESETS["balanced"])
        gpu = load_control.detect_hardware()["gpu"]
        self.store.set_setting("studio_speed", name)
        for key in ("group_size", "rest_ms", "retry_parts", "keep_model_warm"):
            self.store.set_setting(key, preset[key])
        self.store.set_setting("fast_precision", bool(preset["fast_precision"] and gpu))
        self.store.set_setting("parts_at_once", preset["parts"] if (preset["parts"] != "auto" or gpu) else 1)
        for combo, key, default in ((self.group_combo, "group_size", 3), (getattr(self, "parts_combo", None), "parts_at_once", 1),
                                    (getattr(self, "rest_combo", None), "rest_ms", 0), (getattr(self, "retry_combo", None), "retry_parts", 1)):
            if combo is not None:
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(self.store.setting(key, default))))
                combo.blockSignals(False)
        for box, key in ((getattr(self, "fast_check", None), "fast_precision"), (getattr(self, "warm_check", None), "keep_model_warm")):
            if box is not None:
                box.blockSignals(True)
                box.setChecked(bool(self.store.setting(key, False)))
                box.blockSignals(False)
        self.stats_timer.start()
        self.sync_speed_note()

    def run_on_changed(self, *_):
        mode = self.run_on_combo.currentData()
        self.store.set_setting("compute_mode", mode)
        self.store.set_setting("compute_cpu_temp", False)
        if hasattr(self, "compute"):
            self.compute.blockSignals(True)
            self.compute.setCurrentIndex(max(0, self.compute.findData(mode)))
            self.compute.blockSignals(False)
        self.sync_speed_note()

    def sync_speed_note(self):
        hw = load_control.detect_hardware()
        names = load_control._gpu_names or []
        model = self.model_combo.currentData() if hasattr(self, "model_combo") else None
        entry = models().get(model) or {}
        mode = self.store.setting("compute_mode", "auto")
        card = load_control.gpu_profile()
        apple = load_control.apple_profile()
        if apple and entry.get("torch"):
            if mode == "cpu":
                where = "Using the processor only. Choose Automatic to let the Apple graphics help."
            elif mode == "mps":
                where = (f"Using the Apple graphics (Metal) of {apple['name']}." if load_control.gpu_fits(model, entry)
                         else "This model is too big for the Apple graphics here or has no Metal path yet, so the processor does the work.")
            else:
                where = load_control.plan(model, entry)["reason"]
        elif not entry.get("torch") and model:
            where = "This voice model is built for the processor and runs there."
        elif not card:
            where = (f"{names[0]} cannot run these voice engines (they need an NVIDIA card), so the processor does the work." if names
                     else "No NVIDIA graphics card found: the processor does the work.")
        elif mode == "cpu":
            where = "Using the processor only. Choose Automatic to let the graphics card help."
        elif mode in ("cuda", "hybrid") and model and not load_control.gpu_fits(model, entry):
            where = f"This model is too big for {card['name']} ({card['vram_gb']:.0f} GB), so the processor does the work."
        elif mode == "cuda":
            where = f"Using {card['name']} ({load_control.gpu_precision(model)} precision)."
        elif mode == "hybrid":
            where = f"{card['name']} and the processor share the parts; whichever is free takes the next one."
        else:
            where = load_control.plan(model, entry)["reason"] if model else "Automatic picks the best way for this PC."
        extra = ""
        if model and entry:
            from . import recommend
            r = recommend.rate(model, entry)
            if not r["download_only"]:
                extra = f" {r['text']}."
                rtf = recommend.measured_rtf(model)
                text_box = getattr(self, "script", None)
                words = len(text_box.toPlainText().split()) if text_box is not None else 0
                if rtf and words:
                    extra += f" Your last runs took {rtf:g} s for every second of audio, so about {recommend.duration_text(words / 150 * 60 * rtf)} for this script."
                if r["tier"] in ("slow", "very slow"):
                    extra += " For a quicker voice-over choose a model marked ★."
        self.speed_note.setText(where + extra + " Everything else is in Settings > Performance.")

    def sync_studio_lock(self):
        """While a voice-over is being made its model and mode cannot change. After the next text was added to the queue they are free again."""
        job = self.studio_job
        active = bool(job) and (job in self.running or job in getattr(self, "pending_jobs", []))
        if not active:
            self._lock_released = False
        locked = active and not self._lock_released
        if locked == self.studio_locked:
            return
        self.studio_locked = locked
        for widget in self.lock_widgets:
            widget.setEnabled(not locked)
        self.lock_note.setVisible(locked)
        if not locked:
            self.apply_access()          # restores owner locks that were set before

    # ---- keep rows tidy: engine rules + owner rules decide what shows
    def refresh_voices(self, *args):
        super().refresh_voices(*args)
        if self.rows:
            self.pause.setVisible(True)   # the older code hides it on every refresh; it is a normal control now
            self.sync_engine_controls()
            self.apply_voice_filters()
            self.engine_visible = {k: not self.state_widgets.get(k, r[1]).isHidden() for k, r in self.rows.items() if r[3]}
            self.sync_studio_controls()
            self.sync_star()

    def install_runtime(self):
        if "runtime" in self.tasks or self.running or self.busy_installer:
            return
        self.start_task("runtime", None, lambda c, p, l: managed.ensure_runtime_and_tools(l, c), "Preparing Python and C++ tools")

    def set_auto_resolve(self, on):
        self.store.set_setting("auto_resolve", bool(on))
        os.environ["AVS_AUTO_RESOLVE"] = "1" if on else "0"

    def repair_engines(self):
        """Try the full package set again for every engine that is not ready, then check everything."""
        if self.busy_installer or self.move_pending or self.tasks:
            return self.alert("Finish the current download or update first.")
        managed.clear_reduced()
        keys = [k for k in getattr(self, "setup_cards", {}) if not capabilities.unavailable_reason(models()[k])
                and managed.engine_python(k).is_file() and not self.engine_ready(k)]
        if not keys:
            self.toast("Checking everything…")
            return self.run_health_check()
        for key in keys:
            self.install_card(key)
        if self.access.state("page.setup") == SHOW:
            self.change_page(3)

    def install_build_tools(self):
        if os.name == "nt" and not self.confirm("Install build tools", "This installs CMake and the Microsoft C++ build tools (several GB) "
                                                "and Windows asks for permission. Continue?"):
            return
        self.toast("Installing build tools… this can take a long time.")

        def work():
            self.build_tools_done.emit(autoresolve.install_build_tools(self.log_message))

        threading.Thread(target=work, daemon=True).start()

    def on_build_tools_done(self, result):
        ok, message = result
        self.toast(message) if ok else self.alert(message)

    def apply_voice_filters(self):
        """Gender and language narrow the built-in list; every saved clone voice works with every cloning engine."""
        model = self.model_combo.currentData()
        entry = models().get(model)
        if not entry:
            return
        voices = entry.get("voices", [])
        kind = entry["kind"]
        blocked = {v["id"] for v in voices if capabilities.voice_unavailable(model, v)} if kind == "tts" else set()
        if blocked:
            self.voice_combo.blockSignals(True)
            for i in reversed(range(self.voice_combo.count())):
                if self.voice_combo.itemData(i) in blocked:
                    self.voice_combo.removeItem(i)
            if self.voice_combo.count() == 0:
                self.voice_combo.addItem("No voice available", "")
            self.voice_combo.blockSignals(False)
        has_gender = any(v.get("gender") for v in voices)
        families = sorted({favorites.language_family(v.get("language", "")) for v in voices if v.get("language")})
        key = (model, tuple(families), has_gender)
        if self._filter_key != key:
            self._filter_key = key
            for combo in (self.gender_combo, self.vlang_combo):
                combo.blockSignals(True)
            keep = self.vlang_combo.currentData()
            self.vlang_combo.clear()
            self.vlang_combo.addItem("Any language", "")
            for family in families:
                self.vlang_combo.addItem(family, family)
            self.vlang_combo.setCurrentIndex(max(0, self.vlang_combo.findData(keep)))
            if not has_gender:
                self.gender_combo.setCurrentIndex(0)
            for combo in (self.gender_combo, self.vlang_combo):
                combo.blockSignals(False)
        self.gender_combo.setVisible(kind == "tts" and has_gender)
        self.vlang_combo.setVisible(kind == "tts" and len(families) > 1)
        old = self.voice_combo.currentData()
        if kind == "tts":
            gender = self.gender_combo.currentData() if has_gender else ""
            family = self.vlang_combo.currentData() if len(families) > 1 else ""
            if not (gender or family):
                return
            self.voice_combo.blockSignals(True)
            for i in reversed(range(self.voice_combo.count())):
                found = next((x for x in voices if x["id"] == self.voice_combo.itemData(i)), None)
                if found is None:
                    continue
                if (gender and found.get("gender", "").lower() != gender.lower()) or (family and favorites.language_family(found.get("language", "")) != family):
                    self.voice_combo.removeItem(i)
            if self.voice_combo.count() == 0:
                self.voice_combo.addItem("No voice matches these choices", "")
            self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(old)))
            self.voice_combo.blockSignals(False)
            self.populate_profile_options()
        elif kind == "clone":
            query = self.voice_search.text().strip().lower()
            saved = self.store.setting("voice_" + model, old)
            if any(p["id"] == old for p in clones.profiles(self.store)):
                saved = old          # the cloned voice that is selected stays selected when the model changes: no new clone needed
            self.voice_combo.blockSignals(True)
            self.voice_combo.clear()
            for profile in clones.profiles(self.store):
                if query in profile["name"].lower():
                    made_with = models().get(profile["model"], {}).get("name", "").split(" · ")[0]
                    self.voice_combo.addItem(profile["name"] + (f"  ·  made with {made_with}" if profile["model"] != model and made_with else ""), profile["id"])
            if not self.voice_combo.count():
                self.voice_combo.addItem("Create a voice in Voice Clone", "")
            self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(saved)))
            self.voice_combo.blockSignals(False)
            self.populate_profile_options()

    def ask_transcript(self, profile) -> bool:
        """One-time question: what words are spoken in the saved sample? The answer is kept with the voice."""
        if os.environ.get("AVS_NO_PROMPTS"):
            return False
        d = QDialog(self)
        d.setWindowTitle(f"Words spoken in “{profile['name']}”")
        d.resize(560, 360)
        lay = QVBoxLayout(d)
        lay.addWidget(label("Some models learn a voice from the sample AND the words spoken in it. Type those words once. "
                            "They are saved with your voice, so you never have to do this again for any model.", "muted"))
        player = MiniPlayer(self)
        player.load(profile["reference"])
        lay.addWidget(player)
        box = QPlainTextEdit(profile.get("transcript", ""))
        box.setPlaceholderText("Type exactly what is said in the sample, word for word.")
        lay.addWidget(box, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        lay.addWidget(buttons)
        saved = False
        if d.exec():
            try:
                clones.set_transcript(self.store, profile["id"], box.toPlainText())
                saved = True
                self.refresh_library()
            except ValueError as exc:
                QMessageBox.warning(self, "Words spoken", str(exc))
        player.player.pause()
        return saved

    def populate_profile_options(self, *args):
        """Keep the person's last settings when a voice changes; a saved clone voice may still bring its own defaults."""
        if not hasattr(self, "volume_spin"):
            return super().populate_profile_options(*args)
        names = {"s_speed": "output_speed", "s_pitch": "pitch_semitones", "s_temperature": "temperature", "s_guidance": "guidance",
                 "s_emotion_weight": "emotion_weight", "s_exaggeration": "exaggeration", "s_cfg": "cfg_weight", "s_emotion_text": "emotion_text"}
        keep = {k: (w.text() if isinstance(w, QLineEdit) else w.value()) for k, w in self.persisted_widgets() if k in names}
        super().populate_profile_options(*args)
        entry = models().get(self.model_combo.currentData(), {})
        profile = self.profile(self.voice_combo.currentData()) if entry.get("kind") == "clone" else None
        own = (profile or {}).get("options", {})
        for key, widget in self.persisted_widgets():
            if key in names and names[key] not in own:
                if isinstance(widget, QLineEdit):
                    widget.setText(keep[key])
                else:
                    widget.setValue(keep[key])

    def resolve_voice(self, model, voice):
        entry = models()[model]
        if entry["kind"] == "clone":
            profile = self.profile(voice)
            if not profile:
                raise ValueError("Create and select a saved cloned voice first.")
            if "ref_text" in entry["supports"] and not profile["transcript"].strip():
                # Nobody is asked for text: two default lines are saved with the voice and every model uses them.
                try:
                    clones.set_transcript(self.store, profile["id"], clones.DEFAULT_TRANSCRIPT)
                except ValueError:
                    pass
                profile = self.profile(voice) or {**profile, "transcript": clones.DEFAULT_TRANSCRIPT}
            return "", profile["reference"], clones.transcript_of(profile), profile
        return super().resolve_voice(model, voice)

    def sync_engine_controls(self):
        """Show only what this engine accepts, keep every value inside its safe range, fill language and emotion lists."""
        model = self.model_combo.currentData()
        entry = models().get(model)
        if not entry:
            return
        supports = set(entry.get("supports", []))
        low = bool(self.store.setting("low_end_mode", False))
        for key in ("style_alpha", "style_beta", "diffusion_steps", "embedding_scale"):
            getattr(self, key).setVisible(key in supports)
        widgets = {"output_speed": self.output_speed, "pitch_semitones": self.pitch, "temperature": self.temperature, "guidance": self.guidance,
                   "emotion_weight": self.emotion_weight, "exaggeration": self.opt_widgets["exaggeration"][1],
                   "cfg_weight": self.opt_widgets["cfg_weight"][1], "style_alpha": self.style_alpha, "style_beta": self.style_beta,
                   "diffusion_steps": self.diffusion_steps, "embedding_scale": self.embedding_scale}
        for key, widget in widgets.items():
            spec = capabilities.limits(model, key, low)
            if spec:
                widget.blockSignals(True)
                widget.setRange(spec[0], spec[1])
                widget.blockSignals(False)
                if key in self.slider_rows:
                    self.slider_rows[key].refresh_range()
                if key in ("style_alpha", "style_beta", "diffusion_steps", "embedding_scale") and not getattr(widget, "_avs_init", False):
                    widget.setValue(spec[2])
                    widget._avs_init = True
        # language list
        want = entry["languages"] if entry.get("lang_option") else (list(QWEN_LANG) if model.startswith("qwen") else None)
        if want is not None:
            key = tuple(want)
            if getattr(self, "_lang_key", None) != key:
                self.language_combo.blockSignals(True)
                self.language_combo.clear()
                for language in want:
                    self.language_combo.addItem(language, language)
                saved = self.store.setting("qwen_language" if model.startswith("qwen") else "lang_" + model, want[0])
                self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(saved)))
                self.language_combo.blockSignals(False)
                self._lang_key = key
            self.language_combo.setVisible(True)
            self.language_label.setVisible(True)
            if not model.startswith("qwen"):
                try:
                    self.language_combo.currentIndexChanged.disconnect(self._save_lang)
                except (RuntimeError, TypeError, AttributeError):
                    pass
                self._save_lang = lambda _i, m=model: self.store.set_setting("lang_" + m, self.language_combo.currentData())
                self.language_combo.currentIndexChanged.connect(self._save_lang)
        else:
            self.language_combo.setVisible(False)
            self.language_label.setVisible(False)
        # emotion styles
        presets = emotions.available(entry)
        self.emotion_preset.blockSignals(True)
        self.emotion_preset.clear()
        for name in presets:
            self.emotion_preset.addItem(emotions.LABELS[name], name)
        self.emotion_preset.blockSignals(False)
        self.emotion_preset.setVisible(bool(presets))

    def emotion_chosen(self, index):
        entry = models().get(self.model_combo.currentData())
        if not entry:
            return
        for key, value in emotions.values(entry, self.emotion_preset.itemData(index)).items():
            if key == "emotion_text":
                self.emotion_text.setText(value)
            elif key == "exaggeration":
                self.opt_widgets["exaggeration"][1].setValue(value)
            elif key == "cfg_weight":
                self.opt_widgets["cfg_weight"][1].setValue(value)

    def filter_models(self, *_):
        previous = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        from . import recommend
        stars = recommend.recommended(models())
        for m in models().values():
            if m["kind"] == self.voice_type.currentData() and not capabilities.unavailable_reason(m):
                self.model_combo.addItem(m["name"] + recommend.suffix(m["id"], m, stars), m["id"])
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(previous)))
        self.model_combo.blockSignals(False)
        self.refresh_voices()

    def sync_studio_controls(self):
        if hasattr(self, "expr_button"):
            self.expr_button.setVisible(self.model_combo.currentData() == "turbo" and self.access.state("ctl.emotion") == SHOW)
        for key, (lab, widget, gate_key, engine, form) in self.rows.items():
            state = self.access.state(gate_key) if gate_key else SHOW
            wanted = self.engine_visible.get(key, True) if engine else True
            show = wanted and state != HIDE
            form.setRowVisible(widget, show)
            widget.setEnabled(state != LOCK)
            self.mark_locked(widget, state == LOCK)

    def sync_star(self, *_):
        if not hasattr(self, "star_button") or not self.model_combo.currentData():
            return
        model, voice = self.model_combo.currentData(), self.voice_combo.currentData()
        usable = bool(voice)
        fav = usable and favorites.is_favorite(self.store, model, voice)
        self.star_button.setEnabled(usable and self.access.state("ctl.favorites") == SHOW)
        self.star_button.setChecked(fav)
        self.star_button.setText("★" if fav else "☆")
        self.star_button.setToolTip("Remove from favourites" if fav else "Add this voice to your favourites")
        self.preview_button.setEnabled(usable and not self.preview_busy and self.access.state("ctl.preview") == SHOW)

    def current_voice_name(self):
        return self.voice_combo.currentText()

    def toggle_favorite(self):
        model, voice = self.model_combo.currentData(), self.voice_combo.currentData()
        if not voice:
            return
        favorites.toggle(self.store, model, voice, self.current_voice_name())
        self.sync_star()
        self.refresh_favorites()

    def refresh_favorites(self):
        if not hasattr(self, "fav_combo"):
            return
        self.fav_combo.blockSignals(True)
        self.fav_combo.clear()
        items = favorites.favorites(self.store)
        catalog = models()
        self.fav_combo.addItem("★ Choose a favourite…" if items else "No favourites yet · press ☆", None)
        for item in items:
            if item["model"] not in catalog:
                continue
            self.fav_combo.addItem(f"★ {item['name']}  ·  {catalog[item['model']]['name'].split(' · ')[0]}", (item["model"], item["voice"]))
        self.fav_combo.setCurrentIndex(0)
        self.fav_combo.blockSignals(False)

    def favorite_chosen(self, index):
        data = self.fav_combo.itemData(index)
        if data:
            self.select_voice(*data)
        self.fav_combo.setCurrentIndex(0)

    def select_voice(self, model, voice):
        entry = models().get(model)
        if not entry:
            return
        self.voice_search.clear()
        self.voice_type.setCurrentIndex(max(0, self.voice_type.findData(entry["kind"])))
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(model)))
        index = self.voice_combo.findData(voice)
        if index >= 0:
            self.voice_combo.setCurrentIndex(index)

    # ---- script information
    def chunk_setting(self) -> int:
        return int(self.store.setting("chunk_chars", 0) or 0)

    def sync_type_buttons(self, *_):
        kind = self.voice_type.currentData()
        for k, b in self.type_buttons.items():
            b.setChecked(k == kind)

    def show_panel(self, index):
        self.panel_stack.setCurrentIndex(index)
        for i, tab in enumerate(self.panel_tabs):
            tab.setChecked(i == index)
        self.history_tools.setVisible(index == 1)
        if index == 1:
            self.refresh_history()

    def insert_tag(self, tag):
        self.script.insertPlainText(f" {tag} ")
        self.script.setFocus()

    def refresh_pron_combo(self):
        count = len(scripttools.entries(self.store))
        self.pron_combo.blockSignals(True)
        self.pron_combo.setItemText(1, f"My list · {count} word{'s' if count != 1 else ''}")
        self.pron_combo.setCurrentIndex(1 if self.store.setting("pronounce_on", True) and count else 0)
        self.pron_combo.blockSignals(False)

    def reset_values(self):
        model = self.model_combo.currentData()
        pairs = (("output_speed", self.output_speed), ("pitch_semitones", self.pitch), ("temperature", self.temperature), ("guidance", self.guidance),
                 ("emotion_weight", self.emotion_weight), ("exaggeration", self.opt_widgets["exaggeration"][1]),
                 ("cfg_weight", self.opt_widgets["cfg_weight"][1]), ("style_alpha", self.style_alpha), ("style_beta", self.style_beta),
                 ("diffusion_steps", self.diffusion_steps), ("embedding_scale", self.embedding_scale), ("pause_ms", self.pause),
                 ("volume_pct", self.volume_spin))
        for key, widget in pairs:
            spec = capabilities.limits(model, key)
            if spec:
                widget.setValue(spec[2])
        self.emotion_text.clear()
        self.emotion_preset.setCurrentIndex(0)
        for box in (self.chk_norm, self.chk_trim, self.chk_fade, self.mp3_check, self.transcript_check):
            box.setChecked(False)

    def update_counts(self):
        raw = self.script.toPlainText()
        words, chars = scripttools.counts(strip_pause_tags(raw))
        pauses = len(PAUSE_TAG.findall(raw))
        extra = f" · {pauses} pause{'s' if pauses != 1 else ''}" if pauses else ""
        self.script_counts.setText(f"{words:,} words · {chars:,} characters{extra}" if chars else "0 words · 0 characters")

    def update_script_stats(self):
        self.update_counts()
        raw = self.script.toPlainText()
        text = strip_pause_tags(raw)
        if not text.strip():
            self.script_stats.setText("Paste a script or import a file.")
            return
        words = len(text.split())
        pause_seconds = sum(float(m.group(1)) for m in PAUSE_TAG.finditer(raw))
        seconds = int(words / (2.5 * (self.output_speed.value() or 1)) + pause_seconds)
        length = f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"
        mode = self.segment_mode.currentData() or "auto"
        model = self.model_combo.currentData() or ""
        parts, warning = 1, ""
        try:
            clean = text.replace("\r\n", "\n")
            if mode == "paragraph":
                parts = len([p for p in re.split(r"\n\s*\n", clean) if p.strip()])
            elif mode == "group":
                paras = [p for p in re.split(r"\n\s*\n", clean) if p.strip()]
                if len(paras) == 1:
                    paras = [line for line in clean.split("\n") if line.strip()]
                size = int(self.group_combo.currentData() or 3)
                parts = -(-len(paras) // size)
            elif mode == "sentence":
                parts = len(split_sentences(clean))
            elif mode in ("auto", "fast"):
                parts = len(safe_chunks([clean.strip()], model, self.auto_part_size(model, mode)))
            else:
                limit = capabilities.max_chars(model)
                if len(clean.strip()) > limit:
                    warning = f"  ⚠ This model reads about {limit} characters at once. The rest is split automatically."
        except Exception:
            pass
        if mode == "whole" and PAUSE_TAG.search(raw):
            parts, warning = len(PAUSE_TAG.findall(raw)) + 1, ""
        how = {"auto": f"in {parts} safe part{'s' if parts != 1 else ''}", "fast": f"in {parts} small part{'s' if parts != 1 else ''}", "whole": "in one go" if not PAUSE_TAG.search(raw) else f"in {parts} parts split at your pause tags", "paragraph": f"in {parts} paragraph{'s' if parts != 1 else ''}",
               "sentence": f"in {parts} sentence{'s' if parts != 1 else ''}",
               "group": f"in {parts} group{'s' if parts != 1 else ''} of {int(self.group_combo.currentData() or 3)} paragraphs"}[mode]
        self.script_stats.setText(f"About {length} of audio, made {how}, delivered as one audio.{warning}")

    def device_for(self, model_id):
        """'cuda' or 'cpu' for one model: the setting, or (Automatic) the graphics card only where it is really faster."""
        mode = self.store.setting("compute_mode", "auto")
        if mode == "cpu":
            return "cpu"
        if mode == "cuda":
            return "cuda" if managed.nvidia_smi() else "cpu"
        if mode == "mps":                       # the Apple graphics, only for engines that have a Metal path and fit; the processor takes over otherwise
            entry = models().get(model_id) or {}
            return "mps" if entry.get("torch") and load_control.gpu_fits(model_id, entry) else "cpu"
        if mode == "hybrid":                    # the card helps only when the model fits it; otherwise the processor does the work
            return "cuda" if load_control.gpu_fits(model_id, models().get(model_id)) else "cpu"
        return load_control.auto_device(model_id, models().get(model_id))

    def hybrid_for(self, model_id):
        """True when the graphics card and the processor should share the parts of one voice-over."""
        mode = self.store.setting("compute_mode", "auto")
        entry = models().get(model_id) or {}
        if mode == "hybrid":
            return load_control.gpu_fits(model_id, entry)
        if mode == "auto":
            return load_control.plan(model_id, entry)["mode"] == "hybrid"
        return False

    def gpu_options(self, model_id):
        return {"hybrid": self.hybrid_for(model_id), "gpu_precision": load_control.gpu_precision(model_id)}

    def auto_part_size(self, model_id, mode="auto"):
        explicit = self.chunk_setting()
        if explicit and mode == "auto":
            return explicit
        return load_control.auto_chunk_chars(model_id, self.device_for(model_id), mode == "fast", models().get(model_id)) or explicit

    def sync_group_combo(self, *_):
        self.group_combo.setVisible((self.segment_mode.currentData() or "auto") == "group")

    def mode_options(self):
        mode = self.segment_mode.currentData() or "auto"
        if mode == "fast":
            return {"segment_mode": "auto", "auto_chunk": True, "exact_parts": False, "fast_mode": True}
        out = {"segment_mode": mode, "auto_chunk": mode == "auto", "exact_parts": mode != "auto"}
        if mode == "group":
            out["group_size"] = int(self.group_combo.currentData() or 3)
        return out

    def clean_script(self):
        self.script.setPlainText(scripttools.clean(self.script.toPlainText()))

    def edit_pronunciations(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Pronounce")
        dialog.resize(560, 460)
        lay = QVBoxLayout(dialog)
        lay.addWidget(label("Pronunciations", "title"))
        lay.addWidget(label("Write a word as it appears in your script, and how the voice should say it. Example: GPU → G P U. Whole words only.", "muted"))
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Word in script", "Say it as"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        for item in scripttools.entries(self.store):
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(item["word"]))
            table.setItem(row, 1, QTableWidgetItem(item["say"]))
        lay.addWidget(table, 1)
        use = QCheckBox("Apply these when generating")
        use.setChecked(bool(self.store.setting("pronounce_on", True)))
        lay.addWidget(use)
        row_buttons = QHBoxLayout()
        add = QPushButton("Add row")
        add.clicked.connect(lambda: table.insertRow(table.rowCount()))
        remove = QPushButton("Remove row")
        remove.clicked.connect(lambda: table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None)
        row_buttons.addWidget(add)
        row_buttons.addWidget(remove)
        row_buttons.addStretch(1)
        lay.addLayout(row_buttons)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        lay.addWidget(box)
        if dialog.exec():
            items = []
            for r in range(table.rowCount()):
                word, say = table.item(r, 0), table.item(r, 1)
                items.append({"word": word.text() if word else "", "say": say.text() if say else ""})
            scripttools.save_entries(self.store, items)
            self.store.set_setting("pronounce_on", use.isChecked())
            self.refresh_pron_combo()

    @contextmanager
    def pronunciation_applied(self):
        """While a job is created, spoken-form replacements are applied to the script text."""
        items = scripttools.entries(self.store) if self.store.setting("pronounce_on", True) else []
        if not items:
            yield
            return
        original = self.store.new_job
        self.store.new_job = lambda title, script, *a, **k: original(title, scripttools.apply(script, items), *a, **k)
        try:
            yield
        finally:
            self.store.__dict__.pop("new_job", None)

    def add_script(self):
        before = self.studio_job
        text_before = self.script.toPlainText()
        with self.pronunciation_applied():
            super().add_script()
        if self.studio_job and self.studio_job != before:
            # The voice-over is in the Queue and is being made there. The Studio is empty again for the next text.
            self.studio_job = None
            self.script.clear()
            self.word_check_box.setChecked(False)           # word check is asked for again every time
            self.studio_progress.setValue(0)
            self.studio_status.setText("Ready for the next text")
            self.generate_button.setText("Generate voice over")
            self.generate_button.setEnabled(True)
            self.update_queue_hint(just_added=True)
            self._last_added_text = text_before
        self.sync_studio_lock()

    def update_queue_hint(self, just_added=False):
        """One line under the Generate button: what the Queue is doing now."""
        try:
            if not hasattr(self, "queue_hint"):
                return
            running = len(self.running)
            waiting = len(getattr(self, "pending_jobs", []))
            if not running and not waiting:
                self.queue_hint.setText("Added to the Queue. It starts now. See the Queue page." if just_added else "")
                return
            now = ""
            if running:
                jid = next(iter(self.running))
                effective, total, done = self.job_progress(jid)
                now = f"{done} / {total} parts · {int(100 * effective / max(1, total))}%"
            self.queue_hint.setText(("Added to the Queue. " if just_added else "") + f"Queue: {running} making ({now})" + (f" · {waiting} waiting" if waiting else "") + "  — see the Queue page")
        except Exception:
            pass

    def word_check_toggled(self, on):
        if on and not wordcheck.installed():
            self.word_check_box.blockSignals(True)
            self.word_check_box.setChecked(False)
            self.word_check_box.blockSignals(False)
            self.alert("The Word checker is not installed yet.\n\nOpen Models, find 'Word checker' and press Download. After that you can tick this box.")

    def options(self):
        out = super().options()
        model_id = self.model_combo.currentData()
        entry = models()[model_id]
        out["device"] = self.device_for(model_id) if entry.get("torch") else out.get("device", "cpu")
        out["device_auto"] = self.store.setting("compute_mode", "auto") == "auto"
        out.update(self.gpu_options(model_id) if entry.get("torch") else {"hybrid": False})
        supports = set(entry.get("supports", []))
        for key in ("style_alpha", "style_beta", "diffusion_steps", "embedding_scale"):
            if key in supports:
                out[key] = getattr(self, key).value()
        if entry.get("lang_option") and not self.model_combo.currentData().startswith("qwen"):
            out["language"] = self.language_combo.currentData()
        out["low_end"] = bool(self.store.setting("low_end_mode", False))
        out["fast_precision"] = bool(self.store.setting("fast_precision", False))
        out["volume_pct"] = self.volume_spin.value()
        out.update(self.mode_options())
        out["auto_mp3"] = self.mp3_check.isChecked()
        out["export_transcript"] = self.transcript_check.isChecked()
        out["pause_ms"] = self.pause.value()
        out["normalize"] = self.chk_norm.isChecked()
        out["trim_silence"] = self.chk_trim.isChecked()
        out["fade_ms"] = 400 if self.chk_fade.isChecked() else 0
        chunk = self.chunk_setting()
        mode_now = self.segment_mode.currentData() or "auto"
        if mode_now == "fast" or (mode_now == "auto" and not chunk):
            chunk = self.auto_part_size(model_id, mode_now)
        if chunk:
            out["chunk_chars"] = chunk
        out["word_check"] = bool(getattr(self, "word_check_box", None) and self.word_check_box.isChecked())
        return out

    # ------------------------------------------------------------------------- voice samples
    def start_preview(self, model, voice, language="", target="studio"):
        if self.preview_busy:
            return
        if self.busy_installer or self.move_pending:
            self.set_preview_note("Finish the current update first.", target)
            return
        try:
            v, reference, ref_text, profile = self.resolve_voice(model, voice)
        except ValueError as exc:
            self.set_preview_note(str(exc), target)
            return
        if target == "studio" and model == self.model_combo.currentData():
            opts = self.options()
        else:
            opts = {"output_speed": 1.0, "pitch_semitones": 0.0, **((profile or {}).get("options", {}))}
            if model.startswith("qwen"):
                opts["language"] = "Auto"
        device = self.preferred_device()
        self.preview_busy = True
        self.set_preview_busy(True)
        self.set_preview_note("Preparing the sample… the first time takes a little while.", target)

        def work():
            try:
                path = preview.make_preview(self.store, model, v, reference, ref_text, opts, language=language, device=device)
                self.preview_ready.emit(str(path), target)
            except Exception as exc:
                self.preview_failed.emit(str(exc) or exc.__class__.__name__, target)

        threading.Thread(target=work, daemon=True).start()

    def preview_current(self):
        model, voice = self.model_combo.currentData(), self.voice_combo.currentData()
        language = ""
        entry = models().get(model, {})
        for item in entry.get("voices", []):
            if item["id"] == voice:
                language = item.get("language", "")
        if model.startswith("qwen"):
            language = self.language_combo.currentData() or ""
        if voice or entry.get("kind") == "clone":
            self.start_preview(model, voice, language, "studio")

    def set_preview_note(self, text, target):
        {"library": self.library_player, "clone": getattr(self, "clone_player", self.sample_player)}.get(target, self.sample_player).set_note(text)

    def set_preview_busy(self, busy):
        self.preview_button.setText("Preparing…" if busy else "▶ Listen")
        self.preview_button.setEnabled(not busy)
        for b in self.lib_listen_buttons:
            try:
                b.setEnabled(not busy)
            except RuntimeError:
                pass

    def on_preview_ready(self, path, target):
        self.preview_busy = False
        self.set_preview_busy(False)
        self.sync_star()
        if target == "design":
            self.design_button.setEnabled(True)
            self.design_sample_path = path
            self.design_status.setText("Sample ready. Happy with it? Name it below and save it as a reusable voice.")
            self.design_player.load(path, play=True)
            return
        if target == "library" and self._library_row is not None:
            try:
                samples.save(Path(path), samples.row_path(self._library_row))
            except Exception:
                pass
            self._library_row = None
        if target == "clone" and self._clone_sample_id:
            try:
                samples.save(Path(path), samples.clone_path(self._clone_sample_id))
            except Exception:
                pass
            self._clone_sample_id = None
        if target == "clone":
            self.clone_player.load(path, play=True)
            self.clone_player.set_note("This is your clone speaking. If it sounds right, press Use.")
            return
        player = self.library_player if target == "library" else self.sample_player
        player.load(path, play=True)

    def on_preview_failed(self, message, target):
        self.preview_busy = False
        self.set_preview_busy(False)
        self.sync_star()
        text = message.splitlines()[0][:300] if message else "The sample could not be made."
        if target == "clone":
            self.clone_note.setText("Saved. Could not make the sample now: " + text)
            return
        if target == "design":
            self.design_button.setEnabled(True)
            self.design_status.setText(text + (" Open AI Setup, download 'Qwen3-TTS 1.7B VoiceDesign', then try again."
                                               if "not set up" in text or "not downloaded" in text else ""))
            return
        self.set_preview_note(text, target)

    # ------------------------------------------------------------------------- Voice Library page
    def make_logs(self):
        super().make_logs()          # Activity Logs is page 5
        self.make_voice_library()    # Voice Library is page 6
        self.make_design_page()      # 7
        self.make_training_page()    # 8
        self.make_sources_page()     # 9
        self.make_channels_page()    # 10

    def engine_ready(self, model_id):
        try:
            if not managed.engine_python(model_id).is_file():
                return False
            return True if model_id == "piper" else bool(managed.downloaded(model_id, ""))
        except Exception:
            return False

    def fill_filter(self, combo, first, items):
        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(first, "")
        for text, data in items:
            combo.addItem(text, data)
        combo.setCurrentIndex(max(0, combo.findData(keep)))
        combo.blockSignals(False)

    def library_toggle(self, row, *_):
        favorites.toggle(self.store, row["model"], row["voice"], row["name"])
        self.refresh_voice_library()
        self.refresh_favorites()
        self.sync_star()

    def library_listen(self, row, *_):
        """A saved sample plays at once. Only when there is none yet is it made (and then kept for next time)."""
        saved = samples.row_path(row)
        if samples.has(saved):
            self.library_player.load(saved, play=True)
            self.library_player.set_note(row["name"].split(" · ")[0])
            return
        self._library_row = row
        self.start_preview(row["model"], row["voice"], row["language"] if row["kind"] == "tts" else "", "library")

    # ---- samples made in the background
    def start_sample_timers(self):
        self.sample_timer = QTimer(self)
        self.sample_timer.setInterval(45_000)
        self.sample_timer.timeout.connect(self.scan_samples)
        self.sample_timer.start()
        if not os.environ.get("AVS_NO_PROMPTS"):
            QTimer.singleShot(25_000, self.scan_samples)

    def sample_busy(self) -> bool:
        return bool(self.running or self.preview_busy or self.busy_installer or self.move_pending or self.tasks or getattr(self, "pending_jobs", []))

    def scan_samples(self, force=False):
        """Make a sample for every ready voice that has none. Runs quietly, one voice at a time, and steps aside when you generate."""
        if self.sample_building or self.sample_busy():
            return
        if not force and (not self.store.setting("auto_samples", False) or self.store.setting("low_end_mode", False)):
            return
        rows = favorites.all_voices(self.store, self.engine_ready)
        todo = [r for r in rows if r["ready"] and (r["model"], r["voice"]) not in self._sample_failed and not samples.has(samples.row_path(r))]
        if not todo:
            return
        self.sample_building = True
        device = self.preferred_device()
        stop = self._sample_stop = threading.Event()

        def work():
            done = 0
            try:
                for row in todo:
                    if stop.is_set() or self.sample_busy() or guard.reason(self.store):
                        break
                    try:
                        samples.build(self.store, row, device=device)
                        done += 1
                    except Exception:
                        self._sample_failed.add((row["model"], row["voice"]))
                    self.sample_progress.emit(done, len(todo))
            finally:
                self.sample_done.emit()          # always ends, so "a voice sample is being prepared" can never get stuck

        threading.Thread(target=work, daemon=True).start()

    def samples_say(self, text):
        if hasattr(self, "samples_note"):
            self.samples_note.setText(text)

    def prepare_samples_now(self):
        if self.sample_building:
            return self.samples_say("Voice samples are already being prepared.")
        if self.sample_busy():
            return self.samples_say("Finish the current work first, then press this again.")
        self.samples_say("Preparing voice samples. You can stop it any time with Stop preparing.")
        self.scan_samples(force=True)

    def stop_samples(self):
        event = getattr(self, "_sample_stop", None)
        if event is not None and self.sample_building:
            event.set()
            self.samples_say("Stopping after the sample that is being made.")
        else:
            self.samples_say("No voice samples are being prepared.")

    def on_sample_progress(self, done, total):
        if hasattr(self, "lib_count") and self._lib_base:
            self.lib_count.setText(f"{self._lib_base} · preparing samples… {done} of {total}")

    def on_sample_done(self):
        self.sample_building = False
        self.samples_say("Voice samples are finished or stopped.")
        if self.stack.currentIndex() == 6:
            self.refresh_voice_library()

    def library_use(self, row, *_):
        self.select_voice(row["model"], row["voice"])
        self.change_page(0)

    # ------------------------------------------------------------------------- Queue page
    QUEUE_COLUMNS = ["Project", "Model · Voice", "Mode", "Words", "Audio", "Progress", "Now", "Made in", "Created"]

    def make_queue(self):
        super().make_queue()
        page_widget = self.stack.widget(1)
        v = page_widget.layout()

        def drop(index):
            item = v.takeAt(index)
            if item is None:
                return
            def remove(widget):
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

            if item.widget():
                remove(item.widget())
            elif item.layout():
                inner = item.layout()
                while inner.count():
                    taken = inner.takeAt(0)
                    if taken.widget():
                        remove(taken.widget())

        drop(4)   # second button row
        drop(3)   # first button row
        drop(1)   # the wide import button
        table = self.queue_table
        table.setColumnCount(len(self.QUEUE_COLUMNS))
        table.setHorizontalHeaderLabels(self.QUEUE_COLUMNS)
        for i, width in enumerate((230, 250, 120, 80, 80, 230, 320, 120, 130)):
            table.setColumnWidth(i, width)
        for lab in page_widget.findChildren(QLabel):
            if lab.objectName() == "title":
                lab.setText("Queue")
        table.verticalHeader().setDefaultSectionSize(54)
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        bar = QWidget()
        flow = FlowLayout(bar)
        self.queue_buttons = {}
        for key, text, tip, slot, kind in (
                ("import", "Import", "Import TXT or DOCX scripts", self.import_batch, ""),
                ("generate", "▶ Generate", "Make the selected voice-over (continues where it stopped)", self.run_selected, "primary"),
                ("all", "Process all", "Make every waiting voice-over", self.run_all, ""),
                ("pause", "⏸ Pause", "Pause after the current part is finished", self.pause_jobs, ""),
                ("stop", "■ Stop", "Stop right now, even in the middle of a part. Finished parts are kept.", self.stop_generation, "danger"),
                ("clear", "Clear", "Remove the selected job from the list (audio files stay)", self.clear_selected, ""),
                ("clearall", "Clear all", "Remove all jobs from the list (audio files stay)", self.clear_queue, ""),
                ("open", "Open audio", "Open the finished audio", self.open_output, ""),
                ("mp3", "MP3", "Save the finished audio as MP3", self.export_mp3, ""),
                ("folder", "Folder", "Open the folder with your audio", self.open_output_folder, ""),
                ("refresh", "⟳", "Refresh the list", self.refresh_queue, "")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setMinimumHeight(38)
            if kind:
                b.setObjectName(kind)
            b.clicked.connect(lambda _=False, f=slot: f())
            flow.addWidget(b)
            self.queue_buttons[key] = b
        v.insertWidget(1, bar)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.queue_summary = label("", "muted")
        self.queue_summary.setWordWrap(False)
        self.queue_filter = QComboBox()
        for text, data in (("All jobs", ""), ("Waiting", "waiting"), ("Generating", "running"), ("Done", "completed"), ("Failed", "failed")):
            self.queue_filter.addItem(text, data)
        self.queue_filter.currentIndexChanged.connect(self.refresh_queue)
        top.addWidget(self.queue_summary, 1)
        top.addWidget(self.queue_filter)
        v.insertLayout(2, top)
        self._duration_cache = {}
        return

    @staticmethod
    def job_mode(options: dict) -> str:
        mode = options.get("segment_mode", "sentence")
        if options.get("fast_mode"):
            return "Fast"
        if mode == "auto" or (mode == "whole" and options.get("auto_chunk")):
            return "Automatic"
        if mode == "group":
            return f"{options.get('group_size', 3)} paragraphs"
        if options.get("fast_mode"):
            return "Fast"
        return {"whole": "One go", "paragraph": "Paragraphs", "sentence": "Sentences"}.get(mode, mode.title())

    def job_facts(self, job):
        try:
            options = json.loads(job["options"] or "{}")
        except ValueError:
            options = {}
        parts = self.store.sections(job["id"])
        done = sum(1 for x in parts if x["status"] == "complete")
        out = Path(job["output"]) if job.get("output") else None
        ready = bool(out and is_audio(out))
        length = ""
        if ready:
            stamp = (str(out), out.stat().st_mtime_ns)
            if stamp not in self._duration_cache:
                try:
                    from .audio_fx import wav_seconds
                    self._duration_cache[stamp] = wav_seconds(out)
                except Exception:
                    self._duration_cache[stamp] = 0
            seconds = int(self._duration_cache[stamp])
            length = f"{seconds // 60}:{seconds % 60:02d}"
        formats = []
        if ready:
            formats.append("WAV")
            if out.with_suffix(".mp3").is_file():
                formats.append("MP3")
        return {"options": options, "parts": len(parts), "done": done, "ready": ready, "length": length, "out": out,
                "formats": " · ".join(formats) if formats else "WAV (pending)", "source": options.get("source_type", "Typed"),
                "words": len(job["script"].split()), "chars": len(job["script"]),
                "made": float(options.get("made_seconds", 0) or 0)}

    def refresh_queue(self, *args):
        if not hasattr(self, "queue_filter"):
            return
        previous = self.selected_job()
        wanted = self.queue_filter.currentData()
        jobs = self.store.jobs()
        counts = {"waiting": 0, "running": 0, "completed": 0, "failed": 0}
        shown = []
        for job in jobs:
            running = job["id"] in self.running
            waiting = job["id"] in getattr(self, "pending_jobs", [])
            state = "running" if running else "waiting" if (waiting or job["status"] in ("queued", "pending", "paused")) else job["status"]
            if state not in counts:
                state = "failed" if job["status"] in ("failed", "error") else "waiting"
            counts[state] += 1
            if not wanted or wanted == state:
                shown.append((job, state))
        self.queue_summary.setText(f"{counts['waiting']} waiting · {counts['running']} generating · {counts['completed']} done · {counts['failed']} failed")
        table = self.queue_table
        table.blockSignals(True)
        table.setRowCount(len(shown))
        choose = 0
        for row, (job, state) in enumerate(shown):
            f = self.job_facts(job)
            voice = job["voice"] or f["options"].get("clone_name") or "cloned voice"
            reason = self.job_waits.get(job["id"]) if job["id"] in getattr(self, "pending_jobs", []) else self.job_stages.get(job["id"]) if job["id"] in self.running else None
            now = self.queue_now_text(job, state, f, reason)
            values = [job["title"], f"{models().get(job['model'], {}).get('name', job['model']).split(' · ')[0]} · {voice}",
                      self.job_mode(f["options"]), f"{f['words']:,}", f["length"] or "—", "", now,
                      perf.fmt_duration(f["made"]) if f["made"] and state == "completed" else "—",
                      datetime.fromtimestamp(job["created"]).strftime("%d %b  %H:%M")]
            for col, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setToolTip(str(text))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, job["id"])
                table.setItem(row, col, item)
            bar = QProgressBar()
            effective, total_parts, done_parts = self.job_progress(job["id"])
            bar.setRange(0, 1000)
            bar.setValue(1000 if state == "completed" else int(1000 * effective / max(1, total_parts)))
            bar.setFormat(f"{done_parts} / {total_parts} parts")
            bar.setTextVisible(True)
            bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar.setStyleSheet("QProgressBar{height:24px;border-radius:11px;font-size:14px;font-weight:600;} QProgressBar::chunk{border-radius:11px;}")
            table.setCellWidget(row, 5, bar)
            if job["id"] == previous:
                choose = row
        if shown:
            table.selectRow(choose)
        table.blockSignals(False)
        self.update_queue_detail()
        if hasattr(self, "queue_player"):
            self.load_selected_audio()
        if hasattr(self, "panel_stack") and self.panel_stack.currentIndex() == 1 and self.stack.currentIndex() == 0:
            self.refresh_history()

    def update_queue_detail(self):
        if not hasattr(self, "queue_detail"):
            return
        jid = self.selected_job()
        job = self.store.job(jid) if jid else None
        if not job:
            self.queue_detail.setText("Select a job to see its details.")
            return
        f = self.job_facts(job)
        lines = [f"<b>{job['title']}</b>  ·  source: {f['source']}  ·  {f['words']:,} words, {f['chars']:,} characters",
                 f"Model: {models().get(job['model'], {}).get('name', job['model'])}  ·  mode: {self.job_mode(f['options'])}  ·  {f['done']} of {f['parts']} parts done"]
        if f["ready"]:
            size = f["out"].stat().st_size / 1e6
            lines.append(f"Output: {f['out'].name}  ·  type {f['formats']}  ·  {size:.1f} MB  ·  length {f['length']}")
        else:
            lines.append("Output: " + (Path(job["output"]).name if job.get("output") else "—") + "  ·  type WAV (not finished yet)")
        if f["made"] and job["status"] == "completed":
            lines.append(f"Made in: <b>{perf.fmt_duration(f['made'])}</b>")
        summary = perf.summary(job["id"])
        if summary:
            lines.append(f"Speed: {summary['avg_s']} s per part  ·  {perf.verdict(summary['rtf'])}")
        if job.get("error"):
            lines.append("Last problem: " + job["error"].splitlines()[0][:200])
        self.queue_detail.setText("<br>".join(lines))
        self.queue_detail.setTextFormat(Qt.TextFormat.RichText)

    # ------------------------------------------------------------------------- batch import (modes, polish, source type)
    def batch_options(self, entry, path):
        opt = {**self.mode_options(), "target_chars": 0, "pause_ms": self.pause.value(), "device": self.device_for(entry["id"]) if entry.get("torch") else self.preferred_device(),
               "device_auto": self.store.setting("compute_mode", "auto") == "auto", **(self.gpu_options(entry["id"]) if entry.get("torch") else {"hybrid": False}),
               "normalize": self.chk_norm.isChecked(), "trim_silence": self.chk_trim.isChecked(), "fade_ms": 400 if self.chk_fade.isChecked() else 0,
               "volume_pct": self.volume_spin.value(), "auto_mp3": self.mp3_check.isChecked(), "output_speed": self.output_speed.value(),
               "pitch_semitones": self.pitch.value(), "low_end": bool(self.store.setting("low_end_mode", False)),
               "fast_precision": bool(self.store.setting("fast_precision", False)), "source_type": Path(path).suffix.lstrip(".").upper() or "TXT"}
        chunk = self.chunk_setting()
        if chunk:
            opt["chunk_chars"] = chunk
        supports = set(entry.get("supports", []))
        for key, widget in (("exaggeration", self.opt_widgets["exaggeration"][1]), ("cfg_weight", self.opt_widgets["cfg_weight"][1]),
                            ("temperature", self.temperature), ("guidance", self.guidance), ("style_alpha", self.style_alpha),
                            ("style_beta", self.style_beta), ("diffusion_steps", self.diffusion_steps), ("embedding_scale", self.embedding_scale)):
            if key in supports:
                opt[key] = widget.value()
        if entry.get("lang_option") and not entry["id"].startswith("qwen"):
            opt["language"] = self.language_combo.currentData()
        elif entry["id"].startswith("qwen"):
            opt["language"] = self.language_combo.currentData() or "Auto"
        return opt

    def import_batch(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import scripts", str(Path.home()), DOC_FILTER)
        if not paths:
            return
        usable = [m for m in models().values() if m["kind"] in ("tts", "clone") and not capabilities.unavailable_reason(m)]
        d = QDialog(self)
        d.setWindowTitle("Choose a voice for each script")
        d.resize(900, 500)
        v = QVBoxLayout(d)
        v.addWidget(label("Choose the model and voice for each script, or apply the first row to all. Cloning rows use the voice selected in Voice Studio.", "muted"))
        table = QTableWidget(len(paths), 4)
        table.setHorizontalHeaderLabels(["Script", "Type", "Model", "Voice"])
        h = table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        v.addWidget(table)
        rows = []

        def fill(model_combo, voice_combo):
            voice_combo.clear()
            entry = models()[model_combo.currentData()]
            for x in entry["voices"]:
                voice_combo.addItem(x["name"], x["id"])
            if not entry["voices"]:
                voice_combo.addItem("Selected cloned voice", "")

        for i, path in enumerate(paths):
            table.setItem(i, 0, QTableWidgetItem(Path(path).name))
            table.setItem(i, 1, QTableWidgetItem(Path(path).suffix.lstrip(".").upper()))
            mc, vc = QComboBox(), QComboBox()
            for entry in usable:
                mc.addItem(entry["name"], entry["id"])
            mc.setCurrentIndex(max(0, mc.findData(self.model_combo.currentData())))
            fill(mc, vc)
            vc.setCurrentIndex(max(0, vc.findData(self.voice_combo.currentData())))
            mc.currentIndexChanged.connect(lambda _i, m=mc, c=vc: fill(m, c))
            table.setCellWidget(i, 2, mc)
            table.setCellWidget(i, 3, vc)
            rows.append((mc, vc))

        def same():
            model, voice = rows[0][0].currentData(), rows[0][1].currentData()
            for mc, vc in rows[1:]:
                mc.setCurrentIndex(mc.findData(model))
                vc.setCurrentIndex(max(0, vc.findData(voice)))

        v.addWidget(button("Use the first row for all scripts", same))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(d.accept)
        box.rejected.connect(d.reject)
        v.addWidget(box)
        if not d.exec():
            return
        if any(models()[m.currentData()]["kind"] == "clone" for m, _ in rows) and not self.profile(self.voice_combo.currentData()):
            return self.alert("Select a saved cloned voice in Voice Studio before queuing cloning jobs.")
        count = 0
        with self.pronunciation_applied():
            for path, (mc, vc) in zip(paths, rows):
                try:
                    entry = models()[mc.currentData()]
                    reference = ref_text = ""
                    if entry["kind"] == "clone":
                        _v, reference, ref_text, _p = self.resolve_voice(entry["id"], self.voice_combo.currentData())
                    self.store.new_job(Path(path).stem, read_script(path), entry["id"], vc.currentData() if entry["kind"] != "clone" else "",
                                       reference, ref_text, self.batch_options(entry, path))
                    count += 1
                except Exception as exc:
                    self.log_message("ERROR: " + str(exc))
        self.change_page(1)
        self.toast(f"{count} scripts queued. Press Process pending to start.")

    # ------------------------------------------------------------------------- other pages
    def apply_unavailable(self):
        """Every model can be downloaded on every PC. A model that cannot make audio here gets a plain note; every card gets what suits this PC."""
        from . import recommend
        stars = recommend.recommended(models())
        for key, entry in models().items():
            card_ = getattr(self, "setup_cards", {}).get(key)
            if not card_:
                continue
            reason = capabilities.unavailable_reason(entry)
            if reason:
                card_["status"].setToolTip(reason)
            desc = card_.get("desc")
            if desc is not None:
                badge = recommend.badge(key, entry, stars)
                good = key in stars
                colour = "#3fbf7f" if good else ("#e0a24f" if recommend.rate(key, entry)["tier"] in ("slow", "very slow", "download only") else "#7fb2e0")
                desc.setTextFormat(Qt.TextFormat.RichText)
                desc.setText(f"<span style='color:{colour};font-weight:600'>{badge}</span> &nbsp;·&nbsp; {card_.get('base_desc', '')}")
                desc.setToolTip(recommend.rate(key, entry)["why"])

    def refresh_card(self, key):
        super().refresh_card(key)
        self.apply_unavailable()
        self.decorate_card(key)

    def decorate_card(self, key):
        """Update badge and a plain size text on a model card."""
        c = self.setup_cards.get(key)
        if not c or key in self.tasks:
            return
        flag = self.update_flags.get(key)
        if flag and (flag.get("files") or flag.get("deps")):
            c["status"].setText("Update available")
            c["status"].setToolTip(model_info.describe(flag))
            c["install"].setText("Update")
            c["install"].setEnabled(True)
        elif c["bytes"].text() == "Size pending" and not self._size_busy:
            c["bytes"].setText("Size unknown")

    def install_card(self, key):
        reason = capabilities.unavailable_reason(models()[key])
        if reason and not self.confirm("Download only", reason + "\n\nYou can still download it here, for example to copy it to a PC that can run it. Download anyway?"):
            return
        flag = self.update_flags.pop(key, None)
        if flag and flag.get("files"):
            model_info.prepare_update(key, self.card_voices(key))     # ask the source for the current file list
        super().install_card(key)

    @staticmethod
    def model_title(m):
        name = (m["name"].replace(" (experimental)", "").replace(" · Voice Cloning", "")
                .replace(" CustomVoice", "").replace(" · Voice cloning", ""))
        mode = "Cloning" if m["kind"] == "clone" else "Custom voices" if m["id"].startswith("qwen") else "Built-in voices"
        return f"{name} ({mode})"

    def make_setup(self):
        super().make_setup()
        self.apply_unavailable()
        self.stack.widget(3).widget().layout().insertLayout(1, actions(button("Find models on this PC…", self.find_models)))
        self.visible_gates["setup.attach"] = [c["attach"] for c in self.setup_cards.values()]
        page_widget = self.stack.widget(3).widget()
        self.build_update_bar(page_widget)
        self.modernize_bars()
        for lab in page_widget.findChildren(QLabel):
            if lab.objectName() == "muted" and "icense" in lab.text():
                kept = [x for x in re.split(r"(?<=[.])\s+", lab.text()) if "icense" not in x]
                lab.setText(" ".join(kept))
        # Plain wording only: no source websites, licence notes or build details in the interface.
        for b in page_widget.findChildren(QPushButton):
            if b.text() == "S":
                b.hide()
        for lab in page_widget.findChildren(QLabel):
            if lab.toolTip() and lab.objectName() == "muted":
                lab.setToolTip("")
        add_heading = next((x for x in page_widget.findChildren(QLabel) if x.text() == "Add Hugging Face model"), None)
        add_panel = None
        if add_heading:
            add_heading.setText("Add an extra voice model")
            lay = page_widget.layout()
            idx = lay.indexOf(add_heading)
            item = lay.itemAt(idx + 1) if idx >= 0 else None
            add_panel = item.widget() if item else None
        if add_panel:
            for edit in add_panel.findChildren(QLineEdit):
                edit.setPlaceholderText("Model address")
            for lab in add_panel.findChildren(QLabel):
                lab.setText("Owner option for compatible extra voice models.")
            self.gate("setup.add_model", add_heading, add_panel)

    # ------------------------------------------------------------------------- Models: updates, sizes, bars
    def build_update_bar(self, page_widget):
        """Top of the Models page: Find updates and Fix problems."""
        frame, lay = card("Updates", "Checks every installed model and its engine packages. What can be updated is marked on its card.")
        self.find_updates_button = button("Find updates", self.start_update_check, True)
        self.fix_button = button("Fix problems", self.run_health_check)
        self.update_all_button = button("Update all", self.update_all)
        self.update_all_button.hide()
        self.update_note = label("", "muted")
        self.update_note.setWordWrap(False)
        lay.addLayout(actions(self.find_updates_button, self.fix_button, self.update_all_button, self.update_note))
        page_widget.layout().insertWidget(1, frame)
        self.gate("set.dependencies", self.fix_button)      # same owner rule as the software check

    def modernize_bars(self):
        """Replace the plain progress bars on the model cards with the modern glowing bar."""
        for c in self.setup_cards.values():
            old = c["bar"]
            if isinstance(old, NeonBar):
                continue
            new = NeonBar(height=8)
            new.setRange(old.minimum(), old.maximum())
            new.setValue(old.value())
            holder = old.parentWidget().layout() if old.parentWidget() is not None else None
            if holder is None:
                continue
            holder.replaceWidget(old, new)
            old.hide()
            old.deleteLater()
            c["bar"] = new

    def check_model_sizes(self, force=False):
        """Show the saved sizes at once; look up missing or old ones for all models at the same time."""
        keys = list(self.setup_cards)
        for k in keys:
            total = model_info.known_total(k, self._size_cache)
            if total and k not in self.model_totals:
                self.model_totals[k] = total
        for k in self.setup_cards:
            if k not in self.tasks:
                self.refresh_card(k)
        todo = list(keys) if force else model_info.stale_keys(keys, self._size_cache)
        if not todo or self._size_busy or self.move_pending:
            return
        self._size_busy = True

        def work():
            try:
                model_info.refresh_sizes(todo, self.card_voices, on_result=lambda k, row: self.sizes_ready.emit(k, row),
                                         should_stop=lambda: bool(self.move_pending))
            except Exception:
                pass
            finally:
                self.sizes_ready.emit("", None)

        threading.Thread(target=work, daemon=True).start()

    def on_sizes_ready(self, key, row):
        if not key:
            self._size_busy = False
            for k in self.setup_cards:
                if k not in self.tasks:
                    self.refresh_card(k)
            return
        self._size_cache[key] = row
        if row and row.get("total"):
            self.model_totals[key] = row["total"]
        if key in self.setup_cards and key not in self.tasks:
            self.refresh_card(key)

    def start_update_check(self):
        if self._update_busy:
            return
        if self.busy_installer or self.move_pending:
            return self.alert("Finish the current update or maintenance first.")
        keys = [k for k in self.setup_cards if k not in self.tasks and not capabilities.unavailable_reason(models()[k])]
        self._update_busy = True
        self.find_updates_button.setEnabled(False)
        self.update_all_button.hide()
        self.update_note.setText("Checking every installed model…")
        self.check_model_sizes(force=True)

        def work():
            try:
                results, failed = model_info.find_updates(keys, self.card_voices)
            except Exception:
                results, failed = {}, len(keys)
            self.model_updates_ready.emit(results, failed)

        threading.Thread(target=work, daemon=True).start()

    def on_model_updates_ready(self, results, failed):
        self._update_busy = False
        self.find_updates_button.setEnabled(True)
        self.update_flags = {k: v for k, v in results.items() if v.get("files") or v.get("deps")}
        for k in self.setup_cards:
            if k not in self.tasks:
                self.refresh_card(k)
        n = len(self.update_flags)
        if not results:
            text = "No models are installed yet." if not failed else "Could not check. Please check the internet and try again."
        elif n:
            gb = sum(v.get("bytes", 0) for v in self.update_flags.values()) / 1e9
            text = f"{n} update{'s' if n != 1 else ''} found" + (f" · about {gb:.1f} GB" if gb >= 0.05 else "")
        else:
            text = "Everything is up to date."
        if failed and results:
            text += f" · {failed} could not be checked"
        self.update_note.setText(text)
        self.update_all_button.setVisible(bool(n))
        self.store.set_setting("last_update_check", time.time())

    def update_all(self):
        for key in list(self.update_flags):
            self.install_card(key)
        self.update_all_button.hide()
        self.update_note.setText("Updating… the models are queued and use your load settings.")

    # ------------------------------------------------------------------------- Load strength
    def start_load_control(self):
        name = load_control.chosen(self.store)
        if self.store.setting("load_v13", None) is None:
            self.store.set_setting("load_v13", True)
            if self.store.setting("load_level", "auto") == "auto":
                load_control.apply_level_settings(self.store, name)    # first start of this version: limits that suit this PC
            if int(self.store.setting("guard_temp", 88)) == 83:
                self.store.set_setting("guard_temp", 88)               # laptop graphics cards often run at 80 °C: the old limit held work back
            if int(self.store.setting("script_px", 14)) >= 17:
                self.store.set_setting("script_px", 14)                # smaller text in the script box
            if load_control.detect_hardware()["gpu"] and self.store.setting("compute_mode", "auto") == "cpu":
                self.store.set_setting("compute_mode", "auto")         # use the graphics card whenever there is one
        elif self.store.setting("compute_cpu_temp", False):
            self.store.set_setting("compute_cpu_temp", False)          # the automatic fix "use the processor" lasts one session only
            if self.store.setting("compute_mode", "auto") == "cpu" and load_control.detect_hardware()["gpu"]:
                self.store.set_setting("compute_mode", "auto")
        self.activate_level(name, sync=True)

    def activate_level(self, name, sync=False):
        """Make a level real: priority of the software and of its helpers, thread limits, CPU limit for installs."""
        from .resource_policy import set_threads_override, limits
        load_control.set_level(name)
        load_control.set_cpu_limit(limits(self.store)["cpu_limit"])
        set_threads_override(int(self.store.setting("cpu_threads_override", 0)))
        load_control.apply_to_self()
        if hasattr(self, "load_info"):
            self.load_info.setText(load_control.profile(name)["info"])
        if not sync or not hasattr(self, "limit_sliders"):
            return
        cap = limits(self.store)
        for key, slider in self.limit_sliders.items():
            slider.blockSignals(True)
            slider.setValue(cap[key])
            slider.blockSignals(False)
            for lab in getattr(self, "limit_labels", {}).get(key, []):
                lab.setText(f"{cap[key]}%")
        want = int(self.store.setting("cpu_threads_override", 0))
        if self.threads_combo.findData(want) < 0:
            self.threads_combo.addItem(f"{want} threads", want)
        for combo, key, default in ((self.threads_combo, "cpu_threads_override", 0), (self.parts_combo, "parts_at_once", 1)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(self.store.setting(key, default))))
            combo.blockSignals(False)
        for box, key, default in ((self.preload_check, "preload_voice", True), (self.samples_check, "auto_samples", False), (self.warm_check, "keep_model_warm", True)):
            box.blockSignals(True)
            box.setChecked(bool(self.store.setting(key, default)))
            box.blockSignals(False)

    def load_level_changed(self, *_):
        pick = self.load_combo.currentData()
        self.store.set_setting("load_level", pick)
        name = load_control.chosen(self.store)
        load_control.apply_level_settings(self.store, name)
        self.activate_level(name, sync=True)

    # ------------------------------------------------------------------------- Settings page
    def make_settings(self):
        super().make_settings()      # creates the working controls that the older code connects to
        old = self.stack.widget(4)
        holder = getattr(self, "_holder", None) or QWidget(self)
        self._holder = holder
        holder.hide()

        section_keys = {}

        def section(key, title, hint=""):
            frame, lay = card(title, hint)
            self.gate(key, frame)
            section_keys[id(frame)] = key
            return frame, lay

        content = QWidget()
        content.setObjectName("page")
        col = QVBoxLayout(content)
        col.setContentsMargins(22, 18, 22, 22)
        col.setSpacing(14)
        col.addWidget(label("Settings", "title"))
        col.addWidget(label("Everything is saved automatically as you change it.", "muted"))
        self.settings_tabs = QTabWidget()
        tab_layouts = {}
        for name in ("General", "Performance", "Backup", "Updates", "Tools"):
            tab_body = QWidget()
            tab_body.setObjectName("page")
            tab_lay = QVBoxLayout(tab_body)
            tab_lay.setContentsMargins(0, 14, 6, 14)
            tab_lay.setSpacing(14)
            tab_scroll = QScrollArea()
            tab_scroll.setWidgetResizable(True)
            tab_scroll.setWidget(tab_body)
            self.settings_tabs.addTab(tab_scroll, name)
            tab_layouts[name] = tab_lay
        tab_of = {"set.appearance": "General", "set.performance": "Performance", "set.pcinfo": "Performance", "set.protection": "Performance", "set.processing": "Performance", "set.dependencies": "Tools",
                  "set.runtime": "Tools", "set.storage": "General", "set.backup": "Backup"}

        def place(frame):
            tab_layouts[tab_of[section_keys[id(frame)]]].addWidget(frame)

        col.addWidget(self.settings_tabs, 1)

        # ---- Dependencies
        frame, lay = section("set.dependencies", "Software check",
                             "Checks Python, audio tools, disk space and every voice engine in one go, and fixes what is missing.")
        self.health_button = button("Check everything", self.run_health_check, True)
        self.health_note = label("", "muted")
        lay.addLayout(actions(self.health_button, self.health_note))
        self.auto_resolve_check = QCheckBox("Auto resolve")
        self.auto_resolve_check.setChecked(bool(self.store.setting("auto_resolve", True)))
        self.auto_resolve_check.toggled.connect(self.set_auto_resolve)
        lay.addWidget(self.auto_resolve_check)
        lay.addLayout(actions(button("Install build tools", self.install_build_tools), button("Repair engines", self.repair_engines)))
        place(frame)

        # ---- Backup and Updates tabs (cards come from ui_updates)
        tab_layouts["Backup"].addWidget(self.build_backup_card())
        tab_layouts["Backup"].addWidget(self.build_owner_card())
        for update_card in self.build_update_cards():
            tab_layouts["Updates"].addWidget(update_card)

        # ---- This PC (specs and optimise)
        frame, pl = section("set.pcinfo", "This PC", "What your computer has, what that means for voice-overs, and one button to set everything for it.")
        self.pc_label = label("Reading your PC…", "muted")
        self.pc_label.setWordWrap(True)
        self.pc_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pl.addWidget(self.pc_label)
        self.optimize_note = label("", "muted")
        self.optimize_note.setWordWrap(True)
        pl.addWidget(self.optimize_note)
        pl.addLayout(actions(button("Optimize for this PC", self.optimize_pc, True), button("Refresh", self.load_pc_info)))
        place(frame)

        # ---- PC protection (easy to find, can be switched off)
        frame, play = section("set.protection", "PC protection", "Pauses a voice-over for a short time when the graphics card is very hot or memory is full. You decide whether it is on.")
        self.guard_check = QCheckBox("Protect my PC (untick to switch protection off completely)")
        self.guard_check.setToolTip("When ticked, voice generation pauses briefly if the graphics card is very hot or memory is full. When unticked it never pauses.")
        self.guard_check.setChecked(bool(self.store.setting("guard_on", True)))
        self.guard_check.toggled.connect(self.guard_toggled)
        play.addWidget(self.guard_check)
        self.guard_wait = QSlider(Qt.Orientation.Horizontal)
        self.guard_wait.setRange(10, 300)
        self.guard_wait.setSingleStep(5)
        self.guard_wait.setPageStep(30)
        self.guard_wait.setValue(max(10, min(300, int(self.store.setting("guard_wait", 45)))))
        self.guard_wait_label = label(self.wait_text(self.guard_wait.value()), "muted")
        self.guard_wait_label.setFixedWidth(100)
        self.guard_wait_label.setWordWrap(False)
        self.guard_wait.valueChanged.connect(self.guard_wait_changed)
        wait_row = QHBoxLayout()
        wait_row.addWidget(field("Wait at most"))
        wait_row.addWidget(self.guard_wait, 1)
        wait_row.addWidget(self.guard_wait_label)
        play.addLayout(wait_row)
        self.guard_note = label("Protection off: the software never waits. Turn it on to pause when the PC is very hot.", "muted")
        play.addWidget(self.guard_note)
        self.guard_temp = QSpinBox()
        self.guard_temp.setRange(70, 95)
        self.guard_temp.setSuffix(" °C")
        self.guard_temp.setValue(max(70, min(95, int(self.store.setting("guard_temp", 88)))))
        self.guard_temp.valueChanged.connect(lambda n: self.store.set_setting("guard_temp", int(n)))
        gform = new_form()
        self.guard_temp_box = ChoiceBox(self.guard_temp, range(70, 96), lambda n: f"{n} °C")
        gform.addRow(field("Wait above"), self.guard_temp_box)
        play.addLayout(gform)
        self.sync_guard_controls()
        place(frame)

        # ---- Performance
        frame, lay = section("set.performance", "Performance",
                             "Slower PCs work best with small parts and one job at a time. Long scripts are always finished as one audio.")
        hw = load_control.detect_hardware()
        self.load_combo = QComboBox()
        self.load_combo.addItem("Automatic · " + load_control.LEVELS[load_control.recommend(hw)]["title"] + " (suits this PC)", "auto")
        for level in load_control.ORDER:
            self.load_combo.addItem(load_control.LEVELS[level]["title"], level)
        self.load_combo.setCurrentIndex(max(0, self.load_combo.findData(self.store.setting("load_level", "auto"))))
        self.load_combo.setToolTip("How hard the software may work. It controls the priority and the number of threads of everything the software starts, "
                                   "including installs and checks, not only voice-overs.")
        self.load_combo.currentIndexChanged.connect(self.load_level_changed)
        lform = new_form()
        lform.addRow(field("Load strength"), self.load_combo)
        lay.addLayout(lform)
        self.load_info = label(load_control.profile(load_control.chosen(self.store))["info"], "muted")
        lay.addWidget(self.load_info)
        lay.addWidget(label(load_control.describe(hw), "muted"))
        self.low_end = QCheckBox("Low-end PC mode (slower, uses much less memory)")
        self.low_end.setChecked(bool(self.store.setting("low_end_mode", False)))
        self.low_end.toggled.connect(self.set_low_end)
        lay.addWidget(self.low_end)
        form = new_form()
        self.chunk_combo = QComboBox()
        for text, value in (("Automatic (recommended)", 0), ("Small parts · slowest PCs", 150),
                            ("Medium parts", 250), ("Large parts · fast PCs", 400)):
            self.chunk_combo.addItem(text, value)
        self.chunk_combo.setCurrentIndex(max(0, self.chunk_combo.findData(self.chunk_setting())))
        self.chunk_combo.currentIndexChanged.connect(self.chunk_changed)
        form.addRow(field("Part size"), self.chunk_combo)
        lay.addLayout(form)
        from .resource_policy import limits
        self.limit_sliders = {}
        self.limit_labels = {}
        for name, key in (("CPU limit", "cpu_limit"), ("Memory limit", "ram_limit"), ("Graphics limit", "gpu_limit")):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(10, 100)
            slider.setValue(limits(self.store)[key])
            value = label(f"{slider.value()}%", "muted")
            value.setFixedWidth(52)
            value.setWordWrap(False)
            slider.valueChanged.connect(partial(self.limit_changed, key, value))
            row = QHBoxLayout()
            name_label = field(name)
            row.addWidget(name_label)
            row.addWidget(slider, 1)
            row.addWidget(value)
            lay.addLayout(row)
            self.limit_sliders[key] = slider
            self.limit_labels[key] = [value]
            if key == "gpu_limit" and not hw["gpu"]:
                for part in (name_label, slider, value):      # no NVIDIA card: nothing to limit
                    part.hide()
        self.warm_check = QCheckBox("Keep the last voice loaded while not in use (faster, uses more memory)")
        self.warm_check.setChecked(bool(self.store.setting("keep_model_warm", True)))
        self.warm_check.toggled.connect(self.set_warm_enabled)
        lay.addWidget(self.warm_check)
        self.warm_minutes = QSpinBox()
        self.warm_minutes.setRange(2, 6)
        self.warm_minutes.setSuffix(" minutes")
        self.warm_minutes.setValue(max(2, min(6, int(self.store.setting("warm_minutes", 5)))))
        self.warm_minutes.valueChanged.connect(self.warm_minutes_changed)
        wform = new_form()
        wform.addRow(field("Keep loaded"), ChoiceBox(self.warm_minutes, range(2, 7), lambda n: f"{n} minutes"))
        lay.addLayout(wform)
        self.preload_check = QCheckBox("Prepare the selected voice in the background (first Generate starts quickly)")
        self.preload_check.setChecked(bool(self.store.setting("preload_voice", True)))
        self.preload_check.toggled.connect(lambda on: self.store.set_setting("preload_voice", bool(on)))
        lay.addWidget(self.preload_check)
        self.samples_check = QCheckBox("Prepare voice samples automatically in the background (off unless you turn it on)")
        self.samples_check.setChecked(bool(self.store.setting("auto_samples", False)))
        self.samples_check.toggled.connect(lambda on: self.store.set_setting("auto_samples", bool(on)))
        lay.addWidget(self.samples_check)
        lay.addLayout(actions(button("Prepare voice samples now", self.prepare_samples_now), button("Stop preparing", self.stop_samples)))
        self.samples_note = label("", "muted")
        lay.addWidget(self.samples_note)
        lay.addWidget(label("Library voices play instantly after a model is installed.", "muted"))
        self.fast_check = QCheckBox("Faster generation: half precision (NVIDIA only, experimental)")
        self.fast_check.setToolTip("Can speed up and use less graphics memory on some cards. Turn it off if the voice sounds wrong.")
        self.fast_check.setChecked(bool(self.store.setting("fast_precision", False)))
        self.fast_check.toggled.connect(lambda on: self.store.set_setting("fast_precision", bool(on)))
        lay.addWidget(self.fast_check)
        self.fast_check.setVisible(hw["gpu"])
        pf = new_form()
        self.threads_combo = QComboBox()
        self.threads_combo.addItem("Automatic", 0)
        for n in (2, 4, 6, 8, 12, 16):
            if n <= max(2, (os.cpu_count() or 4)):
                self.threads_combo.addItem(f"{n} threads", n)
        self.threads_combo.setCurrentIndex(max(0, self.threads_combo.findData(int(self.store.setting("cpu_threads_override", 0)))))
        self.threads_combo.setToolTip("How many processor threads a voice engine may use. Automatic follows the CPU limit.")
        self.threads_combo.currentIndexChanged.connect(self.threads_changed)
        self.retry_combo = QComboBox()
        for n, text in ((0, "Off"), (1, "1 time"), (2, "2 times"), (3, "3 times")):
            self.retry_combo.addItem(text, n)
        self.retry_combo.setCurrentIndex(max(0, self.retry_combo.findData(int(self.store.setting("retry_parts", 1)))))
        self.retry_combo.setToolTip("When a part fails, try it again automatically (too-long text is split into smaller pieces).")
        self.retry_combo.currentIndexChanged.connect(lambda _i: self.store.set_setting("retry_parts", self.retry_combo.currentData()))
        self.rest_combo = QComboBox()
        for ms, text in ((0, "None"), (200, "0.2 seconds"), (500, "0.5 seconds"), (1000, "1 second"), (2000, "2 seconds")):
            self.rest_combo.addItem(text, ms)
        self.rest_combo.setCurrentIndex(max(0, self.rest_combo.findData(int(self.store.setting("rest_ms", 0)))))
        self.rest_combo.setToolTip("A short rest between parts keeps a hot laptop cooler.")
        self.rest_combo.currentIndexChanged.connect(lambda _i: self.store.set_setting("rest_ms", self.rest_combo.currentData()))
        pf.addRow(field("Threads"), self.threads_combo)
        pf.addRow(field("Retry failed"), self.retry_combo)
        pf.addRow(field("Rest between parts"), self.rest_combo)
        lay.addLayout(pf)
        from .speedup import CHOICES as PART_CHOICES
        self.parts_combo = QComboBox()
        for text, value in PART_CHOICES:
            self.parts_combo.addItem(text, value)
        saved_parts = self.store.setting("parts_at_once", 1)
        self.parts_combo.setCurrentIndex(max(0, self.parts_combo.findData(saved_parts if saved_parts in (1, 2, 3, 4, "auto") else 1)))
        self.parts_combo.currentIndexChanged.connect(lambda _i: self.store.set_setting("parts_at_once", self.parts_combo.currentData()))
        qf = new_form()
        qf.addRow(field("Parts at once"), self.parts_combo)
        lay.addLayout(qf)
        lay.addWidget(label("Makes several parts at the same time. Needs more graphics memory.", "muted"))
        self.autofix_check = QCheckBox("Fix problems automatically (split long text, retry, switch to the processor)")
        self.autofix_check.setChecked(bool(self.store.setting("autofix", True)))
        self.autofix_check.toggled.connect(lambda on: self.store.set_setting("autofix", bool(on)))
        lay.addWidget(self.autofix_check)
        self.release_check = QCheckBox("Free graphics memory after each voice-over")
        self.release_check.setChecked(bool(self.store.setting("release_after_job", False)))
        self.release_check.toggled.connect(lambda on: self.store.set_setting("release_after_job", bool(on)))
        lay.addWidget(self.release_check)
        self.breaker_check = QCheckBox("Stop the queue after 3 failed voice-overs in a row")
        self.breaker_check.setChecked(bool(self.store.setting("guard_breaker", True)))
        self.breaker_check.toggled.connect(lambda on: self.store.set_setting("guard_breaker", bool(on)))
        lay.addWidget(self.breaker_check)
        lay.addLayout(actions(button("Speed test", self.run_speed_test, True), button("Show last speed report", self.show_speed_report_dialog)))
        self.speed_report = label("", "muted")
        lay.addWidget(self.speed_report)
        lay.addLayout(actions(button("Low-end", partial(self.apply_profile, "low")),
                              button("Balanced", partial(self.apply_profile, "balanced")),
                              button("High-end", partial(self.apply_profile, "high")),
                              button("Release loaded voice", self.release_warm_model)))
        place(frame)

        # ---- Processing (existing controls, tidied)
        frame, lay = section("set.processing", "Processing")
        pform = new_form()
        pform.addRow(field("Mode"), self.queue_mode)
        pform.addRow(field("Jobs at once"), ChoiceBox(self.parallel, range(1, 5), lambda n: f"{n} job" + ("s" if n > 1 else "")))
        pform.addRow(field("Downloads at once"), ChoiceBox(self.down_limit, range(1, 5), lambda n: f"{n} download" + ("s" if n > 1 else "")))
        pform.addRow(field("Use"), self.compute)
        lay.addLayout(pform)
        self.beep_check = QCheckBox("Play a sound when finished (when Autoplay is off)")
        self.beep_check.setChecked(bool(self.store.setting("beep_done", True)))
        self.beep_check.toggled.connect(lambda on: self.store.set_setting("beep_done", bool(on)))
        lay.addWidget(self.beep_check)
        place(frame)

        # ---- Save and apply (end of the Performance tab)
        frame, lay = card("Save and apply", "Everything above is already saved. Press this to apply it to the running software now: limits, load strength, processor threads and the voice engines.")
        self.apply_note = label("", "muted")
        self.apply_note.setWordWrap(True)
        lay.addLayout(actions(button("Save and apply to this software", self.apply_performance, True)))
        lay.addWidget(self.apply_note)
        tab_layouts["Performance"].addWidget(frame)

        # ---- Runtime
        frame, lay = section("set.runtime", "Python")
        lay.addLayout(actions(self.bootstrap_status, button("Install / repair Python and C++ tools", self.install_runtime),
                              button("Cancel", lambda: self.cancel_task("runtime"))))
        self.libtorch_status.setParent(holder)
        self.libtorch_status.hide()
        place(frame)

        # ---- Appearance
        frame, lay = section("set.appearance", "Appearance")
        aform = new_form()
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.store.setting("ui_theme", "dark"))))
        self.accent_combo = QComboBox()
        for name in theme.ACCENTS:
            self.accent_combo.addItem(name, name)
        self.accent_combo.setCurrentIndex(max(0, self.accent_combo.findData(self.store.setting("ui_accent", "Teal"))))
        self.text_spin = QSpinBox()
        self.text_spin.setRange(13, 20)
        self.text_spin.setSuffix(" px")
        self.text_spin.setValue(int(self.store.setting("ui_text_px", 15)))
        self.script_spin = QSpinBox()
        self.script_spin.setRange(13, 28)
        self.script_spin.setSuffix(" px")
        self.script_spin.setValue(int(self.store.setting("script_px", 14)))
        aform.addRow(field("Theme"), self.theme_combo)
        aform.addRow(field("Accent colour"), self.accent_combo)
        aform.addRow(field("Text size"), ChoiceBox(self.text_spin, range(13, 21), lambda n: f"{n} px"))
        aform.addRow(field("Script text"), ChoiceBox(self.script_spin, range(13, 29), lambda n: f"{n} px"))
        lay.addLayout(aform)
        for widget, key in ((self.theme_combo, "ui_theme"), (self.accent_combo, "ui_accent")):
            widget.currentIndexChanged.connect(partial(self.appearance_changed, key, widget))
        for widget, key in ((self.text_spin, "ui_text_px"), (self.script_spin, "script_px")):
            widget.valueChanged.connect(partial(self.appearance_changed, key, widget))
        place(frame)

        # ---- Storage
        frame, lay = section("set.storage", "Storage")
        lay.addLayout(actions(button("Open audio folder", lambda: self.open_path(subdir("outputs"))),
                              button("Open data folder", lambda: self.open_path(root())),
                              button("Move folder", self.move_storage),
                              button("Clear voice samples", self.clear_samples)))
        place(frame)


        for tab_lay in tab_layouts.values():
            tab_lay.addStretch(1)
        self.stack.removeWidget(old)
        old.setParent(holder)
        self.stack.insertWidget(4, content)

    # ---- settings handlers
    def appearance_changed(self, key, widget, *_):
        value = widget.currentData() if isinstance(widget, QComboBox) else widget.value()
        self.store.set_setting(key, value)
        self.apply_theme()

    def auto_prepare_restored(self):
        """After a backup with AI models was restored: find them, prepare their engines and let them work. No questions."""
        marker = subdir("state") / "restored_models.json"
        if not marker.is_file():
            return
        try:
            keys = json.loads(marker.read_text(encoding="utf-8")).get("models", [])
            marker.unlink(missing_ok=True)
        except (OSError, ValueError):
            return
        started = 0
        for key in keys:
            try:
                if key in self.setup_cards and key not in self.tasks and all(managed.downloaded(key, v) for v in self.card_voices(key)):
                    self.install_card(key)       # checks the files and prepares the engine (needs internet for the engine's packages)
                    started += 1
            except Exception:
                continue
        if started and not os.environ.get("AVS_NO_PROMPTS"):
            self.toast(f"Preparing {started} restored model(s)…")
            self.change_page(3)

    def load_pc_info(self):
        self.pc_label.setText("Reading your PC…")

        def work():
            try:
                info = load_control.pc_info(root())
            except Exception as exc:
                info = {"error": str(exc)}
            self.pc_info_ready.emit(info)

        threading.Thread(target=work, daemon=True).start()

    def on_pc_info(self, info):
        if info.get("error"):
            self.pc_label.setText("Could not read the PC details: " + info["error"][:200])
            return
        self._pc_info = info
        text = load_control.pc_report(info)
        rows = []
        shown = [mid for mid in ("qwen06bclone", "qwen17bclone", "chatterbox", "turbo", "f5", "xtts2") if mid in models()]
        for mid in shown:
            how = load_control.plan(mid, models()[mid])
            rows.append(f"  {models()[mid]['name'].split(' · ')[0]}: " + {"hybrid": "graphics card + processor together", "gpu": "graphics card", "mps": "Apple graphics (Metal)", "cpu": "processor"}[how["mode"]]
                        + (f" ({how['precision']} precision)" if how["mode"] not in ("cpu", "mps") else ""))
        if rows:
            text += "\n\nWith Run on = Automatic:\n" + "\n".join(rows)
        self.pc_label.setText(text)

    def apply_performance(self):
        """Apply the Performance settings to the running software at once (they are saved already)."""
        from .resource_policy import set_threads_override, limits
        from . import shutdown, limiter
        values = limits(self.store)
        load_control.set_level(load_control.chosen(self.store))
        load_control.set_cpu_limit(values["cpu_limit"])
        set_threads_override(int(self.store.setting("cpu_threads_override", 0)))
        load_control.apply_to_self()
        capped = shutdown.set_cpu_cap(values["cpu_limit"])
        lim = limiter._current.get("limiter")
        if lim is not None and hasattr(lim, "refresh"):
            lim.refresh()
        released = False
        if not (self.running or getattr(self, "pending_jobs", []) or self.tasks):
            warm_worker.release()            # the voice engines load again with the new settings at the next voice-over
            released = True
        self.sync_speed_note()
        lines = [f"Processor limit {values['cpu_limit']}%, memory limit {values['ram_limit']}%, graphics limit {values['gpu_limit']}% now count for the whole software."
                 + (" Windows enforces the processor limit." if capped else ""),
                 "The voice engines will load again with the new settings." if released else "A voice-over is running: the new engine settings start with the next one."]
        self.apply_note.setText("✔ Saved and applied.  " + "  ".join(lines))
        self.toast("Saved and applied to this software")

    def optimize_pc(self):
        lines = load_control.optimize(self.store)
        self.load_combo.blockSignals(True)
        self.load_combo.setCurrentIndex(max(0, self.load_combo.findData("auto")))
        self.load_combo.blockSignals(False)
        self.activate_level(load_control.chosen(self.store), sync=True)
        for combo, key in ((self.run_on_combo, "compute_mode"), (self.chunk_combo, "chunk_chars")):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(self.store.setting(key, "auto" if key == "compute_mode" else 0))))
            combo.blockSignals(False)
        for box, key in ((self.guard_check, "guard_on"),):
            box.blockSignals(True)
            box.setChecked(bool(self.store.setting(key, True)))
            box.blockSignals(False)
        self.guard_wait.blockSignals(True)
        self.guard_wait.setValue(45)
        self.guard_wait.blockSignals(False)
        self.guard_wait_label.setText(self.wait_text(45))
        self.sync_guard_controls()
        self.sync_speed_note()
        self.optimize_note.setText("Done. Set for this PC:\n" + "\n".join("  ✔ " + x for x in lines))

    @staticmethod
    def wait_text(n):
        return f"{n} s" if n < 60 else (f"{n // 60} min" if n % 60 == 0 else f"{n // 60} min {n % 60} s")

    def guard_toggled(self, on):
        self.store.set_setting("guard_on", bool(on))
        self.sync_guard_controls()

    def guard_wait_changed(self, n):
        n = int(round(n / 5.0) * 5) if n > 10 else 10
        self.store.set_setting("guard_wait", n)
        self.guard_wait_label.setText(self.wait_text(n))

    def sync_guard_controls(self):
        on = bool(self.store.setting("guard_on", True))
        for w in (self.guard_wait, self.guard_wait_label, self.guard_temp_box):
            w.setEnabled(on)
        self.guard_note.setVisible(not on)

    def limit_changed(self, key, value_label, n):
        value_label.setText(f"{n}%")
        self.store.set_setting(key, n)
        if key == "cpu_limit":
            load_control.set_cpu_limit(n)

    def chunk_changed(self, *_):
        self.store.set_setting("chunk_chars", self.chunk_combo.currentData())
        self.stats_timer.start()

    def set_low_end(self, on):
        self.store.set_setting("low_end_mode", bool(on))
        self.apply_profile("low" if on else "balanced", from_checkbox=True)

    def threads_changed(self, *_):
        from .resource_policy import set_threads_override
        self.store.set_setting("cpu_threads_override", self.threads_combo.currentData())
        set_threads_override(self.threads_combo.currentData())

    def warm_minutes_changed(self, value):
        self.store.set_setting("warm_minutes", int(value))
        warm_worker.set_idle(int(value) * 60)

    def apply_profile(self, name, from_checkbox=False):
        cpu, ram, gpu, jobs, warm, chunk = {"low": (50, 75, 75, 1, False, 200), "balanced": (85, 85, 90, 1, True, 0),
                                            "high": (90, 90, 90, 3, True, 0)}[name]
        self.preload_check.setChecked(name != "low")
        for key, value in (("cpu_limit", cpu), ("ram_limit", ram), ("gpu_limit", gpu)):
            self.limit_sliders[key].setValue(value)
        self.parallel.setValue(jobs)
        self.queue_mode.setCurrentIndex(1 if jobs > 1 else 0)
        self.warm_check.setChecked(warm)
        self.chunk_combo.setCurrentIndex(max(0, self.chunk_combo.findData(chunk)))
        if not from_checkbox:
            self.low_end.blockSignals(True)
            self.low_end.setChecked(name == "low")
            self.low_end.blockSignals(False)
            self.store.set_setting("low_end_mode", name == "low")

    # ---- speed report and speed test
    def show_speed_report(self):
        """Short findings after a finished voice-over (kept in Settings > Performance)."""
        if hasattr(self, "speed_report") and self.studio_job:
            self.speed_report.setText(" ".join(perf.explain(perf.summary(self.studio_job), self.preferred_device())))

    def show_speed_report_dialog(self):
        lines = perf.explain(perf.summary(self.studio_job or ""), self.preferred_device())
        QMessageBox.information(self, "Speed report", "\n\n".join(lines) + "\n\nFull log: " + str(subdir("logs") / "performance.log"))

    def run_speed_test(self):
        if self.preview_busy or self.running or self.busy_installer:
            return self.alert("Wait for the current work to finish, then run the speed test.")
        model, voice = self.model_combo.currentData(), self.voice_combo.currentData()
        try:
            v, reference, ref_text, profile = self.resolve_voice(model, voice)
        except ValueError as exc:
            return self.alert(str(exc))
        base = {"output_speed": 1.0, "pitch_semitones": 0.0, "fast_precision": bool(self.store.setting("fast_precision", False))}
        jobs = [(models()[model]["name"], model, v, reference, ref_text, {**base, **((profile or {}).get("options", {}))})]
        if models()[model]["kind"] == "clone":
            for m in models().values():
                if m["kind"] == "clone" and m["id"] != model and not capabilities.unavailable_reason(m) and self.engine_ready(m["id"]):
                    jobs.append((m["name"], m["id"], "", reference, ref_text, dict(base)))
        if not self.confirm("Speed test", f"This makes the same short sentence twice with {len(jobs)} voice engine(s) and times it. "
                            "It can take a few minutes the first time because each engine has to load."):
            return
        device = self.preferred_device()
        self.preview_busy = True

        def work():
            results = []
            for i, (name, mid, vid, ref, rt, opts) in enumerate(jobs, 1):
                self.bench_progress.emit(f"Testing {name} ({i} of {len(jobs)})…")
                o = dict(opts)
                if mid.startswith("qwen"):
                    o["language"] = "Auto"
                elif models()[mid].get("lang_option"):
                    o["language"] = "English"
                try:
                    results.append((name, perf.benchmark(self.store, mid, vid, ref, rt, o, device), ""))
                except Exception as exc:
                    results.append((name, [], (str(exc).splitlines() or [exc.__class__.__name__])[0][:200]))
            self.bench_done.emit(results)

        threading.Thread(target=work, daemon=True).start()

    def show_bench(self, results):
        self.preview_busy = False
        lines, best = [], None
        for name, runs, error in results:
            if error:
                lines.append(f"{name}\n  Could not test: {error}")
                continue
            second = runs[-1]
            lines.append(f"{name}\n  First run {runs[0]['wall_s']} s · second run {second['wall_s']} s for {second['audio_s']} s of audio\n"
                         f"  {perf.verdict(second['rtf'])}" + (f"  Device: {second['device']}" if second["device"] else ""))
            if second["rtf"] and (best is None or second["rtf"] < best[1]):
                best = (name, second["rtf"])
        if best and len(results) > 1:
            lines.append(f"Fastest on this PC: {best[0]}.")
        self.speed_report.setText("Speed test finished.")
        QMessageBox.information(self, "Speed test", "\n\n".join(lines))

    def clear_samples(self):
        count = preview.clear_cache() + samples.clear_all()
        self._sample_failed.clear()
        self.sample_player.set_note(f"{count} saved sample(s) removed. The Library makes them again by itself.")

    def first_launch(self):
        super().first_launch()
        if os.environ.get("AVS_NO_PROMPTS") or self.store.setting("low_end_prompted", False):
            return
        self.store.set_setting("low_end_prompted", True)
        try:
            import psutil
            weak = psutil.virtual_memory().total < 8 * 1024 ** 3 or (psutil.cpu_count(logical=True) or 1) < 4
        except Exception:
            weak = False
        if weak:
            QTimer.singleShot(1200, self.offer_low_end)

    def offer_low_end(self):
        if self.confirm("Slower PC detected",
                        "This PC has limited memory or processor power.\n\nTurn on Low-end PC mode? Voice-overs take a little longer "
                        "but use much less memory, and long scripts still finish as one audio. You can change it later in Settings."):
            self.low_end.setChecked(True)

    # ------------------------------------------------------------------------- update (simpler confirmation)
    def confirm_local_update(self, result):
        from .update_local import verify_update, upgrade_arguments
        target, declared, actual = result
        try:
            if not self.confirm("Install update",
                                f"Install {target.name}?\n\nYour voices, saved clones and settings are kept. The software closes and the "
                                "installer opens.\n\nOnly continue if this file came from the person who gave you the software."):
                return
            verify_update(target, declared or actual)
            if self.tasks or self.running or self.audit_running or getattr(self, "pending_jobs", []):
                raise RuntimeError("Pause jobs and downloads before updating.")
            from .source_update import backup_state
            from . import warm_worker, external
            args = upgrade_arguments(target)
            backup_state(self.store.file)
            self.log_message("Installing update to " + args[-1][5:])
            warm_worker.release()
            external.popen(args, close_fds=True)
            self.busy_installer = False
            self.close()
            QApplication.instance().quit()
        except Exception as exc:
            self.alert(str(exc))
        finally:
            self.busy_installer = False
            self.update_status.setText("")

    # ------------------------------------------------------------------------- dependency check
    def run_health_check(self):
        if self.audit_running:
            self.health_note.setText("Please wait: the start-up check is still running.")
            return
        self.health_button.setEnabled(False)
        self.health_note.setText("Checking… this can take up to a minute.")

        def work():
            try:
                system = health.system_checks()
                engines = health.engine_checks(self.card_voices)
            except Exception as exc:
                system = [health.Check("Check", "bad", f"The check could not finish: {exc}")]
                engines = []
            self.health_ready.emit(system, engines)

        threading.Thread(target=work, daemon=True).start()

    def show_health(self, system, engines):
        self.health_button.setEnabled(True)
        if not self._update_busy:
            QTimer.singleShot(0, self.start_update_check)        # updates found are shown on the Models page
        everything = list(system) + list(engines)
        good, warn, bad = health.summarize(everything)
        self.health_note.setText(f"{good} OK · {warn} to repair · {bad} missing")
        fix = health.fixable(everything)
        dialog = QDialog(self)
        dialog.setWindowTitle("Software and dependency check")
        dialog.resize(640, 620)
        lay = QVBoxLayout(dialog)
        lay.addWidget(label("Software check", "title"))
        lay.addWidget(label(f"{good} OK · {warn} to repair · {bad} missing", "muted"))
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setSpacing(6)
        symbol = {"ok": "✔", "warn": "⚠", "bad": "✖", "info": "ℹ"}
        colour = {"ok": "#3fbf7f", "warn": "#e0a43a", "bad": "#e0584f", "info": "#7f98ad"}
        for heading, items in (("This PC", system), ("Voice engines", engines)):
            il.addWidget(label(heading, "sectiontitle"))
            for item in items:
                row = QHBoxLayout()
                mark = QLabel(symbol[item.status])
                mark.setStyleSheet(f"color:{colour[item.status]};font-weight:700;")
                mark.setFixedWidth(22)
                name = QLabel(item.name)
                name.setMinimumWidth(190)
                name.setWordWrap(False)
                detail = label(item.detail, "muted")
                row.addWidget(mark)
                row.addWidget(name)
                row.addWidget(detail, 1)
                il.addLayout(row)
        il.addStretch(1)
        area.setWidget(inner)
        lay.addWidget(area, 1)
        buttons = QHBoxLayout()
        todo = fix["python"] or fix["engines"]
        fix_button = QPushButton("Fix problems")
        fix_button.setObjectName("primary")
        fix_button.setEnabled(bool(todo))
        fix_button.clicked.connect(lambda: (dialog.accept(), self.fix_problems(fix)))
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        buttons.addStretch(1)
        buttons.addWidget(fix_button)
        buttons.addWidget(close_button)
        lay.addLayout(buttons)
        dialog.exec()

    def fix_problems(self, fix):
        if self.busy_installer or self.move_pending:
            return self.alert("Finish the current update first.")
        if fix["python"]:
            self.install_runtime()
        for key in fix["engines"]:
            if key in self.setup_cards:
                self.install_card(key)
        if self.access.state("page.setup") == SHOW:
            self.change_page(3)

    # ------------------------------------------------------------------------- Portable Pack
    # ------------------------------------------------------------------------- owner / user controls
    def move_logs_into_settings(self):
        """Logs are the last tab of Settings (not a side-panel page). The old page keeps its place in the stack, empty."""
        if getattr(self, "_logs_moved", False) or not hasattr(self, "settings_tabs") or self.stack.count() < 6:
            return
        page = self.stack.widget(5)
        self.stack.removeWidget(page)
        self.stack.insertWidget(5, QWidget())
        self.settings_tabs.addTab(page, "Logs")
        self._logs_moved = True

    # ---- Python on a fresh Windows PC: ask first, then install it from python.org (no WinGet needed)
    def python_startup_check(self):
        def work():
            try:
                found = bool(managed.runtime_python())
            except Exception:
                found = False
            self.python_checked.emit(found)
        threading.Thread(target=work, daemon=True).start()

    def on_python_checked(self, found):
        if found:
            self._python_ok = True
        else:
            self.ask_install_python()

    def python_ready(self, ask=True):
        """True when Python 3.11 can be used. If not (Windows), offer to install it and answer False for now."""
        if os.name != "nt" or getattr(self, "_python_ok", False):
            return True
        if managed.runtime_python():
            self._python_ok = True
            return True
        if ask:
            self.ask_install_python()
        return False

    def ask_install_python(self):
        if os.environ.get("AVS_NO_PROMPTS") or "runtime" in self.tasks or self.busy_installer or self.running:
            return
        if self.confirm("Python is needed",
                        "The voice engines need Python 3.11, and it is not on this PC yet.\n\nInstall it now? It is downloaded from python.org (about 25 MB), "
                        "installed only for this software in its own folder (no administrator password), and pip is installed with it.\n\n"
                        "You can keep using the software while it installs. When it is finished, press the setup button again."):
            self.start_task("runtime", None, lambda c, p, l: managed.ensure_runtime_serial(l, c), "Preparing Python")

    def install_card(self, key):
        if not self.python_ready():
            return
        return super().install_card(key)

    def show_rollback_notice(self):
        """After an automatic go-back, say what happened (once)."""
        try:
            import avs_boot
            path = avs_boot.updates_dir() / "notice.txt"
            if not path.is_file():
                return
            text = path.read_text(encoding="utf-8").strip()
            path.unlink(missing_ok=True)
        except Exception:
            return
        if text:
            self.toast(text)
            if not os.environ.get("AVS_NO_PROMPTS"):
                QMessageBox.information(self, "Automatic repair", text)

    def apply_access(self):
        owner = self.access.is_owner
        self.mode_badge.setText("Owner" if owner else "Member")
        self.mode_badge.setVisible(not getattr(self.access, "open_to_all", False))
        if hasattr(self, "settings_tabs"):
            backups = self.access.state("set.backup") == SHOW
            self.settings_tabs.setTabVisible(2, backups)
            if not backups and self.settings_tabs.currentIndex() == 2:
                self.settings_tabs.setCurrentIndex(0)
            self.refresh_backup_access()
        self.apply_sidebar()
        for idx, key in PAGE_KEYS.items():
            state = self.access.state(key)
            button_ = self.nav_buttons[idx]
            if idx in (5, 9) and getattr(self.access, "open_to_all", False):      # Logs live in Settings now; the Sources page is not offered
                button_.setVisible(False)
                continue
            button_.setVisible(state != HIDE)
            button_.setEnabled(state == SHOW)
            button_.setToolTip(LOCKED_TIP if state == LOCK else "")
        for key, widgets in self.gates.items():
            state = self.access.state(key)
            for widget in widgets:
                widget.setVisible(state != HIDE)
                widget.setEnabled(state == SHOW)
                self.mark_locked(widget, state == LOCK)
        for key, widgets in self.visible_gates.items():
            for widget in widgets:
                widget.setVisible(self.access.state(key) == SHOW)
        if hasattr(self, "owner_card") and getattr(self.access, "open_to_all", False):
            self.owner_card.setVisible(False)        # nobody manages access any more
        self.sync_studio_controls()
        self.sync_star()
        if hasattr(self, "access_status"):
            counts = self.access.summary()
            who = "Owner key active on this PC" if self.access.key_mode else ("password set" if self.access.has_password else "no password yet")
            self.access_status.setText(f"{who} · for others: {counts[SHOW]} shown, {counts[LOCK]} locked, {counts[HIDE]} hidden")
        current = self.stack.currentIndex()
        key = PAGE_KEYS.get(current)
        if key and self.access.state(key) != SHOW:
            self.change_page(0)

    def ask_password(self, title, text):
        value, ok = QInputDialog.getText(self, title, text, QLineEdit.EchoMode.Password)
        return value if ok else None

    def set_owner_password(self):
        if self.access.has_password:
            current = self.ask_password("Owner password", "Current owner password:")
            if current is None:
                return False
            if not self.access.verify(current):
                QMessageBox.warning(self, "Owner password", "That password is not correct.")
                return False
        first = self.ask_password("Owner password", "New owner password (at least 4 characters):")
        if first is None:
            return False
        again = self.ask_password("Owner password", "Type it again:")
        if again is None:
            return False
        if first != again:
            QMessageBox.warning(self, "Owner password", "The two passwords are not the same.")
            return False
        try:
            self.access.set_password(first)
        except ValueError as exc:
            QMessageBox.warning(self, "Owner password", str(exc))
            return False
        self.apply_access()
        return True

    def toggle_owner_mode(self):
        if getattr(self.access, "open_to_all", False):
            return
        if self.access.key_mode:
            if self.access.is_owner:
                if not self.confirm("Lock this copy", "Switch to User mode now?\n\nEnter your owner key (Owner login, bottom left) to come back to Owner mode."):
                    return
                self.access.lock_this_copy()
                self.apply_access()
                self.change_page(0)
            else:
                key = self.ask_password("Owner login", "Paste your owner key (it starts with AVS-):")
                if key is None:
                    return
                if self.access.login_with_key(key):
                    self.apply_access()
                else:
                    from . import owner as owner_mod
                    detail = getattr(self.access, "last_error", "") or "That key is not correct for this software."
                    QMessageBox.warning(self, "Owner login", detail + f"\n\nThis copy is version {APP_VERSION} and takes its owner key from {owner_mod.key_source()}. "
                                        f"It accepts owner keys with the fingerprint: {self.access.accepted_fingerprints()}.\n\n"
                                        "If the version here is older than the one you installed, install the newest update ZIP (Settings > Updates).")
            return
        if self.access.is_owner:
            if not self.access.has_password and not self.set_owner_password():
                return
            if not self.confirm("Lock for users",
                                "Switch to User mode now?\n\nThe software will start in User mode from now on. "
                                "Use Owner login (bottom left) or Ctrl+Shift+O and your password to get full access again."):
                return
            self.access.lock_for_users()
            self.apply_access()
            self.change_page(0)
        else:
            password = self.ask_password("Owner login", "Owner password:")
            if password is None:
                return
            if self.access.enter_owner(password):
                self.apply_access()
            else:
                QMessageBox.warning(self, "Owner login", "That password is not correct.")

    def start_owner_mode(self):
        self.access.start_in_owner_mode()
        self.access_status.setText("Next start: Owner mode. " + self.access_status.text())

    def footer_changed(self):
        self.access.set_footer(self.footer_edit.text())
        self.footer.setText(self.access.footer_text(DEFAULT_FOOTER))

    def apply_preset(self):
        self.access.apply_preset(self.preset_combo.currentData())
        self.apply_access()

    def open_feature_manager(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose what users can see and use")
        dialog.resize(660, 740)
        lay = QVBoxLayout(dialog)
        lay.addWidget(label("User access", "title"))
        lay.addWidget(label("These rules apply in User mode. The owner always sees everything. "
                            "Locked items are visible but greyed out. Hidden items are not shown at all.", "muted"))
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        form_layout = QVBoxLayout(inner)
        form_layout.setSpacing(8)
        combos = {}
        group = None
        grid = None
        for key, group_name, title, hint in FEATURES:
            if group_name != group:
                group = group_name
                form_layout.addWidget(label(group, "sectiontitle"))
                grid = QFormLayout()
                grid.setHorizontalSpacing(14)
                form_layout.addLayout(grid)
            combo = QComboBox()
            for state in STATES:
                combo.addItem(STATE_LABELS[state], state)
            combo.setCurrentIndex(STATES.index(self.access.configured(key)))
            combo.currentIndexChanged.connect(partial(self.feature_changed, key, combo))
            name = QLabel(title + (f"  ·  {hint}" if hint else ""))
            name.setWordWrap(True)
            grid.addRow(name, combo)
            combos[key] = combo
        form_layout.addStretch(1)
        area.setWidget(inner)
        lay.addWidget(area, 1)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(dialog.accept)
        lay.addWidget(done)
        dialog.exec()
        self.apply_access()

    def feature_changed(self, key, combo, *_):
        self.access.set_feature(key, combo.currentData())
        self.apply_access()

    # ------------------------------------------------------------------------- closing
    def work_in_progress(self):
        """Plain words for what is still going on ('' when nothing is)."""
        items = []
        if self.running or getattr(self, "pending_jobs", []):
            items.append("a voice-over is being made")
        if self.tasks:
            items.append("a model download or setup is running")
        if self.pack_busy:
            items.append("a backup is being made")
        if self.preloading or self.sample_building or self.preview_busy:
            items.append("voice samples are being prepared (optional)")
        return ", ".join(items)

    def stop_everything_fast(self):
        """Tell every worker to stop and end the helper processes at once. Finished parts and downloaded files are kept."""
        from . import services, warm_worker
        try:
            for job_id in list(self.running) + list(getattr(self, "pending_jobs", [])):
                flag = self.stop_flags.get(job_id)
                if flag:
                    flag.set()
                services.stop_job(job_id)
            self.pending_jobs = []
            for task in list(self.tasks.values()):
                if task.get("cancel") is not None:
                    task["cancel"].set()
            if self.pack_cancel:
                self.pack_cancel.set()
        except Exception:
            pass
        event = getattr(self, "_sample_stop", None)
        if event is not None:
            event.set()
        warm_worker.kill_now()
        try:
            import psutil
            for child in psutil.Process().children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
        except Exception:
            pass

    def closeEvent(self, event):
        busy = self.work_in_progress()
        self._closing_started = True
        if busy and not os.environ.get("AVS_NO_PROMPTS") and not self.busy_installer:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Something is still in progress")
            box.setText(f"Work is still going on: {busy}.")
            box.setInformativeText("Stop it and close now? Finished parts and downloaded files are kept, so you can continue later.")
            stop = box.addButton("Stop and close", QMessageBox.ButtonRole.DestructiveRole)
            keep = box.addButton("Keep working", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is not stop:
                event.ignore()
                return
        try:
            import avs_boot
            avs_boot.mark_good()                 # a clean close proves this version started fine
        except Exception:
            pass
        from . import shutdown
        if busy and not self.busy_installer:
            self.stop_everything_fast()
            event.accept()
            shutdown.leave_now(0.3)          # leave quickly: nothing is waited for, every helper process ends with us
            return
        if self.pack_busy and self.pack_cancel:
            if not self.confirm("Portable Pack in progress", "A Portable Pack is still being created. Cancel it and close?"):
                event.ignore()
                return
            self.pack_cancel.set()
        super().closeEvent(event)
        if event.isAccepted():
            shutdown.leave_now(0.4)          # idle close: the loaded voice engine and every helper process end now, so the PC is free at once


# ================================================================================ word check (methods added to the window)
def _run_word_check_job(self, job):
    """Check a finished voice-over in the background. Result: a message, a report file next to the audio, and a note in the Queue."""
    out = Path(job["output"]) if job.get("output") else None
    if not out or not is_audio(out):
        return
    script = job["script"]
    threads = max(1, min(8, load_control.thread_cap("work")))

    def work():
        try:
            result = wordcheck.check_audio(script, out, threads)
        except Exception as exc:
            result = {"error": str(exc)[:300], "perfect": False, "script_words": 0, "accuracy": 0, "missing": [], "extra": [], "changed": []}
        self.word_checked.emit(job["id"], result)

    self.toast("Checking the spoken words…")
    threading.Thread(target=work, daemon=True).start()


def _on_word_checked(self, jid, result):
    from PySide6.QtWidgets import QPlainTextEdit
    job = self.store.job(jid) if jid else None
    report = None
    if job and not result.get("error"):
        try:
            out = Path(job["output"])
            report = out.with_suffix(".wordcheck.txt")
            report.write_text(wordcheck.report_text(result, out.name), encoding="utf-8")
            options = json.loads(job["options"] or "{}")
            options["word_check_result"] = {"accuracy": result["accuracy"], "perfect": result["perfect"], "summary": wordcheck.summary_line(result)}
            self.store.set_job_options(jid, options)
            self.refresh_queue()
        except Exception:
            report = None
    if os.environ.get("AVS_NO_PROMPTS"):
        return
    dialog = QDialog(self)
    dialog.setWindowTitle("Word check")
    dialog.resize(720, 520)
    lay = QVBoxLayout(dialog)
    lay.addWidget(label(wordcheck.summary_line(result), "sectiontitle"))
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(wordcheck.report_text(result, job["title"] if job else ""))
    lay.addWidget(box, 1)
    row = QHBoxLayout()
    if report:
        row.addWidget(button("Open report", lambda: self.open_path(report)))
    row.addStretch(1)
    row.addWidget(button("Close", dialog.accept, True))
    lay.addLayout(row)
    dialog.setModal(False)
    dialog.show()
    self._word_dialog = dialog


StudioWindow.run_word_check_job = _run_word_check_job
StudioWindow.on_word_checked = _on_word_checked


def _build_check_page(self):
    """Separate page for testing: pick an audio file, paste or load the script, see which words were not spoken."""
    from PySide6.QtWidgets import QPlainTextEdit

    def build(lay):
        lay.addWidget(label("Check words", "title"))
        lay.addWidget(label("Find out if every word of your script was spoken. The audio is turned back into text and compared with the script. "
                            "It only runs when you press the button.", "muted"))
        frame, fl = card("Audio and script")
        form = new_form(110)
        self.check_audio_edit = QLineEdit()
        self.check_audio_edit.setPlaceholderText("Choose the finished voice-over (WAV, MP3, M4A …)")
        pick = QHBoxLayout()
        pick.addWidget(self.check_audio_edit, 1)
        pick.addWidget(button("Choose audio…", self.check_pick_audio))
        pick.addWidget(button("Last voice-over", self.check_use_last))
        holder = QWidget()
        holder.setLayout(pick)
        pick.setContentsMargins(0, 0, 0, 0)
        form.addRow(field("Audio"), holder)
        fl.addLayout(form)
        self.check_script = QPlainTextEdit()
        self.check_script.setPlaceholderText("Paste the script here, or load it from a text file.")
        self.check_script.setMinimumHeight(180)
        fl.addWidget(self.check_script)
        fl.addLayout(actions(button("Load script file…", self.check_load_script), button("Use Studio text", lambda: self.check_script.setPlainText(self.script.toPlainText()))))
        self.check_status = label("", "muted")
        self.check_button = button("Check spoken words", self.run_check_page, True)
        self.check_button.setMinimumHeight(42)
        fl.addWidget(self.check_button)
        fl.addWidget(self.check_status)
        lay.addWidget(frame)
        frame, fl = card("Result")
        self.check_result = QPlainTextEdit()
        self.check_result.setReadOnly(True)
        self.check_result.setMinimumHeight(220)
        fl.addWidget(self.check_result)
        lay.addWidget(frame)
        lay.addStretch(1)

    from .ui_extra import scroll_page
    return scroll_page(build)


def _check_pick_audio(self):
    path, _ = QFileDialog.getOpenFileName(self, "Choose the voice-over audio", str(Path.home()), "Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac);;All files (*)")
    if path:
        self.check_audio_edit.setText(path)


def _check_use_last(self):
    for job in self.store.jobs():
        if job["status"] == "completed" and job.get("output") and is_audio(Path(job["output"])):
            self.check_audio_edit.setText(job["output"])
            self.check_script.setPlainText(job["script"])
            return
    self.check_status.setText("There is no finished voice-over yet.")


def _check_load_script(self):
    path, _ = QFileDialog.getOpenFileName(self, "Choose the script", str(Path.home()), DOC_FILTER)
    if path:
        try:
            self.check_script.setPlainText(read_script(Path(path)))
        except Exception as exc:
            self.check_status.setText(str(exc)[:200])


def _run_check_page(self):
    audio = Path(self.check_audio_edit.text().strip().strip('"'))
    script = self.check_script.toPlainText()
    if not audio.is_file():
        return self.check_status.setText("Choose an audio file first.")
    if not script.strip():
        return self.check_status.setText("Paste or load the script first.")
    if not wordcheck.installed():
        return self.check_status.setText("The Word checker is not installed. Open Models, find 'Word checker' and press Download.")
    self.check_button.setEnabled(False)
    self.check_status.setText("Listening to the audio… this takes a little while.")
    threads = max(1, min(8, load_control.thread_cap("work")))

    def work():
        try:
            result = wordcheck.check_audio(script, audio, threads)
        except Exception as exc:
            result = {"error": str(exc)[:300], "perfect": False, "script_words": 0, "accuracy": 0, "missing": [], "extra": [], "changed": []}
        self.word_checked.emit("", result)
        QTimer.singleShot(0, lambda: None)

    self._check_page_pending = True
    threading.Thread(target=work, daemon=True).start()


_orig_on_word_checked = _on_word_checked


def _on_word_checked_page(self, jid, result):
    if not jid and getattr(self, "_check_page_pending", False):
        self._check_page_pending = False
        self.check_button.setEnabled(True)
        self.check_status.setText(wordcheck.summary_line(result))
        self.check_result.setPlainText(wordcheck.report_text(result, Path(self.check_audio_edit.text()).name))
        return
    _orig_on_word_checked(self, jid, result)


StudioWindow.build_check_page = _build_check_page
StudioWindow.check_pick_audio = _check_pick_audio
StudioWindow.check_use_last = _check_use_last
StudioWindow.check_load_script = _check_load_script
StudioWindow.run_check_page = _run_check_page
StudioWindow.on_word_checked = _on_word_checked_page
