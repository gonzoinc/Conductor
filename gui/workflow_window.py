"""Action-by-action post-install workflow for real Codex client checks."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Callable

from next_steps import QUICK_CHECK_PROMPT
from setup_controller import SetupConfig


CLIENT_NAMES = {"desktop": "Codex desktop", "vscode": "VS Code"}


class WorkflowWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk, config: SetupConfig, location: Path,
                 on_launch: Callable[[str], None], on_use_current: Callable[[], None],
                 on_install_current: Callable[[], None]) -> None:
        super().__init__(parent)
        self.title("Finish Conductor setup")
        self.configure(padx=24, pady=20)
        self.config_data = config
        self.location = location
        self.on_launch = on_launch
        self.on_use_current = on_use_current
        self.on_install_current = on_install_current
        self.prepared_current = False
        self.installing_current = False
        self.clients = list(config.profiles)
        self.client_index = 0
        self.step = 0
        self.error_detail = ""
        self.launch_button: ttk.Button | None = None

        ttk.Label(self, text="Finish Conductor setup", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        self.progress = ttk.Label(self, font=("Segoe UI", 10))
        self.progress.pack(anchor="w", pady=(5, 14))
        self.card = ttk.LabelFrame(self, padding=18)
        self.card.pack(fill="both", expand=True)
        self.heading = ttk.Label(self.card, font=("Segoe UI", 16, "bold"), wraplength=700)
        self.heading.pack(anchor="w")
        self.description = ttk.Label(self.card, wraplength=700, justify="left")
        self.description.pack(anchor="w", pady=(12, 0))
        self.prompt = ttk.Label(self.card, wraplength=700, justify="left")
        self.prompt.pack(anchor="w", pady=(14, 0))
        self.actions = ttk.Frame(self.card)
        self.actions.pack(fill="x", pady=(18, 0))

        self.status = ttk.Label(self, wraplength=740, justify="left")
        self.status.pack(anchor="w", pady=(12, 4))
        self.error_button = ttk.Button(self, text="Copy error details", command=self._copy_error)
        footer = ttk.Frame(self)
        footer.pack(fill="x", pady=(12, 0))
        self.back = ttk.Button(footer, text="Back", command=self._back)
        self.back.pack(side="left")
        ttk.Button(footer, text="Close", command=self.destroy).pack(side="right")
        self._render()

    @property
    def profile(self) -> str:
        return self.clients[self.client_index]

    def _button(self, title: str, action: Callable[[], None], *, primary: bool = False) -> ttk.Button:
        button = ttk.Button(self.actions, text=title, command=action)
        button.pack(side="left", padx=(0, 10) if primary else (0, 8))
        return button

    def _copy(self, value: str, description: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self._status(f"Copied {description}. Paste it into the selected Codex client.")

    def _copy_error(self) -> None:
        if self.error_detail:
            self._copy(self.error_detail, "the error details")

    def _status(self, message: str, error: str = "") -> None:
        self.status.configure(text=message)
        self.error_detail = error
        if error:
            self.error_button.pack(anchor="w", pady=(0, 2))
        else:
            self.error_button.pack_forget()

    def _next(self) -> None:
        self.step += 1
        self._render()

    def _back(self) -> None:
        if self.prepared_current:
            return
        if self.step > 0:
            self.step -= 1
        elif self.client_index > 0:
            self.client_index -= 1
            self.step = 3
        self._render()

    def launch_result(self, profile: str, error: str | None) -> None:
        if profile != self.profile or self.step != 0:
            return
        if error:
            visible = error if not any(character in error for character in ("\\", "/")) else (
                "The test client could not open. Use Copy error details if you need to share the cause."
            )
            self._status(visible, error)
            self._render(keep_status=True)
        else:
            self._next()

    def launch_started(self, profile: str) -> None:
        if profile == self.profile and self.step == 0:
            if self.launch_button is not None:
                self.launch_button.configure(state="disabled")
            self._status(f"Opening isolated {CLIENT_NAMES[profile]}. This may take a moment.")

    def show_current_install(self) -> None:
        self.prepared_current = True
        self._render()

    def _install_current(self) -> None:
        if self.installing_current:
            return
        self.installing_current = True
        self._render()
        self._status("Adding Conductor to this Codex app. Wait for setup to finish.")
        self.on_install_current()

    def install_result(self, error: str) -> None:
        if not self.prepared_current:
            return
        self.installing_current = False
        self._render()
        self._status("Setup stopped. Copy the error details, then try again.", error)

    def _render(self, *, keep_status: bool = False) -> None:
        for child in self.actions.winfo_children():
            child.destroy()
        self.launch_button = None
        name = CLIENT_NAMES[self.profile]
        self.progress.configure(text=f"{name}  •  client {self.client_index + 1} of {len(self.clients)}  •  step {min(self.step + 1, 4)} of 4")
        self.card.configure(text=name)
        self.prompt.configure(text="")
        self.back.configure(state="normal" if self.step or self.client_index else "disabled")
        if not keep_status:
            self._status("")

        if self.prepared_current:
            self.progress.configure(text="Codex desktop  •  add to this app")
            self.back.configure(state="disabled")
            self.heading.configure(text="Add Conductor to this Codex app")
            self.description.configure(text=(
                "This adds Conductor to the Codex settings you normally use. Select "
                "Install below, wait for setup to finish, then restart Codex and start "
                "a new chat."
            ))
            button = self._button("Install Conductor in this Codex app",
                                  self._install_current, primary=True)
            if self.installing_current:
                button.configure(state="disabled")
            self.after_idle(self._fit)
            return

        if self.step == 0:
            if self.config_data.isolated and self.profile == "desktop":
                self.heading.configure(text="Use Conductor in this Codex app")
                self.description.configure(text=(
                    "This installation uses a separate test profile, so a new chat in your "
                    "current Codex app will not have Conductor. This Codex version does not "
                    "reliably open another desktop session for that profile. To test here, "
                    "return to setup, add Conductor to your usual Codex settings, restart "
                    "Codex, and start a new chat."
                ))
                self._button("Use Conductor in this Codex app", self.on_use_current, primary=True)
                if self.client_index + 1 < len(self.clients):
                    self._button("Test VS Code first", self._next_client)
            elif self.config_data.isolated:
                self.heading.configure(text=f"Open {name} with Conductor enabled")
                self.description.configure(text=(
                    "You chose a separate test setup, so your current VS Code chats do not "
                    "have Conductor. This opens a separate VS Code window with the test "
                    "connection. Start a new Codex chat there."
                ))
                self.launch_button = self._button(f"Open {name} test window",
                                                  lambda: self.on_launch(self.profile), primary=True)
            else:
                self.heading.configure(text=f"Restart {name}")
                self.description.configure(text=(
                    "Close and reopen this client so it loads Conductor from your usual Codex settings."
                ))
                self._button(f"I restarted {name}", self._next, primary=True)
        elif self.step == 1:
            self.heading.configure(text="Sign in and open a test chat")
            if self.profile == "desktop" and self.config_data.isolated:
                self.description.configure(text=(
                    "In the new Codex window, sign in if asked. Choose Add project and paste "
                    "the copied test workspace location. Then start a new chat there."
                ))
                self._button("Copy test workspace location", lambda: self._copy(
                    str(self.location / "test-profiles/desktop/workspace"), "the test workspace location"))
                self._button("I opened the test project", self._next, primary=True)
            elif self.profile == "vscode" and self.config_data.isolated:
                self.description.configure(text=(
                    "The separate VS Code window opens the test workspace automatically. "
                    "Sign in to the Codex extension if asked, then open a new Codex chat."
                ))
                self._button("I have a Codex chat", self._next, primary=True)
            else:
                self.description.configure(text=(
                    "Sign in if asked, then start a new Codex chat. The quick check does not touch project files."
                ))
                self._button("I opened a test chat", self._next, primary=True)
        elif self.step == 2:
            self.heading.configure(text="Run one quick check")
            explanation = ("This single prompt checks the connection, one direct answer, and two tiny native "
                           "worker tasks. It creates no project files or extra review jobs.")
            self.description.configure(text=explanation)
            self.prompt.configure(text=QUICK_CHECK_PROMPT)
            self._button("Copy quick check prompt",
                         lambda: self._copy(QUICK_CHECK_PROMPT, "the quick check prompt"))
            self._button("It passed — continue", self._next, primary=True)
            self._button("It failed", lambda: self._status(
                "Stop here and share the exact Codex error. Do not mark this check complete."))
        else:
            self.heading.configure(text=f"{name} checks complete")
            self.description.configure(text=(
                "The quick connection and native-worker check is finished for this client. "
                "Keep the short result from the Codex chat for review."
            ))
            if self.client_index + 1 < len(self.clients):
                next_name = CLIENT_NAMES[self.clients[self.client_index + 1]]
                self._button(f"Continue with {next_name}", self._next_client, primary=True)
            else:
                self._button("Finish", self.destroy, primary=True)
        self.after_idle(self._fit)

    def _next_client(self) -> None:
        self.client_index += 1
        self.step = 0
        self._render()

    def _fit(self) -> None:
        self.update_idletasks()
        width = max(800, self.winfo_reqwidth())
        height = self.winfo_reqheight()
        self.minsize(720, 330)
        self.geometry(f"{width}x{height}")
