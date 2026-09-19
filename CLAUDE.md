# Repository Instructions

## Development

- Manage Python and dependencies with `uv`.
- Support Python 3.11 and newer.
- Run `make check` before committing.
- Keep Makefile targets limited to developer tasks; do not add a target that runs `dtf`.
- Keep this repository generic; never add personal configuration files or a root `dotfiles.json`.
- Preserve each user's `dotfiles.json` as the single source of truth in their separate repository.
- Keep manifest config types limited to INI, TOML, JSON, JSONC, JSON5, and SHELL.
- Keep `AGENTS.md` and `CLAUDE.md` byte-for-byte identical.

## Safety

- Treat this as a public repository and never add credentials, tokens, private keys, or personal
  secrets.
- Preserve atomic writes and the secret guard when changing transfer behavior.
- The `del` command may remove repository copies only; it must never remove machine files.

## Git

- Keep commit messages short and clear.
- Use Conventional Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`,
  `build:`, or `ci:`.

## Versioning

- Before every new commit, propose the next Semantic Version based on its change type: feature,
  fix, or breaking change.
- Before committing, update `cli.VERSION`; it is the single source of truth for the CLI version.
