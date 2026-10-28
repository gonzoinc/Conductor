#!/usr/bin/env python3
"""Install and verify the local macOS client in the current user's Codex profile."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import bootstrap
from install_support import verify_bridge


def setup(root: Path) -> None:
    if sys.platform != 'darwin':
        raise ValueError('This installer requires macOS')
    if os.geteuid() == 0:
        raise ValueError('Run this installer as your normal user, without sudo')
    codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().absolute()
    print('Installing local Conductor for Codex desktop and the Codex VS Code extension.', flush=True)
    python = bootstrap.prepare_runtime(root)
    skills_dir = Path.home() / '.agents/skills'
    subprocess.run([str(python), str(root / 'scripts/install_client.py'), '--mode', 'local',
                    '--codex-home', str(codex_home), '--skills-dir', str(skills_dir)],
                   check=True, stdout=subprocess.PIPE)
    settings = codex_home / 'conductor-coordination/client.json'
    print('Verifying the installed local MCP connection...', flush=True)
    verify_bridge(python, settings)
    print('Conductor local setup complete.')
    print('Codex configuration: ' + str(codex_home / 'config.toml'))
    print('Skill: ' + str(skills_dir / 'conductor-coordinator'))
    print('Existing configuration is preserved; when changed, its backup is config.toml.before-conductor.')
    print('Keep this checkout in place. Restart Codex desktop and reload the VS Code window when your active work is finished, then start new chats.')
    print('Both clients must use this same Codex home. Installation does not enable native subagent controls or override organization policies.')


def main() -> int:
    try:
        setup(Path(__file__).resolve().parents[1])
        return 0
    except KeyboardInterrupt:
        print('\nSetup interrupted; rerun the same command to retry.', file=sys.stderr)
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('macOS setup stopped: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
