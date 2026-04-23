# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for robot_monitor
#
# Build (one-folder distribution):
#   pyinstaller robot_monitor.spec
#
# The resulting dist\robot_monitor\ folder contains the executable and all
# required files. Copy the whole folder to the target machine.
#
# config.ini is included as a writable template so the user can edit it.
# The templates/ folder is bundled as data so Flask can serve the HTML pages.
#
# To build a single-file .exe instead, change:
#   exclude_binaries=True  ->  exclude_binaries=False
# and remove the COLLECT block.

block_cipher = None

a = Analysis(
    ['robot_monitor.py'],
    pathex=[],
    binaries=[],
    datas=[
        # HTML templates required by Flask
        ('templates', 'templates'),
        # Default config shipped with the executable
        ('config.ini', '.'),
    ],
    hiddenimports=[
        # Flask internals not always detected by static analysis
        'flask',
        'flask.templating',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.routing',
        'werkzeug.exceptions',
        # requests + auth helpers
        'requests',
        'requests.auth',
        'requests.adapters',
        'requests.packages',
        'urllib3',
        'urllib3.util',
        'urllib3.util.retry',
        'charset_normalizer',
        # cryptography (used for self-signed TLS cert generation)
        'cryptography',
        'cryptography.x509',
        'cryptography.x509.oid',
        'cryptography.hazmat',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.backends.openssl',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.primitives.serialization',
        'cryptography.hazmat.primitives.asymmetric',
        'cryptography.hazmat.primitives.asymmetric.rsa',
        # Standard-library modules that may be missed on some platforms
        'xml.etree.ElementTree',
        'configparser',
        'queue',
        'threading',
        'ipaddress',
        'logging',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude large packages that are definitely not used
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'PyQt5',
        'PyQt6',
        'wx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,       # keep binaries in the COLLECT folder
    name='robot_monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                # keep console window for log output
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Uncomment to embed a custom icon:
    # icon='icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='robot_monitor',
)
