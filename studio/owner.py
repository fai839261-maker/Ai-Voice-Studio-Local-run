"""Owner / user access control.

The owner decides, feature by feature, what a person who receives the software can
SEE and USE:

    show  - visible and usable
    lock  - visible but greyed out (cannot be changed)
    hide  - not visible at all

The rules live in one small file (config/access.json) inside the application data
folder, so they travel with a Portable Pack backup.

Honest limits: this protects against casual changes by ordinary users. It is not a
security boundary against someone who can read and edit files on the PC.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import sys
import secrets
from pathlib import Path

from .core import root, bundled
from . import ed25519, permits
import base64
import hashlib
import platform
import uuid

SHOW, LOCK, HIDE = "show", "lock", "hide"
STATES = (SHOW, LOCK, HIDE)
STATE_LABELS = {SHOW: "Show and allow", LOCK: "Show but lock", HIDE: "Hide"}

# (key, group, title, hint)
FEATURES = [
    # Pages in the left menu
    ("page.voices", "Menu pages", "Voice Library", "Listen to voices, favourites"),
    ("page.design", "Menu pages", "Voice Design", "Create a new voice from a description"),
    ("page.training", "Menu pages", "Voice Training", "Prepare recordings, build or export a voice"),
    ("page.batch", "Menu pages", "Batch Queue", "Several scripts at once"),
    ("page.channels", "Menu pages", "Channels", "Channels with their scripts, prompts and voice overs"),
    ("page.clone", "Menu pages", "Voice Clone", "Create and manage cloned voices"),
    ("page.check", "Menu pages", "Check words", "Check that every word of the script was spoken"),
    ("page.logs", "Menu pages", "Activity Logs", "Error history"),
    ("page.settings", "Menu pages", "Settings", "Whole settings page"),
    # Voice Studio controls
    ("ctl.voice_type", "Voice Studio controls", "Voice type", "Built-in or cloned"),
    ("ctl.engine", "Voice Studio controls", "Voice engine choice", "Which engine makes the voice"),
    ("ctl.search", "Voice Studio controls", "Find voice box", ""),
    ("ctl.favorites", "Voice Studio controls", "Favourite voices", "Star button and quick list"),
    ("ctl.preview", "Voice Studio controls", "Listen to voice sample", "Play button next to the voice"),
    ("ctl.language", "Voice Studio controls", "Language", ""),
    ("ctl.speed", "Voice Studio controls", "Speed", ""),
    ("ctl.pitch", "Voice Studio controls", "Pitch", ""),
    ("ctl.style", "Voice Studio controls", "Style controls", "Variation, voice match, expression, style mix"),
    ("ctl.emotion", "Voice Studio controls", "Emotion styles", "Happy, sad, calm and more"),
    ("ctl.polish", "Voice Studio controls", "Audio polish", "Pause length, volume levelling, silence, fades"),
    ("ctl.segment", "Voice Studio controls", "Long script mode", "How a long script is split"),
    ("ctl.import", "Voice Studio controls", "Import script buttons", ""),
    # Settings sections
    ("set.appearance", "Settings sections", "Appearance", "Theme, colour, text size"),
    ("set.performance", "Settings sections", "Performance / low-end PC", ""),
    ("set.processing", "Settings sections", "Processing (jobs, downloads, compute)", ""),
    ("set.runtime", "Settings sections", "Python runtime", ""),
    ("set.update", "Settings sections", "Updates", "Check for updates and install update files"),
    ("set.dependencies", "Settings sections", "Check software and dependencies", ""),
    ("set.storage", "Settings sections", "Storage and folders", ""),
    # Advanced
]
FEATURE_KEYS = [f[0] for f in FEATURES]
# Never shown to ordinary users, cannot be configured
# Only the owner ever sees these; they cannot be switched on for users
OWNER_ONLY = {"access.manager", "page.sources", "page.logs", "page.channels", "set.backup", "set.runtime", "set.processing", "set.dependencies",
              "setup.attach", "setup.add_model"}      # shown only after the owner key is added
# Users can always open AI Setup, so they can download voices that are not in a backup
ALWAYS_ALLOWED = {"page.setup"}
# Hidden from users until the owner switches them on (they reveal technical sources)
DEFAULT_HIDDEN: set = set()

PRESETS = {
    "everything": ("Allow everything", {}),
    "simple": ("Simple user: only write, listen, generate", {
        "page.batch": HIDE, "page.channels": HIDE, "page.clone": HIDE, "page.logs": HIDE, "page.training": HIDE,
        "page.settings": HIDE, "ctl.engine": HIDE, "ctl.voice_type": HIDE, "setup.add_model": HIDE,
    }),
    "worker": ("Worker: all voice tools, no setup or system settings", {
        "page.settings": HIDE, "page.logs": HIDE,
    }),
    "locked": ("Everything visible but locked", {k: LOCK for k in FEATURE_KEYS if not k.startswith("page.")}),
}

ITERATIONS = 200_000


# ---------------------------------------------------------------- the owner key
KEY_PREFIX = "AVS"


def encode_key(secret: bytes) -> str:
    """32 secret bytes -> 'AVS-XXXXX-XXXXX-...' (with a 2-byte check so a typing mistake is noticed)."""
    check = hashlib.sha256(b"AVS-KEY" + secret).digest()[:2]
    text = base64.b32encode(secret + check).decode("ascii").rstrip("=")
    return KEY_PREFIX + "-" + "-".join(text[i:i + 5] for i in range(0, len(text), 5))


def decode_key(text: str) -> bytes:
    cleaned = "".join(c for c in (text or "").upper() if c.isalnum())
    if cleaned.startswith(KEY_PREFIX):
        cleaned = cleaned[len(KEY_PREFIX):]
    try:
        raw = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (ValueError, TypeError):
        raise ValueError("That is not a valid owner key. Check it for typing mistakes.")
    if len(raw) != 34 or hashlib.sha256(b"AVS-KEY" + raw[:32]).digest()[:2] != raw[32:]:
        raise ValueError("That is not a valid owner key. Check it for typing mistakes.")
    return raw[:32]


def embedded_public() -> str:
    """The owner's PUBLIC key that comes with the software (empty when this copy has none). It can only check, never unlock."""
    file = os.environ.get("AVS_OWNER_KEY_FILE")
    try:
        data = json.loads(Path(file).read_text(encoding="utf-8")) if file else json.loads(bundled("resources/owner_key.json").read_text(encoding="utf-8"))
        public = str(data.get("public_key", ""))
        bytes.fromhex(public)
        return public if len(public) == 64 else ""
    except (OSError, ValueError, AttributeError):
        return ""


def embedded_publics() -> list:
    """Every owner PUBLIC key this software trusts (a list, so an owner key made earlier keeps working after a new one was added)."""
    file = os.environ.get("AVS_OWNER_KEY_FILE")
    try:
        data = json.loads(Path(file).read_text(encoding="utf-8")) if file else json.loads(bundled("resources/owner_key.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return []
    found = []
    for item in [*(data.get("public_keys") or []), data.get("public_key", "")]:
        try:
            bytes.fromhex(str(item))
        except ValueError:
            continue
        if len(str(item)) == 64 and str(item) not in found:
            found.append(str(item))
    return found


def key_source() -> str:
    layer = os.getenv("AVS_OVERLAY_DIR")
    if layer and (Path(layer) / "resources/owner_key.json").exists():
        return "an installed update (version " + os.getenv("AVS_OVERLAY_VERSION", "?") + ")"
    return "the installed program"


def machine_id() -> str:
    parts = [platform.node(), hex(uuid.getnode())]
    try:
        if sys.platform == "darwin":
            import re as _re, subprocess as _sp
            out = _sp.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=8).stdout
            match = _re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            if match:
                parts.append(match.group(1))
        elif os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                parts.append(str(winreg.QueryValueEx(k, "MachineGuid")[0]))
    except Exception:
        pass
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def config_path() -> Path:
    folder = root() / "config"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "access.json"


def _hash(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


class Access:
    """Reads and writes access.json. `mode` is the mode of this running session."""

    def __init__(self, path: Path | None = None, key_public: str | None = None):
        self.path = Path(path) if path else config_path()
        self.data = self._load()
        # Only the real program (default file) uses the owner key that comes with it; tests pass their own.
        if key_public is not None:
            self.key_publics = [key_public] if key_public else []
        else:
            self.key_publics = embedded_publics() if path is None else []
        self.key_public = self.key_publics[0] if self.key_publics else ""
        self.last_error = ""
        self.owner_secret: bytes | None = None
        if self.key_mode:
            self.mode = "owner" if self.proof_valid() else "user"
        else:
            self.mode = "user" if self.data.get("startup_mode") == "user" and self.has_password else "owner"

    # ---- owner key (this is how the owner proves who they are)
    @property
    def key_mode(self) -> bool:
        return bool(self.key_public)

    def proof_path(self) -> Path:
        return self.path.with_name("owner_proof.json")

    def proof_valid(self) -> bool:
        """The owner signed this PC once; the note cannot be copied to another PC or made without the key."""
        try:
            data = json.loads(self.proof_path().read_text(encoding="utf-8"))
            if data.get("machine") != machine_id():
                return False
            for public in self.key_publics:
                if ed25519.verify(bytes.fromhex(public), b"AVS-owner|" + machine_id().encode(), bytes.fromhex(data["signature"])):
                    self.key_public = public          # the key this PC was unlocked with (permits are checked against it)
                    return True
            return False
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def login_with_key(self, text: str) -> bool:
        self.last_error = ""
        try:
            secret = decode_key(text)
        except ValueError as exc:
            self.last_error = str(exc)
            return False
        public = ed25519.public_key(secret).hex()
        if public not in self.key_publics:
            self.last_error = "That is a valid owner key, but not one this software accepts."
            return False
        self.key_public = public
        self.owner_secret = secret
        signature = ed25519.sign(secret, b"AVS-owner|" + machine_id().encode()).hex()
        self.proof_path().parent.mkdir(parents=True, exist_ok=True)
        self.proof_path().write_text(json.dumps({"machine": machine_id(), "signature": signature}), encoding="utf-8")
        self.mode = "owner"
        return True

    def accepted_fingerprints(self) -> str:
        from . import updater
        return ", ".join(updater.fingerprint_of(k) for k in self.key_publics)

    def lock_this_copy(self) -> None:
        """Back to User mode on this PC until the key is entered again."""
        self.proof_path().unlink(missing_ok=True)
        self.owner_secret = None
        self.mode = "user"

    def backup_allowed(self) -> bool:
        """Owners can always make backups; other copies only with a permit signed by the owner."""
        if self.is_owner:
            return True
        return self.key_mode and any(permits.verify(permits.load(self.path.with_name(permits.NAME)), k) for k in self.key_publics)

    # ---- persistence -------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("features", {})
                return data
        except (OSError, ValueError):
            pass
        return {"features": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    # ---- owner password ----------------------------------------------------
    @property
    def has_password(self) -> bool:
        return bool(self.data.get("hash") and self.data.get("salt"))

    def set_password(self, password: str) -> None:
        if len(password) < 4:
            raise ValueError("Use at least 4 characters for the owner password.")
        salt = secrets.token_bytes(16)
        self.data.update(salt=salt.hex(), hash=_hash(password, salt), iterations=ITERATIONS)
        self.save()

    def verify(self, password: str) -> bool:
        if not self.has_password:
            return True
        try:
            salt = bytes.fromhex(self.data["salt"])
            iterations = int(self.data.get("iterations", ITERATIONS))
        except (ValueError, KeyError, TypeError):
            return False
        return hmac.compare_digest(_hash(password, salt, iterations), str(self.data["hash"]))

    # ---- mode --------------------------------------------------------------
    open_to_all = False        # 0.22: the program switches this on, so nobody needs an owner key to see or use anything

    @property
    def is_owner(self) -> bool:
        return self.open_to_all or self.mode == "owner"

    def enter_owner(self, password: str) -> bool:
        if self.key_mode:
            return self.login_with_key(password)
        if self.verify(password):
            self.mode = "owner"
            return True
        return False

    def lock_for_users(self) -> None:
        """Switch this session to user mode and make future launches start in it."""
        if not self.has_password:
            raise ValueError("Create an owner password first.")
        self.data["startup_mode"] = "user"
        self.save()
        self.mode = "user"

    def start_in_owner_mode(self) -> None:
        self.data["startup_mode"] = "owner"
        self.save()

    # ---- features ----------------------------------------------------------
    def configured(self, key: str) -> str:
        default = HIDE if key in DEFAULT_HIDDEN else SHOW
        state = self.data.get("features", {}).get(key, default)
        return state if state in STATES else default

    def state(self, key: str) -> str:
        """Effective state for this session. The owner always has full access."""
        if self.open_to_all:
            return SHOW
        if key == "set.backup":
            return SHOW if self.backup_allowed() else HIDE
        if key in OWNER_ONLY:
            return SHOW if self.is_owner else HIDE
        if key in ALWAYS_ALLOWED:
            return SHOW
        return SHOW if self.is_owner else self.configured(key)

    def set_feature(self, key: str, state: str) -> None:
        if key not in FEATURE_KEYS:
            raise KeyError(key)
        if state not in STATES:
            raise ValueError(state)
        features = self.data.setdefault("features", {})
        if state == (HIDE if key in DEFAULT_HIDDEN else SHOW):
            features.pop(key, None)
        else:
            features[key] = state
        self.save()

    def apply_preset(self, name: str) -> None:
        if name not in PRESETS:
            raise KeyError(name)
        rules = {k: v for k, v in PRESETS[name][1].items() if k in FEATURE_KEYS}
        for key in DEFAULT_HIDDEN:  # the "everything" style presets must switch these on explicitly
            rules.setdefault(key, SHOW)
        self.data["features"] = {k: v for k, v in rules.items() if v != (HIDE if k in DEFAULT_HIDDEN else SHOW)}
        self.save()

    def summary(self) -> dict:
        counts = {SHOW: 0, LOCK: 0, HIDE: 0}
        for key in FEATURE_KEYS:
            counts[self.configured(key)] += 1
        return counts

    # ---- branding ----------------------------------------------------------
    def footer_text(self, default: str) -> str:
        return str(self.data.get("footer", default))

    def set_footer(self, text: str) -> None:
        self.data["footer"] = text.strip()
        self.save()
