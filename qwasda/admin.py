"""
Admin detection and UAC elevation for Qwasda.

Provides:
- Admin rights detection
- UAC elevation prompt
- Manifest embedding for auto-elevation
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
from pathlib import Path

# Windows constants
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_INFO = 20
SECURITY_MANDATORY_HIGH_RID = 0x00003000
SECURITY_MANDATORY_SYSTEM_RID = 0x00010000

# ShellExecute constants
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400
SW_SHOWNORMAL = 1
SW_HIDE = 0
ELEVATED_ARG = "--qwasda-elevated"


class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [("TokenIsElevated", ctypes.c_ulong)]


class SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("fMask", ctypes.wintypes.ULONG),
        ("hwnd", ctypes.wintypes.HWND),
        ("lpVerb", ctypes.wintypes.LPCWSTR),
        ("lpFile", ctypes.wintypes.LPCWSTR),
        ("lpParameters", ctypes.wintypes.LPCWSTR),
        ("lpDirectory", ctypes.wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.wintypes.LPCWSTR),
        ("hkeyClass", ctypes.wintypes.HKEY),
        ("dwHotKey", ctypes.wintypes.DWORD),
        ("hIcon", ctypes.wintypes.HANDLE),
        ("hProcess", ctypes.wintypes.HANDLE),
    ]


def is_admin() -> bool:
    """
    Check if the current process is running with elevated (admin) privileges.

    Returns:
        True if running as admin, False otherwise.
    """
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        # Fallback method
        try:
            token = ctypes.wintypes.HANDLE()
            if ctypes.windll.advapi32.OpenProcessToken(
                ctypes.windll.kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
            ):
                elevation = TOKEN_ELEVATION()
                size = ctypes.wintypes.DWORD()
                if ctypes.windll.advapi32.GetTokenInformation(
                    token,
                    TOKEN_ELEVATION_INFO,
                    ctypes.byref(elevation),
                    ctypes.sizeof(elevation),
                    ctypes.byref(size),
                ):
                    ctypes.windll.kernel32.CloseHandle(token)
                    return bool(elevation.TokenIsElevated)
                ctypes.windll.kernel32.CloseHandle(token)
        except Exception:
            pass
        return False


def get_integrity_level() -> str:
    """
    Get the mandatory integrity level of the current process.

    Returns:
        String: "Untrusted", "Low", "Medium", "High", "System", or "Unknown"
    """
    try:
        token = ctypes.wintypes.HANDLE()
        if not ctypes.windll.advapi32.OpenProcessToken(
            ctypes.windll.kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
        ):
            return "Unknown"

        # Get token integrity level
        size = ctypes.wintypes.DWORD()
        ctypes.windll.advapi32.GetTokenInformation(
            token, 25, None, 0, ctypes.byref(size)  # TokenIntegrityLevel
        )

        if size.value == 0:
            ctypes.windll.kernel32.CloseHandle(token)
            return "Unknown"

        buffer = ctypes.create_string_buffer(size.value)
        if not ctypes.windll.advapi32.GetTokenInformation(
            token, 25, buffer, size.value, ctypes.byref(size)
        ):
            ctypes.windll.kernel32.CloseHandle(token)
            return "Unknown"

        # Parse SID
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents
        sub_authority_count = ctypes.c_ubyte.from_address(ctypes.addressof(sid) + 1).value
        if sub_authority_count > 0:
            # Last sub-authority is the RID
            rid_offset = ctypes.addressof(sid) + 8 + (sub_authority_count - 1) * 4
            rid = ctypes.c_ulong.from_address(rid_offset).value

            if rid >= SECURITY_MANDATORY_SYSTEM_RID:
                level = "System"
            elif rid >= SECURITY_MANDATORY_HIGH_RID:
                level = "High"
            elif rid >= 0x00002000:  # SECURITY_MANDATORY_MEDIUM_RID
                level = "Medium"
            elif rid >= 0x00001000:  # SECURITY_MANDATORY_LOW_RID
                level = "Low"
            else:
                level = "Untrusted"
        else:
            level = "Unknown"

        ctypes.windll.kernel32.CloseHandle(token)
        return level

    except Exception:
        return "Unknown"


def run_as_admin(
    executable: str | None = None, args: list[str] | None = None, wait: bool = False
) -> tuple[bool, int | None]:
    """
    Re-launch the current script with admin privileges.

    Args:
        executable: Path to executable (defaults to sys.executable)
        args: Command line arguments (defaults to sys.argv)
        wait: Whether to wait for the elevated process to exit

    Returns:
        Tuple of (success, exit_code). exit_code is None if wait=False.
    """
    if executable is None:
        executable = sys.executable
    if args is None:
        args = sys.argv[1:]

    # Build command line
    params = " ".join(f'"{arg}"' if " " in arg else arg for arg in args)

    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"  # This triggers UAC
    sei.lpFile = executable
    sei.lpParameters = params
    sei.lpDirectory = None
    sei.nShow = SW_SHOWNORMAL
    sei.hInstApp = None

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        error = ctypes.windll.kernel32.GetLastError()
        # ERROR_CANCELLED = 1223 (user clicked No on UAC)
        return False, error

    if wait and sei.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        exit_code = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        return True, exit_code.value

    if sei.hProcess:
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)

    return True, None


def ensure_admin(auto_elevate: bool = True) -> bool:
    """
    Ensure the process is running as admin. Optionally auto-elevate.

    Args:
        auto_elevate: If True and not admin, attempt to re-launch as admin

    Returns:
        True if running as admin (or elevation initiated), False otherwise
    """
    if is_admin():
        return True

    if auto_elevate:
        success, _ = request_elevation()
        if success:
            # The caller must terminate the non-elevated process after launch.
            return True

    return False


def request_elevation() -> tuple[bool, int | None]:
    """Request a new elevated process while preserving the command line.

    The marker prevents the elevated child from asking for elevation again.
    The current process remains alive; callers decide when it is safe to exit.
    """
    args = list(sys.argv[1:])
    if ELEVATED_ARG not in args:
        args.append(ELEVATED_ARG)
    return run_as_admin(args=args)


def get_uac_manifest() -> str:
    """
    Get the UAC manifest XML for embedding in the executable.

    This manifest requests 'highestAvailable' execution level,
    which will prompt for elevation if the user is an admin,
    or run as standard user if not.
    """
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity
    version="1.3.4.0"
    processorArchitecture="*"
    name="Qwasda"
    type="win32"
  />
  <description>Qwasda - Ukrainian/English Keyboard Layout Switcher</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel
          level="highestAvailable"
          uiAccess="false"
        />
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <!-- Windows 10 -->
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}" />
      <!-- Windows 11 -->
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}" />
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
    </windowsSettings>
  </application>
</assembly>"""


def write_manifest(manifest_path: Path) -> None:
    """Write UAC manifest to file."""
    manifest_path.write_text(get_uac_manifest(), encoding="utf-8")


def embed_manifest(exe_path: Path, manifest_path: Path) -> bool:
    """
    Embed manifest into executable using mt.exe (Manifest Tool).

    Requires Windows SDK to be installed.
    """
    try:
        # Try to find mt.exe
        windows_sdk_dir = Path(os.environ.get("WINDOWSSDKDIR", ""))
        mt_paths = [
            windows_sdk_dir / "bin" / "10.0.22621.0" / "x64" / "mt.exe",
            windows_sdk_dir / "bin" / "10.0.19041.0" / "x64" / "mt.exe",
            Path("C:/Program Files (x86)/Windows Kits/10/bin/10.0.22621.0/x64/mt.exe"),
            Path("C:/Program Files (x86)/Windows Kits/10/bin/10.0.19041.0/x64/mt.exe"),
        ]

        mt_exe = None
        for p in mt_paths:
            if p.exists():
                mt_exe = p
                break

        if not mt_exe:
            # Try from PATH
            result = subprocess.run(["where", "mt.exe"], capture_output=True, text=True)
            if result.returncode == 0:
                mt_exe = Path(result.stdout.strip().split("\n")[0])

        if not mt_exe:
            return False

        # Embed manifest
        cmd = [
            str(mt_exe),
            "-manifest",
            str(manifest_path),
            "-outputresource:" + str(exe_path) + ";1",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0

    except Exception:
        return False


def is_uac_enabled() -> bool:
    """Check if UAC is enabled on the system."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        )
        value, _ = winreg.QueryValueEx(key, "EnableLUA")
        winreg.CloseKey(key)
        return bool(value == 1)
    except Exception:
        return True  # Assume enabled if can't check
