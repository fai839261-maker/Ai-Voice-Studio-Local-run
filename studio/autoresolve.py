"""Auto resolve: when an optional part of an engine cannot be built on this PC (no build tools), install the engine without that part."""
from __future__ import annotations
import os
import shutil
import sys
import subprocess

BUILD_HINTS = ("cmake", "microsoft visual c++", "vcvarsall", "nmake", "cl.exe", "failed building wheel", "failed to build", "building wheel for")
# model -> the optional part, and the words in an error message that show that part failed
OPTIONAL = {"kokoro": {"name": "Japanese", "triggers": ("pyopenjtalk", "openjtalk")}}
OK_CODES = {0, -1978335189, -1978335135}      # done, already installed, nothing newer


def enabled() -> bool:
    return os.environ.get("AVS_AUTO_RESOLVE", "1") != "0"


def build_tools_missing(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in BUILD_HINTS)


def reducible(model: str, text: str) -> str | None:
    """The name of the optional part to leave out (for example 'Japanese'), or None when nothing can be dropped."""
    part = OPTIONAL.get(model)
    if not part or not enabled():
        return None
    low = (text or "").lower()
    if any(t in low for t in part["triggers"]) or build_tools_missing(text):
        return part["name"]
    return None


def cmake_folder() -> str:
    folder = r"C:\Program Files\CMake\bin"
    return folder if os.path.isdir(folder) else ""


def cpp_tools_present(run=subprocess.run) -> bool:
    """True when the Microsoft C++ build tools are installed (found with Microsoft's own vswhere tool)."""
    if os.name != "nt":
        return True
    vswhere = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not os.path.isfile(vswhere):
        return False
    try:
        result = run([vswhere, "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                     capture_output=True, text=True, errors="replace", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return bool((result.stdout or "").strip())
    except (OSError, subprocess.SubprocessError):
        return False


def cmake_present() -> bool:
    return os.name != "nt" or bool(shutil.which("cmake") or cmake_folder())


def build_tools_present() -> bool:
    if sys.platform == "darwin":
        try:
            return subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return cpp_tools_present() and cmake_present()


def install_build_tools(log=lambda m: None, run=subprocess.run, which=shutil.which, have_cmake=None, have_cpp=None) -> tuple[bool, str]:
    """CMake and the Microsoft C++ build tools through winget (Windows only). Returns (ok, short message)."""
    if sys.platform == "darwin":
        if build_tools_present():
            return True, "The Apple developer tools are already installed."
        try:
            subprocess.Popen(["/usr/bin/xcode-select", "--install"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return False, "Could not open the developer tools installer: " + str(exc)
        return True, "macOS opened its installer for the developer tools: click Install and wait until it finishes."
    if os.name != "nt":
        return False, "Build tools are only needed on Windows and Mac."
    if not which("winget"):
        return False, 'Windows Package Manager (winget) was not found. Install "App Installer" from the Microsoft Store, then try again.'
    common = ["winget", "install", "-e", "--accept-package-agreements", "--accept-source-agreements"]
    steps = (("CMake", [*common, "--id", "Kitware.CMake", "--silent"]),
             ("Microsoft C++ build tools", [*common, "--id", "Microsoft.VisualStudio.2022.BuildTools", "--override",
                                            "--wait --quiet --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"]))
    have = {"CMake": (have_cmake or cmake_present)(), "Microsoft C++ build tools": (have_cpp or cpp_tools_present)()}
    for name, command in steps:
        if have[name]:
            log(f"{name} is already installed.")
            continue
        log(f"Installing {name}…")
        try:
            result = run(command, capture_output=True, text=True, errors="replace", timeout=3 * 3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{name} could not be installed: {exc}"
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode not in OK_CODES and "already installed" not in output.lower():
            first = next((line.strip() for line in output.splitlines() if line.strip()), "unknown problem")
            return False, f"{name} could not be installed ({first[:160]})."
    folder = cmake_folder()
    if folder and folder not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + folder
    return True, "Build tools installed. Press Repair engines to finish."
