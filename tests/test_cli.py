import json
from pathlib import Path

import pytest

from dotfiles.cli import VERSION, main
from dotfiles.manifest import AppConfig, Manifest


def test_help_command_matches_short_help(capsys) -> None:
    assert main(["help"]) == 0
    command_help = capsys.readouterr().out

    with pytest.raises(SystemExit) as exit_info:
        main(["-h"])
    short_help = capsys.readouterr().out

    assert exit_info.value.code == 0
    assert command_help == short_help
    assert "{help,init,add,list,get,set,del}" in command_help


@pytest.mark.parametrize("option", ["--version", "-V"])
def test_version_options(option: str, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([option])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"dtf {VERSION}\n"


def test_del_requires_confirmation_and_can_cancel(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "dotfiles.json"
    manifest = Manifest(
        project="dev0xffh/dotfiles",
        version=1,
        apps={
            "editor": AppConfig(app="zed", config_type="JSONC", paths=("~/.config/zed",))
        },
    )
    manifest.save(path)
    monkeypatch.setattr("builtins.input", lambda _: "no")

    assert main(["--manifest", str(path), "del", "editor"]) == 0
    assert "editor" in Manifest.load(path).apps


def test_del_yes_removes_manifest_entry_and_repository_copy(tmp_path: Path) -> None:
    path = tmp_path / "dotfiles.json"
    manifest = Manifest(
        project="dev0xffh/dotfiles",
        version=1,
        apps={
            "editor": AppConfig(app="zed", config_type="JSONC", paths=("~/.config/zed",))
        },
    )
    manifest.save(path)
    repository = tmp_path / "configs/single/home/.config/zed"
    repository.mkdir(parents=True)
    (repository / "settings.json").write_text("{}\n")

    assert main(["--manifest", str(path), "del", "editor", "--yes"]) == 0
    assert not repository.exists()
    assert json.loads(path.read_text())["apps"] == {}


def test_get_host_converts_single_to_named_host_and_set_uses_private_host(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "dotfiles.json"
    home = tmp_path / "machine-home"
    machine = home / ".config/example/settings.ini"
    machine.parent.mkdir(parents=True)
    machine.write_text("value = machine\n")
    manifest = Manifest(
        project="dev0xffh/dotfiles",
        version=1,
        apps={
            "example": AppConfig(
                app="example", config_type="INI", paths=("~/.config/example/settings.ini",)
            )
        },
    )
    manifest.save(path)
    old_repository = tmp_path / "configs/single/home/.config/example/settings.ini"
    old_repository.parent.mkdir(parents=True)
    old_repository.write_text("value = shared\n")
    (tmp_path / ".dtf-private.json").write_text(
        '{"host": "desktop", "mappings": []}\n'
    )

    def fake_machine_path(value: str, *, home: Path | None = None) -> Path:
        assert value == "~/.config/example/settings.ini"
        return machine

    monkeypatch.setattr("dotfiles.operations.machine_path", fake_machine_path)

    assert main(["--manifest", str(path), "get", "--host", "desktop", "example"]) == 0
    updated = Manifest.load(path)
    assert updated.apps["example"].hosts == ("desktop",)
    repository = tmp_path / "configs/desktop/home/.config/example/settings.ini"
    assert repository.read_text() == "value = machine\n"
    assert not old_repository.exists()

    (tmp_path / ".dtf-private.json").write_text(
        '{"host": "laptop", "mappings": []}\n'
    )
    assert main(["--manifest", str(path), "set", "example"]) == 2
    (tmp_path / ".dtf-private.json").write_text(
        '{"host": "desktop", "mappings": []}\n'
    )

    repository.write_text("value = repository\n")
    assert main(["--manifest", str(path), "set", "example"]) == 0
    assert machine.read_text() == "value = repository\n"

    machine.write_text("value = laptop\n")
    assert main(["--manifest", str(path), "get", "--host", "laptop", "example"]) == 0
    assert Manifest.load(path).apps["example"].hosts == ("desktop", "laptop")
    assert (
        tmp_path / "configs/laptop/home/.config/example/settings.ini"
    ).read_text() == "value = laptop\n"
