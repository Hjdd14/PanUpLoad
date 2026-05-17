# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Project root
_PROJECT_DIR = Path(SPECPATH)

# Collect Flet data files (icons.json, etc.)
_flet_datas = []
try:
    _flet_datas.extend(collect_data_files("flet"))
except Exception:
    pass

# Bundle the Flet desktop Flutter runtime (flet-windows.zip)
_flet_client_zip = str(_PROJECT_DIR / "flet-windows.zip")
if os.path.exists(_flet_client_zip):
    _flet_datas.append((_flet_client_zip, str(Path("flet_desktop") / "app")))

a = Analysis(
    [str(_PROJECT_DIR / "main.py")],
    pathex=[str(_PROJECT_DIR)],
    binaries=[],
    datas=_flet_datas,
    hiddenimports=[
        # Flet and dependencies
        "flet",
        "flet_desktop",
        "flet_desktop.version",
        "flet.auth",
        "flet.canvas",
        "flet.components",
        "flet.controls",
        "flet.fastapi",
        "flet.messaging",
        "flet.pubsub",
        "flet.security",
        "flet.testing",
        "flet.utils",
        "flet.version",
        # Network / HTTP
        "httpx",
        "httpcore",
        "h2",
        "h11",
        "anyio",
        "certifi",
        # Crypto
        "cryptography",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.backends",
        # Serialization
        "msgpack",
        "repath",
        "oauthlib",
        # Async
        "asyncio",
        # PanUpdate packages
        "panupdate",
        "panupdate.auth",
        "panupdate.auth.oauth_server",
        "panupdate.auth.web_login",
        "panupdate.drivers",
        "panupdate.drivers.base",
        "panupdate.drivers.baidu",
        "panupdate.drivers.kuaike",
        "panupdate.core",
        "panupdate.core.file_scanner",
        "panupdate.core.backup_engine",
        "panupdate.core.upload_manager",
        "panupdate.storage",
        "panupdate.storage.crypto",
        "panupdate.storage.db",
        "panupdate.ui",
        "panupdate.ui.app",
        "panupdate.ui.login_page",
        "panupdate.ui.backup_page",
        "panupdate.ui.settings_page",
        "panupdate.ui.components",
        "panupdate.ui.components.drive_card",
        "panupdate.utils",
        "panupdate.utils.retry",
        "panupdate.utils.logger",
        "panupdate.utils.upload_logger",
        # Selenium 4 — Edge automation with CDP cookie access
        "selenium",
        "selenium.webdriver",
        "selenium.webdriver.edge",
        "selenium.webdriver.common",
        "selenium.webdriver.edge.service",
        # Selenium-based login
        "panupdate.auth.selenium_login",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "pandas",
        "numpy",
        "PIL",
        "cv2",
        "scipy",
        "jedi",
        "IPython",
        "pygments",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PanUpLoad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                      # GUI app, no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_PROJECT_DIR / "panupdate" / "ui" / "assets" / "icon.ico") if (_PROJECT_DIR / "panupdate" / "ui" / "assets" / "icon.ico").exists() else None,
)
