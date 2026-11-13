"""Owned independent repositories; source paths are read-only until publication."""
import json
import os
from pathlib import Path
import shutil
import stat


def default_path(workspace_id):
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local/share')
    return base / 'AgentGuild/workspaces' / workspace_id


def marker(path):
    return path.with_name(path.name + '.guild-owner.json')


def validate_location(path, source):
    path, source = Path(path).resolve(), Path(source).resolve()
    if path.is_relative_to(source) or source.is_relative_to(path):
        raise ValueError('workspace must be separate from the source repository')
    return path


def remove_owned(path, source, workspace_id):
    path = validate_location(path, source)
    receipt = marker(path)
    data = json.loads(receipt.read_text(encoding='utf-8'))
    if data != {'workspace_id': workspace_id, 'path': str(path), 'source': str(Path(source).resolve())}:
        raise ValueError('workspace ownership does not match; preserve files for inspection')
    # Only the canonical, explicitly owned directory may be removed recursively.
    if path.exists():
        def writable_retry(function, filename, error):
            target = Path(filename).resolve()
            if not isinstance(error, PermissionError) or not target.is_relative_to(path):
                raise error
            target.chmod(target.stat().st_mode | stat.S_IWRITE)
            function(filename)
        shutil.rmtree(path, onexc=writable_retry)
    receipt.unlink()


def create(git, source, path, workspace_id, base_sha, branch):
    path = validate_location(path, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    if marker(path).exists():
        raise ValueError('workspace has an existing ownership receipt; inspect it first')
    path.mkdir()  # Exclusive claim: never overwrite an existing directory.
    receipt = marker(path)
    try:
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump({'workspace_id': workspace_id, 'path': str(path), 'source': str(Path(source).resolve())}, stream)
    except Exception:
        path.rmdir()
        raise
    try:
        git(source, 'clone', '--no-local', '--no-hardlinks', '--no-checkout', '--', str(source), str(path))
        git(path, 'remote', 'remove', 'origin')
        git(path, 'checkout', '-b', branch, base_sha)
    except Exception:
        remove_owned(path, source, workspace_id)
        raise
    return path
