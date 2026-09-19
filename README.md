# dtf

`dtf` is a small Linux dotfiles CLI collector and deployer. The CLI lives in this repository; your
configuration files live in a separate repository that contains only `dotfiles.json` and the
generated `configs/` tree.

The layout mirrors conventional Linux (RHEL) paths without embedding a user name:

| Machine | Configuration repository |
| --- | --- |
| `~/.config/zed/settings.json` | `{YOUR_REPO}/configs/home/.config/zed/settings.json` |
| `/etc/example/config.toml` | `{YOUR_REPO}/configs/root/etc/example/config.toml` |

## Installation

Install from GitHub with `uv`:

```bash
uv tool install git+https://github.com/dev0xffh/dtf.git
dtf --version
```

Python 3.11 or newer is required.

## Create a configuration repository

Create a new repository rather than forking `dtf`:

```bash
# Create a new repository for your dotfiles
mkdir dotfiles
cd dotfiles
git init
git remote add origin git@github.com:{YOUR_OWNER}/{YOUR_REPO}

# Initialize the dotfiles repository
dtf init --project {YOUR_OWNER}/{YOUR_REPO}
dtf add zed ~/.config/zed
dtf get

# Commit and push changes
git add .
git commit -m "Initial commit"
git push
```

If a GitHub `origin` already exists, `dtf init` detects `owner/repository` automatically and
`--project` can be omitted.

`dotfiles.json` is the only source of truth in the configuration repository:

```json
{
  "project": "owner/dotfiles",
  "version": 1,
  "apps": {
    "editor": {
      "app": "zed",
      "type": "JSON5",
      "paths": [
        "~/.config/zed/settings.json",
        "~/.config/zed/keymap.json"
      ]
    }
  }
}
```

The object key is the alias used by commands. The `app` field records the application name;
without `--name`, both values are identical. `type` must be one of `INI`, `TOML`, `JSON`,
`JSONC`, or `JSON5`.

## Usage

```bash
dtf help
dtf --version
```
Register applications and optional aliases:

```bash
dtf add zed ~/.config/zed
dtf add git ~/.gitconfig ~/.config/git --name work-git --type INI
```

`add` infers the type from a known application, file extension, or existing directory contents.
Use `--type`/`-t` to override it. One alias has one format; register mixed-format trees as
separate aliases.

Inspect, collect, and deploy all entries or selected aliases:

```bash
dtf list
dtf get
dtf get zed work-git
dtf set zed
```

Without `--merge`, the destination becomes an exact copy of the source. A directory replacement
removes destination-only entries. Writes are staged before replacement.

Use `--merge`/`-m` for a structured merge:

```bash
dtf get --merge zed
dtf set -m work-git
```

The source wins: the machine for `get`, and the configuration repository for `set`.
Destination-only keys remain. For INI, TOML, JSONC, and JSON5, an old conflicting value is
placed immediately after the active value:

```toml
limit = 30
# -- BACKUP -- limit = 10
```

```jsonc
"limit": 30
// -- BACKUP -- "limit": 10
```

Zed is detected as JSON5 because its configuration accepts comments and trailing commas,
although Zed labels the language JSONC. Strict JSON can merge destination-only keys, but a value
conflict is rejected because JSON cannot contain the required backup comment.

Remove aliases from the manifest and repository copy:

```bash
dtf del work-git
dtf del --yes zed work-git
```

Without `--yes`/`-y`, `dtf` asks for confirmation. `del` never touches machine files.

Use another manifest by placing the global option before the command:

```bash
dtf --manifest /path/to/dotfiles.json list
```

## Safety

`dtf get` scans file names and contents for common credentials, tokens, and private keys before
writing anything. The scanner is only a guardrail: always inspect `git diff` before committing or
pushing a configuration repository.

`--allow-sensitive` bypasses the scanner. `dtf` never invokes `sudo`; system paths require the
current user's permissions.

Symbolic links are followed, so their target contents are stored instead of the links.

## Development

```bash
git clone https://github.com/dev0xffh/dtf.git
cd dtf
uv sync --locked --dev
make check
```

The Makefile contains only developer targets: `lint`, `test`, `build`, `check`, and `install`.
Commit messages use short Conventional Commit prefixes such as `feat:`, `fix:`, `docs:`, and
`test:`.

Install the current checkout as a user command:

```bash
make install
```

By default, the executable is installed into the directory reported by `uv tool dir --bin`
(usually `~/.local/bin`). Override it when a system-wide location is appropriate and writable:

```bash
make install INSTALL_BIN=/usr/local/bin
```

## License

MIT

Read the [LICENSE](LICENSE) file for details.
