# tools/sandbox/windows.py
"""Windows user-isolated command backend.

Each command runs as a dedicated local user with a fresh logon token, inside a
Job Object, with an ACL-scoped workspace and a per-user firewall. There is no
path that executes a model command as the desktop user.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Mapping

from config.settings import settings
from tools.sandbox.base import (
    SandboxError,
    SandboxPolicy,
    SandboxProcess,
    SandboxStatus,
    sandbox_unavailable,
)
from tools.sandbox.config import SandboxConfigStore
from tools.sandbox.proxy import SandboxProxy
from tools.winjob import WindowsJob

_IS_WINDOWS = os.name == "nt"
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_LOGON_WITH_PROFILE = 0x00000001
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_HANDLE_FLAG_INHERIT = 0x1

if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

    class _STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("lpReserved", wt.LPWSTR),
            ("lpDesktop", wt.LPWSTR),
            ("lpTitle", wt.LPWSTR),
            ("dwX", wt.DWORD),
            ("dwY", wt.DWORD),
            ("dwXSize", wt.DWORD),
            ("dwYSize", wt.DWORD),
            ("dwXCountChars", wt.DWORD),
            ("dwYCountChars", wt.DWORD),
            ("dwFillAttribute", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("wShowWindow", wt.WORD),
            ("cbReserved2", wt.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wt.HANDLE),
            ("hStdOutput", wt.HANDLE),
            ("hStdError", wt.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wt.HANDLE),
            ("hThread", wt.HANDLE),
            ("dwProcessId", wt.DWORD),
            ("dwThreadId", wt.DWORD),
        ]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wt.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wt.BOOL),
        ]

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.c_void_p)]

    _advapi32.CreateProcessWithLogonW.argtypes = [
        wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        wt.LPCWSTR, wt.LPWSTR, wt.DWORD, ctypes.c_void_p,
        wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _advapi32.CreateProcessWithLogonW.restype = wt.BOOL
    _kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
        ctypes.POINTER(_SECURITY_ATTRIBUTES), wt.DWORD,
    ]
    _kernel32.CreatePipe.restype = wt.BOOL
    _kernel32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
    _kernel32.SetHandleInformation.restype = wt.BOOL
    _kernel32.CloseHandle.argtypes = [wt.HANDLE]
    _kernel32.CloseHandle.restype = wt.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _kernel32.WaitForSingleObject.restype = wt.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wt.BOOL
    _kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    _kernel32.TerminateProcess.restype = wt.BOOL
    _kernel32.CreateFileW.argtypes = [
        wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
        wt.DWORD, wt.DWORD, wt.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wt.HANDLE
    _kernel32.GetFileAttributesW.argtypes = [wt.LPCWSTR]
    _kernel32.GetFileAttributesW.restype = wt.DWORD
    _kernel32.GetDriveTypeW.argtypes = [wt.LPCWSTR]
    _kernel32.GetDriveTypeW.restype = wt.UINT
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wt.LPWSTR),
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wt.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wt.BOOL


def _dpapi_unprotect(value: str) -> str:
    if not _IS_WINDOWS:
        raise sandbox_unavailable("DPAPI is only available on Windows.")
    raw = base64.b64decode(value)
    buffer = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buffer, ctypes.c_void_p))
    blob_out = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise sandbox_unavailable("Could not decrypt the sandbox account password; rerun setup-sandbox.ps1.")
    try:
        data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return data.decode("utf-8")
    finally:
        _kernel32.LocalFree(blob_out.pbData)


def _make_env_block(env: Mapping[str, str]) -> ctypes.Array:
    text = "".join(f"{key}={value}\0" for key, value in env.items()) + "\0"
    return ctypes.create_unicode_buffer(text)


def _create_pipe(inherit_write: bool) -> tuple[wt.HANDLE, wt.HANDLE]:
    sa = _SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(sa)
    sa.bInheritHandle = True
    read = wt.HANDLE()
    write = wt.HANDLE()
    if not _kernel32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(sa), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    _kernel32.SetHandleInformation(read, _HANDLE_FLAG_INHERIT, 0)
    if not inherit_write:
        _kernel32.SetHandleInformation(write, _HANDLE_FLAG_INHERIT, 0)
    return read, write


def _handle_value(handle) -> int:
    """Return a ctypes HANDLE/c_void_p (or plain int) as a Python int."""
    if isinstance(handle, ctypes.c_void_p):
        return int(handle.value or 0)
    return int(handle or 0)


def _fd(handle) -> int:
    # msvcrt.open_osfhandle() accepts a C int and overflows when Windows
    # returns a handle above 2^31. Call _open_osfhandle with intptr_t instead.
    function = ctypes.CDLL("ucrtbase", use_errno=True)._open_osfhandle
    function.argtypes = [ctypes.c_void_p, ctypes.c_int]
    function.restype = ctypes.c_int
    fd = function(ctypes.c_void_p(_handle_value(handle)), os.O_RDONLY | os.O_BINARY)
    if fd == -1:
        raise OSError(ctypes.get_errno(), "Could not convert sandbox pipe to a file descriptor")
    return fd


def _null_handle() -> int:
    sa = _SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(sa)
    sa.bInheritHandle = True
    handle = _kernel32.CreateFileW(
        "NUL", _GENERIC_READ, 0, ctypes.byref(sa), _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL, None,
    )
    return _handle_value(handle)


class _WindowsSandboxProcess:
    def __init__(
        self,
        *,
        process_handle: int,
        pid: int,
        stdout,
        stderr,
        job: WindowsJob | None,
        stdout_path: Path | None = None,
    ) -> None:
        self._handle = int(process_handle)
        self._pid = int(pid)
        self.stdout = stdout
        self.stderr = stderr
        self._job = job
        self._exit_code: int | None = None
        self._closed = False
        self._stdout_path = stdout_path

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        if self._exit_code is not None:
            return self._exit_code
        code = wt.DWORD(0)
        if _kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            if code.value != 259:  # STILL_ACTIVE
                self._exit_code = int(code.value)
                return self._exit_code
        return None

    def wait(self, timeout: float | None = None) -> int:
        if self._exit_code is not None:
            return self._exit_code
        wait_ms = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        result = _kernel32.WaitForSingleObject(self._handle, wait_ms)
        if result == _WAIT_TIMEOUT:
            raise TimeoutError("process still running")
        return self.poll() or 0

    def terminate(self, timeout: float = 5.0) -> None:
        if self._job is not None:
            self._job.terminate()
        else:
            _kernel32.TerminateProcess(self._handle, 1)
        try:
            self.wait(timeout)
        except TimeoutError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            self._job.close()
            self._job = None
        for stream in (self.stdout, self.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        try:
            _kernel32.CloseHandle(self._handle)
        except OSError:
            pass


class WindowsSandbox:
    """Production Windows sandbox backend."""

    def __init__(self, *, config: SandboxConfigStore | None = None) -> None:
        self.config = config or SandboxConfigStore()
        self._prepared: dict[str, int] = {}
        self._lock = threading.Lock()
        self._proxy: SandboxProxy | None = None
        self._users_path = self.config.path.parent / "sandbox-users.json"
        self._setup_path = self.config.path.parent / "sandbox-setup.json"
        self._users: dict[str, dict[str, str]] = {}
        self._load_users()

    def _load_users(self) -> None:
        try:
            data = json.loads(self._users_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                self._users = {
                    key: value for key, value in data.items()
                    if isinstance(value, dict) and value.get("username") and value.get("password")
                }
        except (OSError, ValueError):
            self._users = {}

    def _setup_ready(self) -> bool:
        try:
            data = json.loads(self._setup_path.read_text(encoding="utf-8-sig"))
            if not bool(data.get("ready")):
                return False
            # Version 2 removed the online account's firewall rules. Refuse an
            # old setup marker so an upgraded install cannot silently keep the
            # previous allowlist firewall.
            return int(data.get("version") or 1) >= 2
        except (OSError, ValueError, AttributeError, TypeError):
            return False

    def status(self) -> SandboxStatus:
        if not _IS_WINDOWS:
            return SandboxStatus(
                False, "windows", sys.platform,
                "当前平台没有原生 Windows 沙盒后端。",
                reasons=("unsupported_platform",),
            )
        self._load_users()
        reasons: list[str] = []
        if not self._users:
            reasons.append("sandbox_users_missing")
        if not self._setup_ready():
            reasons.append("sandbox_setup_incomplete")
        return SandboxStatus(
            not reasons,
            "windows-local-user",
            "win32",
            "专用本地用户 + ACL + Job Object；在线模式仅注入代理环境变量",
            allowed_domains=tuple(self.config.get()["allowed_domains"]),
            reasons=tuple(reasons),
        )

    def _ensure_proxy(self, *, allow_all: bool) -> str:
        cfg = self.config.get()
        if self._proxy is None or self._proxy.allow_all != allow_all:
            if self._proxy is not None:
                self._proxy.close()
            self._proxy = SandboxProxy(
                cfg["proxy_port"],
                cfg["allowed_domains"],
                set(cfg["allowed_ports"]),
                allow_all=allow_all,
            )
            self._proxy.start()
        return self._proxy.url

    def prepare_workspace(self, policy: SandboxPolicy) -> None:
        if not self.status().ready:
            raise sandbox_unavailable(
                "Sandbox is not initialized. Run scripts/setup-sandbox.ps1 as administrator.",
                hint="setup-sandbox.ps1 -Verify",
            )
        root = policy.workspace.resolve()
        if not root.is_dir():
            raise sandbox_unavailable(f"Workspace does not exist: {root}")
        if not self._is_local_fixed(root):
            raise sandbox_unavailable("The sandbox supports only NTFS workspaces on local fixed disks.")
        key = str(root)
        mtime = 0
        try:
            mtime = root.stat().st_mtime_ns
        except OSError:
            pass
        with self._lock:
            prepared = self._prepared.get(key) == mtime
        users = [item["username"] for item in self._users.values()]
        try:
            if not prepared:
                for user in users:
                    self._icacls(root, "/grant", f"{user}:(OI)(CI)M")
                    self._grant_traverse(root, user)
                    self._grant_toolchain_reads(root, user)
                    self._deny_credentials(user)
            # Protected files can appear between commands; rescan every spawn.
            self._scan_workspace_denies(root, users)
        except SandboxError:
            raise
        except Exception as exc:
            raise sandbox_unavailable(f"Could not set workspace ACLs: {exc}") from exc
        with self._lock:
            self._prepared[key] = mtime

    def _is_local_fixed(self, path: Path) -> bool:
        try:
            drive = os.path.splitdrive(str(path))[0]
            if drive and _kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive + "\\")) != 3:
                return False
            current = path.resolve()
            while True:
                attrs = _kernel32.GetFileAttributesW(str(current))
                if attrs == 0xFFFFFFFF:
                    return False
                if attrs & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                    return False
                if current == current.parent:
                    break
                current = current.parent
            return True
        except Exception:
            return False

    def _icacls(self, path: Path, *args: str, ignore_errors: bool = False) -> None:
        if not path.exists():
            return
        command = ["icacls", str(path), *args, "/Q", "/C"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            if ignore_errors:
                return
            raise SandboxError("sandbox_acl_failed", f"icacls failed: {exc}") from exc
        if result.returncode != 0 and not ignore_errors:
            # icacls may return non-zero when the ACL already exists; fail only when the output clearly indicates an error.
            text = (result.stdout + result.stderr).strip()
            if "已成功处理" not in text and "Successfully processed" not in text and text:
                raise SandboxError("sandbox_acl_failed", text[:300])

    def _grant_traverse(self, workspace: Path, user: str) -> None:
        current = workspace.parent
        while current != current.parent:
            self._icacls(current, "/grant", f"{user}:(X)", ignore_errors=True)
            current = current.parent

    def _grant_toolchain_reads(self, workspace: Path, user: str) -> None:
        """Grant read/execute on user-installed toolchains used by PATH.

        The sandbox user has its own profile; interpreters and CLIs installed in
        the desktop user's profile would otherwise be unreadable. Only PATH
        entries below the user's home are granted, and explicit credential
        denies are applied afterwards.
        """
        from tools.runtimes import runtime_paths, runtime_root

        if runtime_paths():
            bundle = runtime_root().resolve()
            self._grant_traverse(bundle, user)
            self._icacls(bundle, "/grant", f"{user}:(OI)(CI)(RX)")
        home = Path.home().resolve()
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            path = Path(entry).expanduser()
            try:
                resolved = path.resolve()
                resolved.relative_to(home)
            except (OSError, ValueError):
                continue
            if resolved == home or not resolved.is_dir():
                continue
            # Never grant the workspace twice; it already has Modify.
            try:
                resolved.relative_to(workspace)
                continue
            except ValueError:
                pass
            self._icacls(resolved, "/grant", f"{user}:(OI)(CI)(RX)", ignore_errors=True)

    def _deny_credentials(self, user: str) -> None:
        home = Path.home()
        paths = [
            Path(settings.app_home),
            home / ".ssh", home / ".aws", home / ".kube", home / ".gnupg", home / ".docker",
            home / ".netrc", home / ".npmrc", home / ".pypirc",
            home / ".config", home / ".cache",
        ]
        for path in paths:
            if path.exists():
                try:
                    self._icacls(path, "/deny", f"{user}:(OI)(CI)(RX)")
                except SandboxError:
                    # Continue when the credential directory is missing or protected by another policy; do not turn the failure into a write leak.
                    continue

    def _scan_workspace_denies(self, root: Path, users: list[str]) -> None:
        skip = {".git", "node_modules", ".venv", "dist", "build", ".tmp", ".alembic"}
        for current, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name not in skip]
            for name in files:
                lower = name.lower()
                protected = (
                    lower == ".env"
                    or (lower.startswith(".env.") and not lower.endswith((".example", ".sample", ".template", ".dist")))
                    or lower.endswith((".pem", ".key", ".p12", ".pfx"))
                )
                if not protected:
                    continue
                for user in users:
                    try:
                        self._icacls(Path(current) / name, "/deny", f"{user}:(R)")
                    except SandboxError:
                        continue

    def spawn(
        self,
        *,
        command: str,
        cwd: Path,
        env: Mapping[str, str],
        policy: SandboxPolicy,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
        stdin: bool = False,
        tty: bool = False,
        shell: bool = False,
    ) -> SandboxProcess:
        if not _IS_WINDOWS:
            raise sandbox_unavailable("The Windows sandbox backend is not available on this platform.")
        status = self.status()
        if not status.ready:
            raise sandbox_unavailable(
                "Sandbox unavailable: " + ", ".join(status.reasons),
                hint="Run scripts/setup-sandbox.ps1 as administrator.",
            )
        self.prepare_workspace(policy)
        network = policy.normalized_network()
        mode = "online" if network == "allow" else "offline"
        account = self._users.get(mode)
        if not account:
            raise sandbox_unavailable(f"Missing {mode} sandbox account; rerun setup-sandbox.ps1.")
        if shell:
            comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
            command = f'"{comspec}" /d /s /c "{command}"'
        password = _dpapi_unprotect(account["password"])
        profile = Path(account.get("profile", f"C:\\Users\\{account['username']}"))
        if mode == "online":
            # The proxy is advisory in allow mode: the child inherits the proxy
            # variables, but the online account has no firewall restriction and
            # may connect directly.
            proxy_url = self._ensure_proxy(allow_all=True)
            env = dict(env)
            env.update({
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "ALL_PROXY": proxy_url,
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "all_proxy": proxy_url,
                "NO_PROXY": "",
                "no_proxy": "",
            })
        env = dict(env)
        env.update({
            "USERNAME": account["username"],
            "USERDOMAIN": os.environ.get("COMPUTERNAME", "."),
            "USERPROFILE": str(profile),
            "APPDATA": str(profile / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(profile / "AppData" / "Local"),
            "TEMP": str(profile / "AppData" / "Local" / "Temp"),
            "TMP": str(profile / "AppData" / "Local" / "Temp"),
            "HOME": str(profile),
            "HOMEDRIVE": profile.drive,
            "HOMEPATH": str(profile)[len(profile.drive):],
        })
        stdout_handle = stderr_handle = stdin_handle = 0
        stdout_stream = stderr_stream = None
        try:
            stdout_handle, stdout_write = _create_pipe(True)
            stderr_handle, stderr_write = _create_pipe(True)
            stdin_handle = _null_handle()
            si = _STARTUPINFO()
            si.cb = ctypes.sizeof(si)
            si.dwFlags = _STARTF_USESTDHANDLES
            si.lpDesktop = None
            si.hStdInput = stdin_handle
            si.hStdOutput = stdout_write
            si.hStdError = stderr_write
            env_block = _make_env_block(env)
            pi = _PROCESS_INFORMATION()
            cmdline = ctypes.create_unicode_buffer(command)
            ok = _advapi32.CreateProcessWithLogonW(
                account["username"], ".", password, _LOGON_WITH_PROFILE,
                None, cmdline, _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW,
                ctypes.cast(env_block, ctypes.c_void_p), ctypes.c_wchar_p(str(cwd)),
                ctypes.byref(si), ctypes.byref(pi),
            )
            _kernel32.CloseHandle(stdout_write)
            _kernel32.CloseHandle(stderr_write)
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            _kernel32.CloseHandle(pi.hThread)
            job = WindowsJob.create()
            if job is None or not job.assign_handle(_handle_value(pi.hProcess)):
                if job is not None:
                    job.close()
                _kernel32.TerminateProcess(pi.hProcess, 1)
                raise SandboxError("sandbox_job_failed", "Could not add the process to a Job Object.")
            stdout_stream = os.fdopen(_fd(stdout_handle), "rb", buffering=0)
            stderr_stream = os.fdopen(_fd(stderr_handle), "rb", buffering=0)
            if stdin_handle:
                _kernel32.CloseHandle(stdin_handle)
                stdin_handle = 0
            return _WindowsSandboxProcess(
                process_handle=_handle_value(pi.hProcess),
                pid=int(pi.dwProcessId),
                stdout=stdout_stream,
                stderr=stderr_stream,
                job=job,
                stdout_path=stdout_path,
            )
        except Exception:
            for handle in (stdout_handle, stderr_handle, stdin_handle):
                if handle:
                    try:
                        _kernel32.CloseHandle(handle)
                    except OSError:
                        pass
            raise

    def close(self) -> None:
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None
