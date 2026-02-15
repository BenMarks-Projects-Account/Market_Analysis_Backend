from pathlib import Path
import subprocess
import os
import sys
import socket
import webbrowser
import shutil
import tkinter as tk
from tkinter import messagebox
import threading
import time


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def resource_path(name: str) -> Path:
    return base_dir() / name


def _is_backend_root(path: Path) -> bool:
    try:
        return (
            (path / "start_backend.ps1").exists()
            and (path / "requirements.txt").exists()
            and (path / "app" / "main.py").exists()
        )
    except Exception:
        return False


def find_project_root() -> Path | None:
    anchors = []
    try:
        anchors.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    anchors.append(base_dir())
    anchors.append(Path.cwd())

    seen = set()
    for anchor in anchors:
        if not anchor:
            continue
        for parent in (anchor, *anchor.parents):
            key = str(parent).lower()
            if key in seen:
                continue
            seen.add(key)
            if _is_backend_root(parent):
                return parent
    return None


# Prepare a log file for backend stdout/stderr
log_file = resource_path("backend.log")
try:
    log_fh = open(log_file, "a", encoding="utf-8")
except Exception:
    log_fh = None


# Determine project root (folder containing start_backend.ps1, requirements, app/main.py)
project_root = find_project_root()


# Locate start_backend.ps1: prefer repository copy, then base_dir, then bundled _internal
script_path = None
cwd = None
if project_root and (project_root / "start_backend.ps1").exists():
    script_path = project_root / "start_backend.ps1"
    cwd = str(project_root)
else:
    candidate = resource_path("start_backend.ps1")
    if candidate.exists() and (candidate.parent / "requirements.txt").exists():
        script_path = candidate
        cwd = str(candidate.parent)
    else:
        # search under base_dir (useful for PyInstaller _internal placement)
        found = list(base_dir().rglob("start_backend.ps1"))
        for found_script in found:
            parent = found_script.parent
            if (parent / "requirements.txt").exists():
                script_path = found_script
                cwd = str(parent)
                break
        if script_path is None and found:
            script_path = found[0]
            cwd = str(found[0].parent)

if script_path is None or not script_path.exists():
    messagebox.showerror("Error", f"start_backend.ps1 not found. Checked project and bundle locations.")
    if log_fh:
        log_fh.close()
    sys.exit(1)


try:
    if log_fh:
        log_fh.write(f"Starting backend using script: {script_path} (cwd={cwd})\n")
        log_fh.flush()
    proc = subprocess.Popen([
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path)
    ], stdout=log_fh or subprocess.DEVNULL, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, cwd=cwd)
except Exception as e:
    messagebox.showerror("Error", f"Failed to start backend: {e}")
    if log_fh:
        log_fh.close()
    raise


# Open Chrome (or fallback browser) once the backend is listening on 127.0.0.1:5000
def open_chrome_when_ready(url: str = "http://127.0.0.1:5000/", timeout: float = 20.0):
    end = time.time() + float(timeout)
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", 5000), timeout=1):
                break
        except Exception:
            time.sleep(0.4)
    else:
        if log_fh:
            try:
                log_fh.write(f"Timed out waiting for backend at {url}\n")
                log_fh.flush()
            except Exception:
                pass
        return

    chrome_cmd = None
    try:
        if shutil.which("chrome"):
            chrome_cmd = ["chrome", url]
        elif shutil.which("google-chrome"):
            chrome_cmd = ["google-chrome", url]
        else:
            possible = [
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
            ]
            for p in possible:
                if p and os.path.exists(p):
                    chrome_cmd = [p, url]
                    break

        if chrome_cmd:
            subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        else:
            webbrowser.open(url, new=2)

        if log_fh:
            try:
                log_fh.write(f"Opened browser to {url}\n")
                log_fh.flush()
            except Exception:
                pass
    except Exception as e:
        if log_fh:
            try:
                log_fh.write(f"Failed to open browser: {e}\n")
                log_fh.flush()
            except Exception:
                pass


# Start a background thread to open the browser once the server is ready
try:
    t = threading.Thread(target=open_chrome_when_ready, kwargs={"url": "http://127.0.0.1:5000/", "timeout": 25.0}, daemon=True)
    t.start()
except Exception:
    pass


# Minimal Tkinter GUI to show status and allow stopping
root = tk.Tk()
root.title("Market Analysis Backend")
root.geometry("360x140")

status_var = tk.StringVar(value=("Running" if proc.poll() is None else "Stopped"))
status_label = tk.Label(root, textvariable=status_var, font=(None, 14))
status_label.pack(pady=12)


def stop_backend():
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    # Also ensure any backend python/uvicorn processes started by the script are killed
    try:
        # Use PowerShell to list matching PIDs, then force-kill them with taskkill
        ps_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'start_backend.ps1' -or $_.CommandLine -match 'uvicorn' -or $_.CommandLine -match 'app.main:app') } | Select-Object -ExpandProperty ProcessId"
        ]
        res = subprocess.run(ps_cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                pid = line.strip()
                if pid.isdigit():
                    try:
                        subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if log_fh:
                            log_fh.write(f"Killed backend PID {pid}\n")
                            log_fh.flush()
                    except Exception:
                        pass
    except Exception:
        pass

    status_var.set("Stopped")
    if log_fh:
        log_fh.close()
    root.destroy()


stop_btn = tk.Button(root, text="Stop backend", command=stop_backend, width=22)
stop_btn.pack()


def monitor_proc():
    while True:
        rc = proc.poll()
        if rc is not None:
            status_var.set(f"Stopped (code {rc})")
            if log_file.exists():
                messagebox.showerror("Backend stopped", f"Backend exited with code {rc}. See log: {log_file}")
            else:
                messagebox.showerror("Backend stopped", f"Backend exited with code {rc}.")
            break
        time.sleep(1)


mon_thread = threading.Thread(target=monitor_proc, daemon=True)
mon_thread.start()


def on_close():
    stop_backend()


root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
