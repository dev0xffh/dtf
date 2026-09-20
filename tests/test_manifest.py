import json
import subprocess
from pathlib import Path

import pytest

from dotfiles.cli import main
from dotfiles.manifest import Manifest, ManifestError, machine_path, repo_path


def test_init_creates_project_manifest(tmp_path: Path) -> None:
    path = tmp_path / "dotfiles.json"

    assert (
        main(
            [
                "--manifest",
                str(path),
                "init",
                "--project",
                "example/dotfiles",
            ]
        )
        == 0
    )
    assert json.loads(path.read_text()) == {
        "project": "example/dotfiles",
        "version": 1,
        "apps": {},
    }
    assert main(["--manifest", str(path), "init", "--project", "example/dotfiles"]) == 2


def test_init_detects_project_from_github_origin(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "git@github.com:example/configs.git",
        ],
        check=True,
    )
    path = tmp_path / "dotfiles.json"

    assert main(["--manifest", str(path), "init"]) == 0
    assert Manifest.load(path).project == "example/configs"


def test_init_creates_dotfiles_repository_skeleton(tmp_path: Path) -> None:
    repository = tmp_path / "dotfiles"

    assert main(["init", str(repository), "--project", "example/dotfiles"]) == 0

    assert Manifest.load(repository / "dotfiles.json").project == "example/dotfiles"
    assert json.loads((repository / ".dtf-private.json").read_text()) == {
        "host": "main",
        "mappings": [],
    }
    assert (repository / ".dtf-private.example.json").exists()
    assert (repository / ".gitignore").exists()
    assert (repository / "configs/main/home/.gitkeep").exists()
    assert (repository / "configs/main/root/.gitkeep").exists()
    assert (repository / "AGENTS.md").read_bytes() == (repository / "CLAUDE.md").read_bytes()


def test_init_does_not_overwrite_existing_skeleton(tmp_path: Path) -> None:
    repository = tmp_path / "dotfiles"
    repository.mkdir()
    (repository / "README.md").write_text("keep\n")

    assert main(["init", str(repository), "--project", "example/dotfiles"]) == 2
    assert (repository / "README.md").read_text() == "keep\n"


def test_add_uses_application_name_as_alias(tmp_path: Path) -> None:
    path = tmp_path / "dotfiles.json"
    Manifest.empty("example/dotfiles").save(path)

    assert (
        main(
            [
                "--manifest",
                str(path),
                "add",
                "zed",
                "$HOME/.config/zed/settings.json",
            ]
        )
        == 0
    )

    manifest = Manifest.load(path)
    assert manifest.apps["zed"].app == "zed"
    assert manifest.apps["zed"].config_type == "JSON5"
    assert manifest.apps["zed"].paths == ("~/.config/zed/settings.json",)


def test_add_rejects_removed_name_option(tmp_path: Path) -> None:
    path = tmp_path / "dotfiles.json"
    Manifest.empty("example/dotfiles").save(path)

    with pytest.raises(SystemExit) as exit_info:
        main(["--manifest", str(path), "add", "git", "~/.gitconfig", "--name", "work-git"])

    assert exit_info.value.code == 2


@pytest.mark.parametrize(
    ("app", "machine_path_value"),
    [("zsh", "~/.zshrc"), ("prompt", "~/.p10k.zsh"), ("scripts", "~/bin/setup.sh")],
)
def test_add_detects_shell_type(
    tmp_path: Path, app: str, machine_path_value: str
) -> None:
    path = tmp_path / "dotfiles.json"
    Manifest.empty("example/dotfiles").save(path)

    assert main(["--manifest", str(path), "add", app, machine_path_value]) == 0
    assert Manifest.load(path).apps[app].config_type == "SHELL"


def test_add_accepts_explicit_type(tmp_path: Path) -> None:
    path = tmp_path / "dotfiles.json"
    Manifest.empty("example/dotfiles").save(path)

    assert (
        main(
            [
                "--manifest",
                str(path),
                "add",
                "example",
                "~/.config/example/settings.data",
                "--type",
                "json5",
            ]
        )
        == 0
    )
    assert Manifest.load(path).apps["example"].config_type == "JSON5"


def test_rejects_paths_owned_by_multiple_aliases(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="owned"):
        Manifest.from_dict(
            {
                "project": "example/dotfiles",
                "version": 1,
                "apps": {
                    "zed": {
                        "app": "zed",
                        "type": "JSON5",
                        "paths": ["~/.config/zed"],
                    },
                    "other": {
                        "app": "other",
                        "type": "JSON5",
                        "paths": ["~/.config/zed"],
                    },
                },
            }
        )


def test_path_mapping(tmp_path: Path) -> None:
    manifest = tmp_path / "dotfiles.json"
    home = tmp_path / "home"

    assert machine_path("~/.config/zed", home=home) == home / ".config/zed"
    assert repo_path("~/.config/zed", manifest) == tmp_path / "configs/main/home/.config/zed"
    assert repo_path("/etc/example", manifest) == tmp_path / "configs/main/root/etc/example"
    assert repo_path("~/.config/zed", manifest, "desktop") == (
        tmp_path / "configs/desktop/home/.config/zed"
    )


def test_manifest_requires_project() -> None:
    with pytest.raises(ManifestError, match="project"):
        Manifest.from_dict({"version": 1, "apps": {}})


def test_manifest_rejects_unsupported_type() -> None:
    with pytest.raises(ManifestError, match="type"):
        Manifest.from_dict(
            {
                "project": "dev0xffh/dotfiles",
                "version": 1,
                "apps": {
                    "example": {
                        "app": "example",
                        "type": "XML",
                        "paths": ["~/.config/example.xml"],
                    }
                },
            }
        )


def test_manifest_defaults_legacy_apps_to_main_host() -> None:
    manifest = Manifest.from_dict(
        {
            "project": "dev0xffh/dotfiles",
            "version": 1,
            "apps": {
                "example": {
                    "app": "example",
                    "type": "INI",
                    "paths": ["~/.example"],
                }
            },
        }
    )

    assert manifest.apps["example"].hosts == ("main",)


def test_manifest_rejects_main_combined_with_named_host() -> None:
    with pytest.raises(ManifestError, match="cannot be combined"):
        Manifest.from_dict(
            {
                "project": "dev0xffh/dotfiles",
                "version": 1,
                "apps": {
                    "example": {
                        "app": "example",
                        "type": "INI",
                        "paths": ["~/.example"],
                        "hosts": ["main", "desktop"],
                    }
                },
            }
        )
