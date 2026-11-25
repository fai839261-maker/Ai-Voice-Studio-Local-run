"""Qt desktop UI: no terminal needed by end users. All downloads run in QThreads."""
from __future__ import annotations
import json
import os
import shutil
import threading
import traceback
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
 QApplication, QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox,
 QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
 QMainWindow, QMessageBox, QPushButton, QProgressBar, QScrollArea, QSpinBox,
 QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,
 QVBoxLayout, QWidget, QHeaderView, QInputDialog
)

from .core import APP_NAME, APP_VERSION, Store, catalog, models, root, subdir, safe_file_name
from . import services

STYLE = """
* { font-family: 'Segoe UI', 'Helvetica Neue', Arial; font-size: 13px; }
QMainWindow, QWidget#page, QWidget#root, QDialog, QMessageBox, QInputDialog { background: #101a24; color: #e6eef5; }
QWidget { color: #e6eef5; }
QCheckBox { color:#e6eef5; }
QCheckBox::indicator { width:16px;height:16px;border:1px solid #7199a9;border-radius:3px;background:#12212e; }
QCheckBox::indicator:checked { background:#37aa9e;border:2px solid #e6eef5; }
QDialogButtonBox { background:#101a24; }
QScrollArea QWidget#qt_scrollarea_viewport { background:#101a24; }
QTableView { alternate-background-color:#192a38;gridline-color:#2b4354; }
QToolTip { color:#e6eef5; background:#22394b; border:1px solid #7199a9; }
QLineEdit:disabled,QComboBox:disabled { color:#a4bac9; }
QFrame#sidebar { background: #142432; border-right: 1px solid #294052; }
QFrame#panel { background: #192a38; border: 1px solid #2b4354; border-radius: 14px; }
QLabel { color: #dce8f1; }
QLabel#muted { color: #92aabc; }
QLabel#title { color: #ffffff; font-size: 26px; font-weight: 750; }
QLabel#sectiontitle { color: #f4f7fa; font-size: 17px; font-weight: 650; }
QPushButton { background: #253b4d; color:#e6eef5; border:1px solid #395166; border-radius:8px; padding:9px 12px; }
QPushButton:hover { background: #32546a; }
QPushButton:disabled { background:#263441; color:#637888; }
QPushButton#primary { background: #37aa9e; color: #0a222a; font-weight: 750; border: 0; }
QPushButton#primary:hover { background: #69d4c8; }
QPushButton#nav { text-align:left; border:0; background:transparent; padding:13px; font-size:14px; }
QPushButton#nav:checked { background:#20545d; border-left:4px solid #52dcc8; color:#ffffff; }
QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget { background:#12212e; color:#e9f3f8; border:1px solid #375064; border-radius:7px; padding:7px; selection-background-color:#267879; }
QComboBox QAbstractItemView { background:#1b2c3a; color:white; selection-background-color:#267879; }
QHeaderView::section { background:#233a4a; color:#d1e4ec; border:0; padding:8px; font-weight:600; }
QScrollArea { border:0; background:transparent; }
QProgressBar { border:1px solid #3a5869; border-radius:6px; background:#15232f; height:9px; }
QProgressBar::chunk { background:#39bcab; }
QCheckBox { spacing:8px; }
"""


def label(text, kind=""):
    a=QLabel(text)
    if kind: a.setObjectName(kind)
    a.setWordWrap(True)
    return a


def button(text, callback, primary=False):
    b=QPushButton(text)
    if primary:b.setObjectName("primary")
    b.clicked.connect(callback)
    return b


def page(title, intro):
    w=QWidget(); w.setObjectName("page")
    v=QVBoxLayout(w);v.setContentsMargins(24,22,24,20);v.setSpacing(14)
    v.addWidget(label(title,"title"));v.addWidget(label(intro,"muted"))
    return w,v


def panel():
    f=QFrame(); f.setObjectName("panel")
    layout=QVBoxLayout(f);layout.setSpacing(11);layout.setContentsMargins(18,18,18,18)
    return f,layout


def layout_row(*items):
    row=QHBoxLayout(); row.setSpacing(10)
    for item in items:
        if isinstance(item,QWidget): row.addWidget(item)
        else: row.addLayout(item)
    return row


class TaskThread(QThread):
    log = Signal(str)
    finished_result = Signal(object)
    failed = Signal(str)
    def __init__(self, fn):
        super().__init__();self.fn=fn
    def run(self):
        try:
            res=self.fn(self.log.emit)
            self.finished_result.emit(res)
        except Exception as exc:
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.store=Store()
        self.workers=[]
        self.running={}
        self.stop_flags={}
        self.busy_installer=False
        self.setWindowTitle(f"{APP_NAME}  ·  {APP_VERSION}")
        self.resize(1400,880);self.setMinimumSize(1000,690)
        self.setStyleSheet(STYLE)
        self.make_shell()
        self.make_studio()
        self.make_queue()
        self.make_library()
        self.make_setup()
        self.make_settings()
        self.make_logs()
        self.change_page(0)
        self.refresh_queue()
        self.refresh_voices()
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.update_resources)
        self.timer.start(6000)
        self.update_resources()
        self.log_message("Application started. Engines are not considered Ready until real WAV test synthesis passes.")

    def make_shell(self):
        r=QWidget();r.setObjectName("root");self.setCentralWidget(r)
        overall=QVBoxLayout(r);overall.setContentsMargins(0,0,0,0);overall.setSpacing(0)
        strip=QFrame();strip.setFixedHeight(60);strip.setStyleSheet("background:#122431;border-bottom:1px solid #2e4657;")
        bar=QHBoxLayout(strip);bar.setContentsMargins(24,0,24,0)
        bar.addWidget(label("◉  AI VOICE STUDIO","sectiontitle"));bar.addStretch()
        self.resources_label=label("System detecting...","muted");bar.addWidget(self.resources_label)
        overall.addWidget(strip)
        body=QHBoxLayout();body.setContentsMargins(0,0,0,0);body.setSpacing(0);overall.addLayout(body)
        sidebar=QFrame();sidebar.setObjectName("sidebar");sidebar.setFixedWidth(218)
        nav=QVBoxLayout(sidebar);nav.setContentsMargins(10,24,10,18)
        nav.addWidget(label("WORKSPACE","muted"))
        self.nav_buttons=[]
        for idx,name in enumerate(["Voice Studio","Batch Queue","Voice Clone","AI Setup","Settings","Activity Logs"]):
            b=button(name,partial(self.change_page,idx));b.setCheckable(True);b.setObjectName("nav")
            nav.addWidget(b);self.nav_buttons.append(b)
        nav.addStretch()
        body.addWidget(sidebar)
        self.stack=QStackedWidget();body.addWidget(self.stack,1)
        self.footer=label("Whatsapp: +92 3104768426","muted")
        self.footer.setStyleSheet("background:#11202b;padding:8px 22px;")
        overall.addWidget(self.footer)

    def change_page(self, idx):
        self.stack.setCurrentIndex(idx)
        for j,b in enumerate(self.nav_buttons):b.setChecked(j==idx)
        if idx==1:self.refresh_queue()
        if idx==2:self.refresh_library()
        if idx==3:self.scan_selected()

    def make_studio(self):
        w,v=page("Voice Studio","Write a script, choose a compatible voice, and queue real audio generation. No account or activation.")
        card,cv=panel();v.addWidget(card)
        form=QFormLayout()
        self.model_combo=QComboBox()
        for entry in models().values():self.model_combo.addItem(entry["name"],entry["id"])
        self.voice_combo=QComboBox()
        self.model_combo.currentIndexChanged.connect(self.refresh_voices)
        form.addRow("AI engine",self.model_combo);form.addRow("Voice",self.voice_combo)
        cv.addLayout(form)
        self.script=QTextEdit();self.script.setPlaceholderText("Paste your voice-over script here…\nEach sentence becomes an independent, regenerable audio section.")
        self.script.setMinimumHeight(175);cv.addWidget(self.script)
        v.addLayout(layout_row(button("Import script (.txt)",self.import_script),button("Import several scripts",self.import_batch)))
        options,ov=panel();v.addWidget(options)
        ov.addWidget(label("Generation options — controls only apply to supported engines","sectiontitle"))
        opts=QHBoxLayout()
        self.speed=QDoubleSpinBox();self.speed.setRange(0.6,1.5);self.speed.setSingleStep(0.05);self.speed.setValue(1.0)
        self.exaggeration=QDoubleSpinBox();self.exaggeration.setRange(0,1.0);self.exaggeration.setSingleStep(0.1);self.exaggeration.setValue(0.5)
        self.cfg=QDoubleSpinBox();self.cfg.setRange(0,1.0);self.cfg.setSingleStep(0.1);self.cfg.setValue(0.5)
        self.device=QComboBox();self.device.addItems(["CPU", "CUDA (if available)"])
        self.target_chars=QSpinBox();self.target_chars.setRange(0,10000);self.target_chars.setValue(0);self.target_chars.setSpecialValueText("No limit")
        self.pause=QSpinBox();self.pause.setRange(0,2500);self.pause.setValue(220);self.pause.setSuffix(" ms")
        self.opt_widgets={}
        for tag,title,widget in [("speed","Speed",self.speed),("exaggeration","Expression",self.exaggeration),("cfg_weight","Guidance",self.cfg),("device","Compute",self.device),("target_chars","Section target",self.target_chars),("pause_ms","Pause between",self.pause)]:
            cell=QVBoxLayout();l=label(title,"muted");cell.addWidget(l);cell.addWidget(widget);opts.addLayout(cell);self.opt_widgets[tag]=(l,widget)
        ov.addLayout(opts)
        self.ref_group,rv=panel();v.addWidget(self.ref_group)
        rv.addWidget(label("Authorized voice reference (cloning engines only)","sectiontitle"))
        self.ref_file=QLineEdit();self.ref_file.setReadOnly(True)
        rv.addLayout(layout_row(self.ref_file,button("Choose WAV",self.choose_reference)))
        self.ref_text=QLineEdit();self.ref_text.setPlaceholderText("Reference recording transcript (required if using F5 for best results)")
        rv.addWidget(self.ref_text)
        self.cloning_consent=QCheckBox("I have permission to reproduce the speaker's voice.")
        rv.addWidget(self.cloning_consent)
        v.addWidget(button("Add script to processing queue",self.add_script,True))
        v.addStretch(1)
        self.stack.addWidget(self.scroll_wrap(w))
        self.refresh_voices()

    def scroll_wrap(self,w):
        s=QScrollArea();s.setWidgetResizable(True);s.setWidget(w)
        return s

    def refresh_voices(self,*_):
        if not hasattr(self,"voice_combo"):return
        model=self.model_combo.currentData();entry=models()[model]
        prev=self.voice_combo.currentData()
        self.voice_combo.clear()
        for voice in entry.get("voices",[]):self.voice_combo.addItem(voice["name"],voice["id"])
        if not entry.get("voices"):
            self.voice_combo.addItem("Reference-based cloned voice","")
        if prev:
            idx=self.voice_combo.findData(prev)
            if idx>=0:self.voice_combo.setCurrentIndex(idx)
        for tag,(l,control) in self.opt_widgets.items() if hasattr(self,"opt_widgets") else []:
            visible=tag in entry.get("supports",[]) or tag in ("target_chars","pause_ms") or (tag=="device" and model in ("chatterbox","f5"))
            l.setVisible(visible);control.setVisible(visible)
        if hasattr(self,"ref_group"):self.ref_group.setVisible(entry["kind"]=="clone")

    def options(self):
        m=models()[self.model_combo.currentData()]
        opt={"target_chars":self.target_chars.value(),"pause_ms":self.pause.value()}
        if "speed" in m["supports"]:opt["speed"]=self.speed.value()
        if "exaggeration" in m["supports"]:opt["exaggeration"]=self.exaggeration.value()
        if "cfg_weight" in m["supports"]:opt["cfg_weight"]=self.cfg.value()
        if m["kind"]=="clone":opt["device"]="cuda" if self.device.currentIndex()==1 else "cpu"
        return opt

    def import_script(self):
        path,_=QFileDialog.getOpenFileName(self,"Import text script",str(Path.home()),"Text documents (*.txt *.md)")
        if path:
            try:self.script.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
            except Exception as e:self.alert(str(e))

    def import_batch(self):
        paths,_=QFileDialog.getOpenFileNames(self,"Import scripts",str(Path.home()),"Text documents (*.txt *.md)")
        if not paths:return
        if models()[self.model_combo.currentData()]["kind"]=="clone" and not self.cloning_consent.isChecked():
            self.alert("Confirm speaker permission for cloning first.");return
        count=0
        for path in paths:
            try:
                script=Path(path).read_text(encoding="utf-8-sig")
                self.store.new_job(Path(path).stem,script,self.model_combo.currentData(),self.voice_combo.currentData(),self.ref_file.text(),self.ref_text.text(),self.options())
                count+=1
            except Exception as ex:self.log_message(f"Could not import {path}: {ex}")
        self.refresh_queue();self.toast(f"Imported {count} scripts into the queue")
        self.change_page(1)

    def choose_reference(self):
        path,_=QFileDialog.getOpenFileName(self,"Select licensed reference recording",str(Path.home()),"WAV audio (*.wav)")
        if path:self.ref_file.setText(path)

    def add_script(self):
        if models()[self.model_combo.currentData()]["kind"]=="clone":
            if not self.ref_file.text() or not self.cloning_consent.isChecked():
                self.alert("Choose a WAV and confirm that you have permission to clone the speaker.");return
        title,ok=QInputDialog.getText(self,"Project title","Name this audio project:",text="Voice Over")
        if not ok:return
        try:
            new=self.store.new_job(title or "Voice Over",self.script.toPlainText(),self.model_combo.currentData(),self.voice_combo.currentData(),self.ref_file.text(),self.ref_text.text(),self.options())
            self.toast("Added script to queue. Verify the chosen voice in AI Setup before generating.")
            self.change_page(1)
        except Exception as exc:self.alert(str(exc))

    def make_queue(self):
        w,v=page("Batch Queue","Jobs and individual sections survive restart. Double-click a section's text to edit only that section.")
        self.queue_table=QTableWidget(0,5)
        self.queue_table.setHorizontalHeaderLabels(["Project","Model / Voice","Progress","Status","Created"])
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.queue_table.setMinimumHeight(220)
        self.queue_table.itemSelectionChanged.connect(self.refresh_sections)
        v.addWidget(self.queue_table,2)
        v.addLayout(layout_row(button("Generate selected",self.run_selected,True),button("Process all pending",self.run_all),button("Pause after current section",self.pause_jobs),button("Open result",self.open_output),button("Export MP3",self.export_mp3),button("Refresh",self.refresh_queue)))
        v.addWidget(label("Selected job · independent audio sections","sectiontitle"))
        self.section_table=QTableWidget(0,3)
        self.section_table.setHorizontalHeaderLabels(["#","Sentence / section (double-click to edit)","Status"])
        self.section_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.section_table.itemChanged.connect(self.section_edited)
        self.section_table.setMinimumHeight(200)
        v.addWidget(self.section_table,2)
        v.addLayout(layout_row(button("Regenerate selected section",self.regenerate_section),button("Play section",self.play_section),button("Open output folder",self.open_output_folder)))
        self.stack.addWidget(w)

    def selected_job(self):
        row=self.queue_table.currentRow()
        return self.queue_table.item(row,0).data(Qt.UserRole) if row>=0 and self.queue_table.item(row,0) else None

    def selected_section(self):
        i=self.section_table.currentRow()
        return i if i>=0 else None

    def refresh_queue(self):
        old=self.selected_job()
        data=self.store.jobs()
        self.queue_table.blockSignals(True)
        self.queue_table.setRowCount(len(data))
        choose=0
        for idx,job in enumerate(data):
            parts=self.store.sections(job["id"])
            done=sum(s["status"]=="complete" for s in parts)
            values=[job["title"],f"{models().get(job['model'],{}).get('name',job['model'])} · {job['voice'] or json.loads(job['options']).get('clone_name') or 'cloned'}",f"{done}/{len(parts)}",job["status"],datetime.fromtimestamp(job["created"]).strftime("%d %b %H:%M")]
            for col,text in enumerate(values):
                item=QTableWidgetItem(str(text))
                if col==0:item.setData(Qt.UserRole,job["id"])
                self.queue_table.setItem(idx,col,item)
            if job["id"]==old:choose=idx
        self.queue_table.blockSignals(False)
        if data:self.queue_table.selectRow(choose)
        else:self.section_table.setRowCount(0)
        self.refresh_sections()

    def refresh_sections(self):
        jid=self.selected_job()
        if not jid:return
        data=self.store.sections(jid)
        self.section_table.blockSignals(True)
        self.section_table.setRowCount(len(data))
        for i,segment in enumerate(data):
            for c,t in enumerate((str(i+1),segment["body"],segment["status"])):
                cell=QTableWidgetItem(t)
                if c!=1:cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.section_table.setItem(i,c,cell)
        self.section_table.blockSignals(False)

    def section_edited(self,item):
        if item.column()!=1:return
        jid=self.selected_job()
        if jid in self.running:
            self.alert("Pause the job before changing its script.");self.refresh_sections();return
        try:
            self.store.change_section(jid,item.row(),item.text())
            self.toast("Only the modified section was invalidated.")
            self.refresh_queue()
        except Exception as exc:
            self.alert(str(exc));self.refresh_sections()

    def regenerate_section(self):
        jid=self.selected_job();idx=self.selected_section()
        if jid is None or idx is None:return
        if jid in self.running:
            self.alert("Pause first; active jobs cannot be edited.");return
        s=self.store.sections(jid)[idx]
        self.store.change_section(jid,idx,s["body"])
        self.refresh_queue();self.run_selected()

    def play_section(self):
        jid=self.selected_job();idx=self.selected_section()
        if jid is not None and idx is not None:self.open_path(Path(self.store.sections(jid)[idx]["file"]))

    def run_selected(self):
        jid=self.selected_job()
        if jid:self.launch_job(jid)

    def run_all(self):
        self.pending_jobs=[j["id"] for j in reversed(self.store.jobs()) if j["status"] in ("pending","failed","paused")]
        self.pump_jobs()

    def job_capacity(self):
        # Torch models generally take substantial RAM/VRAM; serialize their work.
        limit=self.store.setting("parallel_jobs",1)
        try:
            import psutil
            if psutil.virtual_memory().available < 8*1024**3:limit=1
        except ImportError:limit=1
        return max(1,min(4,int(limit)))

    def pump_jobs(self):
        pending=getattr(self,"pending_jobs",[])
        while pending and len(self.running)<self.job_capacity():
            jid=pending.pop(0)
            job=self.store.job(jid)
            if job and (job["model"] in ("chatterbox","f5") or any(self.store.job(x)["model"] in ("chatterbox","f5") for x in self.running)) and self.running:
                pending.insert(0,jid);break
            self.launch_job(jid)

    def launch_job(self,jid):
        if jid in self.running:return
        if len(self.running)>=self.job_capacity():
            self.alert("Concurrent job limit reached. Pause or finish a job before starting another.");return
        if self.running and (self.store.job(jid)["model"] in ("chatterbox","f5") or
             any(self.store.job(x)["model"] in ("chatterbox","f5") for x in self.running)):
            self.alert("Torch voice-cloning models are serialized to protect system memory.");return
        job=self.store.job(jid)
        if not job or job["status"]=="completed":return
        flag=threading.Event();self.stop_flags[jid]=flag
        th=TaskThread(lambda log: services.process_job(self.store,jid,log,flag))
        self.running[jid]=th;self.workers.append(th)
        th.log.connect(self.log_message)
        th.failed.connect(lambda message,job_id=jid:self.handle_job_end(job_id,message))
        th.finished_result.connect(lambda result,job_id=jid:self.handle_job_end(job_id,None))
        th.finished.connect(lambda:self.workers.remove(th) if th in self.workers else None)
        th.start()
        self.toast("Processing job in background. Check Activity Logs for progress.")
        self.refresh_queue()

    def handle_job_end(self,jid,error):
        self.running.pop(jid,None);self.stop_flags.pop(jid,None)
        if error:self.log_message("ERROR: "+error)
        self.refresh_queue();self.pump_jobs()

    def pause_jobs(self):
        self.pending_jobs=[]
        for stop in self.stop_flags.values():stop.set()
        self.toast("Pause requested. Each active section finishes before the queue stops.")

    def open_output(self):
        jid=self.selected_job()
        if jid:self.open_path(Path(self.store.job(jid)["output"]))

    def open_output_folder(self):
        self.open_path(subdir("outputs"))

    def export_mp3(self):
        jid=self.selected_job()
        if not jid:return
        wav=Path(self.store.job(jid)["output"])
        if not wav.exists():
            self.alert("Generate the complete audio first.");return
        from .audio_fx import ffmpeg_path
        if not ffmpeg_path():
            self.alert("FFmpeg is not installed. Use WAV or install FFmpeg with WinGet (Gyan.FFmpeg).")
            return
        if not self.confirm("Export MP3","Create a 192 kbps MP3 copy of the selected complete WAV?"):return
        self.background(lambda log:services.export_mp3(wav,log),lambda p:self.toast("MP3 exported: "+str(p)),"Encoding selected audio to MP3...")

    def open_path(self,path:Path):
        if not path.exists():self.alert(f"Not found: {path}");return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def make_library(self):
        w,v=page("Voice Library","Built-in voice catalog plus your own authorized reference WAVs. Voice licenses are specific to each voice.")
        self.library_table=QTableWidget(0,4)
        self.library_table.setHorizontalHeaderLabels(["Voice","Language","Engine","Asset status"])
        self.library_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.library_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        v.addWidget(self.library_table)
        v.addWidget(label("Clone your own or licensed voice by selecting a reference WAV in Voice Studio. No reference audio is uploaded to a cloud service by this app.","muted"))
        v.addLayout(layout_row(button("Refresh library",self.refresh_library),button("Open model files",lambda:self.open_path(subdir("models")))))
        self.stack.addWidget(w)

    def refresh_library(self):
        voices=[(m,v) for m in models().values() for v in m.get("voices",[])]
        self.library_table.setRowCount(len(voices))
        for i,(m,v) in enumerate(voices):
            existing=all((services.model_folder(m["id"],v["id"])/item["name"]).exists() for item in services.voice_files(m["id"],v["id"]))
            for c,t in enumerate((v["name"],v["language"],m["id"],"Downloaded" if existing else "Not downloaded")):
                self.library_table.setItem(i,c,QTableWidgetItem(t))

    def make_setup(self):
        w,v=page("AI Setup","Install Python, download shared base dependencies once, then engine + exact model files. 'Ready' requires an actual WAV test.")
        c,cv=panel();v.addWidget(c)
        self.python_status=label("Checking Python…","muted");cv.addWidget(self.python_status)
        cv.addLayout(layout_row(button("Detect Python",self.detect_python),button("Install Python 3.11 via WinGet",self.click_install_python,True)))
        self.base_status=label("PyTorch is optional for Piper/Kokoro, but required for F5-TTS and Chatterbox.","muted")
        cv.addWidget(self.base_status)
        cv.addLayout(layout_row(button("Install shared AI base · CPU",lambda:self.install_base("CPU")),button("Install shared AI base · CUDA 12.8",lambda:self.install_base("CUDA 12.8")),button("Test CUDA / GPU",self.test_cuda)))
        cv.addWidget(label("CPU and CUDA builds can be multi-gigabyte downloads. Choose one explicitly. All package operations require confirmation.","muted"))
        m,ml=panel();v.addWidget(m)
        ml.addWidget(label("Model & adapter manager","sectiontitle"))
        self.setup_model=QComboBox()
        for item in models().values():self.setup_model.addItem(item["name"],item["id"])
        self.setup_model.currentIndexChanged.connect(self.update_setup_voices)
        self.setup_voice=QComboBox()
        self.setup_voice.currentIndexChanged.connect(self.scan_selected)
        ml.addLayout(layout_row(self.setup_model,self.setup_voice))
        self.setup_info=label("Select an engine","muted");ml.addWidget(self.setup_info)
        self.setup_status=label("Not checked","sectiontitle");ml.addWidget(self.setup_status)
        ml.addLayout(layout_row(button("Install engine Python package",self.install_selected_engine),button("Download model files",self.download_selected),button("Real synthesis test",self.verify_selected,True)))
        ml.addLayout(layout_row(button("Check model status",self.scan_selected),button("Open official source",self.open_engine_site),button("Remove model files",self.remove_model)))
        self.setup_ref=label("Cloning verification uses the reference recording selected in Voice Studio.","muted");ml.addWidget(self.setup_ref)
        self.install_bar=QProgressBar();self.install_bar.setRange(0,1);self.install_bar.setValue(0);v.addWidget(self.install_bar)
        self.setup_note=label("No large download starts without a confirmation. Download size is checked via the source server when available.","muted");v.addWidget(self.setup_note)
        v.addStretch(1)
        self.stack.addWidget(self.scroll_wrap(w))
        self.update_setup_voices()
        self.detect_python()

    def current_setup(self):
        return self.setup_model.currentData(),self.setup_voice.currentData() or ""

    def update_setup_voices(self,*_):
        key=self.setup_model.currentData()
        if key is None:return
        entry=models()[key]
        self.setup_voice.blockSignals(True);self.setup_voice.clear()
        for voice in entry.get("voices",[]):self.setup_voice.addItem(voice["name"],voice["id"])
        if not entry.get("voices"):self.setup_voice.addItem("Reference-based","")
        self.setup_voice.blockSignals(False)
        self.setup_info.setText(f"{entry['kind'].upper()} • {entry['device']}\nLicensing: {entry['license_note']}")
        self.setup_status.setText("Not checked")

    def detect_python(self):
        found=services.find_python()
        self.python_status.setText("Runtime Python: "+("verified " + str(json.loads(found)) if found else "NOT FOUND (Store alias is rejected)"))

    def background(self, fn, done=None, labeltext="Working..."):
        if self.busy_installer:
            self.alert("An AI Setup operation is already running.");return
        self.busy_installer=True
        self.install_bar.setRange(0,0)
        self.toast(labeltext)
        worker=TaskThread(fn);self.workers.append(worker)
        worker.log.connect(self.log_message)
        worker.failed.connect(lambda err:self.error_task(err))
        worker.finished_result.connect(lambda result:self.done_task(result,done))
        worker.finished.connect(lambda:self.workers.remove(worker) if worker in self.workers else None)
        worker.start()

    def error_task(self,error):
        self.busy_installer=False;self.install_bar.setRange(0,1);self.install_bar.setValue(0)
        self.alert(error)

    def done_task(self,result,callback):
        self.busy_installer=False;self.install_bar.setRange(0,1);self.install_bar.setValue(1)
        if callback:callback(result)
        self.toast("Operation completed — verify with a real synthesis test before generating.")

    def confirm(self,title,message):
        return QMessageBox.question(self,title,message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes

    def click_install_python(self):
        if not self.confirm("Install Python", "Use Windows Package Manager (WinGet) to install Python 3.11? The installer comes from WinGet's configured package source. Continue?"):return
        self.background(services.install_python,lambda result:self.detect_python(),"Installing Python through WinGet...")

    def install_base(self,mode):
        message=(f"Install the shared Python AI base with {mode} PyTorch, torchaudio, transformers and dependencies?\n\n"
                 "This may download several gigabytes. The total is not reliably available in advance from pip.\n"
                 "Models will NOT become 'Ready' just because these packages install.\n\nProceed?")
        if not self.confirm("Confirm large dependency download",message):return
        self.background(lambda log:services.ensure_base(mode,log),labeltext="Installing shared dependencies...")

    def test_cuda(self):
        self.background(services.gpu_diagnostics,labeltext="Testing actual PyTorch CUDA availability...")

    def install_selected_engine(self):
        key,_=self.current_setup();entry=models()[key]
        if not self.confirm("Install AI engine",f"Install package {entry['package']} into the app-controlled shared Python folder?\n\nThe exact total dependency size may not be available before pip resolves it. Dependencies could conflict with other engines.\n\nContinue?"):return
        self.background(lambda log:services.install_engine(key,log),lambda x:self.scan_selected(),f"Installing {entry['name']}...")

    def download_selected(self):
        model,voice=self.current_setup()
        if not services.voice_files(model,voice):
            self.alert("There are no downloadable model files declared for this adapter.")
            return
        self.background(lambda log:services.download_plan(model,voice),
                        lambda plan:self.confirm_download_plan(model,voice,plan),
                        "Checking model file sizes on source servers...")

    def confirm_download_plan(self,model,voice,plan):
        if not plan:
            self.toast("All model files appear present. Run a real synthesis test.")
            return
        total=sum(item["size"] or 0 for item in plan)
        unknown=sum(item["size"] is None for item in plan)
        lines="\n".join(f"• {x['name']} — {x['size']/1024**2:.1f} MiB" if x["size"] is not None else f"• {x['name']} — server did not report size" for x in plan)
        message=f"Download {len(plan)} files to:\n{services.model_folder(model,voice)}\n\n{lines}\n\nKnown size: {total/1024**2:.1f} MiB"
        if unknown:
            message+=f"\nWARNING: {unknown} file(s) have unreported size. Exact total cannot be verified before downloading."
        message+="\n\nReview the licensing of the voice/model yourself. Start downloading?"
        if not self.confirm("Approve model downloads",message):return
        self.background(lambda log:services.download_model(model,voice,log),
                        lambda x:(self.refresh_library(),self.scan_selected()),
                        "Downloading approved model files...")

    def verify_selected(self):
        model,voice=self.current_setup()
        self_ref=self.ref_file.text() if models()[model]["kind"]=="clone" else ""
        if models()[model]["kind"]=="clone" and not (self_ref and self.cloning_consent.isChecked()):
            self.alert("Choose a licensed reference in Voice Studio and check the permission box first.");return
        if not self.confirm("Run REAL test",f"Generate a real short WAV from {model} / {voice or 'reference'}?\n\nNo automatic model-weight download is permitted inside the synthesis process. Only locally downloaded weights are used."):return
        selected_options=self.options() if self.model_combo.currentData()==model else {"device":"cpu","speed":1.0}
        self.background(lambda log:services.test_engine(self.store,model,voice,selected_options,self_ref,self.ref_text.text(),log),lambda p:self.verify_done(p,model,voice),"Generating a real WAV verification sample...")

    def verify_done(self,path,model,voice):
        self.scan_selected();self.toast(f"Verified actual audio: {path}")

    def scan_selected(self,*_):
        if not hasattr(self,"setup_status"):return
        model,voice=self.current_setup()
        self.setup_status.setText("Checking packages and verification record...")
        if getattr(self,"scan_busy",False):return
        self.scan_busy=True
        th=TaskThread(lambda log: services.readiness(self.store,model,voice))
        self.workers.append(th)
        def received(status):
            self.scan_busy=False
            if self.current_setup()==(model,voice):self.setup_status.setText(status)
            else:self.scan_selected()
        def failed(error):
            self.scan_busy=False
            if self.current_setup()==(model,voice):self.setup_status.setText("Check failed: "+error)
            else:self.scan_selected()
        th.finished_result.connect(received)
        th.failed.connect(failed)
        th.finished.connect(lambda:self.workers.remove(th) if th in self.workers else None)
        th.start()

    def remove_model(self):
        model,voice=self.current_setup()
        folder=services.model_folder(model,voice)
        if not self.confirm("Remove local weights",f"Delete model files from:\n{folder}\n\nThis does not uninstall other engines or delete any generated audio."):return
        if folder.resolve() not in (services.model_folder(model,voice).resolve(),):return
        for path in folder.iterdir():
            if path.is_file():path.unlink()
            elif path.is_dir():shutil.rmtree(path)
        self.store.invalidate(model+":"+voice)
        self.scan_selected();self.refresh_library()

    def open_engine_site(self):
        model,_=self.current_setup()
        QDesktopServices.openUrl(QUrl(models()[model]["repo"]))

    def make_settings(self):
        w,v=page("Settings","Storage and processing controls. The installed app and your data are kept separate.")
        card,cv=panel();v.addWidget(card)
        cv.addWidget(label("Processing","sectiontitle"))
        self.parallel=QSpinBox();self.parallel.setRange(1,4);self.parallel.setValue(self.store.setting("parallel_jobs",1))
        cv.addWidget(label("Maximum simultaneous jobs (resource-aware; torch models run conservatively)","muted"))
        cv.addWidget(self.parallel)
        cv.addWidget(button("Save settings",self.save_settings,True))
        storage,sv=panel();v.addWidget(storage)
        sv.addWidget(label("Local folders","sectiontitle"))
        sv.addWidget(label(str(root()),"muted"))
        sv.addLayout(layout_row(button("Open audio exports",lambda:self.open_path(subdir("outputs"))),button("Open all application data",lambda:self.open_path(root()))))
        update,uv=panel();v.addWidget(update)
        uv.addWidget(label("Secure software updates","sectiontitle"))
        uv.addWidget(label("No update server or code-signing certificate is bundled. Enter your HTTPS release manifest and an exact trusted certificate thumbprint to enable manually approved, signature-verified updates.","muted"))
        self.update_url=QLineEdit(self.store.setting("update_url",""));self.update_url.setPlaceholderText("https://your-domain.example/release.json")
        self.publisher=QLineEdit(self.store.setting("trusted_publisher",""));self.publisher.setPlaceholderText("Pinned Authenticode certificate thumbprint (40 hexadecimal digits)")
        uv.addWidget(self.update_url);uv.addWidget(self.publisher)
        uv.addLayout(layout_row(button("Save update channel",self.save_update),button("Check for signed update",self.check_updates)))
        v.addStretch(1);self.stack.addWidget(self.scroll_wrap(w))

    def save_settings(self):
        self.store.set_setting("parallel_jobs",self.parallel.value())
        self.toast("Processing settings saved")

    def save_update(self):
        url=self.update_url.text().strip()
        if url and not url.startswith("https://"):
            self.alert("Only HTTPS update feed addresses are allowed.");return
        self.store.set_setting("update_url",url)
        self.store.set_setting("trusted_publisher",self.publisher.text().strip())
        self.toast("Update channel saved. Installation requires explicit confirmation and signature verification.")

    def check_updates(self):
        url=self.update_url.text().strip()
        fingerprint=self.publisher.text().strip().replace(" ","")
        if not url or len(fingerprint)!=40:
            self.alert("Configure and save a HTTPS update feed plus exact signer certificate thumbprint first.");return
        self.background(lambda log:services.check_update_feed(url),lambda info:self.update_found(info,fingerprint),"Checking signed release manifest...")

    def update_found(self,info,fingerprint):
        if not info["newer"]:
            self.toast("This version is already current according to the configured feed.")
            return
        size= f"{info['size']/(1024**2):.1f} MiB" if info.get('size') is not None else "unknown (server did not report size)"
        prompt=(f"Version {info['version']} available.\nDownload: {size}\nSource: {info['url']}\n\n"
                "After downloading, both SHA-256 and Windows Authenticode signer identity will be checked.\n"
                "The installer will only open if ALL checks pass. Proceed?")
        if not self.confirm("Approve signed update download",prompt):return
        self.background(lambda log:services.download_signed_update(info,fingerprint,log),self.launch_verified_update,"Downloading and verifying update...")

    def launch_verified_update(self,path):
        if not self.confirm("Install verified update", f"The update passed hash and certificate verification.\n\nLaunch {path.name} and close AI Voice Studio?\n\nSave and pause any queue jobs before proceeding."):return
        if self.running:
            self.alert("Pause the running jobs first.");return
        from . import shutdown
        shutdown.spawn_free([str(path)],close_fds=True)
        QApplication.instance().quit()

    def make_logs(self):
        w,v=page("Activity Logs","Download details, precise errors, adapter testing, and job generation are recorded here and in a local text file.")
        self.log_text=QTextEdit();self.log_text.setReadOnly(True);v.addWidget(self.log_text)
        v.addLayout(layout_row(button("Open log folder",lambda:self.open_path(subdir("logs"))),button("Clear visible log",self.log_text.clear)))
        self.stack.addWidget(w)

    def log_message(self,line):
        from datetime import datetime
        msg=f"[{datetime.now().strftime('%H:%M:%S')}] {line}"
        if hasattr(self,"log_text"):self.log_text.append(msg.replace("<","&lt;").replace(">","&gt;"))
        with (subdir("logs")/"studio.log").open("a",encoding="utf-8") as fp:fp.write(msg+"\n")

    def update_resources(self):
        try:self.resources_label.setText(services.resources())
        except Exception:self.resources_label.setText("Resources unavailable")

    def toast(self,message):
        self.footer.setText(message)
        self.statusBar().showMessage(message,6000)

    def alert(self,message):
        QMessageBox.warning(self,"AI Voice Studio",message)
        self.toast(message)

    def closeEvent(self,event):
        if self.running or self.busy_installer:
            self.alert("Jobs/downloads are active. Pause jobs and finish downloads before closing to avoid interrupted files.")
            event.ignore();return
        event.accept()
