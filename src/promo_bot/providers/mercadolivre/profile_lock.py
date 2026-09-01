"""Exclusive browser profile lock; the OS releases it when the process exits."""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class BrowserProfileInUse(RuntimeError):
    pass


def ensure_profile_outside_workspace(profile_dir: Path, workspace: Path) -> Path:
    profile = profile_dir.expanduser().resolve()
    root = workspace.expanduser().resolve()
    if profile == root or profile.is_relative_to(root):
        raise ValueError("browser profile must be outside the repository workspace")
    return profile


class BrowserProfileLock:
    """Hold a non-blocking one-byte lock adjacent to the persistent profile."""

    def __init__(self, profile_dir: Path) -> None:
        self.profile_dir = profile_dir.expanduser().resolve()
        self.lock_path = self.profile_dir.parent / f".{self.profile_dir.name}.lock"
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("browser profile lock is already held by this instance")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            _lock_file(handle)
        except OSError as exc:
            handle.close()
            raise BrowserProfileInUse("browser profile is already locked") from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock_file(handle)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> BrowserProfileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


if os.name == "nt":
    import msvcrt

    def _lock_file(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(handle: BinaryIO) -> None:
        fcntl.flock(  # type: ignore[attr-defined]
            handle.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
        )

    def _unlock_file(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
