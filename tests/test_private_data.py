from pathlib import Path

import pytest

from dotfiles.private_data import PrivateData, PrivateDataError


def test_private_data_loads_next_to_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "dotfiles.json"
    manifest.write_text("{}\n")
    (tmp_path / ".dtf-private.json").write_text(
        '{"host": "desktop", "mappings": '
        '[{"alias": "git", "map": {"author": "Jack", "email": "me@example.com"}}]}\n'
    )

    data = PrivateData.load_for_manifest(manifest)

    assert data.groups["git"].values == {"author": "Jack", "email": "me@example.com"}
    assert data.host == "desktop"


def test_private_data_legacy_array_defaults_to_single_host() -> None:
    data = PrivateData.from_value([{"alias": "git", "map": {"author": "Jack"}}])

    assert data.host == "single"


def test_private_data_requires_object_map() -> None:
    with pytest.raises(PrivateDataError, match="non-empty object"):
        PrivateData.from_value([{"alias": "git", "map": ["author", "Jack"]}])


def test_private_data_rejects_duplicate_values_within_alias() -> None:
    with pytest.raises(PrivateDataError, match="duplicates"):
        PrivateData.from_value(
            [{"alias": "git", "map": {"author": "same", "email": "same"}}]
        )


def test_private_data_rejects_unknown_manifest_alias(tmp_path: Path) -> None:
    manifest = tmp_path / "dotfiles.json"
    manifest.write_text("{}\n")
    (tmp_path / ".dtf-private.json").write_text(
        '[{"alias": "git", "map": {"author": "Jack"}}]\n'
    )

    with pytest.raises(PrivateDataError, match="unknown manifest alias"):
        PrivateData.load_for_manifest(manifest, aliases={"shell"})
