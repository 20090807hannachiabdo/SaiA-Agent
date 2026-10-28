"""
SaiA Execution Workflow & Scheduler
Handles intelligent direct responses (Q&A/chat without creating files),
PowerShell command sanitization (stripping conversational prefixes and fences),
and automated execution for emails and documents.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, List, Optional

from core.api_saia import SAIA_DATA_DIR, STEP_MARKER, Cancelled, extract_file_paths, mml
from storage.database import Database

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOAD_MARKERS = ("yt_dlp", "yt-dlp", "urlretrieve", "urlopen", "invoke-webrequest", "start-bitstransfer")
DEFAULT_TIMEOUT = 180
DOWNLOAD_TIMEOUT = 1200


def clean_powershell_command(raw_command: str) -> str:
    """Strips conversational text, markdown fences, and isolates pure PowerShell code."""
    cmd = raw_command.strip()

    # 1. Strip markdown fences if present
    if "```" in cmd:
        lines = cmd.split("\n")
        filtered = []
        inside = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                inside = not inside
                continue
            if inside:
                filtered.append(line)
        if filtered:
            cmd = "\n".join(filtered).strip()

    # 2. Drop any accidental conversational introductory sentences
    lines = cmd.split("\n")
    valid_starters = (
        "$", "@", "&", "python", "try", "[", "import", "#",
        "set-", "get-", "invoke-", "start-", "new-", "remove-",
        "copy-", "move-", "test-", "write-", "ipconfig", "netsh", "taskkill"
    )
    start_idx = 0
    for idx, line in enumerate(lines):
        line_clean = line.strip().lower()
        if any(line_clean.startswith(prefix) for prefix in valid_starters):
            start_idx = idx
            break

    return "\n".join(lines[start_idx:]).strip()


class RunToken:
    """One question being processed. cancel() stops it: the model request is abandoned at its next
    checkpoint and a running PowerShell command (with its child processes) is killed."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def check(self) -> None:
        if self.event.is_set():
            raise Cancelled()

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self.proc = proc
        if self.event.is_set():
            self._kill(proc)

    def cancel(self) -> None:
        self.event.set()
        with self._lock:
            proc = self.proc
        if proc is not None:
            threading.Thread(target=self._kill, args=(proc,), daemon=True).start()

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                proc.kill()
        except Exception:
            pass


class ai_agent:
    def __init__(
        self,
        name: str = "SaiA Agent",
        role_id: str = "student",
        session_id: Optional[str] = None,
    ):
        self.name = name
        self.role_id = role_id
        self.db = Database()
        self.db.create_database()
        self.session_id = session_id or self.db.create_session("محادثة جديدة", role_id)
        self.mml = mml()
        self._emergency_aborted = False
        self._run: Optional[RunToken] = None

    def cancel(self) -> None:
        """Cancels the question that is being processed right now (safe to call from the UI thread)."""
        run = self._run
        if run is not None:
            run.cancel()

    def set_session(self, session_id: str, role_id: Optional[str] = None) -> None:
        self.session_id = session_id
        if role_id:
            self.role_id = role_id

    @staticmethod
    def _emit(on_status: Optional[Callable[[str], None]], text: str) -> None:
        if on_status and text:
            try:
                on_status(text)
            except Exception:
                pass

    def _monitor_mouse_failsafe(self, stop_event: threading.Event, process: subprocess.Popen) -> None:
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        screen_width = user32.GetSystemMetrics(0)
        while not stop_event.is_set():
            user32.GetCursorPos(ctypes.byref(pt))
            if pt.x >= screen_width - 8:
                self._emergency_aborted = True
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
                except Exception:
                    pass
                break
            time.sleep(0.06)

    def _collect_output(
        self,
        process: subprocess.Popen,
        timeout: int,
        on_status: Optional[Callable[[str], None]] = None,
        steps: Optional[List[str]] = None,
        run: Optional[RunToken] = None,
    ) -> str:
        errors: List[str] = []
        err_reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
        err_reader.start()

        stop_failsafe = threading.Event()
        failsafe_thread = threading.Thread(target=self._monitor_mouse_failsafe, args=(stop_failsafe, process), daemon=True)
        failsafe_thread.start()

        timed_out: List[bool] = []

        def stop_process():
            timed_out.append(True)
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
                process.kill()
            except Exception:
                pass

        timer = threading.Timer(timeout, stop_process)
        timer.daemon = True
        timer.start()

        lines: List[str] = []
        try:
            for line in process.stdout:
                text = line.strip()
                if text.startswith(STEP_MARKER):
                    step = text[len(STEP_MARKER):].strip()
                    if step:
                        if steps is not None:
                            steps.append(step)
                        self._emit(on_status, step)
                else:
                    lines.append(line)
            process.wait()
        finally:
            timer.cancel()
            stop_failsafe.set()

        err_reader.join(timeout=3)
        if run is not None and run.cancelled:
            raise Cancelled()
        if self._emergency_aborted:
            self._emergency_aborted = False
            return "Error executing command: [EMERGENCY FAIL-SAFE TRIGGERED] تم إيقاف الأمر بحركة طوارئ الفأرة."
        if timed_out:
            return f"Error: The command did not complete within {timeout} seconds."
        if process.returncode != 0:
            return f"Error executing command: {''.join(errors)}"
        return "".join(lines)

    def execute_command(
        self,
        command: str,
        on_status: Optional[Callable[[str], None]] = None,
        steps: Optional[List[str]] = None,
        run: Optional[RunToken] = None,
    ) -> str:
        command = clean_powershell_command(command)
        if not command:
            return "Error executing command: Empty or non-executable command."

        checked = command.replace("\\", "/").lower()
        for exe in {sys.executable, "python"}:
            checked = checked.replace(exe.replace("\\", "/").lower(), "")
        if any(root.replace("\\", "/").lower() in checked for root in (APP_ROOT, str(SAIA_DATA_DIR))):
            return f"Error executing command: Forbidden application folder access. Target Downloads: {DOWNLOADS}."

        timeout = DOWNLOAD_TIMEOUT if any(m in command.lower() for m in DOWNLOAD_MARKERS) else DEFAULT_TIMEOUT
        script = os.path.join(tempfile.gettempdir(), "saia_command.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(
                "try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}\n"
                "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                "$env:PYTHONUTF8 = '1'\n"
                "$env:PYTHONIOENCODING = 'utf-8'\n"
                "$env:PYTHONUNBUFFERED = '1'\n"
                "$ErrorActionPreference = 'Stop'\n"
                + command
                + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE }\n"
            )

        try:
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                cwd=DOWNLOADS, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            return f"Error executing command: {exc}"

        if run is not None:
            run.attach(process)
        return self._collect_output(process, timeout, on_status, steps, run)

    def run_workflow(
        self,
        user_input: str,
        file_path: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Answers one question. Raises Cancelled when agent.cancel() was called meanwhile."""
        run = RunToken()
        self._run = run
        self.mml.bind_cancel(run.event.is_set)
        try:
            return self._run_workflow(user_input, file_path, on_status, run)
        finally:
            self.mml.bind_cancel(None)
            if self._run is run:
                self._run = None

    def _run_workflow(
        self,
        user_input: str,
        file_path: Optional[str],
        on_status: Optional[Callable[[str], None]],
        run: RunToken,
    ) -> str:
        self._emit(on_status, "@preparing")

        # 1. Regex memory extraction for user facts
        m_age = re.search(r"(?:عمري|عمرِي)\s*(?:هو)?\s*[:=]?\s*(\d+)(?:\s*سنة|\s*عام)?", user_input)
        if m_age:
            self.db.set_longterm_memory("user_age", f"{m_age.group(1)} سنة", category="personal", importance=5)

        # 2. Dynamic Auto-Naming
        current_history = self.db.get_session_history(self.session_id, limit=2)
        if len(current_history) <= 1:
            clean_first_line = user_input.strip().split("\n")[0]
            dynamic_title = clean_first_line[:32] + ("..." if len(clean_first_line) > 32 else "")
            self.db.update_session_title(self.session_id, dynamic_title)

        # 3. Detect historical context queries
        is_asking_past = any(
            kw in user_input.lower()
            for kw in (
                "السابقة", "السابق", "قبل", "تتذكر", "فاكر", "قلتلك", "سابقا",
                "عمري", "اسمي", "previous", "earlier", "remember", "past", "last chat"
            )
        )
        past_sessions_context = self.db.get_past_sessions_history(self.session_id, limit=14) if is_asking_past else []

        history = self.db.get_session_history(self.session_id, limit=8)
        role_info = self.db.get_role(self.role_id)
        memories = self.db.get_longterm_memories(limit=15)

        stored_path = " | ".join(map(str, file_path)) if isinstance(file_path, (list, tuple)) else file_path
        self.db.save_message(self.session_id, "user", user_input, stored_path)

        raw = self.mml.generate_command(
            user_request=user_input,
            file_path=file_path,
            history=history,
            role_info=role_info,
            longterm_memories=memories,
            past_sessions_context=past_sessions_context,
        ).strip()
        run.check()  # cancelled while the model was answering: save nothing

        # Parse memory tags if returned
        if "####SAIA_MEMORY####" in raw:
            raw, _, mem_block = raw.partition("####SAIA_MEMORY####")
            raw = raw.strip()
            try:
                mem_dict = json.loads(mem_block.strip())
                if isinstance(mem_dict, dict):
                    for k, v in mem_dict.items():
                        self.db.set_longterm_memory(str(k), str(v), category="personal")
            except Exception:
                pass

        # CASE 1: Direct Chat Response
        if raw.startswith("####SAIA_DIRECT####"):
            direct_ans = raw[len("####SAIA_DIRECT####"):].strip()
            self.db.save_message(self.session_id, "assistant", direct_ans)
            return direct_ans

        # CASE 2: Media generation
        if raw.startswith("####SAIA_MEDIA####"):
            self._emit(on_status, "@media")
            head, _, media_message = raw[len("####SAIA_MEDIA####"):].partition("####SAIA_MESSAGE####")
            media_kind, _, media_prompt = head.strip().partition("\n")
            media_path = self.mml.generate_command(
                user_input, file_path=file_path, media=media_kind.strip().lower(), media_prompt=media_prompt.strip()
            )
            result_text = f"{media_message.strip()}\n\n{media_path}".strip()
            self.db.save_message(self.session_id, "assistant", result_text)
            return result_text

        # CASE 3: Fallback plain conversational text
        if "####SAIA_MESSAGE####" not in raw and not any(
            marker in raw.lower() for marker in ("powershell", "write-output", "set-content", "@'", "python", "get-", "test-", "$")
        ):
            self.db.save_message(self.session_id, "assistant", raw)
            return raw

        # CASE 4: Action/PowerShell Execution
        command, _, message = raw.partition("####SAIA_MESSAGE####")
        command = clean_powershell_command(command)

        if command:
            steps: List[str] = []
            execution_result = self.execute_command(command, on_status, steps, run)
            for _ in range(2):
                if not (execution_result or "").startswith("Error executing command"):
                    break
                self._emit(on_status, "@fixing")
                try:
                    fixed, _, fixed_msg = self.mml.generate_command(
                        user_input, file_path=file_path, command=command, output=execution_result, fix=True
                    ).partition("####SAIA_MESSAGE####")
                except Exception:
                    break
                fixed_cleaned = clean_powershell_command(fixed)
                if not fixed_cleaned:
                    break
                command, message = fixed_cleaned, fixed_msg
                execution_result = self.execute_command(command, on_status, steps, run)

            execution_result = execution_result or ""
            failed = execution_result.startswith("Error")
            verified = "SAIA_OK" in execution_result and not failed
            execution_result = execution_result.replace("SAIA_OK", "").strip()

            produced = extract_file_paths(execution_result)
            files_block = "\n".join(produced)
            leftover = execution_result
            for p in produced:
                leftover = leftover.replace(p, "")

            if message.strip() and verified and not leftover.strip():
                resp = message.strip() + (f"\n\n{files_block}" if files_block else "")
                self.db.save_message(self.session_id, "assistant", resp)
                return resp

            status = "STATUS: VERIFIED" if verified else "STATUS: NOT VERIFIED"
            self._emit(on_status, "@finishing")
            try:
                final_res = self.mml.generate_command(user_input, command=command, output=f"{status}\n{execution_result}")
            except Exception:
                final_res = f"Action completed with errors: {execution_result[:300]}" if failed else execution_result or "Task completed."

            for p in produced:
                if p not in final_res:
                    final_res = f"{final_res}\n\n{p}"

            self.db.save_message(self.session_id, "assistant", final_res)
            return final_res
        else:
            resp = raw
            self.db.save_message(self.session_id, "assistant", resp)
            return resp
