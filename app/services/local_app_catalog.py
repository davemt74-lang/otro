from __future__ import annotations

# v0.40 ships an embedded, owner-reviewed catalog. Remote callers cannot add
# arbitrary URLs or commands. Catalog updates therefore arrive with reviewed
# HomeServer code changes rather than mutable remote manifests.
CATALOG_VERSION = "2026.09.10.2"

_PIPER_VOICE_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    "2525f22960f62209936f0ae3cce3ffe7b2c18f1f/en/en_US/lessac/medium/"
)
_WHISPER_CPP_RELEASE = "https://github.com/ggml-org/whisper.cpp/releases/download/b4938/"
_WHISPER_MODEL_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"


def _file(name: str, target: str, sha256: str, size_bytes: int, *, max_bytes: int | None = None) -> dict:
    return {
        "name": name,
        "kind": "file",
        "target": target,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "max_bytes": max_bytes or max(size_bytes + 1024 * 1024, int(size_bytes * 1.05)),
    }


CATALOG = {
    "piper-tts": {
        "key": "piper-tts",
        "name": "Piper TTS",
        "version": "2023.11.14-2+lessac-medium",
        "category": "Voice",
        "description": "Fast local neural text-to-speech using the standalone Piper Windows runtime and the en_US-lessac-medium voice.",
        "runtime": "Piper standalone CPU",
        "default_voice": "en_US-lessac-medium",
        "source_label": "rhasspy/piper + rhasspy/piper-voices",
        "license": "MIT runtime; voice model metadata is retained from the upstream Piper voice catalog",
        "capabilities": ["voice.tts", "voice.tts.local", "voice.tts.piper", "speech.synthesize"],
        "requirements": {"os": ["windows"], "arch": ["amd64"], "gpu": False, "python": False},
        "artifacts": [
            {
                "name": "piper-windows-amd64",
                "kind": "zip",
                "url": "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip",
                "target": "runtime",
                "sha256": "f3c58906402b24f3a96d92145f58acba6d86c9b5db896d207f78dc80811efcea",
                "size_bytes": 22477236,
                "max_bytes": 24000000,
                "max_unpacked_bytes": 256 * 1024 * 1024,
            },
            {
                **_file(
                    "en_US-lessac-medium-model",
                    "voices/en_US-lessac-medium.onnx",
                    "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
                    63201294,
                    max_bytes=65000000,
                ),
                "url": _PIPER_VOICE_BASE + "en_US-lessac-medium.onnx",
            },
            {
                **_file(
                    "en_US-lessac-medium-config",
                    "voices/en_US-lessac-medium.onnx.json",
                    "f2b3fd2748ef96843101d2a2811cade306d4d57d58081123f9cb6625c4cde969",
                    7010,
                    max_bytes=20000,
                ),
                "url": _PIPER_VOICE_BASE + "en_US-lessac-medium.onnx.json",
            },
        ],
        "required_paths": [
            "runtime/piper/piper.exe",
            "voices/en_US-lessac-medium.onnx",
            "voices/en_US-lessac-medium.onnx.json",
        ],
    },
    "whisper-stt": {
        "key": "whisper-stt",
        "name": "Whisper STT",
        "version": "b4938+tiny.en-q8_0",
        "category": "Voice",
        "description": "Private local speech-to-text using the standalone whisper.cpp Windows x64 runtime and quantized Whisper Tiny English model.",
        "runtime": "whisper.cpp 1.9.3 standalone CPU",
        "source_label": "ggml-org/whisper.cpp + ggerganov/whisper.cpp",
        "license": "MIT runtime; Whisper model follows the upstream model license",
        "capabilities": ["voice.stt", "voice.stt.local", "voice.transcription", "voice.whisper", "speech.transcribe"],
        "requirements": {"os": ["windows"], "arch": ["amd64"], "gpu": False, "python": False},
        "artifacts": [
            {
                "name": "whisper-cpp-windows-x64-b4938",
                "kind": "zip",
                "url": _WHISPER_CPP_RELEASE + "whisper-bin-x64.zip",
                "target": "runtime",
                "sha256": "c2a4b60edb11f7e11a9191ffb50929535527d4d91c9903dbe3e554583bbbc63d",
                "size_bytes": 8361840,
                "max_bytes": 9000000,
                "max_unpacked_bytes": 96 * 1024 * 1024,
            },
            {
                **_file(
                    "whisper-tiny-en-q8-model",
                    "models/ggml-tiny.en-q8_0.bin",
                    "5bc2b3860aa151a4c6e7bb095e1fcce7cf12c7b020ca08dcec0c6d018bb7dd94",
                    43550795,
                    max_bytes=45000000,
                ),
                "url": _WHISPER_MODEL_BASE + "ggml-tiny.en-q8_0.bin",
            },
        ],
        "required_paths": [
            "runtime/Release/whisper-cli.exe",
            "runtime/Release/whisper.dll",
            "runtime/Release/ggml.dll",
            "models/ggml-tiny.en-q8_0.bin",
        ],
    },
}
