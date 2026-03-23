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

[![Coverage](https://codecov.io/gh/YOUR_GITHUB_USERNAME/devcontainer-templates/branch/main/graph/badge.svg)](https://codecov.io/gh/YOUR_GITHUB_USERNAME/devcontainer-templates)

[![PyPI version](https://img.shields.io/pypi/v/dev-code)](https://pypi.org/project/dev-code/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/dev-code)](https://pypi.org/project/dev-code/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**One command. Any project. The right devcontainer.**

`dev-code` is a CLI that opens any project in VS Code inside a devcontainer — instantly, using reusable templates you define once. No more hunting config files. No more copy-pasting `devcontainer.json`.

---

## Install

```bash
pip install dev-code
```

Or run without installing via **uvx**:

```bash
uvx dev-code
```

**Tip:** Add an alias for the fastest workflow:

```bash
alias dev-code="uvx dev-code"
```

> Requires: VS Code with the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) + Docker

---

## Quick-start

```bash
# 1. Seed your first template
dev-code init

# 2. Open any project in a devcontainer
dev-code open dev-code ~/projects/my-app

# That's it. VS Code opens, container spins up.
```

---

## Features

- **One-command open** — `dev-code open <template> <path>` launches VS Code in a devcontainer instantly
- **Reusable templates** — define your devcontainer once, reuse it across every project
- **Built-in template** — ships with the `dev-code` template out of the box
- **Custom templates** — create and manage your own with `dev-code new`
- **File sync on launch** — copy credentials, configs, and secrets into the container via a `dev-code` customization block in `devcontainer.json`
- **WSL support** — works natively on Windows Subsystem for Linux
- **Container dashboard** — see all running devcontainers with `dev-code ps`
- **Works with existing config** — no new format, just standard `devcontainer.json`

---

## Commands

| Command | Description |
|---|---|
| `dev-code init` | Seed the built-in `dev-code` template into your template directory |
| `dev-code open <template> <path>` | Open a project in VS Code using a devcontainer template |
| `dev-code new <name> [base]` | Create a new template (optionally based on an existing one) |
| `dev-code edit [template]` | Open a template for editing in VS Code |
| `dev-code list [--long]` | List available templates |
| `dev-code ps` | Show running devcontainers |

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
dev-code init                  # copies the built-in dev-code template
dev-code new my-python         # creates a new template from the default base
dev-code new my-node dev-code  # creates a new template based on dev-code
dev-code edit my-python        # open the template in VS Code to customise it
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

Supports `owner`, `group`, `permissions`, and `Override` fields per entry. Use `source/.` to copy the contents of a directory.

---

## Contributing

Contributions are welcome! To get started:

```bash
git clone https://github.com/dacrystal/dev-code
dev-code open dev-code ./dev-code
```

The repo includes a `dev-code` devcontainer — open it with itself.

Please open an issue before submitting large changes.

---

## License

MIT
