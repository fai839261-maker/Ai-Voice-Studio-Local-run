"""Settings > Backup and Settings > Updates (update files, GitHub updates, go back)."""
from __future__ import annotations
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from .ui import label, button
from .core import APP_VERSION, subdir
from . import backup, updater, modelfind, permits
from .owner import PRESETS

PUBLISH_STEPS = ("To share a new version: 1) press Make an update file. 2) On GitHub open your repository, choose Releases, then Create a new release "
                 "and give it a tag such as v{version}. 3) Attach the update file (AVS_Update_{version}.zip). 4) Publish the release. "
                 "Everyone then presses Check for updates.")


def gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


def size_text(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.2f} MB"


class _StageBridge(QObject):
    """Carries the answer of the background update check back to the window (so the window never freezes)."""
    finished = Signal(object, object)       # (info, error)
    trust = Signal(str)                     # ask about a new publisher (signed update files only)


class UpdatePages:
    # ================================================================== Backup
    def build_backup_card(self) -> QFrame:
        from .ui_v5 import card, actions
        frame, lay = card("Backup", "Your settings and saved voices are saved automatically. Make a full copy to keep, or to use on another PC.")
        self.bk_models = QCheckBox("Also include downloaded AI models and engines")
        self.bk_models.toggled.connect(lambda _on: self.update_backup_size())
        self.bk_sizes = label("", "muted")
        lay.addWidget(self.bk_models)
        lay.addWidget(self.bk_sizes)
        self.bk_owner = button("Owner backup", lambda: self.run_backup("owner"), True)
        self.bk_others = button("Backup for others", lambda: self.run_backup("others"))
        self.bk_complete = button("Complete backup", lambda: self.run_backup("complete"))
        self.bk_restore = button("Restore selected", self.restore_selected)
        self.bk_cancel = button("Cancel", self.cancel_pack)
        self.bk_cancel.setEnabled(False)
        self.gate("access.manager", self.bk_others)
        self.gate("access.manager", self.bk_complete)
        lay.addLayout(actions(self.bk_owner, self.bk_others, self.bk_complete, self.bk_restore, self.bk_cancel))
        self.bk_help = label("Owner backup: everything of yours. Backup for others: the software with your installed update, and your chosen settings. Complete backup: the setup file "
                             "and settings in one folder; on the new PC double-click Install_Everything.bat.", "muted")
        lay.addWidget(self.bk_help)
        self.pack_bar = QProgressBar()
        self.pack_bar.setRange(0, 1000)
        self.pack_bar.hide()
        self.pack_note = label("", "muted")
        lay.addWidget(self.pack_bar)
        lay.addWidget(self.pack_note)
        self.bk_list = QListWidget()
        self.bk_list.setMinimumHeight(280)
        lay.addWidget(self.bk_list)
        lay.addLayout(actions(button("Open backups folder", lambda: self.open_path(backup.backups_dir()))))
        self.gate("set.backup", frame)
        return frame

    def pick_installer(self):
        """The setup file (.exe made by the build) that goes into a Complete backup. Remembered after the first time."""
        saved = Path(str(self.store.setting("installer_path", "") or ""))
        if saved.is_file():
            return saved
        found = backup.find_installers()
        if found and self.confirm("Setup file", f"Use this setup file?\n\n{found[0]}"):
            self.store.set_setting("installer_path", str(found[0]))
            self.refresh_owner_card()
            return found[0]
        path, _ = QFileDialog.getOpenFileName(self, "Choose the setup file", str(Path.home()), "Setup file (*.exe)")
        if not path:
            return None
        self.store.set_setting("installer_path", path)
        self.refresh_owner_card()
        return Path(path)

    def choose_installer(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the setup file", str(Path.home()), "Setup file (*.exe)")
        if path:
            self.store.set_setting("installer_path", path)
            self.refresh_owner_card()

    def ask_permit(self):
        """Should the person who receives this backup be able to make backups? Returns a signed permit, None (no) or False (cancelled)."""
        box = QMessageBox(self)
        box.setWindowTitle("Backups on the other PC")
        box.setText("Can the person who receives this make backups too?")
        box.setInformativeText("Yes attaches your key. Without it they cannot make backups at all.")
        yes = box.addButton("Yes, attach my key", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("No", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is no:
            return None
        if clicked is not yes:
            return False
        secret = self.access.owner_secret
        if secret is None:
            text = self.ask_password("Owner key", "Paste your owner key to sign the permit:")
            if text is None:
                return False
            try:
                from .owner import decode_key
                from . import ed25519
                secret = decode_key(text)
                if ed25519.public_key(secret).hex() != self.access.key_public:
                    raise ValueError("That key is not the owner key of this software.")
            except ValueError as exc:
                QMessageBox.warning(self, "Owner key", str(exc))
                return False
            self.access.owner_secret = secret
        who, ok = QInputDialog.getText(self, "Who is it for?", "A name for your own records (optional):")
        if not ok:
            return False
        return permits.make(secret, who)

    def build_owner_card(self) -> QFrame:
        from .ui_v5 import card, actions
        frame, lay = card("Owner controls", "What the people you give this software to can use. They cannot change anything else.")
        self.access_status = label("", "muted")
        lay.addWidget(self.access_status)
        self.preset_combo = QComboBox()
        for key, (text, _rules) in PRESETS.items():
            self.preset_combo.addItem(text, key)
        lay.addLayout(actions(self.preset_combo, button("Apply preset", self.apply_preset)))
        lay.addLayout(actions(button("Choose what others can use…", self.open_feature_manager, True), button("Choose setup file…", self.choose_installer)))
        self.installer_note = label("", "muted")
        lay.addWidget(self.installer_note)
        self.gate("access.manager", frame)
        self.owner_card = frame
        self.refresh_owner_card()
        return frame

    def refresh_owner_card(self):
        if hasattr(self, "installer_note"):
            saved = Path(str(self.store.setting("installer_path", "") or ""))
            self.installer_note.setText("Setup file for Complete backups: " + (saved.name if saved.is_file() else "not chosen yet"))

    def refresh_backup_access(self):
        if hasattr(self, "bk_owner"):
            self.bk_owner.setText("Owner backup" if self.access.is_owner else "Backup")
            self.bk_help.setVisible(self.access.is_owner)

    def refresh_backup_list(self):
        if not hasattr(self, "bk_list"):
            return
        self.bk_list.clear()
        rows = backup.list_backups()
        if not rows:
            empty = QListWidgetItem("No backups yet. Automatic backups of your settings and voices appear here.")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            self.bk_list.addItem(empty)
        for row in rows:
            extra = " · with models" if row["models"] else ""
            item = QListWidgetItem(f"{row['created']}    {row['label']}    {size_text(row['size'])}{extra}")
            item.setData(256, str(row["path"]))
            item.setData(257, row["kind"])
            self.bk_list.addItem(item)

    def update_backup_size(self):
        self.bk_sizes.setText("Counting…")

        def work():
            self.bk_sizes_ready.emit(backup.extra_sizes())

        threading.Thread(target=work, daemon=True).start()

    def on_backup_sizes(self, sizes):
        total = sizes["models"] + sizes["engines"]
        self.bk_sizes.setText(f"Adds about {gb(total)} total: models {gb(sizes['models'])}, engines {gb(sizes['engines'])}." if self.bk_models.isChecked()
                              else f"Downloaded models and engines use about {gb(total)} (not included unless you tick the box).")

    def installed_model_rows(self):
        """[(key, name, bytes)] for every model that is downloaded on this PC."""
        from .core import models as catalog_models
        rows = []
        base = subdir("models")
        for key, entry in catalog_models().items():
            folder = base / key
            if not folder.is_dir():
                continue
            try:
                size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".avs-"))
            except OSError:
                size = 0
            if size > 1_000_000:
                rows.append((key, entry["name"].split(" · ")[0], size))
        return rows

    def choose_backup_models(self):
        """Ask which AI models go with the backup. Returns the chosen keys (maybe none), or None if cancelled."""
        rows = self.installed_model_rows()
        if not rows:
            return []
        if os.environ.get("AVS_NO_PROMPTS"):
            return [r[0] for r in rows]
        from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QVBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("Which AI models go with this backup?")
        dialog.resize(560, 460)
        lay = QVBoxLayout(dialog)
        lay.addWidget(label("Which AI models should be attached?", "sectiontitle"))
        lay.addWidget(label("Tick the models to include. On the other PC the software finds them by itself, prepares what they need and works after a restart. "
                            "Models you leave out are not in the backup (they can be downloaded there later).", "muted"))
        listing = QListWidget()
        for key, name, size in rows:
            item = QListWidgetItem(f"{name}   ·   {gb(size)}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(256, key)
            listing.addItem(item)
        lay.addWidget(listing, 1)
        total = label("", "muted")
        lay.addWidget(total)

        def refresh():
            chosen = sum(rows[i][2] for i in range(listing.count()) if listing.item(i).checkState() == Qt.CheckState.Checked)
            total.setText(f"Models in this backup: {gb(chosen)}" if chosen else "No models: the backup stays small.")

        def mark(state):
            for i in range(listing.count()):
                listing.item(i).setCheckState(state)
            refresh()

        listing.itemChanged.connect(lambda *_: refresh())
        refresh()
        row = QHBoxLayout()
        row.addWidget(button("Attach all", lambda: mark(Qt.CheckState.Checked)))
        row.addWidget(button("Attach none", lambda: mark(Qt.CheckState.Unchecked)))
        row.addStretch(1)
        row.addWidget(button("Cancel", dialog.reject))
        row.addWidget(button("Make backup", dialog.accept, True))
        lay.addLayout(row)
        if not dialog.exec():
            return None
        return [listing.item(i).data(256) for i in range(listing.count()) if listing.item(i).checkState() == Qt.CheckState.Checked]

    def run_backup(self, kind):
        if self.pack_busy:
            return
        if self.running or self.tasks or self.busy_installer:
            return self.alert("Finish generation and downloads before making a backup.")
        if kind in ("others", "complete") and not self.access.is_owner:
            return self.toast("Only the owner can make a backup for other people.")
        keys = self.choose_backup_models()
        if keys is None:
            return
        models = bool(keys)
        installer = permit = None
        if kind == "complete":
            installer = self.pick_installer()
            if not installer:
                return
        if kind in ("others", "complete"):
            permit = self.ask_permit()
            if permit is False:
                return
        total = backup.plan_backup(kind, models, installer=installer, model_keys=keys)
        if total > 2e9 and not self.confirm("Make backup", f"This backup is about {gb(total)} and can take a while. Keep this window open until it says Done."):
            return
        self.pack_busy = True
        self.pack_cancel = threading.Event()
        self.pack_bar.setValue(0)
        self.pack_bar.show()
        for b in (self.bk_owner, self.bk_others, self.bk_complete, self.bk_restore):
            b.setEnabled(False)
        self.bk_cancel.setEnabled(True)
        self.pack_note.setText("Starting…")

        def work():
            try:
                def progress(fraction, name):
                    now = time.monotonic()
                    if now - self._last_pack_emit > 0.25:
                        self._last_pack_emit = now
                        self.pack_progress.emit(fraction, name)
                path = backup.create_backup(kind, models, progress, self.pack_cancel, installer=installer, permit=permit or None, model_keys=keys)
                self.pack_done.emit(str(path))
            except backup.Cancelled:
                self.pack_failed.emit("Cancelled. Nothing was saved.")
            except Exception as exc:
                self.pack_failed.emit(str(exc))

        threading.Thread(target=work, daemon=True).start()

    def cancel_pack(self):
        if self.pack_cancel:
            self.pack_cancel.set()
            self.pack_note.setText("Cancelling…")

    def finish_pack_ui(self):
        self.pack_busy = False
        for b in (self.bk_owner, self.bk_others, self.bk_complete, self.bk_restore):
            b.setEnabled(True)
        self.bk_cancel.setEnabled(False)

    def on_pack_progress(self, fraction, name):
        self.pack_bar.setValue(int(fraction * 1000))
        self.pack_note.setText(f"{fraction * 100:.0f}%  ·  {name[-70:]}")

    def on_pack_done(self, path):
        self.finish_pack_ui()
        self.pack_bar.setValue(1000)
        self.pack_note.setText(f"Done: {path}")
        self.refresh_backup_list()
        box = QMessageBox(self)
        box.setWindowTitle("Backup ready")
        box.setText("Your backup is ready.")
        box.setInformativeText(path)
        open_button = box.addButton("Show in folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            self.open_path(Path(path).parent)

    def on_pack_failed(self, message):
        self.finish_pack_ui()
        self.pack_bar.hide()
        self.pack_note.setText(message)

    def restore_selected(self):
        item = self.bk_list.currentItem()
        if not item:
            return self.pack_note.setText("Choose a backup in the list first.")
        path, kind = Path(item.data(256)), item.data(257)
        warning = ("This backup was made for ANOTHER PC. Restoring it here replaces your saved voices and script history with the shared settings. "
                   if kind == "others" else "")
        if not self.confirm("Restore backup", warning + "Your current settings and saved voices are replaced by this backup (a safety copy is kept). "
                                               "The program restarts to finish."):
            return
        try:
            backup.create_backup("auto", note="before restore")
            backup.stage_restore(path)
        except Exception as exc:
            return self.alert(str(exc))
        self.restart_app()

    def auto_backup_now(self):
        if not self.access.is_owner or self.pack_busy:
            return

        def work():
            try:
                if backup.auto_snapshot(self.store):
                    self.backups_changed.emit()
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    # ================================================================== Find models
    def find_models(self):
        if self.busy_installer or self.tasks:
            return self.alert("Wait until downloads finish first.")
        self.toast("Looking for model files on this PC and your drives…")

        def work():
            try:
                self.models_found.emit(modelfind.candidates())
            except Exception:
                self.models_found.emit([])

        threading.Thread(target=work, daemon=True).start()

    def on_models_found(self, found):
        choices = [f"{c['path']}   ({', '.join(c['found'])})" for c in found] + ["Choose a folder myself…"]
        text, ok = QInputDialog.getItem(self, "Find models", "Model files found. Which folder do you want to connect?" if found else
                                        "No model files were found automatically. Plug in the drive, or choose the folder yourself.", choices, 0, False)
        if not ok:
            return
        if text == choices[-1]:
            folder = QFileDialog.getExistingDirectory(self, "Choose the folder that holds models (or its Data folder)", str(Path.home()))
            if not folder:
                return
            source = Path(folder)
            for candidate in (source, source / "Data"):
                if modelfind.looks_like_data(candidate):
                    source = candidate
                    break
            else:
                return self.alert("That folder has no model files (a models or wheels folder).")
        else:
            source = found[choices.index(text)]["path"]
        info = modelfind.plan(source)
        if not info["files"]:
            return self.alert("Everything in that folder is already on this PC.")
        if not self.confirm("Connect models", f"Copy {info['files']} files ({info['bytes'] / 1e9:.2f} GB)? Files you already have are kept."):
            return
        self.pack_cancel = threading.Event()

        def work():
            try:
                count = modelfind.connect(source, lambda f, n: self.models_progress.emit(f, n), self.pack_cancel)
                self.models_connected.emit(count)
            except modelfind.Cancelled:
                self.models_connected.emit("Cancelled.")
            except Exception as exc:
                self.models_connected.emit(str(exc))

        self.toast("Connecting models… this can take a while.")
        threading.Thread(target=work, daemon=True).start()

    def on_models_progress(self, fraction, name):
        self.statusBar().showMessage(f"Connecting models… {fraction * 100:.0f}%  {name[-50:]}") if hasattr(self, "statusBar") else None

    def on_models_connected(self, result):
        if isinstance(result, int):
            for key in list(getattr(self, "setup_cards", {})):
                try:
                    self.refresh_card(key)
                except Exception:
                    pass
            self.refresh_voices()
            self.refresh_library()
            self.toast(f"{result} model files connected. Open Models and press Set up where a voice still asks for it.")
        else:
            self.alert(str(result))

    # ================================================================== Updates
    def build_update_cards(self) -> list[QFrame]:
        from .ui_v5 import card, actions
        main, ml = card("Updates")
        self.upd_check_btn = button("Check for updates", self.check_updates, True)
        ml.addLayout(actions(self.upd_check_btn, button("Install update file…", self.install_update_file)))
        self.upd_status = label("", "")
        ml.addWidget(self.upd_status)
        self.upd_get = button("Download and install", self.download_and_install, True)
        self.upd_get.hide()
        self.upd_bar = QProgressBar()
        self.upd_bar.setRange(0, 1000)
        self.upd_bar.hide()
        ml.addWidget(self.upd_get)
        ml.addWidget(self.upd_bar)
        self.upd_restart = QFrame()
        self.upd_restart.setObjectName("cardinner")
        rl = QHBoxLayout(self.upd_restart)
        self.upd_restart_text = label("", "")
        rl.addWidget(self.upd_restart_text, 1)
        rl.addWidget(button("Restart now", self.restart_app, True))
        self.upd_restart.hide()
        ml.addWidget(self.upd_restart)
        self.gate("set.update", main)

        source, sl = card("Automatic checking")
        self.upd_repo = QLineEdit(updater.update_repo())          # the address is built into the program (kept only for older code)
        self.upd_repo.hide()
        sl.addWidget(label(f"You have version {APP_VERSION}. Updates come from github.com/{updater.update_repo()}. Nothing is installed without you pressing the button.", "muted"))
        self.upd_auto = QCheckBox("Look for updates automatically once a day")
        self.upd_auto.setChecked(bool(self.store.setting("update_auto_check", True)))
        self.upd_auto.toggled.connect(lambda on: self.store.set_setting("update_auto_check", bool(on)))
        sl.addWidget(self.upd_auto)
        return [main, source]

    def upd_log_add(self, text):
        if hasattr(self, "upd_log"):
            self.upd_log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {text}")

    def refresh_updates_view(self):
        if not hasattr(self, "upd_restart"):
            return
        job = updater.pending()
        if job:
            what = {"install": f"Version {job.get('version', '')} is ready.", "rollback": "Going back is ready.", "restore": "The backup restore is ready."}.get(job.get("action"), "A change is ready.")
            self.upd_restart_text.setText(what + " Restart to finish.")
        self.upd_restart.setVisible(bool(job))

    def save_update_source(self):
        text = self.upd_repo.text().strip()
        try:
            repo = updater.clean_repo(text) if text else ""
        except updater.UpdateError as exc:
            return self.upd_status.setText(str(exc))
        self.store.set_setting("update_repo", repo)
        self.upd_repo.setText(repo)
        self.upd_status.setText("Saved. Press Check for updates to test it." if repo else "Update source cleared.")

    # ---- checking GitHub
    def check_updates(self, silent: bool = False):
        self._silent_check = silent
        if not silent:
            self.upd_status.setText("Checking GitHub…")
            self.upd_get.hide()

        def work():
            try:
                result = updater.check_online(self.store)
            except Exception as exc:
                result = {"status": "error", "message": str(exc)[:200] or exc.__class__.__name__}
            self.update_checked.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def on_update_checked(self, result):
        silent, self._silent_check = getattr(self, "_silent_check", False), False
        status = result.get("status")
        release = result.get("release")
        if status == "newer":
            self._release = release
            self.upd_status.setText(f"Update available: version {release['version']}")
            self.upd_get.setText(f"Download and install version {release['version']}")
            self.upd_get.show()
            self.settings_tabs.setTabText(3, "Updates •")
            self.upd_log_add(f"Version {release['version']} found on GitHub.")
            if silent:
                self.toast(f"Update {release['version']} is available. Open Settings > Updates.")
        elif silent:
            return
        elif status == "current":
            self.upd_status.setText("Already latest update installed.")
            self.settings_tabs.setTabText(3, "Updates")
        elif status == "none":
            self.upd_status.setText("Already latest update installed.")
            self.upd_log_add("The latest GitHub release has no update file attached.")
            self.settings_tabs.setTabText(3, "Updates")
        elif status == "no_repo":
            self.upd_status.setText("No update address is set.")
        else:
            self.upd_status.setText("Could not check: " + result.get("message", "unknown problem") + ". Check the internet connection and the GitHub address.")
            self.upd_log_add("Check failed: " + result.get("message", ""))

    def download_and_install(self):
        release = getattr(self, "_release", None)
        if not release:
            return
        self.upd_get.setEnabled(False)
        self.upd_bar.setValue(0)
        self.upd_bar.show()
        self.upd_status.setText(f"Downloading version {release['version']}…")

        def work():
            try:
                path = updater.download_release(release, subdir("updates") / "downloads", lambda f: self.update_progress.emit(f))
                self.update_downloaded.emit(str(path))
            except Exception as exc:
                self.update_downloaded.emit("ERROR:" + (str(exc) or exc.__class__.__name__)[:200])

        threading.Thread(target=work, daemon=True).start()

    def on_update_progress(self, fraction):
        self.upd_bar.setValue(int(fraction * 1000))

    def on_update_downloaded(self, path):
        self.upd_get.setEnabled(True)
        self.upd_bar.hide()
        if path.startswith("ERROR:"):
            self.upd_status.setText(path[6:])
            return self.upd_log_add(path[6:])
        self.upd_log_add("Downloaded " + Path(path).name)
        self.install_file(Path(path))

    # ---- update files
    def install_update_file(self):
        """Choose a ZIP file or an extracted folder; the software checks it and installs it after a restart."""
        box = QMessageBox(self)
        box.setWindowTitle("Install update")
        box.setText("Where is the update?")
        zip_button = box.addButton("ZIP file…", QMessageBox.ButtonRole.AcceptRole)
        folder_button = box.addButton("Folder…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        path = ""
        if clicked is zip_button:
            path, _ = QFileDialog.getOpenFileName(self, "Choose the update ZIP file", str(Path.home()), "ZIP files (*.zip)")
        elif clicked is folder_button:
            path = QFileDialog.getExistingDirectory(self, "Choose the extracted update folder", str(Path.home()))
        if path:
            self.install_file(Path(path))

    def confirm_publisher(self, fingerprint: str) -> bool:
        return self.confirm("Trust this publisher?", "This is the first signed update you are installing. Only continue if the person who gave you this software "
                            f"told you this code:\n\n{fingerprint}\n\nLater signed updates must come from the same publisher.")

    def install_file(self, path: Path):
        """Check the update in the background (the window stays alive), then restart by itself with no question."""
        if getattr(self, "_staging", False):
            return
        self._staging = True
        self.upd_get.hide()
        self.upd_status.setText("Checking the update files… the window stays free. This can take up to a minute.")
        self.upd_bar.setRange(0, 0)                      # moving bar: it is working
        self.upd_bar.show()
        self.upd_check_btn.setEnabled(False)
        bridge = _StageBridge(self)
        self._stage_bridge = bridge
        gate = threading.Event()
        answer = {"ok": False}

        def ask(fingerprint):                            # runs on the window thread
            answer["ok"] = bool(self.confirm_publisher(fingerprint))
            gate.set()

        def trust(fingerprint):                          # runs in the helper thread
            gate.clear()
            bridge.trust.emit(fingerprint)
            gate.wait(600)
            return answer["ok"]

        bridge.trust.connect(ask, Qt.ConnectionType.QueuedConnection)
        bridge.finished.connect(self._install_checked, Qt.ConnectionType.QueuedConnection)

        def work():
            try:
                info = updater.stage_any(self.store, Path(path), trust)
            except BaseException as exc:                 # reported in the window, never lost
                bridge.finished.emit(None, exc)
            else:
                bridge.finished.emit(info, None)

        threading.Thread(target=work, daemon=True, name="avs-check-update").start()

    def _install_checked(self, info, error):
        self._staging = False
        self.upd_bar.setRange(0, 1000)
        self.upd_bar.hide()
        self.upd_check_btn.setEnabled(True)
        if error is not None:
            if isinstance(error, updater.UpdateError):
                text = str(error)
            else:
                text = "The update could not be checked: " + str(error)[:200]
            self.upd_status.setText(text)
            if not os.environ.get("AVS_NO_PROMPTS"):
                QMessageBox.warning(self, "Update", text)
            return
        self.upd_status.setText(f"Version {info['version']} is checked ({info['files']} files). Restarting by itself…")
        self.refresh_updates_view()
        if os.environ.get("AVS_NO_PROMPTS"):
            return
        # No question: the file was checked, so the software restarts at once. Work in progress is stopped; finished parts are kept.
        if hasattr(self, "stop_everything_fast") and self.work_in_progress():
            self.stop_everything_fast()
        QTimer.singleShot(700, self.restart_app)

    def make_update_file(self):
        notes, ok = QInputDialog.getMultiLineText(self, "Make an update file", "What changed in this version? (optional, people see the first line)")
        if not ok:
            return
        folder = QFileDialog.getExistingDirectory(self, "Where should the update file be saved?", str(Path.home()))
        if not folder:
            return
        try:
            path = updater.make_update_file(self.store, Path(folder), notes)
        except Exception as exc:
            self.upd_log_add("✖ " + str(exc))
            return self.alert(str(exc))
        self.upd_log_add(f"✔ Update file saved: {path}")
        self.upd_log_add(PUBLISH_STEPS.format(version=APP_VERSION))
        self.refresh_updates_view()
        self.open_path(path.parent)

    def go_back(self):
        item = self.upd_history.currentItem()
        if not item:
            return self.upd_status.setText("Choose a version in the list first.")
        if not self.confirm("Go back", f"Go back to {item.text().split('    ')[0].lower()}? Your voices and settings are kept. The software restarts."):
            return
        try:
            updater.request_rollback(item.data(256))
        except updater.UpdateError as exc:
            return self.alert(str(exc))
        self.upd_log_add("Going back is prepared.")
        self.restart_app()

    def restart_app(self):
        """Start the program again (it applies the prepared change first) and close this copy completely."""
        from . import shutdown
        try:
            import avs_boot
            avs_boot.mark_good()
        except Exception:
            pass
        flags = [a for a in sys.argv[1:] if a != "--restarted"]
        command = [sys.executable] + ([] if getattr(sys, "frozen", False) else [str(Path(sys.argv[0]).resolve())]) + flags + ["--restarted"]
        try:
            shutdown.cleanup()                               # end the voice engines and every helper first
            shutdown.spawn_free(command, close_fds=True)     # the new copy is free: it is not ended with this one
        except OSError as exc:
            return self.alert("Could not restart automatically: " + str(exc) + ". Close the software and open it again.")
        QApplication.instance().quit()                       # the shutdown helper ends this copy at once, even if a thread is stuck

    def start_update_timers(self):
        from . import shutdown
        shutdown.install()
        try:
            from . import limiter
            limiter.start(self.store, log=lambda text: self.log_message(text) if hasattr(self, 'log_message') else None)
        except Exception:
            pass
        self.auto_backup_timer = QTimer(self)
        self.auto_backup_timer.setInterval(10 * 60 * 1000)
        self.auto_backup_timer.timeout.connect(self.auto_backup_now)
        self.auto_backup_timer.start()
        if not os.environ.get("AVS_NO_PROMPTS"):
            QTimer.singleShot(90_000, self.auto_backup_now)
            if updater.auto_check_due(self.store):
                QTimer.singleShot(15_000, lambda: self.check_updates(True))
