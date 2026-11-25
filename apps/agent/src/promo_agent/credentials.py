"""The pairing kept on this PC. The token is encrypted with Windows DPAPI for the current user:
copying the file to another account or machine does not copy the access."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform != "win32":  # pragma: no cover - this agent is a Windows program
    raise ImportError("promo_agent runs on Windows only")

_FILE = "pairing.json"
_UI_FORBIDDEN = 0x1


@dataclass(frozen=True)
class Pairing:
    api_url: str
    workspace_id: str
    token: str = field(repr=False)


class _Blob(ctypes.Structure):
    _fields_ = (("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char)))


def _dpapi(function: str, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    done = getattr(crypt32, function)(
        ctypes.byref(source), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(target)
    )
    if not done:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def save(state_dir: Path, pairing: Pairing) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    protected = _dpapi("CryptProtectData", pairing.token.encode())
    document = {
        "api_url": pairing.api_url,
        "workspace_id": pairing.workspace_id,
        "token_protected": base64.b64encode(protected).decode(),
    }
    temporary = state_dir / f"{_FILE}.tmp"
    temporary.write_text(json.dumps(document), encoding="utf-8")
    os.replace(temporary, state_dir / _FILE)


def load(state_dir: Path) -> Pairing | None:
    try:
        document = json.loads((state_dir / _FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    token = _dpapi("CryptUnprotectData", base64.b64decode(document["token_protected"]))
    return Pairing(document["api_url"], document["workspace_id"], token.decode())


def forget(state_dir: Path) -> None:
    (state_dir / _FILE).unlink(missing_ok=True)
