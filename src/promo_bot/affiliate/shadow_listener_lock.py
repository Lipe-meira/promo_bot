"""Nonblocking process lock for one automatic shadow listener per database."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class ShadowListenerLock:
    def __init__(self, database_path: Path) -> None:
        resolved = database_path.expanduser().resolve()
        self.path = resolved.with_name(f"{resolved.name}.listener.lock")
        self._file: BinaryIO | None = None

    def __enter__(self) -> ShadowListenerLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("SHADOW_LISTENER_ALREADY_ACTIVE") from None
        self._file = handle
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        handle = self._file
        self._file = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
