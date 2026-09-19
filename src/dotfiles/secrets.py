from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SecretFinding:
    path: Path
    reason: str


_SENSITIVE_NAMES = (
    re.compile(r"(^|[._-])\.env($|[._-])", re.IGNORECASE),
    re.compile(r"(^|[._-])(credentials?|secrets?|tokens?)([._-]|$)", re.IGNORECASE),
    re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(\.pem)?$", re.IGNORECASE),
)
_SENSITIVE_CONTENT = (
    (re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (
        re.compile(
            rb"(?im)^\s*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
            rb"\s*[:=]\s*['\"]?[^\s'\"]{8,}"
        ),
        "credential assignment",
    ),
    (re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}"), "GitHub token"),
    (re.compile(rb"AKIA[0-9A-Z]{16}"), "AWS access key"),
)


def scan_paths(paths: list[Path]) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    seen: set[tuple[int, int]] = set()
    for root in paths:
        if not root.exists():
            continue
        for path in _files(root, seen, set()):
            for pattern in _SENSITIVE_NAMES:
                if pattern.search(path.name):
                    findings.append(SecretFinding(path, "sensitive filename"))
                    break
            try:
                with path.open("rb") as handle:
                    content = handle.read(2 * 1024 * 1024)
            except OSError:
                continue
            if b"\x00" in content:
                continue
            for pattern, reason in _SENSITIVE_CONTENT:
                if pattern.search(content):
                    findings.append(SecretFinding(path, reason))
                    break
    return findings


def _files(path: Path, seen_files: set[tuple[int, int]], active_dirs: set[tuple[int, int]]):
    stat = path.stat()
    identity = (stat.st_dev, stat.st_ino)
    if path.is_dir():
        if identity in active_dirs:
            raise OSError(f"symbolic-link cycle detected at {path}")
        active_dirs.add(identity)
        try:
            for child in path.iterdir():
                yield from _files(child, seen_files, active_dirs)
        finally:
            active_dirs.remove(identity)
    elif identity not in seen_files:
        seen_files.add(identity)
        yield path
