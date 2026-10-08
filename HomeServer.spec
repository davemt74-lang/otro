# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_submodules

livekit_datas, livekit_binaries, livekit_hiddenimports = collect_all('livekit')
sounddevice_datas, sounddevice_binaries, sounddevice_hiddenimports = collect_all('sounddevice')
cv2_datas, cv2_binaries, cv2_hiddenimports = collect_all('cv2')
playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all('playwright')
hiddenimports = collect_submodules('uvicorn') + collect_submodules('serial') + sounddevice_hiddenimports + livekit_hiddenimports + cv2_hiddenimports + playwright_hiddenimports + [
    'app.services.meeting_intelligence',
    'app.services.meeting_intelligence_remote',
]

a = Analysis(
    ['desktop/launcher.py'],
    pathex=['.'],
    binaries=livekit_binaries + sounddevice_binaries + cv2_binaries + playwright_binaries,
    datas=[
        ('database/schema.sql', 'database'),
        ('database/knowledge_collections.sql', 'database'),
        ('database/agent_voice_profiles.sql', 'database'),
        ('database/agent_routing.sql', 'database'),
        ('database/agent_delegation_workflows.sql', 'database'),
        ('database/agent_mission_runtime.sql', 'database'),
        ('database/agent_mission_execution.sql', 'database'),
        ('database/agent_mission_browser.sql', 'database'),
        ('database/agent_mission_live_browser.sql', 'database'),
        ('database/agent_mission_cognition.sql', 'database'),
        ('database/agent_workflow_supervision.sql', 'database'),
        ('database/agent_workflow_automation.sql', 'database'),
        ('database/runtime_certification.sql', 'database'),
        ('database/governed_recordings.sql', 'database'),
        ('database/local_transcription_sessions.sql', 'database'),
        ('database/migrations', 'database/migrations'),
        ('ui', 'ui'),
    ] + livekit_datas + sounddevice_datas + cv2_datas + playwright_datas,
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
