"""Persistent state, paths, script segmentation, and audio assembly."""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid
import wave
from pathlib import Path

APP_VERSION = "0.22.0"
APP_NAME = "AI Voice Studio"


def bundled(rel: str) -> Path:
    layer = os.getenv("AVS_OVERLAY_DIR")           # files from an installed update take priority
    if layer and (Path(layer) / rel).exists():
        return Path(layer) / rel
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / rel


def root() -> Path:
    home = os.getenv("AI_VOICE_STUDIO_HOME")
    if home:
        base = Path(home)
    elif os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "AVS"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AVS"
    else:
        base = Path.home() / ".local" / "share" / "ai-voice-studio"
    base.mkdir(parents=True, exist_ok=True)
    return base


def subdir(name: str) -> Path:
    path = root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def catalog() -> dict:
    return json.loads(bundled("resources/catalog.json").read_text(encoding="utf-8"))


def models() -> dict:
    from .hf_import import read_entries
    from .voice_catalog import merge
    home=root()
    return {m["id"]: merge(m,home) for m in catalog()["models"] + read_entries()}


def split_sentences(text: str, target_chars: int = 0) -> list[str]:
    """Split at sentence endings. target_chars=0 means do not artificially split.
    The target is a user preference, NOT a claimed engine's maximum input size.
    """
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    pieces = []
    for p in paragraphs:
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        # Preserve punctuation; avoid splitting common initials and abbreviations.
        protected = re.sub(r"\b(?:Mr|Mrs|Ms|Dr|Prof|St|Jr|Sr|vs|etc)\.",
                           lambda match: match.group().replace(".", "\uf000"), p, flags=re.I)
        chunks = re.split(r"(?<=[.!?。！？])\s+(?=[\"'“‘(]*\S)", protected)
        pieces.extend(chunk.replace("\uf000", ".") for chunk in chunks)
    if target_chars <= 0:
        return pieces
    result = []
    for piece in pieces:
        if len(piece) <= target_chars:
            result.append(piece)
            continue
        words = piece.split()
        if len(words) == 1:
            result.append(piece)  # never silently truncate any text
            continue
        line = ""
        for word in words:
            if line and len(line) + 1 + len(word) > target_chars:
                result.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        if line:
            result.append(line)
    return [s for s in result if s.strip()]


def safe_file_name(name: str) -> str:
    return re.sub(r'[^\w .()-]+', '_', name, flags=re.UNICODE).strip(' .')[:85] or "Audio"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for data in iter(lambda: fh.read(2**20), b""):
            h.update(data)
    return h.hexdigest()


def is_audio(path: Path) -> bool:
    try:
        if path.stat().st_size < 100:
            return False
        with wave.open(str(path), "rb") as reader:
            return reader.getnframes() > 100 and reader.getframerate() > 0
    except (OSError, wave.Error):
        return False


PAUSE_TAG = re.compile(r"\[\s*pause\s+([0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|second|seconds)?\s*\]", re.IGNORECASE)


def split_pauses(script: str) -> list[tuple[str, int]]:
    """[(text, pause_after_ms)] for a script with [pause 1.5s] tags. A pause is kept between 0.2 and 10 seconds."""
    pieces, last = [], 0
    for m in PAUSE_TAG.finditer(script):
        text = script[last:m.start()].strip()
        ms = int(max(0.2, min(10.0, float(m.group(1)))) * 1000)
        if text:
            pieces.append([text, ms])
        elif pieces:
            pieces[-1][1] += ms
        last = m.end()
    tail = script[last:].strip()
    if tail:
        pieces.append([tail, 0])
    elif pieces:
        pieces[-1][1] = 0
    return [(t, p) for t, p in pieces] or ([(script.strip(), 0)] if script.strip() else [])


def strip_pause_tags(script: str) -> str:
    return PAUSE_TAG.sub(" ", script)


def join_wave(files: list[Path], output: Path, silence_ms: int = 220, gaps: list[int] | None = None) -> Path:
    """Atomic WAV assembly, no third-party transcoder."""
    if not files or any(not is_audio(f) for f in files):
        raise ValueError("Some audio sections are missing or invalid.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".joining.wav")
    try:
        with wave.open(str(files[0]), "rb") as r:
            params = r.getparams()
        with wave.open(str(temp), "wb") as writer:
            writer.setnchannels(params.nchannels)
            writer.setsampwidth(params.sampwidth)
            writer.setframerate(params.framerate)
            for idx, file in enumerate(files):
                with wave.open(str(file), "rb") as reader:
                    if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (params.nchannels, params.sampwidth, params.framerate):
                        raise ValueError("Audio sections have different WAV formats; conversion is required.")
                    while True:
                        chunk = reader.readframes(65536)
                        if not chunk:
                            break
                        writer.writeframesraw(chunk)
                gap = (gaps[idx] if gaps and len(gaps) == len(files) and gaps[idx] else silence_ms)
                if gap and idx < len(files)-1:
                    writer.writeframesraw(b"\0" * int(params.framerate * gap/1000) * params.nchannels * params.sampwidth)
        os.replace(temp, output)
        return output
    finally:
        if temp.exists():
            temp.unlink()


class Store:
    def __init__(self):
        self.file = subdir("state") / "studio.sqlite3"
        self._lock = threading.RLock()
        with self.connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, title TEXT, script TEXT, model TEXT, voice TEXT,
                    reference TEXT, ref_text TEXT, options TEXT, status TEXT,
                    error TEXT DEFAULT '', created REAL, updated REAL, output TEXT
                );
                CREATE TABLE IF NOT EXISTS sections (
                    job_id TEXT, idx INTEGER, body TEXT, status TEXT DEFAULT 'pending',
                    file TEXT, error TEXT DEFAULT '', PRIMARY KEY(job_id,idx),
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS verified (
                    model TEXT PRIMARY KEY, signature TEXT, testfile TEXT, date REAL
                );
            """)
            conn.execute("UPDATE jobs SET status='paused' WHERE status='running'")
            conn.execute("UPDATE sections SET status='pending' WHERE status='running'")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.file, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def setting(self, key, default=None):
        with self._lock, self.connection() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        with self._lock, self.connection() as conn:
            conn.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def new_job(self, title, script, model, voice, reference="", ref_text="", options=None):
        options = options or {}
        job_id = uuid.uuid4().hex[:16]
        target = int(options.get("target_chars",0))
        mode = options.get("segment_mode", "sentence")
        if mode == "auto":  # whole script, cut into safe parts automatically
            options["auto_chunk"] = True
        elif not options.get("auto_chunk"):
            options["exact_parts"] = True  # exactly what was asked: one go, paragraphs or sentences

        def cut(text):
            if mode in ("auto", "whole"):
                parts = [text.strip()] if text.strip() else []
            elif mode == "group":
                text = text.replace("\r\n", "\n")
                paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
                if len(paras) == 1:
                    paras = [line.strip() for line in text.split("\n") if line.strip()]
                size = max(2, min(4, int(options.get("group_size", 3) or 3)))
                parts = ["\n\n".join(paras[i:i + size]) for i in range(0, len(paras), size)]
            elif mode == "paragraph":
                parts = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
                if target:
                    parts = [part for p in parts for part in (split_sentences(p, target) if len(p) > target else [p])]
            else:
                parts = split_sentences(text, target)
            if options.get("auto_chunk"):
                parts = safe_chunks(parts, model, int(options.get("chunk_chars", 0) or 0))
            return parts

        segments, gaps = [], []
        for text, pause_ms in split_pauses(script):  # [pause 1s] tags end a piece and add silence after it
            pieces = cut(text)
            segments.extend(pieces)
            gaps.extend([0] * (len(pieces) - 1) + [pause_ms] if pieces else [])
        if any(gaps):
            options["gaps"] = gaps
        if not segments:
            raise ValueError("Please enter a script before adding a job.")
        folder = subdir("jobs") / job_id
        folder.mkdir(parents=True, exist_ok=True)
        out = subdir("outputs") / f"{safe_file_name(title)}-{job_id[:6]}.wav"
        with self._lock, self.connection() as conn:
            conn.execute("""INSERT INTO jobs(id,title,script,model,voice,reference,ref_text,options,status,created,updated,output)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (job_id,title,script,model,voice,reference,ref_text,json.dumps(options),"pending",time.time(),time.time(),str(out)))
            conn.executemany("INSERT INTO sections(job_id,idx,body,status,file) VALUES(?,?,?,?,?)", [
                (job_id,i,text,"pending",str(folder/f"{i:04d}.wav")) for i,text in enumerate(segments)
            ])
        return job_id

    def jobs(self):
        with self._lock, self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY created DESC")]

    def set_job_options(self, job_id, options: dict):
        with self._lock, self.connection() as conn:
            conn.execute("UPDATE jobs SET options=?, updated=? WHERE id=?", (json.dumps(options), time.time(), job_id))

    def delete_job(self, job_id):
        with self._lock, self.connection() as conn:
            conn.execute("DELETE FROM sections WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        shutil.rmtree(subdir("jobs") / job_id, ignore_errors=True)

    def clear_jobs(self):
        """Remove queue records only; preserve all generated WAVs and model data."""
        with self._lock,self.connection() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                raise RuntimeError('Pause active generation before clearing the queue.')
            conn.execute('DELETE FROM sections');conn.execute('DELETE FROM jobs')

    def rechunk_failed(self,job_id):
        with self._lock,self.connection() as conn:
            job=conn.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            rows=conn.execute('SELECT * FROM sections WHERE job_id=? ORDER BY idx',(job_id,)).fetchall()
            if not job or not rows or any(r['status']=='complete' for r in rows):return
            chunks=safe_chunks([r['body'] for r in rows],job['model'])
            if len(chunks)==len(rows):return
            folder=subdir('jobs')/job_id
            conn.execute('DELETE FROM sections WHERE job_id=?',(job_id,))
            conn.executemany('INSERT INTO sections(job_id,idx,body,status,file) VALUES(?,?,?,?,?)',[(job_id,i,t,'pending',str(folder/f'{i:04d}.wav')) for i,t in enumerate(chunks)])

    def job(self, job_id):
        with self._lock, self.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
        return dict(row) if row else None

    def sections(self, job_id):
        with self._lock, self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM sections WHERE job_id=? ORDER BY idx", (job_id,))]

    def job_state(self, job_id, state, error=""):
        with self._lock, self.connection() as conn:
            conn.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?",(state,error,time.time(),job_id))

    def section_state(self, job_id, idx, state, error=""):
        with self._lock, self.connection() as conn:
            conn.execute("UPDATE sections SET status=?,error=? WHERE job_id=? AND idx=?",(state,error,job_id,idx))

    def change_section(self, job_id, idx, body):
        if not body.strip():
            raise ValueError("Section cannot be blank.")
        with self._lock, self.connection() as conn:
            conn.execute("UPDATE sections SET body=?,status='pending',error='' WHERE job_id=? AND idx=?",(body.strip(),job_id,idx))
            conn.execute("UPDATE jobs SET status='paused',updated=? WHERE id=?",(time.time(),job_id))
        # Old file remains on disk until successfully replaced, but won't be reused while status is pending.

    def verification(self, model):
        with self._lock, self.connection() as conn:
            row = conn.execute("SELECT * FROM verified WHERE model=?", (model,)).fetchone()
        return dict(row) if row else None

    def mark_verified(self, model, signature, testfile):
        if not is_audio(Path(testfile)):
            raise ValueError("Real audio test file was not generated.")
        with self._lock, self.connection() as conn:
            conn.execute("INSERT INTO verified(model,signature,testfile,date) VALUES(?,?,?,?) ON CONFLICT(model) DO UPDATE SET signature=excluded.signature,testfile=excluded.testfile,date=excluded.date",(model,signature,str(testfile),time.time()))

    def invalidate(self, model):
        with self._lock, self.connection() as conn:
            conn.execute("DELETE FROM verified WHERE model=?",(model,))


def safe_chunks(parts, model, override=0):
    # Conservative operational targets, not measured model maximums.
    # override: a smaller piece size chosen for slow PCs (never above the engine target).
    target=240 if model in ('chatterbox','turbo','dia') else 350 if model!='piper' else 700
    if override and override>=60:target=min(target,int(override))
    result=[]
    for part in parts:
        if len(part)<=target:
            result.append(part);continue
        line=''
        for piece in split_sentences(part,target):
            if line and len(line)+len(piece)+1>target:
                result.append(line);line=''
            line=(line+' '+piece).strip()
        if line:result.append(line)
    return result
