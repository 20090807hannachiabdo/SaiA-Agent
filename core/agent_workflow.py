import os
import subprocess
from core.api_saia import mml

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")

class ai_agent:
    def __init__(self, name="AI Agent", role="assistant"):
        self.name = name
        self.role = role
        self.mml = mml()
    def respond(self, user_input, file_path=None):
        return self.mml.generate_command(user_input, file_path)
    def execute_command(self, command):
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=DOWNLOADS, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "Error: the command did not finish within 300 seconds."
        if result.returncode != 0:
            return f"Error executing command: {result.stderr}"
        return result.stdout
    def run_workflow(self, user_input, file_path=None):
        command = self.respond(user_input, file_path)
        if command:
            execution_result = self.execute_command(command)
            try:
                return self.mml.generate_command(user_input, command=command, output=execution_result)
            except Exception:
                return (execution_result or "").strip() or None
        else:
            return "No command generated."