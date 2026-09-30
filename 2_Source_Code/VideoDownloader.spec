# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
for pkg in ['DrissionPage', 'tldextract']:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ['gui.py'],
    pathex=['C:\\Users\\15916\\Desktop\\vt_dev'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ['browser_extract', 'ytdlp_page', 'res_downloader_page',
                                   'converter_page', 'cookie_guard',
                                   'requests', 'aiohttp', 'cryptography', 'Crypto',
                                   'queue', 'subprocess', 'json', 're',
                                   'pkg_resources._vendor.jaraco',
                                   'pkg_resources._vendor.jaraco.text',
                                   'pkg_resources._vendor.jaraco.context',
                                   'pkg_resources._vendor.jaraco.functools',
                                   'pkg_resources._vendor.packaging',
                                   'pkg_resources._vendor.more_itertools',
                                   'pkg_resources._vendor.pyparsing',
                                   'pkg_resources._vendor.appdirs',
                                   'pkg_resources._vendor.importlib_resources'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'numpy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='通用视频下载器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=r'C:\Users\15916\Desktop\vt_dev\app.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='通用视频下载器',
)
