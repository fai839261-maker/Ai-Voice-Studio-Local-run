"""Understand why a part failed, in plain words, and say what fixes it."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Diagnosis:
    code: str
    title: str
    advice: str
    action: str = ""        # 'split' | 'retry_auto' | 'cpu' | 'repair' | 'transcript' | 'health' | ''
    action_label: str = ""


RULES = (
    (("index out of range in self", "text_pos_emb", "srcindex < srcselectdimsize"),
     Diagnosis("too_long", "The text was too long for this model in one piece",
               "Voice models can only read a limited amount at a time. The software splits long text into smaller parts automatically; "
               "this job was sent in one piece. Use Automatic mode, or press the button to re-run it with safe parts.", "retry_auto", "Re-run with safe parts")),
    (("out of memory", "cuda error: out of memory", "cublas_status_alloc_failed"),
     Diagnosis("memory", "Not enough graphics memory", "Close other programs that use the graphics card, or switch to Low-end mode / processor in Settings. "
               "Smaller parts also use less memory.", "cpu", "Use the processor for now")),
    (("device-side assert", "cuda error", "cudnn error", "no kernel image"),
     Diagnosis("cuda", "The graphics card stopped the voice engine", "This is usually a graphics driver or card compatibility problem. "
               "The processor is slower but always works. Updating the NVIDIA driver also helps.", "cpu", "Use the processor for now")),
    (("no module named", "importerror", "dll load failed", "cannot import name", "modulenotfounderror"),
     Diagnosis("repair", "A part of this voice model is missing or damaged", "Repairing re-installs the model's components. Your voices and settings are not touched.",
               "repair", "Repair this model")),
    (("missing chatterbox model file", "missing xtts", "missing stylets", "filenotfounderror", "no such file", "model file"),
     Diagnosis("files", "Some model files are missing", "Download the model again in Models. Files that are already there are kept.", "repair", "Repair this model")),
    (("exact transcript", "transcript required", "exact words"),
     Diagnosis("transcript", "This model needs the words spoken in your voice sample", "Type them once. They are saved with the voice and used by every model.",
               "transcript", "Add the words")),
    (("longer than 5 seconds", "reference clip", "reference audio required", "reference wav"),
     Diagnosis("reference", "The voice sample is too short or missing", "Use a clear sample of 6 to 30 seconds. Open Clones and add a longer one.")),
    (("timed out", "timeout"),
     Diagnosis("timeout", "This part took too long", "Try smaller parts (Settings > Performance > Part size), a lighter model such as Chatterbox Turbo, or Low-end mode.")),
    (("not installed", "engine is not installed"),
     Diagnosis("install", "This voice model is not installed", "Open Models and press Download.", "repair", "Open Models")),
)


def diagnose(text: str, model: str = "") -> Diagnosis:
    low = (text or "").lower()
    for needles, diagnosis in RULES:
        if any(n in low for n in needles):
            if diagnosis.code == "too_long":
                return Diagnosis(diagnosis.code, diagnosis.title, diagnosis.advice, "split" if model else diagnosis.action, diagnosis.action_label)
            return diagnosis
    return Diagnosis("other", "Something went wrong", "Press Settings > Tools > Check everything. It checks Python, audio tools and every model and can fix what is missing.",
                     "health", "Check everything")
