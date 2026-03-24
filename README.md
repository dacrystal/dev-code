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

---

## Why devcode?

If you’ve ever:

* Copied `.devcontainer/` folders between projects
* Avoided committing devcontainer configs to repos you don’t control
* Reconfigured VS Code containers again and again

**devcode fixes this.**

Define your devcontainer once. Reuse it everywhere. Keep your repos clean.

---

## What it does

`devcode` is a CLI that opens any project in VS Code inside a devcontainer—instantly—using reusable templates.

```bash
# Before devcode
code ~/projects/my-app
# → configure .devcontainer manually (or copy-paste from another project)
# → reopen in container
# → remember to not commit .devcontainer to this repo
# → repeat for every project

# After devcode
devcode open py-dev ~/projects/my-app
```

VS Code opens. The container builds. You're ready to go.

---

## Install

Install the `dev-code` package, which provides the `devcode` CLI.

### Option 1 — Install globally (recommended)

```bash
pip install dev-code
```

### Option 2 — Run without installing

```bash
uvx --from dev-code devcode
```

### Optional — Add alias

```bash
alias devcode="uvx --from dev-code devcode"
```

### Requirements

* VS Code with Dev Containers extension
* Docker

---

## Quick start

```bash
# 1. Seed your first template (one-time)
devcode init

# 2. Open any project in a container
devcode open dev-code ~/projects/my-app

# 3. Later: reopen any project instantly
devcode ps -a -i
```

Select a project from the list to reopen it in VS Code.

---

## Core concepts

### Templates

Reusable devcontainer definitions stored locally—not in your repos.

Default location:

```
~/.local/share/dev-code/templates/
```

Override with:

```
$DEVCODE_TEMPLATE_DIR
```

---

## Features

### Core workflow

* **One-command open** — launch any project instantly
* **Reusable templates** — define once, use everywhere
* **Works with any repo** — no config changes required

### Project switching (power feature)

* **Reopen any project instantly** — use `devcode ps -a -i` to list all containers (running and stopped) and interactively reopen one
* **Container dashboard** — inspect running environments with `devcode ps`

### Customization

* **Custom templates**
* **File sync on launch** — inject configs, credentials, and secrets safely

### Environment support

* **WSL support**
* **Standard devcontainer format**

---

## Commands

Pass `-v` / `--verbose` before the subcommand to enable debug output (e.g. `devcode -v open ...`).

| Command                          | Description                                                    |
| -------------------------------- | -------------------------------------------------------------- |
| `devcode open <template> <path>` | Open a project using a template                                |
| `devcode init`                   | Seed the default template                                      |
| `devcode new <name> [base]`      | Create a new template                                          |
| `devcode edit [template]`        | Edit a template                                                |
| `devcode list [--long]`          | List templates                                                 |
| `devcode ps [-a] [-i]`           | List containers (`-a` includes stopped, `-i` interactive mode) |
| `devcode completion <shell>`     | Generate shell completion                                      |

### Examples

```bash
# Show running containers
devcode ps

# Show all containers (including stopped)
devcode ps -a

# Interactive project switcher
devcode ps -a -i
```

Select a container to reopen its project in VS Code.

### Command flags

**`open`**

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--dry-run` | — | Print resolved config path, URI, and copy plan without executing anything. |
| `--container-folder <path>` | `/workspaces/<project-name>` | Override the mount path inside the container. |
| `--timeout <seconds>` | `300` | Seconds to wait for the container to start before aborting post-launch steps. |

**`new`**

| Flag / Arg | Default | Description |
| ---------- | ------- | ----------- |
| `[base]` | `dev-code` | Template to copy from when creating the new template. |
| `--edit` | — | Open the new template in VS Code immediately after creation. |

**`edit`**

Called with no argument, opens the entire templates directory instead of a specific template.

**`list`**

| Flag | Description |
| ---- | ----------- |
| `--long` | Show the templates directory path and the full filesystem path of each template. |

**`ps`**

After printing the list, `-i` prompts `Open [1-N]:` and reopens the selected project in VS Code.

---

## Templates in practice

```bash
devcode init
devcode new my-python
devcode edit my-python
devcode open my-python ~/projects/app
```

---

## File Copy (inject files into container)

Use the `cp` key under `customizations.dev-code` in your `devcontainer.json` to copy files from the host into the container on launch.

```json
{
  "customizations": {
    "dev-code": {
      "cp": [
        {
          "source": "${localEnv:HOME}/.config/myapp",
          "target": "/home/vscode/.config/myapp"
        }
      ]
    }
  }
}
```

### Fields

| Field | Type | Default | Description |
| ------------- | ------ | ------- | ----------- |
| `source` | string | required | Host path. Supports `${localEnv:VAR}` substitution, paths relative to `.devcontainer/`, and a `/.` suffix to copy directory *contents* instead of the directory itself. Entry is silently skipped if a referenced env var is unset. |
| `target` | string | required | Container path. Trailing `/` means "copy into this directory". |
| `override` | bool | `false` | When `false`, skip the entry if the effective target already exists in the container. |
| `owner` | string | — | User for `chown -R owner:group` after copy. Must be paired with `group`. |
| `group` | string | — | Group for `chown -R owner:group`. Must be paired with `owner`. |
| `permissions` | string | — | Mode string passed to `chmod -R` (e.g. `"600"`). |

### Directory contents (`/.` suffix)

Append `/.` to `source` to copy each child of a directory into `target` rather than the directory itself. `target` must end with `/`.

```json
{
  "source": "${localEnv:HOME}/.config/myapp/.",
  "target": "/home/vscode/.config/myapp/",
  "override": false,
  "owner": "vscode",
  "group": "vscode",
  "permissions": "600"
}
```

This copies every file in `~/.config/myapp/` into `/home/vscode/.config/myapp/`, skipping any that already exist, then sets ownership and mode on each copied file.

Perfect for:

* Credentials
* Config files
* Local development secrets

---

## Shell completion

### Bash

```bash
eval "$(devcode completion bash)"
```

### Zsh

```zsh
eval "$(devcode completion zsh)"
```

> Requires `devcode` to be installed (not via alias).

---

## How it works

* Resolve a template
* Launch VS Code with a devcontainer
* Apply optional file sync rules

---

## Contributing

```bash
git clone https://github.com/dacrystal/dev-code
devcode open dev-code ./dev-code
```

---

## License

MIT
