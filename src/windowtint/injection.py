from __future__ import annotations

import ctypes
import ntpath
import os
import sys
from pathlib import Path
from ctypes import wintypes

from .session import WindowTarget


PROCESS_CREATE_THREAD = 0x0002
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
SYNCHRONIZE = 0x00100000
TOKEN_QUERY = 0x0008

MEM_COMMIT = 0x00001000
MEM_RESERVE = 0x00002000
MEM_RELEASE = 0x00008000
PAGE_READWRITE = 0x04

WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_LONG_PATH = 32768
INJECTOR_RELEASE_NAME = "WindowTintInjector-0.4.15.dll"


class InjectionError(RuntimeError):
    pass


class _ModuleEntry32W(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    )


class _ApplyRequest(ctypes.Structure):
    _fields_ = (
        ("hwnd", wintypes.HWND),
        ("path", wintypes.WCHAR * MAX_LONG_PATH),
        ("owner_pid", wintypes.DWORD),
    )


class _RemoveRequest(ctypes.Structure):
    _fields_ = (("hwnd", wintypes.HWND),)


def default_injector_dll() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        adjacent = executable_dir / INJECTOR_RELEASE_NAME
        if adjacent.exists():
            return adjacent
        base = Path(getattr(sys, "_MEIPASS", executable_dir))
    else:
        base = Path(__file__).resolve().parents[2]
    release = base / "bin" / INJECTOR_RELEASE_NAME
    if release.exists():
        return release
    return base / "bin" / "WindowTintInjector.dll"


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.VirtualAllocEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualFreeEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
    )
    kernel32.VirtualFreeEx.restype = wintypes.BOOL
    kernel32.WriteProcessMemory.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    )
    kernel32.WriteProcessMemory.restype = wintypes.BOOL
    kernel32.CreateRemoteThread.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.CreateRemoteThread.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeThread.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeThread.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetProcAddress.argtypes = (wintypes.HMODULE, ctypes.c_char_p)
    kernel32.GetProcAddress.restype = ctypes.c_void_p
    kernel32.LoadLibraryW.argtypes = (wintypes.LPCWSTR,)
    kernel32.LoadLibraryW.restype = wintypes.HMODULE
    kernel32.FreeLibrary.argtypes = (wintypes.HMODULE,)
    kernel32.FreeLibrary.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Module32FirstW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Module32FirstW.restype = wintypes.BOOL
    kernel32.Module32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Module32NextW.restype = wintypes.BOOL
    return kernel32


class RemoteProcessBridge:
    def __init__(self) -> None:
        self._local_dll = None
        self._local_dll_path: Path | None = None
        self._local_base = 0
        self._export_offsets: dict[str, int] = {}

    def ensure_module(self, pid: int, dll_path: Path) -> int:
        self._local_dll_path = Path(dll_path)
        expected_path = os.path.normcase(os.path.abspath(dll_path))
        expected_name = ntpath.basename(str(dll_path)).casefold()
        existing = None
        for module_name, module_path, module_base in self._remote_module_entries(pid):
            if not self._is_injector_module(module_name, module_path):
                continue
            same_path = (
                bool(module_path)
                and os.path.normcase(os.path.abspath(module_path)) == expected_path
            )
            if module_name.casefold() == expected_name or same_path:
                existing = module_base
                continue
            raise InjectionError(
                f"目标进程已加载不同版本的 {module_name}。"
                "请完全退出并重启目标程序后再试；"
                "不能在同一进程中叠加多个 WindowTint 注入器。"
            )
        if existing is not None:
            return existing

        path_buffer = ctypes.create_unicode_buffer(str(dll_path))
        load_address = self._remote_procedure("kernel32.dll", b"LoadLibraryW")
        self._call_remote(
            pid,
            load_address,
            ctypes.string_at(path_buffer, ctypes.sizeof(path_buffer)),
            timeout_ms=20_000,
        )
        loaded = self._find_remote_module(pid, dll_path)
        if loaded is None:
            raise InjectionError("目标进程没有载入 WindowTintInjector.dll")
        return loaded

    def call_apply(
        self,
        pid: int,
        module_base: int,
        hwnd: int,
        backdrop_path: Path,
    ) -> int:
        address = self._remote_export(module_base, b"ApplyRemote")
        request = _ApplyRequest()
        request.hwnd = hwnd
        request.path = str(backdrop_path)
        request.owner_pid = os.getpid()
        return self._call_remote(pid, address, bytes(request))

    def call_apply_painted(
        self,
        pid: int,
        module_base: int,
        hwnd: int,
        backdrop_path: Path,
    ) -> int:
        address = self._remote_export(module_base, b"ApplyPaintedRemote")
        request = _ApplyRequest()
        request.hwnd = hwnd
        request.path = str(backdrop_path)
        request.owner_pid = os.getpid()
        return self._call_remote(pid, address, bytes(request))

    def call_remove(self, pid: int, module_base: int, hwnd: int) -> None:
        address = self._remote_export(module_base, b"RemoveRemote")
        request = _RemoveRequest()
        request.hwnd = hwnd
        self._call_remote(pid, address, bytes(request))

    def _remote_procedure(self, module_name: str, export_name: bytes) -> int:
        kernel32 = _kernel32()
        module = kernel32.GetModuleHandleW(module_name)
        if not module:
            raise InjectionError(f"无法找到 {module_name}")
        address = kernel32.GetProcAddress(module, export_name)
        if not address:
            raise InjectionError(f"无法找到 {module_name}!{export_name.decode()}")
        return int(address)

    def _local_export_offset(self, export_name: bytes) -> int:
        kernel32 = _kernel32()
        if not self._local_dll:
            dll_path = self._local_dll_path or default_injector_dll()
            if not dll_path.exists():
                raise InjectionError(f"injector DLL 不存在：{dll_path}")
            self._local_dll = kernel32.LoadLibraryW(str(dll_path))
            if not self._local_dll:
                raise InjectionError("无法在本进程载入 injector DLL")
            self._local_base = int(self._local_dll)
        key = export_name.decode("ascii")
        if key not in self._export_offsets:
            address = kernel32.GetProcAddress(self._local_dll, export_name)
            if not address:
                raise InjectionError(f"injector DLL 缺少导出：{key}")
            self._export_offsets[key] = int(address) - self._local_base
        return self._export_offsets[key]

    def _remote_export(self, module_base: int, export_name: bytes) -> int:
        return module_base + self._local_export_offset(export_name)

    def _find_remote_module(self, pid: int, dll_path: Path) -> int | None:
        expected = os.path.normcase(os.path.abspath(dll_path))
        expected_name = ntpath.basename(str(dll_path)).casefold()
        for module_name, module_path, module_base in self._remote_module_entries(pid):
            same_path = (
                bool(module_path)
                and os.path.normcase(os.path.abspath(module_path)) == expected
            )
            if module_name.casefold() == expected_name or same_path:
                return module_base
        return None

    def _remote_module_entries(self, pid: int) -> list[tuple[str, str, int]]:
        kernel32 = _kernel32()
        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32,
            pid,
        )
        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            return []
        modules = []
        try:
            entry = _ModuleEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
                return []
            while True:
                modules.append(
                    (
                        entry.szModule,
                        entry.szExePath,
                        int(entry.modBaseAddr or 0),
                    )
                )
                if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                    return modules
        finally:
            kernel32.CloseHandle(snapshot)

    @staticmethod
    def _is_injector_module(module_name: str, module_path: str) -> bool:
        name = module_name or ntpath.basename(module_path)
        folded = name.casefold()
        return folded.startswith("windowtintinjector") and folded.endswith(".dll")

    def _call_remote(
        self,
        pid: int,
        address: int,
        payload: bytes,
        *,
        timeout_ms: int = 10_000,
    ) -> int:
        kernel32 = _kernel32()
        access = (
            PROCESS_CREATE_THREAD
            | PROCESS_VM_OPERATION
            | PROCESS_VM_WRITE
            | PROCESS_VM_READ
            | PROCESS_QUERY_INFORMATION
            | SYNCHRONIZE
        )
        process = kernel32.OpenProcess(access, False, pid)
        if not process:
            raise InjectionError(
                f"无法打开目标进程，错误码 {ctypes.get_last_error()}"
            )
        remote_memory = None
        thread = None
        try:
            remote_memory = kernel32.VirtualAllocEx(
                process,
                None,
                len(payload),
                MEM_COMMIT | MEM_RESERVE,
                PAGE_READWRITE,
            )
            if not remote_memory:
                raise InjectionError(
                    f"目标进程内存分配失败，错误码 {ctypes.get_last_error()}"
                )
            written = ctypes.c_size_t()
            buffer = ctypes.create_string_buffer(payload)
            if not kernel32.WriteProcessMemory(
                process,
                remote_memory,
                buffer,
                len(payload),
                ctypes.byref(written),
            ):
                raise InjectionError(
                    f"写入目标进程失败，错误码 {ctypes.get_last_error()}"
                )
            thread_id = wintypes.DWORD()
            thread = kernel32.CreateRemoteThread(
                process,
                None,
                0,
                ctypes.c_void_p(address),
                remote_memory,
                0,
                ctypes.byref(thread_id),
            )
            if not thread:
                raise InjectionError(
                    f"创建远程线程失败，错误码 {ctypes.get_last_error()}"
                )
            wait = kernel32.WaitForSingleObject(thread, timeout_ms)
            if wait == WAIT_TIMEOUT:
                raise InjectionError("目标进程响应超时")
            if wait != WAIT_OBJECT_0:
                raise InjectionError(
                    f"等待目标进程失败，错误码 {ctypes.get_last_error()}"
                )
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code)):
                raise InjectionError(
                    f"读取远程调用结果失败，错误码 {ctypes.get_last_error()}"
                )
            return int(exit_code.value)
        finally:
            if thread:
                kernel32.CloseHandle(thread)
            if remote_memory:
                kernel32.VirtualFreeEx(process, remote_memory, 0, MEM_RELEASE)
            kernel32.CloseHandle(process)


class InjectionManager:
    def __init__(self, dll_path: Path | None = None, *, bridge=None):
        self.dll_path = Path(dll_path) if dll_path is not None else default_injector_dll()
        self._bridge = bridge if bridge is not None else RemoteProcessBridge()
        self._modules: dict[int, int] = {}
        self._targets: dict[int, int] = {}

    @property
    def active_handles(self) -> set[int]:
        return set(self._targets)

    def apply(self, target: WindowTarget, backdrop_path: Path) -> None:
        if not self.dll_path.exists():
            raise InjectionError(f"injector DLL 不存在：{self.dll_path}")
        if not Path(backdrop_path).exists():
            raise InjectionError(f"背景图片不存在：{backdrop_path}")
        module_base = self._modules.get(target.pid)
        if module_base is None:
            module_base = self._bridge.ensure_module(target.pid, self.dll_path)
            self._modules[target.pid] = module_base
        result = self._bridge.call_apply(
            target.pid,
            module_base,
            target.hwnd,
            Path(backdrop_path),
        )
        if result == 0:
            raise InjectionError("目标进程拒绝应用背景层")
        self._targets[target.hwnd] = target.pid

    def apply_painted(self, target: WindowTarget, backdrop_path: Path) -> None:
        if not self.dll_path.exists():
            raise InjectionError(f"injector DLL 不存在：{self.dll_path}")
        if not Path(backdrop_path).exists():
            raise InjectionError(f"背景图片不存在：{backdrop_path}")
        module_base = self._modules.get(target.pid)
        if module_base is None:
            module_base = self._bridge.ensure_module(target.pid, self.dll_path)
            self._modules[target.pid] = module_base
        result = self._bridge.call_apply_painted(
            target.pid,
            module_base,
            target.hwnd,
            Path(backdrop_path),
        )
        if result == 0:
            raise InjectionError("目标进程拒绝应用绘制背景")
        self._targets[target.hwnd] = target.pid

    def remove(self, target: WindowTarget) -> None:
        pid = self._targets.get(target.hwnd)
        if pid is None:
            return
        module_base = self._modules.get(pid)
        if module_base is None:
            self._targets.pop(target.hwnd, None)
            return
        self._bridge.call_remove(pid, module_base, target.hwnd)
        self._targets.pop(target.hwnd, None)
