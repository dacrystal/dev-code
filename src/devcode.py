#!/usr/bin/env python3
import argparse
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
    "  project · editor · container — simplified  "
)

KNOWN_CP_FIELDS = {"source", "target", "override", "owner", "group", "permissions"}

_BASH_COMPLETION = """\
# devcode bash completion
# Requires bash 4.0+ (macOS ships bash 3.2; install bash 5 via Homebrew if needed).
_dev_code() {
    local -a candidates
    mapfile -t candidates < <(devcode completion --complete "$COMP_CWORD" "${COMP_WORDS[@]}" 2>/dev/null)
    if [[ ${#candidates[@]} -eq 0 ]]; then
        local cur="${COMP_WORDS[COMP_CWORD]}"
        mapfile -t COMPREPLY < <(compgen -f -- "$cur")
    else
        COMPREPLY=("${candidates[@]}")
    fi
}
complete -F _dev_code devcode
"""

_ZSH_COMPLETION = """\
# devcode zsh completion
_dev_code() {
    local candidates
    candidates=$(devcode completion --complete "$(( CURRENT - 1 ))" "${words[@]}" 2>/dev/null)
    if [[ -z "$candidates" ]]; then
        _files
    else
        compadd -- ${(f)candidates}
    fi
}
# Note: compdef requires compinit to have been called first.
compdef _dev_code devcode
"""


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


def resolve_template_search_path() -> list[str]:
    """Return ordered list of template search directories from DEVCODE_TEMPLATE_PATH."""
    new_var = os.environ.get("DEVCODE_TEMPLATE_PATH")
    xdg = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
    default = os.path.join(xdg, "dev-code", "templates")
    if not new_var:
        return [default]
    dirs = [d for d in new_var.split(os.pathsep) if d]
    return dirs if dirs else [default]


def _write_template_dir() -> str:
    """Return the first (canonical write) directory from the template search path."""
    return resolve_template_search_path()[0]


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



def parse_devcontainer_json(config_file: str):
    """Parse devcontainer.json. Returns (dict, cli_used: bool).

    Tries in order: devcontainer CLI, jq, Python json+re fallback.
    """
    # Strategy 1: devcontainer CLI (resolves ${localEnv:VAR} automatically)
    if shutil.which("devcontainer"):
        result = subprocess.run(
            ["devcontainer", "read-configuration", "--config", config_file],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                # read-configuration wraps output; extract .configuration if present
                return data.get("configuration", data), True
            except json.JSONDecodeError:
                pass

    # Strategy 2: jq
    if shutil.which("jq"):
        result = subprocess.run(
            ["jq", ".", config_file],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout), False
            except json.JSONDecodeError:
                pass

    # Strategy 3: Python json + re fallback
    with open(config_file) as f:
        content = f.read()

    # Strip full-line // comments (re.MULTILINE makes ^ match start of each line)
    content = re.sub(r"^\s*//[^\n]*\n?", "", content, flags=re.MULTILINE)
    # Strip trailing commas before } or ]
    content = re.sub(r",(\s*[}\]])", r"\1", content)

    try:
        return json.loads(content), False
    except json.JSONDecodeError as e:
        logger.error("failed to parse %s: %s", config_file, e)
        sys.exit(1)


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


def _process_entry(container_id: str, entry: dict, cli_used: bool, idx: int, config_dir: str) -> None:
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
    if not cli_used:
        resolved = _substitute_env_vars(source)
        if resolved is None:
            logger.warning("entry %d source env var unset or empty, skipping", idx)
            return
        source = resolved

    dot_expand = source.endswith("/.")
    if dot_expand:
        source = source[:-2]
        if not source:
            source = "/"  # source was "/." — strip of 2-char string leaves empty; treat as root
    if not os.path.isabs(source):
        source = os.path.abspath(os.path.join(config_dir, source))
    if dot_expand:
        source = source + "/."

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
            _process_entry(container_id, child_entry, cli_used=True, idx=idx, config_dir=config_dir)
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
    data, cli_used = parse_devcontainer_json(config_file)

    dev_code_section = data.get("customizations", {}).get("dev-code")
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
        _process_entry(container_id, entry, cli_used, idx, config_dir)


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


def cmd_open(args) -> None:
    """open subcommand: open a project in VS Code using a devcontainer template."""
    config_file = resolve_template(args.template)

    project_path = os.path.abspath(args.projectpath)
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

    container_folder = args.container_folder or f"/workspaces/{os.path.basename(project_path)}"
    uri = build_devcontainer_uri(project_path, config_file, container_folder)

    if args.dry_run:
        _cmd_open_dry_run(config_file, project_path, uri)
        return

    if not shutil.which("code"):
        logger.error("'code' not found on PATH")
        sys.exit(1)

    subprocess.Popen(["code", "--folder-uri", uri], start_new_session=True)
    run_post_launch(config_file, project_path, args.timeout)


def _cmd_open_dry_run(config_file: str, project_path: str, uri: str) -> None:
    """Print dry-run plan to stdout without executing anything."""
    print(f"Config:  {config_file}")
    print(f"URI:     {uri}")

    data, cli_used = parse_devcontainer_json(config_file)
    dev_code_section = data.get("customizations", {}).get("dev-code")
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
        if not cli_used:
            unset_vars = [m.group(1) for m in re.finditer(r"\$\{localEnv:([^}]+)\}", source)
                          if not os.environ.get(m.group(1))]
            if unset_vars:
                logger.warning("entry %d: env var unset: %s", idx, ", ".join(unset_vars))
                print(f"  [{idx}] <unset: {unset_vars[0]}> → {target}")
                continue
            source = re.sub(r"\$\{localEnv:([^}]+)\}", lambda m: os.environ[m.group(1)], source)

        # Relative path resolution
        dot_expand = source.endswith("/.")
        if dot_expand:
            source = source[:-2] or "/"
        if not os.path.isabs(source):
            source = os.path.abspath(os.path.join(config_dir, source))
        if dot_expand:
            source += "/."

        annotation = " [missing]" if not os.path.exists(source.rstrip("/.")) else ""
        print(f"  [{idx}] {source}{annotation} → {target}")

    print("(dry run — no operations executed)")


def cmd_new(args) -> None:
    """Create a new template by copying a base template."""
    write_dir = _write_template_dir()
    dest = os.path.join(write_dir, args.name)

    # Step 1: fail if name already exists
    if os.path.exists(dest):
        logger.error("template '%s' already exists: %s", args.name, dest)
        sys.exit(1)

    # Step 2: resolve base — search all dirs, then builtins
    base_name = args.base or "dev-code"
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
    print(f"Created template '{args.name}' at {dest}")

    # Step 6: --edit
    if args.edit:
        open_args = argparse.Namespace(
            template=args.name,
            projectpath=dest,
            container_folder=None,
            timeout=300,
            dry_run=False,
        )
        cmd_open(open_args)


def cmd_edit(args) -> None:
    """Open a template directory directly in VS Code for editing."""
    if args.template is not None:
        template_root = _find_template_in_search_path(args.template)
        if template_root is None:
            logger.error("template not found: %s", args.template)
            sys.exit(1)
        subprocess.run(["code", template_root])
    else:
        for search_dir in resolve_template_search_path():
            if os.path.isdir(search_dir):
                subprocess.run(["code", search_dir])
                return
        logger.error(
            "no template directory found — run 'devcode init' or 'devcode new <name>' to get started"
        )
        sys.exit(1)


def cmd_init(args) -> None:
    """Seed the built-in dev-code template into the user template dir."""
    builtin = get_builtin_template_path("dev-code")
    if builtin is None:
        logger.error("built-in template 'dev-code' not found — packaging error")
        sys.exit(1)

    write_dir = _write_template_dir()
    dest = os.path.join(write_dir, "dev-code")

    if os.path.exists(dest):
        print(f"Skipped 'dev-code': already exists at {dest}")
        return

    try:
        os.makedirs(write_dir, exist_ok=True)
    except OSError as e:
        logger.error("cannot create template dir %s: %s", write_dir, e)
        sys.exit(1)

    try:
        shutil.copytree(builtin, dest)
    except Exception as e:
        logger.error("copy failed: %s", e)
        sys.exit(1)

    print(f"Copied built-in 'dev-code' to {dest}")


def cmd_list(args) -> None:
    """List available templates."""
    search_dirs = resolve_template_search_path()

    if not args.long:
        names = _list_template_names()
        for name in names:
            print(name)
        if not names:
            print("(no templates — run 'devcode init' or 'devcode new <name>' to get started)")
        return

    # --long output: one section per search dir
    any_printed = False
    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            logger.debug("template search dir not found, skipping: %s", search_dir)
            continue
        templates = [
            name for name in sorted(os.listdir(search_dir))
            if _is_valid_template(os.path.join(search_dir, name))
        ]
        print(search_dir)
        if templates:
            col_w = max(len(n) for n in templates) + 2
            for name in templates:
                print(f"  {name:<{col_w}}{os.path.join(search_dir, name)}")
        else:
            print("  (no templates)")
        print()
        any_printed = True

    if not any_printed:
        print("(no template directories found — run 'devcode init' or 'devcode new <name>' to get started)")


def _template_name_from_config(config_path: str) -> str:
    """Extract template name as the directory immediately above .devcontainer/."""
    norm = os.path.normpath(config_path)
    parts = norm.replace("\\", "/").split("/")
    for i, part in enumerate(parts):
        if part == ".devcontainer" and i > 0:
            return parts[i - 1]
    return os.path.basename(os.path.dirname(os.path.dirname(norm)))


def cmd_ps(args) -> None:
    """List devcontainers (running by default; all with -a)."""
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
    if not args.all:
        rows = [r for r in rows if len(r) >= 4 and r[3].startswith("Up")]

    # Always drop malformed rows so rows and display stay parallel
    rows = [r for r in rows if len(r) >= 4]

    if not rows:
        print("no devcontainers" if args.all else "no running devcontainers")
        return

    home = os.path.expanduser("~")

    def fmt_path(p):
        return "~" + p[len(home):] if p.startswith(home) else p

    # Build display rows: (num, cid, template, path, status)
    display = []
    for i, row in enumerate(rows, 1):
        cid, folder, config, status = row[0], row[1], row[2], row[3]
        template = _template_name_from_config(config) if config else "(unknown)"
        display.append((str(i), cid[:12], template, fmt_path(folder), status))

    headers = ("#", "CONTAINER ID", "TEMPLATE", "PROJECT PATH", "STATUS")
    widths = [max(len(h), max((len(r[i]) for r in display), default=0)) for i, h in enumerate(headers)]

    def fmt_row(r):
        return "  ".join(f"{v:<{widths[i]}}" for i, v in enumerate(r))

    print(fmt_row(headers))
    for row in display:
        print(fmt_row(row))

    if not args.interactive:
        return

    choice = input(f"Open [1-{len(display)}]: ").strip()
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

    if not container_folder:
        container_folder = f"/workspaces/{os.path.basename(local_folder)}"

    open_args = argparse.Namespace(
        template=config_file,
        projectpath=projectpath,
        container_folder=container_folder,
        timeout=300,
        dry_run=False,
    )
    cmd_open(open_args)


_SUBCOMMANDS = ["open", "new", "edit", "init", "list", "ps", "completion"]

_SUBCOMMAND_FLAGS = {
    "open": ["--dry-run", "--container-folder", "--timeout"],
    "new": ["--edit"],
    "edit": [],
    "init": [],
    "list": ["--long"],
    "ps": ["-a", "-i"],
    "completion": [],
}


def cmd_completion(args) -> None:
    """completion subcommand: print shell completion script or candidates to stdout."""
    if args.complete_words is not None:
        # Internal path: called by the shell completion scripts.
        # Always exits 0; prints nothing on any error.
        try:
            words = args.complete_words
            if not words:
                sys.exit(0)
            try:
                cword_index = int(words[0])
            except ValueError:
                sys.exit(0)
            words = words[1:]

            if not (0 <= cword_index < len(words)):
                sys.exit(0)

            current_word = words[cword_index]

            if cword_index == 1:
                candidates = _SUBCOMMANDS
            else:
                subcommand = words[1] if len(words) > 1 else ""
                if subcommand == "list":
                    candidates = ["--long"]
                elif subcommand == "completion":
                    candidates = ["bash", "zsh"] if cword_index == 2 else []
                elif current_word.startswith("-"):
                    candidates = _SUBCOMMAND_FLAGS.get(subcommand, [])
                else:
                    if subcommand == "open" and cword_index == 2:
                        candidates = _list_template_names()
                    elif subcommand == "new" and cword_index == 3:
                        candidates = _list_template_names()
                    elif subcommand == "edit" and cword_index == 2:
                        candidates = _list_template_names()
                    else:
                        candidates = []

            for c in candidates:
                if c.startswith(current_word):
                    print(c)
        except Exception:
            pass
        sys.exit(0)

    if args.shell == "bash":
        print(_BASH_COMPLETION, end="")
    elif args.shell == "zsh":
        print(_ZSH_COMPLETION, end="")
    else:
        logger.error("unknown shell %r: supported shells are bash and zsh", args.shell)
        sys.exit(1)


class _BannerParser(argparse.ArgumentParser):
    """ArgumentParser that shows the banner only in full --help output, not in error usage lines."""

    def format_help(self):
        return BANNER + "\n\n" + super().format_help()


def main():
    try:
        _version = importlib.metadata.version("dev-code")
    except importlib.metadata.PackageNotFoundError:
        _version = "(dev)"

    parser = _BannerParser(prog="devcode")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version}")
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="subcommand")

    p_open = subparsers.add_parser("open")
    p_open.add_argument("template")
    p_open.add_argument("projectpath")
    p_open.add_argument("--container-folder")
    p_open.add_argument("--timeout", type=int, default=300)
    p_open.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_new = subparsers.add_parser("new")
    p_new.add_argument("name")
    p_new.add_argument("base", nargs="?")
    p_new.add_argument("--edit", action="store_true")

    p_edit = subparsers.add_parser("edit")
    p_edit.add_argument("template", nargs="?")

    subparsers.add_parser("init")

    p_list = subparsers.add_parser("list")
    p_list.add_argument("--long", action="store_true")

    p_ps = subparsers.add_parser("ps")
    p_ps.add_argument("-a", "--all", action="store_true", dest="all")
    p_ps.add_argument("-i", "--interactive", action="store_true", dest="interactive")

    p_completion = subparsers.add_parser("completion")
    p_completion.add_argument("shell", nargs="?")
    p_completion.add_argument("--complete", nargs="*", dest="complete_words", help=argparse.SUPPRESS)

    args = parser.parse_args()
    if args.subcommand is None:
        parser.print_help()
        sys.exit(0)
    _configure_logging(args.verbose)

    dispatch = {
        "open": cmd_open,
        "new": cmd_new,
        "edit": cmd_edit,
        "init": cmd_init,
        "list": cmd_list,
        "ps": cmd_ps,
        "completion": cmd_completion,
    }
    dispatch[args.subcommand](args)


if __name__ == "__main__":
    main()