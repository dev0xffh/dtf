from __future__ import annotations

import configparser
import json
import os
import re
import tempfile
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import json5
import tomlkit

BACKUP_MARKER = "-- BACKUP --"


class MergeError(ValueError):
    """Raised when files cannot be safely merged."""


def merge_file(source: Path, destination: Path, *, config_type: str) -> None:
    source_bytes = source.read_bytes()
    destination_bytes = destination.read_bytes()
    if source_bytes == destination_bytes:
        return
    try:
        source_text = source_bytes.decode("utf-8")
        destination_text = destination_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MergeError(f"cannot merge binary file: {source}") from exc

    config_type = config_type.upper()
    newline = _newline_of(source_text)
    final_newline = source_text.endswith(("\n", "\r"))
    clean_source = _remove_backups(source_text)
    clean_destination = _remove_backups(destination_text)

    if config_type == "INI":
        result = _merge_ini(clean_source, clean_destination)
    elif config_type == "TOML":
        result = _merge_toml(clean_source, clean_destination)
    elif config_type in {"JSONC", "JSON5"}:
        result = _merge_jsonc(clean_source, clean_destination)
        if BACKUP_MARKER in destination_text:
            try:
                if json5.loads(result) == json5.loads(clean_destination):
                    result = destination_text
            except ValueError:
                pass
    elif config_type == "JSON":
        result = _merge_json(clean_source, clean_destination)
    elif config_type == "SHELL":
        result = _merge_shell(clean_source, clean_destination)
    else:
        raise MergeError(f"unsupported merge format: {config_type}")

    result = result.replace("\r\n", "\n").replace("\r", "\n")
    result = result.rstrip("\n") + "\n" if final_newline else result.rstrip("\n")
    if newline != "\n":
        result = result.replace("\n", newline)
    _atomic_write(destination, result.encode("utf-8"))


_SHELL_ASSIGNMENT = re.compile(
    r"^\s*(?:(?:export|readonly|typeset|declare|local)\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*="
)
_SHELL_ALIAS = re.compile(r"^\s*alias\s+([A-Za-z_][A-Za-z0-9_.-]*)\s*=")
_SHELL_COMPLEX = re.compile(
    r"^\s*(?:if|then|else|elif|fi|for|while|until|case|esac|select|repeat|do|done|function)"
    r"(?:\s|$)|^\s*[{}](?:\s|$)|^\s*[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{"
)
_SHELL_MERGED_MARKER = "-- MERGED DESTINATION-ONLY --"


def _shell_key(line: str) -> tuple[str, str] | None:
    assignment = _SHELL_ASSIGNMENT.match(line)
    if assignment:
        return ("variable", assignment.group(1))
    alias = _SHELL_ALIAS.match(line)
    if alias:
        return ("alias", alias.group(1))
    return None


def _merge_shell(source: str, destination: str) -> str:
    source_lines = source.splitlines()
    destination_lines = destination.splitlines()
    source_keys: dict[tuple[str, str], tuple[int, str]] = {}
    source_statements: set[str] = set()
    for index, line in enumerate(source_lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            source_statements.add(stripped)
        key = _shell_key(line)
        if key:
            source_keys[key] = (index, line)

    backups: dict[int, list[str]] = {}
    additions: list[str] = []
    pending_comments: list[str] = []
    for line in destination_lines:
        stripped = line.strip()
        if not stripped:
            if pending_comments:
                pending_comments.append("")
            continue
        if stripped.startswith("#"):
            if _SHELL_MERGED_MARKER not in stripped:
                pending_comments.append(line)
            continue

        key = _shell_key(line)
        if key and key in source_keys:
            source_index, source_line = source_keys[key]
            if stripped != source_line.strip():
                backups.setdefault(source_index, []).append(line)
            pending_comments.clear()
            continue
        if stripped in source_statements:
            pending_comments.clear()
            continue
        if line.rstrip().endswith("\\") or "<<" in line or _SHELL_COMPLEX.match(line):
            raise MergeError(
                "cannot safely merge destination-only compound shell syntax; "
                "use normal overwrite"
            )
        additions.extend(pending_comments)
        pending_comments.clear()
        additions.append(line)

    output: list[str] = []
    for index, line in enumerate(source_lines):
        output.append(line)
        for old_line in backups.get(index, []):
            output.append(f"# {BACKUP_MARKER} {old_line}")
    if additions:
        if output and output[-1]:
            output.append("")
        output.append(f"# {_SHELL_MERGED_MARKER}")
        output.extend(additions)
    return "\n".join(output) + "\n"


def _remove_backups(text: str) -> str:
    return "".join(
        line for line in text.splitlines(keepends=True) if BACKUP_MARKER not in line
    )


def _newline_of(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf:
        return "\r\n"
    if cr > lf and cr:
        return "\r"
    return "\n"


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _merge_jsonc(source: str, destination: str) -> str:
    try:
        source_data = json5.loads(source)
        destination_data = json5.loads(destination)
    except ValueError as exc:
        raise MergeError(f"invalid JSON/JSONC: {exc}") from exc
    conflicts: dict[tuple[str, ...], Any] = {}
    merged = _merge_values(source_data, destination_data, (), conflicts)
    lines = _render_json(merged, 0, (), conflicts)
    if () in conflicts:
        old = json.dumps(conflicts[()], ensure_ascii=False, indent=2).splitlines()
        lines.extend(f"// {BACKUP_MARKER} {line}" for line in old)
    return "\n".join(lines) + "\n"


def _merge_json(source: str, destination: str) -> str:
    try:
        source_data = json.loads(source)
        destination_data = json.loads(destination)
    except json.JSONDecodeError as exc:
        raise MergeError(f"invalid strict JSON: {exc}") from exc
    conflicts: dict[tuple[str, ...], Any] = {}
    merged = _merge_values(source_data, destination_data, (), conflicts)
    if conflicts:
        paths = ", ".join("/" + "/".join(path) for path in conflicts)
        raise MergeError(
            "strict JSON cannot retain conflicting values as comments "
            f"({paths}); use JSONC/JSON5 or normal overwrite"
        )
    return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"


def _merge_values(
    source: Any,
    destination: Any,
    path: tuple[str, ...],
    conflicts: dict[tuple[str, ...], Any],
) -> Any:
    if isinstance(source, dict) and isinstance(destination, dict):
        merged: dict[str, Any] = {}
        for key, value in source.items():
            child = path + (str(key),)
            if key in destination:
                merged[key] = _merge_values(value, destination[key], child, conflicts)
            else:
                merged[key] = value
        for key, value in destination.items():
            if key not in source:
                merged[key] = value
        return merged
    if source != destination:
        conflicts[path] = destination
    return source


def _render_json(
    value: Any,
    level: int,
    path: tuple[str, ...],
    conflicts: dict[tuple[str, ...], Any],
) -> list[str]:
    indent = "  " * level
    if not isinstance(value, dict):
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
        return [indent + line for line in rendered.splitlines()]

    lines = [indent + "{"]
    items = list(value.items())
    for index, (key, child) in enumerate(items):
        child_path = path + (str(key),)
        child_lines = _render_json(child, level + 1, child_path, conflicts)
        prefix = "  " * (level + 1) + json.dumps(str(key), ensure_ascii=False) + ": "
        child_lines[0] = prefix + child_lines[0].lstrip()
        if index < len(items) - 1:
            child_lines[-1] += ","
        lines.extend(child_lines)
        if child_path in conflicts:
            old_property = json.dumps(
                {str(key): conflicts[child_path]}, ensure_ascii=False, indent=2
            ).splitlines()[1:-1]
            lines.extend(
                "  " * (level + 1) + f"// {BACKUP_MARKER} " + old_line.strip()
                for old_line in old_property
            )
    lines.append(indent + "}")
    return lines


def _merge_toml(source: str, destination: str) -> str:
    try:
        source_doc = tomlkit.parse(source)
        destination_doc = tomlkit.parse(destination)
    except Exception as exc:
        raise MergeError(f"invalid TOML: {exc}") from exc
    conflicts: dict[tuple[str, ...], Any] = {}
    _merge_toml_tables(source_doc, destination_doc, (), conflicts)
    result = tomlkit.dumps(source_doc)
    return _inject_toml_backups(result, conflicts)


def _merge_toml_tables(
    source: MutableMapping[str, Any],
    destination: MutableMapping[str, Any],
    path: tuple[str, ...],
    conflicts: dict[tuple[str, ...], Any],
) -> None:
    for key in destination:
        child = path + (str(key),)
        if key not in source:
            source[key] = destination[key]
            continue
        left = source[key]
        right = destination[key]
        if isinstance(left, MutableMapping) and isinstance(right, MutableMapping):
            _merge_toml_tables(left, right, child, conflicts)
        elif left != right:
            conflicts[child] = right


_TOML_TABLE = re.compile(r"^\s*\[([^\]]+)]\s*(?:#.*)?$")
_ASSIGNMENT = re.compile(r'^\s*(?:"([^"]+)"|\'([^\']+)\'|([A-Za-z0-9_-]+))\s*=')


def _inject_toml_backups(text: str, conflicts: dict[tuple[str, ...], Any]) -> str:
    if not conflicts:
        return text
    output: list[str] = []
    table: tuple[str, ...] = ()
    handled: set[tuple[str, ...]] = set()
    for line in text.splitlines():
        output.append(line)
        table_match = _TOML_TABLE.match(line)
        if table_match:
            table = tuple(part.strip().strip("\"'") for part in table_match.group(1).split("."))
            continue
        assignment = _ASSIGNMENT.match(line)
        if not assignment:
            continue
        key = next(group for group in assignment.groups() if group is not None)
        path = table + (key,)
        if path in conflicts:
            rendered = tomlkit.dumps({key: conflicts[path]}).strip().splitlines()
            output.extend(f"# {BACKUP_MARKER} {old}" for old in rendered)
            handled.add(path)
    for path, value in conflicts.items():
        if path in handled:
            continue
        rendered = json.dumps(value, ensure_ascii=False, default=str)
        output.append(f"# {BACKUP_MARKER} {'.'.join(path)} = {rendered}")
    return "\n".join(output) + "\n"


def _ini_parser(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False, empty_lines_in_values=True)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise MergeError(f"invalid INI: {exc}") from exc
    return parser


def _merge_ini(source: str, destination: str) -> str:
    source_parser = _ini_parser(source)
    destination_parser = _ini_parser(destination)
    lines = source.splitlines()
    conflicts: dict[tuple[str, str], str] = {}
    missing: dict[str, list[tuple[str, str]]] = {}

    sections = [configparser.DEFAULTSECT, *destination_parser.sections()]
    for section in sections:
        destination_values = (
            destination_parser.defaults()
            if section == configparser.DEFAULTSECT
            else dict(destination_parser.items(section, raw=True))
        )
        source_values = (
            source_parser.defaults()
            if section == configparser.DEFAULTSECT
            else dict(source_parser.items(section, raw=True))
            if source_parser.has_section(section)
            else {}
        )
        for key, value in destination_values.items():
            if key not in source_values:
                missing.setdefault(section, []).append((key, value))
            elif source_values[key] != value:
                conflicts[(section, key)] = value

    output: list[str] = []
    section = configparser.DEFAULTSECT
    inserted: set[str] = set()
    for line in [*lines, None]:
        section_match = re.match(r"^\s*\[([^]]+)]", line or "")
        if section_match:
            if section in missing and section not in inserted:
                output.extend(f"{key} = {value}" for key, value in missing[section])
                inserted.add(section)
            section = section_match.group(1)
        if line is None:
            break
        output.append(line)
        assignment = re.match(r"^\s*([^#;][^:=\s]*)\s*[:=]", line)
        if assignment:
            key = assignment.group(1)
            old = conflicts.get((section, key))
            if old is not None:
                old_lines = str(old).splitlines() or [""]
                output.append(f"# {BACKUP_MARKER} {key} = {old_lines[0]}")
                output.extend(f"# {BACKUP_MARKER}   {item}" for item in old_lines[1:])

    if section in missing and section not in inserted:
        output.extend(f"{key} = {value}" for key, value in missing[section])
        inserted.add(section)
    for missing_section, values in missing.items():
        if missing_section in inserted:
            continue
        if output and output[-1]:
            output.append("")
        if missing_section != configparser.DEFAULTSECT:
            output.append(f"[{missing_section}]")
        output.extend(f"{key} = {value}" for key, value in values)
    return "\n".join(output) + "\n"
