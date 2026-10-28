"""The post-install UI must present contextual actions without visible paths."""
from __future__ import annotations

from pathlib import Path
import sys
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
from next_steps import QUICK_CHECK_PROMPT
from setup_app import SetupApp
from setup_controller import SetupConfig
from workflow_window import WorkflowWindow


@unittest.skipUnless(sys.platform == "win32", "Windows setup workflow")
class WorkflowTests(unittest.TestCase):
    def test_each_step_keeps_actions_with_instructions_and_hides_paths(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        folder = Path("C:/Conductor")
        config = SetupConfig("local", folder, Path("C:/ConductorData"),
                             ("desktop", "vscode"), True)
        launched = []
        current = []
        installed = []
        workflow = WorkflowWindow(root, config, folder, launched.append,
                                  lambda: current.append(True),
                                  lambda: installed.append(True))
        self.addCleanup(workflow.destroy)
        workflow.withdraw()
        for profile in ("desktop", "vscode"):
            for step in range(4):
                workflow.step = step
                workflow._render()
                root.update()
                visible = " ".join((workflow.heading.cget("text"),
                                    workflow.description.cget("text"),
                                    workflow.prompt.cget("text")))
                self.assertNotIn("C:/", visible)
                self.assertNotIn("C:\\", visible)
                self.assertNotIn(".md", visible)
                self.assertTrue(workflow.actions.winfo_children())
            workflow.step = 2
            workflow._render()
            copy_prompt = next(button for button in workflow.actions.winfo_children()
                               if button.cget("text") == "Copy quick check prompt")
            copy_prompt.invoke()
            self.assertEqual(workflow.clipboard_get(), QUICK_CHECK_PROMPT)
            if profile == "desktop":
                workflow.step = 0
                workflow._render()
                switch = next(button for button in workflow.actions.winfo_children()
                              if button.cget("text") == "Use Conductor in this Codex app")
                switch.invoke()
                self.assertEqual(current, [True])
                self.assertEqual(launched, [])
                workflow.show_current_install()
                self.assertTrue(workflow.prepared_current)
                install = next(button for button in workflow.actions.winfo_children()
                               if button.cget("text") == "Install Conductor in this Codex app")
                install.invoke()
                self.assertEqual(installed, [True])
                self.assertTrue(workflow.installing_current)
                workflow.install_result("Specific setup failure from the installer")
                self.assertIn("Copy the error details", workflow.status.cget("text"))
                workflow.error_button.invoke()
                self.assertEqual(workflow.clipboard_get(), "Specific setup failure from the installer")
                workflow.prepared_current = False
                workflow.step = 1
                workflow._render()
                copy = next(button for button in workflow.actions.winfo_children()
                            if button.cget("text") == "Copy test workspace location")
                copy.invoke()
                self.assertIn("test-profiles", workflow.clipboard_get())
                workflow._next_client()
        self.assertEqual(workflow.profile, "vscode")
        self.assertLessEqual(len(QUICK_CHECK_PROMPT.split()), 100)
        self.assertIn("create no files", QUICK_CHECK_PROMPT)
        self.assertIn("two independent scout tasks", QUICK_CHECK_PROMPT)
        acceptance = (Path(__file__).resolve().parents[1] / "docs/acceptance.md").read_text(encoding="utf-8")
        self.assertIn("> " + QUICK_CHECK_PROMPT, acceptance)

    def test_switch_to_current_app_prepares_explicit_install(self):
        app = SetupApp()
        self.addCleanup(app.destroy)
        app.withdraw()
        app.completed_config = SetupConfig("local", Path("C:/Conductor"),
                                           Path("C:/ConductorData"), ("desktop", "vscode"), True)
        app.completion = WorkflowWindow(app, app.completed_config,
                                        app.completed_config.install_dir,
                                        lambda _: None, app._prepare_current_desktop,
                                        lambda: None)
        app.completion.withdraw()
        app._prepare_current_desktop()
        self.assertFalse(app.isolated.get())
        self.assertTrue(app.desktop.get())
        self.assertFalse(app.vscode.get())
        self.assertEqual(app.data_path.get(), "C:\\ConductorData")
        self.assertTrue(app.completion.prepared_current)
        self.assertIn("Install Conductor", app.completion.actions.winfo_children()[0].cget("text"))


if __name__ == "__main__":
    unittest.main()
