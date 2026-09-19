from pathlib import Path

import json5
import pytest
import tomlkit

from dotfiles.merge import BACKUP_MARKER, MergeError, merge_file


def test_zed_json_merge_uses_source_and_comments_old_value(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_text('{\n  "limit": 30,\n  "nested": {"font": 14}\n}\n')
    destination.write_text('{\n  "limit": 10,\n  "nested": {"font": 12, "theme": "dark"}\n}\n')

    merge_file(source, destination, config_type="JSONC")

    result = destination.read_text()
    parsed = json5.loads(result)
    assert parsed == {"limit": 30, "nested": {"font": 14, "theme": "dark"}}
    assert '// -- BACKUP -- "limit": 10' in result
    assert '// -- BACKUP -- "font": 12' in result


def test_zed_json_merge_does_not_accumulate_backups(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_text('{"limit": 30}\n')
    destination.write_text('{"limit": 10}\n')

    merge_file(source, destination, config_type="JSONC")
    first = destination.read_text()
    merge_file(source, destination, config_type="JSONC")

    assert first == destination.read_text()
    assert destination.read_text().count(BACKUP_MARKER) == 1


def test_strict_json_merge_is_rejected_on_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_text('{"limit": 30}\n')
    destination.write_text('{"limit": 10}\n')

    with pytest.raises(MergeError, match="strict JSON"):
        merge_file(source, destination, config_type="JSON")


def test_strict_json_merges_destination_only_keys(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_text('{"limit": 30}\n')
    destination.write_text('{"limit": 30, "theme": "dark"}\n')

    merge_file(source, destination, config_type="JSON")

    assert json5.loads(destination.read_text()) == {"limit": 30, "theme": "dark"}


def test_json5_merge_supports_json5_input(tmp_path: Path) -> None:
    source = tmp_path / "source.json5"
    destination = tmp_path / "destination.json5"
    source.write_text("{limit: 30,}\n")
    destination.write_text("{'limit': 10, theme: 'dark'}\n")

    merge_file(source, destination, config_type="JSON5")

    result = destination.read_text()
    assert json5.loads(result) == {"limit": 30, "theme": "dark"}
    assert BACKUP_MARKER in result


def test_toml_merge_keeps_destination_only_and_comments_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source.toml"
    destination = tmp_path / "destination.toml"
    source.write_text("[editor]\nlimit = 30\n")
    destination.write_text('[editor]\nlimit = 10\ntheme = "dark"\n')

    merge_file(source, destination, config_type="TOML")

    result = destination.read_text()
    parsed = tomlkit.parse(result)
    assert parsed["editor"]["limit"] == 30
    assert parsed["editor"]["theme"] == "dark"
    assert "# -- BACKUP -- limit = 10" in result


def test_ini_merge_keeps_destination_only_and_comments_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source.ini"
    destination = tmp_path / "destination.ini"
    source.write_text("[editor]\nlimit = 30\n")
    destination.write_text("[editor]\nlimit = 10\ntheme = dark\n")

    merge_file(source, destination, config_type="INI")

    result = destination.read_text()
    assert "limit = 30\n# -- BACKUP -- limit = 10" in result
    assert "theme = dark" in result


def test_merge_preserves_crlf(tmp_path: Path) -> None:
    source = tmp_path / "source.ini"
    destination = tmp_path / "destination.ini"
    source.write_bytes(b"[editor]\r\nlimit = 30\r\n")
    destination.write_bytes(b"[editor]\r\nlimit = 10\r\n")

    merge_file(source, destination, config_type="INI")

    result = destination.read_bytes()
    assert b"\r\n" in result
    assert b"\n" not in result.replace(b"\r\n", b"")


def test_shell_merge_comments_conflicts_and_keeps_simple_destination_lines(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.zsh"
    destination = tmp_path / "destination.zsh"
    source.write_text("export EDITOR=nvim\nalias ll='ls -la'\n")
    destination.write_text(
        "export EDITOR=vim\nalias ll='ls'\nsource ~/.config/shell/local.zsh\n"
    )

    merge_file(source, destination, config_type="SHELL")

    result = destination.read_text()
    assert "export EDITOR=nvim\n# -- BACKUP -- export EDITOR=vim" in result
    assert "alias ll='ls -la'\n# -- BACKUP -- alias ll='ls'" in result
    assert "# -- MERGED DESTINATION-ONLY --\nsource ~/.config/shell/local.zsh" in result


def test_shell_merge_rejects_destination_only_compound_syntax(tmp_path: Path) -> None:
    source = tmp_path / "source.zsh"
    destination = tmp_path / "destination.zsh"
    source.write_text("export EDITOR=nvim\n")
    original = "if [[ -f ~/.local.zsh ]]; then\n  source ~/.local.zsh\nfi\n"
    destination.write_text(original)

    with pytest.raises(MergeError, match="compound shell syntax"):
        merge_file(source, destination, config_type="SHELL")

    assert destination.read_text() == original
