from __future__ import annotations

import os
import winreg
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


OFFICIAL_PROJECT_URL = "https://github.com/namazso/SecureUxTheme"
OFFICIAL_RELEASE_URL = "https://github.com/namazso/SecureUxTheme/releases/latest"
LICENSE_URL = "https://github.com/namazso/SecureUxTheme/blob/master/COPYING"
PERSONALIZATION_URI = "ms-settings:personalization"

_IFEO_KEY = (
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    r"\Image File Execution Options\winlogon.exe"
)
_VERIFIER_DLL_NAME = "secureuxtheme4.dll"
_GLOBAL_FLAG_VERIFIER = 0x100


class InstallationState(Enum):
    MISSING = "missing"
    PARTIAL = "partial"
    INSTALLED = "installed"


@dataclass(frozen=True)
class InstallationProbe:
    system32_dll: bool = False
    syswow64_dll: bool = False
    system32_proxy: bool = False
    syswow64_proxy: bool = False
    verifier_dlls: str = ""
    global_flag: int = 0

    @property
    def has_any_dll(self) -> bool:
        return self.system32_dll or self.syswow64_dll

    @property
    def has_any_proxy(self) -> bool:
        return self.system32_proxy or self.syswow64_proxy

    @property
    def verifier_enabled(self) -> bool:
        names = {
            name.strip().casefold()
            for name in self.verifier_dlls.replace(";", ",").split(",")
            if name.strip()
        }
        return _VERIFIER_DLL_NAME in names and bool(
            self.global_flag & _GLOBAL_FLAG_VERIFIER
        )


@dataclass(frozen=True)
class InstallationStatus:
    state: InstallationState
    title: str
    detail: str

    @property
    def installed(self) -> bool:
        return self.state is InstallationState.INSTALLED


def classify_installation(probe: InstallationProbe) -> InstallationStatus:
    if not probe.has_any_dll:
        return InstallationStatus(
            InstallationState.MISSING,
            "未检测到 SecureUxTheme",
            "高阶系统主题保持关闭。WindowTint 不会自动下载或安装。",
        )
    if not probe.has_any_proxy:
        return InstallationStatus(
            InstallationState.PARTIAL,
            "检测到不完整的安装",
            "SecureUxTheme DLL 存在，但 ThemeUiProxy 缺失。建议重新运行官方安装器。",
        )
    if not probe.verifier_enabled:
        return InstallationStatus(
            InstallationState.PARTIAL,
            "组件已放置，但尚未启用",
            "winlogon 的 VerifierDlls/GlobalFlag 尚未启用。可能尚未重启或安装未完成。",
        )
    return InstallationStatus(
        InstallationState.INSTALLED,
        "SecureUxTheme 已安装",
        "系统级主题补丁已配置。第三方 .msstyles 主题仍需你自行选择并确认兼容性。",
    )


def _read_ifeo_config() -> tuple[str, int]:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            _IFEO_KEY,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            try:
                verifier_dlls = str(winreg.QueryValueEx(key, "VerifierDlls")[0])
            except OSError:
                verifier_dlls = ""
            try:
                global_flag = int(winreg.QueryValueEx(key, "GlobalFlag")[0])
            except (OSError, ValueError, TypeError):
                global_flag = 0
            return verifier_dlls, global_flag
    except OSError:
        return "", 0


def probe_system(system_root: Path | None = None) -> InstallationProbe:
    root = (
        Path(system_root)
        if system_root is not None
        else Path(os.environ.get("SystemRoot", r"C:\Windows"))
    )
    system32 = root / "System32"
    syswow64 = root / "SysWOW64"
    verifier_dlls, global_flag = _read_ifeo_config()
    return InstallationProbe(
        system32_dll=(system32 / "SecureUxTheme4.dll").exists(),
        syswow64_dll=(syswow64 / "SecureUxTheme4.dll").exists(),
        system32_proxy=(system32 / "ThemeUiProxy.dll").exists(),
        syswow64_proxy=(syswow64 / "ThemeUiProxy.dll").exists(),
        verifier_dlls=verifier_dlls,
        global_flag=global_flag,
    )


def detect_installation(system_root: Path | None = None) -> InstallationStatus:
    return classify_installation(probe_system(system_root))
