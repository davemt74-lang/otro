from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import capability_registry  # noqa: E402


piper = capability_registry.local_apps.CATALOG["piper-tts"]
assert "speech.synthesize" in piper["capabilities"]

original_table_exists = capability_registry._table_exists
original_installed = capability_registry.local_apps.installed_capabilities

try:
    capability_registry._table_exists = lambda table_name: table_name == "local_apps"

    # A healthy same-version install may keep the exact same binary while the
    # reviewed HomeServer adapter adds capability metadata. The registry should
    # project the current trusted catalog capabilities without a reinstall.
    capability_registry.local_apps.installed_capabilities = lambda: [{
        "key": "piper-tts",
        "name": "Piper TTS",
        "version": piper["version"],
        "status": "installed",
        "capabilities": ["voice.tts", "voice.tts.local", "voice.tts.piper"],
        "local": True,
    }]
    same_version = capability_registry._local_app_inventory()
    assert len(same_version) == 1
    assert "speech.synthesize" in same_version[0]["capabilities"]

    # Never grant newly reviewed catalog capabilities to a stale/mismatched
    # binary. Until that app is explicitly updated, retain only its recorded
    # installed capability set.
    stale_caps = ["voice.tts", "voice.tts.local", "voice.tts.piper"]
    capability_registry.local_apps.installed_capabilities = lambda: [{
        "key": "piper-tts",
        "name": "Piper TTS",
        "version": "older-piper-runtime",
        "status": "installed",
        "capabilities": list(stale_caps),
        "local": True,
    }]
    stale_version = capability_registry._local_app_inventory()
    assert stale_version[0]["capabilities"] == stale_caps
    assert "speech.synthesize" not in stale_version[0]["capabilities"]

    # Unknown locally recorded apps are not expanded from unrelated catalog
    # metadata and remain unchanged.
    capability_registry.local_apps.installed_capabilities = lambda: [{
        "key": "synthetic-legacy-app",
        "name": "Synthetic Legacy App",
        "version": "1.0.0",
        "status": "installed",
        "capabilities": ["synthetic.read"],
        "local": True,
    }]
    unknown = capability_registry._local_app_inventory()
    assert unknown[0]["capabilities"] == ["synthetic.read"]
finally:
    capability_registry._table_exists = original_table_exists
    capability_registry.local_apps.installed_capabilities = original_installed

print("HomeServer v0.41 Local App capability metadata compatibility regression passed")
