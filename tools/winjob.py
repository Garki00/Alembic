# tools/winjob.py
"""Windows Job Object helper for terminating a command tree.

``taskkill /T /F`` is not always available in constrained environments and can
take several seconds to give up. A job object with ``KILL_ON_JOB_CLOSE`` gives
the runtime a fast, reliable way to stop the whole process tree, including
children started by PowerShell or cmd.
"""

from __future__ import annotations

import os

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class WindowsJob:
    """A kill-on-close job object; no-op on non-Windows platforms."""

    def __init__(self, handle: int | None = None) -> None:
        self.handle = handle
        self._closed = False

    @classmethod
    def create(cls) -> "WindowsJob | None":
        if os.name != "nt":
            return None
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            _kernel32.CloseHandle(handle)
            return None
        return cls(handle)

    def assign(self, popen) -> bool:
        if os.name != "nt" or not self.handle:
            return False
        try:
            process_handle = int(popen._handle)  # type: ignore[attr-defined]
        except (AttributeError, TypeError, ValueError):
            return False
        return self.assign_handle(process_handle)

    def assign_handle(self, process_handle: int) -> bool:
        if os.name != "nt" or not self.handle:
            return False
        return bool(_kernel32.AssignProcessToJobObject(self.handle, process_handle))

    def terminate(self) -> None:
        if os.name != "nt" or not self.handle or self._closed:
            return
        try:
            _kernel32.TerminateJobObject(self.handle, 1)
        except OSError:
            pass

    def close(self) -> None:
        if os.name != "nt" or not self.handle or self._closed:
            return
        self._closed = True
        try:
            _kernel32.CloseHandle(self.handle)
        except OSError:
            pass
        self.handle = None
