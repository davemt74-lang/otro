from __future__ import annotations

# v0.40 ships an embedded, owner-reviewed catalog. Remote callers cannot add
# arbitrary URLs or commands. Catalog updates therefore arrive with reviewed
# HomeServer code changes rather than mutable remote manifests.
CATALOG_VERSION = "2026.09.10.1"

_PIPER_VOICE_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    "2525f22960f62209936f0ae3cce3ffe7b2c18f1f/en/en_US/lessac/medium/"
)
_WHISPER_BASE = "https://huggingface.co/onnx-community/whisper-tiny.en/resolve/main/"


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
        "capabilities": ["voice.tts", "voice.tts.local", "voice.tts.piper"],
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
        "version": "tiny.en-quantized-2026.09.10",
        "category": "Voice",
        "description": "Local Whisper Tiny English speech-to-text model bundle prepared for the HomeServer Transformers.js/WASM transcription adapter.",
        "runtime": "Transformers.js / ONNX Runtime Web (WASM adapter)",
        "source_label": "onnx-community/whisper-tiny.en",
        "license": "See the upstream model/base-model license and model card",
        "capabilities": ["voice.stt", "voice.stt.local", "voice.transcription", "voice.whisper"],
        "requirements": {"os": ["windows", "linux", "darwin"], "gpu": False, "python": False, "wasm": True},
        "artifacts": [
            {**_file("whisper-config", "model/config.json", "251ea843b5901a99efa58c0b99b8052c6019aa3e7d2baf46693a1128ff606233", 2197, max_bytes=20000), "url": _WHISPER_BASE + "config.json"},
            {**_file("whisper-generation-config", "model/generation_config.json", "7b2e8451ed5f118e75fdd991409d72119d21d2fef1eba9723f68fb9c57fe5dc9", 1646, max_bytes=20000), "url": _WHISPER_BASE + "generation_config.json"},
            {**_file("whisper-preprocessor", "model/preprocessor_config.json", "a6a76d28c93edb273669eb9e0b0636a2bddbb1272c3261e47b7ca6dfdbac1b8d", 339, max_bytes=10000), "url": _WHISPER_BASE + "preprocessor_config.json"},
            {**_file("whisper-tokenizer", "model/tokenizer.json", "5eb60cec1e77aeeb6869a2bb5a8e01a84c3fe5d072d75369343021fe6f5310d0", 2405679, max_bytes=3000000), "url": _WHISPER_BASE + "tokenizer.json"},
            {**_file("whisper-tokenizer-config", "model/tokenizer_config.json", "93879c3dccdd4b976f709acd85b44778873f30c275e67026f30ca1e4c975230c", 282662, max_bytes=400000), "url": _WHISPER_BASE + "tokenizer_config.json"},
            {**_file("whisper-added-tokens", "model/added_tokens.json", "560be47bea388757f8d4cc185c5d82067426cbb6361e38016dd90ddc01ab203a", 34604, max_bytes=100000), "url": _WHISPER_BASE + "added_tokens.json"},
            {**_file("whisper-special-tokens", "model/special_tokens_map.json", "98bdf3ec5b32e31575b02f64b0a32bde7c0449075d34484a7df9bdd3cdeb9fb9", 2173, max_bytes=20000), "url": _WHISPER_BASE + "special_tokens_map.json"},
            {**_file("whisper-vocab", "model/vocab.json", "f6bd25a65e4e63ca31360e9fb11c7e4f9a391a78385d640acd814092dd6eee4f", 999186, max_bytes=1200000), "url": _WHISPER_BASE + "vocab.json"},
            {**_file("whisper-merges", "model/merges.txt", "1ce1664773c50f3e0cc8842619a93edc4624525b728b188a9e0be33b7726adc5", 456318, max_bytes=600000), "url": _WHISPER_BASE + "merges.txt"},
            {**_file("whisper-normalizer", "model/normalizer.json", "bf1c507dc8724ca9cf9903640dacfb69dae2f00edee4f21ceba106a7392f26dd", 52666, max_bytes=100000), "url": _WHISPER_BASE + "normalizer.json"},
            {**_file("whisper-encoder-quantized", "model/onnx/encoder_model_quantized.onnx", "e93ec822f16a8fd264e7de972ad17d615ea7334b75a52d54c50c2e18dd503a25", 10124993, max_bytes=11000000), "url": _WHISPER_BASE + "onnx/encoder_model_quantized.onnx"},
            {**_file("whisper-decoder-merged-quantized", "model/onnx/decoder_model_merged_quantized.onnx", "c0592d0749413c960569e1c7fb806b060d5d18f3ebad4a95cbf9a77dc6e9be52", 30718858, max_bytes=32000000), "url": _WHISPER_BASE + "onnx/decoder_model_merged_quantized.onnx"},
        ],
        "required_paths": [
            "model/config.json",
            "model/preprocessor_config.json",
            "model/tokenizer.json",
            "model/onnx/encoder_model_quantized.onnx",
            "model/onnx/decoder_model_merged_quantized.onnx",
        ],
    },
}
