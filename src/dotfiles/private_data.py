from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotfiles.manifest import DEFAULT_HOST, normalize_host

PRIVATE_DATA_FILENAME = ".dtf-private.json"
PRIVATE_DATA_EXAMPLE_FILENAME = ".dtf-private.example.json"
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_-]*)\}\}")


class PrivateDataError(ValueError):
    """Raised when the optional private substitution file is invalid."""


@dataclass(frozen=True)
class PrivateGroup:
    alias: str
    values: dict[str, str]


@dataclass(frozen=True)
class PrivateData:
    host: str
    groups: dict[str, PrivateGroup]

    @classmethod
    def empty(cls) -> PrivateData:
        return cls(host=DEFAULT_HOST, groups={})

    @classmethod
    def load_for_manifest(
        cls, manifest_path: Path, aliases: Collection[str] | None = None
    ) -> PrivateData:
        path = manifest_path.parent / PRIVATE_DATA_FILENAME
        if not path.exists():
            return cls.empty()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PrivateDataError(f"cannot read private data file {path}: {exc}") from exc
        data = cls.from_value(raw, path)
        if aliases is not None:
            unknown = sorted(set(data.groups) - set(aliases))
            if unknown:
                raise PrivateDataError(
                    "private data contains unknown manifest alias: " + ", ".join(unknown)
                )
        return data

    @classmethod
    def from_value(cls, raw: Any, source: Path | None = None) -> PrivateData:
        location = f" in {source}" if source else ""
        if isinstance(raw, list):
            host = DEFAULT_HOST
            entries = raw
        elif isinstance(raw, dict):
            try:
                host = normalize_host(raw.get("host", DEFAULT_HOST))
            except ValueError as exc:
                raise PrivateDataError(f"invalid private data host{location}") from exc
            entries = raw.get("mappings")
            if not isinstance(entries, list):
                raise PrivateDataError(f"private data mappings{location} must be a JSON array")
        else:
            raise PrivateDataError(f"private data{location} must be a JSON object")
        groups: dict[str, PrivateGroup] = {}
        for item in entries:
            if not isinstance(item, dict):
                raise PrivateDataError(f"private data entries{location} must be objects")
            alias = item.get("alias")
            mapping = item.get("map")
            if not isinstance(alias, str) or not alias.strip():
                raise PrivateDataError(f"private data alias{location} must be a non-empty string")
            if alias in groups:
                raise PrivateDataError(f"duplicate private data alias: {alias}")
            if not isinstance(mapping, dict) or not mapping:
                raise PrivateDataError(
                    f"private data map for {alias!r}{location} must be a non-empty object"
                )
            values: dict[str, str] = {}
            value_owners: dict[str, str] = {}
            for key, value in mapping.items():
                if not isinstance(key, str) or not _PLACEHOLDER.fullmatch("{{" + key + "}}"):
                    raise PrivateDataError(f"invalid private placeholder name: {key!r}")
                if not isinstance(value, str) or not value:
                    raise PrivateDataError(
                        f"private value for {alias}.{key} must be a non-empty string"
                    )
                if value in value_owners:
                    raise PrivateDataError(
                        f"private value for {alias}.{key} duplicates "
                        f"{value_owners[value]} and is ambiguous"
                    )
                value_owners[value] = f"{alias}.{key}"
                values[key] = value
            groups[alias] = PrivateGroup(alias=alias, values=values)
        return cls(host=host, groups=groups)

    def transformer(self, alias: str, direction: str) -> Callable[[bytes], bytes] | None:
        group = self.groups.get(alias)
        if group is None:
            return None
        if direction == "get":
            return lambda content: _to_repository(content, group.values)
        if direction == "set":
            return lambda content: _to_machine(content, group.values)
        raise ValueError(f"unsupported private data direction: {direction}")


def _to_repository(content: bytes, values: dict[str, str]) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    for key, value in sorted(values.items(), key=lambda item: len(item[1]), reverse=True):
        text = text.replace(value, "{{" + key + "}}")
    return text.encode("utf-8")


def _to_machine(content: bytes, values: dict[str, str]) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text.encode("utf-8")
