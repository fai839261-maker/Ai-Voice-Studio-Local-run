"""Small, testable policies shared by the setup and generation UI."""
import re
from datetime import datetime


def effective_device(entry, preferred):
    return preferred if entry.get('torch') and preferred in ('cuda', 'mps') else 'cpu'


def automatic_title(script, now=None):
    words = re.findall(r'\S+', script.strip())
    prefix = ' '.join(words[:5])[:48].strip()
    stamp = (now or datetime.now()).strftime('%d %b %H-%M-%S')
    return f'{prefix or "Voice Over"} — {stamp}'


def setup_state(stage, dependency='', cancelling=False, repair=False):
    if cancelling: return 'Cancelling'
    s = stage.lower()
    if 'connection interrupted' in s or s.startswith(('retry','server cannot resume')): return 'Retrying'
    if s.startswith(('checking', 'verifying', 'reusing', 'attached')): return 'Checking'
    if s.startswith(('connecting', 'contacting')): return 'Resolving'
    if s in ('installing dependencies', 'weights downloaded'):
        if dependency.startswith('Downloading'): return 'Downloading'
        if dependency.startswith(('Checking', 'Dependencies ready')): return 'Checking'
        if dependency.startswith('Waiting'): return 'Waiting'
        if dependency.startswith('Repairing'): return 'Repairing'
        if dependency.startswith('Installing'): return 'Repairing' if repair else 'Installing'
        return 'Resolving'
    return 'Downloading'


def dependency_state(message):
    s = message.lower()
    if 'dependencies ready' in s: return 'Dependencies ready'
    if 'repair needed' in s or 'reinstall' in s or 'repairing' in s: return 'Repairing dependencies'
    if 'waiting for' in s: return 'Waiting for dependencies'
    if 'installing' in s or 'building wheel' in s: return 'Installing dependencies'
    if 'downloading' in s or s.startswith('package ') or 'fetching' in s: return 'Downloading dependencies'
    if any(x in s for x in ('check','verified','reuse','reusing')): return 'Checking dependencies'
    return 'Resolving dependencies'


def render_timeout(model, text):
    # Short ONNX requests should never silently occupy the worker for an hour.
    if model in ('piper', 'kokoro') and len(text) <= 300: return 120
    return 3600
