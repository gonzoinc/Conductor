"""Conductor's Windows graphical setup. No terminal is required."""
from __future__ import annotations

import os
import json
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from setup_controller import (SetupCancelled, SetupConfig, completed_setup, install,
                              installed_setup, launch_test_profile)
from workflow_window import WorkflowWindow


def bundled_resources() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


class SetupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Conductor Setup")
        self.minsize(660, 500)
        self.configure(padx=28, pady=22)
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        self.install_path = tk.StringVar(value=str(local / "Programs/Conductor"))
        self.data_path = tk.StringVar(value=str(local / "Conductor"))
        self.desktop = tk.BooleanVar(value=True)
        self.vscode = tk.BooleanVar(value=False)
        self.isolated = tk.BooleanVar(value=True)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.completion: WorkflowWindow | None = None
        self.completed_config: SetupConfig | None = None
        self.completed_location: Path | None = None
        self._build()
        self.after(100, self._drain)

    def _build(self) -> None:
        actions = ttk.Frame(self)
        actions.pack(side="bottom", fill="x", pady=(14, 4))
        self.start = ttk.Button(actions, text="Install Conductor", command=self._start)
        self.start.pack(side="left")
        self.stop = ttk.Button(actions, text="Cancel", command=self._cancel, state="disabled")
        self.stop.pack(side="left", padx=8)
        ttk.Button(actions, text="Run client checks", command=self._show_installed).pack(side="left", padx=8)
        ttk.Button(actions, text="Copy progress", command=self._copy_progress).pack(side="left")
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right")
        self.log = tk.Text(self, height=5, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(side="bottom", fill="x")
        ttk.Label(self, text="Progress", font=("Segoe UI", 10, "bold")).pack(side="bottom", anchor="w", pady=(8, 4))

        ttk.Label(self, text="Set up Conductor", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        ttk.Label(self, text="Conductor shares task status between Codex agents on this computer.\n"
                              "Your project files and Codex login stay here. For a shared Linux service, use the VM installer.", wraplength=640).pack(anchor="w", pady=(8, 22))
        self.destination = ttk.LabelFrame(self, text="1  Choose folders", padding=13)
        self.destination.pack(fill="x", pady=13)
        self._path_row(self.destination, "Install Conductor in", self.install_path, directory=True)
        self._path_row(self.destination, "Keep local task data in", self.data_path, directory=True)
        clients = ttk.LabelFrame(self, text="2  Choose Codex clients", padding=13)
        clients.pack(fill="x")
        ttk.Checkbutton(clients, text="Codex desktop", variable=self.desktop).pack(anchor="w")
        ttk.Checkbutton(clients, text="Codex in VS Code", variable=self.vscode).pack(anchor="w")
        ttk.Checkbutton(clients, text="Use separate test profiles first (keeps my normal Codex setup untouched)", variable=self.isolated).pack(anchor="w", pady=(7, 0))
        ttk.Label(clients, text="Clear this only when you are ready to add Conductor to your normal Codex settings.", wraplength=625).pack(anchor="w")
        self.after_idle(self._fit_window)

    def _fit_window(self) -> None:
        """Size the form for the selected mode instead of hiding controls."""
        self.update_idletasks()
        width = max(760, self.winfo_reqwidth())
        height = self.winfo_reqheight()
        self.minsize(width, height)
        self.geometry(f"{width}x{height}")

    @staticmethod
    def _path_row(parent: tk.Widget, label: str, variable: tk.StringVar, directory: bool | None = None) -> None:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(7, 2))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
        if directory is not None:
            def browse() -> None:
                selected = filedialog.askdirectory() if directory else filedialog.askopenfilename()
                if selected:
                    variable.set(selected)
            ttk.Button(row, text="Browse…", command=browse).pack(side="left", padx=(6, 0))

    def _write(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _copy_progress(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end-1c"))

    def _show_completion(self, location: Path, config: SetupConfig) -> None:
        self.completed_location, self.completed_config = location, config
        if self.completion is not None and self.completion.winfo_exists():
            self.completion.destroy()
        self.completion = WorkflowWindow(self, config, location, self._launch_profile,
                                         self._prepare_current_desktop, self._start)
        self.completion.lift()

    def _prepare_current_desktop(self) -> None:
        """Prepare an explicit install action in the same guided window."""
        config = self.completed_config
        if config is None:
            return
        self.install_path.set(str(config.install_dir))
        if config.state_dir is not None:
            self.data_path.set(str(config.state_dir))
        self.desktop.set(True)
        self.vscode.set(False)
        self.isolated.set(False)
        if self.completion is not None and self.completion.winfo_exists():
            self.completion.show_current_install()

    def _show_installed(self) -> None:
        try:
            config = installed_setup(Path(self.install_path.get()))
            self._show_completion(config.install_dir, config)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self._write(f"Could not open installed setup: {error}")

    def _launch_profile(self, profile: str) -> None:
        if self.completed_location is None or self.completion is None:
            return
        self.completion.launch_started(profile)
        location = self.completed_location
        def work() -> None:
            try:
                launch_test_profile(location, profile, bundled_resources())
                self.events.put(("launch_done", (profile, "")))
            except Exception as error:
                self.events.put(("launch_error", (profile, str(error))))
        threading.Thread(target=work, daemon=True).start()

    def _start(self) -> None:
        profiles = tuple(name for name, enabled in (("desktop", self.desktop.get()), ("vscode", self.vscode.get())) if enabled)
        config = SetupConfig(mode="local", install_dir=Path(self.install_path.get()),
                             state_dir=Path(self.data_path.get()), profiles=profiles,
                             isolated=self.isolated.get())
        self.cancel.clear()
        self.start.configure(state="disabled")
        self.stop.configure(state="normal")
        self._write("Starting setup…")

        def work() -> None:
            try:
                location = install(config, bundled_resources(), lambda line: self.events.put(("line", line)), self.cancel)
                try:
                    completed_setup(location, config)
                except (OSError, ValueError) as error:
                    self.events.put(("line", f"Setup succeeded, but reopening its next steps may need this window: {error}"))
                self.events.put(("done", (location, config)))
            except SetupCancelled:
                self.events.put(("cancelled", "Setup stopped. Run this installer again to resume."))
            except Exception as error:
                self.events.put(("error", str(error)))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        self.cancel.set()
        self._write("Stopping after the current setup step…")

    def _drain(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "line":
                    self._write(str(value))
                elif kind == "done":
                    location, config = value
                    self._write("Setup complete. Continue through the guided client checks.")
                    self._show_completion(location, config)
                elif kind in ("cancelled", "error"):
                    self._write(str(value))
                    if kind == "error":
                        self._write("Fix the issue shown above, then choose Install Conductor again.")
                    if self.completion is not None and self.completion.winfo_exists():
                        self.completion.install_result(str(value))
                elif kind in ("launch_done", "launch_error"):
                    profile, detail = value
                    if self.completion is not None and self.completion.winfo_exists():
                        self.completion.launch_result(profile, detail if kind == "launch_error" else None)
                    self._write("Test client launch requested." if kind == "launch_done" else
                                "Test client could not open. See the guided check for copyable details.")
                if kind in ("done", "cancelled", "error"):
                    self.start.configure(state="normal")
                    self.stop.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        import hashlib
        import json
        import zipfile
        resources = bundled_resources()
        lock = json.loads((resources / "runtime-lock.json").read_text(encoding="utf-8"))
        assert hashlib.sha256((resources / "uv.exe").read_bytes()).hexdigest() == lock["uv_exe_sha256"]
        assert hashlib.sha256((resources / "conductor-source.zip").read_bytes()).hexdigest() == lock["source_archive_sha256"]
        with zipfile.ZipFile(resources / "conductor-source.zip") as package:
            assert "scripts/bootstrap.py" in package.namelist()
            assert "pyproject.toml" in package.namelist()
    else:
        SetupApp().mainloop()
