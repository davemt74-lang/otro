from __future__ import annotations

# v0.43 keeps Local Apps and voice packs in one embedded, owner-reviewed
# catalog. Remote callers cannot add arbitrary URLs or commands; each voice
# model is pinned by size and SHA-256 like every other managed artifact.
CATALOG_VERSION = "2026.09.10.3"

_PIPER_VOICE_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    "2525f22960f62209936f0ae3cce3ffe7b2c18f1f/en/en_US/lessac/medium/"
)
_PIPER_V1_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
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


def _piper_voice_pack(
    *,
    key: str,
    name: str,
    voice_key: str,
    source_path: str,
    model_sha256: str,
    model_size: int,
    config_sha256: str,
    config_size: int,
    language: str,
) -> dict:
    filename = voice_key
    return {
        "key": key,
        "name": name,
        "version": f"piper-v1.0.0+{voice_key}",
        "category": "Voice",
        "description": f"Optional local Piper voice pack: {name}. Uses the separately installed Piper TTS runtime.",
        "runtime": "Piper voice assets",
        "voice_key": voice_key,
        "runtime_app_key": "piper-tts",
        "source_label": "rhasspy/piper-voices v1.0.0",
        "license": "Voice model metadata follows the upstream Piper voice catalog",
        "capabilities": ["voice.pack", "voice.tts.voice", "voice.tts.piper"],
        "requirements": {"os": ["windows"], "arch": ["amd64"], "gpu": False, "python": False},
        "artifacts": [
            {
                **_file(
                    f"{voice_key}-model",
                    f"voices/{filename}.onnx",
                    model_sha256,
                    model_size,
                    max_bytes=65000000,
                ),
                "url": _PIPER_V1_BASE + source_path + f"/{filename}.onnx",
            },
            {
                **_file(
                    f"{voice_key}-config",
                    f"voices/{filename}.onnx.json",
                    config_sha256,
                    config_size,
                    max_bytes=20000,
                ),
                "url": _PIPER_V1_BASE + source_path + f"/{filename}.onnx.json",
            },
        ],
        "required_paths": [
            f"voices/{filename}.onnx",
            f"voices/{filename}.onnx.json",
        ],
        "language": language,
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
    "piper-voice-amy-medium": _piper_voice_pack(
        key="piper-voice-amy-medium",
        name="Piper Voice — Amy (US English, Medium)",
        voice_key="en_US-amy-medium",
        source_path="en/en_US/amy/medium",
        model_sha256="b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
        model_size=63201294,
        config_sha256="95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
        config_size=4882,
        language="en-US",
    ),
    "piper-voice-ryan-medium": _piper_voice_pack(
        key="piper-voice-ryan-medium",
        name="Piper Voice — Ryan (US English, Medium)",
        voice_key="en_US-ryan-medium",
        source_path="en/en_US/ryan/medium",
        model_sha256="abf4c274862564ed647ba0d2c47f8ee7c9b717d27bdad9219100eb310db4047a",
        model_size=63201294,
        config_sha256="44034c056cb15681b2ad494307c7f3f2e4499d1253c700c711fa0a4607ffe78d",
        config_size=4883,
        language="en-US",
    ),
    "piper-voice-alan-medium": _piper_voice_pack(
        key="piper-voice-alan-medium",
        name="Piper Voice — Alan (British English, Medium)",
        voice_key="en_GB-alan-medium",
        source_path="en/en_GB/alan/medium",
        model_sha256="0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330",
        model_size=63201294,
        config_sha256="c0f0d124e5895c00e7c03b35dcc8287f319a6998a365b182deb5c8e752ee8c1e",
        config_size=4888,
        language="en-GB",
    ),
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
