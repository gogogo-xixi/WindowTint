from __future__ import annotations


WS_CAPTION = 0x00C00000

SYSTEM_PROCESSES = {
    "applicationframehost.exe",
    "audiodg.exe",
    "csrss.exe",
    "ctfmon.exe",
    "dwm.exe",
    "explorer.exe",
    "fontdrvhost.exe",
    "lsass.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "securityhealthservice.exe",
    "services.exe",
    "shellexperiencehost.exe",
    "sihost.exe",
    "smss.exe",
    "startmenuexperiencehost.exe",
    "svchost.exe",
    "taskhostw.exe",
    "wininit.exe",
    "winlogon.exe",
}

SECURITY_PROCESSES = {
    "360tray.exe",
    "hipstray.exe",
    "huorong.exe",
    "msmpeng.exe",
    "nissrv.exe",
    "qqpctray.exe",
    "usysdiag.exe",
}

ANTCHEAT_MARKERS = (
    "ace-guard",
    "aceguard",
    "anticheat",
    "battleye",
    "beservice",
    "easyanticheat",
    "gameguard",
    "mhyprot",
    "nprotect",
    "tenprotect",
    "tp3helper",
    "vanguard",
    "xigncode",
)

EXPLORER_FILE_WINDOW_CLASSES = {
    "CabinetWClass",
    "ExploreWClass",
}


def is_antcheat_module(name: str) -> bool:
    normalized = name.casefold()
    return any(marker in normalized for marker in ANTCHEAT_MARKERS)


def is_blocked_process(name: str) -> bool:
    normalized = name.casefold()
    return (
        normalized in SYSTEM_PROCESSES
        or normalized in SECURITY_PROCESSES
        or is_antcheat_module(normalized)
    )


def is_explorer_file_window(process_name: str, class_name: str) -> bool:
    return (
        process_name.casefold() == "explorer.exe"
        and class_name in EXPLORER_FILE_WINDOW_CLASSES
    )


def is_supported_window_style(style: int) -> bool:
    return style & WS_CAPTION == WS_CAPTION


def target_rejection_reason(
    *,
    process_name: str,
    style: int,
    is_64_bit: bool,
    has_antcheat_module: bool,
    is_appcontainer: bool,
    is_elevated: bool,
) -> str | None:
    if is_blocked_process(process_name):
        return "系统或安全软件进程"
    if has_antcheat_module:
        return "检测到反作弊模块"
    if not is_supported_window_style(style):
        return "窗口没有标准标题栏"
    if is_appcontainer:
        return "AppContainer/UWP 窗口暂不支持"
    if not is_64_bit:
        return "首版暂不支持 32 位进程"
    if is_elevated:
        return "目标进程权限高于当前工具"
    return None
