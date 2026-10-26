"""
SaiA Autonomous Background Automation Engine
Runs flows through Windows Task Scheduler so they keep running after the GUI is closed.

Fixes vs. the previous version:
- sys.path / working directory are set, so the task can import `storage` / `core` and find database.db
- the trigger has an explicit RepetitionDuration (otherwise it can fire only once)
- PowerShell errors are no longer swallowed (return codes checked, failures written to automation.log)
- works both from source (pythonw + this file) and from a frozen exe (exe --run-task ID)
- toast uses a registered AppID so it actually shows
- pause / resume helpers so background tasks can be cancelled while the GUI is open
"""

from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Tuple

FROZEN = getattr(sys, "frozen", False)
PROJECT_ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import Database  # noqa: E402

LOG_FILE = PROJECT_ROOT / "automation.log"
TASK_PREFIX = "SaiA_AutoFlow_"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# AppID of Windows PowerShell: registered by Windows, so toasts from it are displayed.
TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_PAUSED = False  # True while the GUI is open and background tasks are suspended


def _log(message: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}\n")
    except Exception:
        pass


def get_task_scheduler_name(task_id: str) -> str:
    return f"{TASK_PREFIX}{task_id}"


def _q(value: str) -> str:
    """Escapes text for a PowerShell single-quoted string."""
    return str(value).replace("'", "''")


def _ps(script: str, timeout: int = 25) -> Tuple[bool, str]:
    full = "$ErrorActionPreference='Stop'\n" + script
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
            capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
        )
    except Exception as exc:
        _log(f"powershell launch failed: {exc}")
        return False, str(exc)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        _log(f"powershell error (code {r.returncode}): {out[:500]}")
    return r.returncode == 0, out


def _launch_command(task_id: str) -> Tuple[str, str]:
    """Returns (executable, arguments) the scheduler must run."""
    if FROZEN:
        return sys.executable, f'--run-task "{task_id}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return str(exe), f'"{Path(__file__).resolve()}" --run-task "{task_id}"'


def parse_interval_minutes(value, default: int = 60) -> int:
    """Accepts '15', '0.25', 15.0 ... and returns whole minutes >= 1 (Task Scheduler's minimum)."""
    try:
        minutes = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default
    return max(1, int(round(minutes)))


def register_windows_task(task_id: str, interval_minutes) -> bool:
    """Registers (or replaces) a repeating background task. Returns True only on real success."""
    if sys.platform != "win32":
        return False
    name = get_task_scheduler_name(task_id)
    exe, args = _launch_command(task_id)
    minutes = parse_interval_minutes(interval_minutes)
    script = "\n".join([
        f"$action = New-ScheduledTaskAction -Execute '{_q(exe)}' -Argument '{_q(args)}' -WorkingDirectory '{_q(str(PROJECT_ROOT))}'",
        f"$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        f"-RepetitionInterval (New-TimeSpan -Minutes {minutes}) -RepetitionDuration (New-TimeSpan -Days 3650)",
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)",
        f"Register-ScheduledTask -TaskName '{name}' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null",
        f"if ({'$true' if _PAUSED else '$false'}) {{ Disable-ScheduledTask -TaskName '{name}' | Out-Null }}",
    ])
    ok, _ = _ps(script)
    return ok


def unregister_windows_task(task_id: str) -> bool:
    """Stops any running instance and removes the scheduled task."""
    if sys.platform != "win32":
        return False
    name = get_task_scheduler_name(task_id)
    ok, _ = _ps(
        f"Stop-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue\n"
        f"Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false -ErrorAction SilentlyContinue"
    )
    return ok


def set_task_enabled(task_id: str, enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    name = get_task_scheduler_name(task_id)
    if enabled:
        ok, _ = _ps(f"Enable-ScheduledTask -TaskName '{name}' | Out-Null")
    else:
        ok, _ = _ps(
            f"Stop-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue\n"
            f"Disable-ScheduledTask -TaskName '{name}' | Out-Null"
        )
    return ok


def pause_all_background_tasks() -> None:
    """Call when the GUI opens: suspends every background flow (and stops a running one)."""
    global _PAUSED
    _PAUSED = True
    try:
        for task in Database().list_scheduled_tasks():
            set_task_enabled(task["id"], False)
    except Exception:
        _log("pause_all failed:\n" + traceback.format_exc())


def resume_all_background_tasks() -> None:
    """Call when the GUI closes: re-registers every active flow so it keeps running without the GUI."""
    global _PAUSED
    _PAUSED = False
    try:
        for task in Database().list_scheduled_tasks():
            if task.get("is_active", 1):
                register_windows_task(task["id"], task.get("trigger_value"))
    except Exception:
        _log("resume_all failed:\n" + traceback.format_exc())


def show_desktop_toast(title: str, message: str) -> None:
    """Displays a native Windows toast notification."""
    t = html.escape(title, quote=False)
    m = html.escape(message, quote=False)
    script = "\n".join([
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null",
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
        "$template = @'",
        f"<toast><visual><binding template='ToastGeneric'><text>{t}</text><text>{m}</text></binding></visual></toast>",
        "'@",
        "$xml.LoadXml($template)",
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_q(TOAST_APP_ID)}')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))",
    ])
    _ps(script, timeout=10)


def execute_flow_task(task_id: str) -> str:
    """Executes a single scheduled automation flow."""
    from core.agent_workflow import ai_agent

    db = Database()
    task = next((x for x in db.list_scheduled_tasks() if x["id"] == task_id), None)
    if not task:
        _log(f"Task {task_id} not found in database.")
        return f"Task {task_id} not found."

    title = task.get("title", "SaiA Flow")
    agent = ai_agent(name="SaiA Automation Runner", role_id=task.get("role_id", "student"))
    try:
        result = agent.run_workflow(task.get("prompt", "")) or ""
    finally:
        # the runner creates a throw-away chat session; remove it so history is not flooded
        try:
            agent.db.delete_session(agent.session_id)
        except Exception:
            pass

    db.update_task_execution(task_id, str(time.time()))
    _log(f"Flow '{title}' ({task_id}) finished.")
    show_desktop_toast(f"⚡ {title}", result[:180] + ("..." if len(result) > 180 else ""))
    return result


def main():
    parser = argparse.ArgumentParser(description="SaiA Automation Background Service")
    parser.add_argument("--run-task", type=str, help="Executes the specified task ID immediately")
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)  # relative paths (database.db, ...) must resolve like in the GUI
    if args.run_task:
        try:
            execute_flow_task(args.run_task)
        except Exception:
            _log(f"Flow {args.run_task} crashed:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()