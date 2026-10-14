"""Safe Windows profiles for Discord, Figma, Chrome, AyuGram, and OBS Studio."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import winreg

APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "AppOptimizer"
STATE_FILE = APP_DIR / "state.json"
GPU_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
PROCESS_SET_INFORMATION, PROCESS_QUERY_INFORMATION, PROCESS_SET_QUOTA = 0x0200, 0x0400, 0x0100
NORMAL_PRIORITY_CLASS, BELOW_NORMAL_PRIORITY_CLASS = 0x20, 0x4000
PROCESS_POWER_THROTTLING, THROTTLING_VERSION, THROTTLING_EXECUTION_SPEED = 4, 1, 1
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


class PowerThrottlingState(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD), ("ControlMask", wintypes.DWORD), ("StateMask", wintypes.DWORD)]


APPS = {
    "Discord": (("Discord.exe",), True),
    "AyuGram": (("AyuGram.exe", "Telegram.exe"), True),
    "Figma": (("Figma.exe",), False),
    "Chrome": (("chrome.exe",), False),
    "OBS Studio": (("obs64.exe", "obs.exe"), False),
}


def ps(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)


def state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def pids(names: tuple[str, ...]) -> list[int]:
    condition = " OR ".join("Name = '" + name + "'" for name in names)
    output = ps(f"Get-CimInstance Win32_Process -Filter \"{condition}\" | Select-Object -ExpandProperty ProcessId").stdout
    values = []
    for line in output.splitlines():
        try: values.append(int(line.strip()))
        except ValueError: pass
    return values


def exe_paths(names: tuple[str, ...]) -> list[Path]:
    listed = ", ".join("'" + name + "'" for name in names)
    output = ps(f"Get-CimInstance Win32_Process | Where-Object {{ $_.Name -in @({listed}) }} | ForEach-Object {{ $_.ExecutablePath }}").stdout
    return [Path(line.strip()) for line in output.splitlines() if line.strip()]


def profile(pid: int, economy: bool, trim: bool = False) -> str:
    access = PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle: return f"PID {pid}: доступ запрещён"
    try:
        kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS if economy else NORMAL_PRIORITY_CLASS)
        throttling = PowerThrottlingState(THROTTLING_VERSION, THROTTLING_EXECUTION_SPEED, THROTTLING_EXECUTION_SPEED if economy else 0)
        eco = bool(kernel32.SetProcessInformation(handle, PROCESS_POWER_THROTTLING, ctypes.byref(throttling), ctypes.sizeof(throttling)))
        freed = bool(psapi.EmptyWorkingSet(handle)) if trim else False
        extra = (" EcoQoS" if eco and economy else " EcoQoS снят" if eco else "") + (", память освобождена" if freed else "")
        return f"PID {pid}: {'экономичный' if economy else 'производительный'} профиль применён.{extra}"
    finally:
        kernel32.CloseHandle(handle)


def high_gpu(paths: list[Path]) -> int:
    if not paths: return 0
    data = state(); backup = data.setdefault("gpu_backup", {})
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, GPU_KEY) as key:
        for path in paths:
            name = str(path)
            if name not in backup:
                try:
                    value, kind = winreg.QueryValueEx(key, name); backup[name] = [value, kind]
                except FileNotFoundError: backup[name] = None
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, "GpuPreference=2;")
    save(data); return len(paths)


def restore_gpu() -> int:
    data = state(); backup = data.get("gpu_backup", {})
    if not backup: return 0
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, GPU_KEY) as key:
        for path, old in backup.items():
            if old is None:
                try: winreg.DeleteValue(key, path)
                except FileNotFoundError: pass
            else: winreg.SetValueEx(key, path, 0, old[1], old[0])
    data.pop("gpu_backup", None); save(data); return len(backup)


class Optimizer(tk.Tk):
    def __init__(self) -> None:
        super().__init__(); self.title("App Optimizer — Discord, Figma, Chrome, AyuGram, OBS")
        self.geometry("760x570"); self.minsize(690, 520); self.protocol("WM_DELETE_WINDOW", self.close)
        self.running, self.stop = False, threading.Event(); self.seconds = tk.IntVar(value=60)
        self.status = tk.StringVar(value="Выберите профиль и примените его к запущенным приложениям.")
        self.enabled = {name: tk.BooleanVar(value=True) for name in APPS}; self.build()

    def build(self) -> None:
        box = ttk.Frame(self, padding=18); box.pack(fill="both", expand=True)
        ttk.Label(box, text="Оптимизация приложений", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(box, text="Discord и AyuGram получают экономичный фоновый профиль. Figma, Chrome и OBS остаются в обычном приоритете; для них можно назначить высокопроизводительную GPU Windows.", wraplength=700).pack(anchor="w", pady=(5, 12))
        apps = ttk.LabelFrame(box, text="Приложения", padding=10); apps.pack(fill="x")
        for name, value in self.enabled.items(): ttk.Checkbutton(apps, text=name, variable=value).pack(side="left", padx=(0, 22))
        actions = ttk.Frame(box); actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Применить к запущенным", command=self.apply).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Очистить память сейчас", command=lambda: self.apply(True)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Высокая GPU для Figma/Chrome/OBS", command=self.gpu).pack(side="left")
        monitor = ttk.LabelFrame(box, text="Постоянное применение профилей", padding=10); monitor.pack(fill="x")
        ttk.Label(monitor, text="Проверять каждые").pack(side="left"); ttk.Spinbox(monitor, from_=20, to=600, textvariable=self.seconds, width=6).pack(side="left", padx=6); ttk.Label(monitor, text="секунд").pack(side="left")
        self.mon = ttk.Button(monitor, text="Включить мониторинг", command=self.toggle); self.mon.pack(side="right")
        row = ttk.Frame(box); row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="Вернуть настройки GPU", command=self.undo_gpu).pack(side="left")
        ttk.Button(row, text="Открыть Chrome: Производительность", command=self.chrome).pack(side="right")
        ttk.Label(box, textvariable=self.status, foreground="#176b31", wraplength=700).pack(anchor="w", pady=(14, 6))
        self.log = tk.Text(box, height=11, state="disabled", wrap="word", font=("Consolas", 9)); self.log.pack(fill="both", expand=True)
        ttk.Label(box, text="Chrome: включите «Экономия памяти» и добавьте figma.com в исключения. Figma: оставьте WebGL/аппаратное ускорение включёнными. OBS: запустите от администратора, используйте аппаратный кодировщик и ограничьте FPS игры. Если тормозит стрим Discord — временно выключите аппаратное ускорение в Voice & Video.", wraplength=700).pack(anchor="w", pady=(9, 0))

    def selected(self) -> list[str]: return [name for name, value in self.enabled.items() if value.get()]
    def logline(self, text: str) -> None:
        self.log.configure(state="normal"); self.log.insert("end", time.strftime("[%H:%M:%S] ") + text + "\n"); self.log.see("end"); self.log.configure(state="disabled")
    def apply(self, trim: bool = False) -> None:
        total = 0
        for name in self.selected():
            names, economy = APPS[name]; ids = pids(names)
            if not ids: self.logline(f"{name}: процесс не запущен."); continue
            total += len(ids)
            for pid in ids: self.logline(f"{name}: {profile(pid, economy, trim)}")
        self.status.set(f"Профили обработаны: процессов {total}." if total else "Подходящие запущенные процессы не найдены.")
        if trim: self.logline("Очистка памяти разовая: постоянная очистка вызывает фризы и не используется.")
    def gpu(self) -> None:
        paths = []
        for name in ("Figma", "Chrome", "OBS Studio"):
            if self.enabled[name].get(): paths += exe_paths(APPS[name][0])
        paths = list(dict.fromkeys(paths))
        if not paths: messagebox.showinfo("Нужны запущенные приложения", "Запустите Figma, Chrome или OBS и повторите действие."); return
        self.status.set(f"Высокопроизводительная GPU назначена для {high_gpu(paths)} приложений. Перезапустите их."); self.logline("GPU: " + ", ".join(map(str, paths)))
    def undo_gpu(self) -> None:
        count = restore_gpu(); self.status.set(f"Возвращены прежние настройки GPU: {count}." if count else "Сохранённых изменений GPU нет.")
    def chrome(self) -> None:
        subprocess.Popen(["cmd", "/c", "start", "", "chrome://settings/performance"], creationflags=subprocess.CREATE_NO_WINDOW)
    def loop(self) -> None:
        while not self.stop.wait(max(20, self.seconds.get())): self.after(0, self.apply)
    def toggle(self) -> None:
        if self.running:
            self.running = False; self.stop.set(); self.mon.configure(text="Включить мониторинг"); self.status.set("Постоянное применение профилей выключено."); return
        self.running = True; self.stop.clear(); self.mon.configure(text="Выключить мониторинг"); self.apply(); threading.Thread(target=self.loop, daemon=True).start(); self.status.set("Постоянное применение профилей включено.")
    def close(self) -> None: self.stop.set(); self.destroy()


if __name__ == "__main__":
    if sys.platform != "win32": raise SystemExit("Приложение предназначено для Windows.")
    Optimizer().mainloop()
