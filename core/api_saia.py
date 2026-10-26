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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("SaiA")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / "api_keys.env"
load_dotenv(ENV_FILE)

MEDIA_MODELS = {
    "audio": ("gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"),
    "image": ("gemini-2.5-flash-image", "gemini-3.1-flash-lite-image", "gemini-nano-banana-2.1"),
}
MEDIA_VOICE = "Kore"
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
- Check deliverables exist and are non-empty. Only on complete success, print: SAIA_OK.
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

        api_key = os.getenv("AI_API_KEY")
        if not api_key:
            raise ValueError(f"AI_API_KEY was not found in: {ENV_FILE}")

        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=180000))
        logger.info("SaiA Model Initialized | model=%s | retries=%d", self.model, self.max_retries)

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
                try:
                    if media == "audio":
                        interaction = self.client.interactions.create(
                            model=model_name,
                            input=[{"type": "user_input", "content": [{"type": "text", "text": prompt_text}]}],
                            response_format={"type": "audio"},
                            generation_config={"speech_config": [{"voice": MEDIA_VOICE}]},
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
                        return text
                except Exception as exc:
                    last_error = exc
                    if attempt < self.max_retries and self._is_retryable_error(exc):
                        time.sleep(2 ** attempt)
                        continue
                    break

        raise RuntimeError(f"Failed to generate response: {last_error}") from last_error
