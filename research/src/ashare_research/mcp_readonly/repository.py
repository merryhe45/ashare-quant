"""Bounded file reads. No caller-controlled path, cache, network or write API."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from .errors import ArtifactError

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "artifacts"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
FACTORS = "(?:momentum_20|momentum_60|momentum_120|ma60_distance|volatility_20|liquidity_20)"
SUPPORTED = re.compile(r"(?:results\.json|report\.md|(?:factors|labels|assignments|candidate_manifest|sample_manifest|coverage_daily|coverage_symbols|observations|outcomes|comparison|matched_signal_keys)\.parquet|factor_"+FACTORS+r"\.parquet|(?:native|matched)_"+FACTORS+r"_(?:assignments|outcomes)\.parquet)\Z")
MAX_FILE = 16 * 1024 * 1024
MAX_REPORT = 1024 * 1024
MAX_CHILDREN = 256


def is_redirection(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def reject_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_json(raw):
    # Bound depth before json.loads to avoid decoder recursion/stack exhaustion.
    depth = 0
    quoted = escaped = False
    for char in raw.decode("utf-8"):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting limit")
        elif char in "]}":
            depth -= 1
    def invalid(_):
        raise ValueError("Nonstandard JSON number")
    return json.loads(raw, object_pairs_hook=reject_pairs, parse_constant=invalid)


class Repository:
    def __init__(self, root=DEFAULT_ROOT):
        # Alternate roots are for trusted in-process tests, never MCP parameters.
        self.root = Path(os.path.abspath(root))

    def checked_path(self, experiment_id=None, name=None):
        if experiment_id is not None and not ID.fullmatch(experiment_id):
            raise ArtifactError("INVALID_ARGUMENT", "Invalid experiment identifier")
        if name is not None and not SUPPORTED.fullmatch(name):
            raise ArtifactError("INVALID_ARGUMENT", "Unsupported artifact type")
        path = self.root
        if experiment_id is not None:
            path = path / experiment_id
        if name is not None:
            path = path / name
        # Check every ancestor, including the trusted root's original spelling.
        for component in reversed([path, *path.parents]):
            info = component.lstat()
            if is_redirection(info):
                raise ArtifactError("INVALID_ARTIFACT", "Filesystem redirection rejected")
        if not path.resolve(strict=True).is_relative_to(self.root):
            raise ArtifactError("INVALID_ARTIFACT", "Artifact escaped approved root")
        return path

    def read(self, experiment_id, name):
        try:
            path = self.checked_path(experiment_id, name)
            initial = path.lstat()
            if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
                raise ArtifactError("INVALID_ARTIFACT", "Artifact must be a regular unlinked file")
            cap = MAX_REPORT if name == "report.md" else MAX_FILE
            if initial.st_size > cap:
                raise ArtifactError("INVALID_ARTIFACT", "Artifact exceeds input size limit")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
                    raise ArtifactError("ARTIFACT_CHANGED", "Artifact changed while opening")
                if os.name == "nt":
                    self._verify_windows_handle(handle.fileno(), path)
                raw = handle.read(cap + 1)
                final = os.fstat(handle.fileno())
            after = self.checked_path(experiment_id, name).lstat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            # Windows path stat and handle fstat disagree on ctime semantics;
            # compare ctime only within the same API, and file ID/mtime across both.
            if len(raw) > cap or identity(initial) != identity(final) or identity(final) != identity(after) or initial.st_ctime_ns != after.st_ctime_ns or opened.st_ctime_ns != final.st_ctime_ns:
                raise ArtifactError("ARTIFACT_CHANGED", "Artifact changed during read")
            return raw, {"experiment_id": experiment_id, "artifact_relative_path": f"{experiment_id}/{name}", "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
        except FileNotFoundError:
            raise ArtifactError("NOT_FOUND", "Experiment or artifact not found") from None
        except OSError:
            raise ArtifactError("INVALID_ARTIFACT", "Artifact could not be read safely") from None

    def _verify_windows_handle(self, descriptor, expected):
        import ctypes
        from ctypes import wintypes
        import msvcrt
        get_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        count = get_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
        if count == 0 or count >= len(buffer):
            raise ArtifactError("INVALID_ARTIFACT", "Cannot verify opened file location")
        opened = buffer.value.removeprefix("\\\\?\\")
        if os.path.normcase(os.path.abspath(opened)) != os.path.normcase(str(expected)):
            raise ArtifactError("INVALID_ARTIFACT", "Opened file redirection rejected")

    def experiment(self, experiment_id):
        raw, source = self.read(experiment_id, "results.json")
        try:
            data = parse_json(raw)
            if not isinstance(data, dict) or data.get("experiment_id") != experiment_id:
                raise ValueError("Identity mismatch")
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise ArtifactError("INVALID_ARTIFACT", "Malformed JSON or mismatched experiment identity") from None
        return data, source

    def identifiers(self):
        try:
            root = self.checked_path()
            entries = []
            with os.scandir(root) as children:
                for child in children:
                    if len(entries) >= MAX_CHILDREN:
                        raise ArtifactError("INVALID_ARTIFACT", "Discovery directory limit exceeded")
                    entries.append(child)
            # Reparse entries are returned as invalid candidates, never followed.
            return sorted(e.name for e in entries if ID.fullmatch(e.name) and (e.is_dir(follow_symlinks=False) or is_redirection(e.stat(follow_symlinks=False))))
        except FileNotFoundError:
            raise ArtifactError("NOT_FOUND", "Artifact root unavailable") from None
        except OSError:
            raise ArtifactError("INVALID_ARTIFACT", "Cannot discover artifacts safely") from None

    def inventory(self, experiment_id):
        try:
            folder = self.checked_path(experiment_id)
            entries = list(folder.iterdir())
            if len(entries) > MAX_CHILDREN:
                raise ArtifactError("INVALID_ARTIFACT", "Artifact count limit exceeded")
            # No parsing or following metadata pointers; only known file types.
            return sorted(p.name for p in entries if SUPPORTED.fullmatch(p.name))
        except OSError:
            raise ArtifactError("INVALID_ARTIFACT", "Cannot inspect artifact inventory") from None
