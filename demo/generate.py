#!/usr/bin/env python3
"""Generate an asciinema v2 .cast file from a YAML demo script."""

import json
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).parent


def _validate(demos: list) -> None:
    """Validate step sequences across all demos before generation begins.

    Raises ValueError if any prompt step does not immediately follow a cmd step,
    or if any comment step has a null value.
    """
    for demo in demos:
        steps = demo.get("steps", [])
        name = demo.get("name", "?")
        for i, step in enumerate(steps):
            if "comment" in step and step["comment"] is None:
                raise ValueError(
                    f"Demo '{name}' step {i}: 'comment' value must not be null"
                )
            if "prompt" in step:
                prev = steps[i - 1] if i > 0 else {}
                if i == 0 or "cmd" not in prev:
                    prev_key = list(prev.keys())[0] if prev else "none"
                    raise ValueError(
                        f"Demo '{name}' step {i}: 'prompt' must immediately follow 'cmd' "
                        f"(preceding step has key '{prev_key}')"
                    )


def _next_ps1(steps: list, from_idx: int, demo_ps1: str | None, global_ps1: str, fallback_step: dict) -> str:
    """Return the resolved PS1 for the next cmd/comment step at or after from_idx.

    Scans forward, skipping pause and prompt steps.
    Falls back to fallback_step's resolved PS1 if no cmd/comment found.
    """
    for step in steps[from_idx:]:
        if "cmd" in step or "comment" in step:
            return next(v for v in (step.get("ps1"), demo_ps1, global_ps1) if v is not None)
    return next(v for v in (fallback_step.get("ps1"), demo_ps1, global_ps1) if v is not None)


def generate(config: dict, demos: list, output_path: str | Path) -> None:
    """Generate a .cast file from config and demos.

    Args:
        config: dict with keys: width, height, title, ps1,
                typing_delay, post_cmd_delay, between_demos{delay, clear},
                comment_color
        demos: list of {name, ps1?, steps} where each step is
               {cmd, output?, ps1?}, {prompt, output?}, {pause}, or {comment, ps1?}
        output_path: path to write the .cast file
    """
    width = config["width"]
    height = config["height"]
    title = config["title"]
    global_ps1 = config["ps1"]
    typing_delay = config["typing_delay"]
    post_cmd_delay = config["post_cmd_delay"]
    between_delay = config["between_demos"]["delay"]
    between_clear = config["between_demos"]["clear"]
    comment_color = config["comment_color"]

    _validate(demos)

    events = []
    t = 0.0  # cumulative timestamp

    def emit(text: str) -> None:
        events.append([round(t, 6), "o", text])

    for demo_idx, demo in enumerate(demos):
        steps = demo["steps"]
        demo_ps1 = demo.get("ps1")
        prev_cmd_step: dict | None = None

        # Demo-start PS1: find first cmd/comment step and emit its PS1
        for step in steps:
            if "cmd" in step or "comment" in step:
                emit(next(v for v in (step.get("ps1"), demo_ps1, global_ps1) if v is not None))
                break

        for step_idx, step in enumerate(steps):
            if "cmd" in step:
                prev_cmd_step = step
                for ch in step["cmd"]:
                    t += typing_delay
                    emit(ch)
                emit("\r\n")
                raw_output = (step.get("output", "") or "").replace("\\x1b", "\x1b")
                lines = raw_output.rstrip("\n").splitlines()
                next_step = steps[step_idx + 1] if step_idx + 1 < len(steps) else None
                next_is_prompt = next_step is not None and "prompt" in next_step
                for line_idx, line in enumerate(lines):
                    is_last = line_idx == len(lines) - 1
                    if is_last and next_is_prompt:
                        emit(line)
                    else:
                        emit(line + "\r\n")
                if not next_is_prompt:
                    emit(_next_ps1(steps, step_idx + 1, demo_ps1, global_ps1, step))
                t += post_cmd_delay

            elif "prompt" in step:
                for ch in step["prompt"]:
                    t += typing_delay
                    emit(ch)
                emit("\r\n")
                raw_output = (step.get("output", "") or "").replace("\\x1b", "\x1b")
                lines = raw_output.rstrip("\n").splitlines()
                for line in lines:
                    emit(line + "\r\n")
                fallback = prev_cmd_step if prev_cmd_step is not None else step
                emit(_next_ps1(steps, step_idx + 1, demo_ps1, global_ps1, fallback))
                t += post_cmd_delay

            elif "pause" in step:
                t += step["pause"]

            elif "comment" in step:
                text = "# " + step["comment"]
                emit(comment_color)
                for ch in text:
                    t += typing_delay
                    emit(ch)
                emit("\x1b[0m")
                emit("\r\n")
                emit(_next_ps1(steps, step_idx + 1, demo_ps1, global_ps1, step))
                t += post_cmd_delay

            else:
                keys = list(step.keys())
                raise ValueError(
                    f"Unrecognized step (keys: {keys}) in demo '{demo['name']}'. "
                    f"Steps must have 'cmd', 'prompt', 'pause', or 'comment'."
                )

        if demo_idx < len(demos) - 1:
            t += between_delay
            if between_clear:
                emit("\x1b[2J\x1b[H")

    header = {
        "version": 2,
        "width": width,
        "height": height,
        "title": title,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for event in events:
            f.write(json.dumps(event) + "\n")


def main() -> None:
    yaml_path = _HERE / "demo.yaml"
    cast_path = _HERE / "demo.cast"

    try:
        with open(yaml_path, encoding="utf-8") as f:
            script = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: demo script not found: {yaml_path}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: failed to parse {yaml_path}: {e}", file=sys.stderr)
        sys.exit(1)

    generate(script["config"], script["demos"], str(cast_path))
    print(f"Written: {cast_path}")


if __name__ == "__main__":
    main()
