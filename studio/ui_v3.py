"""Compact setup, saved clone profiles, monitor, seekable player and task shelf."""
from __future__ import annotations
import os,shutil,threading,queue,traceback,time,json,subprocess,sys
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QUrl,QPropertyAnimation,QEasingCurve
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtWidgets import (QWidget,QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,QFormLayout,QComboBox,QDoubleSpinBox,QLineEdit,QCheckBox,QLabel,QProgressBar,QPushButton,QScrollArea,QSlider,QTableWidget,QTableWidgetItem,QHeaderView,QDialog,QDialogButtonBox,QFileDialog,QInputDialog,QMessageBox,QApplication,QProgressDialog,QSizePolicy,QAbstractItemView)
from .ui_v2 import StudioWindow as V2
from .ui import page,panel,button,label,layout_row
from .core import models,subdir,is_audio,APP_VERSION,root,bundled
from . import managed,services,clones,external
from .importers import DOC_FILTER,read_script
from .setup_queue import available_slots
from .generation_policy import effective_device,automatic_title,setup_state,dependency_state

class SeekSlider(QSlider):
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            v=self.minimum()+round((self.maximum()-self.minimum())*max(0,min(1,event.position().x()/max(1,self.width()-1))))
            self.setValue(v);self.sliderMoved.emit(v)
        super().mousePressEvent(event)

class AudioPlayer(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window;self.path=None;self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio);self.audio.setVolume(.8)
        l=QVBoxLayout(self);l.setContentsMargins(0,0,0,0);self.name=label('No audio selected','muted');l.addWidget(self.name)
        self.slider=SeekSlider(Qt.Orientation.Horizontal);self.slider.setRange(0,0);self.timer=label('00:00 / 00:00','muted')
        self.slider.sliderMoved.connect(self.player.setPosition);self.player.durationChanged.connect(lambda n:self.slider.setRange(0,n));self.player.positionChanged.connect(self.position)
        self.player.errorOccurred.connect(lambda e,msg:window.alert(msg or str(e)))
        l.addLayout(layout_row(self.slider,self.timer));l.addLayout(layout_row(button('Play',self.player.play),button('Pause',self.player.pause),button('− 5s',lambda:self.player.setPosition(max(0,self.player.position()-5000))),button('+ 5s',lambda:self.player.setPosition(min(self.player.duration(),self.player.position()+5000))),button('Export audio',self.save,True)))
    def position(self,n):
        if not self.slider.isSliderDown():self.slider.setValue(n)
        def fmt(x):return f'{x//60000:02d}:{x//1000%60:02d}'
        self.timer.setText(fmt(n)+' / '+fmt(self.player.duration()))
    def load(self,path,play=False):
        path=Path(path)
        if not is_audio(path):self.window.alert('No completed WAV is available.');return
        self.path=path;self.name.setText(path.name);self.player.setSource(QUrl.fromLocalFile(str(path)))
        if play:self.player.play()
    def save(self):
        if not self.path or not is_audio(self.path):return self.window.alert('Generate audio first.')
        from .reference_dialog import export_dialog
        try:
            if export_dialog(self,self.path):self.window.toast('Audio exported')
        except Exception as e:self.window.alert(str(e))

class StudioWindow(V2):
    def __init__(self):
        self.voice_refresh_running=False;self.piper_selected=None;self.job_waits={};self.job_stages={};self.pending_setup=[];self.attached_assets={};self.model_totals={};self.model_plans={};self.move_pending=None;self.audit_running=False;self.resource_busy=False;self.studio_job=None;self.resource_values={}
        super().__init__();self.timer.setInterval(3000)
        try:
            import psutil;psutil.cpu_percent(interval=None)   # starting point for the first meter reading
        except Exception:pass
    def first_launch(self):
        # Audit existing profiles off the UI thread; no unsolicited GB downloads.
        if self.busy_installer:return
        import time as _t
        if _t.time()-float(self.store.setting('audit_ts',0))<12*3600 and not os.environ.get('AVS_NO_PROMPTS'):
            self.refresh_voices();self.check_model_sizes();return          # checked recently: do not repeat the slow package check
        self.audit_running=True
        def work():
            from .environment_audit import audit_existing
            try:
                with managed.INSTALL_LOCK:report=audit_existing(lambda msg:self.events.put(('audit_issue','',msg)))
                self.events.put(('audit_done','',report))
            except Exception as e:self.events.put(('audit_issue','',str(e)));self.events.put(('audit_finished','',None))
        self.bootstrap_status.setText('Checking existing environments…')
        threading.Thread(target=work,daemon=True).start()
        self.refresh_voices();self.check_model_sizes()
    def make_shell(self):
        super().make_shell()
        self.resources_label.hide();bar=self.resources_label.parentWidget().layout();self.meters={}
        bar.setSpacing(14);self.meter_animations={}
        for k,color in [('CPU','#55c7b8'),('RAM','#80a9ef'),('GPU','#b39bea')]:
            wrap=QWidget();wrap.setFixedWidth(160);lay=QHBoxLayout(wrap);lay.setContentsMargins(0,0,0,0);lay.setSpacing(7)
            t=label(k+' —','muted');t.setFixedWidth(71);t.setWordWrap(False)
            p=QProgressBar();p.setFixedSize(80,8);p.setTextVisible(False);p.setRange(0,100)
            p.setStyleSheet('QProgressBar {background:#233b4b;border:0;border-radius:4px;} QProgressBar::chunk {background:'+color+';border-radius:4px;}')
            lay.addWidget(t);lay.addWidget(p);bar.addWidget(wrap);self.meters[k]=(t,p)
            anim=QPropertyAnimation(p,b"value",self);anim.setDuration(350);anim.setEasingCurve(QEasingCurve.Type.OutCubic);self.meter_animations[k]=anim
        self.footer.setText('Whatsapp: +92 3104768426');self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
    def toast(self,message):
        # Do not create a QStatusBar or place transient text in the footer.
        pass
    def update_resources(self):
        if self.resource_busy or self.move_pending:return
        if self.isMinimized() and not (self.running or getattr(self,'pending_jobs',[]) or self.tasks):return   # nobody sees the meters when minimized and nothing is waiting
        self.resource_busy=True
        def work():
            data={}
            try:
                import psutil
                from . import load_control
                vm=psutil.virtual_memory();data['CPU']=psutil.cpu_percent(interval=None);data['RAM']=vm.percent   # no waiting inside the reading
                data['APP']=load_control.app_usage()
                if managed.nvidia_smi():
                    p=external.run([managed.nvidia_smi(),'--query-gpu=utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,creationflags=managed.FLAGS)
                    if p.returncode==0:
                        gpu,used,total=map(float,p.stdout.splitlines()[0].split(','));data['GPU']=gpu;data['VRAM']=(used,total)
            except Exception:pass
            self.events.put(('resources','',data))
        threading.Thread(target=work,daemon=True).start()
    def make_setup(self):
        w,v=page('AI Setup','One-click model setup. Downloads may be several GB.');v.setSpacing(8);self.install_bar=QProgressBar();self.install_bar.hide()
        self.voice_refresh_status=label("Voice lists ready", "muted");self.voice_refresh_button=button("Refresh voices",self.refresh_voice_catalog);v.addLayout(layout_row(self.voice_refresh_status,self.voice_refresh_button,button("Piper voices",self.choose_piper_voice)))
        for heading,kind in [('Text to speech','tts'),('Voice cloning','clone'),('Voice design','design'),('Word checker','check')]:
            if not any(m['kind']==kind for m in models().values()):continue
            v.addWidget(label(heading,'sectiontitle'))
            for m in models().values():
                if m['kind']!=kind:continue
                key=m['id'];card,cv=panel();cv.setSpacing(4);cv.setContentsMargins(10,6,10,6);v.addWidget(card)
                top=QHBoxLayout();name=label(self.model_title(m),'sectiontitle');name.setStyleSheet('font-size:14px;');name.setWordWrap(False);name.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred);name.setToolTip(self.model_title(m));top.addWidget(name,1);source=button('S',lambda _,k=key:self.show_model_sources(k));source.setToolTip('Model repository, packages and local adapter');source.setFixedWidth(32);source.setStyleSheet('padding:5px;');status=label('Not downloaded','muted');status.setWordWrap(False);top.addWidget(status);cv.addLayout(top)
                desc=label(m['description'],'muted');desc.setWordWrap(False);desc.setToolTip(m['license_note']);cv.addWidget(desc)
                bottom=QHBoxLayout();bar=QProgressBar();bar.setRange(0,1000);bar.setTextVisible(False);bar.setFixedHeight(7);bottom.addWidget(bar,1);size=label('','muted');size.setWordWrap(False);bottom.addWidget(size)
                install=button('Download',lambda _,k=key:self.install_card(k),True);cancel=button('Cancel',lambda _,k=key:self.cancel_task(k));install.setFixedWidth(112);cancel.setFixedWidth(70);install.setStyleSheet('padding:5px;');cancel.setStyleSheet('padding:5px;');cancel.setEnabled(False);bottom.addWidget(install);bottom.addWidget(source);bottom.addWidget(cancel);attach=button('＋',lambda _,k=key:self.attach_model(k));attach.setToolTip('Attach ZIP or folder');attach.setFixedWidth(36);bottom.insertWidget(bottom.count()-1,attach);cv.addLayout(bottom)
                detail=label('','muted');detail.setWordWrap(False);detail.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);detail.hide();cv.addWidget(detail)
                transfer=label('','muted');transfer.setWordWrap(False);transfer.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);transfer.hide();cv.addWidget(transfer)
                self.setup_cards[key]=dict(status=status,bar=bar,bytes=size,install=install,cancel=cancel,detail=detail,transfer=transfer,attach=attach,desc=desc,base_desc=m['description'])
        v.addWidget(label('Add Hugging Face model','sectiontitle'))
        custom,cl=panel();self.hf_input=QLineEdit();self.hf_input.setPlaceholderText('https://huggingface.co/owner/model or owner/model');cl.addLayout(layout_row(self.hf_input,button('Add model',self.add_hf_model,True)));cl.addWidget(label('Supported: existing catalog models and public standard VITS/MMS models with safetensors. Other architectures require an adapter.','muted'));v.addWidget(custom)
        v.addStretch();self.stack.addWidget(self.scroll_wrap(w));self.scan_selected()
    @staticmethod
    def model_title(m):
        name=m['name'].replace(' (experimental)','').replace(' · Voice Cloning','').replace(' CustomVoice','').replace(' · Voice cloning','')
        mode='Cloning' if m['kind']=='clone' else 'Custom voices' if m['id'].startswith('qwen') else 'Built-in voices'
        hf=m.get('hf_repo') or any(v.get('repo') for v in m.get('voices',[])) or any('huggingface.co' in f['url'] for f in m.get('files',[]))
        return f"{name} ({mode}) {'H' if hf else 'G'}"
    def check_model_sizes(self):
        # Read-only metadata preview. Never downloads model data or dependencies.
        keys=list(self.setup_cards)
        def work():
            for key in keys:
                if self.move_pending:break
                try:
                    plans=[(v,managed.file_manifest(key,v,persist=False)) for v in self.card_voices(key)]
                    items=[i for _,plan in plans for i in plan]
                    total=sum(i['size'] for i in items) if items and all(i.get('size') is not None for i in items) else None
                    self.events.put(('model_size',key,(total,plans)))
                except Exception:pass # actual install logs authoritative errors
        threading.Thread(target=work,daemon=True).start()
    def attach_model(self,key):
        if key in self.tasks or self.busy_installer or self.move_pending or all(managed.downloaded(key,v) for v in self.card_voices(key)):return
        choice=QMessageBox(self);choice.setWindowTitle('Attach model');choice.setText('Choose a trusted model ZIP or folder. Missing files will download automatically.')
        z=choice.addButton('ZIP',QMessageBox.ButtonRole.ActionRole);f=choice.addButton('Folder',QMessageBox.ButtonRole.ActionRole);choice.addButton(QMessageBox.StandardButton.Cancel);choice.exec()
        if choice.clickedButton()==z:path,_=QFileDialog.getOpenFileName(self,'Attach ZIP',str(Path.home()),'ZIP (*.zip)')
        elif choice.clickedButton()==f:path=QFileDialog.getExistingDirectory(self,'Attach folder',str(Path.home()))
        else:return
        if not path:return
        if not self.confirm('Trusted model files','Only attach files from a source you trust. Files are checked against known sizes and hashes where available. Source code is not executed; missing weights and approved dependencies download from configured sources. Continue?'):return
        self.attached_assets[key]=path;self.install_card(key)
    def show_model_sources(self,key):
        from .model_sources import source_details
        details=source_details(key)
        dlg=QDialog(self);dlg.setWindowTitle(models()[key]['name']+' — Sources');dlg.resize(610,350)
        outer=QVBoxLayout(dlg);scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();v=QVBoxLayout(body)
        links=[x for x in details['links'] if not x['label'].startswith(('File:', 'Dependency:')) and x['label']!='Python dependency index']
        for item in links:
            b=button(item['label'],lambda _,u=item['url']:QDesktopServices.openUrl(QUrl(u)));b.setToolTip(item['url']);v.addWidget(b)
        v.addWidget(label('Packages: '+', '.join(details['requirements']),'muted'))
        v.addWidget(label('Adapter: '+Path(details['adapter']).name,'muted'))
        v.addWidget(label(models()[key].get('language_note','Voices and language depend on the installed checkpoint.'),'muted'));v.addWidget(label(details['license'],'muted'));v.addStretch();scroll.setWidget(body);outer.addWidget(scroll)
        outer.addWidget(button('Close',dlg.accept));dlg.exec()

    def add_hf_model(self):
        if self.tasks or self.running or self.busy_installer or getattr(self,'pending_jobs',[]):return self.alert('Finish active setup and jobs before adding a model.')
        value=self.hf_input.text();self.busy_installer=True
        def work():
            try:
                from .hf_import import import_repo
                self.events.put(('hf_added','',import_repo(value)))
            except Exception as e:self.events.put(('hf_error','',str(e)))
        threading.Thread(target=work,daemon=True).start()
    def finish_hf_add(self,result):
        self.busy_installer=False
        entry,added=result
        if added:
            from .environment_audit import write_license_inventory
            write_license_inventory()
            old=self.stack.widget(3);self.stack.removeWidget(old);old.deleteLater();self.setup_cards={};self.make_setup();new=self.stack.widget(self.stack.count()-1);self.stack.removeWidget(new);self.stack.insertWidget(3,new);self.change_page(3);self.filter_models()
        self.install_card(entry['id'])
        if added:self.check_model_sizes()

    def refresh_voice_catalog(self):
        if self.voice_refresh_running or self.move_pending or self.busy_installer:return
        self.refresh_voices();self.voice_refresh_running=True;self.voice_refresh_button.setEnabled(False);self.voice_refresh_status.setText('Refreshing voice lists…')
        def work():
            try:
                from .voice_catalog import refresh_piper
                n=refresh_piper(root(),managed.fetch_json);self.events.put(('voice_catalog','',n))
            except Exception as exc:self.events.put(('voice_catalog_error','',str(exc)))
        threading.Thread(target=work,daemon=True).start()
    def choose_piper_voice(self):
        if 'piper' in self.tasks or self.busy_installer or self.move_pending:return self.alert('Finish Piper setup first.')
        voices=models()['piper']['voices'];d=QDialog(self);d.setWindowTitle('Piper voices');d.resize(560,170);l=QVBoxLayout(d);search=QLineEdit();search.setPlaceholderText('Search voice, language or locale');l.addWidget(search);combo=QComboBox();l.addWidget(combo)
        def fill(text=''):
            combo.clear()
            for v in voices:
                if text.lower() in (v['name']+' '+v.get('language','')+' '+v['id']).lower():combo.addItem(v['name'],v['id'])
        fill();search.textChanged.connect(fill);l.addWidget(label('Refresh voices to load the official index. Only selected and existing voices are checked.','muted'));buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(d.accept);buttons.rejected.connect(d.reject);l.addWidget(buttons)
        if d.exec() and combo.currentData():
            self.piper_selected=combo.currentData();self.model_totals.pop('piper',None);self.model_plans.pop('piper',None);self.install_card('piper')
    def card_voices(self,key):
        from .voice_catalog import piper_targets
        return piper_targets(root(),models()[key],self.piper_selected) if key=='piper' else ['']
    def refresh_card(self,key):
        if key in self.tasks:return
        c=self.setup_cards[key];voices=self.card_voices(key)
        files=all(managed.downloaded(key,voice) for voice in voices);env_ready=managed.marker_current(key)
        aux=key not in ('indextts2','dia') or (services.model_folder(key,'')/'.avs-aux-complete').is_file()
        downloaded=files and env_ready and aux
        c['status'].setText('Downloaded' if downloaded else 'Dependencies need check' if files else 'Not downloaded');c['install'].setText('Check' if files else 'Download');c['attach'].setEnabled(not files);c['bar'].setRange(0,1000);c['bar'].setValue(1000 if downloaded else 0)
        size=sum(p.stat().st_size for voice in voices for p in services.model_folder(key,voice).rglob('*') if p.is_file() and not p.name.startswith('.avs-'))
        total=self.model_totals.get(key)
        if key in self.model_plans:
            size=0
            for voice,items in self.model_plans[key]:
                folder=services.model_folder(key,voice)
                for item in items:
                    dest=folder/item['name']
                    try:size+=dest.stat().st_size if managed.valid_file(dest,item) else managed.downloads.offset_for(dest,item)
                    except OSError:pass
        c['bytes'].setText(f'{size/1e9:.3f} / {total/1e9:.3f} GB' if total else f'{size/1e9:.3f} GB' if size else 'Size pending')
        c['bytes'].setToolTip('Model weights only; dependencies and runtimes may require additional storage.')
        if total and not downloaded:c['bar'].setValue(int(min(1,size/total)*1000))
    def install_libtorch(self):
        if self.tasks or self.running or self.busy_installer or self.move_pending:return self.alert("Finish active setup first.")
        if not self.confirm("Optional LibTorch SDK","Download the official Windows x64 CPU C++ SDK (2.8.0)? This is separate from Python inference and may use additional storage."):return
        from .libtorch_sdk import install
        self.start_task("libtorch",None,install,"Preparing LibTorch")
    def install_runtime(self):
        if 'runtime' in self.tasks or self.running or self.busy_installer:return
        self.start_task('runtime',None,lambda c,p,l:managed.ensure_runtime_serial(l,c),'Preparing Python')
    def install_card(self,key):
        if key in self.tasks:return
        if self.busy_installer:return self.alert('Finish the software update or maintenance first.')
        # Queue excess requests instead of emitting errors or requiring settings changes.
        self.pending_setup.append(key)
        self.tasks[key]={'phase':'queued','cancel':threading.Event()}
        c=self.setup_cards[key];c['install'].setEnabled(False);c['cancel'].setEnabled(True);c['status'].setText('Queued');c['install'].setText('Queued');c['attach'].setEnabled(False);c['bar'].setRange(0,1000);c['bar'].setValue(0)
        self.pump_setup()
    def pump_setup(self):
        if self.move_pending or self.busy_installer:return
        limit=self.store.setting('parallel_downloads',3)
        active_models={self.store.job(j)['model'] for j in self.running if self.store.job(j)}
        priority=set()
        for j in getattr(self,'pending_jobs',[]):
            job=self.store.job(j)
            if job and job['model'] in models() and managed.marker_current(job['model']) and managed.downloaded(job['model'],job['voice']):priority.add(models()[job['model']]['profile'])
        while self.pending_setup and available_slots(self.tasks,self.setup_cards,limit):
            key=next((k for k in self.pending_setup if k not in active_models and (models()[k]['profile'] not in priority or not managed.marker_current(k) or not all(managed.downloaded(k,v) for v in self.card_voices(k)))),None)
            if key is None:break
            self.pending_setup.remove(key)
            if key not in self.tasks:continue
            self.tasks.pop(key)
            device=self.preferred_device()
            self.start_task(key,'',lambda c,p,l,k=key,d=device:self.download_model(k,c,p,l,d),'Checking model files')
    def cancel_task(self,key):
        t=self.tasks.get(key)
        if not t:return
        if t['phase']=='queued':
            self.pending_setup.remove(key);self.tasks.pop(key)
            c=self.setup_cards[key];c['install'].setEnabled(True);c['cancel'].setEnabled(False);self.refresh_card(key);c['status'].setText('Cancelled' if c['status'].text()!='Downloaded' else 'Downloaded');self.attached_assets.pop(key,None);self.pump_setup()
        else:
            t['cancel'].set();c=self.setup_cards.get(key)
            if c:c['status'].setText('Cancelling');c['install'].setText('Cancelling');c['cancel'].setEnabled(False)
            else:self.runtime_status(key).setText('Cancelling…')
    def download_model(self,key,cancel,progress,log,device):
        # Weight transfers and dependency preparation can overlap. Only the
        # environment mutations are serialized, protecting shared profiles.
        from . import warm_worker
        warm_worker.release_model(key)
        voices=self.card_voices(key);errors=[];attached=self.attached_assets.pop(key,None)
        def deps():
            try:
                # Reuse a compatible already-prepared profile unless a repair is requested for weights already present.
                if not managed.marker_current(key) or all(managed.downloaded(key,v) for v in voices):managed.ensure_engine(key,device,log,cancel)
                log('Dependencies ready')
            except Exception as exc:errors.append(exc);log('Dependency preparation failed; model files can still finish: '+str(exc))
        installer=threading.Thread(target=deps,daemon=True);installer.start()
        try:
            plans=[]
            for voice in voices:
                managed.check_cancel(cancel);progress(0,None,'Contacting source / checking saved file list');plans.append((voice,managed.file_manifest(key,voice)))
            self.events.put(('active_plan',key,plans))
            items=[it for _,ls in plans for it in ls];total=sum(x['size'] for x in items) if all(x.get('size') is not None for x in items) else None
            def current_total():return sum(x['size'] for x in items) if all(x.get('size') is not None for x in items) else None
            if attached:
                from .local_assets import import_assets
                count=0
                for voice,plan in plans:
                    count+=import_assets(attached,services.model_folder(key,voice),plan,lambda d,t,s:progress(0,current_total(),s),lambda:managed.check_cancel(cancel))
                log(f'Attached {count} matching model files; downloading missing files from configured sources')
                if not count:progress(0,current_total(),'Checking source ZIP (no matching weights)')
            base=0
            for voice,plan in plans:
                managed.download_files(key,voice,plan,lambda d,t,s,b=base:progress(b+d,current_total(),s),cancel)
                base+=sum(x.stat().st_size for x in [services.model_folder(key,voice)/it['name'] for it in plan])
            progress(base,current_total(),'Installing dependencies')
        except BaseException:
            cancel.set();installer.join();raise
        installer.join();managed.check_cancel(cancel)
        if errors:raise errors[0]
        log('Checking model-specific auxiliary files');managed.prepare_auxiliary(key,voices[0],log,cancel);return 'Downloaded'
    def start_task(self,key,voice,fn,message,phase='work',device=None):
        if key in self.tasks:return
        cancel=threading.Event();self.tasks[key]=dict(cancel=cancel,voice=voice,phase=phase,device=device)
        c=self.setup_cards.get(key)
        if c:self.tasks[key]['repair']=all(managed.downloaded(key,v) for v in self.card_voices(key));c['status'].setText('Checking');c['install'].setEnabled(False);c['install'].setText('Checking');c['cancel'].setEnabled(True);c['bar'].setRange(0,0);c['attach'].setEnabled(False);c['detail'].hide();c['transfer'].setText('Checking…');c['transfer'].show()
        else:self.runtime_status(key).setText(message)
        
        def work():
            last=0;previous_stage=None
            def progress(done,total,stage):
                nonlocal last,previous_stage
                now=time.monotonic()
                if now-last>.25 or stage!=previous_stage:
                    self.events.put(('progress',key,(done,total,stage,now)));last=now;previous_stage=stage
            def log(line):
                if line.strip():self.events.put(('detail',key,line[-3000:]))
            try:res=fn(cancel,progress,log);managed.check_cancel(cancel);self.events.put(('done',key,res))
            except managed.Cancelled:self.events.put(('cancelled',key,'Cancelled'))
            except Exception as e:self.events.put(('error',key,str(e)+'\n'+traceback.format_exc()))
        threading.Thread(target=work,daemon=True).start()
    def drain_events(self):
        for _ in range(250):
            try:kind,key,value=self.events.get_nowait()
            except queue.Empty:break
            if kind in ('voice_catalog','voice_catalog_error'):
                self.voice_refresh_running=False;self.voice_refresh_button.setEnabled(True);self.refresh_voices()
                if kind=='voice_catalog':self.voice_refresh_status.setText(f'{value} Piper voices indexed · local voices refreshed')
                else:self.voice_refresh_status.setText('Local voices refreshed · online index unavailable');self.log_message('ERROR: Piper voice index\n'+value)
                continue
            if kind=='active_plan':
                self.model_plans[key]=value
                continue
            if kind=='model_size':
                total,plans=value
                if total and key not in self.model_totals:self.model_totals[key]=total;self.model_plans[key]=plans
                if key in self.setup_cards and key not in self.tasks:self.refresh_card(key)
                continue
            if kind=='hf_added':
                self.finish_hf_add(value);continue
            if kind=='hf_error':
                self.busy_installer=False;self.alert(value);continue
            if kind=='local_update':
                from .source_update import SourcePackage
                if isinstance(value,SourcePackage):self.confirm_source_update(value)
                else:self.confirm_local_update(value)
                continue
            if kind=='source_built':
                self.update_status.setText('Build completed. Confirm installation.')
                self.confirm_local_update(value);continue
            if kind=='local_update_error':
                self.busy_installer=False;self.update_status.setText('Update failed. See Activity Logs.');self.alert(value);continue
            if kind=='audit_issue':
                self.log_message('ERROR: Startup compatibility: '+value);continue
            if kind=='audit_finished':
                self.audit_running=False;continue
            if kind=='audit_done':
                self.audit_running=False
                import time as _t;self.store.set_setting('audit_ts',_t.time())
                n=value['repair_required'];self.bootstrap_status.setText(f'{n} environment(s) need repair' if n else 'Existing environments checked')
                self.scan_selected();continue
            if kind=='resources':
                self.resource_busy=False;self.resource_values=value
                for k,(t,b) in self.meters.items():
                    val=value.get(k);t.setText(k+(' —' if val is None else f' {val:.0f}%'));anim=self.meter_animations[k];anim.stop();anim.setStartValue(b.value());anim.setEndValue(round(val or 0));anim.start()
                    if k=='GPU' and 'VRAM' in value:t.setToolTip(f'VRAM {value["VRAM"][0]/1024:.1f}/{value["VRAM"][1]/1024:.1f} GiB')
                app=value.get('APP')
                if app and 'CPU' in self.meters:
                    self.meters['CPU'][0].setToolTip(f'Whole PC {value.get("CPU") or 0:.0f}% · this software {app[0]:.0f}%')
                    self.meters['RAM'][0].setToolTip(f'Whole PC {value.get("RAM") or 0:.0f}% · this software {app[1]:.0f} MB')
                self.event_timer.setInterval(100 if (self.tasks or self.running or getattr(self,'pending_jobs',[])) else 300)   # quieter when idle
                if not self.busy_installer:self.pump_jobs();self.pump_setup()
                continue
            if kind=='job_end':
                self.job_stages.pop(key,None);self.job_waits.pop(key,None)
                job=self.store.job(key);complete=bool(job and job['status']=='completed')
                self.handle_job_end(key,models().get(job['model'],{}).get('name',job['model'])+'\n'+value if job and value else value);self.pump_setup()
                if key==self.studio_job:
                    if complete:self.studio_player.load(job['output'])
                    self.refresh_studio_job()
                continue
            if kind=='job_detail':
                low=value.lower()
                if 'reusing loaded voice engine' in low:stage='Reusing voice model'
                elif 'loading voice engine' in low:stage='Loading voice model'
                elif 'retrying once in a fresh process' in low:stage='Restarting voice worker'
                elif 'retrying this section' in low:stage='GPU failed · retrying on CPU'
                elif 'waiting for' in low and ('installer' in low or 'runtime' in low):stage='Waiting for runtime repair'
                elif 'repairing voice runtime' in low:stage='Repairing voice runtime'
                elif 'checking existing voice runtime' in low:stage='Checking voice runtime'
                elif 'preparing ' in low and ' adapter' in low:stage='Generating audio'
                elif 'section ' in low and ' done' in low:stage='Generating audio'
                else:stage=None
                if stage:self.job_stages[key]=stage
                if key==self.studio_job:self.refresh_studio_job()
                continue
            if key not in self.tasks:continue
            c=self.setup_cards.get(key);title=models()[key]['name'] if key in models() else 'LibTorch SDK' if key=='libtorch' else 'Python runtime'
            if kind=='progress':
                done,total,stage,observed=value;size=f'{done/1e9:.3f} / {total/1e9:.3f} GB' if total else f'{done/1e9:.3f} GB · size pending'
                if key=='libtorch':self.runtime_status(key).setText(f'LibTorch · {size} · {stage.split("·")[0][:40]}')
                if c:
                    if total:self.model_totals[key]=total
                    task=self.tasks[key];now=observed;last=task.get('rate_sample');rate=0
                    if last and stage==last[2] and done>=last[0] and now>last[1]:rate=(done-last[0])/(now-last[1])
                    task['rate_sample']=(done,now,stage)
                    c['bar'].setRange(0,1000 if total else 0);c['bar'].setValue(int(min(1,done/total)*1000) if total else 0);c['bytes'].setText(size if total else f'{done/1e9:.3f} GB · size pending')
                    task['weight_stage']=stage
                    state=setup_state(stage,task.get('dependency_stage',''),task['cancel'].is_set(),task.get('repair',False))
                    c['status'].setText(state);c['install'].setText(state)
                    detail='Downloading weights' if state=='Downloading' and stage not in ('Installing dependencies','Weights downloaded') else state
                    if rate>0 and state=='Downloading':detail+=f' · {rate/1e6:.2f} MB/s'
                    if task.get('dependency_stage'):detail+=' · '+task['dependency_stage']
                    c['transfer'].setText(detail);c['transfer'].setToolTip('Model weights only; dependencies are separate.');c['transfer'].show()
            elif kind=='detail':
                if c:
                    stage=dependency_state(value)
                    import re
                    m=re.search(r': (\d+(?:\.\d+)?) MB(?: / (\d+(?:\.\d+)?) MB)?',value)
                    if m and stage=='Downloading dependencies':
                        stage+=' · '+m[1]+(' / '+m[2] if m[2] else '')+' MB'
                        speed=re.search(r'([0-9.]+) MB/s',value)
                        if speed:stage+=' · '+speed[1]+' MB/s'
                    self.tasks[key]['dependency_stage']=stage
                    task=self.tasks[key]
                    if task.get('weight_stage') in ('Installing dependencies','Weights downloaded') or not task.get('weight_stage'):
                        state=setup_state('Installing dependencies',stage,task['cancel'].is_set(),task.get('repair',False));c['status'].setText(state);c['install'].setText(state);c['transfer'].setText(stage);c['transfer'].show()
                else:self.runtime_status(key).setText('Preparing LibTorch' if key=='libtorch' else 'Preparing Python')
            else:
                self.tasks.pop(key);status='Downloaded' if kind=='done' else 'Cancelled' if kind=='cancelled' else 'Failed — see logs'
                if kind=='error':self.log_message('ERROR: '+title+'\n'+value)
                if c:
                    c['install'].setEnabled(True);c['cancel'].setEnabled(False);self.refresh_card(key);c['status'].setText('Downloaded' if kind=='done' else status)
                    c['detail'].hide();c['transfer'].hide()
                    if kind=='error':
                        c['status'].setToolTip('See Activity Logs');c['install'].setText('Check');c['transfer'].hide()
                else:self.runtime_status(key).setText(('LibTorch SDK installed' if key=='libtorch' else 'Python installed') if kind=='done' else status)
                if kind=='done' and c:self.refresh_voices()
                self.pump_jobs();self.pump_setup()
    def make_logs(self):
        super().make_logs()
        layout=self.stack.widget(self.stack.count()-1).layout()
        layout.addWidget(button('Copy today’s errors',self.copy_today_errors,True))
    def copy_today_errors(self):
        from datetime import date
        from .log_filter import today_errors
        text=self.error_file.read_text(encoding='utf-8',errors='replace') if self.error_file.exists() else ''
        QApplication.clipboard().setText(today_errors(text,date.today().isoformat()))
    def make_studio(self):
        super().make_studio();w=self.stack.widget(0).widget();v=w.layout()
        # Hidden inherited controls are retained only for the old queue code.
        for tag in ('device','target_chars','pause_ms','speed'):
            for x in self.opt_widgets[tag]:x.hide()
        self.ref_group.hide();self.voice_combo.currentIndexChanged.connect(self.populate_profile_options);self.voice_combo.currentIndexChanged.connect(self.remember_voice)
        form=self.model_combo.parentWidget().layout().itemAt(0).layout();self.voice_type=QComboBox();self.voice_type.addItem('Built-in voices','tts');self.voice_type.addItem('Cloned voices','clone');form.insertRow(0,'Voice type',self.voice_type);self.voice_type.currentIndexChanged.connect(self.filter_models)
        self.voice_search=QLineEdit();self.voice_search.setPlaceholderText('Search name or language');form.insertRow(3,'Find voice',self.voice_search);self.voice_search.textChanged.connect(self.refresh_voices)
        self.language_combo=QComboBox()
        from .voice_catalog import QWEN_LANG
        for language in QWEN_LANG:self.language_combo.addItem(language,language)
        form.addRow('Language',self.language_combo);self.language_label=form.labelForField(self.language_combo);self.language_combo.setCurrentIndex(max(0,self.language_combo.findData(self.store.setting('qwen_language','Auto'))));self.language_combo.currentIndexChanged.connect(lambda _:self.store.set_setting('qwen_language',self.language_combo.currentData()))
        self.output_speed=QDoubleSpinBox();self.output_speed.setRange(.5,2);self.output_speed.setSingleStep(.05);self.output_speed.setValue(1);self.pitch=QDoubleSpinBox();self.pitch.setRange(-12,12);self.pitch.setSingleStep(.5);self.pitch.setSuffix(' st')
        self.temperature=QDoubleSpinBox();self.temperature.setRange(.1,2);self.temperature.setValue(.8);self.temperature.setSingleStep(.1)
        self.guidance=QDoubleSpinBox();self.guidance.setRange(1,8);self.guidance.setValue(3);self.guidance.setSingleStep(.25)
        self.emotion_text=QLineEdit();self.emotion_text.setPlaceholderText('Optional emotion description');self.emotion_weight=QDoubleSpinBox();self.emotion_weight.setRange(0,1);self.emotion_weight.setValue(1);self.emotion_weight.setSingleStep(.1)
        old_panel=self.speed.parentWidget();old_panel.hide();opts,ol=panel();ol.setSpacing(5);ol.setContentsMargins(12,8,12,8);self.extra_fields={}
        h=QHBoxLayout()
        for tag,word,widget in [('output_speed','Speed',self.output_speed),('pitch_semitones','Pitch',self.pitch),('temperature','Temperature',self.temperature),('guidance','Guidance',self.guidance),('emotion_weight','Emotion weight',self.emotion_weight),('emotion_text','Emotion',self.emotion_text)]:
            l=label(word,'muted');cell=QVBoxLayout();cell.addWidget(l);cell.addWidget(widget);h.addLayout(cell);self.extra_fields[tag]=(l,widget)
        for tag in ('exaggeration','cfg_weight'):
            l,wid=self.opt_widgets[tag];cell=QVBoxLayout();cell.addWidget(l);cell.addWidget(wid);h.addLayout(cell)
        ol.addLayout(h);v.insertWidget(v.count()-2,opts)
        for btn in w.findChildren(QPushButton):
            if btn.text()=='Add script to processing queue':btn.setText('Generate voice over');self.generate_button=btn;break
        self.studio_player=AudioPlayer(self);v.insertWidget(v.count()-1,self.studio_player)
        self.studio_status=label('Ready to generate','muted');self.studio_progress=QProgressBar();self.studio_progress.setRange(0,100);self.studio_progress.setValue(0);self.studio_progress.setFixedHeight(9);self.studio_progress.setTextVisible(False)
        self.studio_pause=button('Pause',self.pause_studio_job);self.studio_pause.setEnabled(False)
        v.insertLayout(v.count()-1,layout_row(self.studio_status,self.studio_progress,self.studio_pause));self.filter_models()
        # Remove the introductory heading/tagline and keep the player visible.
        for _ in range(2):
            item=v.takeAt(0)
            if item and item.widget():item.widget().deleteLater()
        v.setContentsMargins(16,12,16,12);v.setSpacing(8)
        self.script.setMinimumHeight(115)
        self.segment_mode.setItemText(self.segment_mode.findData('whole'),'One final audio · automatic safety chunks')
        self.segment_mode.setToolTip('Every mode produces one combined WAV. Long requests are split internally for reliability.')
        self.segment_mode.setCurrentIndex(max(0,self.segment_mode.findData(self.store.setting('segment_mode','paragraph'))));self.segment_mode.currentIndexChanged.connect(lambda _:self.store.set_setting('segment_mode',self.segment_mode.currentData()))
        for btn in w.findChildren(QPushButton):
            if btn.text()=='Import several scripts':btn.setText('Import multiple scripts to batch')
        self.studio_player.name.setWordWrap(False)
    def pause_studio_job(self):
        jid=self.studio_job
        if jid in self.stop_flags:self.stop_flags[jid].set();self.studio_status.setText('Pausing after current section');self.studio_pause.setEnabled(False)
        elif jid in getattr(self,'pending_jobs',[]):self.pending_jobs.remove(jid);self.store.job_state(jid,'paused');self.refresh_queue();self.refresh_studio_job()
        elif jid and self.store.job(jid)['status'] in ('paused','failed'):
            if self.busy_installer:return self.alert('Finish maintenance before resuming.')
            self.pending_jobs=getattr(self,'pending_jobs',[])+[jid];self.pump_jobs();self.refresh_studio_job()
    def refresh_studio_job(self):
        if not self.studio_job or not hasattr(self,'studio_status'):return
        job=self.store.job(self.studio_job)
        if not job:return
        parts=self.store.sections(self.studio_job);done=sum(x['status']=='complete' for x in parts);total=len(parts);status=job['status'];active=self.studio_job in self.running or self.studio_job in getattr(self,'pending_jobs',[])
        names={'pending':'Queued','running':'Generating','completed':'Completed — listen or save below','paused':'Paused — resume here','failed':'Failed — see Activity Logs or retry'}
        from .resource_policy import admission,limits
        wait=self.job_waits.get(self.studio_job,'') if self.studio_job in getattr(self,'pending_jobs',[]) else self.job_stages.get(self.studio_job,'') if status=='running' else ''
        self.studio_status.setText(wait or f'{names.get(status,status)} · {done}/{total} sections');self.studio_progress.setValue(round(100*done/max(1,total)));self.studio_pause.setText('Pause' if active or status not in ('paused','failed') else 'Resume / retry');self.studio_pause.setEnabled((active and not (self.studio_job in self.stop_flags and self.stop_flags[self.studio_job].is_set())) or (not active and status in ('paused','failed')))
        if hasattr(self,'generate_button'):self.generate_button.setEnabled(not (self.busy_installer or self.move_pending));self.generate_button.setText('Add to queue' if active else 'Generate voice over')
    def refresh_queue_if_running(self):
        if self.running or getattr(self,'pending_jobs',[]):self.refresh_queue()
        self.refresh_studio_job()
    def filter_models(self,*_):
        prev=self.model_combo.currentData();self.model_combo.blockSignals(True);self.model_combo.clear()
        for m in models().values():
            if m['kind']==self.voice_type.currentData():self.model_combo.addItem(m['name'],m['id'])
        self.model_combo.setCurrentIndex(max(0,self.model_combo.findData(prev)));self.model_combo.blockSignals(False);self.refresh_voices()
    def refresh_voices(self,*_):
        if not hasattr(self,'voice_combo') or not self.model_combo.currentData():return
        m=models()[self.model_combo.currentData()];old=self.store.setting('voice_'+m['id'],self.voice_combo.currentData());self.voice_combo.blockSignals(True);self.voice_combo.clear()
        q=self.voice_search.text().strip().lower() if hasattr(self,'voice_search') else ''
        if m['kind']=='clone':
            for p in clones.profiles(self.store):
                if p['model']==m['id'] and q in p['name'].lower():self.voice_combo.addItem(p['name'],p['id'])
            if not self.voice_combo.count():self.voice_combo.addItem('Create a voice in Voice Clone','')
        else:
            for x in m['voices']:
                if q not in (x['name']+' '+x.get('language','')+' '+x['id']).lower():continue
                if m['id']=='piper' and not all((root()/'models'/'piper'/x['id']/(x['id']+suffix)).is_file() for suffix in ('.onnx','.onnx.json')):continue
                self.voice_combo.addItem(x['name'],x['id'])
            if not self.voice_combo.count():self.voice_combo.addItem('No matching installed voice','')
        if hasattr(self,'language_combo'):
            show=m['id'].startswith('qwen');self.language_combo.setVisible(show);self.language_label.setVisible(show)
        self.voice_combo.setCurrentIndex(max(0,self.voice_combo.findData(old)));self.voice_combo.blockSignals(False)
        for tag,(l,c) in getattr(self,'opt_widgets',{}).items():
            visible=tag in ('exaggeration','cfg_weight') and tag in m['supports'];l.setVisible(visible);c.setVisible(visible)
        if hasattr(self,'ref_group'):self.ref_group.hide()
        for tag,(l,c) in getattr(self,'extra_fields',{}).items():
            visible=tag in ('output_speed','pitch_semitones') or tag in m['supports'];l.setVisible(visible);c.setVisible(visible)
        self.populate_profile_options()
    def remember_voice(self,*_):
        mid=self.model_combo.currentData();voice=self.voice_combo.currentData()
        if mid and voice:self.store.set_setting('voice_'+mid,voice)
    def profile(self,ident):return next((p for p in clones.profiles(self.store) if p['id']==ident),None)
    def populate_profile_options(self,*_):
        if not hasattr(self,'output_speed'):return
        ident=self.voice_combo.currentData();p=self.profile(ident) if models()[self.model_combo.currentData()]['kind']=='clone' else None
        self.ref_file.setText(p['reference'] if p else '');self.ref_text.setText(p['transcript'] if p else '');self.cloning_consent.setChecked(bool(p))
        defaults={'output_speed':1.,'pitch_semitones':0.,'temperature':.8,'guidance':3.,'emotion_weight':1.,'emotion_text':'','exaggeration':.5,'cfg_weight':.5}
        for key,default in defaults.items():
            ctrl=self.extra_fields[key][1] if key in self.extra_fields else self.opt_widgets[key][1];val=(p or {}).get('options',{}).get(key,default)
            if isinstance(ctrl,QLineEdit):ctrl.setText(str(val))
            else:ctrl.setValue(float(val))
    def options(self):
        m=models()[self.model_combo.currentData()];out={'device':effective_device(m,self.preferred_device()),'segment_mode':self.segment_mode.currentData() if hasattr(self,'segment_mode') else 'paragraph','output_speed':self.output_speed.value() if hasattr(self,'output_speed') else 1.,'pitch_semitones':self.pitch.value() if hasattr(self,'pitch') else 0.}
        out['auto_chunk']=True
        if m['id'].startswith('qwen') and hasattr(self,'language_combo'):out['language']=self.language_combo.currentData()
        for key in m['supports']:
            if key=='speed':continue # independent output speed, not twice applied
            ctrl=(getattr(self,'extra_fields',{}).get(key) or getattr(self,'opt_widgets',{}).get(key))
            if ctrl:out[key]=ctrl[1].text() if isinstance(ctrl[1],QLineEdit) else ctrl[1].value()
        return out
    def resolve_voice(self,model,voice):
        if models()[model]['kind']=='clone':
            p=self.profile(voice)
            if not p or p['model']!=model:raise ValueError('Create and select a saved cloned voice first.')
            return '',p['reference'],p['transcript'],p
        if not voice:raise ValueError('Select an available voice first. Download additional Piper voices in AI Setup.')
        return voice,'','',None
    def add_script(self):
        model=self.model_combo.currentData();voice=self.voice_combo.currentData()
        if self.busy_installer:return self.alert('Finish the software update before generating.')
        if self.move_pending:return self.alert("Finish Move folder first.")
        try:
            v,ref,text,p=self.resolve_voice(model,voice)
            if model in self.tasks and self.tasks[model]['phase']!='queued':raise ValueError('This model is being downloaded or repaired. Select another installed model.')
            if not managed.downloaded(model,v):raise ValueError('Download this model in AI Setup first.')
            title=automatic_title(self.script.toPlainText())
            opts=self.options();opts['clone_name']=(p or {}).get('name','');self.studio_job=self.store.new_job(title or 'Voice Over',self.script.toPlainText(),model,v,ref,text,opts)
            self.studio_player.player.stop();self.studio_player.player.setSource(QUrl());self.studio_player.path=None;self.studio_player.name.setText('Audio job added · all results in Batch Queue');self.refresh_queue();self.pending_jobs=getattr(self,'pending_jobs',[])+[self.studio_job];self.pump_jobs();self.refresh_studio_job()
        except Exception as e:self.alert(str(e))
    def import_batch(self):
        if self.busy_installer:return self.alert('Finish the software update first.')
        paths,_=QFileDialog.getOpenFileNames(self,'Import scripts',str(Path.home()),DOC_FILTER)
        if not paths:return
        d=QDialog(self);d.setWindowTitle('Batch voice assignment');d.resize(920,490);l=QVBoxLayout(d);table=QTableWidget(len(paths),3);table.setHorizontalHeaderLabels(['Script','Model','Voice / saved clone']);table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);table.verticalHeader().setDefaultSectionSize(40);l.addWidget(table);rows=[]
        def fill(mc,vc):
            vc.clear();m=models()[mc.currentData()]
            for x in (clones.profiles(self.store) if m['kind']=='clone' else m['voices']):
                if m['id']=='piper' and not all((root()/'models'/'piper'/x['id']/(x['id']+suffix)).is_file() for suffix in ('.onnx','.onnx.json')):continue
                if m['kind']=='clone' and x['model']!=m['id']:continue
                vc.addItem(x['name'],x['id'])
            if not vc.count():vc.addItem('No saved voice','')
        for i,path in enumerate(paths):
            mc=QComboBox();vc=QComboBox();table.setItem(i,0,QTableWidgetItem(Path(path).name))
            for m in models().values():
                if m['kind']!='check':mc.addItem(m['name'],m['id'])
            mc.setCurrentIndex(mc.findData(self.model_combo.currentData()));fill(mc,vc);vc.setCurrentIndex(max(0,vc.findData(self.voice_combo.currentData())));mc.currentIndexChanged.connect(lambda _,a=mc,b=vc:fill(a,b));table.setCellWidget(i,1,mc);table.setCellWidget(i,2,vc);rows.append((mc,vc))
        def same():
            model,voice=rows[0][0].currentData(),rows[0][1].currentData()
            for a,b in rows:a.setCurrentIndex(a.findData(model));b.setCurrentIndex(max(0,b.findData(voice)))
        l.addWidget(button('Apply first voice to all',same));auto=QCheckBox('Start imported jobs automatically');auto.setChecked(bool(self.store.setting('auto_batch',True)));auto.toggled.connect(lambda n:self.store.set_setting('auto_batch',n));l.addWidget(auto);bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);bb.accepted.connect(d.accept);bb.rejected.connect(d.reject);l.addWidget(bb)
        if not d.exec():return
        try:
            planned=[]
            for path,(a,b) in zip(paths,rows):
                model=a.currentData();v,ref,tr,p=self.resolve_voice(model,b.currentData());opts={'device':self.preferred_device(),'segment_mode':self.segment_mode.currentData(),**((p or {}).get('options',{})),'clone_name':(p or {}).get('name',''),'auto_chunk':True};
                if model.startswith('qwen'):opts['language']=self.store.setting('qwen_language','Auto')
                planned.append((Path(path).stem,read_script(path),model,v,ref,tr,opts))
            if any(not args[1].strip() for args in planned):raise ValueError('One or more documents contain no text.')
            ids=[self.store.new_job(*args) for args in planned]
            self.change_page(1)
            if auto.isChecked() and not self.busy_installer:
                self.pending_jobs=getattr(self,'pending_jobs',[])+ids;self.pump_jobs()
        except Exception as e:self.alert(str(e))
    def make_library(self):
        w,v=page('Voice Clone','Save and name a reference voice you have permission to reproduce.');self.clone_table=QTableWidget(0,2);self.clone_table.setHorizontalHeaderLabels(['Saved voice','Model']);self.clone_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);v.addWidget(self.clone_table)
        v.addLayout(layout_row(button('Create voice clone',self.create_clone,True),button('Rename / edit',lambda:self.create_clone(self.selected_clone())),button('Remove profile',self.delete_clone),button('Use in studio',self.use_clone)))
        self.stack.addWidget(w)
    def refresh_library(self):
        if not hasattr(self,'clone_table'):return
        ps=clones.profiles(self.store);self.clone_table.setRowCount(len(ps))
        for i,p in enumerate(ps):
            a=QTableWidgetItem(p['name']);a.setData(Qt.ItemDataRole.UserRole,p['id']);self.clone_table.setItem(i,0,a);self.clone_table.setItem(i,1,QTableWidgetItem(models()[p['model']]['name']))
    def selected_clone(self):
        i=self.clone_table.currentRow();item=self.clone_table.item(i,0) if i>=0 else None;return self.profile(item.data(Qt.ItemDataRole.UserRole)) if item else None
    def create_clone(self,existing=None):
        if not isinstance(existing,dict):existing={}
        d=QDialog(self);d.setObjectName('cloneDialog');d.setWindowTitle('Save cloned voice');d.setMinimumWidth(640);d.resize(720,555);l=QVBoxLayout(d);form=QFormLayout();l.addLayout(form)
        name=QLineEdit(existing.get('name',''));mc=QComboBox();ref=QLineEdit(existing.get('reference',''));tr=QLineEdit(existing.get('transcript',''));consent=QCheckBox('I have permission to reproduce this voice.');consent.setChecked(bool(existing.get('consent')))
        for m in models().values():
            if m['kind']=='clone':mc.addItem(m['name'],m['id'])
        mc.setCurrentIndex(max(0,mc.findData(existing.get('model'))));form.addRow('Name',name);form.addRow('Model',mc)
        edited_files=[];metadata=dict(existing.get('reference_analysis',{}))
        def edit_path(p):
            from .reference_dialog import ReferenceDialog
            if not Path(p).is_file():return self.alert('Choose a reference audio file first.')
            editor=ReferenceDialog(d,p)
            if editor.exec() and editor.result_file:
                edited_files.append(editor.result_file);metadata.clear();metadata.update(editor.metadata)
                ref.setText(str(editor.result_file));consent.setChecked(False);tr.clear()
                analysis.setText(f"{metadata.get('seconds',0):.2f}s · RMS {metadata.get('rms_dbfs','—')} dBFS · enter matching transcript")
        def choose():
            p,_=QFileDialog.getOpenFileName(d,'Reference audio',str(Path.home()),'Audio (*.wav *.mp3 *.mp2 *.mpeg *.m4a *.flac *.ogg *.aac *.mp4);;All files (*)')
            if p:edit_path(p)
        rw=QWidget();rw.setLayout(layout_row(ref,button('Browse',choose),button('Crop / edit',lambda:edit_path(ref.text()))));form.addRow('Audio sample',rw);form.addRow('Exact transcript',tr);l.addWidget(consent)
        analysis=label('Select a clean reference clip','muted');l.addWidget(analysis)
        notes=QLineEdit(existing.get('style_notes',''));notes.setPlaceholderText('e.g. calm, older narrator, measured pauses');form.addRow('Narration notes',notes)
        emotion=QLineEdit(existing.get('options',{}).get('emotion_text',''));emotion.setPlaceholderText('Optional emotion instruction for IndexTTS 2');form.addRow('Emotion instruction',emotion)
        speed=QDoubleSpinBox();speed.setRange(.5,2);speed.setSingleStep(.05);speed.setValue(existing.get('options',{}).get('output_speed',1));pitch=QDoubleSpinBox();pitch.setRange(-12,12);pitch.setSingleStep(.5);pitch.setValue(existing.get('options',{}).get('pitch_semitones',0));form.addRow('Default speed',speed);form.addRow('Default pitch (semitones)',pitch)
        exaggeration=QDoubleSpinBox();exaggeration.setRange(.25,2);exaggeration.setSingleStep(.05);exaggeration.setValue(existing.get('options',{}).get('exaggeration',.5));form.addRow('Chatterbox expression',exaggeration)
        bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);bb.accepted.connect(d.accept);bb.rejected.connect(d.reject);l.addWidget(bb)
        try:
            if d.exec():
                clones.save_profile(self.store,name.text(),mc.currentData(),ref.text(),tr.text(),consent.isChecked(),{**existing.get('options',{}),'output_speed':speed.value(),'pitch_semitones':pitch.value(),'emotion_text':emotion.text(),'exaggeration':exaggeration.value()},existing.get('id'),style_notes=notes.text());self.refresh_library();self.refresh_voices()
        except Exception as e:self.alert(str(e))
        finally:
            for p in edited_files:p.unlink(missing_ok=True)
    def delete_clone(self):
        p=self.selected_clone()
        if p and self.confirm('Remove voice','Remove the saved profile? Existing queued jobs retain their reference.'):clones.remove_profile(self.store,p['id']);self.refresh_library();self.refresh_voices()
    def use_clone(self):
        p=self.selected_clone()
        if p:self.voice_type.setCurrentIndex(1);self.model_combo.setCurrentIndex(self.model_combo.findData(p['model']));self.refresh_voices();self.voice_combo.setCurrentIndex(self.voice_combo.findData(p['id']));self.change_page(0)
    def make_queue(self):
        w,v=page('Batch Queue','');intro=v.takeAt(1);intro.widget().deleteLater();v.setContentsMargins(16,12,16,12);v.setSpacing(8)
        v.addWidget(button('Import multiple TXT / DOCX scripts',self.import_batch,True))
        self.queue_table=QTableWidget(0,5);self.queue_table.setHorizontalHeaderLabels(['Project','Model / Voice','Progress','Status','Created']);self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.queue_table.setAlternatingRowColors(True)
        h=self.queue_table.horizontalHeader();h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive);h.setStretchLastSection(True);h.setMinimumSectionSize(65)
        for i,width in enumerate((180,260,85,210,130)):self.queue_table.setColumnWidth(i,width)
        self.queue_table.verticalHeader().setDefaultSectionSize(34);self.queue_table.verticalHeader().hide();self.queue_table.setMinimumHeight(180);v.addWidget(self.queue_table,1)
        # Internal sections remain in the database for resumability; no section table in the UI.
        self.section_table=QTableWidget(0,3,w);self.section_table.hide()
        v.addLayout(layout_row(button('Generate selected',self.run_selected,True),button('Process pending',self.run_all),button('Pause',self.pause_jobs),button('Refresh',self.refresh_queue)))
        v.addLayout(layout_row(button('Open audio',self.open_output),button('Export MP3',self.export_mp3),button('Output folder',self.open_output_folder),button('Clear all jobs',self.clear_queue)))
        self.queue_player=AudioPlayer(self);v.addWidget(self.queue_player);self.queue_table.itemSelectionChanged.connect(self.load_selected_audio);self.stack.addWidget(w)
    def clear_queue(self):
        if self.running or self.busy_installer:return self.alert('Pause active generation before clearing the queue.')
        if not self.confirm('Clear Batch Queue','Remove all jobs from the queue? Model files, saved voices and all audio files will be kept.'):return
        self.pending_jobs=[];self.store.clear_jobs();self.studio_job=None
        self.refresh_queue();self.studio_status.setText('Ready to generate');self.studio_progress.setValue(0);self.studio_pause.setEnabled(False);self.generate_button.setEnabled(True)
    def run_all(self):
        if self.busy_installer:return self.alert('Finish maintenance first.')
        self.pending_jobs=[j['id'] for j in reversed(self.store.jobs()) if j['status'] in ('pending','failed','paused') and j['id'] not in self.running]
        self.pump_jobs()
    def job_capacity(self):
        if self.store.setting('generation_mode','sequential')!='parallel':return 1
        return super().job_capacity()
    def pump_jobs(self):
        if self.move_pending or self.busy_installer:return
        from .resource_policy import admission,limits
        pending=getattr(self,'pending_jobs',[])
        for jid in list(self.job_waits):
            if jid not in pending:self.job_waits.pop(jid,None)
        while pending and len(self.running)<self.job_capacity():
            selected=None
            # Sequential is strict FIFO; parallel can use a free eligible slot.
            candidates=list(pending) if self.store.setting('generation_mode','sequential')=='parallel' else pending[:1]
            for jid in candidates:
                job=self.store.job(jid)
                if not job or job['status']=='completed':pending.remove(jid);continue
                mid=job['model']
                if mid not in models():
                    pending.remove(jid);self.store.job_state(jid,'failed','Model not in catalog');self.log_message('ERROR: Model no longer in catalog: '+mid);continue
                task=self.tasks.get(mid)
                if task and task['phase']!='queued':self.job_waits[jid]='Waiting for this model setup';continue
                lock=managed.generation_lock(mid)
                if not lock.acquire(blocking=False):self.job_waits[jid]='Waiting for shared runtime';continue
                lock.release()
                if self.running and (models()[mid].get('torch') or any(models()[self.store.job(x)['model']].get('torch') for x in self.running)):
                    self.job_waits[jid]='Waiting for active AI engine';continue
                device=effective_device(models()[mid],json.loads(job['options']).get('device','cpu'))
                reason=admission(self.resource_values,limits(self.store),device) if self.store.setting('guard_on',True) else ''    # protection off: never wait
                if reason:
                    since=self.__dict__.setdefault('job_wait_since',{}).setdefault(jid,time.monotonic())
                    if time.monotonic()-since<min(15,max(1,int(self.store.setting('guard_wait',45)))):   # a job is never held back for more than 15 seconds by a busy PC
                        self.job_waits[jid]=reason
                        continue
                    self.log_message('Starting anyway: '+reason+' for 15 seconds.')
                self.__dict__.setdefault('job_wait_since',{}).pop(jid,None)
                selected=jid;break
            if selected is None:break
            pending.remove(selected);self.job_waits.pop(selected,None);self.job_stages[selected]='Starting voice engine';self.launch_job(selected)
        for jid in pending:self.job_waits.setdefault(jid,'Waiting for audio job slot')
        self.refresh_studio_job()
    def refresh_sections(self):
        # The section editor has been removed; avoid populating a hidden table.
        pass
    def refresh_queue(self):
        # Refresh selection without restarting audio or resetting its seek position.
        super().refresh_queue()
        for row in range(self.queue_table.rowCount()):
            jid=self.queue_table.item(row,0).data(Qt.UserRole);status=self.queue_table.item(row,3)
            reason=self.job_waits.get(jid) if jid in getattr(self,'pending_jobs',[]) else self.job_stages.get(jid) if jid in self.running else None
            if reason:status.setText(reason);status.setToolTip(reason)
        if hasattr(self,'queue_player'):self.load_selected_audio()
    def load_selected_audio(self):
        jid=self.selected_job();job=self.store.job(jid) if jid else None
        if job and job['status']=='completed' and is_audio(Path(job['output'])):
            if self.queue_player.path!=Path(job['output']):self.queue_player.load(job['output'])
        elif self.queue_player.path:
            self.queue_player.player.stop();self.queue_player.player.setSource(QUrl());self.queue_player.path=None;self.queue_player.name.setText('No completed audio selected')
    def run_selected(self):
        if self.busy_installer:return self.alert('Finish the software update before generating.')
        jid=self.selected_job()
        if not jid or jid in self.running or jid in getattr(self,'pending_jobs',[]) or self.store.job(jid)['status']=='completed':return
        self.pending_jobs=getattr(self,'pending_jobs',[])+[jid];self.pump_jobs()
    def save_audio(self):
        jid=self.selected_job()
        if not jid:return
        job=self.store.job(jid)
        if job["status"]!="completed":return self.alert("Complete the job first.")
        from .reference_dialog import export_dialog
        try:
            if export_dialog(self,job["output"]):self.toast("Audio exported")
        except Exception as e:self.alert(str(e))
    def open_output(self):
        jid=self.selected_job()
        if jid and self.store.job(jid)['status']=='completed':self.queue_player.load(self.store.job(jid)['output'],True)
        else:self.alert('Complete the job first.')
    def play_section(self):
        jid=self.selected_job();idx=self.selected_section()
        if jid is not None and idx is not None:
            s=self.store.sections(jid)[idx]
            if s['status']=='complete':self.queue_player.load(s['file'],True)
    def make_settings(self):
        super().make_settings()
        # Licensing metadata is bundled and written into the application data folder.
        # No lengthy source/terms cards in the ordinary settings screen.
        w=self.stack.widget(4).widget()
        from .resource_policy import limits
        c,cv=panel();cv.addWidget(label('Resource limits','sectiontitle'));cv.addWidget(label('New jobs wait above the selected system usage. CPU threads and PyTorch VRAM allocator are also restricted; these are not hard OS-wide caps.','muted'))
        self.limit_sliders={}
        for name,key in [('CPU','cpu_limit'),('RAM','ram_limit'),('GPU','gpu_limit')]:
            slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(10,100);slider.setValue(limits(self.store)[key]);value=label(str(slider.value())+'%','muted');value.setFixedWidth(55)
            slider.valueChanged.connect(lambda n,k=key,t=value:(t.setText(f'{n}%'),self.store.set_setting(k,n)))
            cv.addLayout(layout_row(label(name+' limit'),slider,value));self.limit_sliders[key]=slider
        def preset(cpu,ram,gpu,jobs):
            for key,val in [('cpu_limit',cpu),('ram_limit',ram),('gpu_limit',gpu)]:self.limit_sliders[key].setValue(val)
            self.parallel.setValue(jobs)
            if hasattr(self,'queue_mode'):self.queue_mode.setCurrentIndex(1 if jobs>1 else 0)
        cv.addLayout(layout_row(button('Low-end',lambda:preset(50,75,75,1)),button('Balanced',lambda:preset(85,85,90,1)),button('High-end',lambda:preset(100,95,100,3))))
        warm=QCheckBox('Keep last AI model loaded (3 minutes idle)');warm.setChecked(bool(self.store.setting('keep_model_warm',True)));warm.toggled.connect(self.set_warm_enabled);cv.addWidget(warm);cv.addWidget(button('Release loaded model',self.release_warm_model))
        w.layout().insertWidget(2,c)
        runtime,rv=panel();rv.addWidget(label('Runtime','sectiontitle'));self.bootstrap_status=label('Python · automatic','muted');self.libtorch_status=label('LibTorch · optional CPU SDK','muted');rv.addLayout(layout_row(self.bootstrap_status,button('Python',self.install_runtime),button('Cancel',lambda:self.cancel_task('runtime'))));rv.addLayout(layout_row(self.libtorch_status,button('LibTorch',self.install_libtorch),button('Cancel',lambda:self.cancel_task('libtorch'))));w.layout().insertWidget(3,runtime)
        storage,sv=panel();sv.addWidget(button('Move folder',self.move_storage,True))
        w.layout().insertWidget(3,storage)
        self.queue_mode=QComboBox();self.queue_mode.addItem('Sequential · one at a time','sequential');self.queue_mode.addItem('Parallel · simultaneous lightweight jobs','parallel');self.queue_mode.setCurrentIndex(1 if self.store.setting('generation_mode','sequential')=='parallel' else 0)
        processing=self.parallel.parentWidget().layout();processing.insertLayout(1,layout_row(label('Voice generation mode'),self.queue_mode))
        self.parallel.setEnabled(self.queue_mode.currentData()=='parallel')
        def mode_changed(*_):
            self.store.set_setting('generation_mode',self.queue_mode.currentData());self.parallel.setEnabled(self.queue_mode.currentData()=='parallel');self.pump_jobs()
        self.queue_mode.currentIndexChanged.connect(mode_changed)
        for control,key in [(self.parallel,'parallel_jobs'),(self.down_limit,'parallel_downloads')]:control.valueChanged.connect(lambda n,k=key:self.store.set_setting(k,n))
        self.compute.currentIndexChanged.connect(lambda _:self.store.set_setting('compute_mode',self.compute.currentData()))
        self.update_status=label('','muted');self.update_status.hide()
        for x in w.findChildren(QLabel):
            if x.text()==str(root()):x.hide()
            if x.text()=='Offline software update':
                card=x.parentWidget();w.layout().removeWidget(card);card.hide();card.deleteLater()
    def runtime_status(self,key):
        return self.libtorch_status if key=='libtorch' else self.bootstrap_status
    def set_warm_enabled(self,enabled):
        self.store.set_setting("keep_model_warm",enabled)
        if not enabled:self.release_warm_model()
    def release_warm_model(self):
        from . import warm_worker
        if not self.running:warm_worker.release_idle();self.toast("Idle voice model released")
        else:self.toast("Pause generation before releasing the model")
    def move_storage(self):
        from .migration import next_destination,active_path
        if self.busy_installer or self.move_pending or self.voice_refresh_running:return self.alert('Finish the current update or voice list refresh first.')
        if os.name!='nt' or not getattr(sys,'frozen',False):return self.alert('Move folder is available in the installed Windows application.')
        if active_path().exists():return self.alert('A previous move is pending. Restart AI Voice Studio to recover it.')
        target=QFileDialog.getExistingDirectory(self,'Choose where to move the folder',str(Path.home()))
        if not target:return
        try:destination=next_destination(Path(target))
        except Exception as e:return self.alert(str(e))
        if not self.confirm('Move folder',f'Move AI Voice Studio and its managed data to:\n{destination}\n\nActive jobs and downloads will pause or cancel safely. Save any unsaved scripts in other AI Voice Studio windows before continuing; the helper may request those windows to close normally. The app will close and reopen after verification. Keep both drives connected.'):return
        self.move_pending=destination
        # No new setup/audio work while draining existing workers. Checkpoints and
        # incomplete model files are kept on disk. Never force-kill workers.
        self.move_wait=QProgressDialog('Preparing to move folder… Waiting for active work to stop safely.','Cancel move',0,0,self)
        self.move_wait.setWindowTitle('Move folder');self.move_wait.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.move_wait.setAutoClose(False);self.move_wait.setAutoReset(False);self.move_wait.setMinimumDuration(0)
        self.move_wait.canceled.connect(self.cancel_move_preparation);self.move_wait.show()
        self.pause_jobs()
        for key in list(self.tasks):self.cancel_task(key)
        QTimer.singleShot(200,self.finish_move_preparation)
    def cancel_move_preparation(self):
        if self.move_pending:
            self.move_pending=None
            self.move_wait.hide()
    def finish_move_preparation(self):
        if not self.move_pending:return
        if self.tasks or self.running or self.audit_running or self.resource_busy or self.busy_installer or getattr(self,'pending_jobs',[]):
            QTimer.singleShot(500,self.finish_move_preparation);return
        from .migration import start,log_path
        destination=self.move_pending
        try:
            for player in (self.studio_player,self.queue_player):player.player.stop();player.player.setSource(QUrl())
            from . import warm_worker
            warm_worker.release()
            start(destination,Path(sys.executable),root())
            self.move_pending=None;self.move_wait.hide();self.timer.stop();self.close();QApplication.instance().quit()
        except Exception as e:
            self.move_pending=None;self.move_wait.hide()
            self.log_message('ERROR: Move folder: '+str(e));self.alert(f'{e}\n\nLog: {log_path()}')
    def offline_update(self):
        if self.move_pending:return self.alert('Finish Move folder first.')
        if self.tasks or self.running or self.busy_installer or self.audit_running or getattr(self,'pending_jobs',[]):return self.alert('Finish downloads, startup audit and active jobs before updating.')
        path,_=QFileDialog.getOpenFileName(self,'Select trusted local update',str(Path.home()),'AI Voice Studio updates (*.exe *.zip)')
        if not path:return
        from .source_update import stage_any
        self.busy_installer=True;self.update_status.setText('Checking update package…')
        def work():
            try:self.events.put(('local_update','',stage_any(path)))
            except Exception as e:self.events.put(('local_update_error','',str(e)))
        threading.Thread(target=work,daemon=True).start()
    def confirm_source_update(self,package):
        from .source_update import build_source
        if os.name!='nt':
            self.busy_installer=False;self.update_status.setText('Source compilation requires Windows.');return
        if not self.confirm('Trust source and build update',f'Version {package.version} source ZIP\nSHA-256: {package.digest}\n\nBuilding executes the source package and its build scripts with your Windows account. Only proceed if you trust its origin. Missing build tools may be downloaded. The existing program will not be replaced until a successful build and final installation confirmation.'):
            self.busy_installer=False;self.update_status.setText('Update cancelled.');return
        self.update_status.setText('Building update in background… Logs are in the update files folder.')
        def work():
            try:self.events.put(('source_built','',build_source(package)))
            except Exception as e:self.events.put(('local_update_error','',str(e)))
        threading.Thread(target=work,daemon=True).start()
    def confirm_local_update(self,result):
        from .update_local import verify_update, upgrade_arguments
        target,declared,actual=result
        try:
            expected,ok=QInputDialog.getText(self,'Verify offline update','Enter the SHA-256 from your trusted build. A checksum inside the ZIP alone does not authenticate the publisher.',text=declared)
            if not ok:return
            verify_update(target,expected)
            if not self.confirm('Install trusted update',f'Install {target.name}? Existing data will be preserved. Only proceed if you trust the installer source.') :return
            if self.tasks or self.running or self.audit_running or getattr(self,'pending_jobs',[]):raise RuntimeError('Pause jobs and downloads before updating.')
            from .source_update import backup_state
            args=upgrade_arguments(target)
            backup_state(self.store.file)
            self.log_message('Installing update to '+args[-1][5:])
            from . import warm_worker
            warm_worker.release()
            external.popen(args,close_fds=True);self.busy_installer=False;self.close();QApplication.instance().quit()
        except Exception as e:self.alert(str(e))
        finally:
            self.busy_installer=False;self.update_status.setText('Ready for local update')
    def closeEvent(self,event):
        if self.audit_running and not os.environ.get('AVS_NO_PROMPTS'):   # tests close windows at once; people are asked to wait
            self.alert('Wait for the current environment audit before closing.');event.ignore();return
        super().closeEvent(event)
        if event.isAccepted():
            from . import warm_worker
            warm_worker.release()
    def save_settings(self):
        super().save_settings();self.pump_setup();QMessageBox.information(self,'Settings','Settings saved.')
