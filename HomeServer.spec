# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_submodules

livekit_datas, livekit_binaries, livekit_hiddenimports = collect_all('livekit')
sounddevice_datas, sounddevice_binaries, sounddevice_hiddenimports = collect_all('sounddevice')
hiddenimports = collect_submodules('uvicorn') + collect_submodules('serial') + sounddevice_hiddenimports + livekit_hiddenimports + [
    'app.services.meeting_intelligence',
    'app.services.meeting_intelligence_remote',
]

a = Analysis(
    ['desktop/launcher.py'],
    pathex=['.'],
    binaries=livekit_binaries + sounddevice_binaries,
    # Every SQLite feature schema, including recordings/transcriptions and the
    # versioned migrations, must be available inside the packaged application.
    # Do not enumerate only old schema filenames: that silently breaks upgrades.
    datas=[
        ('database', 'database'),
        ('ui', 'ui'),
    ] + livekit_datas + sounddevice_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HomeServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
