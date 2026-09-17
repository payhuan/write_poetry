"""Resolve mutable run artifacts outside the installed Skill directory."""

import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
ENV_NAME = 'WRITE_POETRY_WORKDIR'
_override = None


def configure_workspace(value=None):
    """Set a process-local workspace override and return its validated root."""
    global _override
    _override = Path(value).expanduser() if value else None
    return workspace_root()


def workspace_root():
    configured = _override or (Path(os.environ[ENV_NAME]).expanduser()
                               if os.environ.get(ENV_NAME, '').strip() else None)
    if configured is None:
        if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):
            configured = Path(os.environ['LOCALAPPDATA']) / 'write-poetry'
        elif os.environ.get('XDG_STATE_HOME'):
            configured = Path(os.environ['XDG_STATE_HOME']) / 'write-poetry'
        else:
            configured = Path.home() / '.local' / 'state' / 'write-poetry'
    root = configured.resolve()
    if root == PROJECT or root.is_relative_to(PROJECT):
        raise ValueError('Runtime workspace must be outside the Skill directory: ' + str(root))
    return root


def input_path(value):
    """Resolve runtime input first, then explicit cwd/static Skill resources."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    candidates = [workspace_root() / path]
    cwd_candidate = Path.cwd().resolve() / path
    if not cwd_candidate.is_relative_to(PROJECT):
        candidates.append(cwd_candidate)
    candidates.append(PROJECT / path)
    return next((candidate.resolve() for candidate in candidates if candidate.exists()),
                candidates[0].resolve())


def output_path(value):
    """Resolve relative output in the workspace and reject Skill-tree writes."""
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else workspace_root() / path).resolve()
    if path == PROJECT or path.is_relative_to(PROJECT):
        raise ValueError('Runtime output must stay outside the Skill directory: ' + str(path))
    return path
