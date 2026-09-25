#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""免打包预览 NSIS 安装器皮肤 —— 秒级看效果，不用跑一小时 electron-builder。

原理：用 electron-builder 自带的 makensis 编译两个"迷你安装器"（只含一页），
启动后截图，再关掉。目录页能看到 installerHeader.bmp，完成页能看到
installerSidebar.bmp（MUI 的完成页/欢迎页是"大页面"布局，本身没有页眉条，
所以页眉图只能在目录页/安装页核对）。

依赖：Pillow（截图）、numpy（可选）。需要能弹窗的桌面会话。

跑法：
  "C:/ProgramData/anaconda3/python.exe" scripts/preview-installer-skin.py

输出：tmp/installer-skin/preview/{directory-page.png, finish-page.png}
"""
from __future__ import annotations

import ctypes
import glob
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "tmp", "installer-skin")
PREVIEW = os.path.join(WORK, "preview")

# NSI 模板：%ROOT% 会被替换成仓库根的绝对路径（反斜杠形式）。
# 三个坑都在这里规避：UTF-8 BOM、File 指令只认反斜杠、相对路径按脚本目录解析。
NSI = r'''Unicode true
!include "MUI2.nsh"
Name "Abcyesno"
OutFile "%ROOT%\tmp\installer-skin\%NAME%.exe"
InstallDir "$LOCALAPPDATA\AbcyesnoSkinPreview"
RequestExecutionLevel user
!define MUI_ICON "%ROOT%\build\icon.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "%ROOT%\build\installerHeader.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP "%ROOT%\build\installerSidebar.bmp"
%EXTRA%
!insertmacro MUI_LANGUAGE "SimpChinese"
Section "Dummy"
SectionEnd
'''

PAGES = {
    "skin-directory": (
        "directory-page.png",
        r'!define MUI_DIRECTORYPAGE_TEXT_TOP "选择 Abcyesno 的安装位置。"' "\n"
        r"!insertmacro MUI_PAGE_DIRECTORY",
        "安装页/目录页：主要核对页眉右侧的 installerHeader.bmp",
    ),
    "skin-finish": (
        "finish-page.png",
        r'!define MUI_FINISHPAGE_TITLE "Abcyesno 安装向导已完成"' "\n"
        r'!define MUI_FINISHPAGE_TEXT "左侧大图是 installerSidebar.bmp。"' "\n"
        r"!insertmacro MUI_PAGE_FINISH",
        "完成页：主要核对左侧的 installerSidebar.bmp",
    ),
}


def find_makensis() -> str:
    base = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "electron-builder", "Cache", "nsis"
    )
    hits = sorted(glob.glob(os.path.join(base, "nsis-3.*", "makensis.exe")))
    if not hits:
        raise SystemExit(f"没找到 makensis：{base}\\nsis-3.*\\makensis.exe")
    return hits[-1]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def grab_window(title_key: str, out_path: str) -> bool:
    u = ctypes.windll.user32
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]

    found: list[tuple[int, str]] = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                found.append((h, buf.value))
        return True

    hwnd = None
    for _attempt in range(40):
        found.clear()
        u.EnumWindows(CB(cb), 0)
        for h, t in found:
            if title_key in t:
                hwnd = h
                break
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        return False

    u.SetForegroundWindow(hwnd)
    time.sleep(0.9)
    rect = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    img.save(out_path)
    return True


def main() -> int:
    os.makedirs(PREVIEW, exist_ok=True)
    makensis = find_makensis()
    print(f"makensis: {makensis}")

    for name, (shot, extra, note) in PAGES.items():
        nsi_path = os.path.join(WORK, f"{name}.nsi")
        with open(nsi_path, "w", encoding="utf-8-sig") as fh:  # NSIS3 必须 BOM
            fh.write(NSI.replace("%ROOT%", ROOT).replace("%NAME%", name).replace("%EXTRA%", extra))

        exe = os.path.join(WORK, f"{name}.exe")
        build = subprocess.run(
            [makensis, f"-DROOT={ROOT}", nsi_path],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if build.returncode != 0 or not os.path.exists(exe):
            print(f"[{name}] 编译失败：")
            print((build.stdout + build.stderr).strip()[-1200:])
            return 1
        print(f"[{name}] 编译 OK -> {note}")

        proc = subprocess.Popen([exe], cwd=WORK)
        try:
            ok = grab_window("Abcyesno", os.path.join(PREVIEW, shot))
            print(f"[{name}] 截图 {'OK' if ok else '失败（没找到窗口）'} -> {shot}")
        finally:
            proc.kill()
            proc.wait(timeout=5)
            time.sleep(0.4)

    print(f"\n预览图在：{PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
