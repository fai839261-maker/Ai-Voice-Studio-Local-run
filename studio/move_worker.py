"""Standalone Windows move helper (built as a small independent EXE)."""
from __future__ import annotations
import csv
from contextlib import closing
import ctypes
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from studio.move_engine import (atomic_json,read_json,relocate,rollback,cleanup,inside,
                                link_matches,safe_remove_tree,prepare_resume)


def clean_env():
    e = dict(os.environ)
    bundle = getattr(sys,'_MEIPASS',None)
    if bundle:
        base = Path(bundle)
        e['PATH'] = os.pathsep.join(p for p in e.get('PATH','').split(os.pathsep) if p and not inside(Path(p.strip('"')).absolute(),base))
    for k in list(e):
        if k.startswith('_PYI_') or k in ('PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH'):
            del e[k]
    e['PYINSTALLER_RESET_ENVIRONMENT']='1'
    return e


def run(cmd, **kwargs):
    return subprocess.run([str(x) for x in cmd],env=clean_env(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),**kwargs)


def privacy(folder):
    """New managed files inherit private, current-user/SYSTEM/admin access."""
    if os.name!='nt':
        return
    sys32=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'
    user=run([sys32/'whoami.exe','/user','/fo','csv','/nh'],capture_output=True,text=True,check=True,timeout=30)
    sid=next((v for row in csv.reader(user.stdout.splitlines()) for v in row if re.fullmatch(r'S-1-\d+(?:-\d+)+',v)),None)
    if not sid:
        raise RuntimeError('Unable to determine the current Windows user for destination permissions.')
    for args in (['/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F'],['/inheritance:r']):
        result=run([sys32/'icacls.exe',folder,*args],capture_output=True,text=True,timeout=30,errors='replace')
        if result.returncode:
            raise RuntimeError(f'Could not secure destination folder: {result.stdout} {result.stderr}')


def wait_parent(plan):
    import psutil
    try:
        proc=psutil.Process(plan['pid'])
        if abs(proc.create_time()-plan['parent_created']) < 1:
            proc.wait(timeout=120)
    except psutil.NoSuchProcess:
        return
    except psutil.TimeoutExpired as exc:
        raise RuntimeError('AI Voice Studio did not close cleanly. No files have been switched.') from exc


def process_blockers(plan):
    """Fresh process snapshot. Never match only by process name or stale PID."""
    import psutil
    roots=[Path(r[k]) for r in plan['rows'] for k in ('physical','logical','dst')]
    psutil.process_iter.cache_clear()
    blockers=[]
    for proc in psutil.process_iter(['pid','exe','create_time','cmdline']):
        try:
            exe=proc.info['exe']
            if not exe or proc.pid==os.getpid():continue
            # A crash between starting Robocopy and assigning it to the job
            # object can leave a child alive. Wait for only a copy whose target
            # is inside this transaction's unique staging tree (never kill it).
            args=proc.info.get('cmdline') or []
            orphan=(Path(exe).name.casefold()=='robocopy.exe' and 'staging' in plan
                    and len(args)>=3 and any(inside(Path(arg),Path(plan['staging']))
                                            for arg in args[2:3]))
            if orphan or any(inside(Path(exe),r) for r in roots):
                blockers.append((proc.pid,proc.info['create_time'],Path(exe)))
        except (psutil.NoSuchProcess,psutil.AccessDenied):
            pass
    return blockers


def request_app_close(pid,created,exe):
    """Send ordinary WM_CLOSE only to this installation's verified main window.

    Never terminate processes, and never send a message to a reused PID.
    The other GUI's normal closeEvent may refuse while work is active.
    """
    if os.name!='nt' or created is None:
        return False
    import psutil
    from ctypes import wintypes
    try:
        p=psutil.Process(pid)
        if abs(p.create_time()-created)>.01 or Path(p.exe())!=exe:
            return False
    except (psutil.NoSuchProcess,psutil.AccessDenied):
        return False
    user=ctypes.windll.user32
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    sent=False
    @callback_type
    def visit(hwnd,_):
        nonlocal sent
        owner=wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==pid and user.IsWindowVisible(hwnd):
            title=ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(hwnd,title,len(title))
            if title.value.startswith('AI Voice Studio') and '·' in title.value:
                try:
                    current=psutil.Process(pid)
                    if abs(current.create_time()-created)<=.01 and Path(current.exe())==exe:
                        sent=bool(user.PostMessageW(hwnd,0x0010,0,0)) or sent
                except (psutil.NoSuchProcess,psutil.AccessDenied):
                    pass
        return True
    user.EnumWindows(visit,0)
    return sent


def ensure_quiet(plan,progress=lambda *_:None,log=lambda _:None,timeout=45,allow_close=False):
    """Wait for all application/runtime processes, including exiting launchers.

    Two seconds of continuous quiet are required; a single snapshot is not
    sufficient. Unknown runtime workers are never force stopped.
    """
    start=time.monotonic();deadline=start+timeout;quiet_since=None;asked=set();last=None
    while True:
        blockers=process_blockers(plan);now=time.monotonic()
        if not blockers:
            if quiet_since is None:quiet_since=now
            if now-quiet_since>=2:return
        else:
            quiet_since=None
            desc=', '.join(f'{exe.name} (PID {pid})' for pid,_,exe in blockers)
            if desc!=last:
                log('Waiting for managed processes: '+desc);last=desc
            progress('Waiting for application files to unlock',0,0,desc)
            if allow_close and now-start>=3:
                for pid,created,exe in blockers:
                    key=(pid,created)
                    if key not in asked and exe.name.casefold()==plan['exe'].casefold():
                        # Recheck exact installation executable, not other copies
                        # merely having the same filename in a managed cache.
                        app=plan['rows'][0]
                        if any(exe==Path(app[k])/plan['exe'] for k in ('logical','physical','dst')):
                            if request_app_close(pid,created,exe):
                                asked.add(key);log(f'Requested normal window close for PID {pid}.')
        if now>=deadline and blockers:
            raise RuntimeError('Application files are still in use after waiting: '+desc+'. Close the listed process normally, save any work, and retry. No process was forcibly terminated.')
        time.sleep(.5)


def smoke(plan):
    app=Path(plan['rows'][0]['logical']);data=Path(plan['rows'][1]['logical'])
    exe=app/plan['exe'];report=data/'packaged-self-check.json';db=data/'state'/'studio.sqlite3'
    if not exe.is_file() or not db.is_file():
        raise RuntimeError('Installed executable or saved studio database missing after path switch.')
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=15)) as conn:
        if conn.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:
            raise RuntimeError('Saved project database integrity check failed.')
    report.unlink(missing_ok=True)
    env=clean_env();env['AI_VOICE_STUDIO_HOME']=str(data)
    r=subprocess.run([str(exe),'--self-check'],cwd=app,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=120)
    if r.returncode or not report.is_file():
        raise RuntimeError('New location did not pass the packaged application self-check.')
    check=read_json(report)
    if check.get('result')!='PASS' or check.get('missing'):
        raise RuntimeError('New location is missing application resources.')


def pointer_matches(plan):
    try:
        return Path(read_json(Path(plan['control'])/'active.json')['run'])==Path(plan['run'])
    except (OSError,ValueError,KeyError):
        return False


def finish_pointer(plan):
    if pointer_matches(plan):
        (Path(plan['control'])/'active.json').unlink()
        if os.name=='nt':
            # Keep the standalone helper free of GUI/core imports.
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run',0,winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE) as key:
                    value,_=winreg.QueryValueEx(key,'AVS-PendingMove')
                    expected=subprocess.list2cmdline([str(Path(plan['run'])/'AVS_Move_Helper.exe'),'--recover'])
                    if value==expected:winreg.DeleteValue(key,'AVS-PendingMove')
            except OSError:pass


def execute(journal,progress,choice="resume"):
    plan=read_json(journal)
    def log(msg):
        with Path(plan['log']).open('a',encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + str(msg)+'\n')
    if not pointer_matches(plan):
        return 'Move folder', 'No active move for this helper. Nothing changed.',False
    try:
        progress('Waiting for AI Voice Studio to close',0,0,'')
        wait_parent(plan)
        ensure_quiet(plan,progress,log,allow_close=True)
        if plan['phase'] in ('complete','rolled_back'):
            finish_pointer(plan)
            return 'Move folder','Move already finished.',True
        if plan['phase']=='committed':
            # An interrupted cleanup must NEVER restore partially deleted backups.
            warnings=cleanup(plan,log)
            plan['phase']='complete';atomic_json(journal,plan)
        elif choice=='rollback':
            log('User requested original location.')
            rollback(plan,log)
            plan['phase']='rolled_back';atomic_json(journal,plan)
            finish_pointer(plan)
            return 'Move folder','Original location restored. AI Voice Studio will reopen.',True
        else:
            if plan['phase'] in ('publishing','switching'):
                prepare_resume(plan,journal)
            if plan['phase'] not in ('planned','copying'):
                raise RuntimeError('Unknown migration phase; original backups retained.')
            warnings=relocate(plan,journal,lambda:(ensure_quiet(plan,progress,log),smoke(plan)),progress,log,privacy,
                              before_switch=lambda:ensure_quiet(plan,progress,log))
        try:
            atomic_json(Path(plan['control'])/'last-move.json',{'app':plan['rows'][0]['logical'],'destination':plan['destination']})
        except OSError as exc:
            log(f'Could not save move history: {exc}')
        finish_pointer(plan)
        msg=f'Moved successfully to:\n{plan["destination"]}\n\nAI Voice Studio will reopen.'
        if warnings:
            msg+='\n\nThe new files are verified, but some old backups could not be deleted. See the migration log before removing anything.'
        return 'Move folder',msg,True
    except Exception as exc:
        log('ERROR: '+str(exc)+'\n'+traceback.format_exc())
        # Read the durable journal rather than trusting an in-memory stage update.
        saved=read_json(journal)
        try:
            if saved['phase'] in ('committed','complete'):
                # New links are already committed. Retain all remaining backups.
                if not all(link_matches(Path(r['logical']),Path(r['dst'])) for r in saved['rows']):
                    raise RuntimeError('Committed links need manual recovery; backups retained.')
                finish_pointer(saved)
                return 'Move folder',f'New location is committed, but final cleanup failed.\n{exc}\n\nLog: {plan["log"]}',True
            # Keep the checkpoint and durable pointer on recoverable failures.
            # Restore logical paths immediately if a switch was interrupted.
            if saved['phase'] in ('publishing','switching'):
                prepare_resume(saved,journal)
            return 'Move folder',f'Move paused. Original files and transfer checkpoints retained.\n\n{exc}\n\nOpen AI Voice Studio again or restart Windows to choose Resume or Original location.\n\nLog: {plan["log"]}',False
        except Exception as recovery:
            log('RECOVERY ERROR: '+str(recovery)+'\n'+traceback.format_exc())
            return 'Move folder',f'Move interrupted; original backups have been retained. Do not delete any folders or junctions.\n\n{recovery}\n\nRun this helper again after resolving the problem:\n{Path(plan["run"])/"AVS_Move_Helper.exe"}\n\nLog: {plan["log"]}',False


def dialog_execute(journal,choice="resume"):
    """Responsive native-style progress window while the main program is closed."""
    events=queue.Queue()
    def report(stage,done,total,detail):
        events.put(('progress',(stage,done,total,detail)))
    result=[]
    def work():
        try:
            result.append(execute(journal,report,choice))
        except Exception as exc:
            result.append(('Move folder',f'Recovery helper stopped: {exc}\nRun the helper again to recover.',False))
        finally:
            events.put(('done',None))
    # Tkinter is collected by the independent PyInstaller build. A failed UI
    # falls back to the same safe worker and a native result dialog.
    try:
        import tkinter as tk
        from tkinter import ttk
        root=tk.Tk();root.title('AI Voice Studio — Move folder');root.geometry('620x180');root.resizable(False,False)
        outer=ttk.Frame(root,padding=20);outer.pack(fill='both',expand=True)
        stagevar=tk.StringVar(value='Preparing move…');detailvar=tk.StringVar(value='Please keep both drives connected.')
        ttk.Label(outer,textvariable=stagevar,font=('Segoe UI',12,'bold')).pack(anchor='w')
        bar=ttk.Progressbar(outer,mode='indeterminate',maximum=1000);bar.pack(fill='x',pady=15);bar.start(20)
        ttk.Label(outer,textvariable=detailvar,wraplength=560).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW',lambda:None)
    except Exception:
        work();return result[0]
    threading.Thread(target=work,daemon=True).start()
    def tick():
        try:
            while True:
                tag,value=events.get_nowait()
                if tag=='done':
                    root.destroy();return
                s,d,t,detail=value;stagevar.set(s)
                if t:
                    bar.stop();bar.configure(mode='determinate',value=int(min(1000,d/t*1000)))
                    detail=f'{d/2/1e9:.2f} / {t/2/1e9:.2f} GB copied/verified equivalent\n{detail}'
                elif str(bar.cget('mode'))!='indeterminate':
                    bar.configure(mode='indeterminate');bar.start(20)
                detailvar.set(detail[:2000])
        except queue.Empty:
            pass
        root.after(100,tick)
    root.after(100,tick);root.mainloop()
    return result[0]


def main():
    if '--self-test' in sys.argv:
        return 0
    if os.name!='nt':
        return 1
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    journal=Path(sys.executable).parent/'plan.json' if getattr(sys,'frozen',False) else Path(sys.argv[1])
    import msvcrt
    with (journal.parent/'worker.lock').open('a+b') as lock:
        lock.seek(0,2)
        if not lock.tell():
            lock.write(b'0');lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            ctypes.windll.user32.MessageBoxW(None,'A move helper is already running. Keep both drives connected.','Move folder',0x40)
            return 1
        p=read_json(journal)
        if not pointer_matches(p):return 0
        choice='resume'
        if p['phase'] not in ('committed','complete','rolled_back') and ('--recover' in sys.argv or p['phase']!='planned'):
            answer=ctypes.windll.user32.MessageBoxW(None,
                'An unfinished Move folder operation was found.\n\nYES: Continue to the new folder using saved transfer checkpoints.\nNO: Restore the original location and discard the staged copy.\nCANCEL: Decide later; all files remain protected.\n\nOriginal: '+p['rows'][0]['physical']+'\nDestination: '+p['destination'],
                'AI Voice Studio — Recover move',0x23)
            if answer==2:return 0
            if answer not in (6,7):return 0
            choice='resume' if answer==6 else 'rollback'
        title,msg,reopen=dialog_execute(journal,choice)
        ctypes.windll.user32.MessageBoxW(None,msg,title,0x40 if reopen else 0x30)
        if reopen:
            p=read_json(journal);app=Path(p['rows'][0]['logical']);exe=app/p['exe']
            # Do not launch another GUI if a failed move left one running.
            if exe.is_file() and not process_blockers(p):
                subprocess.Popen([str(exe)],cwd=app,env=clean_env(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)
        return 0 if reopen else 1


if __name__=='__main__':
    sys.exit(main())
