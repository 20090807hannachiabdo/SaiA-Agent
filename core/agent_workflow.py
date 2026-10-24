import os
import subprocess
import tempfile
import time
from core.api_saia import mml

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")

class ai_agent:
    def __init__(self, name="AI Agent", role="assistant", is_subscribed=False):
        self.name = name
        self.role = role
        self.mml = mml(is_subscribed=is_subscribed)
    def set_subscription(self, is_subscribed):
        # Call this when the user's plan changes (paid = better image models)
        self.mml.is_subscribed = bool(is_subscribed)
    def respond(self, user_input, file_path=None):
        return self.mml.generate_command(user_input, file_path)
    def execute_command(self, command):
        # The command is run from a script file (no command-line length limit, Arabic text stays intact)
        script = os.path.join(tempfile.gettempdir(), "saia_command.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(
                "try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}\n"
                "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                "$env:PYTHONUTF8 = '1'\n"
                "$env:PYTHONIOENCODING = 'utf-8'\n"
                "$ErrorActionPreference = 'Stop'\n"
                + command + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE }\n"
            )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                cwd=DOWNLOADS, capture_output=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=180,
            )
        except subprocess.TimeoutExpired:
            return "Error: the command did not finish within 180 seconds."
        if result.returncode != 0:
            return f"Error executing command: {result.stderr}"
        return result.stdout
    def run_workflow(self, user_input, file_path=None):
        started = time.time()
        raw = self.respond(user_input, file_path).strip()
        if raw.startswith("####SAIA_MEDIA####"):
            # Image / speech / video request: made by the most suitable Gemini media model (no PowerShell)
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
            execution_result = self.execute_command(command)
            # Self-repair: if the command failed, send the real error back to the model and retry (up to 2 times)
            for attempt in range(2):
                if not (execution_result or "").startswith("Error executing command"):
                    break
                try:
                    fixed, sep, fixed_message = self.mml.generate_command(
                        user_input, file_path, command=command, output=execution_result, fix=True
                    ).partition("####SAIA_MESSAGE####")
                except Exception:
                    break
                if not fixed.strip():
                    break
                command, message = fixed.strip(), fixed_message
                execution_result = self.execute_command(command)
            # Debug log: the last executed command, how long it took, and its output
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_command.txt"), "w", encoding="utf-8") as log:
                log.write(f"[model: {generated:.1f}s | PowerShell: {time.time() - started:.1f}s]\n\n{command}\n\n--- output ---\n{execution_result}")
            if message.strip() and not (execution_result or "").strip():
                # Silent success: use the message written together with the command (no second model call)
                return message.strip()
            try:
                return self.mml.generate_command(user_input, command=command, output=execution_result)
            except Exception:
                return (execution_result or "").strip() or None
        else:
            return "No command generated."
