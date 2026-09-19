# dtf

`dtf` is a small Linux dotfiles CLI collector and deployer. The CLI lives in this repository; your
configuration files live in a separate repository that contains only `dotfiles.json` and the
generated `configs/` tree.

The layout mirrors conventional Linux (RHEL) paths without embedding a user name:

| Machine | Configuration repository |
| --- | --- |
| `~/.config/zed/settings.json` | `{YOUR_REPO}/configs/single/home/.config/zed/settings.json` |
| `/etc/example/config.toml` | `{YOUR_REPO}/configs/single/root/etc/example/config.toml` |

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
# Create a new repository for your dotfiles (ex: dotfiles)
mkdir dotfiles
cd dotfiles
git init

# Initialize the dotfiles repository; dtf asks for the path when omitted
dtf init --project {YOUR_OWNER}/{YOUR_REPO}
# Or provide it directly:
dtf init ~/dotfiles --project {YOUR_OWNER}/{YOUR_REPO}
# Start adding your local configuration files
dtf add zed ~/.config/zed/settings.json
dtf get

# Commit and push changes
git add .
git commit -m "Initial commit"
git push
```

If a GitHub `origin` already exists, `dtf init` detects `owner/repository` automatically and
`--project` can be omitted.

The default path prompt falls back to `$HOME/dotfiles`. The skeleton contains an empty manifest,
`configs/`, `.gitignore`, `README.md`, matching `AGENTS.md` and `CLAUDE.md`, plus empty and example
private-data files.

`dotfiles.json` is the only source of truth in the configuration repository:

```json
{
  "project": "dtf",
  "version": 1,
  "apps": {
    "editor": {
      "app": "zed",
      "type": "JSON5",
      "paths": [
        "~/.config/zed/settings.json",
        "~/.config/zed/keymap.json"
      ],
      "hosts": ["single"]
    }
  }
}
```

The object key is the application name and is used by commands. The `app` field mirrors that name.

Supported configuration types:

- `INI` — INI-style files, including `.ini`, `.cfg`, and `.conf`.
- `TOML` — TOML files with recursive table and key merging.
- `JSON` — strict JSON; destination-only keys can be merged, but value conflicts are rejected.
- `JSONC` — JSON with comments; conflicting destination values are retained as `//` comments.
- `JSON5` — JSON5, including comments and trailing commas; used automatically for Zed.
- `SHELL` — Bash and Zsh files such as `.bashrc`, `.zshrc`, `.profile`, `*.sh`, and `*.zsh`.

## Usage

```bash
dtf help
dtf --version
```
Register applications:

```bash
dtf add zed ~/.config/zed/settings.json
dtf add git ~/.gitconfig ~/.config/git --type INI
```

`add` infers the type from a known application, file extension, or existing directory contents.
Use `--type`/`-t` to override it. One alias has one format; register mixed-format trees as
separate aliases.

Inspect, collect, and deploy all entries or selected aliases:

```bash
dtf list
dtf get
dtf get zed git
dtf set zed
```

## Hosts

An alias is either shared by every machine or has separate copies per named host. New aliases use
the shared `single` host, stored below `configs/single/{home,root}`. `single` cannot be combined
with named hosts for the same alias.

To split a shared alias, collect it explicitly for the first host. The successful collection moves
that alias from `single` to the named host in the manifest and removes its old shared repository
copy. Collect additional hosts explicitly:

```bash
dtf get --host desktop git
dtf get --host laptop git
```

The manifest then records `"hosts": ["desktop", "laptop"]` for `git`, with repository copies
under `configs/desktop/` and `configs/laptop/`. `set` always selects the host declared in the
local `.dtf-private.json`; a host must already be registered before it can be deployed. Only
`get` accepts `--host NAME`, for collecting and registering a host. `-h` remains the help option.

Without `--merge`, the destination becomes an exact copy of the source. A directory replacement
removes destination-only entries. Writes are staged before replacement.

Use `--merge`/`-m` for a structured merge:

```bash
dtf get --merge zed
dtf set -m git
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

Shell files such as `.bashrc`, `.zshrc`, `.profile`, `*.sh`, and `*.zsh` are detected as
`SHELL`. Shell merge handles variables, exported variables, aliases, and independent one-line
commands. It rejects destination-only functions, control-flow blocks, heredocs, and continued
lines instead of risking a syntactically broken startup file.

Remove aliases from the manifest and repository copy:

```bash
dtf del git
dtf del --yes zed git
```

Without `--yes`/`-y`, `dtf` asks for confirmation. `del` never touches machine files.

Use another manifest by placing the global option before the command:

```bash
dtf --manifest /path/to/dotfiles.json list
```

## Private local substitutions

Some configuration values are not secrets but should not be committed to a public repository:
author names, email addresses, account names, local paths, and machine-specific settings. The
`dtf init` skeleton creates `.dtf-private.json` and an `.dtf-private.example.json` beside
`dotfiles.json`; the real file is ignored by Git.

The file identifies the current machine and contains one mapping object per manifest alias:

```json
{
  "host": "single",
  "mappings": [
    {
      "alias": "git",
      "map": {
        "author": "Jack",
        "email": "my@email.com"
      }
    }
  ]
}
```

When `get git` collects a machine file, matching values are written to the repository as
placeholders:

```text
Jack <my@email.com>  ->  {{author}} <{{email}}>
```

When `set git` deploys the repository file, the placeholders are replaced with the local values.
Mappings are scoped to an alias, so the same placeholder names can safely have different values
for different applications or machines. Missing mappings leave files unchanged. Values are
plain text and should still be reviewed before sharing the configuration repository.

The `host` field is written explicitly by `dtf init`; older private-data arrays remain compatible
and are treated as `"single"`.

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
