"""
core/voice.py - speech in and out for SaiA.

  Recorder        microphone -> WAV (Windows MCI, no extra packages)
  transcribe_wav  WAV -> text, any language (English / Arabic / French / mixed) via the Gemini model
  Speaker         reads an answer aloud (Gemini TTS, falling back to the built-in Windows voices)
  clean_for_speech  strips Markdown / code / table symbols so the voice reads naturally
"""

from __future__ import annotations

import base64
import ctypes
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger("SaiA")

IS_WINDOWS = sys.platform == "win32"
MAX_RECORD_SECONDS = 480
MIN_WAV_BYTES = 6000          # ~0.2 s of 16 kHz mono audio: shorter means nothing was said
TTS_CHUNK_CHARS = 700
TTS_MAX_CHARS = 9000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TRANSCRIBE_PROMPT = (
    "Transcribe this audio exactly as spoken. The speaker may use English, Arabic or French "
    "(or any other language, even mixed in one sentence): write every part in the language and "
    "script that was spoken, never translate. Write Arabic in Arabic script. Use Western digits "
    "0-9 for numbers. Add natural punctuation. Output ONLY the transcript with no comments, "
    "quotes or labels. If there is no intelligible speech, output nothing."
)


# ------------------------------------------------------------------ recording
class Recorder:
    ALIAS = "saia_rec"

    def __init__(self) -> None:
        self.recording = False
        self.started_at = 0.0

    @staticmethod
    def _mci(command: str) -> Tuple[bool, str]:
        winmm = ctypes.windll.winmm
        buf = ctypes.create_unicode_buffer(256)
        err = winmm.mciSendStringW(command, buf, 255, 0)
        if err:
            msg = ctypes.create_unicode_buffer(256)
            winmm.mciGetErrorStringW(err, msg, 255)
            return False, msg.value or f"MCI error {err}"
        return True, buf.value

    def start(self) -> Tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Recording is available on Windows only."
        if self.recording:
            return True, ""
        self._mci(f"close {self.ALIAS}")
        ok, msg = self._mci(f"open new type waveaudio alias {self.ALIAS}")
        if not ok:
            return False, msg
        for setting in ("bitspersample 16", "channels 1", "samplespersec 16000",
                        "alignment 2", "bytespersec 32000"):
            self._mci(f"set {self.ALIAS} {setting}")
        ok, msg = self._mci(f"record {self.ALIAS}")
        if not ok:
            self._mci(f"close {self.ALIAS}")
            return False, msg
        self.recording = True
        self.started_at = time.time()
        return True, ""

    @property
    def elapsed(self) -> int:
        return int(time.time() - self.started_at) if self.recording else 0

    def stop(self) -> Tuple[Optional[str], str]:
        """Stops and saves. Returns (wav_path or None, error)."""
        if not self.recording:
            return None, ""
        self.recording = False
        path = os.path.join(tempfile.gettempdir(), f"saia_rec_{int(time.time())}.wav")
        self._mci(f"stop {self.ALIAS}")
        ok, msg = self._mci(f'save {self.ALIAS} "{path}"')
        self._mci(f"close {self.ALIAS}")
        if not ok:
            return None, msg
        if not os.path.isfile(path) or os.path.getsize(path) < MIN_WAV_BYTES:
            return None, "too_short"
        return path, ""

    def cancel(self) -> None:
        if self.recording:
            self.recording = False
            self._mci(f"stop {self.ALIAS}")
            self._mci(f"close {self.ALIAS}")


# ------------------------------------------------------------------ speech -> text
def transcribe_wav(mml_obj: Any, path: str) -> str:
    """Transcribes a WAV file with the same Gemini client the agent already uses."""
    from google.genai import types
    from core.api_saia import FALLBACK_MODEL

    with open(path, "rb") as f:
        data = f.read()
    contents = [TRANSCRIBE_PROMPT, types.Part.from_bytes(data=data, mime_type="audio/wav")]
    models = [mml_obj.model]
    if FALLBACK_MODEL and FALLBACK_MODEL != mml_obj.model:
        models.append(FALLBACK_MODEL)

    last_err: Optional[Exception] = None
    for model in models:
        for attempt in range(1, 3):
            try:
                response = mml_obj.client.models.generate_content(
                    model=model, contents=contents,
                    config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=4096),
                )
                text = mml_obj._extract_response_text(response)
                return text.strip().strip('"').strip()
            except Exception as exc:
                last_err = exc
                if attempt < 2 and mml_obj._is_retryable_error(exc):
                    time.sleep(1.5)
                    continue
                break
    raise RuntimeError(str(last_err) if last_err else "transcription failed")


# ------------------------------------------------------------------ text -> speech
_FENCE = re.compile(r"```.*?```", re.S)


def clean_for_speech(text: str) -> str:
    try:
        from ui.math_text import convert_math
    except Exception:
        try:
            from math_text import convert_math
        except Exception:
            convert_math = None
    if convert_math:
        text = convert_math(text)
    text = text.replace("‎", "")
    text = _FENCE.sub(" ", text)
    text = re.sub(r"`([^`\n]*)`", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*|__|\*(?=\S)|(?<=\S)\*", "", text)
    text = re.sub(r"^\s*[-*+•]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", "", text, flags=re.M)
    text = re.sub(
        r"^[ \t]*\|(.*?)\|?[ \t]*$",
        lambda m: ", ".join(c.strip() for c in m.group(1).split("|") if c.strip()) + ".",
        text, flags=re.M,
    )
    text = text.replace("|", ", ")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def split_text(text: str, size: int = TTS_CHUNK_CHARS) -> List[str]:
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?؟…\n])\s+", text):
        if len(current) + len(sentence) + 1 > size and current:
            chunks.append(current.strip())
            current = ""
        while len(sentence) > size:  # a single very long sentence
            chunks.append(sentence[:size])
            sentence = sentence[size:]
        current += " " + sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks


def detect_culture(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en-US"
    if sum(1 for c in letters if "؀" <= c <= "ۿ") / len(letters) > 0.3:
        return "ar-SA"
    lowered = f" {text.lower()} "
    french_hits = sum(lowered.count(w) for w in (" le ", " la ", " les ", " des ", " une ", " est ", " et ", " pour ", " que "))
    if french_hits >= 2 or sum(1 for c in text if c in "éèêàçùôîâ") >= 3:
        return "fr-FR"
    return "en-US"


def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def synthesize(mml_obj: Any, text: str) -> bytes:
    """Gemini TTS -> WAV bytes (same call shape the app already uses for audio generation)."""
    from core.api_saia import MEDIA_MODELS, get_media_voice

    last: Optional[Exception] = None
    for model_name in MEDIA_MODELS.get("audio", ()):
        try:
            interaction = mml_obj.client.interactions.create(
                model=model_name,
                input=[{"type": "user_input", "content": [{"type": "text", "text": text}]}],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": get_media_voice()}]},
            )
            data = base64.b64decode(interaction.output_audio.data)
            return data if data[:4] == b"RIFF" else _pcm_to_wav(data)
        except Exception as exc:
            last = exc
    raise RuntimeError(f"TTS failed: {last}")


class Speaker:
    """One answer at a time. speak() returns immediately; on_done() is called from the worker thread."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self.token: Any = None  # whatever the caller uses to know which button is active

    @property
    def active(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()
        if IS_WINDOWS:
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def speak(self, text: str, mml_obj: Any, token: Any = None,
              on_done: Optional[Callable[[Any, Optional[str]], None]] = None) -> None:
        self.stop()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._stop = threading.Event()
        self.token = token
        self._thread = threading.Thread(target=self._run, args=(text, mml_obj, token, on_done), daemon=True)
        self._thread.start()

    def _run(self, text: str, mml_obj: Any, token: Any, on_done: Optional[Callable]) -> None:
        stop, error = self._stop, None
        try:
            clean = clean_for_speech(text)[:TTS_MAX_CHARS]
            if not clean:
                return
            chunks = split_text(clean)
            spoken = 0
            try:
                for chunk in chunks:
                    if stop.is_set():
                        return
                    self._play_wav(synthesize(mml_obj, chunk), stop)
                    spoken += 1
            except Exception as exc:
                logger.warning("Gemini TTS unavailable, using Windows voice: %s", exc)
                if not stop.is_set():
                    self._sapi(" ".join(chunks[spoken:]), stop)
        except Exception as exc:
            error = str(exc)
        finally:
            if on_done:
                try:
                    on_done(token, error)
                except Exception:
                    pass

    @staticmethod
    def _play_wav(data: bytes, stop: threading.Event) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("Audio playback is available on Windows only.")
        import winsound
        path = os.path.join(tempfile.gettempdir(), f"saia_tts_{threading.get_ident()}.wav")
        with open(path, "wb") as f:
            f.write(data)
        if not stop.is_set():
            winsound.PlaySound(path, winsound.SND_FILENAME)  # blocks until finished or purged
        try:
            os.remove(path)
        except OSError:
            pass

    def _sapi(self, text: str, stop: threading.Event) -> None:
        if not IS_WINDOWS or not text.strip():
            return
        base = os.path.join(tempfile.gettempdir(), f"saia_sapi_{threading.get_ident()}")
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(text)
        culture = detect_culture(text)
        try:
            from core.api_saia import get_media_voice, voice_gender
            gender = "Male" if voice_gender(get_media_voice()) == "male" else "Female"
        except Exception:
            gender = "NotSet"
        with open(base + ".ps1", "w", encoding="utf-8-sig") as f:
            f.write(
                "Add-Type -AssemblyName System.Speech\n"
                f"$t = Get-Content -Raw -Encoding UTF8 '{base}.txt'\n"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                "try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::" + gender + ", "
                "[System.Speech.Synthesis.VoiceAge]::NotSet, 0, "
                f"[System.Globalization.CultureInfo]::GetCultureInfo('{culture}')) }} catch {{}}\n"
                "$s.Speak($t)\n"
            )
        if stop.is_set():
            return
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", base + ".ps1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )
        self._proc.wait()
        for ext in (".txt", ".ps1"):
            try:
                os.remove(base + ext)
            except OSError:
                pass
