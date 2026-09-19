from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dotfiles.manifest import DEFAULT_HOST, AppConfig, Manifest, machine_path, repo_path
from dotfiles.merge import MergeError, merge_file
from dotfiles.private_data import PrivateData
from dotfiles.secrets import SecretFinding, scan_paths


@dataclass(frozen=True)
class OperationError:
    alias: str
    path: str
    message: str


@dataclass
class OperationResult:
    completed: int
    errors: list[OperationError]


def select_apps(manifest: Manifest, aliases: list[str]) -> list[tuple[str, AppConfig]]:
    if not aliases:
        return list(manifest.apps.items())
    missing = [alias for alias in aliases if alias not in manifest.apps]
    if missing:
        raise ValueError("unknown app alias: " + ", ".join(missing))
    return [(alias, manifest.apps[alias]) for alias in aliases]


def sensitive_findings(
    selected: list[tuple[str, AppConfig]], *, home: Path | None = None
) -> list[SecretFinding]:
    paths = [machine_path(value, home=home) for _, app in selected for value in app.paths]
    return scan_paths(paths)


def transfer(
    selected: list[tuple[str, AppConfig]],
    manifest_path: Path,
    *,
    direction: str,
    merge: bool,
    host: str = DEFAULT_HOST,
    home: Path | None = None,
    private_data: PrivateData | None = None,
) -> OperationResult:
    completed = 0
    errors: list[OperationError] = []
    for alias, app in selected:
        transform = private_data.transformer(alias, direction) if private_data else None
        for value in app.paths:
            machine = machine_path(value, home=home)
            repository = repo_path(value, manifest_path, host)
            source, destination = (
                (machine, repository) if direction == "get" else (repository, machine)
            )
            try:
                if not _lexists(source):
                    raise FileNotFoundError(f"source does not exist: {source}")
                _validate_no_cycles(source, set())
                if merge:
                    _merge_path(
                        source,
                        destination,
                        config_type=app.config_type,
                        active=set(),
                        transform=transform,
                    )
                else:
                    _replace_path(source, destination, transform=transform)
                completed += 1
            except (OSError, MergeError, ValueError) as exc:
                errors.append(OperationError(alias, value, str(exc)))
    return OperationResult(completed=completed, errors=errors)


def remove_repository_configs(
    aliases: list[str],
    manifest: Manifest,
    manifest_path: Path,
    *,
    hosts: tuple[str, ...] | None = None,
) -> list[OperationError]:
    errors: list[OperationError] = []
    for alias in aliases:
        app = manifest.apps[alias]
        for host in hosts or app.hosts:
            for value in app.paths:
                target = repo_path(value, manifest_path, host)
                try:
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    elif _lexists(target):
                        target.unlink()
                    _prune_empty_parents(target.parent, manifest_path.parent / "configs")
                except OSError as exc:
                    errors.append(OperationError(alias, value, str(exc)))
    return errors


def _merge_path(
    source: Path,
    destination: Path,
    *,
    config_type: str,
    active: set[tuple[int, int]],
    transform: Callable[[bytes], bytes] | None,
) -> None:
    stat = source.stat()
    identity = (stat.st_dev, stat.st_ino)
    if source.is_dir():
        if identity in active:
            raise OSError(f"symbolic-link cycle detected at {source}")
        if _lexists(destination) and not destination.is_dir():
            raise MergeError(f"cannot merge directory into non-directory: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        active.add(identity)
        try:
            for child in source.iterdir():
                _merge_path(
                    child,
                    destination / child.name,
                    config_type=config_type,
                    active=active,
                    transform=transform,
                )
        finally:
            active.remove(identity)
        return

    if _lexists(destination) and destination.is_dir():
        raise MergeError(f"cannot merge file into directory: {destination}")
    if not _lexists(destination):
        _replace_path(source, destination, transform=transform)
        return
    if transform is None:
        merge_file(source, destination, config_type=config_type)
    else:
        _merge_transformed_file(source, destination, config_type=config_type, transform=transform)


def _replace_path(
    source: Path,
    destination: Path,
    *,
    transform: Callable[[bytes], bytes] | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.dtf-", dir=destination.parent)
    )
    staged = staging_root / "payload"
    backup = staging_root / "previous"
    try:
        if source.is_dir():
            shutil.copytree(
                source,
                staged,
                symlinks=False,
                copy_function=lambda source_file, destination_file: _copy_file(
                    source_file, destination_file, transform
                ),
            )
        else:
            _copy_file(source, staged, transform)
        had_destination = _lexists(destination)
        if had_destination:
            os.replace(destination, backup)
        try:
            os.replace(staged, destination)
        except BaseException:
            if had_destination:
                os.replace(backup, destination)
            raise
        if had_destination:
            _remove_path(backup)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _copy_file(
    source: Path | str,
    destination: Path | str,
    transform: Callable[[bytes], bytes] | None,
) -> str:
    source = Path(source)
    destination = Path(destination)
    if transform is None:
        shutil.copy2(source, destination, follow_symlinks=True)
    else:
        destination.write_bytes(transform(source.read_bytes()))
        shutil.copystat(source, destination, follow_symlinks=True)
    return str(destination)


def _merge_transformed_file(
    source: Path,
    destination: Path,
    *,
    config_type: str,
    transform: Callable[[bytes], bytes],
) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".dtf-private-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(transform(source.read_bytes()))
        merge_file(Path(temporary), destination, config_type=config_type)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif _lexists(path):
        path.unlink()


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _validate_no_cycles(path: Path, active: set[tuple[int, int]]) -> None:
    stat = path.stat()
    if not path.is_dir():
        return
    identity = (stat.st_dev, stat.st_ino)
    if identity in active:
        raise OSError(f"symbolic-link cycle detected at {path}")
    active.add(identity)
    try:
        for child in path.iterdir():
            _validate_no_cycles(child, active)
    finally:
        active.remove(identity)


def _prune_empty_parents(path: Path, stop: Path) -> None:
    while path != stop and stop in path.parents:
        try:
            path.rmdir()
        except OSError:
            break
        path = path.parent
