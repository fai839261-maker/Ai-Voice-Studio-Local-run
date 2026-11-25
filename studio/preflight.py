"""Installer-side runtime check, run without GUI elevation or model downloads."""
from __future__ import annotations
import json
from datetime import datetime
from .core import subdir


def prepare_runtime(self_check, offline=False):
    from . import managed
    logfile = subdir('logs') / 'setup-preflight.log'
    def log(message):
        with logfile.open('a', encoding='utf-8') as fp:
            fp.write(str(message) + '\n')
    try:
        log('\n' + datetime.now().isoformat(timespec='seconds') + ' Installer preflight')
        if self_check() != 0:
            raise RuntimeError('Installed program resource check failed. Reinstall from the trusted setup EXE.')
        from .environment_audit import audit_existing, write_license_inventory
        write_license_inventory()
        python = managed.runtime_python() if offline else managed.ensure_runtime_serial(log)
        if offline and not python:
            log('OFFLINE: Python 3.11 missing; install from Settings when internet is available.')
            return 0
        log(f'Compatible runtime: {python}')
        # Check pip and venv capability. Repair pip only if missing; do not upgrade unrelated packages.
        if not managed.import_ok(python, 'pip'):
            managed.run([python, '-m', 'ensurepip', '--upgrade'], log, env=managed.env(False))
        managed.run([python, '-m', 'pip', '--version'], log, env=managed.env())
        if not managed.import_ok(python, 'venv'):
            raise RuntimeError('Python venv is missing; repair the Python 3.11 installation.')
        report=audit_existing(log)
        log(f"Existing environment audit: {report['repair_required']} profile(s) require repair. Repair is on demand.")
        log('PASS: installed resources and Python runtime; model-specific packages remain on demand.')
        return 0
    except Exception as exc:
        log(f'ERROR: {exc}')
        return 4
