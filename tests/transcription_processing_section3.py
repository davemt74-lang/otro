"""Exercise canonical Whisper service with real disposable child processes.
Synthetic audio/runtime only; no installed-device or provider certification.
"""
import asyncio
import importlib.util
import io
import subprocess
import sys
import tempfile
import threading
import time
import types
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Isolate managed-app/database dependencies; load the production service unchanged.
for name in ('section3', 'section3.services'):
    package = types.ModuleType(name)
    package.__path__ = []
    sys.modules[name] = package
config = types.ModuleType('section3.config')
config.settings = types.SimpleNamespace(data_dir=None)
sys.modules[config.__name__] = config
for name in ('local_apps', 'voice_settings'):
    module = types.ModuleType('section3.services.' + name)
    sys.modules[module.__name__] = module
spec = importlib.util.spec_from_file_location('section3.services.local_voice', ROOT / 'app/services/local_voice.py')
voice = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = voice
spec.loader.exec_module(voice)

def wav(seconds=0.01):
    out = io.BytesIO()
    with wave.open(out, 'wb') as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b'\x00\x00' * int(seconds * 16000))
    return out.getvalue()

def error_code(fn, code):
    try:
        fn()
        raise AssertionError('expected failure')
    except voice.LocalVoiceError as exc:
        assert exc.status_code == code, (exc.status_code, str(exc))

with tempfile.TemporaryDirectory(prefix='transcription-section3-') as temp:
    config.settings.data_dir = Path(temp)
    executable = Path(temp) / 'whisper-cli.exe'
    executable.write_bytes(b'synthetic')
    voice._active_preferences = lambda: ({'stt_model': 'synthetic'}, {'app_key': 'synthetic', 'path': 'model.bin'}, {})
    voice._resolve_managed_file = lambda *_: executable
    original_popen = subprocess.Popen
    children = []
    mode = 'success'
    def launch(command, **kwargs):
        assert kwargs['shell'] is False
        assert kwargs['stdin'] is subprocess.DEVNULL
        assert kwargs['stdout'] is subprocess.DEVNULL and kwargs['stderr'] is subprocess.DEVNULL
        output = command[command.index('-of') + 1] + '.txt'
        code = "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('synthetic transcript')" if mode == 'success' else 'import time;time.sleep(60)'
        child = original_popen([sys.executable, '-c', code, output], **kwargs)
        children.append(child)
        return child
    voice.subprocess.Popen = launch
    try:
        result = voice.transcribe(wav())
        assert result == {'text': 'synthetic transcript', 'provider': 'whisper.cpp', 'local': True, 'model': 'synthetic'}
        assert not list(voice._voice_temp_root().iterdir())
        print('PASS real child output and private temporary audio cleanup')
        error_code(lambda: voice.transcribe(wav()[:-2]), 422)
        error_code(lambda: voice.transcribe(wav(121)), 413)
        print('PASS truncated and excessive-duration WAV rejected before processing')
        mode = 'sleep'
        cancel = threading.Event()
        outcome = []
        def work():
            try: voice.transcribe(wav(), cancel=cancel)
            except voice.LocalVoiceError as exc: outcome.append(exc.status_code)
        worker = threading.Thread(target=work)
        worker.start()
        deadline = time.monotonic() + 2
        while len(children) < 2 and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert len(children) == 2
        error_code(lambda: voice.transcribe(wav()), 429)
        cancel.set()
        worker.join(2)
        assert not worker.is_alive() and outcome == [499]
        assert children[-1].poll() is not None
        assert not list(voice._voice_temp_root().iterdir())
        print('PASS bounded concurrency, cancellation, child reap and audio cleanup')
        voice.TRANSCRIBE_TIMEOUT_SECONDS = 0.05
        error_code(lambda: voice.transcribe(wav()), 504)
        assert children[-1].poll() is not None
        assert not list(voice._voice_temp_root().iterdir())
        print('PASS deadline kills child, reaps it and releases processing slot')
        voice.TRANSCRIBE_TIMEOUT_SECONDS = 90
        async def check_disconnect():
            polls = []
            class Request:
                async def is_disconnected(self):
                    polls.append(time.monotonic())
                    return len(polls) >= 4
            async def heartbeat():
                ticks = 0
                while ticks < 15:
                    await asyncio.sleep(0.025)
                    ticks += 1
                return ticks
            processing = asyncio.create_task(voice.transcribe_request(wav(), Request()))
            assert await heartbeat() == 15
            try:
                await processing
                raise AssertionError('disconnect must cancel')
            except voice.LocalVoiceError as exc: assert exc.status_code == 499
            assert children[-1].poll() is not None
            assert not list(voice._voice_temp_root().iterdir())
        asyncio.run(check_disconnect())
        mode = 'success'
        assert voice.transcribe(wav())['text'] == 'synthetic transcript'
        print('PASS async control remains responsive; disconnect cancels; next request succeeds')
    finally:
        voice.subprocess.Popen = original_popen
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
print('TRANSCRIPTION_PROCESSING_SECTION3=PASS (5 behavioral cases)')
