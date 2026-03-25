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

Reusable Dev Containers for any project — without modifying the repository.

---


`devcode` is a CLI that opens any project in VS Code Dev Containers using reusable, local templates.

Define your environment once and reuse it across projects.

---

## Why devcode?

Typical Dev Container workflows involve:

* Copying `.devcontainer/` directories between projects
* Recreating environments repeatedly
* Committing configuration to repositories you do not control

`devcode` separates environment configuration from project code:

* Templates are stored locally
* Projects remain unchanged
* Containers are launched with a single command

---

## Quick Start

```bash
# Install
pip install dev-code

# Create default template
devcode init

# Open a project
devcode open dev-code ~/projects/my-app

# Reopen projects later
devcode ps -a -i
```

---

## Requirements

* VS Code with the Dev Containers extension
* Docker

---

## Core Concepts

### Templates

Templates are reusable devcontainer configurations stored locally.

Default location:

```
~/.local/share/dev-code/templates/
```

Override search paths:

```bash
DEVCODE_TEMPLATE_PATH=/my/templates:/team/templates
```

* The first path is used for writes
* Additional paths are read-only

---

## Command Reference

### Global Flags

```bash
-v, --verbose   Enable debug output
```

---

### devcode open

```bash
devcode open <template> <path> [options]
```

Open a project using a template.

#### Arguments

* `<template>`

  * Template name, or
  * Path to a `devcontainer.json`, or
  * Path to a directory containing it

  Paths must start with `./`, `../`, `/`, or `~/`.

  If both a template and directory match, the template takes precedence and a warning is shown.

* `<path>`
  Project directory

#### Options

| Option                      | Default                 | Description                                                |
| --------------------------- | ----------------------- | ---------------------------------------------------------- |
| `--dry-run`                 | —                       | Print resolved configuration and actions without executing |
| `--container-folder <path>` | `/workspaces/<project>` | Container mount path                                       |
| `--timeout <seconds>`       | `300`                   | Time to wait for container startup                         |

---

### devcode init

```bash
devcode init
```

Creates the default template.

---

### devcode new

```bash
devcode new <name> [base]
```

Create a new template.

| Argument | Default    | Description           |
| -------- | ---------- | --------------------- |
| `[base]` | `dev-code` | Template to copy from |

Options:

```bash
--edit
```

Open the template in VS Code after creation.

---

### devcode edit

```bash
devcode edit [template]
```

* With a name: opens that template
* Without arguments: opens the templates directory

---

### devcode list

```bash
devcode list [--long]
```

| Option   | Description                             |
| -------- | --------------------------------------- |
| `--long` | Show full paths and grouped directories |

---

### devcode ps

```bash
devcode ps [-a] [-i]
```

| Flag | Description                |
| ---- | -------------------------- |
| `-a` | Include stopped containers |
| `-i` | Interactive reopen mode    |

Interactive mode prompts:

```
Open [1-N]:
```

Selecting a number reopens the project in VS Code.

---

### devcode completion

```bash
devcode completion bash
devcode completion zsh
```

Enable in shell:

```bash
eval "$(devcode completion bash)"
```

---

## Typical Workflow

```bash
devcode init
devcode new python-dev
devcode edit python-dev
devcode open python-dev ~/projects/my-app
```

---

## Template System

### Default Location

```
~/.local/share/dev-code/templates/
```

### Custom Paths

```bash
DEVCODE_TEMPLATE_PATH=$HOME/my/templates:/team/shared/templates
```

Resolution order:

1. First directory is the write target
2. Remaining directories are used for lookup

---

## File Injection (cp)

Inject files from the host into the container at startup.

### Example

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

---

### Fields

| Field         | Required | Description                            |
| ------------- | -------- | -------------------------------------- |
| `source`      | Yes      | Host path                              |
| `target`      | Yes      | Container path                         |
| `override`    | No       | Skip if target exists (default: false) |
| `owner`       | No       | Requires `group`                       |
| `group`       | No       | Requires `owner`                       |
| `permissions` | No       | chmod applied recursively              |

---

### Source Behavior

* Supports `${localEnv:VAR}`
* Supports relative paths from `.devcontainer/`
* Missing environment variables cause the entry to be skipped

---

### Copy Directory Contents

Use `/.` suffix:

```json
{
  "source": "${localEnv:HOME}/.config/myapp/.",
  "target": "/home/vscode/.config/myapp/"
}
```

Copies directory contents instead of the directory itself.

---

### Behavior Rules

* `target/` copies into the directory
* Without trailing `/` copies as a file or directory
* `override=false` skips existing files
* Ownership and permissions are applied after copying

---

## Project Switching

```bash
devcode ps -a -i
```

Lists containers and allows reopening projects interactively.

---

## Advanced Options

* Multiple template directories
* Template inheritance
* Verbose debugging (`-v`)
* Dry runs (`--dry-run`)
* Custom container paths

---

## Internal Flow

1. Resolve template
2. Resolve project path
3. Launch VS Code Dev Container
4. Apply file injection rules

---

## License

MIT
