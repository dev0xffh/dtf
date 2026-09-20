from pathlib import Path

from dotfiles.manifest import AppConfig, Manifest
from dotfiles.operations import remove_repository_configs, sensitive_findings, transfer
from dotfiles.private_data import PrivateData


def _manifest() -> Manifest:
    return Manifest(
        project="dev0xffh/dotfiles",
        version=1,
        apps={
            "example": AppConfig(
                app="example", config_type="INI", paths=("~/.config/example",)
            )
        },
    )


def test_get_and_set_mirror_directories(tmp_path: Path) -> None:
    home = tmp_path / "home"
    manifest_path = tmp_path / "repo/dotfiles.json"
    machine = home / ".config/example"
    machine.mkdir(parents=True)
    (machine / "current.ini").write_text("value = machine\n")
    selected = list(_manifest().apps.items())

    result = transfer(selected, manifest_path, direction="get", merge=False, home=home)
    repository = tmp_path / "repo/configs/main/home/.config/example"
    assert not result.errors
    assert (repository / "current.ini").read_text() == "value = machine\n"

    (repository / "current.ini").write_text("value = repository\n")
    (repository / "new.ini").write_text("new = yes\n")
    (machine / "machine-only.ini").write_text("remove = yes\n")
    result = transfer(selected, manifest_path, direction="set", merge=False, home=home)

    assert not result.errors
    assert sorted(path.name for path in machine.iterdir()) == ["current.ini", "new.ini"]
    assert (machine / "current.ini").read_text() == "value = repository\n"


def test_get_and_set_use_named_host_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    manifest_path = tmp_path / "repo/dotfiles.json"
    machine = home / ".config/example/settings.ini"
    machine.parent.mkdir(parents=True)
    machine.write_text("value = desktop\n")
    selected = list(_manifest().apps.items())

    result = transfer(
        selected, manifest_path, direction="get", merge=False, home=home, host="desktop"
    )
    repository = tmp_path / "repo/configs/desktop/home/.config/example/settings.ini"
    assert not result.errors
    assert repository.read_text() == "value = desktop\n"

    repository.write_text("value = repository\n")
    result = transfer(
        selected, manifest_path, direction="set", merge=False, home=home, host="desktop"
    )
    assert not result.errors
    assert machine.read_text() == "value = repository\n"


def test_batch_continues_after_missing_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    manifest_path = tmp_path / "repo/dotfiles.json"
    present = home / ".config/present.ini"
    present.parent.mkdir(parents=True)
    present.write_text("ok = yes\n")
    selected = [
        (
            "example",
            AppConfig(
                app="example",
                config_type="INI",
                paths=("~/.config/missing.ini", "~/.config/present.ini"),
            ),
        )
    ]

    result = transfer(selected, manifest_path, direction="get", merge=False, home=home)

    assert result.completed == 1
    assert len(result.errors) == 1
    assert (tmp_path / "repo/configs/main/home/.config/present.ini").exists()


def test_secret_guard_finds_private_key(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = home / ".config/example"
    target.mkdir(parents=True)
    (target / "id_ed25519").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-key\n"
    )

    findings = sensitive_findings(list(_manifest().apps.items()), home=home)

    assert findings
    assert any(finding.reason == "private key" for finding in findings)


def test_remove_repository_configs_never_touches_machine(tmp_path: Path) -> None:
    home = tmp_path / "home"
    machine = home / ".config/example"
    machine.mkdir(parents=True)
    (machine / "settings.ini").write_text("keep = yes\n")
    manifest_path = tmp_path / "repo/dotfiles.json"
    repository = tmp_path / "repo/configs/main/home/.config/example"
    repository.mkdir(parents=True)
    (repository / "settings.ini").write_text("delete = yes\n")

    errors = remove_repository_configs(["example"], _manifest(), manifest_path)

    assert not errors
    assert not repository.exists()
    assert (machine / "settings.ini").exists()


def test_private_mapping_round_trips_get_and_set(tmp_path: Path) -> None:
    home = tmp_path / "home"
    manifest_path = tmp_path / "repo/dotfiles.json"
    machine = home / ".gitconfig"
    machine.parent.mkdir(parents=True)
    machine.write_text("[user]\n\tname = Jack\n\temail = my@email.com\n")
    private_data = PrivateData.from_value(
        [
            {
                "alias": "example",
                "map": {"author": "Jack", "email": "my@email.com"},
            }
        ]
    )
    manifest = Manifest(
        project="example/dotfiles",
        version=1,
        apps={"example": AppConfig(app="git", config_type="INI", paths=("~/.gitconfig",))},
    )
    selected = list(manifest.apps.items())

    result = transfer(
        selected,
        manifest_path,
        direction="get",
        merge=False,
        home=home,
        private_data=private_data,
    )
    repository = tmp_path / "repo/configs/main/home/.gitconfig"
    assert not result.errors
    assert "{{author}}" in repository.read_text()
    assert "{{email}}" in repository.read_text()
    assert "Jack" not in repository.read_text()

    result = transfer(
        selected,
        manifest_path,
        direction="set",
        merge=False,
        home=home,
        private_data=private_data,
    )
    assert not result.errors
    assert "Jack" in machine.read_text()
    assert "my@email.com" in machine.read_text()
    assert "{{author}}" not in machine.read_text()


def test_private_mapping_transforms_directory_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    manifest_path = tmp_path / "repo/dotfiles.json"
    machine = home / ".config/git"
    machine.mkdir(parents=True)
    (machine / "user.conf").write_text("author = Jack\n")
    private_data = PrivateData.from_value(
        [{"alias": "git", "map": {"author": "Jack"}}]
    )
    manifest = Manifest(
        project="example/dotfiles",
        version=1,
        apps={"git": AppConfig(app="git", config_type="INI", paths=("~/.config/git",))},
    )

    result = transfer(
        list(manifest.apps.items()),
        manifest_path,
        direction="get",
        merge=False,
        home=home,
        private_data=private_data,
    )

    assert not result.errors
    assert (tmp_path / "repo/configs/main/home/.config/git/user.conf").read_text() == (
        "author = {{author}}\n"
    )
