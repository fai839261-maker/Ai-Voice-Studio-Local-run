"""Keeps the software (this window AND every helper process it started) inside the limits of Settings > Performance.

Before 0.18 the limits only chose how many threads a voice engine may start. Nothing measured the total, so the window plus the
Python helpers could together use far more than the limit. Now:
- CPU: Windows itself enforces a hard cap for the whole software (the shutdown job). If that is not available, a measuring loop
  pauses the helper processes for a fraction of every second so the total stays at the limit.
- Memory: when the software's memory is above the limit, idle voice engines are unloaded.
- Graphics card: when the card is busier than the limit, the graphics helper is paused for a fraction of every second.
The limit means the whole PC as the Task Manager shows it (100 percent = every thread).
"""
import threading
import time

TICK = 0.5


def logical_cores() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=True) or 1
    except Exception:
        import os
        return os.cpu_count() or 1


def _tree(root_pid):
    import psutil
    root = psutil.Process(root_pid)
    return root, root.children(recursive=True)


def _seconds(procs) -> float:
    total = 0.0
    for proc in procs:
        try:
            t = proc.cpu_times()
            total += t.user + t.system
        except Exception:
            pass
    return total


def pause_fraction(used: float, budget: float, window: float, cap: float = 0.9) -> float:
    """Seconds to pause so that running / (running + paused) = budget / used."""
    if budget <= 0:
        return min(cap, window)
    if used <= budget:
        return 0.0
    return min(cap, window * (used / budget - 1))


class Limiter:
    def __init__(self, get_limits, root_pid=None, cores=None, hard_cap=None, gpu_util=None, gpu_pids=None, total_ram=None,
                 on_ram=None, log=None, keep=None):
        import os
        self.get_limits = get_limits                    # -> {'cpu': %, 'ram': %, 'gpu': %}
        self.root_pid = root_pid or os.getpid()
        self.cores = cores or logical_cores()
        self.hard_cap = hard_cap                        # callable(percent) -> True when Windows enforces the CPU cap itself
        self.gpu_util = gpu_util or (lambda: None)      # -> system graphics-card use in percent, or None
        self.gpu_pids = gpu_pids or (lambda: [])        # -> process ids of the helpers that work on the graphics card
        self.on_ram = on_ram or (lambda: None)
        self.log = log or (lambda text: None)
        self.keep = keep or (lambda: set())             # process ids that must never be paused
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="avs-limiter")
        self.last = {"cpu": 0.0, "throttled": False, "cap_by_windows": False}
        self._noted = {}
        self._ram_total = total_ram
        self._paused = set()

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        self._resume(list(self._paused))

    # ---- helpers
    def _note(self, key, text, every=60.0):
        now = time.monotonic()
        if now - self._noted.get(key, -1e9) >= every:
            self._noted[key] = now
            self.log(text)

    def _pause(self, procs, seconds):
        held = []
        for proc in procs:
            try:
                proc.suspend()
                held.append(proc)
            except Exception:
                pass
        self._paused.update(held)
        self.stop.wait(seconds)
        self._resume(held)

    def _resume(self, procs):
        for proc in procs:
            try:
                proc.resume()
            except Exception:
                pass
        self._paused.difference_update(procs)

    # ---- one measuring step (the loop calls this; tests call it too)
    def step(self, window=TICK):
        limits = self.get_limits()
        cpu_limit, ram_limit, gpu_limit = (int(limits.get(k, 100)) for k in ("cpu", "ram", "gpu"))
        try:
            root, kids = _tree(self.root_pid)
        except Exception:
            return
        spared = set(self.keep())
        helpers = [p for p in kids if p.pid not in spared]
        before_root, before_helpers = _seconds([root]), _seconds(helpers)
        started = time.monotonic()
        if self.stop.wait(window):
            return
        elapsed = max(0.05, time.monotonic() - started)
        used_root = max(0.0, _seconds([root]) - before_root)
        used_helpers = max(0.0, _seconds(helpers) - before_helpers)
        share = (used_root + used_helpers) / (self.cores * elapsed) * 100
        self.last.update(cpu=round(share, 1), throttled=False)

        # CPU
        by_windows = bool(self.hard_cap and self.hard_cap(cpu_limit))
        self.last["cap_by_windows"] = by_windows
        if cpu_limit < 100 and not by_windows:
            allowed = cpu_limit / 100 * self.cores * elapsed
            budget = max(0.05 * self.cores * elapsed, allowed - used_root)      # the window itself cannot be paused
            if used_root + used_helpers > allowed and used_helpers > budget:
                pause = pause_fraction(used_helpers, budget, window)
                self.last["throttled"] = True
                self._note("cpu", f"Limit: the software used {share:.0f}% of the processor; helpers are paused briefly to keep it at {cpu_limit}%.")
                live = []
                for proc in helpers:
                    try:
                        if proc.is_running():
                            live.append(proc)
                    except Exception:
                        pass
                self._pause(live, pause)

        # memory
        if ram_limit < 100:
            try:
                import psutil
                total = self._ram_total or psutil.virtual_memory().total
                rss = 0
                for proc in [root, *helpers]:
                    try:
                        rss += proc.memory_info().rss
                    except Exception:
                        pass
                if rss > total * ram_limit / 100:
                    self._note("ram", f"Limit: the software uses {rss / total * 100:.0f}% of the memory (limit {ram_limit}%); idle voice engines are unloaded.", 30.0)
                    self.on_ram()
            except Exception:
                pass

        # graphics card
        if gpu_limit < 100:
            try:
                util = self.gpu_util()
                pids = set(self.gpu_pids())
                if util is not None and util > gpu_limit and pids:
                    targets = [p for p in kids if p.pid in pids]
                    pause = pause_fraction(util, gpu_limit, window, cap=0.8)
                    self._note("gpu", f"Limit: the graphics card was {util:.0f}% busy (limit {gpu_limit}%); the graphics helper is paused briefly.")
                    self.last["throttled"] = True
                    self._pause(targets, pause)
            except Exception:
                pass

    def _run(self):
        while not self.stop.is_set():
            try:
                self.step()
            except Exception:
                self.stop.wait(1.0)


def smi_gpu_util():
    """Graphics-card use in percent (NVIDIA), or None."""
    try:
        import subprocess
        from .guard import smi_path
        tool = smi_path()
        if not tool:
            return None
        out = subprocess.run([tool, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=3, creationflags=0x08000000 if __import__("os").name == "nt" else 0)
        if out.returncode == 0:
            return max(float(x) for x in out.stdout.split())
    except Exception:
        pass
    return None


_current = {"limiter": None}


def start(store, log=None):
    """Start the one limiter of the software. The limits are read from the settings every few seconds, so a change in Settings counts at once."""
    if _current["limiter"] is not None:
        return _current["limiter"]
    from . import shutdown, warm_worker
    from .resource_policy import limits

    cache = {"at": 0.0, "value": {"cpu": 100, "ram": 100, "gpu": 100}}

    def get_limits():
        now = time.monotonic()
        if now - cache["at"] > 2.0:
            cache["at"] = now
            try:
                values = limits(store)
                cache["value"] = {"cpu": values["cpu_limit"], "ram": values["ram_limit"], "gpu": values["gpu_limit"]}
            except Exception:
                pass
        return cache["value"]

    gpu_cache = {"at": 0.0, "value": None}

    def gpu_util():
        now = time.monotonic()
        if now - gpu_cache["at"] > 1.5:
            gpu_cache["at"] = now
            gpu_cache["value"] = smi_gpu_util()
        return gpu_cache["value"]

    lim = Limiter(get_limits, hard_cap=shutdown.set_cpu_cap, gpu_util=gpu_util, gpu_pids=warm_worker.gpu_pids,
                  on_ram=warm_worker.release_idle, log=log, keep=lambda: shutdown._KEEP).start()
    lim.refresh = lambda: cache.__setitem__("at", 0.0)          # a change in Settings counts at once
    _current["limiter"] = lim
    return lim
