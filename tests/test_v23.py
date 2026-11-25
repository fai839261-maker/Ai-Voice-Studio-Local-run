import os as _os
_os.environ.setdefault("AVS_OWNER_KEY_FILE", "none")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import os, sys, tempfile, unittest, types, io
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["AI_VOICE_STUDIO_HOME"] = tempfile.mkdtemp()
os.environ["AVS_NO_PROMPTS"] = "1"

PAGE_LATEST = "https://github.com/o/r/releases/tag/v1.2.3"
ASSETS = '''
<a href="/o/r/releases/download/v1.2.3/AI_Voice_Studio_COMPLETE_1_2_3.zip" rel="nofollow">x</a>
<span class="Truncate-text">sha256:%s</span>
<a href="/o/r/releases/download/v1.2.3/Other%%20file.txt" rel="nofollow">y</a>
<a href="/o/r/archive/refs/tags/v1.2.3.zip" rel="nofollow">z</a>
''' % ("ab" * 32)


class Resp(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class GitHubFallback(unittest.TestCase):
    def fake_open(self, req, timeout=0):
        url = req.full_url
        if url.endswith("/releases/latest"):
            return Resp(b"", PAGE_LATEST)
        if "expanded_assets/v1.2.3" in url:
            return Resp(ASSETS.encode(), url)
        raise AssertionError(url)

    def test_rate_limit_falls_back_to_the_web_pages(self):
        from studio import updater, downloads
        with patch.object(downloads, "fetch_json", side_effect=Exception("HTTP Error 403: rate limit exceeded")), patch.object(downloads, "open_secure", side_effect=self.fake_open):
            release = updater.latest_release("o/r")
        self.assertEqual(release["version"], "1.2.3")
        self.assertEqual(release["name"], "AI_Voice_Studio_COMPLETE_1_2_3.zip")
        self.assertEqual(release["sha256"], "ab" * 32)
        self.assertTrue(release["url"].startswith("https://github.com/o/r/releases/download/v1.2.3/"))

    def test_if_both_fail_the_first_message_is_kept(self):
        from studio import updater, downloads
        with patch.object(downloads, "fetch_json", side_effect=Exception("HTTP Error 403: rate limit exceeded")), patch.object(downloads, "open_secure", side_effect=OSError("offline")):
            with self.assertRaises(Exception) as ctx:
                updater.latest_release("o/r")
        self.assertIn("403", str(ctx.exception))

    def test_a_repo_without_releases(self):
        from studio import updater, downloads
        with patch.object(downloads, "fetch_json", side_effect=Exception("HTTP Error 404")), patch.object(downloads, "open_secure", side_effect=lambda req, timeout=0: Resp(b"", "https://github.com/o/r/releases")):
            self.assertIsNone(updater.latest_release("o/r"))


class PythonInstall(unittest.TestCase):
    def test_sys_is_imported_and_nothing_else_is_undefined(self):
        from studio import managed
        self.assertTrue(hasattr(managed, "sys"))
        managed.runtime_python()                                         # used to raise NameError
        import subprocess
        out = subprocess.run([sys.executable, "-m", "pyflakes", str(ROOT / "studio"), str(ROOT / "runtime"), str(ROOT / "main.py"), str(ROOT / "avs_boot.py")], capture_output=True, text=True)
        if "No module named pyflakes" not in out.stderr:
            self.assertNotIn("undefined name", out.stdout)

    def test_the_python_installer_command(self):
        from studio import managed
        calls = []
        target = managed.own_python_dir() / "python.exe"
        state = {"done": False}

        def fake_probe(path):
            return Path(path) if state["done"] and Path(path) == target else None

        def fake_run(args, log, cancel=None, *a, **k):
            calls.append(list(args))
            state["done"] = True

        def fake_download(url, dest, log, cancel=None, name="file"):
            Path(dest).write_bytes(b"MZ")
            return dest
        with patch.object(managed, "probe", fake_probe), patch.object(managed, "run", fake_run), patch.object(managed, "download_file", fake_download), patch.object(managed, "signature_ok", return_value=True):
            managed.install_python_windows(lambda m: None)
        args = calls[0]
        for needed in ("/quiet", "InstallAllUsers=0", "Include_pip=1", "PrependPath=0", "TargetDir=" + str(managed.own_python_dir())):
            self.assertIn(needed, args)
        self.assertTrue(args[0].endswith("python-3.11.9-amd64.exe"))

    def test_an_unsigned_installer_is_never_run(self):
        from studio import managed
        ran = []

        def fake_download(url, dest, log, cancel=None, name="file"):
            Path(dest).write_bytes(b"MZ")
            return dest
        with patch.object(managed, "probe", return_value=None), patch.object(managed, "run", side_effect=lambda *a, **k: ran.append(a)), \
                patch.object(managed, "download_file", fake_download), patch.object(managed, "signature_ok", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                managed.install_python_windows(lambda m: None)
        self.assertFalse(ran)
        self.assertIn("signature", str(ctx.exception))
        self.assertFalse((managed.subdir("py") / "python-3.11.9-amd64.exe").exists())

    def test_pip_is_added_only_when_missing_then_updated(self):
        from studio import managed
        for has_pip, expected in ((False, ["ensurepip", "install"]), (True, ["install"])):
            seen = []
            with patch.object(managed, "probe_import", return_value=(has_pip, "")), patch.object(managed, "run", side_effect=lambda cmd, *a, **k: seen.append([x for x in cmd if x in ("ensurepip", "install")][0])):
                managed.ensure_pip("py", lambda m: None)
            self.assertEqual(seen, expected)

    def test_the_download_is_cut_and_cleaned_when_cancelled(self):
        from studio import managed, downloads
        import threading
        stop = threading.Event()
        stop.set()
        dest = Path(tempfile.mkdtemp()) / "f.exe"
        with patch.object(downloads, "open_secure", return_value=Resp(b"x" * 1000, "https://www.python.org/f.exe")):
            with self.assertRaises(managed.Cancelled):
                managed.download_file("https://www.python.org/f.exe", dest, lambda m: None, stop)
        self.assertFalse(dest.exists() or dest.with_name(dest.name + ".part").exists())


try:
    import qtstub  # noqa: F401
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except Exception:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "Qt not available")
class AskFirst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        cls.w = StudioWindow()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_missing_python_is_offered_before_a_model_setup(self):
        from studio import ui_v5, managed
        w = self.w
        shim = types.SimpleNamespace(name="nt", environ={}, path=os.path)
        started, asked, base = [], [], []
        w._python_ok = False
        w.confirm = lambda title, text: asked.append(title) or True
        w.start_task = lambda key, voice, fn, msg, **k: started.append((key, msg))
        with patch.object(ui_v5, "os", shim), patch.object(managed, "runtime_python", return_value=None), patch.object(ui_v5.V3, "install_card", lambda self, key: base.append(key)):
            w.install_card("piper")
        self.assertEqual(asked, ["Python is needed"])
        self.assertEqual(started, [("runtime", "Preparing Python")])
        self.assertEqual(base, [])                                       # the setup itself waits until Python is there

    def test_with_python_the_setup_goes_ahead(self):
        from studio import ui_v5, managed
        w = self.w
        shim = types.SimpleNamespace(name="nt", environ={}, path=os.path)
        base = []
        w._python_ok = False
        with patch.object(ui_v5, "os", shim), patch.object(managed, "runtime_python", return_value=Path("python.exe")), patch.object(ui_v5.V3, "install_card", lambda self, key: base.append(key)):
            w.install_card("piper")
        self.assertEqual(base, ["piper"])
        self.assertTrue(w._python_ok)


if __name__ == "__main__":
    unittest.main()
