"""At most one process tree, held in a Windows Job Object.

The job is what makes two guarantees true: stopping ends every process the program created, and if
the agent dies in any way (closed window, Task Manager, crash) Windows closes the job and ends the
tree, so a later start can never add a second copy to a forgotten one.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from typing import Any, Literal

if sys.platform != "win32":  # pragma: no cover - this agent is a Windows program
    raise ImportError("promo_agent runs on Windows only")

DEFAULT_COMMAND: tuple[str, ...] = (sys.executable, "-I", "-m", "promo_agent.harmless_process")
#: How long a process asked to stop may take to leave by itself before it is forced. The bot's
#: ordered stop can take about 45 s in its worst case.
STOP_GRACE_SECONDS = 60.0

_CREATE_SUSPENDED = 0x00000004
_KILL_ON_JOB_CLOSE = 0x00002000
_BASIC_ACCOUNTING = 1
_BASIC_PROCESS_ID_LIST = 3
_EXTENDED_LIMITS = 9


class _BasicLimits(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _IoCounters(ctypes.Structure):
    _fields_ = tuple(
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    )


class _ExtendedLimits(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class _Accounting(ctypes.Structure):
    _fields_ = (
        ("TotalUserTime", wintypes.LARGE_INTEGER),
        ("TotalKernelTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    )


class _ProcessIds(ctypes.Structure):
    _fields_ = (
        ("NumberOfAssignedProcesses", wintypes.DWORD),
        ("NumberOfProcessIdsInList", wintypes.DWORD),
        ("ProcessIdList", ctypes.c_size_t * 256),
    )


def _kernel32() -> Any:
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateJobObjectW.restype = wintypes.HANDLE
    dll.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    dll.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    dll.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    dll.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    dll.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    return dll


def _check(result: object) -> None:
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())


class _Job:
    def __init__(self) -> None:
        self._api = _kernel32()
        self._handle = self._api.CreateJobObjectW(None, None)  # never inherited by children
        _check(self._handle)
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        _check(
            self._api.SetInformationJobObject(
                self._handle, _EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits)
            )
        )

    def add(self, process_handle: int) -> None:
        _check(self._api.AssignProcessToJobObject(self._handle, process_handle))

    def active(self) -> int:
        info = _Accounting()
        _check(
            self._api.QueryInformationJobObject(
                self._handle, _BASIC_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None
            )
        )
        return int(info.ActiveProcesses)

    def pids(self) -> list[int]:
        info = _ProcessIds()
        _check(
            self._api.QueryInformationJobObject(
                self._handle, _BASIC_PROCESS_ID_LIST, ctypes.byref(info), ctypes.sizeof(info), None
            )
        )
        return [int(info.ProcessIdList[i]) for i in range(info.NumberOfProcessIdsInList)]

    def terminate(self) -> None:
        self._api.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        self._api.CloseHandle(self._handle)


class Supervisor:
    """Starts and stops one program. What it runs is fixed here, on the PC, never by a server."""

    def __init__(
        self,
        command: Sequence[str] = DEFAULT_COMMAND,
        grace_seconds: float = STOP_GRACE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._command = list(command)
        self._grace = grace_seconds
        self._clock = clock
        self._deadline: float | None = None  # set while a stop is under way
        #: True if the last stop had to end the tree by force.
        self.forced = False
        self._process: subprocess.Popen[bytes] | None = None
        self._job: _Job | None = None
        self._unexpected_exit = False

    @property
    def state(self) -> Literal["running", "stopped"]:
        """Looking at the state is also what notices an exit and enforces the stop deadline."""

        if self._job is not None:
            if self._job.active() == 0:
                if self._deadline is None:
                    self._unexpected_exit = True
                self._release()
            elif self._deadline is not None and self._clock() >= self._deadline:
                self.forced = True
                self._job.terminate()
        return "running" if self._job is not None else "stopped"

    @property
    def stopping(self) -> bool:
        return self.state == "running" and self._deadline is not None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def pids(self) -> list[int]:
        return self._job.pids() if self._job is not None else []

    def take_unexpected_exit(self) -> bool:
        """True once after the program ended without being asked to."""

        _ = self.state
        ended, self._unexpected_exit = self._unexpected_exit, False
        return ended

    def start(self) -> None:
        if self.state == "running":  # also while it is still leaving: never two
            return
        self._unexpected_exit = False
        self.forced = False
        job = _Job()
        process: subprocess.Popen[bytes] | None = None
        try:
            # Created suspended, so that it is inside the job before it can create anything.
            process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                    | _CREATE_SUSPENDED
                ),
            )
            handle = int(process._handle)  # type: ignore[attr-defined]
            job.add(handle)
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes = [wintypes.HANDLE]
            if resume(handle) != 0:
                raise OSError("could not resume the new process")
        except BaseException:
            if process is not None:
                process.kill()
                process.wait()
            job.close()
            raise
        self._process, self._job = process, job

    def request_stop(self) -> None:
        """Ask the process to end, without waiting: closing its input is the request.

        If it has not left when the deadline passes, the next look at `state` ends the whole tree.
        Asking again does not move the deadline.
        """

        if self.state != "running" or self._deadline is not None:
            return
        self.forced = False
        self._deadline = self._clock() + self._grace
        process = self._process
        try:
            if process is not None and process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass

    def stop(self) -> None:
        """Ask and wait: returns once nothing is left running."""

        self.request_stop()
        while self.state == "running":
            time.sleep(0.05)

    def _release(self) -> None:
        if self._process is not None:
            if self._process.stdin is not None and not self._process.stdin.closed:
                try:
                    self._process.stdin.close()
                except OSError:
                    pass
            self._process.poll()
        if self._job is not None:
            self._job.close()
        self._process = self._job = None
        self._deadline = None
