#!/usr/bin/env python3
"""Install this checkout's complete skill using Python 3.10+ and no network."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import uuid


SKILL_NAME = "robotics-conference-video"
SOURCE = Path(__file__).resolve().parent.parent / "skills" / SKILL_NAME
IGNORED_DIRECTORIES = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache", "validation", "outputs", "test_artifacts"}
IGNORED_FILENAMES = {".DS_Store", "Thumbs.db", "dependencies.json"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".log"}


class InstallError(Exception):
    """An actionable failure that leaves existing skills intact."""


def is_link(path: Path) -> bool:
    """Include Windows directory junctions, not only POSIX symbolic links."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def default_destination(environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    codex_directory = environ.get("CODEX_HOME", "").strip()
    if codex_directory:
        return Path(codex_directory).expanduser() / "skills"
    agents = home / ".agents" / "skills"
    return agents if agents.is_dir() else home / ".codex" / "skills"


def inventory(root: Path) -> dict[str, str]:
    """Hash every maintained file and directory; transient caches are excluded."""
    if is_link(root):
        raise InstallError(f"Refusing a linked skill directory: {root}")
    if not root.is_dir():
        raise InstallError(f"Skill directory does not exist: {root}")
    entries: dict[str, str] = {}
    folded: set[str] = set()
    for parent, directories, filenames in os.walk(root, followlinks=False):
        parent_path = Path(parent)
        directories[:] = sorted(name for name in directories if name not in IGNORED_DIRECTORIES)
        for name in directories:
            path = parent_path / name
            if is_link(path):
                raise InstallError(f"Refusing a linked directory in the skill: {path}")
            relative = path.relative_to(root).as_posix() + "/"
            if relative.rstrip("/").casefold() in folded:
                raise InstallError(f"Case-insensitive path collision: {relative}")
            folded.add(relative.rstrip("/").casefold())
            entries[relative] = "directory"
        for name in sorted(filenames):
            path = parent_path / name
            if name in IGNORED_FILENAMES or path.suffix.lower() in IGNORED_SUFFIXES:
                continue
            if is_link(path):
                raise InstallError(f"Refusing a linked file in the skill: {path}")
            if not path.is_file():
                raise InstallError(f"Unsupported file type in the skill: {path}")
            relative = path.relative_to(root).as_posix()
            if relative.casefold() in folded:
                raise InstallError(f"Case-insensitive path collision: {relative}")
            folded.add(relative.casefold())
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            entries[relative] = digest.hexdigest()
    return entries


def tree_hash(entries: dict[str, str]) -> str:
    serialized_entries = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized_entries.encode("utf-8")).hexdigest()


def differences(expected: dict[str, str], actual: dict[str, str]) -> dict[str, list[str]]:
    return {
        "missing": sorted(expected.keys() - actual.keys()),
        "extra": sorted(actual.keys() - expected.keys()),
        "changed": sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name]),
    }


def install(source: Path, destination: Path, *, check: bool = False, update: bool = False) -> dict:
    source = source.absolute()
    expected = inventory(source)
    if "SKILL.md" not in expected:
        raise InstallError(f"Source lacks SKILL.md: {source}")
    root = destination.expanduser().resolve()
    target = root / SKILL_NAME
    # The source must not be inside the destination or vice versa.
    source_resolved = source.resolve()
    target_resolved = target.resolve()
    if source_resolved == target_resolved or source_resolved in target_resolved.parents or target_resolved in source_resolved.parents:
        raise InstallError("Source and installed skill must be separate, non-nested directories")
    if is_link(target):
        raise InstallError(f"Refusing to replace or check a linked skill directory: {target}")
    info = {"skill": SKILL_NAME, "path": str(target), "source": str(source),
            "files": sum(value != "directory" for value in expected.values()), "tree_sha256": tree_hash(expected)}
    if check:
        actual = inventory(target)
        delta = differences(expected, actual)
        info.update(action="check", status="PASS" if expected == actual else "FAIL", differences=delta,
                    installed_tree_sha256=tree_hash(actual))
        return info
    if target.exists() and not update:
        raise InstallError(f"Skill already exists; nothing was changed. Use --check or --update: {target}")
    previous: dict[str, str] | None = None
    if target.exists():
        previous = inventory(target)  # Reject nested links before moving an existing tree.
        if expected == previous:
            info.update(action="update", status="UNCHANGED", backup=None)
            return info
    root.mkdir(parents=True, exist_ok=True)
    # mkdtemp creates a unique staging directory alongside the exact target.
    stage = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}.stage-", dir=root))
    backup: Path | None = None
    try:
        for relative, digest in expected.items():
            staged_path = stage / relative
            if digest == "directory":
                staged_path.mkdir(parents=True, exist_ok=True)
            else:
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, staged_path)
        if inventory(stage) != expected:
            raise InstallError("Staged file verification failed; existing skill was not changed")
        if is_link(target):
            raise InstallError("Installed skill changed to a linked directory during staging; nothing was replaced")
        if target.exists():
            if not update or previous is None:
                raise InstallError("A skill appeared during staging; nothing was replaced")
            if inventory(target) != previous:
                raise InstallError("Installed skill changed during staging; nothing was replaced")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = root / f".{SKILL_NAME}.backup-{timestamp}-{uuid.uuid4().hex[:8]}"
            os.replace(target, backup)
        elif previous is not None:
            raise InstallError("Installed skill disappeared during staging; the update was stopped")
        try:
            os.replace(stage, target)
        except OSError:
            if backup is not None and not target.exists():
                os.replace(backup, target)
                backup = None
            raise
        info.update(action="update" if backup else "install", status="PASS", backup=str(backup) if backup else None)
        return info
    finally:
        # Only remove the unique staging directory this process just created.
        if stage.exists() and stage.parent.resolve() == root.resolve() and stage.name.startswith(f".{SKILL_NAME}.stage-"):
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, help="Skills root (the skill subdirectory is added automatically)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Read-only comparison of every maintained file and directory")
    action.add_argument("--update", action="store_true", help="Update this skill only, retaining the previous directory in a timestamped backup")
    args = parser.parse_args(argv)
    try:
        result = install(SOURCE, args.dest or default_destination(), check=args.check, update=args.update)
    except (InstallError, OSError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "FAIL" else 1


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required")
    raise SystemExit(main())
