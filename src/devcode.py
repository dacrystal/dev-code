#!/usr/bin/env python3
import click
import click.shell_completion
import importlib.metadata
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("devcode")

BANNER = (
    "     _                                _\n"
    "    | |                              | |\n"
    "  __| | _____   ________ ___ ___   __| | ___\n"
    " / _` |/ _ \\ \\ / /______/ __/ _ \\ / _` |/ _ \\\n"
    "| (_| |  __/\\ V /      | (_| (_) | (_| |  __/\n"
    " \\__,_|\\___| \\_/        \\___\\___/ \\__,_|\\___|\n"
    "  project · editor · container — simplified\n"
)

KNOWN_CP_FIELDS = {"source", "target", "override", "owner", "group", "permissions"}

_DEFAULT_SETTINGS = {
    "template_sources": ["~/.local/share/dev-code/templates"],
    "default_template": "dev-code",
    "template_write_dir": None,
}

def _configure_logging(verbose: bool) -> None:
    """Configure the module logger. Guard prevents double-registration."""
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)


def is_wsl() -> bool:
    """Detect WSL (but avoid Docker containers) by reading /proc/version."""
    if "WSLENV" not in os.environ:
        return False
    try:
        with open("/proc/version", "r") as f:
            content = f.read()
        return "Microsoft" in content or "WSL" in content
    except Exception:
        return False


def wsl_to_windows(path: str) -> str:
    """Convert WSL path to Windows path using wslpath."""
    try:
        return subprocess.check_output(
            ["wslpath", "-w", path],
            text=True
        ).strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to convert path with wslpath: {path}") from e


def _conf_dir() -> str:
    """Return the devcode config directory path."""
    override = os.environ.get("DEVCODE_CONF_DIR")
    if override:
        return override
    xdg_config = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
    )
    return os.path.join(xdg_config, "dev-code")


def _load_settings() -> dict:
    """Read settings.json, creating it with defaults if absent. Never raises."""
    conf_dir = _conf_dir()
    settings_path = os.path.join(conf_dir, "settings.json")
    if not os.path.exists(settings_path):
        try:
            os.makedirs(conf_dir, exist_ok=True)
            with open(settings_path, "w") as f:
                json.dump(_DEFAULT_SETTINGS, f, indent=2)
                f.write("\n")
            logger.info("created default settings at %s", settings_path)
        except OSError as e:
            logger.warning("could not create settings file %s: %s", settings_path, e)
        return dict(_DEFAULT_SETTINGS)
    try:
        with open(settings_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("failed to load settings from %s: %s; using defaults", settings_path, e)
        return dict(_DEFAULT_SETTINGS)


def _save_settings(settings: dict) -> None:
    """Write settings dict to settings.json. Exits on write failure."""
    conf_dir = _conf_dir()
    settings_path = os.path.join(conf_dir, "settings.json")
    try:
        os.makedirs(conf_dir, exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
    except OSError as e:
        logger.error("could not write settings file %s: %s", settings_path, e)
        sys.exit(1)


def resolve_template_search_path() -> list[str]:
    """Return ordered list of template search directories from settings.json."""
    settings = _load_settings()
    sources = settings.get("template_sources")
    if not sources:
        xdg = os.environ.get(
            "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
        )
        return [os.path.realpath(os.path.join(xdg, "dev-code", "templates"))]
    return [os.path.realpath(os.path.expanduser(d)) for d in sources if d]


def _resolve_write_target(path_override=None) -> str:
    """Resolve the directory where new templates are written.

    Priority (highest to lowest):
    1. path_override  — the --path flag value
    2. settings.template_write_dir  — persistent setting
    3. XDG_DATA_HOME/dev-code/templates  — unconditional fallback
    """
    if path_override is not None:
        resolved = os.path.realpath(os.path.expanduser(path_override))
        if os.path.exists(resolved) and not os.path.isdir(resolved):
            logger.error("--path '%s' is not a directory", path_override)
            sys.exit(1)
        return resolved

    settings = _load_settings()
    write_dir = settings.get("template_write_dir")
    if write_dir:
        resolved = os.path.realpath(os.path.expanduser(write_dir))
        if os.path.exists(resolved) and not os.path.isdir(resolved):
            logger.warning(
                "template_write_dir '%s' is not a directory, falling back to XDG default",
                write_dir,
            )
        else:
            return resolved

    xdg = os.environ.get(
        "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
    )
    return os.path.join(xdg, "dev-code", "templates")


def _list_template_names() -> list:
    """Return sorted deduplicated list of valid user template names across all search dirs."""
    seen = []
    seen_set = set()
    for search_dir in resolve_template_search_path():
        if not os.path.isdir(search_dir):
            logger.debug("template search dir not found, skipping: %s", search_dir)
            continue
        try:
            for name in sorted(os.listdir(search_dir)):
                if name in seen_set:
                    continue
                candidate = os.path.join(search_dir, name)
                if _is_valid_template(candidate):
                    seen.append(name)
                    seen_set.add(name)
                else:
                    logger.debug("skipping invalid template: %s", candidate)
        except Exception:
            pass
    return sorted(seen)


def get_builtin_template_path(name: str) -> str | None:
    """Return absolute path to a bundled template directory, or None if not found."""
    module_dir = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(module_dir, "dev_code_templates", name)
    if os.path.isdir(candidate):
        return candidate
    try:
        import importlib.resources as pkg_resources
        ref = pkg_resources.files("dev_code_templates").joinpath(name)
        if ref.is_dir():
            return str(ref)
    except Exception as e:
        logger.debug("importlib.resources fallback failed for %r: %s", name, e)
    return None


def _is_valid_template(template_root: str) -> bool:
    """Return True if template_root contains .devcontainer/devcontainer.json."""
    return os.path.isfile(
        os.path.join(template_root, ".devcontainer", "devcontainer.json")
    )


def _find_template_in_search_path(name: str) -> str | None:
    """Search all template dirs for name; return template root path or None."""
    for search_dir in resolve_template_search_path():
        candidate = os.path.join(search_dir, name)
        if _is_valid_template(candidate):
            return candidate
    return None


def resolve_template(name: str) -> str:
    """Return absolute path to template's devcontainer.json. Exits on failure."""
    # 1. Explicit path prefix — skip template lookup entirely
    if _has_path_prefix(name):
        path_result = _resolve_as_path(name)
        if path_result:
            return path_result
        expanded = os.path.abspath(os.path.expanduser(name))
        if os.path.isfile(expanded):
            logger.error("config file must be named devcontainer.json")
            sys.exit(1)
        if os.path.isdir(expanded):
            logger.error("path '%s' does not contain a devcontainer.json", name)
            sys.exit(1)
        logger.error("path not found: %s", name)
        sys.exit(1)
    # 2. Try template lookup across all search dirs
    template_root = _find_template_in_search_path(name)
    if template_root:
        config = os.path.join(template_root, ".devcontainer", "devcontainer.json")
        if _resolve_as_path(name):
            logger.warning(
                "'%s' matches both a template and a local path — using template. "
                "Use './%s' to open as path instead.",
                name, name,
            )
        return config
    # 3. No template — try path fallback
    path_result = _resolve_as_path(name)
    if path_result:
        return path_result
    logger.error("template not found: %s", name)
    sys.exit(1)


def _resolve_as_path(p: str) -> str | None:
    """Return absolute path to devcontainer.json if p resolves as a path, else None."""
    expanded = os.path.abspath(os.path.expanduser(p))
    if os.path.isfile(expanded):
        return expanded if os.path.basename(expanded) == "devcontainer.json" else None
    if os.path.isdir(expanded):
        candidate = os.path.join(expanded, ".devcontainer", "devcontainer.json")
        if os.path.exists(candidate):
            return candidate
        candidate = os.path.join(expanded, "devcontainer.json")
        if os.path.exists(candidate):
            return candidate
    return None


def _has_path_prefix(p: str) -> bool:
    """Return True if p has an explicit path prefix (not a plain template name).

    Detection: p starts with its own topmost parent component.
    - "./foo" → last parent "." → starts with "." → True
    - "../foo" → last parent ".." → starts with ".." → True
    - "/foo" → last parent "/" → starts with "/" → True
    - "~/foo" → expanded to absolute → starts with "/" → True
    - "mydev" → last parent "." → does NOT start with "." → False
    """
    if p.startswith("~"):
        p = str(Path(p).expanduser())
    if os.path.isabs(p):
        return True
    parents = Path(p).parents
    return not len(parents) or p.startswith(str(parents[-1]))


def build_devcontainer_uri(host_path: str, config_file: str, container_folder: str) -> str:
    # Handle WSL conversion
    if is_wsl():
        host_path = wsl_to_windows(host_path)
        config_file = wsl_to_windows(config_file)

    # Build JSON
    data = {
        "hostPath": host_path,
        "configFile": {
            "$mid": 1,
            "path": config_file,
            "scheme": "file"
        }
    }

    # Compact JSON (important!)
    json_str = json.dumps(data, separators=(",", ":"))

    # Hex encode
    hex_str = json_str.encode("utf-8").hex()

    # Build URI
    return f"vscode-remote://dev-container+{hex_str}{container_folder}"



def parse_devcontainer_json(config_file: str, cwd: str | None = None) -> dict:
    """Run devcontainer read-configuration and return the full raw output dict."""
    result = subprocess.run(
        ["devcontainer", "read-configuration", "--config", config_file],
        capture_output=True, text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        logger.error("devcontainer read-configuration failed: %s", result.stderr.strip())
        sys.exit(1)
    return json.loads(result.stdout)


def wait_for_container(config_file: str, project_path: str, timeout: int) -> str:
    """Poll Docker until a devcontainer for project_path starts. Returns container ID."""
    label_value = wsl_to_windows(project_path) if is_wsl() else project_path
    deadline = time.time() + timeout

    while time.time() < deadline:
        result = subprocess.run(
            [
                "docker", "container", "ls",
                "--filter", f"label=devcontainer.local_folder={label_value}",
                "--filter", f"label=devcontainer.config_file={config_file}",
                "--format", "{{.ID}}",
            ],
            capture_output=True, text=True,
        )
        ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if ids:
            if len(ids) > 1:
                logger.warning("multiple containers matched label; using first (%s)", ids[0])
            return ids[0]
        time.sleep(2)

    logger.error(
        "timed out waiting for container (label=devcontainer.local_folder=%s); "
        "path format mismatch may be the cause (e.g. in WSL, Windows path vs Linux path)",
        label_value,
    )
    sys.exit(1)


def _substitute_env_vars(s: str):
    """Resolve ${localEnv:VAR} patterns. Returns resolved string, or None if any var unset/empty."""
    for match in re.finditer(r"\$\{localEnv:([^}]+)\}", s):
        var = match.group(1)
        if not os.environ.get(var):
            return None
    return re.sub(r"\$\{localEnv:([^}]+)\}", lambda m: os.environ[m.group(1)], s)


def _expand_source_path(source: str, config_dir: str) -> str:
    """Strip /. suffix, resolve relative paths against config_dir, restore /. suffix."""
    dot_expand = source.endswith("/.")
    if dot_expand:
        source = source[:-2] or "/"
    if not os.path.isabs(source):
        source = os.path.abspath(os.path.join(config_dir, source))
    if dot_expand:
        source += "/."
    return source


def _fmt_path(p: str) -> str:
    """Abbreviate p with ~ when it starts with the home directory."""
    home = os.path.expanduser("~")
    if p == home or p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


def _fmt_row(r: tuple, widths: list) -> str:
    """Left-justify each value in r to its column width, join with two spaces."""
    return "  ".join(f"{v:<{widths[i]}}" for i, v in enumerate(r))


def _docker_run(cmd: list, label: str) -> bool:
    """Run a full docker command list. Logs warning and returns False on failure."""
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        logger.warning("%s failed (exit %d)", label, result.returncode)
        if result.stderr:
            logger.debug("%s stderr: %s", label, result.stderr.decode(errors="replace").strip())
        return False
    return True


def _list_dir_children(source_dir: str) -> list:
    """Return absolute paths of all children in source_dir (includes dot files)."""
    return [os.path.join(source_dir, name) for name in os.listdir(source_dir)]


def _process_entry(container_id: str, entry: dict, idx: int, config_dir: str) -> None:
    """Process a single customizations.dev-code.cp copy entry."""
    for key in entry:
        if key not in KNOWN_CP_FIELDS:
            logger.warning("entry %d has unknown field '%s', ignoring", idx, key)

    source = entry.get("source")
    target = entry.get("target")

    if not source or not target:
        missing = "source" if not source else "target"
        logger.warning("entry %d missing '%s', skipping", idx, missing)
        return

    # Step 1: Env var substitution + relative path resolution
    resolved = _substitute_env_vars(source)
    if resolved is None:
        logger.warning("entry %d source env var unset or empty, skipping", idx)
        return
    source = resolved

    source = _expand_source_path(source, config_dir)

    # Step 2: Source expansion for dir-contents (source/.)
    if source.endswith("/."):
        if not target.endswith("/"):
            logger.warning(
                "entry %d source ends with '/.' but target '%s' has no trailing '/'; appending '/' to target",
                idx, target,
            )
            target = target + "/"
        actual_dir = source[:-2]
        if not os.path.isdir(actual_dir):
            logger.warning("entry %d source dir not found or not a directory: %s, skipping", idx, actual_dir)
            return
        # Empty dir is a silent no-op
        for child_path in _list_dir_children(actual_dir):
            child_entry = {k: entry[k] for k in KNOWN_CP_FIELDS if k in entry}
            child_entry["source"] = child_path
            child_entry["target"] = target
            _process_entry(container_id, child_entry, idx=idx, config_dir=config_dir)
        return

    # Step 3: Check source exists
    if not os.path.exists(source):
        logger.warning("entry %d source not found: %s, skipping", idx, source)
        return

    # Step 4: Classify source type (file vs dir-itself; docker cp handles both natively)
    # source_is_dir = os.path.isdir(source)  # not branched on — docker cp handles both

    # Step 5: Compute effective target (for override check and chown/chmod only)
    if target.endswith("/"):
        effective = target.rstrip("/") + "/" + os.path.basename(source)
    else:
        effective = target

    # Step 6: Override check (before any side effects)
    override = entry.get("override", False)
    if not override:
        result = subprocess.run(
            ["docker", "exec", container_id, "test", "-e", effective],
            capture_output=True,
        )
        if result.returncode == 0:
            logger.info("entry %d effective target '%s' exists and override=false, skipping", idx, effective)
            return

    # Step 7: Pre-create dirs
    if target.endswith("/"):
        _docker_run(["docker", "exec", container_id, "mkdir", "-p", target], f"entry {idx} mkdir {target}")
    else:
        parent = os.path.dirname(target)
        if parent:
            _docker_run(["docker", "exec", container_id, "mkdir", "-p", parent], f"entry {idx} mkdir {parent}")

    # Step 8: Copy
    ok = _docker_run(["docker", "cp", source, f"{container_id}:{target}"], f"entry {idx} cp")
    if not ok:
        return

    # Step 9: chown / chmod (only if copy succeeded; applied to effective)
    owner = entry.get("owner")
    group = entry.get("group")
    if owner and group:
        _docker_run(
            ["docker", "exec", "-u", "root", container_id, "chown", "-R", f"{owner}:{group}", effective],
            f"entry {idx} chown",
        )

    permissions = entry.get("permissions")
    if permissions:
        _docker_run(
            ["docker", "exec", "-u", "root", container_id, "chmod", "-R", permissions, effective],
            f"entry {idx} chmod",
        )


def run_post_launch(config_file: str, project_path: str, timeout: int) -> None:
    """Parse devcontainer.json and run customizations.dev-code.cp copy entries."""
    data = parse_devcontainer_json(config_file, cwd=project_path)
    config = data.get("configuration", {})

    dev_code_section = config.get("customizations", {}).get("dev-code")
    if dev_code_section is None:
        return
    if not isinstance(dev_code_section, dict):
        logger.error("customizations.dev-code must be a dict in %s", config_file)
        sys.exit(1)

    entries = dev_code_section.get("cp")
    if not entries:
        return
    if not isinstance(entries, list):
        logger.error("customizations.dev-code.cp must be a list in %s", config_file)
        sys.exit(1)

    container_id = wait_for_container(config_file, project_path, timeout)

    config_dir = os.path.dirname(os.path.abspath(config_file))
    for idx, entry in enumerate(entries):
        _process_entry(container_id, entry, idx, config_dir)


def _git_repo_root(path: str) -> str | None:
    """Return the git repository root for path, or None if not a git repo or git unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except OSError:
        return None


def _find_container_config_for_project(project_path: str) -> str | None:
    """Return config_file of most recently created container for project_path, or None.

    Checks running containers first; falls back to all containers (stopped too).
    """
    label_value = wsl_to_windows(project_path) if is_wsl() else project_path
    fmt = '{{.CreatedAt}}\t{{.Label "devcontainer.config_file"}}'

    for extra_args in [[], ["-a"]]:
        try:
            result = subprocess.run(
                ["docker", "container", "ls"] + extra_args + [
                    "--filter", f"label=devcontainer.local_folder={label_value}",
                    "--format", fmt,
                ],
                capture_output=True, text=True,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        rows = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[1].strip():
                rows.append((parts[0], parts[1].strip()))
        if rows:
            rows.sort(key=lambda r: r[0], reverse=True)
            return rows[0][1]

    return None


def _do_open(projectpath: str, template, container_folder, timeout: int, dry_run: bool) -> None:
    """Core open logic shared by the open command and internal callers."""
    project_path = os.path.realpath(projectpath)

    if not os.path.exists(project_path):
        logger.error("projectpath not found: %s", projectpath)
        sys.exit(1)

    if project_path == "/":
        logger.error("projectpath must not resolve to /")
        sys.exit(1)

    git_root = _git_repo_root(project_path)
    if git_root is not None:
        if os.path.normcase(os.path.realpath(git_root)) != os.path.normcase(os.path.realpath(project_path)):
            logger.error(
                "projectpath '%s' is a subdirectory of a git repository rooted at '%s'.\n"
                "VS Code devcontainer would mount '%s' instead of '%s', causing \"Workspace does not exist\".\n"
                "Use the git root as projectpath, or restructure so the project root is its own repository.",
                project_path, git_root, git_root, project_path,
            )
            sys.exit(1)

    if template:
        config_file = resolve_template(template)
    else:
        config_file = _find_container_config_for_project(project_path)
        if config_file is None:
            settings = _load_settings()
            default = settings.get("default_template", "")
            if not default:
                logger.error(
                    "no template specified and no default_template configured in settings"
                )
                sys.exit(1)
            config_file = resolve_template(default)

    if not container_folder:
        data = parse_devcontainer_json(config_file, cwd=project_path)
        container_folder = data.get("workspace", {}).get("workspaceFolder", "")
    uri = build_devcontainer_uri(project_path, config_file, container_folder)

    if dry_run:
        _cmd_open_dry_run(config_file, project_path, uri)
        return

    if not shutil.which("code"):
        logger.error("'code' not found on PATH")
        sys.exit(1)

    subprocess.Popen(["code", "--folder-uri", uri], start_new_session=True)
    run_post_launch(config_file, project_path, timeout)


def _cmd_open_dry_run(config_file: str, project_path: str, uri: str) -> None:
    """Print dry-run plan to stdout without executing anything."""
    print(f"Config:  {config_file}")
    print(f"URI:     {uri}")

    data = parse_devcontainer_json(config_file, cwd=project_path)
    config = data.get("configuration", {})
    dev_code_section = config.get("customizations", {}).get("dev-code")
    entries = []
    if isinstance(dev_code_section, dict):
        raw = dev_code_section.get("cp")
        if isinstance(raw, list):
            entries = raw

    if not entries:
        print("(dry run — no copy entries)")
        return

    print("Copy plan:")
    config_dir = os.path.dirname(os.path.abspath(config_file))
    for idx, entry in enumerate(entries):
        source = entry.get("source", "")
        target = entry.get("target", "(no target)")

        # Env var substitution
        resolved = _substitute_env_vars(source)
        if resolved is None:
            unset = [m.group(1) for m in re.finditer(r"\$\{localEnv:([^}]+)\}", source)
                     if not os.environ.get(m.group(1))]
            logger.warning("entry %d: env var unset: %s", idx, ", ".join(unset))
            print(f"  [{idx}] <unset: {unset[0] if unset else '?'}> → {target}")
            continue
        source = resolved

        source = _expand_source_path(source, config_dir)
        annotation = " [missing]" if not os.path.exists(source.rstrip("/.")) else ""
        print(f"  [{idx}] {source}{annotation} → {target}")

    print("(dry run — no operations executed)")


def _template_name_from_config(config_path: str) -> str:
    """Extract template name as the directory immediately above .devcontainer/."""
    norm = os.path.normpath(config_path)
    parts = norm.replace("\\", "/").split("/")
    for i, part in enumerate(parts):
        if part == ".devcontainer" and i > 0:
            return parts[i - 1]
    return os.path.basename(os.path.dirname(os.path.dirname(norm)))


def _complete_templates(ctx, param, incomplete):
    """Shell completion callback: return template names matching the incomplete prefix."""
    return [
        click.shell_completion.CompletionItem(t)
        for t in _list_template_names()
        if t.startswith(incomplete)
    ]


class _DevCodeGroup(click.Group):
    """Custom CLI group: loads settings eagerly and prepends banner to help output."""
    def main(self, *args, **kwargs):
        # Trigger settings file creation/validation before any CLI output (including --help).
        _load_settings()
        return super().main(*args, **kwargs)

    def format_help(self, ctx, formatter):
        formatter.write(BANNER)
        formatter.write_paragraph()
        super().format_help(ctx, formatter)


@click.group(invoke_without_command=True, cls=_DevCodeGroup)
@click.version_option(package_name="dev-code", prog_name="devcode")
@click.option(
    "-v", "--verbose",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=lambda ctx, param, v: _configure_logging(v),
)
@click.pass_context
def cli(ctx):
    """Open projects in VS Code Dev Containers using reusable local templates."""
    if not shutil.which("devcontainer"):
        click.echo("error: devcontainer CLI not found on PATH", err=True)
        raise click.exceptions.Exit(1)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.group("template")
def template_group():
    """Manage dev container templates."""
    pass


@template_group.group("source")
def source_group():
    """Manage template search paths."""
    pass


@cli.command("open")
@click.argument("projectpath", type=click.Path(file_okay=False, resolve_path=True))
@click.argument("template", required=False, shell_complete=_complete_templates)
@click.option("--container-folder", default=None, help="Path inside the container. Default: resolved from devcontainer config.")
@click.option("--timeout", type=int, default=300, show_default=True,
              help="Seconds to wait for devcontainer to start.")
@click.option("--dry-run", is_flag=True, help="Print plan without executing.")
def open_command(projectpath, template, container_folder, timeout, dry_run):
    """Open a project in VS Code using a devcontainer template."""
    _do_open(projectpath, template, container_folder, timeout, dry_run)


@template_group.command("new")
@click.argument("name")
@click.argument("base", required=False, shell_complete=_complete_templates)
@click.option("--edit", is_flag=True, help="Open the new template in VS Code after creating it.")
@click.option("--path", "write_path", default=None, help="Directory to write the new template into.")
def new_command(name, base, edit, write_path):
    """Create a new template by copying a base template."""
    write_dir = _resolve_write_target(write_path)
    dest = os.path.join(write_dir, name)

    # Step 1: fail if name already exists
    if os.path.exists(dest):
        logger.error("template '%s' already exists: %s", name, dest)
        sys.exit(1)

    # Step 2: resolve base — search all dirs, then builtins
    base_name = base or "dev-code"
    base_root = _find_template_in_search_path(base_name)
    if base_root:
        base_src = base_root
    else:
        builtin = get_builtin_template_path(base_name)
        if builtin:
            base_src = builtin
        else:
            logger.error("base template not found: %s", base_name)
            sys.exit(1)

    # Step 3-4: create write dir
    try:
        os.makedirs(write_dir, exist_ok=True)
    except OSError as e:
        logger.error("cannot create template dir %s: %s", write_dir, e)
        sys.exit(1)

    # Step 5: copy
    shutil.copytree(base_src, dest)
    print(f"Created template '{name}' at {dest}")

    # Warn if write_dir is not discoverable
    # resolve_template_search_path already returns realpath'd entries
    search_paths = resolve_template_search_path()
    if os.path.realpath(write_dir) not in search_paths:
        logger.warning(
            "write dir '%s' is not in template_sources — new template may not be discoverable",
            write_dir,
        )

    # Step 6: --edit
    if edit:
        _do_open(
            projectpath=dest,
            template=name,
            container_folder=None,
            timeout=300,
            dry_run=False,
        )


@template_group.command("edit")
@click.argument("template", shell_complete=_complete_templates)
def edit_command(template):
    """Open a template directory in VS Code for editing."""
    template_root = _find_template_in_search_path(template)
    if template_root is None:
        logger.error("template not found: %s", template)
        sys.exit(1)
    subprocess.run(["code", template_root])


@template_group.command("list")
@click.option("--long", is_flag=True, help="Show description and path for each template.")
def template_list_command(long):
    """List available templates."""
    if not long:
        names = _list_template_names()
        for name in names:
            print(name)
        if not names:
            print("(no templates)")
        return

    # --long output: flat table with NAME, DESC, PATH
    names = _list_template_names()
    display = []
    for name in names:
        template_root = _find_template_in_search_path(name)
        if template_root is None:
            continue
        config_file = os.path.join(template_root, ".devcontainer", "devcontainer.json")
        desc = ""
        try:
            data = parse_devcontainer_json(config_file)
            desc = data.get("configuration", {}).get("name", "")
        except (SystemExit, Exception):
            # parse_devcontainer_json calls sys.exit(1) on bad JSON (raises SystemExit,
            # not Exception); catch both so a malformed template doesn't abort the listing.
            pass
        display.append((name, desc, _fmt_path(template_root)))

    if not display:
        print("(no templates)")
        return

    headers = ("NAME", "DESC", "PATH")
    widths = [max(len(h), max((len(r[i]) for r in display), default=0)) for i, h in enumerate(headers)]
    print(_fmt_row(headers, widths))
    for row in display:
        print(_fmt_row(row, widths))


@template_group.command("default")
@click.argument("name", required=False)
def template_default_command(name):
    """Get or set the default template."""
    settings = _load_settings()
    if name is None:
        current = settings.get("default_template", "")
        if current:
            click.echo(current)
        return
    settings["default_template"] = name
    _save_settings(settings)
    click.echo(f"default template set to '{name}'")


@source_group.command("list")
def source_list_command():
    """List configured template search paths."""
    settings = _load_settings()
    for path in settings.get("template_sources", []):
        click.echo(path)


@source_group.command("add")
@click.argument("path", type=click.Path())
def source_add_command(path):
    """Add a template search path."""
    path = os.path.expanduser(path)
    path = os.path.abspath(path)
    settings = _load_settings()
    sources = settings.get("template_sources", [])
    if path not in sources:
        sources.append(path)
        settings["template_sources"] = sources
        _save_settings(settings)
        click.echo(f"added '{path}' to template_sources")
    else:
        click.echo(f"'{path}' is already in template_sources")


@source_group.command("remove")
@click.argument("path", type=click.Path())
def source_remove_command(path):
    """Remove a template search path."""
    path = os.path.expanduser(path)
    path = os.path.abspath(path)
    settings = _load_settings()
    sources = settings.get("template_sources", [])
    if path not in sources:
        logger.error("'%s' not found in template_sources", path)
        sys.exit(1)
    sources.remove(path)
    settings["template_sources"] = sources
    _save_settings(settings)
    click.echo(f"removed '{path}' from template_sources")


@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion_command(shell):
    """Print shell completion setup command."""
    if shell == "fish":
        click.echo("eval (env _DEVCODE_COMPLETE=fish_source devcode)")
    else:
        click.echo(f'eval "$(_DEVCODE_COMPLETE={shell}_source devcode)"')


@cli.command("list")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show all containers (not just running).")
@click.option("-i", "--interactive", is_flag=True, help="Prompt to reopen a listed container.")
def list_command(show_all, interactive):
    """List dev containers."""
    fmt = "{{.CreatedAt}}\t{{.ID}}\t{{.Label \"devcontainer.local_folder\"}}\t{{.Label \"devcontainer.config_file\"}}\t{{.Status}}"
    result = subprocess.run(
        ["docker", "container", "ls", "-a",
         "--filter", "label=devcontainer.local_folder",
         "--format", fmt],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error("docker ps failed — is Docker running?")
        sys.exit(1)

    rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]

    # Sort ascending by CreatedAt (index 0), then drop it
    rows.sort(key=lambda r: r[0])
    rows = [r[1:] for r in rows]  # now: [cid, local_folder, config_file, status]

    # Filter: without -a keep only running containers
    if not show_all:
        rows = [r for r in rows if len(r) >= 4 and r[3].startswith("Up")]

    # Always drop malformed rows so rows and display stay parallel
    rows = [r for r in rows if len(r) >= 4]

    if not rows:
        print("no devcontainers" if show_all else "no running devcontainers")
        return

    # Build display rows: (num, cid, template, path, status)
    display = []
    for i, row in enumerate(rows, 1):
        cid, folder, config, status = row[0], row[1], row[2], row[3]
        template = _template_name_from_config(config) if config else "(unknown)"
        display.append((str(i), cid[:12], template, _fmt_path(folder), status))

    headers = ("#", "CONTAINER ID", "TEMPLATE", "PROJECT PATH", "STATUS")
    widths = [max(len(h), max((len(r[i]) for r in display), default=0)) for i, h in enumerate(headers)]

    print(_fmt_row(headers, widths))
    for row in display:
        print(_fmt_row(row, widths))

    if not interactive:
        return

    choice = input(f"\nOpen [1-{len(display)}]: ").strip()
    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(display)):
            raise ValueError
    except ValueError:
        print("invalid selection")
        sys.exit(1)

    # rows[idx] is [cid, local_folder, config_file, status]
    selected_row = rows[idx]
    cid_full = selected_row[0]
    local_folder = selected_row[1]
    config_file = selected_row[2]

    if not config_file:
        print("container has no config_file label")
        sys.exit(1)

    projectpath = local_folder

    # Resolve container_folder from docker inspect mounts
    container_folder = None
    inspect = subprocess.run(
        ["docker", "inspect", cid_full, "--format", "{{json .Mounts}}"],
        capture_output=True, text=True,
    )
    if inspect.returncode == 0:
        try:
            mounts = json.loads(inspect.stdout.strip())
            for m in mounts:
                if m.get("Type") == "bind" and m.get("Source") == local_folder:
                    container_folder = m.get("Destination")
                    break
        except (json.JSONDecodeError, AttributeError):
            pass

    _do_open(
        projectpath=projectpath,
        template=config_file,
        container_folder=container_folder if container_folder else None,
        timeout=300,
        dry_run=False,
    )


@cli.command("prune")
@click.argument("path", required=False, type=click.Path(file_okay=False, resolve_path=True))
@click.option("--all-projects", is_flag=True, help="Prune containers across all projects.")
@click.option("--include-recent", is_flag=True, help="Also prune the most recently used container.")
def prune_command(path, all_projects, include_recent):
    """Remove stopped containers."""
    if not path and not all_projects:
        click.echo(
            "error: specify a project path or use --all-projects to prune across all projects",
            err=True,
        )
        sys.exit(1)

    fmt = "{{.CreatedAt}}\t{{.ID}}\t{{.Label \"devcontainer.local_folder\"}}\t{{.Label \"devcontainer.config_file\"}}\t{{.Status}}"
    filter_args = ["--filter", "label=devcontainer.local_folder"]
    if path:
        label_value = wsl_to_windows(path) if is_wsl() else path
        filter_args = ["--filter", f"label=devcontainer.local_folder={label_value}"]

    result = subprocess.run(
        ["docker", "container", "ls", "-a"] + filter_args + ["--format", fmt],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error("docker ls failed — is Docker running?")
        sys.exit(1)

    rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]
    stopped = [r for r in rows if len(r) >= 5 and not r[4].startswith("Up")]

    if not stopped:
        click.echo("no stopped containers to prune")
        return

    # Sort ascending by CreatedAt; keep the last (most recent) unless --include-recent
    stopped.sort(key=lambda r: r[0])
    to_prune = stopped if include_recent else stopped[:-1]

    if not to_prune:
        click.echo("no containers to prune (use --include-recent to also prune the most recent)")
        return

    click.echo(f"Containers to remove ({len(to_prune)}):")
    for r in to_prune:
        cid, folder = r[1][:12], r[2]
        click.echo(f"  {cid}  {folder}")

    if not click.confirm(f"\nRemove {len(to_prune)} container(s)?", default=False):
        click.echo("aborted")
        return

    for r in to_prune:
        cid = r[1]
        rm_result = subprocess.run(["docker", "rm", cid], capture_output=True, text=True)
        if rm_result.returncode != 0:
            logger.warning("failed to remove %s: %s", cid[:12], rm_result.stderr.strip())
        else:
            click.echo(f"removed {cid[:12]}")


if __name__ == "__main__":
    cli()
