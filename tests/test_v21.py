import os as _os
_os.environ.setdefault("AVS_OWNER_KEY_FILE", "none")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import os, sys, tempfile, unittest, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["AI_VOICE_STUDIO_HOME"] = tempfile.mkdtemp()
os.environ["AVS_NO_PROMPTS"] = "1"
from studio import channels as ch


def fresh():
    return ch.Channels(Path(tempfile.mkdtemp()) / "c.json")


class Data(unittest.TestCase):
    def setUp(self):
        self.d = fresh()
        self.c = self.d.add_channel("Bud")["id"]

    def test_competitors_all_optional(self):
        self.d.add_competitor(self.c, "Only name")
        self.d.add_competitor(self.c, "", "https://youtube.com/@x")
        self.d.add_competitor(self.c, "Both", "https://youtube.com/@y")
        with self.assertRaises(ValueError):
            self.d.add_competitor(self.c, "  ", " ")
        self.assertEqual(self.d.competitors_text(self.c, links_only=True), "https://youtube.com/@x\nhttps://youtube.com/@y")
        self.assertEqual(self.d.competitors_text(self.c).splitlines(), ["Only name", "https://youtube.com/@x", "Both - https://youtube.com/@y"])
        cid = self.d.channel(self.c)["competitors"][0]["id"]
        self.d.update_competitor(self.c, cid, "Renamed", "")
        self.d.remove_competitor(self.c, cid)
        self.assertEqual(len(self.d.channel(self.c)["competitors"]), 2)

    def test_countries(self):
        self.d.add_country(self.c, "USA")
        with self.assertRaises(ValueError):
            self.d.add_country(self.c, "usa")
        s = self.d.add_script(self.c, "A", "x", country="USA")
        self.d.remove_country(self.c, "USA")
        self.assertEqual(self.d.script(self.c, s["id"])["country"], "")

    def test_sections_are_optional_and_join(self):
        s = self.d.add_script(self.c, "A", "plain text")
        self.assertEqual(s["sections"], [])
        self.d.set_sections(self.c, s["id"], [{"title": "Intro", "text": "one"}, {"title": "Body", "text": "two"}])
        self.assertEqual(self.d.script(self.c, s["id"])["text"], "one\n\ntwo")
        self.d.set_sections(self.c, s["id"], [])
        self.assertEqual(self.d.script(self.c, s["id"])["sections"], [])

    def test_search(self):
        self.d.add_script(self.c, "Rome", "Rome and Caesar and ancient egypt")
        self.d.add_script(self.c, "Egypt", "ancient egypt only")
        self.d.add_script(self.c, "Nothing", "zzz")
        r = self.d.search(self.c, "rome ancient")
        self.assertEqual(r["per_keyword"], {"rome": 1, "ancient": 2})
        self.assertEqual(len(r["hits"]), 2)
        self.assertEqual(len(self.d.search(self.c, "rome ancient", match_all=True)["hits"]), 1)
        self.assertEqual(len(self.d.search(self.c, "ancient egypt, caesar")["hits"]), 2)       # comma = phrases
        self.assertEqual(self.d.search(self.c, "")["hits"], [])

    def test_passcodes(self):
        self.assertTrue(self.d.check_passcode("section", "anything"))          # no code set = open
        with self.assertRaises(ValueError):
            self.d.set_passcode("section", "ab")
        self.d.set_passcode("section", "zq-pass")
        self.assertTrue(self.d.check_passcode("section", "zq-pass"))
        self.assertFalse(self.d.check_passcode("section", "zq-pasz"))
        self.d.set_passcode("channels", "k99", [self.c])
        self.assertEqual(self.d.locked_channels(), [self.c])
        self.assertNotIn("zq-pass", json.dumps(self.d.data))                       # only a salted hash is stored
        self.d.remove_passcode("channels")
        self.assertEqual(self.d.locked_channels(), [])
        reloaded = ch.Channels(self.d.file)
        self.assertTrue(reloaded.lock_on("section"))

    def test_old_files_still_load(self):
        old = {"version": 1, "active": "a1", "channels": [{"id": "a1", "name": "Old", "category": "", "description": "", "emoji": "🎙", "status": "Active",
               "master_prompt": "keep me", "master_on": True, "voice_direction": "", "created": 1, "prompts": [], "voices": [],
               "scripts": [{"id": "s1", "title": "T", "text": "hello", "status": "Draft", "notes": "", "created": 1, "updated": 1}]}]}
        f = Path(tempfile.mkdtemp()) / "c.json"
        f.write_text(json.dumps(old))
        d = ch.Channels(f)
        self.assertEqual(d.vo_scripts("a1"), [])
        self.assertEqual(d.search("a1", "hello")["hits"][0]["id"], "s1")
        d.update_script("a1", "s1", country="UK", vo=True)
        self.assertEqual(d.channel("a1")["master_prompt"], "keep me")           # nothing of the old data is dropped


try:
    import qtstub  # noqa: F401
    from PySide6.QtWidgets import QApplication, QPushButton
    HAVE_QT = True
except Exception:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "Qt not available")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QImage, QColor
        cls.app = QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        cls.w = StudioWindow()
        cls.w.show()
        cls.w.stack.setCurrentIndex(10)
        cls.png = str(Path(tempfile.mkdtemp()) / "logo.png")
        img = QImage(300, 200, QImage.Format.Format_RGB32)
        img.fill(QColor("#7c3aed"))
        img.save(cls.png)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def pump(self):
        self.app.processEvents()

    def test_01_page_layout_and_logo(self):
        w = self.w
        w.ch_new_dialog = lambda: ("Bud", self.png)
        w.ch_new()
        active = w.channel_data.active()
        self.assertEqual(active["name"], "Bud")
        from studio import ui_channels as uc
        self.assertTrue(uc.logo_file(active))
        self.assertFalse(w.ch_combo.itemIcon(0).isNull())
        for gone in ("ch_master", "ch_master_on", "ch_name", "ch_desc", "ch_category"):
            self.assertFalse(hasattr(w, gone), gone)                                 # master prompt and detail boxes are gone from the dashboard
        labels = [b.text() for b in w.ch_stack.widget(0).findChildren(QPushButton)]
        self.assertNotIn("Add Studio voice as main", labels)                         # voices moved to Voice Overs
        self.assertIn("Change logo", labels)
        scripts_buttons = [b.text() for b in w.ch_stack.widget(1).findChildren(QPushButton)]
        self.assertNotIn("Duplicate", scripts_buttons)                               # no Duplicate in Scripts
        self.assertIn("➜ Send to Voice Overs", scripts_buttons)
        w.ch_remove_logo()
        self.assertFalse(uc.logo_file(w.channel_data.active()))

    def test_02_competitors_and_copy(self):
        w = self.w
        cid = w.channel_data.active()["id"]
        for pair in (("A channel", ""), ("", "https://youtube.com/@x"), ("Both", "https://youtube.com/@y")):
            w.ch_competitor_dialog = lambda title, name="", link="", p=pair: p
            w.ch_competitor_add()
        self.assertEqual(w.ch_comp_table.rowCount(), 3)
        w.ch_competitor_text(links_only=True)
        w.ch_copy(w.ch_competitor_text(links_only=True))
        from PySide6.QtGui import QGuiApplication
        self.assertEqual(QGuiApplication.clipboard().text(), "https://youtube.com/@x\nhttps://youtube.com/@y")
        w.ch_comp_table.selectRow(2)
        w.ch_competitor_copy_selected()
        self.assertEqual(QGuiApplication.clipboard().text(), "Both - https://youtube.com/@y")
        from studio.ui_channels import parse_competitor_line
        self.assertEqual(parse_competitor_line("Rome TV - https://youtube.com/@rome"), ("Rome TV", "https://youtube.com/@rome"))
        self.assertEqual(parse_competitor_line("www.site.com/x"), ("", "www.site.com/x"))
        self.assertEqual(parse_competitor_line("Just a name"), ("Just a name", ""))

    def test_03_countries_sections_search_vo(self):
        w = self.w
        d = w.channel_data
        cid = d.active()["id"]
        self.assertTrue(w.ch_country_box.isHidden())                                  # nothing shown until a country exists
        self.assertTrue(w.ch_table.isColumnHidden(1))
        w.ch_ask_text = lambda *a, **k: ("USA", True)
        w.ch_country_add()
        w.ch_ask_text = lambda *a, **k: ("Pakistan", True)
        w.ch_country_add()
        self.pump()
        self.assertFalse(w.ch_country_box.isHidden())
        self.assertFalse(w.ch_table.isColumnHidden(1))
        a = d.add_script(cid, "Rome story", "Rome and Caesar. The ancient egypt era.", country="USA")
        b = d.add_script(cid, "Egypt story", "Only ancient egypt here.", country="Pakistan")
        c = d.add_script(cid, "Other", "nothing relevant", country="USA")
        w.refresh_channels(select=a["id"])
        self.pump()
        self.assertEqual(w.ch_table.rowCount(), 3)
        w.ch_set_country_filter("USA")
        self.assertEqual(w.ch_table.rowCount(), 2)
        w.ch_set_country_filter("")
        # --- search
        w.ch_search.setText("ancient egypt, caesar")
        w.ch_search_run()
        self.assertEqual(w.ch_results.count(), 2)
        self.assertIn("2 of 3", w.ch_search_info.text())
        w.ch_result_clicked(w.ch_results.item(0))
        self.assertEqual(w._ch_shown, w.ch_results.item(0).data(0x100))
        self.assertTrue(w.ch_text.extraSelections())
        w.ch_search.setText("")
        w.ch_search_run()
        self.assertTrue(w.ch_results.isHidden())
        # --- sections: hidden until added
        w.ch_table.clearSelection()
        w.ch_table.selectRow(2)
        self.assertEqual(w._ch_shown, c["id"])
        self.assertTrue(w.ch_sec_box.isHidden())
        w.ch_ask_text = lambda *a, **k: ("Outro", True)
        w.ch_section_add()
        self.assertFalse(w.ch_sec_box.isHidden())
        self.assertEqual([s["title"] for s in w._ch_sections], ["Section 1", "Outro"])
        w.ch_text.setPlainText("the end")
        w.ch_save_script(quiet=True)
        self.assertEqual(d.script(cid, c["id"])["text"], "nothing relevant\n\nthe end")
        w.ch_section_join()
        w.ch_save_script(quiet=True)
        self.assertEqual(d.script(cid, c["id"])["sections"], [])
        self.assertTrue(w.ch_sec_box.isHidden())
        # --- country survives a refresh (regression check)
        w.refresh_channels(select=c["id"])
        self.assertEqual(d.script(cid, c["id"])["country"], "USA")
        # --- send to voice overs, voice per script
        w.ch_table.clearSelection()
        w.ch_table.selectRow(0)
        w.ch_table.selectRow(1) if False else None
        w.ch_send_vo(True)
        self.assertEqual(w.ch_vo_scripts.rowCount(), 1)
        d.assign_voice(cid, "piper", "v1", "Voice One", "main")
        d.assign_voice(cid, "kokoro", "v2", "Voice Two", "alt")
        w.refresh_channels()
        combo = w.ch_vo_scripts.cellWidget(0, 3)
        self.assertEqual(combo.count(), 2)
        combo.setCurrentIndex(1)
        w.ch_row_voice(combo.parent() and w.ch_vo_scripts.item(0, 0).data(0x100), combo)
        sid = w.ch_vo_scripts.item(0, 0).data(0x100)
        self.assertEqual(d.script(cid, sid)["voice"]["voice"], "v2")
        self.assertEqual(w.ch_voice_for(d.active(), d.script(cid, sid))[1], "v2")
        w.ch_all_voice.setCurrentIndex(0)
        w.ch_voice_for_all()
        self.assertIsNone(d.script(cid, sid)["voice"])                               # main voice = follows the channel
        made = []
        w.ch_generate = lambda ids, start=True: made.append(ids) or []
        w.ch_vo_scripts.clearSelection()
        w.ch_generate_vo()
        self.assertEqual(made, [[sid]])
        w.ch_vo_scripts.selectRow(0)
        w.ch_vo_remove()
        self.assertEqual(w.ch_vo_scripts.rowCount(), 0)

    def test_04_passcodes(self):
        w = self.w
        d = w.channel_data
        cid = d.active()["id"]
        w.ch_stack.setCurrentIndex(0)
        self.assertIsNone(w.ch_locked_reason())
        answers = iter([("abc", True), ("abc", True)])
        w.ch_ask_text = lambda *a, **k: next(answers)
        w.ch_pass_set("section")
        self.assertTrue(d.lock_on("section"))
        w.ch_lock_everything()
        self.pump()
        self.assertEqual(w.ch_locked_reason(), "section")
        self.assertEqual(w.ch_gate.currentIndex(), 0)
        self.assertTrue(w.ch_header.isHidden())                                       # not even the channel names are shown
        w.ch_pass_edit.setText("wrong")
        w.ch_unlock()
        self.assertEqual(w.ch_gate.currentIndex(), 0)
        w.ch_pass_edit.setText("abc")
        w.ch_unlock()
        self.assertEqual(w.ch_gate.currentIndex(), 1)
        self.assertFalse(w.ch_header.isHidden())
        # prompts only
        d.set_passcode("prompts", "pp1")
        w.ch_lock_everything()
        w._ch_open.add("section")
        w.ch_apply_gate()
        self.assertEqual(w.ch_gate.currentIndex(), 1)                                 # other tabs stay open
        w.ch_show(2)
        self.assertEqual(w.ch_locked_reason(), "prompts")
        w.ch_pass_edit.setText("pp1")
        w.ch_unlock()
        self.assertIsNone(w.ch_locked_reason())
        # chosen channels only: a second channel stays free
        other = d.add_channel("Free")["id"]
        d.set_passcode("channels", "ch1", [cid])
        w._ch_open.discard("ch:" + cid)
        d.set_active(cid)
        w.ch_show(0)
        self.assertEqual(w.ch_locked_reason(), "channel")
        d.set_active(other)
        w.refresh_channels()
        self.assertIsNone(w.ch_locked_reason())
        d.set_active(cid)
        w.refresh_channels()
        w.ch_pass_edit.setText("ch1")
        w.ch_unlock()
        self.assertIsNone(w.ch_locked_reason())
        # forgot passcode needs the real owner key
        self.assertFalse(w.ch_owner_key_ok("not a key"))
        for kind in ("section", "prompts", "channels"):
            d.remove_passcode(kind)
        d.delete_channel(other)
        w.ch_lock_everything()
        self.assertIsNone(w.ch_locked_reason())

    def test_05_keyboard(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        from studio import shortcuts
        w = self.w
        d = w.channel_data
        cid = d.active()["id"]
        w.stack.setCurrentIndex(10)
        w.ch_show(1)
        a = d.add_script(cid, "Key A", "aaa")
        d.add_script(cid, "Key B", "bbb")
        w.refresh_channels(select=a["id"])
        w.activateWindow()
        w.ch_table.setFocus()
        self.pump()
        w.confirm = lambda *a, **k: True
        before = len(d.channel(cid)["scripts"])
        # typing in the editor must never delete a script
        w.ch_text.setFocus()
        self.pump()
        QTest.keyClick(w.ch_text, Qt.Key.Key_Delete)
        self.assertEqual(len(d.channel(cid)["scripts"]), before)
        # the same key on the list deletes the selected script
        w.ch_table.setFocus()
        self.pump()
        w.ch_table.selectRow(0)
        QTest.keyClick(w.ch_table, Qt.Key.Key_Delete)
        self.pump()
        if len(d.channel(cid)["scripts"]) == before:                                   # offscreen focus can be unavailable: call the handler directly
            self.assertTrue(shortcuts.on_delete(w, w.ch_table))
        self.assertEqual(len(d.channel(cid)["scripts"]), before - 1)
        # copy and page keys
        row = next(r for r in range(w.ch_table.rowCount()) if w.ch_table.item(r, 0).text().startswith("Key"))
        w.ch_table.clearSelection()
        w.ch_table.selectRow(row)
        shortcuts.on_copy(w, w.ch_table)
        from PySide6.QtGui import QGuiApplication
        self.assertIn("Key", QGuiApplication.clipboard().text())
        w.stack.setCurrentIndex(0)
        shortcuts.go_page(w, 6)
        self.assertEqual(w.stack.currentIndex(), 6)
        w.nav_buttons[10].hide()
        shortcuts.go_page(w, 10)
        self.assertEqual(w.stack.currentIndex(), 6)                                    # a hidden page cannot be opened by keyboard
        w.nav_buttons[10].show()
        w.stack.setCurrentIndex(10)
        shortcuts.find(w)
        self.assertEqual(w.ch_stack.currentIndex(), 1)
        shortcuts.channel_tab(w, 1)
        self.assertEqual(w.ch_stack.currentIndex(), 2)
        shortcuts.channel_tab(w, -1)
        shortcuts.new_item(w)                                                          # Ctrl+N on Scripts = a new script
        self.assertTrue(any(s["title"] == "New script" for s in d.channel(cid)["scripts"]))
        self.assertGreaterEqual(len(shortcuts.HELP), 5)


if __name__ == "__main__":
    unittest.main()
