# dev-code

```text
     _                                _
    | |                              | |
  __| | _____   ________ ___ ___   __| | ___
 / _` |/ _ \ \ / /______/ __/ _ \ / _` |/ _ \
| (_| |  __/\ V /      | (_| (_) | (_| |  __/
 \__,_|\___| \_/        \___\___/ \__,_|\___|
  project · editor · container — simplified
```

[![Coverage](https://codecov.io/gh/dacrystal/dev-code/branch/main/graph/badge.svg)](https://codecov.io/gh/dacrystal/dev-code)

[![PyPI version](https://img.shields.io/pypi/v/dev-code)](https://pypi.org/project/dev-code/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/dev-code)](https://pypi.org/project/dev-code/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**One command. Any project. The right devcontainer.**

`devcode` is a CLI that opens any project in VS Code inside a devcontainer — instantly, using reusable templates you define once. No more hunting config files. No more copy-pasting devcontainer configurations.

Use any template on any project — even repos you don't control or where you'd rather keep the devcontainer out of the repository.

```bash
# Before devcode
code ~/projects/myhellopy
# → use Dev Containers extension to configure .devcontainer/
#   (or copy-paste devcontainer configurations from another project)
# → "Reopen in Container" → wait for build
# → remember: don't commit .devcontainer to this repo

# After devcode
devcode open py-dev ~/projects/myhellopy
```

---

## Install

```bash
pip install dev-code
```

Or run without installing via **uvx**:

```bash
uvx --from dev-code devcode
```

**Tip:** Add an alias for the fastest workflow:

```bash
alias devcode="uvx --from dev-code devcode"
```

> Requires: VS Code with the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) + Docker

> **Optional:** Install the [`devcontainer` CLI](https://github.com/devcontainers/cli) for automatic resolution of all `devcontainer.json` variables (e.g. `${localEnv:VAR}`). Without it, devcode uses a Python-based parser that handles `${localEnv:VAR}` only. Install `jq` to improve parsing of `devcontainer.json` files that use comments or non-standard syntax (`jq` does not add variable resolution).

---

## Shell Completion

Add tab-completion for `devcode` subcommands, template names, and flags.

### Bash

Add to `~/.bashrc`:

```bash
eval "$(devcode completion bash)"
```

> Requires bash 4.0+. macOS ships bash 3.2 — install bash 5 via Homebrew first.

### Zsh

Add to `~/.zshrc` **after** `compinit`:

```zsh
eval "$(devcode completion zsh)"
```

Completes subcommand names, template names (for `open`, `new`, `edit`), flags, and shell names.

> **Note:** Shell completion requires `devcode` to be on your `PATH` as a real executable (e.g. `pip install dev-code`). It will not work if you are using the `alias devcode="uvx --from dev-code devcode"` shortcut, because shell completion scripts call `devcode` as a subprocess and aliases are not visible to subprocesses.

---

## Quick-start

```bash
# 1. Seed your first template
devcode init

# 2. Open any project in a devcontainer
devcode open dev-code ~/projects/my-app

# That's it. VS Code opens, container spins up.
```

---

## Features

- **One-command open** — `devcode open <template> <path>` launches VS Code in a devcontainer instantly
- **Reusable templates** — define your devcontainer once, reuse it across every project
- **Built-in template** — ships with the `dev-code` template out of the box
- **Custom templates** — create and manage your own with `devcode new`
- **File sync on launch** — copy credentials, configs, and secrets into the container via a `dev-code` customization block in `devcontainer.json`
- **WSL support** — works natively on Windows Subsystem for Linux
- **Container dashboard** — see all running devcontainers with `devcode ps`
- **Works with existing config** — no new format, just standard `devcontainer.json`

---

## Commands

| Command | Description |
|---|---|
| `devcode init` | Seed the built-in `dev-code` template into your template directory |
| `devcode open <template> <path>` | Open a project in VS Code using a devcontainer template |
| `devcode new <name> [base]` | Create a new template (optionally based on an existing one) |
| `devcode edit [template]` | Open a template for editing in VS Code |
| `devcode list [--long]` | List available templates |
| `devcode ps` | Show running devcontainers |
| `devcode completion <shell>` | Print shell completion script (`bash` or `zsh`) |

### Options

| Flag | Command | Description |
|---|---|---|
| `--dry-run` | `open` | Print the devcontainer URI and copy plan without executing |
| `--container-folder` | `open` | Override the in-container workspace path |
| `--timeout` | `open` | Seconds to wait for container to start (default: 300) |
| `--edit` | `new` | Open the new template for editing immediately after creation |
| `--long` | `list` | Show full paths alongside template names |
| `-v, --verbose` | all | Enable debug logging |

---

## Templates

Templates are directories containing a `.devcontainer/devcontainer.json` file. They live in `~/.local/share/dev-code/templates/` by default (XDG-compliant), or wherever `$DEVCODE_TEMPLATE_DIR` points.

### Get started

```bash
devcode init                  # copies the built-in dev-code template
devcode new my-python         # creates a new template from the default base
devcode new my-node dev-code  # creates a new template based on dev-code
devcode edit my-python        # open the template in VS Code to customise it
```

### File sync — copy files into the container on launch

Add a `customizations.dev-code.cp` section to your `devcontainer.json` to copy files from your host into the running container:

```json
{
  "customizations": {
    "dev-code": {
      "cp": [
        {
          "source": "${localEnv:HOME}/.claude/credentials.json",
          "target": "/home/vscode/.claude/credentials.json"
        }
      ]
    }
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `source` | string | required | Host path. Supports `${localEnv:VAR}` and relative paths. Use trailing `/.` to copy directory contents. |
| `target` | string | required | Container path. Trailing `/` places the source inside the directory. |
| `override` | bool | `false` | When `true`, overwrite an existing target. When `false` (default), skip if target already exists. |
| `owner` | string | — | User for `chown -R owner:group` after copy. Both `owner` and `group` must be set; silently skipped if either is omitted. |
| `group` | string | — | Group for `chown`. Both `owner` and `group` must be set; silently skipped if either is omitted. |
| `permissions` | string | — | Mode for `chmod -R` after copy (e.g. `"600"`). |

All field names are lowercase.

---

## Contributing

Contributions are welcome! To get started:

```bash
git clone https://github.com/dacrystal/dev-code
devcode open dev-code ./dev-code
```

The repo includes a `dev-code` devcontainer — open it with itself.

Please open an issue before submitting large changes.

---

## License

MIT
