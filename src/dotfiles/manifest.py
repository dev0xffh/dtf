from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1
SUPPORTED_TYPES = frozenset({"INI", "TOML", "JSON", "JSONC", "JSON5", "SHELL"})
PROJECT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_HOST = "main"


class ManifestError(ValueError):
    """Raised when the manifest is missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    app: str
    config_type: str
    paths: tuple[str, ...]
    hosts: tuple[str, ...] = (DEFAULT_HOST,)


@dataclass
class Manifest:
    project: str
    version: int
    apps: dict[str, AppConfig]

    @classmethod
    def empty(cls, project: str) -> Manifest:
        if not PROJECT_PATTERN.fullmatch(project):
            raise ManifestError("project must be a non-empty identifier")
        return cls(project=project, version=MANIFEST_VERSION, apps={})

    @classmethod
    def load(cls, path: Path) -> Manifest:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ManifestError(f"manifest not found: {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Any) -> Manifest:
        if not isinstance(raw, dict):
            raise ManifestError("manifest root must be a JSON object")
        project = raw.get("project")
        if not isinstance(project, str) or not PROJECT_PATTERN.fullmatch(project):
            raise ManifestError("manifest project must be a non-empty identifier")
        if raw.get("version") != MANIFEST_VERSION:
            raise ManifestError(f"unsupported manifest version: {raw.get('version')!r}")
        raw_apps = raw.get("apps")
        if not isinstance(raw_apps, dict):
            raise ManifestError("manifest apps must be a JSON object")

        apps: dict[str, AppConfig] = {}
        owned: dict[str, str] = {}
        for alias, value in raw_apps.items():
            if not isinstance(alias, str) or not alias.strip():
                raise ManifestError("app aliases must be non-empty strings")
            if not isinstance(value, dict):
                raise ManifestError(f"app {alias!r} must be a JSON object")
            app = value.get("app")
            config_type = value.get("type")
            paths = value.get("paths")
            if not isinstance(app, str) or not app.strip():
                raise ManifestError(f"app {alias!r} has an invalid app name")
            if not isinstance(config_type, str) or config_type.upper() not in SUPPORTED_TYPES:
                allowed = ", ".join(sorted(SUPPORTED_TYPES))
                raise ManifestError(f"app {alias!r} type must be one of: {allowed}")
            if not isinstance(paths, list) or not paths:
                raise ManifestError(f"app {alias!r} must contain at least one path")
            hosts = _normalize_hosts(value.get("hosts", [DEFAULT_HOST]), alias)
            normalized: list[str] = []
            for item in paths:
                if not isinstance(item, str):
                    raise ManifestError(f"app {alias!r} contains a non-string path")
                item = normalize_machine_path(item)
                if item not in normalized:
                    normalized.append(item)
                if item in owned and owned[item] != alias:
                    raise ManifestError(
                        f"path {item!r} is owned by both {owned[item]!r} and {alias!r}"
                    )
                owned[item] = alias
            apps[alias] = AppConfig(
                app=app,
                config_type=config_type.upper(),
                paths=tuple(normalized),
                hosts=hosts,
            )
        _validate_no_overlaps(apps)
        return cls(project=project, version=MANIFEST_VERSION, apps=apps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "version": self.version,
            "apps": {
                alias: {
                    "app": item.app,
                    "type": item.config_type,
                    "paths": list(item.paths),
                    "hosts": list(item.hosts),
                }
                for alias, item in self.apps.items()
            },
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def normalize_machine_path(value: str, *, home: Path | None = None) -> str:
    value = value.strip()
    if not value:
        raise ManifestError("paths cannot be empty")
    home = (home or Path.home()).resolve()
    if value == "$HOME":
        value = "~"
    elif value.startswith("$HOME/"):
        value = "~/" + value[6:]

    if value == "~":
        raise ManifestError("tracking the entire home directory is not allowed")
    if value.startswith("~/"):
        relative = Path(value[2:])
        if relative.is_absolute() or ".." in relative.parts:
            raise ManifestError(f"unsafe home path: {value!r}")
        return "~/" + relative.as_posix()

    path = Path(value)
    if not path.is_absolute():
        raise ManifestError(f"path must start with ~/ or /: {value!r}")
    normalized = Path(os.path.normpath(value))
    if normalized == Path("/"):
        raise ManifestError("tracking the filesystem root is not allowed")
    try:
        relative = normalized.relative_to(home)
    except ValueError:
        return normalized.as_posix()
    return "~" if not relative.parts else "~/" + relative.as_posix()


def machine_path(value: str, *, home: Path | None = None) -> Path:
    home = home or Path.home()
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value)


def repo_path(value: str, manifest_path: Path, host: str = DEFAULT_HOST) -> Path:
    host = normalize_host(host)
    root = manifest_path.parent / "configs" / host
    if value == "~":
        return root / "home"
    if value.startswith("~/"):
        return root / "home" / value[2:]
    return root / "root" / value.removeprefix("/")


def _normalize_hosts(value: Any, alias: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"app {alias!r} hosts must be a non-empty array")
    hosts = tuple(normalize_host(host) for host in value)
    if len(set(hosts)) != len(hosts):
        raise ManifestError(f"app {alias!r} hosts cannot contain duplicates")
    if DEFAULT_HOST in hosts and len(hosts) != 1:
        raise ManifestError(
            f"app {alias!r} host {DEFAULT_HOST!r} cannot be combined with named hosts"
        )
    return hosts


def normalize_host(value: Any) -> str:
    if not isinstance(value, str) or not HOST_PATTERN.fullmatch(value):
        raise ManifestError(
            "host must contain letters, digits, dots, underscores, or hyphens"
        )
    return value


def _validate_no_overlaps(apps: dict[str, AppConfig]) -> None:
    entries: list[tuple[str, Path, str]] = []
    for alias, app in apps.items():
        for value in app.paths:
            namespace = "home" if value == "~" or value.startswith("~/") else "root"
            logical = Path("/") if value in {"~", "/"} else Path(value.removeprefix("~/"))
            for other_namespace, other, other_alias in entries:
                if namespace != other_namespace or alias == other_alias:
                    continue
                if logical == other or logical in other.parents or other in logical.parents:
                    raise ManifestError(
                        f"overlapping paths are owned by {other_alias!r} and {alias!r}"
                    )
            entries.append((namespace, logical, alias))
