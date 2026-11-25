"""Runs out-of-process using the detected real Python; no PySide6 dependency."""
from __future__ import annotations
import argparse
import json
import sys
import traceback
from pathlib import Path

ADAPTERS={
 "indextts2":"adapters.indextts_adapter", "xtts2":"adapters.xtts2_adapter", "styletts2":"adapters.styletts2_adapter", "qwen17bdesign":"adapters.qwen_adapter", "dia":"adapters.dia_adapter",
 "piper":"adapters.piper_adapter", "fishs2":"adapters.fishs2_adapter",
 "kokoro":"adapters.kokoro_adapter",
 "chatterbox":"adapters.chatterbox_adapter",
 "f5":"adapters.f5_adapter",
 "openf5":"adapters.openf5_adapter", "turbo":"adapters.turbo_adapter", "voxcpm":"adapters.voxcpm_adapter",
 **{f"qwen{x}{y}":"adapters.qwen_adapter" for x in ("06b","17b") for y in ("clone","voice")}
}

def generate(job):
    import os
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK','1')      # Apple graphics: operations Metal lacks run on the processor instead of failing
    opts=job.get('options',{})
    threads=max(1,int(opts.get('cpu_threads',os.cpu_count() or 1)))
    for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]=str(threads)
    module='adapters.vits_adapter' if job['model'].startswith('hf_vits_') else ADAPTERS.get(job['model'])
    if not module:
        raise ValueError("No adapter exists for selected model")
    import importlib
    adapter=importlib.import_module(module)
    print(f"Preparing {job['model']} adapter",flush=True)
    if job['model'] not in ('piper','kokoro'):
        import torch
        torch.set_num_threads(threads)
        if opts.get('device')=='cuda' and torch.cuda.is_available():
            fraction=max(.1,min(1.,float(opts.get('gpu_memory_fraction',1.))))
            if fraction<1:torch.cuda.set_per_process_memory_fraction(fraction,0)
    adapter.generate(job)
    print("Completed real audio generation",flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--payload")
    parser.add_argument("--worker",action="store_true")
    args=parser.parse_args()
    if args.worker:
        # stdin closes automatically when the parent exits; stderr is merged into logs.
        for line in sys.stdin:
            request={}
            try:
                request=json.loads(line)
                generate(request['job'])
                print('\nAVS_RESULT '+json.dumps({'id':request['id'],'ok':True}),flush=True)
            except Exception:
                print('\nAVS_RESULT '+json.dumps({'id':request.get('id','') if isinstance(request,dict) else '', 'ok':False,'error':traceback.format_exc()}),flush=True)
                return 1 # never reuse a potentially poisoned CUDA context
        return 0
    if not args.payload:parser.error('--payload or --worker required')
    generate(json.loads(Path(args.payload).read_text(encoding="utf-8")))
    return 0

if __name__=="__main__":
    try:sys.exit(main())
    except Exception:
        traceback.print_exc();sys.exit(1)
