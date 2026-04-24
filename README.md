> **This package has been renamed to [opcd](https://github.com/dacrystal/opcd).**
> Run `pip install opcd` to get the new package.

---

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

![](assets/demo.png "Demo gif")

[![GitHub](https://img.shields.io/badge/github-dacrystal%2Fdev--code-blue?logo=github)](https://github.com/dacrystal/dev-code)
[![Coverage](https://codecov.io/gh/dacrystal/dev-code/branch/main/graph/badge.svg)](https://codecov.io/gh/dacrystal/dev-code)
[![PyPI version](https://img.shields.io/pypi/v/dev-code)](https://pypi.org/project/dev-code/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/dev-code)](https://pypi.org/project/dev-code/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Reusable Dev Containers for any project — without modifying the repository.

## Table of Contents

- [Background](#background)
- [Install](#install)
- [Usage](#usage)
- [Configuration](#configuration)
- [Template System](#template-system)
- [File Injection](#file-injection)
- [API](#api)
- [Contributing](#contributing)
- [License](#license)

---

## Background

`devcode` is a CLI that opens any project in VS Code Dev Containers using reusable, local templates.

Define your environment once and reuse it across projects.

Typical Dev Container workflows involve:

* Copying `.devcontainer/` directories between projects
* Recreating environments repeatedly
* Committing configuration to repositories you do not control

`devcode` separates environment configuration from project code:

* Templates are stored locally
* Projects remain unchanged
* Containers are launched with a single command

---

## Install

### Dependencies

- VS Code with the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension
- Docker
- [devcontainer CLI](https://github.com/devcontainers/cli)

```bash
pip install dev-code
```

---

## Usage

```bash
# Open a project (auto-detects template from container history, or uses default)
devcode open ~/projects/my-app

# Open with an explicit template
devcode open ~/projects/my-app dev-code
```

### Typical Workflow

```bash
devcode template new python-dev
devcode template edit python-dev
devcode open ~/projects/my-app python-dev
```

### Project Switching

```bash
devcode list -a -i
```

Lists containers and allows reopening projects interactively.

### Advanced Options

- Multiple template directories
- Verbose debugging (`-v`)
- Dry runs (`--dry-run`)
- Custom container paths

### Internal Flow

1. Validate project path (must exist)
2. Resolve template (explicit → container history → settings default)
3. Launch VS Code Dev Container
4. Apply file injection rules

---

## Configuration

devcode reads `settings.json` from:

```
~/.config/dev-code/settings.json
```

Override the config directory:

```bash
DEVCODE_CONF_DIR=/custom/path devcode open ~/projects/my-app
```

The file is created automatically with defaults on first run.

### settings.json

```json
{
  "template_sources": ["~/.local/share/dev-code/templates"],
  "default_template": "dev-code",
  "template_write_dir": null
}
```

| Key | Description |
| --- | --- |
| `template_sources` | Ordered list of template directories searched when resolving templates. |
| `default_template` | Template used when `devcode open` is called without a template argument and no container history is found. Error if unset. |
| `template_write_dir` | Directory where `devcode template new` writes new templates. `null` (default) uses the XDG data home: `~/.local/share/dev-code/templates`. Overridden per-invocation by `--path`. |

---

## Template System

### Default Location

```
~/.local/share/dev-code/templates/
```

Configure additional paths via `template_sources` in `settings.json` (see [Configuration](#configuration)).

---

## File Injection

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

### Fields

| Field         | Required | Description                            |
| ------------- | -------- | -------------------------------------- |
| `source`      | Yes      | Host path                              |
| `target`      | Yes      | Container path                         |
| `override`    | No       | Skip if target exists (default: false) |
| `owner`       | No       | Requires `group`                       |
| `group`       | No       | Requires `owner`                       |
| `permissions` | No       | chmod applied recursively              |

### Source Behavior

* Supports `${localEnv:VAR}`
* Supports relative paths from `.devcontainer/`
* Missing environment variables cause the entry to be skipped

### Copy Directory Contents

Use `/.` suffix:

```json
{
  "source": "${localEnv:HOME}/.config/myapp/.",
  "target": "/home/vscode/.config/myapp/"
}
```

Copies directory contents instead of the directory itself.

### Behavior Rules

* `target/` copies into the directory
* Without trailing `/` copies as a file or directory
* `override=false` skips existing files
* Ownership and permissions are applied after copying

---

## API

Full reference for all `devcode` commands and flags.

### Global Flags

```bash
-v, --verbose   Enable debug output
--version       Show version and exit
```

---

### devcode open

```bash
devcode open <path> [template] [options]
```

Open a project in VS Code using a devcontainer template.

#### Arguments

* `<path>` — Project directory (must exist)
* `[template]` *(optional)* — Template name, path to a `devcontainer.json`, or path to a directory containing it. Paths must start with `./`, `../`, `/`, or `~/`. If omitted, devcode resolves in this order:
  1. Most recently running container for this project path
  2. Most recently stopped container for this project path
  3. `default_template` from `settings.json` (error if not set)

#### Options

| Option | Default | Description |
| --- | --- | --- |
| `--dry-run` | — | Print resolved configuration and actions without executing |
| `--container-folder <path>` | resolved from devcontainer config | Container mount path |
| `--timeout <seconds>` | `300` | Time to wait for container startup |

---

### devcode list

```bash
devcode list [-a] [-i]
```

List dev containers.

| Flag | Description |
| --- | --- |
| `-a, --all` | Include stopped containers |
| `-i, --interactive` | Prompt to reopen a listed container |

Interactive mode prompts `Open [1-N]:` — selecting a number reopens the project in VS Code.

---

### devcode prune

```bash
devcode prune [path] [options]
```

Remove stopped dev containers. Either `[path]` or `--all-projects` is required.

#### Arguments

* `[path]` *(optional)* — Limit pruning to containers for this project directory.

#### Options

| Option | Description |
| --- | --- |
| `--all-projects` | Prune stopped containers across all projects |
| `--include-recent` | Also prune the most recently used container (skipped by default) |

---

### devcode template

```bash
devcode template <subcommand>
```

Manage dev container templates.

---

#### devcode template new

```bash
devcode template new <name> [base] [options]
```

Create a new template by copying a base template.

| Argument | Default | Description |
| --- | --- | --- |
| `[base]` | `dev-code` | Template to copy from |

| Option | Description |
| --- | --- |
| `--edit` | Launch the new template as a Dev Container in VS Code after creation |
| `--path <dir>` | Write the new template into `<dir>` instead of the configured write target |

---

#### devcode template edit

```bash
devcode template edit <template>
```

Open a template directory in VS Code for editing.

---

#### devcode template list

```bash
devcode template list [--long]
```

List available templates.

| Option | Description |
| --- | --- |
| `--long` | Show description and full path for each template |

---

#### devcode template default

```bash
devcode template default [name]
```

Get or set the default template.

* Without `name`: prints the current default.
* With `name`: sets `default_template` in `settings.json`.

---

#### devcode template source

```bash
devcode template source <subcommand>
```

Manage template search paths stored in `settings.json`.

---

#### devcode template source list

```bash
devcode template source list
```

Print all configured template search paths, one per line.

---

#### devcode template source add

```bash
devcode template source add <path>
```

Append `<path>` to `template_sources` in `settings.json`. Prints a notice and exits cleanly if already present.

---

#### devcode template source remove

```bash
devcode template source remove <path>
```

Remove `<path>` from `template_sources` in `settings.json`. Exits with an error if not found.

---

### devcode completion

```bash
devcode completion bash
devcode completion zsh
devcode completion fish
```

Print the shell completion setup command for the given shell.

Add to your shell rc file for persistent completion:

```bash
# bash (~/.bashrc)
eval "$(devcode completion bash)"

# zsh (~/.zshrc)
eval "$(devcode completion zsh)"

# fish (~/.config/fish/config.fish)
eval (devcode completion fish)
```

---

## Contributing

Ask questions, report bugs, or request features in [Issues](https://github.com/dacrystal/dev-code/issues).

PRs welcome. Open an issue first for significant changes.

Run `tox` (or `pytest` for a single-interpreter run) before submitting.

---

## License

MIT © Nasser Alansari (dacrystal)

See [LICENSE](LICENSE).
