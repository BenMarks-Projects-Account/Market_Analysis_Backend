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


# ── Single-instance guard (bind a localhost port) ────────────────────────────
_instance_lock_sock = None

def _acquire_single_instance(port: int = 55123) -> bool:
    """Return True if we are the only launcher running."""
    global _instance_lock_sock
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        _instance_lock_sock = s          # prevent GC from closing the socket
        return True
    except OSError:
        return False

if not _acquire_single_instance():
    # Another launcher is already running — exit silently
    try:
        root_warn = tk.Tk()
        root_warn.withdraw()
        messagebox.showinfo("BenTrade", "BenTrade Launcher is already running.")
        root_warn.destroy()
    except Exception:
        pass
    sys.exit(0)


# ── Process-wide browser-launch guard ────────────────────────────────────────
_browser_launched = False
_browser_lock = threading.Lock()


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
    """Locate the backend root directory (the folder with start_backend.ps1,
    requirements.txt, and app/main.py).

    Strategy:
      1. Walk UP from each anchor checking each parent directly.
      2. At each parent, also check well-known child sub-paths where the
         backend lives (handles the case where the EXE is in dist/launcher/
         but the backend is in a sibling tree like BenTrade/backend/).
    """
    _CHILD_CANDIDATES = [
        Path("BenTrade", "backend"),
        Path("backend"),
    ]

    anchors: list[Path] = []
    try:
        anchors.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    anchors.append(base_dir())
    anchors.append(Path.cwd())

    seen: set[str] = set()
    for anchor in anchors:
        if not anchor:
            continue
        for parent in (anchor, *anchor.parents):
            key = str(parent).lower()
            if key in seen:
                continue
            seen.add(key)
            # Direct check
            if _is_backend_root(parent):
                return parent
            # Check known child sub-paths
            for child in _CHILD_CANDIDATES:
                candidate = parent / child
                if _is_backend_root(candidate):
                    return candidate
    return None


# Prepare a log file for backend stdout/stderr
log_file = resource_path("backend.log")
try:
    log_fh = open(log_file, "a", encoding="utf-8")
except Exception:
    log_fh = None

def _log(msg: str):
    if log_fh:
        try:
            log_fh.write(f"{msg}\n")
            log_fh.flush()
        except Exception:
            pass


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
        # Do NOT use a bundled-only script that lacks requirements.txt / app —
        # it will always fail.  Fall through to the error below instead.

if script_path is None or not script_path.exists():
    messagebox.showerror("Error", f"start_backend.ps1 not found. Checked project and bundle locations.")
    if log_fh:
        log_fh.close()
    sys.exit(1)


# ── Diagnostics ──────────────────────────────────────────────────────────────
_log(f"[diag] frozen={getattr(sys, 'frozen', False)}")
_log(f"[diag] base_dir={base_dir()}")
_log(f"[diag] cwd={Path.cwd()}")
_log(f"[diag] project_root={project_root}")
_log(f"[diag] script_path={script_path}")
_log(f"[diag] backend_cwd={cwd}")
try:
    _cwd_files = ", ".join(sorted(p.name for p in Path(cwd).iterdir())[:20])
    _log(f"[diag] cwd contents (first 20): {_cwd_files}")
except Exception:
    pass


_CREATE_NO_WINDOW = 0x08000000

_si = subprocess.STARTUPINFO()
_si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
_si.wShowWindow = 0  # SW_HIDE

try:
    _log(f"Starting backend using script: {script_path} (cwd={cwd})")
    proc = subprocess.Popen([
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path)
    ], stdout=log_fh or subprocess.DEVNULL, stderr=subprocess.STDOUT,
       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW,
       startupinfo=_si, cwd=cwd)
except Exception as e:
    messagebox.showerror("Error", f"Failed to start backend: {e}")
    if log_fh:
        log_fh.close()
    raise


# ── Chrome / Edge discovery helpers ──────────────────────────────────────────

def _find_chrome_via_registry() -> str | None:
    """Try to locate chrome.exe from the Windows registry."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    reg_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
    ]
    for hive, sub_key in reg_paths:
        try:
            with winreg.OpenKey(hive, sub_key) as key:
                val, _ = winreg.QueryValueEx(key, "")
                if val and os.path.isfile(val):
                    return str(val)
        except OSError:
            continue
    return None


def _find_chrome() -> str | None:
    reg = _find_chrome_via_registry()
    if reg:
        return reg
    possible = [
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in possible:
        if p and os.path.isfile(p):
            return p
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _find_edge() -> str | None:
    possible = [
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for p in possible:
        if p and os.path.isfile(p):
            return p
    for name in ("msedge", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            return p
    return None


# ── Browser launch (exactly once) ───────────────────────────────────────────

def open_chrome_when_ready(url: str = "http://127.0.0.1:5000/", timeout: float = 25.0):
    global _browser_launched

    # Wait for the backend to be reachable
    end = time.time() + float(timeout)
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", 5000), timeout=1):
                break
        except Exception:
            time.sleep(0.4)
    else:
        _log(f"Timed out waiting for backend at {url}")
        return

    # ── Guard: only launch once across all threads ──
    with _browser_lock:
        if _browser_launched:
            _log("Browser launch skipped — already launched")
            return
        _browser_launched = True

    # ── Simple browser open — one normal Chrome window, no kiosk/app/fullscreen ──
    chrome_exe = _find_chrome()
    edge_exe = _find_edge()
    exe = chrome_exe or edge_exe
    _log(f"Chrome discovery: {chrome_exe or '(not found)'}")
    _log(f"Edge discovery:   {edge_exe or '(not found)'}")

    try:
        if exe:
            cmd = [exe, "--new-window", url]
            _log(f"Opening browser: {' '.join(cmd)}")
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        else:
            _log(f"No Chrome/Edge found — falling back to default browser for {url}")
            webbrowser.open(url, new=2)
        _log("Browser launch succeeded")
    except Exception as e:
        _log(f"Browser launch failed: {e}")
        try:
            webbrowser.open(url, new=2)
            _log("Fallback webbrowser.open succeeded")
        except Exception:
            pass


# Start a background thread to open the browser once the server is ready
try:
    t = threading.Thread(target=open_chrome_when_ready, kwargs={"url": "http://127.0.0.1:5000/", "timeout": 25.0}, daemon=True)
    t.start()
except Exception:
    pass


# ── BenTrade Quantum Neon Launcher ───────────────────────────────────────────

BG_COLOR      = "#0a0c1a"
BG_DARK       = "#050818"
ACCENT_CYAN   = "#00e0ff"
ACCENT_DIM    = "#00b0cc"
GLOW_OUTER    = "#002838"
GLOW_MID      = "#005570"
TEXT_PRIMARY  = "#e6fbff"
TEXT_MUTED    = "#8899aa"
BRAND_BAR_BG  = "#0d1020"
GREEN_LIVE    = "#00ff88"
RED_DEAD      = "#ff4444"

W, H = 560, 280

root = tk.Tk()
root.title("BenTrade Launcher")
root.geometry(f"{W}x{H}")
root.configure(bg=BG_COLOR)
root.resizable(False, False)

# ── Canvas — all rendering on a single surface ──────────────────────────────
canvas = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0)
canvas.pack(fill="both", expand=True)

# Gradient background (top-to-bottom subtle fade)
_GRAD = 80
for _i in range(_GRAD):
    _t = _i / _GRAD
    _r = int(10 * (1 - _t) + 5 * _t)
    _g = int(12 * (1 - _t) + 8 * _t)
    _b = int(26 * (1 - _t) + 24 * _t)
    _c = f"#{max(0,_r):02x}{max(0,_g):02x}{max(0,_b):02x}"
    _y0 = int(H * _i / _GRAD)
    _y1 = int(H * (_i + 1) / _GRAD) + 1
    canvas.create_rectangle(0, _y0, W, _y1, fill=_c, outline="")

# Scanline overlay (subtle CRT / holo effect)
for _sy in range(0, H, 3):
    canvas.create_rectangle(0, _sy, W, _sy + 1, fill="#020410", outline="",
                            stipple="gray25")

# ── Brand bar ────────────────────────────────────────────────────────────────
canvas.create_rectangle(0, 0, W, 46, fill=BRAND_BAR_BG, outline="")
canvas.create_line(0, 46, W, 46, fill=ACCENT_CYAN, width=1)

# BT badge
canvas.create_rectangle(16, 9, 52, 37, fill=ACCENT_CYAN, outline="")
canvas.create_text(34, 23, text="BT", font=("Segoe UI", 12, "bold"),
                   fill=BG_COLOR, anchor="center")

# BENTRADE wordmark
canvas.create_text(62, 23, text="BENTRADE",
                   font=("Segoe UI", 13, "bold"), fill=ACCENT_CYAN, anchor="w")

# Right-side tagline
canvas.create_text(W - 16, 23, text="Launcher",
                   font=("Consolas", 9), fill=TEXT_MUTED, anchor="e")


# ── Glow text helper ────────────────────────────────────────────────────────
def _glow_text(x, y, text, font, glow=GLOW_OUTER, mid=GLOW_MID,
               fg=ACCENT_CYAN, anchor="center"):
    """Canvas text with layered neon glow."""
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2),
                   (-2, -2), (2, -2), (-2, 2), (2, 2)):
        canvas.create_text(x + dx, y + dy, text=text, font=font,
                           fill=glow, anchor=anchor)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        canvas.create_text(x + dx, y + dy, text=text, font=font,
                           fill=mid, anchor=anchor)
    return canvas.create_text(x, y, text=text, font=font, fill=fg,
                              anchor=anchor)


# ── Header (glowing) ────────────────────────────────────────────────────────
_header_id = _glow_text(W // 2, 84, "BenTrade Intelligence is Running",
                        ("Segoe UI", 16, "bold"))

# Subheader
canvas.create_text(W // 2, 114, text="Your Desk. Your Rules. Wall Street In Your Pocket",
                   font=("Segoe UI", 11), fill=TEXT_MUTED, anchor="center")

# ── Separator ────────────────────────────────────────────────────────────────
canvas.create_line(50, 136, W - 50, 136, fill="#1a2a3a", width=1)

# ── Status indicators ───────────────────────────────────────────────────────
_backend_sid = canvas.create_text(W // 2 - 90, 156,
                                  text="Backend: Starting\u2026",
                                  font=("Consolas", 10), fill=TEXT_PRIMARY,
                                  anchor="center")
_ui_sid = canvas.create_text(W // 2 + 90, 156,
                             text="UI: Waiting\u2026",
                             font=("Consolas", 10), fill=TEXT_PRIMARY,
                             anchor="center")


# ── Button factory ───────────────────────────────────────────────────────────
def _make_btn(text, command, bg=ACCENT_CYAN, fg=BG_COLOR, w=16):
    return tk.Button(root, text=text, command=command,
                     font=("Segoe UI", 10, "bold"), fg=fg, bg=bg,
                     activebackground="#004d66", activeforeground=TEXT_PRIMARY,
                     relief=tk.FLAT, cursor="hand2", highlightthickness=0,
                     bd=0, padx=10, pady=5, width=w)


# ── Open Logs ────────────────────────────────────────────────────────────────
def _open_logs():
    try:
        if sys.platform == "win32":
            os.startfile(str(log_file))
        else:
            subprocess.Popen(["xdg-open", str(log_file)])
    except Exception as e:
        _log(f"Failed to open log: {e}")


# ── Stop function ────────────────────────────────────────────────────────────
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
    try:
        ps_cmd = [
            "powershell", "-NoProfile", "-Command",
            ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
             "($_.CommandLine -match 'start_backend.ps1' -or "
             "$_.CommandLine -match 'uvicorn' -or "
             "$_.CommandLine -match 'app.main:app') } | "
             "Select-Object -ExpandProperty ProcessId"),
        ]
        res = subprocess.run(ps_cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                pid = line.strip()
                if pid.isdigit():
                    try:
                        subprocess.run(["taskkill", "/PID", pid, "/F", "/T"],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                        _log(f"Killed backend PID {pid}")
                    except Exception:
                        pass
    except Exception:
        pass

    canvas.itemconfig(_backend_sid, text="Backend: Stopped", fill=RED_DEAD)
    if log_fh:
        log_fh.close()
    root.destroy()


# Place buttons via canvas windows
_stop_btn = _make_btn("Stop BenTrade", stop_backend, bg=ACCENT_CYAN)
_logs_btn = _make_btn("Open Logs", _open_logs, bg="#1a2a3a",
                      fg=TEXT_PRIMARY, w=12)
canvas.create_window(W // 2 - 74, 210, window=_stop_btn, anchor="center")
canvas.create_window(W // 2 + 74, 210, window=_logs_btn, anchor="center")

# ── Bottom accent line ───────────────────────────────────────────────────────
canvas.create_line(0, H - 3, W, H - 3, fill=ACCENT_CYAN, width=1)

# ── Pulse animation (header glow cycles) ────────────────────────────────────
_PULSE_SEQ = [ACCENT_CYAN, "#00c8dd", "#00a8bb", "#00c8dd"]
_pulse_i = [0]


def _pulse():
    _pulse_i[0] = (_pulse_i[0] + 1) % len(_PULSE_SEQ)
    canvas.itemconfig(_header_id, fill=_PULSE_SEQ[_pulse_i[0]])
    root.after(600, _pulse)


root.after(1200, _pulse)


# ── Status polling ───────────────────────────────────────────────────────────
def _poll_status():
    if proc.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", 5000), timeout=0.3):
                canvas.itemconfig(_backend_sid, text="Backend: Online",
                                  fill=GREEN_LIVE)
        except Exception:
            canvas.itemconfig(_backend_sid, text="Backend: Starting\u2026",
                              fill=TEXT_PRIMARY)
    else:
        rc = proc.returncode
        canvas.itemconfig(_backend_sid,
                          text=f"Backend: Stopped ({rc})", fill=RED_DEAD)

    if _browser_launched:
        canvas.itemconfig(_ui_sid, text="UI: Ready", fill=GREEN_LIVE)
    else:
        canvas.itemconfig(_ui_sid, text="UI: Launching\u2026", fill=TEXT_PRIMARY)

    root.after(1500, _poll_status)


root.after(800, _poll_status)


# ── Process monitor ──────────────────────────────────────────────────────────
def monitor_proc():
    while True:
        rc = proc.poll()
        if rc is not None:
            _log(f"Backend process exited with code {rc}")
            root.after(0, lambda: canvas.itemconfig(
                _backend_sid, text=f"Backend: Stopped ({rc})", fill=RED_DEAD))
            root.after(200, lambda: messagebox.showerror(
                "Backend stopped",
                f"Backend exited with code {rc}.\nSee log: {log_file}",
            ))
            break
        time.sleep(1)


mon_thread = threading.Thread(target=monitor_proc, daemon=True)
mon_thread.start()


def on_close():
    stop_backend()


root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
