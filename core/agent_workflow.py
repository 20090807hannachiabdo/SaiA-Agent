import os
import subprocess
import sys
import tempfile
import threading
import time
from core.api_saia import mml, STEP_MARKER

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")

# The SaiA application folder: generated commands must never touch it
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Commands that download from the internet may need much longer than 180 seconds
DOWNLOAD_MARKERS = ("yt_dlp", "yt-dlp", "urlretrieve", "urlopen", "invoke-webrequest", "start-bitstransfer")
DEFAULT_TIMEOUT = 180
DOWNLOAD_TIMEOUT = 1200


class ai_agent:
    def __init__(self, name="AI Agent", role="assistant"):
        self.name = name
        self.role = role
        self.mml = mml()
    @staticmethod
    def _emit(on_status, text):
        # Reporting progress must never break the task itself
        if on_status and text:
            try:
                on_status(text)
            except Exception:
                pass
    def respond(self, user_input, file_path=None):
        return self.mml.generate_command(user_input, file_path)
    def _collect_output(self, process, timeout, on_status=None, steps=None):
        # Reads the output line by line while the command is still running:
        # SAIA_STEP lines are reported live and removed from the final output
        errors = []
        err_reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
        err_reader.start()
        timed_out = []
        def stop_process():
            timed_out.append(True)
            try:
                if sys.platform == "win32":
                    # Also stops the child processes (for example the Python interpreter)
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
                process.kill()
            except Exception:
                pass
        timer = threading.Timer(timeout, stop_process)
        timer.daemon = True
        timer.start()
        lines = []
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
        err_reader.join(timeout=5)
        if timed_out:
            return f"Error: the command did not finish within {timeout} seconds."
        if process.returncode != 0:
            return f"Error executing command: {''.join(errors)}"
        return "".join(lines)
    def execute_command(self, command, on_status=None, steps=None):
        # Safety: refuse commands that write into the SaiA application folder itself
        # (the Python interpreter path is removed first because it may live in a venv inside it)
        checked = command.replace("\\", "/").lower()
        for exe in {sys.executable, "python"}:
            checked = checked.replace(exe.replace("\\", "/").lower(), "")
        if APP_ROOT.replace("\\", "/").lower() in checked:
            return (
                "Error executing command: the command used the SaiA application folder "
                f"({APP_ROOT}). Files and folders must be created only inside the Downloads folder: {DOWNLOADS}."
            )
        timeout = DOWNLOAD_TIMEOUT if any(m in command.lower() for m in DOWNLOAD_MARKERS) else DEFAULT_TIMEOUT
        # The command is run from a script file (no command-line length limit, Arabic text stays intact)
        script = os.path.join(tempfile.gettempdir(), "saia_command.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(
                "try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}\n"
                "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                "$env:PYTHONUTF8 = '1'\n"
                "$env:PYTHONIOENCODING = 'utf-8'\n"
                "$env:PYTHONUNBUFFERED = '1'\n"
                "$ErrorActionPreference = 'Stop'\n"
                + command + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE }\n"
            )
        try:
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                cwd=DOWNLOADS, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            return f"Error executing command: {exc}"
        return self._collect_output(process, timeout, on_status, steps)
    def run_workflow(self, user_input, file_path=None, on_status=None):
        # on_status(text) receives live progress: model steps, or "@preparing", "@fixing", "@finishing", "@media"
        self._emit(on_status, "@preparing")
        started = time.time()
        raw = self.respond(user_input, file_path).strip()
        if raw.startswith("####SAIA_MEDIA####"):
            # Image / speech / video request: made by the most suitable Gemini media model (no PowerShell)
            self._emit(on_status, "@media")
            head, _, media_message = raw[len("####SAIA_MEDIA####"):].partition("####SAIA_MESSAGE####")
            media_kind, _, media_prompt = head.strip().partition("\n")
            media_path = self.mml.generate_command(
                user_input, file_path, media=media_kind.strip().lower(), media_prompt=media_prompt.strip()
            )
            return f"{media_message.strip()}\n\n{media_path}".strip()
        command, _, message = raw.partition("####SAIA_MESSAGE####")
        command = command.strip()
        generated = time.time() - started
        if command:
            started = time.time()
            steps = []
            execution_result = self.execute_command(command, on_status, steps)
            # Self-repair: if the command failed, send the real error back to the model and retry (up to 2 times)
            for attempt in range(2):
                if not (execution_result or "").startswith("Error executing command"):
                    break
                self._emit(on_status, "@fixing")
                try:
                    fixed, sep, fixed_message = self.mml.generate_command(
                        user_input, file_path, command=command, output=execution_result, fix=True
                    ).partition("####SAIA_MESSAGE####")
                except Exception:
                    break
                if not fixed.strip():
                    break
                command, message = fixed.strip(), fixed_message
                execution_result = self.execute_command(command, on_status, steps)
            # Debug log: the last executed command, how long it took, its progress steps, and its output
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_command.txt"), "w", encoding="utf-8") as log:
                log.write(
                    f"[model: {generated:.1f}s | PowerShell: {time.time() - started:.1f}s]\n\n{command}\n\n"
                    f"--- steps ---\n" + "\n".join(steps) + f"\n\n--- output ---\n{execution_result}"
                )
            # The command prints SAIA_OK only after it verified its own result
            execution_result = execution_result or ""
            failed = execution_result.startswith("Error")
            verified = "SAIA_OK" in execution_result and not failed
            execution_result = execution_result.replace("SAIA_OK", "").strip()
            if message.strip() and verified and not execution_result:
                # Verified silent success: use the message written together with the command (no second model call)
                return message.strip()
            status = "STATUS: VERIFIED" if verified else "STATUS: NOT VERIFIED"
            self._emit(on_status, "@finishing")
            try:
                return self.mml.generate_command(user_input, command=command, output=f"{status}\n{execution_result}")
            except Exception:
                if failed:
                    return "There was a problem and the task was not completed. " + execution_result[:300]
                return (execution_result or "").strip() or "The task could not be confirmed."
        else:
            return "No command generated."
