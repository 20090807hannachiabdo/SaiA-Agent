"""
SaiA AI Core Engine
Full autonomous suite:
- Direct Chat & Academic Tutoring (No files created unless requested)
- Gmail Email sending via yagmail with environment credentials
- Office Documents generation (Word, Excel, PowerPoint, PDF)
- Media Generation (Images, Audio/TTS)
- Persistent Long-Term Memory & Cross-Session Recall
- Desktop Automation, Notifications, and System Control
"""


from __future__ import annotations

import base64
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

try:
    from core.attachments import build_attachment_contents
    ATTACH_OK = True
except Exception:  # keeps the single-file path working if the module is missing
    ATTACH_OK = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("SaiA")


class Cancelled(BaseException):
    """Raised inside a request that the user cancelled. Like asyncio.CancelledError it derives from
    BaseException, so the many `except Exception:` blocks in the app cannot swallow it by accident."""

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The keys file lives in the user's AppData (…\AppData\Roaming\SaiA\api_keys.env): it survives
# updates / re-installs, works from a packaged exe, and is never shipped inside the app folder.
SAIA_DATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "SaiA"
ENV_FILE = SAIA_DATA_DIR / "api_keys.env"
LEGACY_ENV_FILE = PROJECT_ROOT / "api_keys.env"


def _migrate_legacy_env() -> None:
    """One-time copy of the old api_keys.env (next to the app) into AppData. The old file is kept."""
    try:
        if not ENV_FILE.exists() and LEGACY_ENV_FILE.is_file():
            SAIA_DATA_DIR.mkdir(parents=True, exist_ok=True)
            ENV_FILE.write_bytes(LEGACY_ENV_FILE.read_bytes())
    except Exception as exc:  # the app must start even when AppData is not writable
        logging.getLogger("SaiA").warning("could not migrate %s: %s", LEGACY_ENV_FILE, exc)


def _load_env_file() -> None:
    """Loads non-empty values from the keys file; an empty or missing file is fine (keys can be added later)."""
    try:
        if ENV_FILE.is_file():
            from dotenv import dotenv_values
            for key, value in dotenv_values(ENV_FILE).items():
                if value:
                    os.environ[key] = value
    except Exception as exc:
        logging.getLogger("SaiA").warning("could not read %s: %s", ENV_FILE, exc)


_migrate_legacy_env()
_load_env_file()

# Credentials managed from Settings -> "API & Gmail Keys" (stored in api_keys.env).
_PATH_RE = re.compile(
    r"""[A-Za-z]:[\\/][^\r\n"<>|?*]*?\.[A-Za-z0-9]{1,8}(?=$|[\s"'<>|,;)\]`*]|\.(?:\s|$))""", re.M
)


def extract_file_paths(text: str, max_items: int = 8) -> List[str]:
    """Absolute Windows paths mentioned in `text` that exist on disk (used for the file cards in chat)."""
    seen, found = set(), []
    for match in _PATH_RE.finditer(text or ""):
        path = os.path.normpath(match.group(0))
        key = path.lower()
        if key in seen:
            continue
        try:
            exists = os.path.isfile(path)
        except OSError:
            exists = False
        if exists:
            seen.add(key)
            found.append(path)
            if len(found) >= max_items:
                break
    return found


ENV_FIELDS = ("AI_API_KEY", "SENDER_EMAIL", "APP_PASSWRD")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def read_env_values() -> Dict[str, str]:
    """Returns the three credentials as stored in the env file (falls back to the process environment)."""
    from dotenv import dotenv_values

    stored: Dict[str, str] = {}
    try:
        if ENV_FILE.is_file():
            stored = {k: (v or "") for k, v in dotenv_values(ENV_FILE).items()}
    except Exception:
        stored = {}
    return {k: stored.get(k) or os.environ.get(k, "") for k in ENV_FIELDS}


def _format_env_value(value: str) -> str:
    if value and (value != value.strip() or any(c in value for c in "#\"'\\$`")):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def save_env_values(values: Dict[str, str]) -> None:
    """Writes the given credentials into the env file (other lines are kept) and applies them immediately."""
    clean: Dict[str, str] = {}
    for key in ENV_FIELDS:
        if key in values:
            value = str(values[key] or "").strip()
            if "\n" in value or "\r" in value:
                raise ValueError(f"{key} must be a single line.")
            clean[key] = value

    text = ""
    if ENV_FILE.is_file():
        text = ENV_FILE.read_text(encoding="utf-8-sig")
    newline = "\r\n" if ("\r\n" in text or not text) else "\n"

    pending = dict(clean)
    written = set()
    out: List[str] = []
    for line in text.splitlines():
        match = _ENV_LINE.match(line)
        key = match.group(1) if match else None
        if key in clean:
            if key not in written:  # also drops duplicate definitions of the same key
                out.append(f"{key}={_format_env_value(clean[key])}")
                written.add(key)
                pending.pop(key, None)
            continue
        out.append(line)
    for key, value in pending.items():
        out.append(f"{key}={_format_env_value(value)}")

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENV_FILE.with_name(ENV_FILE.name + ".tmp")
    tmp.write_bytes((newline.join(out) + newline).encode("utf-8"))
    os.replace(tmp, ENV_FILE)

    for key, value in clean.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)

MEDIA_MODELS = {
    "audio": ("gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"),
    "image": ("gemini-2.5-flash-image", "gemini-3.1-flash-lite-image", "gemini-nano-banana-2.1"),
}

# Prebuilt Gemini TTS voices offered in Settings -> Voice (name -> gender).
# Add any other Gemini voice name here and it appears in the picker automatically.
VOICES = {
    "Kore": "female", "Zephyr": "female", "Leda": "female", "Aoede": "female",
    "Callirrhoe": "female", "Sulafat": "female", "Vindemiatrix": "female", "Achernar": "female",
    "Puck": "male", "Charon": "male", "Fenrir": "male", "Orus": "male",
    "Algieba": "male", "Schedar": "male", "Umbriel": "male", "Enceladus": "male",
}
DEFAULT_VOICE = "Kore"
MEDIA_VOICE = DEFAULT_VOICE  # kept for backward compatibility; use get_media_voice()


def set_media_voice(name: str) -> str:
    """Sets the voice used for spoken answers and generated audio. Unknown names fall back to the default."""
    global MEDIA_VOICE
    MEDIA_VOICE = name if name in VOICES else DEFAULT_VOICE
    return MEDIA_VOICE


def get_media_voice() -> str:
    return MEDIA_VOICE if MEDIA_VOICE in VOICES else DEFAULT_VOICE


def voice_gender(name: str) -> str:
    return VOICES.get(name, "female")

FALLBACK_MODEL = "gemini-3.5-flash"
STEP_MARKER = "SAIA_STEP:"

# --------------------------------------------------
# Complete Comprehensive System Prompt
# --------------------------------------------------

SYSTEM_PROMPT = r"""
You are SaiA, a highly capable autonomous AI agent running natively on Windows.

THREE MODES OF OPERATION (STRICT ROUTING):

MODE 1: DIRECT CHAT & Q&A (NO FILES, NO COMMANDS)
- If the user asks a question, requests an explanation, seeks tutoring, solves a math problem,
  asks about their age or past chats, requests advice or code snippets WITHOUT explicitly asking
  to save or create a file, send an email, or execute a system action:
  DO NOT write a PowerShell command! DO NOT generate files in Downloads!
  Return ONLY:
####SAIA_DIRECT####
<Your detailed, comprehensive, high-quality answer in the user's language>
- FORMATTING OF THE ANSWER (the app renders Markdown visually):
  Use '## ' headings for sections, **bold** for key terms, '- ' bullets and '1. ' numbered steps,
  Markdown tables ('| col | col |' + separator row) whenever comparing items or listing data,
  and fenced code blocks with a language tag (```python) for any code.
  Keep simple answers as plain short paragraphs; do not over-format.
  ATTACHMENTS: the user may attach many files (images, PDFs, Word/Excel/PowerPoint, code, audio). The
  ATTACHMENTS list in the request says which were read. Refer to files by name or number, compare them
  when asked, and say plainly when a file was skipped or truncated. Audio attachments can be transcribed on request.
  INTERACTIVE FUNCTION GRAPH: whenever you explain or analyse a function of x (linear, quadratic, trig, exponential,
  logarithmic...) or the user asks to draw / plot / graph / try a function, embed a live analysis window (slider +
  moving point + graph + zeros/extrema) by writing a fenced block tagged saia-func inside your answer. It may sit
  between normal paragraphs, so explain around it:
```saia-func
f(x) = 2x + 3
range: -10, 10
x: 3.5
```
  One expression per line (max 3, named f, g, h). 'range' and 'x' (initial value) are optional. Variable is x; use ^ for
  powers and sin cos tan sqrt ln log exp abs pi. The app draws everything itself: never add ASCII graphs or value tables.
  MATH: write formulas the way a person writes them: x², √x, a/b, ≤, ≥, ≠, ±, ·, π, ∞. Prefer plain linear text;
  if you must use LaTeX put it inside $...$ (the app converts it). Use Western digits 0-9 only. Never leave raw backslash commands outside $...$.

MODE 2: MEDIA GENERATION (IMAGE / AUDIO / VIDEO)
- If the user asks to GENERATE a new image, picture, drawing, logo, speech/voiceover:
  Return ONLY:
####SAIA_MEDIA####<image or audio>
<detailed prompt in English for image, or exact speech text for audio>
####SAIA_MESSAGE####
<Short 1-2 sentence confirmation in user's language>

MODE 3: POWERSHELL COMMAND EXECUTION (ACTIONS & FILES)
- Use this mode ONLY when the user asks for a real action:
  1. Sending an EMAIL via Gmail.
  2. EXPLICITLY creating, saving, exporting, or downloading a FILE (Word, Excel, PPTX, PDF, Zip, Code file).
  3. System actions (desktop wallpaper, notifications, WiFi inspection, scheduled reminders).
- Return ONLY:
1. One valid PowerShell command (START DIRECTLY with executable code, NEVER add text or markdown before it).
2. The exact separator:
####SAIA_MESSAGE####
3. A brief user-facing message (1-2 sentences) in the user's language confirming success.

VERIFICATION (Mandatory for file/state changes):
- Check deliverables exist and are non-empty. If a file was created or saved, print its full absolute path on its own line
  (the app turns it into a file card with Open / Save a copy). Only on complete success, print: SAIA_OK.
PROGRESS STEPS:
- Print "SAIA_STEP: <short 2-6 words present-tense description>" before each major phase starts.

MEMORY & CONTINUITY (CRITICAL):
- You HAVE persistent memory and access to previous conversations.
- NEVER claim that you do not remember or do not store data. Use "USER PERSISTENT LONG-TERM MEMORY"
  and "PAST CONVERSATIONS ARCHIVE" to answer user questions about past chats, their age, name, etc.
- When the user tells you new personal details, you may append at the very end:
  ####SAIA_MEMORY####{"key": "value"}

TOOLBOX & METHODS FOR ACTIONS:

1. EMAIL (Gmail via yagmail):
- Credentials are in the process environment. Read them with:
  sender = os.environ.get("SENDER_EMAIL")
  password = os.environ.get("APP_PASSWRD", "").replace(" ", "")
  If the user says "to me", use sender.
- Send code structure:
@'
import os
import yagmail
sender = os.environ.get("SENDER_EMAIL")
password = os.environ.get("APP_PASSWRD", "").replace(" ", "")
if not sender or not password:
    raise ValueError("Gmail credentials missing from environment.")
yag = yagmail.SMTP(sender, password)
yag.send(to=recipients, subject=subject, contents=body, attachments=attachments_list_or_None)
yag.close()
print("SAIA_OK")
'@ | & 'python' -u -

2. OFFICE DOCUMENTS (Python method):
- Use openpyxl (Excel), python-docx (Word), python-pptx (PowerPoint).
- Run Python inline via here-string piped to the python interpreter:
@'
<python code>
'@ | & 'python' -u -
- Save deliverables ONLY to the user's Downloads directory.
- For Excel: format tables, bold headers, set column widths. For Arabic: ws.sheet_view.rightToLeft = True.
- For Word: format headings, styles, margins. For Arabic: set <w:bidi/> and Arial font.
- For PowerPoint: 16:9 widescreen layout, blank slide layout, styled cards and clear large fonts.

3. PDF GENERATION:
- Write fully styled HTML into $env:TEMP, then convert via headless Microsoft Edge:
  & $edge --headless --disable-gpu --no-pdf-header-footer --print-to-pdf="<pdf_path>" "<html_path>"

4. ARCHIVING & COMPRESSION:
- Zip/Unzip via Python zipfile. Password-protected zip via pyzipper.

5. DOWNLOADS:
- YouTube/Video via yt-dlp to Downloads folder. Direct URLs via urllib.request.

6. DESKTOP NOTIFICATIONS & WALLPAPER:
- Windows Toast notifications via Windows.UI.Notifications.
- Wallpaper change via ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3).

7. SCREEN & MOUSE FAIL-SAFE:
- In mouse automation, abort immediately if cursor reaches the extreme right edge (x >= GetSystemMetrics(0) - 8).
"""

REPLY_PROMPT = """
You are SaiA, explaining the outcome of an executed action.
Review the command output provided by the user.
Be concise, clear, and direct in the user's language.
Plain text only, without markdown code blocks.
Never write file paths in your answer: the app shows the created file as a card by itself.
"""


class mml:
    def __init__(
        self,
        name: str = "mml",
        model: str = "gemini-3.5-flash-lite",
        max_output_tokens: int = 16384,
        max_retries: int = 3,
    ):
        self.name = name
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_retries = max(1, int(max_retries))

        self._client: Any = None
        self._client_key: Optional[str] = None
        self._tls = threading.local()  # per-thread cancel check (a cancelled request may still be running)
        if not os.getenv("AI_API_KEY"):
            logger.warning("AI_API_KEY is empty - add it from Settings > API & Gmail Keys (%s)", ENV_FILE)
        logger.info("SaiA Model Initialized | model=%s | retries=%d", self.model, self.max_retries)

    def bind_cancel(self, check: Optional[Any]) -> None:
        """Registers (or clears) a function that returns True once the current thread's request was cancelled."""
        self._tls.cancel = check

    def _raise_if_cancelled(self) -> None:
        check = getattr(self._tls, "cancel", None)
        if check is not None and check():
            raise Cancelled()

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self._raise_if_cancelled()
            time.sleep(min(0.2, max(0.0, end - time.time())))

    @property
    def client(self) -> Any:
        """Gemini client, built lazily so a key saved from Settings takes effect without a restart."""
        api_key = os.getenv("AI_API_KEY")
        if not api_key:
            raise ValueError(
                f"AI_API_KEY is missing. Add it from Settings > API & Gmail Keys (file: {ENV_FILE})"
            )
        if self._client is None or api_key != self._client_key:
            self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=180000))
            self._client_key = api_key
        return self._client

    def _get_system_context(
        self,
        role_info: Optional[Dict[str, Any]] = None,
        longterm_memories: Optional[List[Dict[str, Any]]] = None,
        past_sessions_context: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        import datetime
        import platform
        import sys

        downloads_dir = Path.home() / "Downloads"
        python_exe = "python" if getattr(sys, "frozen", False) else sys.executable

        role_section = ""
        if role_info:
            role_section = f"""
SPECIALIST ROLE ACTIVE: {role_info.get('name_en', '')} ({role_info.get('name_ar', '')})
SPECIALIST GUIDANCE:
{role_info.get('system_prompt_addon', '')}
"""

        memory_section = ""
        if longterm_memories:
            mem_items = [
                f"- [{m.get('category', 'general').upper()}] {m.get('memory_key')}: {m.get('memory_value')}"
                for m in longterm_memories
            ]
            memory_section = f"""
USER PERSISTENT LONG-TERM MEMORY (Known user facts):
{chr(10).join(mem_items)}
"""

        past_section = ""
        if past_sessions_context:
            p_lines = [
                f"{'User' if m.get('role') == 'user' else 'SaiA'}: {m.get('content', '')}"
                for m in past_sessions_context
            ]
            past_section = f"""
PAST CONVERSATIONS ARCHIVE (Historical context):
{chr(10).join(p_lines)}
"""

        return f"""
SYSTEM CONTEXT
--------------
Operating system: {platform.system()} {platform.release()}
Python executable: {python_exe}
Local time: {datetime.datetime.now().astimezone().isoformat()}
Downloads directory: {downloads_dir}
SaiA application folder: {PROJECT_ROOT}
{role_section}
{memory_section}
{past_section}
"""

    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        message = str(error).upper()
        retryable_markers = ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "TIMEOUT")
        return any(marker in message for marker in retryable_markers)

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        try:
            text = response.text
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception:
            pass
        return ""

    def _build_contents(
        self,
        user_request: str,
        file_path: Optional[str] = None,
        command: Optional[str] = None,
        output: Optional[str] = None,
        fix: bool = False,
        history: Optional[List[Dict[str, Any]]] = None,
        role_info: Optional[Dict[str, Any]] = None,
        longterm_memories: Optional[List[Dict[str, Any]]] = None,
        past_sessions_context: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Any]:
        context = self._get_system_context(
            role_info=role_info,
            longterm_memories=longterm_memories,
            past_sessions_context=past_sessions_context,
        )

        if command is not None and not fix:
            return [f"{REPLY_PROMPT}\n\n{context}\n\nUSER REQUEST:\n{user_request}\n\nCOMMAND EXECUTED:\n{command}\n\nOUTPUT:\n{(output or '')[:6000]}"]

        history_section = ""
        if history:
            turns = [f"{'User' if item.get('role') == 'user' else 'SaiA'}: {item.get('content', '')}" for item in history]
            history_section = f"\nCURRENT SESSION TURNS:\n{chr(10).join(turns[-10:])}\n"

        prompt = f"""
{context}
{history_section}
CURRENT USER REQUEST:
{user_request}

Determine the correct Mode:
- If this is a question, explanation, calculation, or chat WITHOUT asking for a file/email/system action -> Use MODE 1 (####SAIA_DIRECT####).
- If this asks to send an email, create a document/file, or run a tool -> Use MODE 3 (PowerShell command + ####SAIA_MESSAGE####).
  Make sure Line 1 of the command is pure executable code (NEVER add markdown fences or conversational phrases before it).
"""
        if fix:
            prompt += f"\nPREVIOUS COMMAND FAILED:\n{command}\n\nERROR OUTPUT:\n{(output or '')[:3000]}\n"

        contents: List[Any] = [prompt]
        if file_path and ATTACH_OK:
            try:
                parts, manifest = build_attachment_contents(file_path)
                if manifest:
                    contents[0] = contents[0] + manifest
                contents.extend(parts)
                file_path = None  # handled (one file or many)
            except Exception as exc:
                logger.warning("attachment processing failed: %s", exc)
                if not isinstance(file_path, (str, Path)):
                    file_path = None
        if file_path:
            p = Path(file_path)
            if p.exists():
                suffix = p.suffix.lower()
                if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    try:
                        with Image.open(p) as img:
                            contents.append(img.copy())
                        contents.append(f"\nAttached image located at: {p}\n")
                    except Exception:
                        pass
                elif suffix in {".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yaml", ".md", ".csv"}:
                    try:
                        text_content = p.read_text(encoding="utf-8", errors="replace")[:50000]
                        contents.append(f"\nFILE: {p.name}\nCONTENT:\n{text_content}")
                    except Exception:
                        pass
        return contents

    def generate_command(
        self,
        user_request: str,
        file_path: Optional[str] = None,
        command: Optional[str] = None,
        output: Optional[str] = None,
        fix: bool = False,
        media: Optional[str] = None,
        media_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        role_info: Optional[Dict[str, Any]] = None,
        longterm_memories: Optional[List[Dict[str, Any]]] = None,
        past_sessions_context: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        if not isinstance(user_request, str) or not user_request.strip():
            raise ValueError("user_request cannot be empty.")

        if media is not None:
            prompt_text = (media_prompt or user_request).strip()
            downloads_dir = Path.home() / "Downloads"
            stamp = time.strftime("%Y%m%d_%H%M%S")
            last_err = None
            for model_name in MEDIA_MODELS.get(media, ()):
                self._raise_if_cancelled()
                try:
                    if media == "audio":
                        interaction = self.client.interactions.create(
                            model=model_name,
                            input=[{"type": "user_input", "content": [{"type": "text", "text": prompt_text}]}],
                            response_format={"type": "audio"},
                            generation_config={"speech_config": [{"voice": get_media_voice()}]},
                        )
                        target = downloads_dir / f"SaiA_audio_{stamp}.wav"
                        target.write_bytes(base64.b64decode(interaction.output_audio.data))
                        return str(target)
                    else:
                        interaction = self.client.interactions.create(model=model_name, input=prompt_text)
                        target = downloads_dir / f"SaiA_image_{stamp}.png"
                        target.write_bytes(base64.b64decode(interaction.output_image.data))
                        return str(target)
                except Exception as exc:
                    last_err = exc
            raise RuntimeError(f"Media generation failed: {last_err}")

        is_reply_mode = command is not None and not fix
        contents = self._build_contents(
            user_request=user_request,
            file_path=file_path,
            command=command,
            output=output,
            fix=fix,
            history=history,
            role_info=role_info,
            longterm_memories=longterm_memories,
            past_sessions_context=past_sessions_context,
        )

        system_instruction = REPLY_PROMPT if is_reply_mode else SYSTEM_PROMPT
        models_to_try = [self.model]
        if FALLBACK_MODEL and FALLBACK_MODEL != self.model:
            models_to_try.append(FALLBACK_MODEL)

        last_error = None
        for current_model in models_to_try:
            temp = 0.2 if is_reply_mode else 0.0
            for attempt in range(1, self.max_retries + 1):
                self._raise_if_cancelled()
                try:
                    response = self.client.models.generate_content(
                        model=current_model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=temp,
                            max_output_tokens=self.max_output_tokens,
                        ),
                    )
                    text = self._extract_response_text(response)
                    if text:
                        self._raise_if_cancelled()
                        return text
                except Exception as exc:
                    last_error = exc
                    if attempt < self.max_retries and self._is_retryable_error(exc):
                        self._sleep(2 ** attempt)
                        continue
                    break

        raise RuntimeError(f"Failed to generate response: {last_error}") from last_error
