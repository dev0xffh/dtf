from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from dotfiles.manifest import (
    DEFAULT_HOST,
    SUPPORTED_TYPES,
    AppConfig,
    Manifest,
    ManifestError,
    machine_path,
    normalize_host,
    normalize_machine_path,
    repo_path,
)
from dotfiles.operations import (
    OperationError,
    remove_repository_configs,
    select_apps,
    sensitive_findings,
    transfer,
)
from dotfiles.private_data import PrivateData

VERSION = "0.2.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dtf", description="Collect and deploy dotfiles.")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("dotfiles.json"),
        help="manifest path (default: ./dotfiles.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("help", help="show this help message")
    init = subparsers.add_parser("init", help="create a dotfiles repository skeleton")
    init.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="repository path (default: prompt, then $HOME/dotfiles)",
    )
    init.add_argument(
        "--project",
        help="project identity as owner/repository (default: detect from GitHub origin)",
    )

    add = subparsers.add_parser("add", help="register paths for an application")
    add.add_argument("app", help="application name and manifest alias, for example zed")
    add.add_argument("paths", nargs="+", help="absolute paths or paths beginning with ~/ or $HOME/")
    add.add_argument(
        "-t",
        "--type",
        dest="config_type",
        type=str.upper,
        choices=sorted(SUPPORTED_TYPES),
        help="configuration format (default: detect from app, path, or content)",
    )

    subparsers.add_parser("list", help="list registered applications")

    for command in ("get", "set"):
        action = subparsers.add_parser(command, help=f"{command} configuration files")
        action.add_argument("aliases", nargs="*", help="aliases to process (default: all)")
        action.add_argument("-m", "--merge", action="store_true", help="merge supported formats")
        if command == "get":
            action.add_argument(
                "--host",
                help="collect into this host and register it for selected aliases",
            )
            action.add_argument(
                "--allow-sensitive",
                action="store_true",
                help="collect files even when the secret guard reports a match",
            )

    delete = subparsers.add_parser("del", help="remove applications from the repository")
    delete.add_argument("aliases", nargs="+", help="aliases to remove")
    delete.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "help":
        parser.print_help()
        return 0
    manifest_path = args.manifest.expanduser().resolve()
    try:
        if args.command == "init":
            return _init(args.path, args.project, args.manifest)
        manifest = Manifest.load(manifest_path)
        if args.command == "add":
            return _add(args, manifest, manifest_path)
        if args.command == "list":
            return _list(manifest, manifest_path)
        if args.command in {"get", "set"}:
            return _transfer(args, manifest, manifest_path)
        if args.command == "del":
            return _delete(args, manifest, manifest_path)
    except (ManifestError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


def _init(path: Path | None, project: str | None, manifest_option: Path) -> int:
    if path is None and manifest_option != Path("dotfiles.json"):
        path = manifest_option.parent
    if path is None:
        fallback = Path.home() / "dotfiles"
        try:
            answer = input(f"Dotfiles repository path [{fallback}]: ").strip()
        except EOFError:
            answer = ""
        path = Path(answer).expanduser() if answer else fallback
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)

    managed = {
        "dotfiles.json",
        ".dtf-private.json",
        ".dtf-private.example.json",
        ".gitignore",
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "configs",
    }
    conflicts = sorted(item for item in managed if (path / item).exists())
    if conflicts:
        raise ManifestError(
            f"repository skeleton already contains managed paths: {', '.join(conflicts)}"
        )

    project = project or _project_from_git(path) or f"local/{path.name}"
    Manifest.empty(project).save(path / "dotfiles.json")
    (path / ".dtf-private.json").write_text(
        '{\n  "host": "single",\n  "mappings": []\n}\n', encoding="utf-8"
    )
    for namespace in ("home", "root"):
        directory = path / "configs" / DEFAULT_HOST / namespace
        directory.mkdir(parents=True)
        (directory / ".gitkeep").write_text("", encoding="utf-8")
    template_root = Path(__file__).parent / "templates" / "dotfiles"
    for name in (
        ".dtf-private.example.json",
        ".gitignore",
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
    ):
        shutil.copyfile(template_root / name, path / name)
    print(f"created dotfiles repository skeleton at {path}")
    return 0


def _project_from_git(directory: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    remote = result.stdout.strip()
    match = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?$", remote)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _add(args: argparse.Namespace, manifest: Manifest, path: Path) -> int:
    app_name = args.app.strip()
    if not app_name:
        raise ManifestError("app must be non-empty")
    alias = app_name
    normalized = [normalize_machine_path(item) for item in args.paths]
    current = manifest.apps.get(alias)
    if current and current.app != app_name:
        raise ManifestError(
            f"alias {alias!r} already belongs to app {current.app!r}, not {app_name!r}"
        )
    paths = list(current.paths if current else ())
    for item in normalized:
        if item not in paths:
            paths.append(item)
    config_type = args.config_type
    if config_type is None:
        config_type = current.config_type if current else _detect_config_type(app_name, normalized)
    raw = manifest.to_dict()
    raw["apps"][alias] = {
        "app": app_name,
        "type": config_type,
        "paths": paths,
        "hosts": list(current.hosts) if current else [DEFAULT_HOST],
    }
    updated = Manifest.from_dict(raw)
    updated.save(path)
    print(f"registered {alias}: {len(paths)} path(s), type {config_type}")
    return 0


def _list(manifest: Manifest, manifest_path: Path) -> int:
    if not manifest.apps:
        print("No applications registered.")
        return 0
    print("ALIAS\tAPP\tTYPE\tHOST\tMACHINE PATH\tREPOSITORY PATH\tSTATUS")
    for alias, app in manifest.apps.items():
        for host in app.hosts:
            for value in app.paths:
                repository = repo_path(value, manifest_path, host)
                machine_status = "machine" if _machine_exists(value) else "-"
                repo_status = "repo" if repository.exists() else "-"
                print(
                    f"{alias}\t{app.app}\t{app.config_type}\t{host}\t{value}\t"
                    f"{repository.relative_to(manifest_path.parent)}"
                    f"\t{machine_status}/{repo_status}"
                )
    return 0


def _machine_exists(value: str) -> bool:
    return machine_path(value).exists()


def _detect_config_type(app: str, values: list[str]) -> str:
    known_apps = {
        "bash": "SHELL",
        "git": "INI",
        "p10k": "SHELL",
        "shell": "SHELL",
        "zed": "JSON5",
        "zsh": "SHELL",
    }
    if app.casefold() in known_apps:
        return known_apps[app.casefold()]

    detected: set[str] = set()
    for value in values:
        path = machine_path(value)
        candidates = [path]
        if path.is_dir():
            candidates = [item for item in path.rglob("*") if item.is_file()]
        for candidate in candidates:
            config_type = _type_from_path(candidate)
            if config_type:
                detected.add(config_type)
    if len(detected) == 1:
        return detected.pop()
    if not detected:
        raise ManifestError("cannot detect config type; pass --type/-t")
    rendered = ", ".join(sorted(detected))
    raise ManifestError(f"multiple config types detected ({rendered}); use separate aliases")


def _type_from_path(path: Path) -> str | None:
    shell_names = {
        ".bash_login",
        ".bash_logout",
        ".bash_profile",
        ".bashrc",
        ".kshrc",
        ".profile",
        ".zlogin",
        ".zlogout",
        ".zprofile",
        ".zshenv",
        ".zshrc",
        "bashrc",
        "zshrc",
    }
    if path.name.casefold() in shell_names:
        return "SHELL"
    suffixes = {
        ".bash": "SHELL",
        ".ini": "INI",
        ".cfg": "INI",
        ".conf": "INI",
        ".sh": "SHELL",
        ".toml": "TOML",
        ".json": "JSON",
        ".jsonc": "JSONC",
        ".json5": "JSON5",
        ".zsh": "SHELL",
    }
    return suffixes.get(path.suffix.casefold())


def _transfer(args: argparse.Namespace, manifest: Manifest, manifest_path: Path) -> int:
    selected = select_apps(manifest, args.aliases)
    private_data = PrivateData.load_for_manifest(manifest_path, manifest.apps)
    requested_host = getattr(args, "host", None)
    host = normalize_host(requested_host) if requested_host else private_data.host
    _validate_host_selection(
        selected, host, registering=args.command == "get" and bool(requested_host)
    )
    if args.command == "get" and not args.allow_sensitive:
        findings = sensitive_findings(selected)
        if findings:
            print("error: secret guard blocked collection:", file=sys.stderr)
            for finding in findings:
                print(f"  {finding.path}: {finding.reason}", file=sys.stderr)
            print("Review the files, then use --allow-sensitive to override.", file=sys.stderr)
            return 1
    result = transfer(
        selected,
        manifest_path,
        direction=args.command,
        merge=args.merge,
        host=host,
        private_data=private_data,
    )
    if args.command == "get" and requested_host:
        _record_collected_host(manifest, selected, host, manifest_path, result.errors)
    for error in result.errors:
        print(f"error: {error.alias} {error.path}: {error.message}", file=sys.stderr)
    print(
        f"{args.command} ({host}): {result.completed} completed, {len(result.errors)} failed"
    )
    return 1 if result.errors else 0


def _validate_host_selection(
    selected: list[tuple[str, AppConfig]], host: str, *, registering: bool
) -> None:
    unavailable: list[str] = []
    for alias, app in selected:
        if host in app.hosts:
            continue
        if registering and host != DEFAULT_HOST:
            continue
        unavailable.append(alias)
    if unavailable:
        rendered = ", ".join(unavailable)
        raise ManifestError(
            f"host {host!r} is not registered for: {rendered}; "
            f"use get --host {host} to collect it first"
        )


def _record_collected_host(
    manifest: Manifest,
    selected: list[tuple[str, AppConfig]],
    host: str,
    manifest_path: Path,
    transfer_errors: list[OperationError],
) -> None:
    failed_aliases = {error.alias for error in transfer_errors}
    updates: dict[str, AppConfig] = {}
    for alias, app in selected:
        if alias in failed_aliases or host in app.hosts:
            continue
        if app.hosts == (DEFAULT_HOST,):
            cleanup_errors = remove_repository_configs(
                [alias], manifest, manifest_path, hosts=(DEFAULT_HOST,)
            )
            transfer_errors.extend(cleanup_errors)
            if cleanup_errors:
                continue
            updates[alias] = replace(app, hosts=(host,))
        else:
            updates[alias] = replace(app, hosts=(*app.hosts, host))
    if updates:
        manifest.apps.update(updates)
        manifest.save(manifest_path)


def _delete(args: argparse.Namespace, manifest: Manifest, manifest_path: Path) -> int:
    aliases = list(dict.fromkeys(args.aliases))
    missing = [alias for alias in aliases if alias not in manifest.apps]
    if missing:
        raise ManifestError("unknown app alias: " + ", ".join(missing))
    if not args.yes:
        rendered = ", ".join(aliases)
        answer = input(
            f"Delete {rendered} from the manifest and repository configs? [y/N] "
        ).strip()
        if answer.casefold() not in {"y", "yes"}:
            print("cancelled")
            return 0

    errors = remove_repository_configs(aliases, manifest, manifest_path)
    if errors:
        for error in errors:
            print(f"error: {error.alias} {error.path}: {error.message}", file=sys.stderr)
        print("manifest unchanged because repository cleanup failed", file=sys.stderr)
        return 1
    for alias in aliases:
        del manifest.apps[alias]
    manifest.save(manifest_path)
    print(f"deleted {len(aliases)} application(s)")
    return 0
