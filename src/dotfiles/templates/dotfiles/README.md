# dotfiles

This local repository stores personal configuration collected and deployed by `dtf` ([see here](https://github.com/dev0xffh/dtf)).

## Usage

Run commands from this directory:

```bash
# List all collected configs/dotfiles
# keeping in sync with the manifest
dtf list

# Add a new config/dotfile to manifest
dtf add zed ~/.config/zed/settings.json

# Gather all config files from their real location to ./configs/single/ according to the manifest
dtf get

# Gather one or more specified config(s)
dtf get git zed

# Deploy config files from ./configs/ to their location following the manifest
dtf set

# Deploy one or more specified config(s)
dtf set git zed

# Collect a host-specific version; this moves the alias out of shared single mode
dtf get --host desktop git

# Deploy the current host-specific copy selected by .dtf-private.json
dtf set git
```

## Manifest

`dotfiles.json` is the manifest, single source of truth. By default machine files are shared under
`configs/single/{home,root}`. Once an alias is collected with
`get --host NAME`, it is stored only under `configs/NAME/{home,root}`; `single` and named hosts
cannot be mixed for one alias. The local `.dtf-private.json` selects the default host.
