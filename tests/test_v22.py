import os as _os
_os.environ.setdefault("AVS_OWNER_KEY_FILE", "none")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import os, sys, json, tempfile, subprocess, unittest, hashlib, io
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["AI_VOICE_STUDIO_HOME"] = tempfile.mkdtemp()
os.environ["AVS_NO_PROMPTS"] = "1"


def layer(folder: Path, version: str):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "AVS_UPDATE.json").write_text(json.dumps({"version": version, "api": 1, "files": {}}), encoding="utf-8")


def run_boot(home: Path, code: str) -> str:
    env = {**os.environ, "AI_VOICE_STUDIO_HOME": str(home), "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-c", "import sys,json;sys.path.insert(0,r'%s');import avs_boot;%s" % (ROOT, code)], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class Boot(unittest.TestCase):
    def test_only_three_old_versions_are_kept(self):
        home = Path(tempfile.mkdtemp())
        code = ("import time\nfrom pathlib import Path\n"
                "for v in ['9.0.1','9.0.2','9.0.3','9.0.4','9.0.5']:\n"
                " d=avs_boot.overlay_dir();d.mkdir(parents=True,exist_ok=True);(d/'AVS_UPDATE.json').write_text(json.dumps({'version':v,'api':1,'files':{}}))\n"
                " avs_boot.archive_current('test');time.sleep(1.1)\n"
                "print(sorted(avs_boot.read_manifest(p)['version'] for p in avs_boot.history_dir().iterdir()))")
        self.assertEqual(run_boot(home, code), "['9.0.3', '9.0.4', '9.0.5']")

    def test_a_version_that_will_not_start_goes_back_one_version(self):
        home = Path(tempfile.mkdtemp())
        layer(home / "app_overlay", "9.0.3")
        layer(home / "updates" / "history" / "9.0.2-20260101-000002", "9.0.2")
        layer(home / "updates" / "history" / "9.0.1-20260101-000001", "9.0.1")
        (home / "updates").mkdir(exist_ok=True)
        (home / "updates" / "boot.json").write_text(json.dumps({"unconfirmed": 2}))
        out = run_boot(home, "m=avs_boot.activate();print(m['version'] if m else None)")
        self.assertEqual(out, "9.0.2")                                                   # one version before, not the original program
        self.assertTrue(list((home / "updates" / "quarantine").iterdir()))               # the bad one is kept aside, not lost
        self.assertIn("went back to version 9.0.2", (home / "updates" / "notice.txt").read_text())
        self.assertEqual(sorted(p.name[:5] for p in (home / "updates" / "history").iterdir()), ["9.0.1"])

    def test_without_an_older_version_the_original_program_runs(self):
        home = Path(tempfile.mkdtemp())
        layer(home / "app_overlay", "9.0.3")
        (home / "updates").mkdir(exist_ok=True)
        (home / "updates" / "boot.json").write_text(json.dumps({"unconfirmed": 2}))
        self.assertEqual(run_boot(home, "m=avs_boot.activate();print(m)"), "None")
        self.assertFalse((home / "app_overlay").exists())

    def test_a_working_version_is_left_alone(self):
        home = Path(tempfile.mkdtemp())
        layer(home / "app_overlay", "9.0.3")
        out = run_boot(home, "m=avs_boot.activate();avs_boot.mark_good();print(m['version'], avs_boot._boot_state())")
        self.assertEqual(out, "9.0.3 {'unconfirmed': 0}")


class Online(unittest.TestCase):
    def fake(self, version, digest=None):
        asset = {"name": f"AI_Voice_Studio_UPDATE_{version.replace('.', '_')}.zip", "browser_download_url": "https://github.com/x/y/releases/download/v/a.zip", "size": 10}
        if digest:
            asset["digest"] = "sha256:" + digest
        return lambda url: {"tag_name": "v" + version, "html_url": "https://github.com/x/y", "body": "notes", "assets": [asset, {"name": "Source code (zip)"}]}

    def test_the_address_is_built_in(self):
        from studio import updater
        os.environ.pop("AVS_UPDATE_REPO", None)
        self.assertEqual(updater.update_repo(), "fai839261-maker/Ai-Voice-Studio-Local-run")
        seen = []
        store = type("S", (), {"setting": lambda s, k, d=None: d, "set_setting": lambda s, k, v: None})()
        updater.check_online(store, lambda url: seen.append(url) or {"assets": []})
        self.assertIn("repos/fai839261-maker/Ai-Voice-Studio-Local-run/releases/latest", seen[0])

    def test_newer_current_and_none(self):
        from studio import updater
        store = type("S", (), {"setting": lambda s, k, d=None: d, "set_setting": lambda s, k, v: None})()
        self.assertEqual(updater.check_online(store, self.fake("99.0.0"))["status"], "newer")
        self.assertEqual(updater.check_online(store, self.fake("0.0.1"))["status"], "current")
        self.assertEqual(updater.check_online(store, lambda u: {"assets": [{"name": "readme.txt"}]})["status"], "none")
        self.assertEqual(updater.check_online(store, self.fake("99.0.0", "ab" * 32))["release"]["sha256"], "ab" * 32)

    def download(self, data, digest):
        from studio import updater, downloads

        class Resp(io.BytesIO):
            headers = {"Content-Length": str(len(data))}

            def geturl(self):
                return "https://objects.githubusercontent.com/file"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        release = {"name": "AI_Voice_Studio_UPDATE_99_0_0.zip", "url": "https://github.com/x/y/releases/download/v/a.zip", "size": len(data), "sha256": digest}
        with patch.object(downloads, "open_secure", return_value=Resp(data)):
            return updater.download_release(release, Path(tempfile.mkdtemp()))

    def test_a_download_must_match_the_published_checksum(self):
        from studio import updater
        data = b"hello update"
        self.assertTrue(self.download(data, hashlib.sha256(data).hexdigest()).is_file())
        with self.assertRaises(updater.UpdateError):
            self.download(data, "0" * 64)
        self.assertTrue(self.download(data, "").is_file())                              # GitHub gave no checksum: still allowed


try:
    import qtstub  # noqa: F401
    from PySide6.QtWidgets import QApplication, QLineEdit
    HAVE_QT = True
except Exception:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "Qt not available")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        cls.w = StudioWindow()
        cls.w.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_no_owner_login_and_everything_is_open(self):
        w = self.w
        self.assertTrue(w.owner_button.isHidden() or not w.owner_button.isVisible())
        self.assertFalse(w.mode_badge.isVisible())
        for idx in (0, 1, 2, 3, 4, 6, 7, 8, 10, 11):
            self.assertTrue(w.nav_buttons[idx].isEnabled(), idx)
            self.assertFalse(w.nav_buttons[idx].isHidden(), idx)
        self.assertEqual(w.access.state("page.channels"), "show")
        w.toggle_owner_mode()                                                           # does nothing, asks nothing
        self.assertTrue(w.access.is_owner)
        self.assertFalse(w.owner_card.isVisible())

    def test_sources_and_logs_left_the_side_panel(self):
        w = self.w
        self.assertTrue(w.nav_buttons[9].isHidden())
        self.assertTrue(w.nav_buttons[5].isHidden())
        self.assertEqual(w.stack.count(), 12)                                           # page numbers did not shift
        self.assertEqual(w.settings_tabs.tabText(w.settings_tabs.count() - 1), "Logs")  # last tab of Settings
        self.assertIsNotNone(w.settings_tabs.widget(w.settings_tabs.count() - 1))

    def test_update_messages(self):
        w = self.w
        w.on_update_checked({"status": "newer", "release": {"version": "99.1.0", "notes": "long notes that must not show", "name": "x", "url": "u"}})
        self.assertEqual(w.upd_status.text(), "Update available: version 99.1.0")
        self.assertIn("99.1.0", w.upd_get.text())
        w.on_update_checked({"status": "current"})
        self.assertEqual(w.upd_status.text(), "Already latest update installed.")
        self.assertFalse(w.upd_repo.isVisible())                                        # nothing to type: the address is built in
        w.upd_status.setText("")
        calls = []
        with patch("studio.updater.check_online", side_effect=lambda store: calls.append(1) or {"status": "current"}):
            w.check_updates()
            import time
            for _ in range(50):
                self.app.processEvents()
                time.sleep(0.05)
                if calls:
                    break
        self.assertTrue(calls)                                                          # works with no address saved

    def test_voice_samples_only_when_asked(self):
        w = self.w
        self.assertFalse(w.store.setting("auto_samples", False))
        self.assertFalse(w.samples_check.isChecked())
        built = []
        with patch("studio.favorites.all_voices", side_effect=lambda *a, **k: built.append(1) or []):
            w.scan_samples()                                                            # the timer: must not even look
            self.assertFalse(built)
            w.prepare_samples_now()                                                     # you pressed the button
            self.assertTrue(built)
        for _ in range(20):
            self.app.processEvents()
        self.assertFalse(w.sample_building)
        self.assertEqual(w.work_in_progress(), "")

    def test_one_switch_for_automatic_samples(self):
        w = self.w
        scans = []
        w.scan_samples = lambda force=False: scans.append(force)
        self.assertFalse(w.lib_auto_samples.isChecked())                              # off by default, on the Library page too
        w.lib_auto_samples.setChecked(True)
        self.assertTrue(w.store.setting("auto_samples", False))
        self.assertTrue(w.samples_check.isChecked())                                  # Settings shows the same state
        w.samples_check.setChecked(False)
        self.assertFalse(w.store.setting("auto_samples", True))
        self.assertFalse(w.lib_auto_samples.isChecked())
        self.assertIn("OFF", w.samples_note.text())
        del w.scan_samples

    def test_the_close_question_names_the_work(self):
        w = self.w
        for flag in ("sample_building", "preloading", "preview_busy"):
            setattr(w, flag, True)
            try:
                self.assertEqual(w.work_in_progress(), "")                   # optional background work never blocks closing
                self.assertTrue(w.optional_work())
            finally:
                setattr(w, flag, False)
        w.running = {"job": object()}
        try:
            self.assertIn("voice-over", w.work_in_progress())                # real work still asks
        finally:
            w.running = {}

    def test_notice_after_automatic_repair(self):
        import avs_boot
        w = self.w
        (avs_boot.updates_dir() / "notice.txt").write_text("Version 9 did not start properly, so the software went back to version 8 by itself.")
        w.show_rollback_notice()
        self.assertFalse((avs_boot.updates_dir() / "notice.txt").exists())


if __name__ == "__main__":
    unittest.main()
