from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, got {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "ui/chat-enhancements.js",
    "    unlockLocalAudio();\n    const status = await refreshLocalVoiceStatus();\n    if (!conversationStarting) return;",
    "    unlockLocalAudio();\n    const settingsController = window.HomeServerVoiceSettings;\n    if (settingsController?.load) {\n      try { await settingsController.load(); } catch (_) {}\n    }\n    if (!conversationStarting) return;\n    const status = await refreshLocalVoiceStatus();\n    if (!conversationStarting) return;",
)

replace_once(
    "ui/chat-dictation.js",
    "    starting = true;\n    setState('checking');\n    const status = await readStatus();\n    if (!starting || startGeneration !== generation) return;",
    "    starting = true;\n    setState('checking');\n    const settingsController = window.HomeServerVoiceSettings;\n    if (settingsController?.load) {\n      try { await settingsController.load(); } catch (_) {}\n    }\n    if (!starting || startGeneration !== generation) return;\n    const status = await readStatus();\n    if (!starting || startGeneration !== generation) return;",
)

Path(".github/voice_settings_load_patch.py").unlink(missing_ok=True)
Path(".github/workflows/voice-settings-load-patch.yml").unlink(missing_ok=True)
