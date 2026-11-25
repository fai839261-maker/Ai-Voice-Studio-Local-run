"""Simplified v0.2 desktop workflow. All setup tasks communicate through a GUI-thread event queue."""
from __future__ import annotations
import os,queue,threading,time,shutil,subprocess,traceback,zipfile,re
from pathlib import Path
from xml.etree import ElementTree as ET
from PySide6.QtCore import QTimer,QUrl
from PySide6.QtWidgets import (QApplication,QComboBox,QDialog,QDialogButtonBox,QFileDialog,QHBoxLayout,QLabel,QLineEdit,QMessageBox,QProgressBar,QSpinBox,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget,QHeaderView,QPushButton)
from PySide6.QtGui import QDesktopServices,QTextCursor
from .ui import StudioWindow as Original, page,panel,label,button,layout_row
from .core import models,catalog,root,subdir,sha256,is_audio,APP_VERSION
from . import managed,services,external

from .importers import DOC_FILTER,read_script

class StudioWindow(Original):
    def __init__(self):
        self.tasks={}; self.events=queue.Queue(); self.setup_cards={}
        super().__init__()
        self.event_timer=QTimer(self);self.event_timer.timeout.connect(self.drain_events);self.event_timer.start(100)
        self.timer.timeout.connect(self.refresh_queue_if_running)
        QTimer.singleShot(600,self.first_launch)

    def first_launch(self):
        if self.store.setting('initial_setup_approved',False):return
        if self.confirm('Automatic first-run setup','Prepare the recommended AI runtime now?\n\nAI Voice Studio checks existing compatible Python and its own Qwen/PyTorch environment first. Missing Python is installed through WinGet; missing dependencies through pip. This may download several GB. No model weights are downloaded yet.\n\nYou can also skip and install any model later in AI Setup.'):
            self.store.set_setting('initial_setup_approved',True)
            device=self.preferred_device();self.start_task('runtime',None,lambda cancel,progress,log:managed.ensure_engine('qwen06bvoice',device,log,cancel),'Preparing recommended AI dependencies')
        else:self.store.set_setting('initial_setup_approved',True)

    def preferred_device(self):
        mode=self.store.setting('compute_mode','auto')
        if mode=='cpu':return 'cpu'
        return 'cuda' if managed.nvidia_smi() else 'cpu'    # 'auto' and 'cuda': the graphics card whenever there is one

    def make_studio(self):
        super().make_studio()
        w=self.stack.widget(0).widget(); lay=w.layout()
        self.segment_mode=QComboBox();self.segment_mode.addItem('Paragraph chunks (recommended)','paragraph');self.segment_mode.addItem('One request — whole script','whole');self.segment_mode.addItem('Sentence chunks','sentence')
        lay.insertLayout(3,layout_row(label('Generation mode','muted'),self.segment_mode))
        self.device.setCurrentIndex(1 if self.preferred_device()=='cuda' else 0)
        for btn in w.findChildren(QPushButton):
            if btn.text()=='Import script (.txt)':btn.setText('Import TXT / DOCX')
        self.script.setPlaceholderText('Paste a script or import a Word document. Blank lines separate paragraphs.')

    def refresh_voices(self,*args):
        super().refresh_voices(*args)
        if hasattr(self,'opt_widgets') and self.model_combo.currentData():
            visible=models()[self.model_combo.currentData()].get('torch',False)
            for obj in self.opt_widgets['device']:obj.setVisible(visible)

    def options(self):
        opt=super().options();opt['segment_mode']=self.segment_mode.currentData() if hasattr(self,'segment_mode') else 'paragraph'
        if models()[self.model_combo.currentData()].get('torch'):opt['device']='cuda' if self.device.currentIndex()==1 else 'cpu'
        return opt

    def import_script(self):
        path,_=QFileDialog.getOpenFileName(self,'Import script',str(Path.home()),DOC_FILTER)
        if path:
            try:self.script.setPlainText(read_script(path))
            except Exception as e:self.alert(str(e))

    def import_batch(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Import scripts',str(Path.home()),DOC_FILTER)
        if not paths:return
        d=QDialog(self);d.setWindowTitle('Assign voices to batch');d.resize(850,480);v=QVBoxLayout(d)
        v.addWidget(label('Choose engine and voice for each script, or apply the first row to all. Cloning rows share the authorized reference selected in Voice Studio.'))
        table=QTableWidget(len(paths),3);table.setHorizontalHeaderLabels(['Script','Engine','Voice']);table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);v.addWidget(table)
        rows=[]
        def fill(mc,vc):
            vc.clear();entry=models()[mc.currentData()]
            for x in entry['voices']:vc.addItem(x['name'],x['id'])
            if not entry['voices']:vc.addItem('Authorized reference','')
        for i,path in enumerate(paths):
            table.setItem(i,0,QTableWidgetItem(Path(path).name));mc=QComboBox();vc=QComboBox()
            for e in models().values():mc.addItem(e['name'],e['id'])
            mc.setCurrentIndex(mc.findData(self.model_combo.currentData()));fill(mc,vc);vc.setCurrentIndex(max(0,vc.findData(self.voice_combo.currentData())))
            mc.currentIndexChanged.connect(lambda _,m=mc,c=vc:fill(m,c));table.setCellWidget(i,1,mc);table.setCellWidget(i,2,vc);rows.append((mc,vc))
        def same():
            model,voice=rows[0][0].currentData(),rows[0][1].currentData()
            for mc,vc in rows[1:]:mc.setCurrentIndex(mc.findData(model));vc.setCurrentIndex(max(0,vc.findData(voice)))
        v.addWidget(button('Apply first row voice to all scripts',same));buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(d.accept);buttons.rejected.connect(d.reject);v.addWidget(buttons)
        if not d.exec():return
        if any(models()[m.currentData()]['kind']=='clone' for m,_ in rows) and not (self.ref_file.text() and self.cloning_consent.isChecked()):self.alert('Select an authorized reference and confirm permission in Voice Studio before importing cloning jobs.');return
        count=0
        for path,(mc,vc) in zip(paths,rows):
            try:
                opt={'segment_mode':self.segment_mode.currentData(),'target_chars':self.target_chars.value(),'pause_ms':self.pause.value(),'device':self.preferred_device()}
                # Only propagate settings supported by this row's engine.
                for key,val in [('speed',self.speed.value()),('exaggeration',self.exaggeration.value()),('cfg_weight',self.cfg.value())]:
                    if key in models()[mc.currentData()]['supports']:opt[key]=val
                self.store.new_job(Path(path).stem,read_script(path),mc.currentData(),vc.currentData(),self.ref_file.text(),self.ref_text.text(),opt);count+=1
            except Exception as e:self.log_message('ERROR: '+str(e))
        self.change_page(1);self.toast(f'{count} scripts queued. Select Process all pending to start.')

    def make_queue(self):
        super().make_queue();self.stack.widget(1).layout().addWidget(button('Save final WAV as…',self.save_audio,True))

    def open_output(self):
        jid=self.selected_job()
        if jid and self.store.job(jid)["status"]=="completed":super().open_output()
        else:self.alert("Complete the job before opening its final audio.")

    def export_mp3(self):
        jid=self.selected_job()
        if jid and self.store.job(jid)["status"]=="completed":super().export_mp3()
        else:self.alert("Complete the job before exporting MP3.")

    def save_audio(self):
        jid=self.selected_job()
        if not jid:return
        job=self.store.job(jid);src=Path(job['output'])
        if job['status']!='completed' or not is_audio(src):self.alert('Finish the entire job before saving its final audio.');return
        dest,_=QFileDialog.getSaveFileName(self,'Save final audio',str(Path.home()/src.name),'WAV audio (*.wav)')
        if dest:
            try:
                if src.resolve()!=Path(dest).resolve():shutil.copy2(src,dest)
                self.toast('Saved '+dest)
            except OSError as e:self.alert(str(e))

    def refresh_queue_if_running(self):
        if self.running:self.refresh_queue()

    def make_setup(self):
        w,v=page('AI Setup','Install a voice model with one action. Required dependencies are checked and installed automatically.');self.install_bar=QProgressBar();self.install_bar.hide() # compatibility with inherited export worker
        box,bv=panel();v.addWidget(box);bv.addWidget(label('AI runtime','sectiontitle'));bv.addWidget(label('Python installation is automatic when a model needs it. Existing compatible installations are reused.','muted'))
        self.bootstrap_status=label('Ready to prepare dependencies','muted');bv.addWidget(self.bootstrap_status)
        bv.addLayout(layout_row(button('Install / repair Python',self.install_runtime,True),button('Prepare recommended AI dependencies',self.prepare_base),button('Cancel preparation',lambda:self.cancel_task('runtime'))))
        self.downloads_limit=3
        for heading,kind in [('Text to speech','tts'),('Voice cloning','clone')]:
            v.addWidget(label(heading,'sectiontitle'))
            for m in models().values():
                if m['kind']!=kind:continue
                key=m['id'];card,cv=panel();v.addWidget(card);cv.addWidget(label(m['name'],'sectiontitle'));cv.addWidget(label(m['description'],'muted'));cv.addWidget(label(m['device']+' • '+m['license_note'],'muted'))
                voice=QComboBox()
                for x in m['voices']:voice.addItem(x['name'],x['id'])
                if not m['voices']:voice.addItem('Authorized reference','')
                cv.addWidget(voice);status=label('Not downloaded','muted');cv.addWidget(status)
                bar=QProgressBar();bar.setRange(0,1000);bar.setValue(0);bar.setTextVisible(False);cv.addWidget(bar)
                bytes_label=label('0 GB downloaded','muted');cv.addWidget(bytes_label)
                install=button('Download & set up',lambda _,k=key:self.install_card(k),True);cancel=button('Cancel',lambda _,k=key:self.cancel_task(k));test=button('Test / use',lambda _,k=key:self.test_card(k));remove=button('Remove files',lambda _,k=key:self.remove_card(k));source=button('Source',lambda _,u=m['repo']:QDesktopServices.openUrl(QUrl(u)))
                cv.addLayout(layout_row(install,cancel,test,remove,source));cancel.setEnabled(False)
                self.setup_cards[key]=dict(voice=voice,status=status,bar=bar,bytes=bytes_label,install=install,cancel=cancel,test=test,remove=remove)
                voice.currentIndexChanged.connect(lambda _,k=key:self.refresh_card(k))
        v.addWidget(label('Additional model integrations','sectiontitle'))
        for m in catalog().get('discovery',[]):
            card,cv=panel();v.addWidget(card);cv.addWidget(label(m['name']+' — adapter validation required','sectiontitle'));cv.addWidget(label(m['reason'],'muted'));cv.addWidget(button('Official repository',lambda _,u=m['repo']:QDesktopServices.openUrl(QUrl(u))))
        v.addLayout(layout_row(button('Find new Hugging Face models',lambda:QDesktopServices.openUrl(QUrl('https://huggingface.co/models?pipeline_tag=text-to-speech&sort=trending'))),button('Refresh model status',self.scan_selected)))
        v.addStretch();self.stack.addWidget(self.scroll_wrap(w));self.scan_selected()

    def scan_selected(self,*_):
        for key in self.setup_cards:self.refresh_card(key)

    def refresh_card(self,key):
        if key in self.tasks:return
        c=self.setup_cards[key];voice=c['voice'].currentData() or ''
        # Fast filesystem-only status; the actual synthesis test verifies imports and weights.
        downloaded=managed.downloaded(key,voice)
        c['status'].setText('Downloaded • test required' if downloaded else 'Not downloaded')
        record=self.store.verification(key+':'+voice)
        if downloaded and record and is_audio(Path(record['testfile'])):c['status'].setText('Previously tested • rechecked before generation')
        c['test'].setEnabled(downloaded and managed.engine_python(key).is_file());c['install'].setText('Repair / complete setup' if downloaded else 'Download & set up')
        if downloaded:
            total=sum(p.stat().st_size for p in services.model_folder(key,voice).rglob('*') if p.is_file() and not p.name.endswith('.part'))
            c['bar'].setValue(1000);c['bytes'].setText(f'{total/1e9:.2f} GB on disk')
        else:c['bar'].setValue(0);c['bytes'].setText('Size checked before download')

    def install_runtime(self):
        if 'runtime' in self.tasks:return
        if self.running:self.alert('Pause audio jobs first.');return
        if self.confirm('Install Python','Check existing Python 3.11 x64 and automatically install it through WinGet only if missing?'):
            self.start_task('runtime',None,lambda c,p,l:managed.ensure_runtime(l,c),'Preparing Python')

    def prepare_base(self):
        if 'runtime' in self.tasks:return
        if self.running:self.alert('Pause audio jobs first.');return
        if self.confirm('AI dependencies','Prepare Qwen/PyTorch and its dependencies? Existing valid packages are reused. Missing packages can be several GB.'):
            device=self.preferred_device();self.start_task('runtime',None,lambda c,p,l:managed.ensure_engine('qwen06bvoice',device,l,c),'Preparing AI dependencies')

    def install_card(self,key):
        if self.running or self.busy_installer:self.alert('Pause audio jobs and finish exports before changing engine dependencies.');return
        if key in self.tasks:return
        if sum(k in self.setup_cards for k in self.tasks)>=self.store.setting('parallel_downloads',3):self.alert('Download limit reached. Change simultaneous downloads in Settings or wait for a slot.');return
        voice=self.setup_cards[key]['voice'].currentData() or '';device=self.preferred_device()
        # Discover metadata in background before approval. No weights are downloaded at this stage.
        self.start_task(key,voice,lambda c,p,l:managed.file_manifest(key,voice),'Checking model size',phase='plan',device=device)

    def approve_plan(self,key,task,items):
        total=sum(x.get('size') or 0 for x in items);unknown=any(x.get('size') is None for x in items);voice=task['voice'];device=task['device']
        message=f"Model weights: {'at least ' if unknown else ''}{total/1e9:.2f} GB{' (some source sizes unavailable)' if unknown else ''}.\nExisting valid files are reused. Missing engine dependencies may add several GB (pip total is not reliably available).\nDestination: {services.model_folder(key,voice)}\n\nDownload and install?"
        if not self.confirm('Approve download',message):return
        def work(cancel,progress,log):
            managed.download_files(key,voice,items,progress,cancel)
            progress(0,None,'Preparing engine dependencies (pip; exact total unavailable)');managed.ensure_engine(key,device,log,cancel)
            return 'Downloaded and installed • test required'
        self.start_task(key,voice,work,'Downloading model files',device=device)

    def start_task(self,key,voice,fn,message,phase='work',device=None):
        if key in self.tasks:return
        cancel=threading.Event();self.tasks[key]=dict(cancel=cancel,voice=voice,phase=phase,device=device)
        if key in self.setup_cards:
            c=self.setup_cards[key];c['status'].setText(message);c['voice'].setEnabled(False);c['install'].setEnabled(False);c['remove'].setEnabled(False);c['test'].setEnabled(False);c['cancel'].setEnabled(True);c['bar'].setRange(0,0)
        else:self.bootstrap_status.setText(message)
        def run():
            last=[0.0]
            def progress(done,total,stage):
                now=time.monotonic()
                if now-last[0]>.15 or (total and done==total):self.events.put(('progress',key,(done,total,stage)));last[0]=now
            def log(line):
                if line.strip():self.events.put(('detail',key,line[-250:]))
            try:result=fn(cancel,progress,log);managed.check_cancel(cancel);self.events.put(('done',key,result))
            except managed.Cancelled as e:self.events.put(('cancelled',key,str(e)))
            except Exception as e:self.events.put(('error',key,str(e)+'\n'+traceback.format_exc()))
        threading.Thread(target=run,daemon=True).start()

    def cancel_task(self,key):
        if key in self.tasks:self.tasks[key]['cancel'].set();self.toast('Cancellation requested; network reads may take up to their timeout.')

    def drain_events(self):
        for _ in range(250):
            try:kind,key,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=='job_detail':
                self.toast(str(value)[-250:]);continue
            if kind=='job_end':
                self.handle_job_end(key,value);continue
            if key not in self.tasks:continue
            c=self.setup_cards.get(key)
            if kind=='progress':
                done,total,stage=value
                if c:
                    c['bar'].setRange(0,1000 if total else 0);c['bar'].setValue(min(1000,int(done/total*1000)) if total else 0);c['bytes'].setText(f'{done/1e9:.3f} / {total/1e9:.3f} GB' if total else f'{done/1e9:.3f} GB • total unavailable');c['status'].setText(stage)
            elif kind=='detail':self.toast(f'{key}: {value}')
            else:
                task=self.tasks.pop(key)
                if c:
                    old_bytes=c['bytes'].text();old_progress=c['bar'].value()
                    c['voice'].setEnabled(True);c['install'].setEnabled(True);c['cancel'].setEnabled(False);c['remove'].setEnabled(True);c['bar'].setRange(0,1000);self.refresh_card(key)
                if kind=='done' and task['phase']=='plan':self.approve_plan(key,task,value);continue
                if kind=='done' and task['phase']=='test':
                    if c:c['status'].setText('READY • real audio test passed')
                    self.use_card(key,task['voice']);self.toast('Real audio test passed. Voice selected.');continue
                if kind=='error':
                    self.log_message('ERROR: '+value);status='Failed — see Activity Logs';self.toast(status)
                elif kind=='cancelled':
                    status='Cancelled • partial files retained'
                    if c:c['bytes'].setText(old_bytes);c['bar'].setValue(max(0,old_progress))
                else:status='Setup completed • run test' if c else 'Runtime preparation complete'
                if c:c['status'].setText(status)
                else:self.bootstrap_status.setText(status)

    def use_card(self,key,voice):
        self.model_combo.setCurrentIndex(self.model_combo.findData(key));self.voice_combo.setCurrentIndex(max(0,self.voice_combo.findData(voice)));self.change_page(0)

    def test_card(self,key):
        if self.tasks or self.running or self.busy_installer:self.alert('Finish setup/downloads and pause active jobs before testing a model.');return
        voice=self.setup_cards[key]['voice'].currentData() or '';m=models()[key];ref=self.ref_file.text();transcript=self.ref_text.text()
        if m['kind']=='clone' and not(ref and self.cloning_consent.isChecked()):self.use_card(key,voice);self.alert('Choose an authorized reference WAV and confirm permission, then return to AI Setup and test.');return
        if 'ref_text' in m['supports'] and not transcript.strip():self.use_card(key,voice);self.alert('Enter the exact reference recording transcript first.');return
        opts={'device':self.preferred_device(),'speed':1.0}
        self.start_task(key,voice,lambda c,p,l:services.test_engine(self.store,key,voice,opts,ref,transcript,l),'Testing real audio generation',phase='test')
        self.setup_cards[key]['cancel'].setEnabled(False) # original inference worker cannot interrupt mid-call

    def remove_card(self,key):
        if self.running or self.tasks:self.alert('Finish setup or active jobs before removing model files.');return
        voice=self.setup_cards[key]['voice'].currentData() or ''
        if self.confirm('Remove model weights','Remove this model’s local weights and partial downloads? Existing audio and scripts remain.'):
            shutil.rmtree(services.model_folder(key,voice));self.store.invalidate(key+':'+voice);self.refresh_card(key)

    def refresh_library(self):
        entries=[(m,v) for m in models().values() for v in m.get('voices',[])];self.library_table.setRowCount(len(entries))
        for i,(m,v) in enumerate(entries):
            for j,t in enumerate((v['name'],v['language'],m['id'],'Downloaded' if managed.downloaded(m['id'],v['id']) else 'Not downloaded')):self.library_table.setItem(i,j,QTableWidgetItem(t))

    def make_settings(self):
        w,v=page('Settings','Processing and storage.')
        c,cv=panel();v.addWidget(c);cv.addWidget(label('Processing','sectiontitle'));self.parallel=QSpinBox();self.parallel.setRange(1,4);self.parallel.setValue(self.store.setting('parallel_jobs',1));self.down_limit=QSpinBox();self.down_limit.setRange(1,4);self.down_limit.setValue(self.store.setting('parallel_downloads',3));self.compute=QComboBox();self.compute.addItem('Automatic: graphics card when available','auto');self.compute.addItem('NVIDIA graphics card (CUDA)','cuda');self.compute.addItem('Processor only','cpu');self.compute.setCurrentIndex(max(0,self.compute.findData(self.store.setting('compute_mode','auto'))))
        cv.addLayout(layout_row(label('Concurrent audio jobs'),self.parallel));cv.addLayout(layout_row(label('Concurrent model downloads'),self.down_limit));cv.addLayout(layout_row(label('Preferred compute'),self.compute));cv.addWidget(label('Heavy engines are serialized to avoid VRAM exhaustion. Concurrent lightweight jobs obey the selected limit.','muted'));cv.addWidget(button('Save settings',self.save_settings,True))
        st,sv=panel();v.addWidget(st);sv.addWidget(label('Storage','sectiontitle'));sv.addWidget(label(str(root()),'muted'));sv.addLayout(layout_row(button('Open audio exports',lambda:self.open_path(subdir('outputs'))),button('Open app data',lambda:self.open_path(root()))))
        v.addStretch();self.stack.addWidget(self.scroll_wrap(w))

    def save_settings(self):
        self.store.set_setting('parallel_jobs',self.parallel.value());self.store.set_setting('parallel_downloads',self.down_limit.value());self.store.set_setting('compute_mode',self.compute.currentData());self.toast('Settings saved')

    def offline_update(self):
        if self.tasks or self.running or self.busy_installer:self.alert('Finish downloads and pause jobs before updating.');return
        file,_=QFileDialog.getOpenFileName(self,'Select trusted local installer',str(Path.home()),'AI Voice Studio setup (AI_Voice_Studio_Setup_*.exe)')
        if not file:return
        p=Path(file)
        if not re.fullmatch(r'AI_Voice_Studio_Setup_\d+\.\d+\.\d+\.exe',p.name,re.I):self.alert('Select an AI Voice Studio setup EXE.');return
        if tuple(map(int,p.stem.rsplit('_',1)[-1].split('.'))) < tuple(map(int,APP_VERSION.split('.'))):self.alert('Older installers cannot be used as updates.');return
        from PySide6.QtWidgets import QInputDialog
        sidecar=Path(str(p)+'.sha256.txt');default=sidecar.read_text(encoding='ascii',errors='ignore').strip() if sidecar.is_file() else ''
        expected,ok=QInputDialog.getText(self,'Verify offline installer','Confirm the expected SHA-256 from a trusted build or publisher (64 hex characters):',text=default)
        if not ok:return
        if not re.fullmatch('[0-9a-fA-F]{64}',expected.strip()) or sha256(p).lower()!=expected.strip().lower():self.alert('Installer hash does not match. Update rejected.');return
        if self.confirm('Install local update','Hash matches the value you supplied. This confirms file integrity, not publisher identity. Only run an installer you personally built or obtained from a trusted source. Close and launch it?'):
            try:external.popen([str(p),'/AVSOFFLINE=1'],close_fds=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));QApplication.instance().quit()
            except OSError as e:self.alert(str(e))

    def make_logs(self):
        from PySide6.QtWidgets import QTextEdit
        w,v=page('Activity Logs','Errors only. Copy or clear the saved error history.');self.log_text=QTextEdit();self.log_text.setReadOnly(True);v.addWidget(self.log_text)
        self.error_file=subdir('logs')/'errors.log'
        if self.error_file.exists():self.log_text.setPlainText(self.error_file.read_text(encoding='utf-8',errors='replace')[-500000:])
        v.addLayout(layout_row(button('Copy errors',lambda:QApplication.clipboard().setText(self.log_text.toPlainText())),button('Clear errors',self.clear_errors)));self.stack.addWidget(w)

    def clear_errors(self):
        self.log_text.clear();self.error_file.write_text('',encoding='utf-8');self.toast('Error history cleared')

    def log_message(self,line):
        if not hasattr(self,'error_file'):return
        if not (line.startswith(('ERROR:','Traceback','Could not import')) or 'Error:' in line):return
        from datetime import datetime
        msg=f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}'
        self.log_text.moveCursor(QTextCursor.MoveOperation.End);self.log_text.insertPlainText(msg+'\n')
        with self.error_file.open('a',encoding='utf-8') as f:f.write(msg+'\n')

    def alert(self,message):
        self.log_message('ERROR: '+str(message));super().alert(message)

    def run_selected(self):
        if self.tasks or self.busy_installer:self.alert('Wait for engine setup to finish before starting audio generation.');return
        super().run_selected()

    def run_all(self):
        if self.tasks or self.busy_installer:self.alert('Wait for engine setup to finish before starting audio generation.');return
        super().run_all()

    def pump_jobs(self):
        while getattr(self,'pending_jobs',[]) and len(self.running)<self.job_capacity():
            jid=self.pending_jobs[0];job=self.store.job(jid)
            if job and self.running and (models()[job['model']].get('torch') or any(models()[self.store.job(x)['model']].get('torch') for x in self.running)):break
            self.pending_jobs.pop(0);self.launch_job(jid)

    def launch_job(self,jid):
        job=self.store.job(jid)
        if not job or jid in self.running or job['status']=='completed':return
        if len(self.running)>=self.job_capacity():self.alert('Concurrent job limit reached.');return
        if self.running and (models()[job['model']].get('torch') or any(models()[self.store.job(x)['model']].get('torch') for x in self.running)):self.alert('Heavy engines run one at a time to protect memory.');return
        flag=threading.Event();self.stop_flags[jid]=flag
        def process():
            try:services.process_job(self.store,jid,lambda line:self.events.put(('job_detail',jid,line)),flag);self.events.put(('job_end',jid,None))
            except Exception as e:self.events.put(('job_end',jid,str(e)+'\n'+traceback.format_exc()))
        th=threading.Thread(target=process,daemon=True);self.running[jid]=th;th.start();self.toast('Audio generation in progress');self.refresh_queue()

    def background(self,fn,done=None,labeltext="Working..."):
        if self.tasks:self.alert("Finish active AI setup tasks first.");return
        super().background(fn,done,labeltext)

    def closeEvent(self,event):
        if self.tasks:self.alert('Cancel or complete active setup tasks before closing.');event.ignore();return
        super().closeEvent(event)
