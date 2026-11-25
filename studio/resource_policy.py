"""Resource controls: admission thresholds, thread count and optional CUDA allocator budget."""
import os,math
DEFAULTS={'cpu_limit':85,'ram_limit':85,'gpu_limit':90}
MAX_LIMIT=100  # the owner may give the software the whole PC (Settings > Performance)

def limits(store):
    return {k:max(10,min(MAX_LIMIT,int(store.setting(k,v)))) for k,v in DEFAULTS.items()}

def admission(values, options, device):
    for label,key in [('CPU','cpu_limit'),('RAM','ram_limit'),('GPU','gpu_limit')]:
        if label=='GPU' and device!='cuda':continue
        val=values.get(label)
        if val is not None and val>=options[key] and options[key]<100:
            return f'Waiting for {label} usage below {options[key]}%'
    return ''

THREADS_OVERRIDE = 0  # 0 = automatic (from the CPU limit)


def set_threads_override(count):
    global THREADS_OVERRIDE
    THREADS_OVERRIDE = max(0, min(64, int(count or 0)))


def physical_cores():
    """Real cores (hyper-threads do not speed up voice engines; using them all makes them slower)."""
    try:
        import sys
        if sys.platform == "darwin":                 # the efficiency cores of an M-series Mac slow a shared job down: count the performance cores
            from . import platform_info as pi
            layout = pi.core_layout()
            if layout["efficiency"] and layout["performance"]:
                return layout["performance"]
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except Exception:
        pass
    return max(1, (os.cpu_count() or 2) // 2)


def thread_budget(pct, cores=None):
    total = cores or physical_cores()
    if THREADS_OVERRIDE:
        return max(1, min(total, THREADS_OVERRIDE))
    return max(1, math.floor(total*pct/100))
