import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Load demo/generate.py as a module
_gen_path = Path(__file__).parent.parent / "demo" / "generate.py"
spec = importlib.util.spec_from_file_location("generate", _gen_path)
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)


def _parse_cast(path):
    """Return (header_dict, [event, ...]) from a .cast file."""
    lines = Path(path).read_text().splitlines()
    header = json.loads(lines[0])
    events = [json.loads(l) for l in lines[1:] if l.strip()]
    return header, events


class TestHeader(unittest.TestCase):
    def test_header_fields(self):
        cfg = {"width": 100, "height": 25, "title": "t", "ps1": "$ ",
               "typing_delay": 0.05, "post_cmd_delay": 1.0,
               "between_demos": {"delay": 1.0, "clear": False},
               "comment_color": "\x1b[90m"}
        demos = [{"name": "d", "steps": [{"cmd": "echo hi", "output": "hi"}]}]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        header, _ = _parse_cast(out)
        self.assertEqual(header["version"], 2)
        self.assertEqual(header["width"], 100)
        self.assertEqual(header["height"], 25)
        self.assertEqual(header["title"], "t")
        os.unlink(out)


class TestCmdStep(unittest.TestCase):
    def _run(self, steps, cfg_overrides=None):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.1, "post_cmd_delay": 1.0,
               "between_demos": {"delay": 1.0, "clear": False},
               "comment_color": "\x1b[90m"}
        if cfg_overrides:
            cfg.update(cfg_overrides)
        demos = [{"name": "d", "steps": steps}]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        return events

    def test_ps1_emitted_as_first_event(self):
        events = self._run([{"cmd": "hi", "output": "out"}])
        self.assertEqual(events[0][1], "o")
        self.assertEqual(events[0][2], "$ ")

    def test_ps1_has_zero_timestamp(self):
        events = self._run([{"cmd": "hi", "output": "out"}])
        self.assertEqual(events[0][0], 0.0)

    def test_command_typed_char_by_char(self):
        events = self._run([{"cmd": "hi", "output": "out"}])
        # After prompt: one event per character
        chars = [e[2] for e in events[1:3]]
        self.assertEqual(chars, ["h", "i"])

    def test_command_chars_advance_timestamp(self):
        events = self._run([{"cmd": "hi", "output": "out"}])
        # Each char advances by typing_delay (0.1)
        self.assertAlmostEqual(events[1][0], 0.1, places=5)
        self.assertAlmostEqual(events[2][0], 0.2, places=5)

    def test_newline_after_command(self):
        events = self._run([{"cmd": "hi", "output": "out"}])
        newline_event = events[3]  # prompt + 2 chars + newline
        self.assertIn("\r\n", newline_event[2])

    def test_output_emitted_at_same_timestamp_as_newline(self):
        events = self._run([{"cmd": "hi", "output": "out"}])
        newline_ts = events[3][0]
        output_ts = events[4][0]
        self.assertEqual(newline_ts, output_ts)

    def test_output_line_ends_with_crlf(self):
        events = self._run([{"cmd": "hi", "output": "out"}])
        output_event = events[4]
        self.assertTrue(output_event[2].endswith("\r\n"))
        self.assertFalse(output_event[2].endswith("\r\n\r\n"))

    def test_multiline_output_each_line_gets_crlf(self):
        events = self._run([{"cmd": "hi", "output": "line1\nline2\n"}])
        # Events: prompt, h, i, \r\n, line1\r\n, line2\r\n
        self.assertEqual(events[4][2], "line1\r\n")
        self.assertEqual(events[5][2], "line2\r\n")

    def test_output_lines_share_timestamp(self):
        events = self._run([{"cmd": "hi", "output": "line1\nline2\n"}])
        output_events = [e for e in events if "line" in e[2]]
        self.assertEqual(output_events[0][0], output_events[1][0])

    def test_no_output_field_emits_no_output_events(self):
        events = self._run([{"cmd": "hi"}])
        # demo-start PS1 + h + i + \r\n + trailing PS1 = 5 events
        texts = [e[2] for e in events]
        self.assertEqual(texts, ["$ ", "h", "i", "\r\n", "$ "])

    def test_post_cmd_delay_advances_next_typing_timestamp(self):
        # cmd a: PS1(0), a(0.1), \r\n(0.1), trailing PS1(0.1) → post_cmd → b types at 1.2
        events = self._run([{"cmd": "a"}, {"cmd": "b"}])
        b_char = next(e for e in events if e[2] == "b")
        self.assertAlmostEqual(b_char[0], 1.2, places=5)

    def test_post_cmd_delay_produces_no_cast_event(self):
        events = self._run([{"cmd": "a"}, {"cmd": "b"}])
        texts = [e[2] for e in events]
        # demo-start PS1, a, \r\n, trailing PS1 (for b), b, \r\n, trailing PS1 (fallback)
        self.assertEqual(texts, ["$ ", "a", "\r\n", "$ ", "b", "\r\n", "$ "])


class TestPauseStep(unittest.TestCase):
    def _run(self, steps):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.1, "post_cmd_delay": 1.0,
               "between_demos": {"delay": 1.0, "clear": False},
               "comment_color": "\x1b[90m"}
        demos = [{"name": "d", "steps": steps}]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        return events

    def test_pause_produces_no_cast_event(self):
        events = self._run([{"pause": 2.0}])
        self.assertEqual(len(events), 0)

    def test_pause_advances_timestamp_for_next_event(self):
        events = self._run([{"pause": 2.0}, {"cmd": "a"}])
        # demo-start PS1 at t=0 (before pause), then a at 2.0+0.1=2.1
        self.assertAlmostEqual(events[0][0], 0.0, places=5)
        self.assertEqual(events[0][2], "$ ")
        a_char = next(e for e in events if e[2] == "a")
        self.assertAlmostEqual(a_char[0], 2.1, places=5)

    def test_pause_after_cmd_adds_to_post_cmd_delay(self):
        # cmd a → pause 2.0 → cmd b: b typed at 0.1 + 1.0 + 2.0 + 0.1 = 3.2
        events = self._run([{"cmd": "a"}, {"pause": 2.0}, {"cmd": "b"}])
        b_char = next(e for e in events if e[2] == "b")
        self.assertAlmostEqual(b_char[0], 3.2, places=5)


class TestBetweenDemos(unittest.TestCase):
    def _run(self, demos, between_cfg):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.0, "post_cmd_delay": 0.0,
               "between_demos": between_cfg,
               "comment_color": "\x1b[90m"}
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        return events

    def test_clear_emitted_between_demos(self):
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        events = self._run(demos, {"delay": 1.0, "clear": True})
        texts = [e[2] for e in events]
        self.assertIn("\x1b[2J\x1b[H", texts)

    def test_clear_not_emitted_when_clear_false(self):
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        events = self._run(demos, {"delay": 1.0, "clear": False})
        texts = [e[2] for e in events]
        self.assertNotIn("\x1b[2J\x1b[H", texts)

    def test_clear_timestamp_is_after_between_delay(self):
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        events = self._run(demos, {"delay": 3.0, "clear": True})
        clear_event = next(e for e in events if e[2] == "\x1b[2J\x1b[H")
        # typing_delay=0, post_cmd_delay=0 → \r\n for 'a' is at 0.0
        # between_delay=3.0 → clear at 3.0
        self.assertAlmostEqual(clear_event[0], 3.0, places=5)

    def test_next_demo_prompt_shares_clear_timestamp(self):
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        events = self._run(demos, {"delay": 3.0, "clear": True})
        clear_ts = next(e[0] for e in events if e[2] == "\x1b[2J\x1b[H")
        # Find the prompt of demo 2 (the "$ " after the clear)
        clear_idx = next(i for i, e in enumerate(events) if e[2] == "\x1b[2J\x1b[H")
        next_prompt_ts = events[clear_idx + 1][0]
        self.assertEqual(clear_ts, next_prompt_ts)

    def test_no_transition_after_last_demo(self):
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        events = self._run(demos, {"delay": 1.0, "clear": True})
        # Clear should appear exactly once
        clears = [e for e in events if e[2] == "\x1b[2J\x1b[H"]
        self.assertEqual(len(clears), 1)

    def test_single_demo_no_transition(self):
        demos = [{"name": "d1", "steps": [{"cmd": "a"}]}]
        events = self._run(demos, {"delay": 1.0, "clear": True})
        clears = [e for e in events if e[2] == "\x1b[2J\x1b[H"]
        self.assertEqual(len(clears), 0)


class TestErrorHandling(unittest.TestCase):
    def test_unrecognized_step_raises(self):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.1, "post_cmd_delay": 1.0,
               "between_demos": {"delay": 1.0, "clear": False},
               "comment_color": "\x1b[90m"}
        demos = [{"name": "d", "steps": [{"unknown_key": "value"}]}]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        with self.assertRaises(ValueError):
            generate.generate(cfg, demos, out)
        os.unlink(out)


class TestPromptStep(unittest.TestCase):
    def _cfg(self, **overrides):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.1, "post_cmd_delay": 1.0,
               "between_demos": {"delay": 1.0, "clear": False},
               "comment_color": "\x1b[90m"}
        cfg.update(overrides)
        return cfg

    def _run(self, steps, **cfg_overrides):
        cfg = self._cfg(**cfg_overrides)
        demos = [{"name": "d", "steps": steps}]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        return events

    def test_prompt_step_emits_no_ps1_before_typing(self):
        # Prompt typing is not preceded by a PS1 event
        events = self._run([{"cmd": "q", "output": "Pick:"}, {"prompt": "2"}])
        char_idx = next(i for i, e in enumerate(events) if e[2] == "2")
        self.assertNotEqual(events[char_idx - 1][2], "$ ")
        self.assertEqual(events[char_idx - 1][2], "Pick:")

    def test_prompt_step_types_char_by_char(self):
        events = self._run([{"cmd": "q", "output": "Answer:"}, {"prompt": "ab"}])
        a_idx = next(i for i, e in enumerate(events) if e[2] == "a")
        self.assertEqual(events[a_idx][2], "a")
        self.assertEqual(events[a_idx + 1][2], "b")

    def test_prompt_step_chars_advance_timestamp(self):
        # cmd "q" no output: PS1(0), q(0.1), \r\n(0.1), no trailing PS1
        # post_cmd_delay(1.0): t=1.1 → prompt a(1.2), b(1.3)
        events = self._run([{"cmd": "q"}, {"prompt": "ab"}])
        a_idx = next(i for i, e in enumerate(events) if e[2] == "a")
        self.assertAlmostEqual(events[a_idx][0], 1.2, places=5)
        self.assertAlmostEqual(events[a_idx + 1][0], 1.3, places=5)

    def test_prompt_step_emits_crlf_after_value(self):
        events = self._run([{"cmd": "q", "output": "Pick:"}, {"prompt": "2"}])
        char_idx = next(i for i, e in enumerate(events) if e[2] == "2")
        self.assertEqual(events[char_idx + 1][2], "\r\n")

    def test_prompt_step_output_shares_crlf_timestamp(self):
        events = self._run([{"cmd": "q", "output": "Pick:"}, {"prompt": "2", "output": "done"}])
        char_idx = next(i for i, e in enumerate(events) if e[2] == "2")
        crlf_ts = events[char_idx + 1][0]
        output_ts = events[char_idx + 2][0]
        self.assertEqual(crlf_ts, output_ts)

    def test_prompt_step_output_ends_with_crlf(self):
        events = self._run([{"cmd": "q", "output": "Pick:"}, {"prompt": "2", "output": "done"}])
        char_idx = next(i for i, e in enumerate(events) if e[2] == "2")
        self.assertTrue(events[char_idx + 2][2].endswith("\r\n"))

    def test_empty_prompt_value_no_chars_emitted(self):
        # cmd "q" no output, post_cmd_delay=1.0, typing_delay=0.1
        # PS1(0), q(0.1), \r\n(0.1), [no trailing PS1], post_cmd: t=1.1
        # prompt: \r\n at t=1.1 (no chars typed)
        events = self._run([{"cmd": "q"}, {"prompt": ""}])
        crlf_events = [e for e in events if e[2] == "\r\n"]
        # crlf_events[0] = cmd's \r\n at 0.1, crlf_events[1] = prompt's at 1.1
        self.assertAlmostEqual(crlf_events[1][0], 1.1, places=5)

    def test_empty_prompt_output_shares_timestamp(self):
        events = self._run([{"cmd": "q"}, {"prompt": "", "output": "hi"}])
        crlf_events = [e for e in events if e[2] == "\r\n"]
        prompt_crlf_ts = crlf_events[1][0]
        output_event = next(e for e in events if "hi" in e[2])
        self.assertEqual(prompt_crlf_ts, output_event[0])

    def test_lookahead_last_cmd_output_line_has_no_crlf(self):
        steps = [
            {"cmd": "a", "output": "line1\nOpen [1-2]:"},
            {"prompt": "1"},
        ]
        events = self._run(steps)
        output_events = [e for e in events if "line1" in e[2] or "Open" in e[2]]
        self.assertEqual(output_events[0][2], "line1\r\n")
        self.assertEqual(output_events[1][2], "Open [1-2]:")

    def test_consecutive_prompts_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"cmd": "a"}, {"prompt": "x"}, {"prompt": "y"}])

    def test_prompt_as_first_step_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"prompt": "x"}])

    def test_cmd_no_output_followed_by_prompt(self):
        # No output lines to suppress; prompt types on line after cmd's \r\n
        # With current (pre-refactor) PS1 timing: PS1(0), a(0.1), \r\n(0.1), x(1.2), \r\n(1.2)
        steps = [{"cmd": "a"}, {"prompt": "x"}]
        events = self._run(steps)
        texts = [e[2] for e in events]
        # PS1 is still at start in this task (Task 2 moves it); just verify no crash and x appears
        x_idx = next(i for i, e in enumerate(events) if e[2] == "x")
        self.assertGreater(x_idx, 0)

    def test_pause_between_cmd_and_prompt_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"cmd": "a", "output": "last"}, {"pause": 1.0}, {"prompt": "x"}])

    def test_lookahead_does_not_trigger_across_demos(self):
        # cmd last in demo1 always gets \r\n even if next demo starts differently
        demos = [
            {"name": "d1", "steps": [{"cmd": "a", "output": "last line"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        cfg = self._cfg()
        cfg["between_demos"] = {"delay": 0.0, "clear": False}
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        output_event = next(e for e in events if "last line" in e[2])
        self.assertTrue(output_event[2].endswith("\r\n"))

    def test_pause_after_prompt_stacks_on_post_cmd_delay(self):
        # cmd q → prompt a → pause 2.0 → cmd b
        # PS1(0), q(0.1), \r\n(0.1), post_cmd(1.0)→t=1.1
        # a(1.2), \r\n(1.2), post_cmd(1.0)→t=2.2, pause(2.0)→t=4.2
        # b(4.3)
        steps = [{"cmd": "q"}, {"prompt": "a"}, {"pause": 2.0}, {"cmd": "b"}]
        events = self._run(steps)
        b_char = next(e for e in events if e[2] == "b")
        self.assertAlmostEqual(b_char[0], 4.3, places=5)

    def test_post_cmd_delay_after_prompt_advances_next_typing(self):
        # cmd q → prompt a → cmd b
        # q(0.1), post_cmd→t=1.1, a(1.2), post_cmd→t=2.2, b(2.3)
        steps = [{"cmd": "q"}, {"prompt": "a"}, {"cmd": "b"}]
        events = self._run(steps)
        b_char = next(e for e in events if e[2] == "b")
        self.assertAlmostEqual(b_char[0], 2.3, places=5)


class TestPS1Override(unittest.TestCase):
    def _run(self, demos, global_ps1="$ "):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": global_ps1,
               "typing_delay": 0.0, "post_cmd_delay": 0.0,
               "between_demos": {"delay": 0.0, "clear": False},
               "comment_color": "\x1b[90m"}
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        return events

    def test_global_ps1_used_by_default(self):
        demos = [{"name": "d", "steps": [{"cmd": "a"}]}]
        events = self._run(demos, global_ps1="G> ")
        self.assertEqual(events[0][2], "G> ")

    def test_demo_ps1_overrides_global(self):
        demos = [{"name": "d", "ps1": "D> ", "steps": [{"cmd": "a"}]}]
        events = self._run(demos, global_ps1="G> ")
        self.assertEqual(events[0][2], "D> ")

    def test_step_ps1_overrides_demo(self):
        demos = [{"name": "d", "ps1": "D> ",
                  "steps": [{"cmd": "a", "ps1": "S> "}]}]
        events = self._run(demos, global_ps1="G> ")
        self.assertEqual(events[0][2], "S> ")

    def test_step_ps1_does_not_affect_other_steps(self):
        demos = [{"name": "d", "steps": [
            {"cmd": "a", "ps1": "S> "},
            {"cmd": "b"},
        ]}]
        events = self._run(demos, global_ps1="G> ")
        ps1_events = [e for e in events if e[2] in ("S> ", "G> ")]
        # demo-start PS1 = S> (first cmd), trailing after a = G> (next cmd b), trailing after b = G>
        self.assertEqual(ps1_events[0][2], "S> ")
        self.assertEqual(ps1_events[1][2], "G> ")
        self.assertEqual(ps1_events[2][2], "G> ")

    def test_prompt_step_unaffected_by_ps1(self):
        demos = [{"name": "d", "ps1": "D> ", "steps": [
            {"cmd": "q", "output": "Pick:"},
            {"prompt": "x"},
        ]}]
        events = self._run(demos)
        x_idx = next(i for i, e in enumerate(events) if e[2] == "x")
        # Event immediately before "x" is the last output (not a PS1)
        self.assertNotEqual(events[x_idx - 1][2], "D> ")
        self.assertEqual(events[x_idx - 1][2], "Pick:")


class TestPromptValidation(unittest.TestCase):
    def _cfg(self):
        return {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
                "typing_delay": 0.0, "post_cmd_delay": 0.0,
                "between_demos": {"delay": 0.0, "clear": False},
                "comment_color": "\x1b[90m"}

    def _run(self, steps):
        cfg = self._cfg()
        demos = [{"name": "d", "steps": steps}]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        try:
            generate.generate(cfg, demos, out)
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_prompt_after_cmd_is_valid(self):
        self._run([{"cmd": "a"}, {"prompt": "x"}])  # must not raise

    def test_prompt_as_first_step_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"prompt": "x"}])

    def test_prompt_after_pause_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"cmd": "a"}, {"pause": 1.0}, {"prompt": "x"}])

    def test_prompt_after_comment_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"comment": "hi"}, {"prompt": "x"}])

    def test_prompt_after_prompt_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"cmd": "a"}, {"prompt": "x"}, {"prompt": "y"}])

    def test_validation_is_global_prepass(self):
        # demo1 valid, demo2 invalid: error raised before processing demo1
        cfg = self._cfg()
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"prompt": "x"}]},
        ]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        try:
            with self.assertRaises(ValueError):
                generate.generate(cfg, demos, out)
        finally:
            os.unlink(out)

    def test_comment_null_raises(self):
        with self.assertRaises(ValueError):
            self._run([{"comment": None}])


class TestPS1Timing(unittest.TestCase):
    def _run(self, demos, **cfg_overrides):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.0, "post_cmd_delay": 0.0,
               "between_demos": {"delay": 0.0, "clear": False},
               "comment_color": "\x1b[90m"}
        cfg.update(cfg_overrides)
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        return events

    def _demo(self, steps):
        return [{"name": "d", "steps": steps}]

    def test_demo_start_ps1_is_first_event_at_t0(self):
        events = self._run(self._demo([{"cmd": "a"}]))
        self.assertEqual(events[0][2], "$ ")
        self.assertAlmostEqual(events[0][0], 0.0, places=5)

    def test_trailing_ps1_after_cmd_output_zero_delta(self):
        # trailing PS1 shares timestamp with last output event
        events = self._run(self._demo([{"cmd": "a", "output": "out"}]))
        # Events: PS1(0), a(0), \r\n(0), out\r\n(0), trailing PS1(0)
        self.assertEqual(events[-1][2], "$ ")
        self.assertEqual(events[-1][0], events[-2][0])

    def test_trailing_ps1_after_cmd_no_output_follows_crlf(self):
        events = self._run(self._demo([{"cmd": "a"}]))
        # Events: PS1, a, \r\n, trailing PS1
        self.assertEqual(len(events), 4)
        self.assertEqual(events[3][2], "$ ")
        self.assertEqual(events[3][0], events[2][0])  # same t as \r\n

    def test_no_trailing_ps1_when_cmd_followed_by_prompt(self):
        events = self._run(self._demo([{"cmd": "a", "output": "Pick:"}, {"prompt": "x"}]))
        # "Pick:" has no \r\n (lookahead), event after it is "x" (not PS1)
        pick_idx = next(i for i, e in enumerate(events) if "Pick:" in e[2])
        self.assertEqual(events[pick_idx + 1][2], "x")

    def test_trailing_ps1_after_prompt(self):
        events = self._run(self._demo([{"cmd": "a", "output": "Pick:"}, {"prompt": "x"}]))
        x_idx = next(i for i, e in enumerate(events) if e[2] == "x")
        # x, \r\n, trailing PS1
        self.assertEqual(events[x_idx + 1][2], "\r\n")
        self.assertEqual(events[x_idx + 2][2], "$ ")

    def test_trailing_ps1_source_scans_past_pause(self):
        # cmd a (ps1 A>) → pause → cmd b (ps1 B>): trailing PS1 after a = B>
        events = self._run(self._demo([
            {"cmd": "a", "ps1": "A> "},
            {"pause": 1.0},
            {"cmd": "b", "ps1": "B> "},
        ]))
        # Events: A>(0), a(0), \r\n(0), B>(0), b(1.0), \r\n(1.0), B>(1.0)
        self.assertEqual(events[3][2], "B> ")

    def test_trailing_ps1_fallback_to_own_ps1(self):
        events = self._run(self._demo([{"cmd": "a", "ps1": "X> "}]))
        self.assertEqual(events[-1][2], "X> ")

    def test_trailing_ps1_for_prompt_uses_preceding_cmd_ps1(self):
        events = self._run(self._demo([
            {"cmd": "a", "ps1": "C> ", "output": "Pick:"},
            {"prompt": "x"},
        ]))
        x_idx = next(i for i, e in enumerate(events) if e[2] == "x")
        trailing = events[x_idx + 2]  # x, \r\n, trailing PS1
        self.assertEqual(trailing[2], "C> ")

    def test_demo_with_no_cmd_or_comment_emits_no_ps1(self):
        events = self._run(self._demo([{"pause": 1.0}]))
        self.assertEqual(len(events), 0)

    def test_demo_start_ps1_before_pause(self):
        # Demo starts with pause: PS1 at t=0 before pause advances t
        events = self._run(self._demo([{"pause": 2.0}, {"cmd": "a"}]))
        self.assertAlmostEqual(events[0][0], 0.0, places=5)
        self.assertEqual(events[0][2], "$ ")
        a_char = next(e for e in events if e[2] == "a")
        self.assertAlmostEqual(a_char[0], 2.0, places=5)

    def test_clear_before_demo_start_ps1_same_timestamp(self):
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        events = self._run(demos, **{"between_demos": {"delay": 1.0, "clear": True}})
        clear_idx = next(i for i, e in enumerate(events) if e[2] == "\x1b[2J\x1b[H")
        ps1_after = events[clear_idx + 1]
        self.assertEqual(ps1_after[2], "$ ")
        self.assertEqual(events[clear_idx][0], ps1_after[0])

    def test_demo_start_ps1_after_delay_no_clear(self):
        demos = [
            {"name": "d1", "steps": [{"cmd": "a"}]},
            {"name": "d2", "steps": [{"cmd": "b"}]},
        ]
        events = self._run(demos, **{"between_demos": {"delay": 2.0, "clear": False}})
        ps1_events = [e for e in events if e[2] == "$ "]
        # demo1: start PS1(0), trailing(0); demo2: start PS1(2.0), trailing(2.0)
        self.assertAlmostEqual(ps1_events[2][0], 2.0, places=5)

    def test_last_step_of_last_demo_emits_trailing_ps1(self):
        events = self._run(self._demo([{"cmd": "a"}]))
        self.assertEqual(events[-1][2], "$ ")

    def test_trailing_ps1_after_comment(self):
        events = self._run(self._demo([{"comment": "hi"}]))
        # trailing PS1 is the last event, at same timestamp as \r\n
        self.assertEqual(events[-1][2], "$ ")
        crlf = next(e for e in events if e[2] == "\r\n")
        self.assertEqual(events[-1][0], crlf[0])


class TestCommentStep(unittest.TestCase):
    COLOR = "\x1b[90m"
    RESET = "\x1b[0m"

    def _run(self, steps, **overrides):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.1, "post_cmd_delay": 1.0,
               "between_demos": {"delay": 0.0, "clear": False},
               "comment_color": self.COLOR}
        cfg.update(overrides)
        demos = [{"name": "d", "steps": steps}]
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        generate.generate(cfg, demos, out)
        _, events = _parse_cast(out)
        os.unlink(out)
        return events

    def test_comment_color_emitted_atomically_before_text(self):
        events = self._run([{"comment": "hi"}])
        # Events: demo-start PS1(0), color(0), #(0.1), space(0.2), h(0.3), i(0.4), reset(0.4), \r\n(0.4), trailing PS1(0.4)
        self.assertEqual(events[1][2], self.COLOR)
        self.assertAlmostEqual(events[1][0], 0.0, places=5)  # zero delta

    def test_hash_space_typed_at_typing_delay(self):
        events = self._run([{"comment": "hi"}])
        self.assertEqual(events[2][2], "#")
        self.assertAlmostEqual(events[2][0], 0.1, places=5)
        self.assertEqual(events[3][2], " ")
        self.assertAlmostEqual(events[3][0], 0.2, places=5)

    def test_comment_text_typed_after_hash_space(self):
        events = self._run([{"comment": "hi"}])
        self.assertEqual(events[4][2], "h")
        self.assertEqual(events[5][2], "i")

    def test_reset_emitted_after_last_char_zero_delta(self):
        events = self._run([{"comment": "hi"}])
        i_ts = events[5][0]   # last char "i"
        self.assertEqual(events[6][2], self.RESET)
        self.assertEqual(events[6][0], i_ts)

    def test_crlf_after_reset_same_timestamp(self):
        events = self._run([{"comment": "hi"}])
        self.assertEqual(events[7][2], "\r\n")
        self.assertEqual(events[7][0], events[6][0])

    def test_trailing_ps1_after_crlf_same_timestamp(self):
        events = self._run([{"comment": "hi"}])
        self.assertEqual(events[8][2], "$ ")
        self.assertEqual(events[8][0], events[7][0])

    def test_post_cmd_delay_after_comment(self):
        # Two comments: second color appears at t=1.0 (after post_cmd_delay)
        events = self._run([{"comment": "a"}, {"comment": "b"}],
                           typing_delay=0.0, post_cmd_delay=1.0)
        color_events = [e for e in events if e[2] == self.COLOR]
        self.assertAlmostEqual(color_events[1][0], 1.0, places=5)

    def test_output_field_silently_ignored(self):
        events = self._run([{"comment": "hi", "output": "ignored"}])
        texts = [e[2] for e in events]
        self.assertNotIn("ignored\r\n", texts)
        self.assertNotIn("ignored", texts)

    def test_comment_ps1_override_used_for_trailing_ps1(self):
        events = self._run([{"comment": "hi", "ps1": "X> "}])
        self.assertEqual(events[-1][2], "X> ")

    def test_empty_comment_types_hash_space_only(self):
        # comment: "" → types "# " (2 chars), no extra text
        events = self._run([{"comment": ""}], typing_delay=0.1)
        # Events: demo-start PS1(0), color(0), #(0.1), space(0.2), reset(0.2), \r\n(0.2), trailing PS1(0.2)
        texts = [e[2] for e in events]
        self.assertEqual(texts[2], "#")
        self.assertEqual(texts[3], " ")
        self.assertEqual(texts[4], self.RESET)
        self.assertEqual(len(texts), 7)

    def test_missing_comment_color_raises_key_error(self):
        cfg = {"width": 80, "height": 24, "title": "t", "ps1": "$ ",
               "typing_delay": 0.1, "post_cmd_delay": 1.0,
               "between_demos": {"delay": 0.0, "clear": False}}
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            out = f.name
        try:
            with self.assertRaises(KeyError):
                generate.generate(cfg, [{"name": "d", "steps": [{"cmd": "a"}]}], out)
        finally:
            os.unlink(out)


if __name__ == "__main__":
    unittest.main()
