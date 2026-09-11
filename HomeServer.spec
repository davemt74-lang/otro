# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('uvicorn')

a = Analysis(
    ['desktop/launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('database/schema.sql', 'database'),
        ('database/knowledge_collections.sql', 'database'),
        ('database/agent_voice_profiles.sql', 'database'),
        ('database/agent_routing.sql', 'database'),
        ('database/agent_delegation_workflows.sql', 'database'),
        ('database/migrations', 'database/migrations'),
        ('ui', 'ui'),
    ],
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
