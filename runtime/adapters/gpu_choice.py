"""Which graphics-card precision is fast on THIS card.

Older NVIDIA cards (Pascal and before: GTX 10 series, Quadro P, GTX 9 series) run half precision (fp16/bf16) at a tiny
fraction of their normal speed. A voice engine that is told to use half precision there can need many minutes for one
short part, while the processor needs seconds. Newer cards (Turing and later) are fast with fp16, Ampere and later with bf16.
"""


def capability(torch):
    try:
        if torch.cuda.is_available():
            return torch.cuda.get_device_capability(0)
    except Exception:
        pass
    return (0, 0)


def slow_half(torch) -> bool:
    """True for cards older than compute capability 7.0 (half precision is very slow there)."""
    major, _minor = capability(torch)
    return 0 < major < 7


def gpu_dtype(torch, opts=None):
    """Older cards (before the 2018 generation) are fast at FULL precision and very slow at half precision: they get float32.
    Newer cards get bfloat16 / float16 unless the software asked for full precision."""
    major, _minor = capability(torch)
    if 0 < major < 7 or (opts or {}).get("gpu_precision") == "full":
        return torch.float32
    if major >= 8 and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    return torch.float16


def pick_device(torch, opts, default="cpu") -> str:
    """'cuda', 'mps' (Apple graphics) or 'cpu': what was asked for, when this computer really has it."""
    want = opts.get("device", default)
    if want == "cuda" and torch.cuda.is_available():
        return "cuda"
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if want == "mps" and mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def use_cuda(torch, opts) -> bool:
    """CUDA is used when asked for and available."""
    if opts.get("device", "cuda") != "cuda" or not torch.cuda.is_available():
        return False
    return True        # the software already decided (per model) that the card fits; an older card runs at full precision
