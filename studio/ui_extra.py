"""Voice Design, Voice Training and Voice Sources pages (mixed into the main window)."""
from __future__ import annotations
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .ui import label, button
from .core import models
from . import clones, design, training, sources, capabilities, managed
from .voice_catalog import QWEN_LANG

AUDIO_FILTER = "Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.mp4 *.wma);;All files (*)"


def scroll_page(build):
    """Wrap a page built by build(layout) in a scroll area with the standard margins."""
    content = QWidget()
    content.setObjectName("page")
    lay = QVBoxLayout(content)
    lay.setContentsMargins(22, 18, 22, 22)
    lay.setSpacing(14)
    build(lay)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(content)
    return area


class ExtraPages:
    # ------------------------------------------------------------------ Voice Design (page 7)
    def make_design_page(self):
        from .ui_v5 import card, field, new_form, actions, MiniPlayer
        from .widgets import DropPick

        def build(lay):
            lay.addWidget(label("Design", "title"))
            lay.addWidget(label("Create a new voice from a description. Pick a starting point, choose from the lists, listen, then save it.", "muted"))
            columns = QHBoxLayout()
            columns.setSpacing(16)
            left, right = QVBoxLayout(), QVBoxLayout()
            left.setSpacing(14)
            right.setSpacing(14)
            columns.addLayout(left, 1)
            columns.addLayout(right, 1)
            lay.addLayout(columns)

            frame, fl = card("Start from")
            self.design_start = DropPick(list(design.TEMPLATES), "Choose a starting point…")
            self.design_start.changed.connect(self.design_start_chosen)
            fl.addWidget(self.design_start)
            left.addWidget(frame)

            frame, fl = card("Shape the voice")
            self.design_chips = {}
            shape = new_form(110)
            for group, options in design.CHOICES.items():
                picker = DropPick([label_ for label_, _p in options], "Any")
                picker.changed.connect(self.compose_design)
                shape.addRow(field(group), picker)
                self.design_chips[group] = picker
            fl.addLayout(shape)
            left.addWidget(frame)

            frame, fl = card("Description", "You can edit this text freely. It is what the AI reads.")
            self.design_text = QPlainTextEdit()
            self.design_text.setPlaceholderText("Example: " + design.EXAMPLES[0])
            self.design_text.setFixedHeight(104)
            fl.addWidget(self.design_text)
            fl.addLayout(actions(button("Clear", self.design_clear), button("Example", self.design_example)))
            left.addWidget(frame)
            left.addStretch(1)

            frame, fl = card("Listen")
            form = new_form()
            self.design_sample = QPlainTextEdit("Welcome. This is how my new voice sounds when it reads your script.")
            self.design_sample.setFixedHeight(78)
            self.design_language = QComboBox()
            for language in QWEN_LANG:
                self.design_language.addItem(language, language)
            self.design_feeling = QLineEdit()
            self.design_feeling.setPlaceholderText("Optional, for example: whispering")
            form.addRow(field("Text"), self.design_sample)
            form.addRow(field("Speak in"), self.design_language)
            form.addRow(field("Feeling"), self.design_feeling)
            fl.addLayout(form)
            self.design_button = button("▶  Create sample", self.create_design_sample, True)
            self.design_button.setMinimumHeight(42)
            fl.addWidget(self.design_button)
            self.design_player = MiniPlayer(self)
            fl.addWidget(self.design_player)
            self.design_status = label("", "muted")
            fl.addWidget(self.design_status)
            right.addWidget(frame)

            frame, fl = card("Keep this voice",
                             "A designed voice changes a little every time. Saving it as a reusable voice keeps it identical across your whole script.")
            form = new_form()
            self.design_name = QLineEdit()
            self.design_name.setPlaceholderText("Name, for example: Deep British narrator")
            self.design_engine = QComboBox()
            form.addRow(field("Name"), self.design_name)
            form.addRow(field("Save with"), self.design_engine)
            fl.addLayout(form)
            fl.addLayout(actions(button("Save voice", self.save_design, True)))
            self.design_saved = label("", "muted")
            fl.addWidget(self.design_saved)
            right.addWidget(frame)
            right.addStretch(1)

        self.design_sample_path = None
        self._design_use = ""
        self.stack.addWidget(scroll_page(build))

    def refresh_design_engines(self):
        if not hasattr(self, "design_engine"):
            return
        self.design_engine.clear()
        for m in models().values():
            if m["kind"] == "clone" and "ref_text" in m["supports"] and not capabilities.unavailable_reason(m):
                self.design_engine.addItem(m["name"].split(" · ")[0].replace(" (experimental)", ""), m["id"])
        for m in models().values():
            if m["id"] == "xtts2":
                self.design_engine.addItem("XTTS-v2", "xtts2")

    def compose_design(self, *_):
        selected = {group: chips.value() for group, chips in self.design_chips.items()}
        text = design.compose_from_chips(selected, self._design_use)
        if text:
            self.design_text.setPlainText(text)

    def design_start_chosen(self):
        name = self.design_start.value()
        if name:
            self.design_template(name)

    def design_template(self, name):
        choices, use = design.TEMPLATES[name]
        self._design_use = use
        for group, chips in self.design_chips.items():
            chips.set_value(choices.get(group, ""))
        self.compose_design()

    def design_clear(self):
        self._design_use = ""
        if hasattr(self, "design_start"):
            self.design_start.set_value("")
        for chips in self.design_chips.values():
            chips.set_value("")
        self.design_text.clear()

    def design_example(self):
        import random
        self.design_text.setPlainText(random.choice(design.EXAMPLES))

    def create_design_sample(self):
        if self.preview_busy:
            return
        try:
            description = design.valid_description(self.design_text.toPlainText())
        except ValueError as exc:
            self.design_status.setText(str(exc))
            return
        if self.busy_installer or self.move_pending:
            self.design_status.setText("Finish the current update first.")
            return
        sample = " ".join(self.design_sample.toPlainText().split()) or "Welcome. This is how my new voice sounds."
        options = {"voice_description": description, "language": self.design_language.currentData(),
                   "emotion_text": self.design_feeling.text().strip()}
        device = self.preferred_device()
        self.preview_busy = True
        self.design_button.setEnabled(False)
        self.design_status.setText("Creating the voice… the first time can take a few minutes.")
        self.design_sample_text = sample
        self.design_description = description

        def work():
            from . import preview
            try:
                path = preview.make_preview(self.store, "qwen17bdesign", "", "", "", options, text=sample, device=device)
                self.preview_ready.emit(str(path), "design")
            except Exception as exc:
                self.preview_failed.emit(str(exc) or exc.__class__.__name__, "design")

        threading.Thread(target=work, daemon=True).start()

    def save_design(self):
        path = getattr(self, "design_sample_path", None)
        if not path:
            self.design_saved.setText("Create a sample first.")
            return
        name = self.design_name.text().strip()
        if not name:
            self.design_saved.setText("Give the voice a name.")
            return
        model = self.design_engine.currentData()
        if not model:
            self.design_saved.setText("No cloning engine is available to keep the voice.")
            return
        try:
            clones.save_profile(self.store, "Designed · " + name, model, path, self.design_sample_text, True, {},
                                style_notes=self.design_description)
        except Exception as exc:
            self.design_saved.setText(str(exc))
            return
        self.refresh_library()
        self.refresh_voices()
        self.design_saved.setText("Saved. Find it under Cloned voices in Voice Studio and in the Voice Library. "
                                  "Download the matching engine in AI Setup if you have not yet.")

    # ------------------------------------------------------------------ Voice Training (page 8)
    def make_training_page(self):
        from .ui_v5 import card, field, new_form, actions

        def build(lay):
            lay.addWidget(label("Voice Training", "title"))
            lay.addWidget(label("Prepare clear recordings of one speaker. Build a voice from them right away, or export a dataset for fine-tuning. "
                                "Only use voices you have permission to use.", "muted"))
            frame, fl = card("Project")
            self.train_combo = QComboBox()
            self.train_combo.currentIndexChanged.connect(self.refresh_training)
            fl.addLayout(actions(self.train_combo, button("New project", self.new_training), button("Delete project", self.delete_training)))
            lay.addWidget(frame)

            frame, fl = card("Recordings")
            fl.addLayout(actions(button("Add recordings…", self.add_training_files, True), button("Add a folder…", self.add_training_folder),
                                 button("Remove selected", self.remove_training_clip)))
            self.train_bar = QProgressBar()
            self.train_bar.setRange(0, 1000)
            self.train_bar.hide()
            fl.addWidget(self.train_bar)
            self.train_table = QTableWidget(0, 5)
            self.train_table.setHorizontalHeaderLabels(["File", "Seconds", "Level", "Check", "Transcript (type what is said)"])
            h = self.train_table.horizontalHeader()
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            self.train_table.verticalHeader().hide()
            self.train_table.setMinimumHeight(260)
            self.train_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.train_table.itemChanged.connect(self.training_text_changed)
            fl.addWidget(self.train_table)
            self.train_summary = label("", "muted")
            fl.addWidget(self.train_summary)
            lay.addWidget(frame)

            frame, fl = card("Build a voice from these recordings",
                             "Picks the cleanest clips, joins them into one reference, and saves it as a voice you can use at once.")
            form = new_form()
            self.train_name = QLineEdit()
            self.train_name.setPlaceholderText("Voice name")
            self.train_engine = QComboBox()
            form.addRow(field("Voice name"), self.train_name)
            form.addRow(field("Save using"), self.train_engine)
            fl.addLayout(form)
            fl.addLayout(actions(button("Build voice", self.build_training_voice, True)))
            self.train_result = label("", "muted")
            fl.addWidget(self.train_result)
            lay.addWidget(frame)

            frame, fl = card("Export for fine-tuning",
                             "Writes the recordings and transcripts in the standard layout for the chosen trainer. "
                             "This app does not run the fine-tuning itself; use a computer with a strong NVIDIA graphics card.")
            self.export_engine = QComboBox()
            self.export_engine.addItem("XTTS-v2 (22.05 kHz)", "xtts")
            self.export_engine.addItem("StyleTTS 2 (24 kHz)", "styletts2")
            fl.addLayout(actions(self.export_engine, button("Export dataset…", self.export_training, True)))
            lay.addWidget(frame)
            lay.addStretch(1)

        self.stack.addWidget(scroll_page(build))
        self.refresh_training_projects()

    def refresh_training_engines(self):
        if not hasattr(self, "train_engine"):
            return
        self.train_engine.clear()
        for m in models().values():
            if m["kind"] == "clone" and not capabilities.unavailable_reason(m):
                self.train_engine.addItem(m["name"].split(" · ")[0].replace(" (experimental)", ""), m["id"])

    def refresh_training_projects(self, select=None):
        keep = select or self.train_combo.currentData()
        self.train_combo.blockSignals(True)
        self.train_combo.clear()
        for p in training.projects():
            self.train_combo.addItem(p["name"], p["id"])
        if not self.train_combo.count():
            self.train_combo.addItem("No project yet · press New project", None)
        self.train_combo.setCurrentIndex(max(0, self.train_combo.findData(keep)))
        self.train_combo.blockSignals(False)
        self.refresh_training()

    def current_training(self):
        return self.train_combo.currentData()

    def refresh_training(self, *_):
        pid = self.current_training()
        table = self.train_table
        table.blockSignals(True)
        table.setRowCount(0)
        if not pid:
            self.train_summary.setText("Create a project to begin.")
            table.blockSignals(False)
            return
        project = training.load(pid)
        table.setRowCount(len(project["clips"]))
        for i, clip in enumerate(project["clips"]):
            issues = training.clip_issues(clip)
            cells = [clip["name"], f"{clip['seconds']:.1f}", f"{clip['rms_dbfs']:.0f} dB", "OK" if not issues else ", ".join(issues), clip.get("text", "")]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c != 4:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setData(Qt.ItemDataRole.UserRole, clip["id"])
                table.setItem(i, c, item)
            table.item(i, 0).setData(Qt.ItemDataRole.UserRole, clip["id"])
        table.blockSignals(False)
        r = training.report(pid)
        self.train_summary.setText(f"{r['clips']} clips · {r['minutes']} min · {r['with_text']} with transcript. {r['verdict']} ({r['note']})")

    def new_training(self):
        name, ok = QInputDialog.getText(self, "New voice project", "Project name:")
        if ok and name.strip():
            try:
                p = training.create(name)
            except ValueError as exc:
                return self.alert(str(exc))
            self.refresh_training_projects(p["id"])

    def delete_training(self):
        pid = self.current_training()
        if pid and self.confirm("Delete project", "Delete this project and its prepared recordings? Your original files are not touched."):
            training.delete(pid)
            self.refresh_training_projects()

    def add_training_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose recordings", str(Path.home()), AUDIO_FILTER)
        if paths:
            self.run_training_import(paths)

    def add_training_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of recordings", str(Path.home()))
        if folder:
            exts = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".wma"}
            self.run_training_import([str(p) for p in sorted(Path(folder).iterdir()) if p.suffix.lower() in exts])

    def run_training_import(self, paths):
        pid = self.current_training()
        if not pid:
            return self.alert("Create a project first.")
        if getattr(self, "train_busy", False) or not paths:
            return
        self.train_busy = True
        self.train_bar.setValue(0)
        self.train_bar.show()

        def work():
            try:
                result = training.add_clips(pid, paths, lambda f, n: self.train_progress.emit(f, n))
            except Exception as exc:
                result = ([], [str(exc)])
            self.train_done.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def on_train_progress(self, fraction, name):
        self.train_bar.setValue(int(fraction * 1000))
        self.train_summary.setText("Preparing " + name[-60:])

    def on_train_done(self, result):
        self.train_busy = False
        self.train_bar.hide()
        self.refresh_training()
        added, problems = result
        if problems:
            self.train_summary.setText(f"{len(added)} added. Skipped: " + "; ".join(problems)[:300])

    def remove_training_clip(self):
        pid, row = self.current_training(), self.train_table.currentRow()
        item = self.train_table.item(row, 0) if row >= 0 else None
        if pid and item:
            training.remove_clip(pid, item.data(Qt.ItemDataRole.UserRole))
            self.refresh_training()

    def training_text_changed(self, item):
        if item.column() == 4 and self.current_training():
            training.set_text(self.current_training(), item.data(Qt.ItemDataRole.UserRole), item.text())
            r = training.report(self.current_training())
            self.train_summary.setText(f"{r['clips']} clips · {r['minutes']} min · {r['with_text']} with transcript. {r['verdict']}")

    def build_training_voice(self):
        pid, model = self.current_training(), self.train_engine.currentData()
        name = self.train_name.text().strip()
        if not pid or not model or not name:
            self.train_result.setText("Choose a project, a voice name and an engine first.")
            return
        try:
            wav, text = training.build_reference(pid)
            clones.save_profile(self.store, name, model, wav, text, True, {}, style_notes="Built from the Voice Training project")
        except Exception as exc:
            self.train_result.setText(str(exc))
            return
        self.refresh_library()
        self.refresh_voices()
        self.train_result.setText("Voice saved. It is now under Cloned voices in Voice Studio and in the Voice Library.")

    def export_training(self):
        pid = self.current_training()
        if not pid:
            return self.alert("Create a project first.")
        folder = QFileDialog.getExistingDirectory(self, "Where should the dataset be saved?", str(Path.home()))
        if not folder:
            return
        try:
            out = training.export_dataset(pid, self.export_engine.currentData(), Path(folder))
        except Exception as exc:
            return self.alert(str(exc))
        self.train_summary.setText(f"Dataset saved to {out}")
        self.open_path(out)

    # ------------------------------------------------------------------ Voice Sources (page 9)
    def make_sources_page(self):
        from .ui_v5 import card, actions

        def build(lay):
            lay.addWidget(label("Voice Sources", "title"))
            lay.addWidget(label("Add a GitHub or Hugging Face repository for a voice model. It is checked online for licence, tags, size and "
                                "updates. Checking never runs the repository's code.", "muted"))
            frame, fl = card()
            self.source_input = QLineEdit()
            self.source_input.setPlaceholderText("https://github.com/owner/name  or  https://huggingface.co/owner/name")
            self.source_input.returnPressed.connect(self.add_source)
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(self.source_input, 1)
            row.addWidget(button("Add and check", self.add_source, True))
            row.addWidget(button("Check all now", self.check_sources))
            fl.addLayout(row)
            self.source_builtin = QCheckBox("Also list the sources behind the built-in engines")
            self.source_builtin.toggled.connect(lambda _: self.refresh_sources())
            fl.addWidget(self.source_builtin)
            self.source_auto = QCheckBox("Check the saved sources automatically about once a week")
            self.source_auto.setChecked(bool(self.store.setting("sources_auto_check", True)))
            self.source_auto.toggled.connect(lambda on: self.store.set_setting("sources_auto_check", bool(on)))
            fl.addWidget(self.source_auto)
            self.source_status = label("", "muted")
            fl.addWidget(self.source_status)
            lay.addWidget(frame)
            self.source_table = QTableWidget(0, 6)
            self.source_table.setHorizontalHeaderLabels(["Repository", "Site", "Licence", "Result", "Updated", ""])
            h = self.source_table.horizontalHeader()
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for c in (1, 2, 4):
                h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            h.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            self.source_table.setColumnWidth(5, 250)
            self.source_table.verticalHeader().hide()
            self.source_table.verticalHeader().setDefaultSectionSize(46)
            self.source_table.setMinimumHeight(420)
            self.source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            lay.addWidget(self.source_table)
            lay.addWidget(label("Only engines this app has an adapter for can make voices. Standard VITS voices can be added as voices; other "
                                "repositories are reported honestly and kept for later.", "muted"))

        self.source_reports = {}
        self.stack.addWidget(scroll_page(build))

    def fetch_source(self, url):
        return managed.fetch_json(url)

    def refresh_sources(self):
        if not hasattr(self, "source_table"):
            return
        rows = list(sources.saved())
        have = {(r["kind"], r["repo"].lower()) for r in rows}
        for b in (sources.builtin() if self.source_builtin.isChecked() else []):
            if (b["kind"], b["repo"].lower()) not in have:
                rows.append({**self.source_reports.get((b["kind"], b["repo"].lower()), {}), **b, "builtin": True})
        table = self.source_table
        table.setRowCount(len(rows))
        names = {"hf": "Hugging Face", "github": "GitHub"}
        for i, r in enumerate(rows):
            built = bool(r.get("builtin"))
            result = r.get("verdict_text") or ("Built-in engine source. Press Check all now." if built else "")
            if r.get("notes"):
                result += "  " + " ".join(r["notes"])
            cells = [r["repo"], names[r["kind"]], r.get("license") or ("—" if built and not r.get("license_class") else "not stated"),
                     result, str(r.get("updated", ""))[:10]]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                table.setItem(i, c, item)
            cell = QWidget()
            hl = QHBoxLayout(cell)
            hl.setContentsMargins(4, 3, 4, 3)
            hl.setSpacing(6)
            link = QPushButton("Details")
            link.clicked.connect(lambda _=False, rep=r: self.show_source_details(rep))
            hl.addWidget(link)
            if r.get("verdict") == "importable":
                use = QPushButton("Add as voice")
                use.setObjectName("primary")
                use.clicked.connect(lambda _=False, repo=r["repo"]: self.import_source_voice(repo))
                hl.addWidget(use)
            if not built:
                rm = QPushButton("Remove")
                rm.clicked.connect(lambda _=False, k=r["kind"], repo=r["repo"]: (sources.remove(k, repo), self.refresh_sources()))
                hl.addWidget(rm)
            table.setCellWidget(i, 5, cell)

    def show_source_details(self, r):
        """Everything known about a source, shown inside the app."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        names = {"hf": "Hugging Face", "github": "GitHub"}
        url = r.get("url") or sources.url_for(r["kind"], r["repo"])
        lines = [f"Repository: {r['repo']}", f"Site: {names[r['kind']]}", f"Address: {url}"]
        for key, title in (("license", "Licence"), ("license_text", ""), ("verdict_text", "Result"), ("updated", "Last update"), ("size_gb", "Weights (GB)"),
                           ("downloads", "Downloads"), ("stars", "Stars"), ("engine", "Used by model")):
            if r.get(key) not in (None, "", []):
                lines.append(f"{title + ': ' if title else ''}{r[key]}")
        if r.get("tags"):
            lines.append("Tags: " + ", ".join(r["tags"]))
        for note in r.get("notes", []):
            lines.append("Note: " + note)
        d = QDialog(self)
        d.setWindowTitle("Source details")
        d.resize(620, 400)
        v = QVBoxLayout(d)
        box = QPlainTextEdit("\n".join(lines))
        box.setReadOnly(True)
        v.addWidget(box)
        row = QHBoxLayout()
        copy = QPushButton("Copy address")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(url))
        close = QPushButton("Close")
        close.clicked.connect(d.accept)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        d.exec()

    def add_source(self):
        value = self.source_input.text().strip()
        if not value:
            return
        try:
            sources.parse_source(value)
        except ValueError as exc:
            self.source_status.setText(str(exc))
            return
        self.source_status.setText("Checking online…")
        self.run_source_job(lambda: ("add", sources.add(value, self.fetch_source)))

    def check_sources(self):
        self.source_status.setText("Checking all sources online… this can take a minute.")

        def job():
            changes = sources.recheck_all(self.fetch_source)
            reports = {}
            for b in sources.builtin():
                try:
                    reports[(b["kind"], b["repo"].lower())] = sources.check(b["kind"], b["repo"], self.fetch_source)
                except Exception:
                    pass
            return "all", (changes, reports)

        self.run_source_job(job)

    def run_source_job(self, job):
        def work():
            try:
                self.sources_ready.emit(job())
            except Exception as exc:
                self.sources_ready.emit(("error", str(exc)[:300] or exc.__class__.__name__))

        threading.Thread(target=work, daemon=True).start()

    def on_sources_ready(self, result):
        kind, data = result
        if kind == "error":
            self.source_status.setText("Could not check: " + data)
        elif kind == "add":
            self.source_input.clear()
            self.source_status.setText(f"{data['repo']}: {data['verdict_text']}")
        elif kind == "auto":
            self.source_status.setText("Weekly check done. " + ("; ".join(data) if data else "No changes."))
        else:
            changes, reports = data
            self.source_reports.update({k: v for k, v in reports.items()})
            self.source_status.setText("Check done. " + ("; ".join(changes) if changes else "No changes in saved sources."))
        self.refresh_sources()

    def auto_check_sources(self):
        if not sources.auto_due(self.store) or not sources.saved():
            return
        import time as _t
        self.store.set_setting("sources_last_auto", _t.time())
        self.run_source_job(lambda: ("auto", sources.recheck_all(self.fetch_source)))

    def import_source_voice(self, repo):
        self.hf_input.setText(repo)
        self.add_hf_model()
        self.source_status.setText("Adding the voice… see AI Setup. Restart the software to see it in the voice lists.")
