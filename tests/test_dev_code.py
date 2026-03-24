import argparse
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Load src/dev_code.py as a module
spec = importlib.util.spec_from_file_location(
    "dev_code",
    os.path.join(os.path.dirname(__file__), "..", "src", "dev_code.py"),
)
dev_code = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_code)
sys.modules["dev_code"] = dev_code


class TestSmoke(unittest.TestCase):
    def test_existing_helpers_present(self):
        assert callable(dev_code.is_wsl)
        assert callable(dev_code.wsl_to_windows)
        assert callable(dev_code.build_devcontainer_uri)
        assert callable(dev_code.resolve_template_dir)
        assert callable(dev_code.resolve_template)

    def test_banner_is_string(self):
        assert isinstance(dev_code.BANNER, str)
        assert len(dev_code.BANNER) > 0


class TestParseDevcontainerJson(unittest.TestCase):
    def _write_json(self, content: str) -> str:
        """Write content to a temp file, return path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write(content)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_fallback_plain_json(self):
        path = self._write_json('{"name": "Dev"}')
        data, cli_used = dev_code.parse_devcontainer_json(path)
        self.assertEqual(data["name"], "Dev")
        self.assertFalse(cli_used)

    def test_fallback_strips_line_comments(self):
        content = '// top comment\n{"name": "Dev"}'
        path = self._write_json(content)
        data, cli_used = dev_code.parse_devcontainer_json(path)
        self.assertEqual(data["name"], "Dev")

    def test_fallback_strips_trailing_commas(self):
        content = '{"features": {"uv": {},}}'
        path = self._write_json(content)
        data, _ = dev_code.parse_devcontainer_json(path)
        self.assertIn("features", data)

    def test_fallback_preserves_url_strings(self):
        content = '{"image": "https://example.com/image"}'
        path = self._write_json(content)
        data, _ = dev_code.parse_devcontainer_json(path)
        self.assertEqual(data["image"], "https://example.com/image")

    def test_fallback_parse_error_exits(self):
        path = self._write_json("not json at all {{{")
        with self.assertRaises(SystemExit):
            dev_code.parse_devcontainer_json(path)

    def test_devcontainer_cli_used_when_available(self):
        config = {"customizations": {"dev-code": []}}
        mock_result = MagicMock(returncode=0, stdout=json.dumps(config))
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/devcontainer" if x == "devcontainer" else None):
            with patch("subprocess.run", return_value=mock_result):
                data, cli_used = dev_code.parse_devcontainer_json("/fake/devcontainer.json")
        self.assertTrue(cli_used)
        self.assertIn("customizations", data)

    def test_jq_used_when_devcontainer_unavailable(self):
        config = {"name": "test"}
        mock_result = MagicMock(returncode=0, stdout=json.dumps(config))
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/jq" if x == "jq" else None):
            with patch("subprocess.run", return_value=mock_result):
                path = self._write_json('{"name": "test"}')
                data, cli_used = dev_code.parse_devcontainer_json(path)
        self.assertFalse(cli_used)
        self.assertEqual(data["name"], "test")


class TestWaitForContainer(unittest.TestCase):
    def _make_docker_result(self, output: str, returncode: int = 0):
        return MagicMock(returncode=returncode, stdout=output)

    def test_returns_container_id_on_success(self):
        results = [
            self._make_docker_result(""),        # first poll: nothing
            self._make_docker_result("abc123\n"), # second poll: found
        ]
        with patch("subprocess.run", side_effect=results):
            with patch("time.sleep"):
                with patch("time.time", side_effect=[0, 1, 2, 3]):
                    cid = dev_code.wait_for_container("/fake.json", "/myproject", timeout=60)
        self.assertEqual(cid, "abc123")

    def test_times_out_and_exits(self):
        # Always return empty
        with patch("subprocess.run", return_value=self._make_docker_result("")):
            with patch("time.sleep"):
                # time.time() exceeds deadline immediately after first check
                with patch("time.time", side_effect=[0, 0, 61, 61, 61]):
                    with self.assertRaises(SystemExit):
                        dev_code.wait_for_container("/fake.json", "/myproject", timeout=60)

    def test_timeout_message_includes_label_value(self):
        with patch("subprocess.run", return_value=self._make_docker_result("")):
            with patch("time.sleep"):
                with patch("time.time", side_effect=[0, 0, 61, 61, 61]):
                    with self.assertLogs("dev-code", level="WARNING") as cm:
                        with self.assertRaises(SystemExit):
                            dev_code.wait_for_container("/fake.json", "/my/project", timeout=60)
        self.assertTrue(any("/my/project" in line for line in cm.output))

    def test_warns_on_multiple_containers(self):
        result = self._make_docker_result("abc123\ndef456\n")
        with patch("subprocess.run", return_value=result):
            with patch("time.sleep"):
                with patch("time.time", side_effect=[0, 1, 1, 1, 1]):
                    with self.assertLogs("dev-code", level="WARNING") as cm:
                        cid = dev_code.wait_for_container("/fake.json", "/myproject", timeout=60)
        self.assertEqual(cid, "abc123")
        self.assertTrue(any("multiple" in line.lower() for line in cm.output))

    def test_wsl_converts_path(self):
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            return self._make_docker_result("abc123")
        with patch("subprocess.run", side_effect=fake_run):
            with patch("time.sleep"):
                with patch("time.time", side_effect=[0, 1]):
                    with patch.object(dev_code, "is_wsl", return_value=True):
                        with patch.object(dev_code, "wsl_to_windows", return_value=r"C:\myproject"):
                            dev_code.wait_for_container("/fake.json", "/myproject", timeout=60)
        label_filter = next(a for a in calls[0] if "devcontainer.local_folder" in a)
        self.assertIn(r"C:\myproject", label_filter)


class TestResolveTemplateDir(unittest.TestCase):
    def test_uses_env_override(self):
        with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": "/custom/templates"}):
            self.assertEqual(dev_code.resolve_template_dir(), "/custom/templates")

    def test_xdg_data_home(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("DEVCODE_TEMPLATE_DIR", "XDG_DATA_HOME")}
        with patch.dict(os.environ, {**env, "XDG_DATA_HOME": "/xdg"}, clear=True):
            result = dev_code.resolve_template_dir()
        self.assertEqual(os.path.normpath(result), os.path.normpath("/xdg/dev-code/templates"))

    def test_default_xdg(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("DEVCODE_TEMPLATE_DIR", "XDG_DATA_HOME")}
        with patch.dict(os.environ, env, clear=True):
            result = dev_code.resolve_template_dir()
        self.assertIn(os.path.join(".local", "share", "dev-code", "templates"), result)


class TestGetBuiltinTemplatePath(unittest.TestCase):
    def test_returns_path_for_known_builtin(self):
        with tempfile.TemporaryDirectory() as d:
            builtin_dir = os.path.join(d, "dev_code_templates", "dev-code")
            os.makedirs(builtin_dir)
            with patch.object(dev_code, "__file__", os.path.join(d, "dev_code.py")):
                result = dev_code.get_builtin_template_path("dev-code")
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("dev-code"))

    def test_returns_none_for_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(dev_code, "__file__", os.path.join(d, "dev_code.py")):
                result = dev_code.get_builtin_template_path("nonexistent")
        self.assertIsNone(result)


class TestResolveTemplate(unittest.TestCase):
    def test_finds_user_template(self):
        with tempfile.TemporaryDirectory() as d:
            tpath = os.path.join(d, "mytemplate", ".devcontainer")
            os.makedirs(tpath)
            cfg = os.path.join(tpath, "devcontainer.json")
            open(cfg, "w").close()
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": d}):
                result = dev_code.resolve_template("mytemplate")
        self.assertEqual(result, cfg)

    def test_falls_back_to_builtin(self):
        with tempfile.TemporaryDirectory() as user_dir:
            with tempfile.TemporaryDirectory() as pkg_dir:
                builtin = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
                os.makedirs(builtin)
                cfg = os.path.join(builtin, "devcontainer.json")
                open(cfg, "w").close()
                with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                    with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                        result = dev_code.resolve_template("dev-code")
        self.assertEqual(result, cfg)

    def test_exits_when_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": d}):
                with patch.object(dev_code, "__file__", os.path.join(d, "dev_code.py")):
                    with self.assertRaises(SystemExit):
                        dev_code.resolve_template("no-such-template")


class TestMain(unittest.TestCase):
    def setUp(self):
        # Create a minimal template dir for tests
        self.tmpdir = tempfile.mkdtemp()
        tpl = os.path.join(self.tmpdir, "claude", ".devcontainer")
        os.makedirs(tpl)
        open(os.path.join(tpl, "devcontainer.json"), "w").close()
        self.env_patch = patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": self.tmpdir})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_projectpath_root_exits(self):
        with patch.object(sys, "argv", ["dev-code", "open", "claude", "/"]):
            with self.assertRaises(SystemExit):
                dev_code.main()

    def test_code_not_on_path_exits(self):
        with patch.object(sys, "argv", ["dev-code", "open", "claude", "/myproject"]):
            with patch("shutil.which", return_value=None):
                with self.assertRaises(SystemExit):
                    dev_code.main()

    def test_launches_vscode_with_folder_uri(self):
        launched = []
        def fake_popen(cmd, **kw):
            launched.append(cmd)
            return MagicMock()

        with patch.object(sys, "argv", ["dev-code", "open", "claude", "/myproject"]):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    with patch.object(dev_code, "run_post_launch"):
                        dev_code.main()

        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0][0], "code")
        self.assertEqual(launched[0][1], "--folder-uri")
        self.assertIn("vscode-remote://dev-container+", launched[0][2])

    def test_default_container_folder(self):
        launched = []
        def fake_popen(cmd, **kw):
            launched.append(cmd)
            return MagicMock()

        with patch.object(sys, "argv", ["dev-code", "open", "claude", "/myproject"]):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    with patch.object(dev_code, "run_post_launch"):
                        dev_code.main()

        self.assertIn("/workspaces/myproject", launched[0][2])

    def test_custom_container_folder(self):
        launched = []
        def fake_popen(cmd, **kw):
            launched.append(cmd)
            return MagicMock()

        with patch.object(sys, "argv", ["dev-code", "open", "claude", "/myproject", "--container-folder", "/workspace/custom"]):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    with patch.object(dev_code, "run_post_launch"):
                        dev_code.main()

        self.assertIn("/workspace/custom", launched[0][2])

    def test_timeout_passed_to_run_post_launch(self):
        captured = {}
        def fake_rpl(config_file, project_path, timeout):
            captured["timeout"] = timeout

        with patch.object(sys, "argv", ["dev-code", "open", "claude", "/myproject", "--timeout", "42"]):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch("subprocess.Popen", return_value=MagicMock()):
                    with patch.object(dev_code, "run_post_launch", side_effect=fake_rpl):
                        dev_code.main()

        self.assertEqual(captured["timeout"], 42)

    def test_default_timeout_is_300(self):
        captured = {}
        def fake_rpl(config_file, project_path, timeout):
            captured["timeout"] = timeout

        with patch.object(sys, "argv", ["dev-code", "open", "claude", "/myproject"]):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch("subprocess.Popen", return_value=MagicMock()):
                    with patch.object(dev_code, "run_post_launch", side_effect=fake_rpl):
                        dev_code.main()

        self.assertEqual(captured["timeout"], 300)


class TestListDirChildren(unittest.TestCase):
    def test_returns_absolute_paths(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "file.txt"), "w").close()
            result = dev_code._list_dir_children(d)
        self.assertEqual(result, [os.path.join(d, "file.txt")])

    def test_includes_dot_files(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, ".hidden"), "w").close()
            result = dev_code._list_dir_children(d)
        self.assertIn(os.path.join(d, ".hidden"), result)

    def test_empty_dir_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            result = dev_code._list_dir_children(d)
        self.assertEqual(result, [])


class TestRunPostLaunch(unittest.TestCase):
    def _make_config(self, entries):
        """Return a devcontainer.json dict with given customizations.dev-code.cp entries."""
        return {"customizations": {"dev-code": {"cp": entries}}}

    def _run(self, entries, env=None, source_exists=True, container_id="cid123",
             target_exists=False, cli_used=False, extra_patches=None):
        """Helper: run run_post_launch with mocked dependencies."""
        config = self._make_config(entries)
        patches = {
            "parse_devcontainer_json": MagicMock(return_value=(config, cli_used)),
            "wait_for_container": MagicMock(return_value=container_id),
        }
        if extra_patches:
            patches.update(extra_patches)

        docker_calls = []
        def fake_run(cmd, **kw):
            docker_calls.append(cmd)
            # Override check: test -e returns 1 (not exists) by default
            if "test" in cmd and "-e" in cmd:
                return MagicMock(returncode=1 if not target_exists else 0)
            return MagicMock(returncode=0)

        env_context = patch.dict(os.environ, env or {})
        src_exists = patch("os.path.exists", return_value=source_exists)
        src_isdir = patch("os.path.isdir", return_value=True)

        with env_context, src_exists, src_isdir:
            with patch.object(dev_code, "parse_devcontainer_json", patches["parse_devcontainer_json"]):
                with patch.object(dev_code, "wait_for_container", patches["wait_for_container"]):
                    with patch("subprocess.run", side_effect=fake_run):
                        dev_code.run_post_launch("/fake/devcontainer.json", "/myproject", 300)

        return docker_calls

    def test_no_entries_skips_docker(self):
        calls = self._run([])
        self.assertEqual(calls, [])

    def test_absent_key_skips_docker(self):
        config = {"customizations": {}}
        with patch.object(dev_code, "parse_devcontainer_json",
                          return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container") as mock_wait:
                dev_code.run_post_launch("/fake.json", "/proj", 300)
        mock_wait.assert_not_called()

    def test_none_value_skips_docker(self):
        # dev-code key present but null — not a dict, skip silently
        config = {"customizations": {"dev-code": None}}
        with patch.object(dev_code, "parse_devcontainer_json",
                          return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container") as mock_wait:
                dev_code.run_post_launch("/fake.json", "/proj", 300)
        mock_wait.assert_not_called()

    def test_non_dict_value_exits(self):
        # dev-code key exists but is not a dict (e.g. old flat-list format)
        config = {"customizations": {"dev-code": "bad"}}
        with patch.object(dev_code, "parse_devcontainer_json",
                          return_value=(config, False)):
            with self.assertRaises(SystemExit):
                dev_code.run_post_launch("/fake.json", "/proj", 300)

    def test_absent_cp_key_skips_docker(self):
        # dev-code is a dict but has no cp key — silent no-op
        config = {"customizations": {"dev-code": {}}}
        with patch.object(dev_code, "parse_devcontainer_json",
                          return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container") as mock_wait:
                dev_code.run_post_launch("/fake.json", "/proj", 300)
        mock_wait.assert_not_called()

    def test_cp_non_list_exits(self):
        # cp key exists but is not a list
        config = {"customizations": {"dev-code": {"cp": "oops"}}}
        with patch.object(dev_code, "parse_devcontainer_json",
                          return_value=(config, False)):
            with self.assertRaises(SystemExit):
                dev_code.run_post_launch("/fake.json", "/proj", 300)

    def test_missing_source_warns_and_skips(self):
        entries = [{"target": "/home/vscode/.claude"}]
        with self.assertLogs("dev-code", level="WARNING") as cm:
            calls = self._run(entries)
        self.assertTrue(any("source" in line.lower() for line in cm.output))
        docker_cp_calls = [c for c in calls if "cp" in c]
        self.assertEqual(docker_cp_calls, [])

    def test_missing_target_warns_and_skips(self):
        entries = [{"source": "/home/.claude"}]
        with self.assertLogs("dev-code", level="WARNING") as cm:
            calls = self._run(entries)
        self.assertTrue(any("target" in line.lower() for line in cm.output))

    def test_override_false_skips_when_target_exists(self):
        entries = [{"source": "/src", "target": "/tgt", "override": False}]
        calls = self._run(entries, target_exists=True)
        docker_cp_calls = [c for c in calls if "cp" in c]
        self.assertEqual(docker_cp_calls, [])

    def test_override_false_copies_when_target_absent(self):
        entries = [{"source": "/src", "target": "/tgt", "override": False}]
        calls = self._run(entries, target_exists=False)
        docker_cp_calls = [c for c in calls if "cp" in c]
        self.assertEqual(len(docker_cp_calls), 1)

    def test_override_true_always_copies(self):
        entries = [{"source": "/src", "target": "/tgt", "override": True}]
        calls = self._run(entries, target_exists=True)
        docker_cp_calls = [c for c in calls if "cp" in c]
        self.assertEqual(len(docker_cp_calls), 1)

    def test_capital_Override_warns(self):
        """Capital-O 'Override' is an unknown field and triggers the unknown-field warning."""
        entries = [{"source": "/src", "target": "/tgt", "Override": True}]
        with self.assertLogs("dev-code", level="WARNING") as cm:
            self._run(entries)
        self.assertTrue(any("Override" in line for line in cm.output))

    def test_unknown_field_warns(self):
        """Any field not in the known schema triggers a warning."""
        entries = [{"source": "/src", "target": "/tgt", "typo_field": "bad", "override": True}]
        with self.assertLogs("dev-code", level="WARNING") as cm:
            self._run(entries)
        self.assertTrue(any("typo_field" in line for line in cm.output))

    def test_unknown_field_warns_once_for_dir_expansion(self):
        """Unknown-field warning fires once for the parent entry, not once per expanded child."""
        entries = [{"source": "/src/dotfiles/.", "target": "/home/user/", "bad_key": "x", "override": True}]
        config = self._make_config(entries)
        children = ["/src/dotfiles/.bashrc", "/src/dotfiles/.zshrc"]
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=True):
                            with patch.object(dev_code, "_list_dir_children", return_value=children):
                                with self.assertLogs("dev-code", level="WARNING") as cm:
                                    dev_code.run_post_launch("/fake.json", "/proj", 300)
        bad_key_warnings = [line for line in cm.output if "bad_key" in line]
        self.assertEqual(len(bad_key_warnings), 1, f"Expected 1 warning, got: {bad_key_warnings}")

    def test_chown_called_when_owner_and_group_present(self):
        entries = [{"source": "/src", "target": "/tgt",
                    "owner": "vscode", "group": "vscode", "override": True}]
        calls = self._run(entries)
        chown_calls = [c for c in calls if "chown" in c]
        self.assertEqual(len(chown_calls), 1)
        self.assertIn("vscode:vscode", chown_calls[0])

    def test_chown_skipped_when_group_missing(self):
        entries = [{"source": "/src", "target": "/tgt", "owner": "vscode", "override": True}]
        calls = self._run(entries)
        chown_calls = [c for c in calls if "chown" in c]
        self.assertEqual(chown_calls, [])

    def test_chmod_called_when_permissions_present(self):
        entries = [{"source": "/src", "target": "/tgt",
                    "permissions": "0755", "override": True}]
        calls = self._run(entries)
        chmod_calls = [c for c in calls if "chmod" in c]
        self.assertEqual(len(chmod_calls), 1)
        self.assertIn("0755", chmod_calls[0])

    def test_env_var_substitution_in_source(self):
        entries = [{"source": "${localEnv:HOME}/.claude", "target": "/tgt", "override": True}]
        calls = self._run(entries, env={"HOME": "/home/testuser"})
        cp_calls = [c for c in calls if "cp" in c]
        # source should be resolved before reaching docker cp
        self.assertEqual(len(cp_calls), 1)

    def test_env_var_missing_warns_and_skips(self):
        entries = [{"source": "${localEnv:MISSING_VAR}/.claude", "target": "/tgt"}]
        env = {k: v for k, v in os.environ.items() if k != "MISSING_VAR"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertLogs("dev-code", level="WARNING"):
                calls = self._run(entries)
        cp_calls = [c for c in calls if "cp" in c]
        self.assertEqual(cp_calls, [])

    def test_source_not_found_warns_and_skips(self):
        entries = [{"source": "/nonexistent", "target": "/tgt", "override": True}]
        calls = self._run(entries, source_exists=False)
        cp_calls = [c for c in calls if "cp" in c]
        self.assertEqual(cp_calls, [])

    def test_relative_source_resolved_from_config_dir(self):
        """Relative source paths are resolved relative to config_file's directory."""
        entries = [{"source": "claude-config/settings.json", "target": "/tgt", "override": True}]
        config = self._make_config(entries)
        cp_calls = []
        def fake_run(cmd, **kw):
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "cp":
                cp_calls.append(cmd)
            return MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as config_dir:
            config_file = os.path.join(config_dir, "devcontainer.json")
            with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
                with patch.object(dev_code, "wait_for_container", return_value="cid"):
                    with patch("subprocess.run", side_effect=fake_run):
                        with patch("os.path.exists", return_value=True):
                            with patch("os.path.isdir", return_value=False):
                                dev_code.run_post_launch(config_file, "/proj", 300)
        self.assertEqual(len(cp_calls), 1)
        # Third arg to "docker cp" is the host source path
        resolved_src = cp_calls[0][2]
        expected_dir = os.path.join(config_dir, "claude-config")
        self.assertIn(expected_dir, resolved_src)
        self.assertIn("settings.json", resolved_src)

    def test_absolute_source_not_modified(self):
        """Absolute source paths are used as-is (not prefixed with config_dir)."""
        entries = [{"source": "/absolute/path/file.json", "target": "/tgt", "override": True}]
        config = self._make_config(entries)

        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", return_value=MagicMock(returncode=1)):
                    with patch("os.path.exists", return_value=False):
                        with self.assertLogs("dev-code", level="WARNING") as cm:
                            dev_code.run_post_launch("/fake/devcontainer.json", "/proj", 300)
        # source not found — warns with the original absolute path unchanged
        self.assertTrue(any("/absolute/path/file.json" in line for line in cm.output))
        # must not be prefixed with config_dir
        matching = [line for line in cm.output if "/absolute/path/file.json" in line]
        self.assertTrue(all("/fake" not in line.split("/absolute")[0] for line in matching))

    def test_file_source_docker_cp_uses_exact_source_path(self):
        """docker cp must receive the actual file path, not a tmpdir."""
        entries = [{"source": "/src/file.txt", "target": "/home/user/", "override": True}]
        config = self._make_config(entries)
        cp_calls = []
        def fake_run(cmd, **kw):
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "cp":
                cp_calls.append(cmd)
            return MagicMock(returncode=0)
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            dev_code.run_post_launch("/fake.json", "/proj", 300)
        self.assertEqual(len(cp_calls), 1)
        self.assertIn("/src/file.txt", cp_calls[0])
        # Must not use a tmpdir
        self.assertNotIn("/tmp", " ".join(cp_calls[0]))

    def test_file_source_mkdir_creates_parent_not_target(self):
        """For file source + non-slash target, mkdir -p dirname(target), not target itself."""
        entries = [{"source": "/src/file.txt", "target": "/home/user/config", "override": True}]
        config = self._make_config(entries)
        mkdir_args = []
        def fake_run(cmd, **kw):
            if "mkdir" in cmd:
                mkdir_args.append(cmd)
            return MagicMock(returncode=0)
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            dev_code.run_post_launch("/fake.json", "/proj", 300)
        self.assertTrue(any("/home/user" in str(c) and "config" not in str(c)
                            for c in mkdir_args),
                        f"Expected mkdir on /home/user, got: {mkdir_args}")

    def test_override_check_uses_effective_path_not_raw_target(self):
        """For trailing-slash target, override check is against target/basename, not target/."""
        entries = [{"source": "/src/file.txt", "target": "/home/user/", "override": False}]
        config = self._make_config(entries)
        tested_paths = []
        cp_calls = []
        def fake_run(cmd, **kw):
            if "test" in cmd and "-e" in cmd:
                tested_paths.append(cmd[-1])
                # /home/user/ exists, /home/user/file.txt does not
                return MagicMock(returncode=0 if cmd[-1].rstrip("/") == "/home/user" else 1)
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "cp":
                cp_calls.append(cmd)
            return MagicMock(returncode=0)
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            dev_code.run_post_launch("/fake.json", "/proj", 300)
        # Override check must test effective path (file.txt inside target dir), not raw target
        self.assertTrue(any("file.txt" in p for p in tested_paths),
                        f"Expected effective path checked, got: {tested_paths}")
        # Since effective path doesn't exist, copy must proceed
        self.assertEqual(len(cp_calls), 1)

    def test_dir_contents_source_expands_to_children(self):
        """source ending with /. expands to individual child entries."""
        entries = [{"source": "/src/dotfiles/.", "target": "/home/user/", "override": True}]
        config = self._make_config(entries)
        cp_calls = []
        def fake_run(cmd, **kw):
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "cp":
                cp_calls.append(cmd)
            return MagicMock(returncode=0)
        children = ["/src/dotfiles/.bashrc", "/src/dotfiles/.gitconfig"]
        def isdir_side_effect(path):
            return path == "/src/dotfiles"
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", side_effect=isdir_side_effect):
                            with patch.object(dev_code, "_list_dir_children",
                                              return_value=children):
                                dev_code.run_post_launch("/fake.json", "/proj", 300)
        # One docker cp per child
        self.assertEqual(len(cp_calls), 2)
        cp_sources = [c[2] for c in cp_calls]  # third arg is host source
        self.assertIn("/src/dotfiles/.bashrc", cp_sources)
        self.assertIn("/src/dotfiles/.gitconfig", cp_sources)

    def test_dir_contents_relative_source_expands_to_children(self):
        """Relative source ending with /. expands just like an absolute source."""
        entries = [{"source": "dotfiles/.", "target": "/home/user/", "override": True}]
        config = self._make_config(entries)
        cp_calls = []
        def fake_run(cmd, **kw):
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "cp":
                cp_calls.append(cmd)
            return MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as config_dir:
            config_file = os.path.join(config_dir, "fake.json")
            dotfiles_dir = os.path.join(config_dir, "dotfiles")
            children = [
                os.path.join(dotfiles_dir, ".bashrc"),
                os.path.join(dotfiles_dir, ".gitconfig"),
            ]
            def isdir_side_effect(path):
                return os.path.normcase(path) == os.path.normcase(dotfiles_dir)
            with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
                with patch.object(dev_code, "wait_for_container", return_value="cid"):
                    with patch("subprocess.run", side_effect=fake_run):
                        with patch("os.path.exists", return_value=True):
                            with patch("os.path.isdir", side_effect=isdir_side_effect):
                                with patch.object(dev_code, "_list_dir_children",
                                                  return_value=children):
                                    dev_code.run_post_launch(config_file, "/proj", 300)
        # Must expand to one docker cp per child, NOT one cp of the whole dir
        self.assertEqual(len(cp_calls), 2)
        cp_sources = [c[2] for c in cp_calls]
        self.assertIn(os.path.join(dotfiles_dir, ".bashrc"), cp_sources)
        self.assertIn(os.path.join(dotfiles_dir, ".gitconfig"), cp_sources)

    def test_dir_contents_source_without_trailing_slash_target_warns(self):
        """source/. with target not ending in / produces a warning."""
        entries = [{"source": "/src/dotfiles/.", "target": "/home/user", "override": True}]
        config = self._make_config(entries)
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                    with patch("os.path.exists", return_value=True):
                        with patch.object(dev_code, "_list_dir_children", return_value=[]):
                            with self.assertLogs("dev-code", level="WARNING") as cm:
                                dev_code.run_post_launch("/fake.json", "/proj", 300)
        self.assertTrue(len(cm.output) > 0, "Expected a warning for missing trailing slash")

    def test_dir_contents_empty_dir_is_silent_noop(self):
        """source/. with empty dir produces no copies and no warnings."""
        entries = [{"source": "/src/dotfiles/.", "target": "/home/user/", "override": True}]
        config = self._make_config(entries)
        cp_calls = []
        def fake_run(cmd, **kw):
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "cp":
                cp_calls.append(cmd)
            return MagicMock(returncode=0)
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=True):
                            with patch.object(dev_code, "_list_dir_children", return_value=[]):
                                dev_code.run_post_launch("/fake.json", "/proj", 300)
        self.assertEqual(cp_calls, [])

    def test_chown_skipped_when_cp_fails(self):
        """chown must not run if docker cp returned non-zero."""
        entries = [{"source": "/src", "target": "/tgt",
                    "owner": "vscode", "group": "vscode", "override": True}]
        config = self._make_config(entries)
        def fake_run(cmd, **kw):
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "cp":
                return MagicMock(returncode=1)  # cp fails
            return MagicMock(returncode=0)
        with patch.object(dev_code, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(dev_code, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            calls = []
                            original_run = subprocess.run
                            def tracking_run(cmd, **kw):
                                calls.append(cmd)
                                return fake_run(cmd, **kw)
                            with patch("subprocess.run", side_effect=tracking_run):
                                dev_code.run_post_launch("/fake.json", "/proj", 300)
        chown_calls = [c for c in calls if "chown" in c]
        self.assertEqual(chown_calls, [], "chown must not run after failed cp")


class TestCmdInit(unittest.TestCase):
    def _run_init(self, template_dir):
        with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": template_dir}):
            args = argparse.Namespace(subcommand="init", verbose=False)
            dev_code.cmd_init(args)

    def test_copies_builtin_to_user_dir(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            # Set up fake built-in
            builtin = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
            os.makedirs(builtin)
            open(os.path.join(builtin, "devcontainer.json"), "w").close()
            with tempfile.TemporaryDirectory() as user_dir:
                dest_base = os.path.join(user_dir, "dev-code")
                with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                    self._run_init(user_dir)
                self.assertTrue(os.path.isdir(dest_base))
                self.assertTrue(os.path.exists(os.path.join(dest_base, ".devcontainer", "devcontainer.json")))

    def test_skips_if_already_exists(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            builtin = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
            os.makedirs(builtin)
            open(os.path.join(builtin, "devcontainer.json"), "w").close()
            with tempfile.TemporaryDirectory() as user_dir:
                existing = os.path.join(user_dir, "dev-code")
                os.makedirs(existing)
                with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                    captured = []
                    with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0])):
                        self._run_init(user_dir)
                self.assertTrue(any("Skipped" in s for s in captured))

    def test_exits_when_builtin_not_found(self):
        with tempfile.TemporaryDirectory() as user_dir:
            with patch.object(dev_code, "get_builtin_template_path", return_value=None):
                with self.assertRaises(SystemExit):
                    self._run_init(user_dir)

    def test_creates_user_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            builtin = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
            os.makedirs(builtin)
            open(os.path.join(builtin, "devcontainer.json"), "w").close()
            with tempfile.TemporaryDirectory() as base:
                user_dir = os.path.join(base, "new", "nested", "dir")  # doesn't exist yet
                with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                    self._run_init(user_dir)
                self.assertTrue(os.path.isdir(user_dir))
                self.assertTrue(os.path.isdir(os.path.join(user_dir, "dev-code")))


class TestCmdList(unittest.TestCase):
    def _make_template(self, base_dir, name):
        p = os.path.join(base_dir, name, ".devcontainer")
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, "devcontainer.json"), "w").close()

    def _run_list(self, user_dir, pkg_dir, long=False):
        lines = []
        with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
            with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                args = MagicMock(subcommand="list", verbose=False, long=long)
                with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                    dev_code.cmd_list(args)
        return lines

    def test_lists_user_templates(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            with tempfile.TemporaryDirectory() as user_dir:
                self._make_template(user_dir, "mytemplate")
                lines = self._run_list(user_dir, pkg_dir)
        self.assertIn("mytemplate", lines)

    def test_lists_builtins(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            b = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
            os.makedirs(b)
            open(os.path.join(b, "devcontainer.json"), "w").close()
            with tempfile.TemporaryDirectory() as user_dir:
                lines = self._run_list(user_dir, pkg_dir)
        self.assertIn("dev-code", lines)

    def test_long_shows_template_dir(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            with tempfile.TemporaryDirectory() as user_dir:
                self._make_template(user_dir, "mytemplate")
                lines = self._run_list(user_dir, pkg_dir, long=True)
        combined = "\n".join(lines)
        self.assertIn("Template dir:", combined)
        self.assertIn(user_dir, combined)

    def test_no_user_dir_shows_hint(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            nonexistent = os.path.join(pkg_dir, "no-such-dir")
            lines = self._run_list(nonexistent, pkg_dir)
        combined = "\n".join(str(l) for l in lines)
        self.assertIn("dev-code init", combined)


class TestCmdNew(unittest.TestCase):
    def _setup_pkg(self, pkg_dir):
        """Create a fake dev-code built-in in pkg_dir."""
        b = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
        os.makedirs(b)
        open(os.path.join(b, "devcontainer.json"), "w").close()

    def test_creates_template_from_default_base(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            self._setup_pkg(pkg_dir)
            with tempfile.TemporaryDirectory() as user_dir:
                args = MagicMock(subcommand="new", verbose=False,
                                 base=None, edit=False)
                args.name = "myapp"
                with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                    with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                        dev_code.cmd_new(args)
                self.assertTrue(os.path.isdir(os.path.join(user_dir, "myapp")))

    def test_creates_template_from_explicit_base(self):
        with tempfile.TemporaryDirectory() as user_dir:
            # Create a base template in user dir
            base_path = os.path.join(user_dir, "mybase", ".devcontainer")
            os.makedirs(base_path)
            open(os.path.join(base_path, "devcontainer.json"), "w").close()

            args = MagicMock(subcommand="new", verbose=False,
                             base="mybase", edit=False)
            args.name = "myapp"
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                dev_code.cmd_new(args)
            self.assertTrue(os.path.isdir(os.path.join(user_dir, "myapp")))

    def test_exits_if_name_already_exists(self):
        with tempfile.TemporaryDirectory() as user_dir:
            existing = os.path.join(user_dir, "myapp")
            os.makedirs(existing)
            args = MagicMock(subcommand="new", verbose=False,
                             base=None, edit=False)
            args.name = "myapp"
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                with self.assertRaises(SystemExit):
                    dev_code.cmd_new(args)

    def test_exits_if_base_not_found(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            with tempfile.TemporaryDirectory() as user_dir:
                args = MagicMock(subcommand="new", verbose=False,
                                 base="no-such-base", edit=False)
                args.name = "myapp"
                with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                    with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                        with self.assertRaises(SystemExit):
                            dev_code.cmd_new(args)

    def test_edit_flag_calls_cmd_open(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            self._setup_pkg(pkg_dir)
            with tempfile.TemporaryDirectory() as user_dir:
                args = MagicMock(subcommand="new", verbose=False,
                                 base=None, edit=True)
                args.name = "myapp"
                with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                    with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                        with patch.object(dev_code, "cmd_open") as mock_open:
                            dev_code.cmd_new(args)
                mock_open.assert_called_once()
                call_args = mock_open.call_args[0][0]
                self.assertEqual(call_args.template, "myapp")
                self.assertEqual(call_args.projectpath, os.path.join(user_dir, "myapp"))


class TestCmdEdit(unittest.TestCase):
    def test_opens_whole_template_dir(self):
        with tempfile.TemporaryDirectory() as user_dir:
            args = MagicMock(subcommand="edit", verbose=False, template=None)
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                with patch.object(dev_code, "cmd_open") as mock_open:
                    dev_code.cmd_edit(args)
            call_args = mock_open.call_args[0][0]
            self.assertEqual(call_args.projectpath, user_dir)

    def test_opens_specific_template(self):
        with tempfile.TemporaryDirectory() as user_dir:
            tpl = os.path.join(user_dir, "claude", ".devcontainer")
            os.makedirs(tpl)
            open(os.path.join(tpl, "devcontainer.json"), "w").close()

            args = MagicMock(subcommand="edit", verbose=False, template="claude")
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                with patch.object(dev_code, "cmd_open") as mock_open:
                    dev_code.cmd_edit(args)
            call_args = mock_open.call_args[0][0]
            self.assertEqual(call_args.projectpath, os.path.join(user_dir, "claude"))

    def test_exits_if_template_dir_missing_no_arg(self):
        with tempfile.TemporaryDirectory() as base:
            nonexistent = os.path.join(base, "no-such-dir")
            args = MagicMock(subcommand="edit", verbose=False, template=None)
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": nonexistent}):
                with self.assertRaises(SystemExit):
                    dev_code.cmd_edit(args)

    def test_exits_if_named_template_not_found(self):
        with tempfile.TemporaryDirectory() as user_dir:
            args = MagicMock(subcommand="edit", verbose=False, template="no-such")
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                with self.assertRaises(SystemExit):
                    dev_code.cmd_edit(args)


class TestTemplateNameFromConfig(unittest.TestCase):
    def test_extracts_name(self):
        path = "/home/user/.local/share/dev-code/templates/claude/.devcontainer/devcontainer.json"
        self.assertEqual(dev_code._template_name_from_config(path), "claude")

    def test_fallback_on_no_devcontainer(self):
        path = "/some/arbitrary/path/devcontainer.json"
        # Should not raise; returns some string
        result = dev_code._template_name_from_config(path)
        self.assertIsInstance(result, str)


class TestCmdPs(unittest.TestCase):
    def _docker_output(self, rows):
        # rows: (created_at, cid, local_folder, config_file, status)
        return "\n".join("\t".join(r) for r in rows) + "\n" if rows else ""

    def test_lists_containers(self):
        rows = [("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
                 "/home/user/.local/share/dev-code/templates/claude/.devcontainer/devcontainer.json",
                 "Up 2 hours")]
        mock_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=False)
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_ps(args)
        combined = "\n".join(lines)
        self.assertIn("claude", combined)
        self.assertIn("abc123def456", combined)
        self.assertIn("#", combined)   # header row has # column
        self.assertIn("1", combined)   # row 1

    def test_no_containers_message(self):
        mock_result = MagicMock(returncode=0, stdout="")
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=False)
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_ps(args)
        self.assertTrue(any("no running devcontainers" in str(l) for l in lines))

    def test_docker_unavailable_exits(self):
        mock_result = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=False)
            with self.assertRaises(SystemExit):
                dev_code.cmd_ps(args)

    def test_malformed_row_skipped(self):
        # A row with fewer than 4 fields after dropping CreatedAt should be skipped silently
        malformed = "2026-03-24 10:00:00 +0000 UTC\tabc123\t/home/user/myapp"  # only 3 fields after drop
        good = "2026-03-24 11:00:00 +0000 UTC\tbbb222\t/home/user/other\t/some/config\tUp 1 hour"
        stdout = malformed + "\n" + good + "\n"
        mock_result = MagicMock(returncode=0, stdout=stdout)
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=False)
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_ps(args)  # must not raise
        combined = "\n".join(lines)
        self.assertIn("bbb222", combined)  # good row shown
        self.assertNotIn("abc123", combined)  # malformed row skipped

    def test_all_flag_includes_stopped(self):
        rows = [
            ("2026-03-24 09:00:00 +0000 UTC", "aaa111", "/home/user/old",
             "/home/user/.local/share/dev-code/templates/node/.devcontainer/devcontainer.json",
             "Exited (0) 1 hour ago"),
            ("2026-03-24 10:00:00 +0000 UTC", "bbb222", "/home/user/new",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 5 minutes"),
        ]
        mock_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=True, interactive=False)
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_ps(args)
        combined = "\n".join(lines)
        self.assertIn("aaa111", combined)   # stopped container included
        self.assertIn("bbb222", combined)   # running container included

    def test_no_all_flag_excludes_stopped(self):
        rows = [
            ("2026-03-24 09:00:00 +0000 UTC", "aaa111", "/home/user/old",
             "/home/user/.local/share/dev-code/templates/node/.devcontainer/devcontainer.json",
             "Exited (0) 1 hour ago"),
            ("2026-03-24 10:00:00 +0000 UTC", "bbb222", "/home/user/new",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 5 minutes"),
        ]
        mock_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=False)
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_ps(args)
        combined = "\n".join(lines)
        self.assertNotIn("aaa111", combined)   # stopped excluded
        self.assertIn("bbb222", combined)      # running shown

    def test_all_flag_empty_message(self):
        mock_result = MagicMock(returncode=0, stdout="")
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=True, interactive=False)
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_ps(args)
        self.assertTrue(any("no devcontainers" in str(l) and "running" not in str(l) for l in lines))

    def test_interactive_valid_selection_calls_cmd_open(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        inspect_result = MagicMock(
            returncode=0,
            stdout='[{"Type":"bind","Source":"/home/user/myapp","Destination":"/workspaces/myapp"}]'
        )
        with patch("subprocess.run", side_effect=[ls_result, inspect_result]):
            with patch("builtins.input", return_value="1"):
                with patch("dev_code.cmd_open") as mock_open:
                    args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=True)
                    dev_code.cmd_ps(args)
        mock_open.assert_called_once()
        call_args = mock_open.call_args[0][0]
        self.assertEqual(call_args.template, "python")
        self.assertEqual(call_args.projectpath, "/home/user/myapp")
        self.assertEqual(call_args.container_folder, "/workspaces/myapp")
        self.assertEqual(call_args.timeout, 300)
        self.assertFalse(call_args.dry_run)

    def test_interactive_mount_fallback(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        # No matching mount — inspect returns unrelated mount
        inspect_result = MagicMock(
            returncode=0,
            stdout='[{"Type":"bind","Source":"/other/path","Destination":"/workspace/other"}]'
        )
        with patch("subprocess.run", side_effect=[ls_result, inspect_result]):
            with patch("builtins.input", return_value="1"):
                with patch("dev_code.cmd_open") as mock_open:
                    args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=True)
                    dev_code.cmd_ps(args)
        call_args = mock_open.call_args[0][0]
        self.assertEqual(call_args.container_folder, "/workspaces/myapp")  # fallback

    def test_interactive_invalid_selection_exits(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", return_value="99"):
                args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=True)
                with self.assertRaises(SystemExit):
                    dev_code.cmd_ps(args)

    def test_interactive_non_integer_exits(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", return_value="abc"):
                args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=True)
                with self.assertRaises(SystemExit):
                    dev_code.cmd_ps(args)

    def test_interactive_missing_config_label_exits(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "",   # empty config_file label
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", return_value="1"):
                args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=True)
                with self.assertRaises(SystemExit):
                    dev_code.cmd_ps(args)

    def test_interactive_empty_table_no_prompt(self):
        ls_result = MagicMock(returncode=0, stdout="")
        prompted = []
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", side_effect=lambda _: prompted.append(True) or "1"):
                args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=True)
                dev_code.cmd_ps(args)
        self.assertEqual(prompted, [])   # input() was never called

    def test_interactive_all_with_malformed_row_correct_selection(self):
        # With -a -i, a malformed row must not shift the index of a good row
        malformed = "2026-03-24 08:00:00 +0000 UTC\tbad_id\t/home/user/bad"  # 3 fields after drop
        good = "2026-03-24 10:00:00 +0000 UTC\tgood111\t/home/user/myapp\t/path/to/python/config\tExited (0) 1 hour ago"
        stdout = malformed + "\n" + good + "\n"
        ls_result = MagicMock(returncode=0, stdout=stdout)
        inspect_result = MagicMock(returncode=0, stdout='[]')
        with patch("subprocess.run", side_effect=[ls_result, inspect_result]):
            with patch("builtins.input", return_value="1"):
                with patch("dev_code.cmd_open") as mock_open:
                    args = MagicMock(subcommand="ps", verbose=False, all=True, interactive=True)
                    dev_code.cmd_ps(args)
        mock_open.assert_called_once()
        call_args = mock_open.call_args[0][0]
        self.assertEqual(call_args.projectpath, "/home/user/myapp")  # good row, not malformed

    def test_sort_ascending_by_created_at(self):
        # Docker returns rows newest-first; cmd_ps must reorder oldest-first
        rows = [
            ("2026-03-24 12:00:00 +0000 UTC", "newer111", "/home/user/newer",
             "/home/user/.local/share/dev-code/templates/node/.devcontainer/devcontainer.json",
             "Up 1 minute"),
            ("2026-03-24 08:00:00 +0000 UTC", "older222", "/home/user/older",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 4 hours"),
        ]
        mock_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            args = MagicMock(subcommand="ps", verbose=False, all=False, interactive=False)
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_ps(args)
        # Find data rows (skip header)
        data_rows = [l for l in lines if "older222" in l or "newer111" in l]
        self.assertEqual(len(data_rows), 2)
        # older222 must appear before newer111
        idx_older = next(i for i, l in enumerate(lines) if "older222" in l)
        idx_newer = next(i for i, l in enumerate(lines) if "newer111" in l)
        self.assertLess(idx_older, idx_newer)
        # older222 must be row #1
        self.assertIn("1", lines[idx_older])


class TestCmdOpenDryRun(unittest.TestCase):
    def _make_template(self, base_dir, name, cp_entries=None):
        """Create template with optional cp entries in devcontainer.json."""
        tpl = os.path.join(base_dir, name, ".devcontainer")
        os.makedirs(tpl, exist_ok=True)
        data = {}
        if cp_entries is not None:
            data = {"customizations": {"dev-code": {"cp": cp_entries}}}
        with open(os.path.join(tpl, "devcontainer.json"), "w") as f:
            json.dump(data, f)

    def _run_dry_run(self, user_dir, template, projectpath, container_folder=None):
        lines = []
        args = argparse.Namespace(
            template=template,
            projectpath=projectpath,
            container_folder=container_folder,
            timeout=300,
            dry_run=True,
            verbose=False,
        )
        with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                dev_code.cmd_open(args)
        return lines

    def test_prints_config_and_uri(self):
        with tempfile.TemporaryDirectory() as user_dir:
            self._make_template(user_dir, "mytemplate")
            lines = self._run_dry_run(user_dir, "mytemplate", "/myproject")
        combined = "\n".join(lines)
        self.assertIn("Config:", combined)
        self.assertIn("URI:", combined)
        self.assertIn("vscode-remote://dev-container+", combined)

    def test_no_docker_or_vscode_called(self):
        with tempfile.TemporaryDirectory() as user_dir:
            self._make_template(user_dir, "mytemplate")
            with patch("subprocess.Popen") as mock_popen:
                with patch("subprocess.run") as mock_run:
                    self._run_dry_run(user_dir, "mytemplate", "/myproject")
            mock_popen.assert_not_called()
            # subprocess.run may be called by parse_devcontainer_json (devcontainer CLI check),
            # but NOT by wait_for_container or docker cp
            for call in mock_run.call_args_list:
                cmd = call[0][0] if call[0] else call[1].get("args", [])
                self.assertNotIn("docker", str(cmd)[:20] if isinstance(cmd, list) and cmd else "")

    def test_shows_copy_plan(self):
        with tempfile.TemporaryDirectory() as src_dir:
            src_file = os.path.join(src_dir, "myfile")
            open(src_file, "w").close()
            with tempfile.TemporaryDirectory() as user_dir:
                self._make_template(user_dir, "mytemplate", cp_entries=[
                    {"source": src_file, "target": "/home/vscode/myfile"}
                ])
                lines = self._run_dry_run(user_dir, "mytemplate", "/myproject")
        combined = "\n".join(lines)
        self.assertIn("Copy plan:", combined)
        self.assertIn(src_file, combined)

    def test_no_entries_shows_no_copy_entries(self):
        with tempfile.TemporaryDirectory() as user_dir:
            self._make_template(user_dir, "mytemplate", cp_entries=[])
            lines = self._run_dry_run(user_dir, "mytemplate", "/myproject")
        combined = "\n".join(lines)
        self.assertNotIn("Copy plan:", combined)
        self.assertIn("no copy entries", combined)

    def test_unset_env_var_shown_as_placeholder(self):
        env_clean = {k: v for k, v in os.environ.items() if k != "NONEXISTENT_VAR_XYZ"}
        with tempfile.TemporaryDirectory() as user_dir:
            self._make_template(user_dir, "mytemplate", cp_entries=[
                {"source": "${localEnv:NONEXISTENT_VAR_XYZ}", "target": "/home/vscode/x"}
            ])
            with patch.dict(os.environ, env_clean, clear=True):
                with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                    lines = []
                    args = argparse.Namespace(template="mytemplate", projectpath="/myproject",
                                              container_folder=None, timeout=300, dry_run=True,
                                              verbose=False)
                    with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                        dev_code.cmd_open(args)
        combined = "\n".join(lines)
        self.assertIn("<unset:", combined)


class TestBanner(unittest.TestCase):
    def test_help_contains_tagline(self):
        """Banner tagline appears in --help output.
        Uses subprocess to avoid argparse SystemExit contaminating the test runner.
        """
        result = subprocess.run(
            ["uv", "run", "python", "src/dev_code.py", "--help"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project · editor · container — simplified", result.stdout)


class TestListTemplateNames(unittest.TestCase):
    def _make_dirs(self, base, *names):
        for name in names:
            os.makedirs(os.path.join(base, name), exist_ok=True)

    def test_returns_user_templates(self):
        with tempfile.TemporaryDirectory() as user_dir:
            self._make_dirs(user_dir, "alpha", "beta")
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                names = dev_code._list_template_names()
        self.assertIn("alpha", names)
        self.assertIn("beta", names)

    def test_returns_builtin_templates(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            builtin_base = os.path.join(pkg_dir, "dev_code_templates", "dev-code")
            os.makedirs(builtin_base)
            with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": "/nonexistent_xyz"}):
                    names = dev_code._list_template_names()
        self.assertIn("dev-code", names)

    def test_deduplicates_same_name(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            with tempfile.TemporaryDirectory() as user_dir:
                os.makedirs(os.path.join(pkg_dir, "dev_code_templates", "shared"))
                os.makedirs(os.path.join(user_dir, "shared"))
                with patch.object(dev_code, "__file__", os.path.join(pkg_dir, "dev_code.py")):
                    with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                        names = dev_code._list_template_names()
        self.assertEqual(names.count("shared"), 1)

    def test_returns_sorted(self):
        with tempfile.TemporaryDirectory() as user_dir:
            self._make_dirs(user_dir, "zebra", "alpha", "mango")
            with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": user_dir}):
                names = dev_code._list_template_names()
        subset = [n for n in names if n in ("zebra", "alpha", "mango")]
        self.assertEqual(subset, sorted(subset))

    def test_returns_empty_on_missing_dirs(self):
        with patch.dict(os.environ, {"DEVCODE_TEMPLATE_DIR": "/totally_nonexistent_xyz"}):
            with patch.object(dev_code, "__file__", "/nonexistent/dev_code.py"):
                names = dev_code._list_template_names()
        self.assertEqual(names, [])

class TestCmdComplete(unittest.TestCase):
    def _run(self, cword_index, words, template_names=None):
        """Run cmd_completion --complete path, capture printed lines, assert exit code 0."""
        if template_names is None:
            template_names = []
        # --complete receives [cword_index_str, *words] as complete_words
        args = argparse.Namespace(complete_words=[str(cword_index)] + words, shell=None)
        captured = []
        with patch.object(dev_code, "_list_template_names", return_value=template_names):
            with patch("builtins.print", side_effect=lambda x: captured.append(x)):
                with self.assertRaises(SystemExit) as cm:
                    dev_code.cmd_completion(args)
        self.assertEqual(cm.exception.code, 0)
        return captured

    def test_completing_subcommand_returns_all_subcommands(self):
        result = self._run(1, ["dev-code", ""])
        for sub in ("open", "new", "edit", "init", "list", "ps", "completion"):
            self.assertIn(sub, result)

    def test_prefix_filters_subcommands(self):
        result = self._run(1, ["dev-code", "op"])
        self.assertEqual(result, ["open"])

    def test_open_index2_returns_template_names(self):
        result = self._run(2, ["dev-code", "open", ""], template_names=["alpha", "beta"])
        self.assertIn("alpha", result)
        self.assertIn("beta", result)

    def test_open_index2_prefix_filters_templates(self):
        result = self._run(2, ["dev-code", "open", "al"], template_names=["alpha", "beta"])
        self.assertEqual(result, ["alpha"])

    def test_open_index3_returns_empty(self):
        result = self._run(3, ["dev-code", "open", "mytemplate", ""])
        self.assertEqual(result, [])

    def test_open_index4_returns_empty(self):
        result = self._run(4, ["dev-code", "open", "mytemplate", "/path", ""])
        self.assertEqual(result, [])

    def test_open_flag_completion_at_index3(self):
        result = self._run(3, ["dev-code", "open", "mytemplate", "--"])
        self.assertIn("--dry-run", result)
        self.assertIn("--container-folder", result)
        self.assertIn("--timeout", result)

    def test_open_flag_completion_at_index2(self):
        result = self._run(2, ["dev-code", "open", "--"])
        self.assertIn("--dry-run", result)

    def test_new_index2_returns_empty(self):
        result = self._run(2, ["dev-code", "new", ""])
        self.assertEqual(result, [])

    def test_new_index3_returns_template_names(self):
        result = self._run(3, ["dev-code", "new", "myname", ""], template_names=["base1"])
        self.assertIn("base1", result)

    def test_new_flag_completion(self):
        result = self._run(2, ["dev-code", "new", "--"])
        self.assertEqual(result, ["--edit"])

    def test_edit_index2_returns_template_names(self):
        result = self._run(2, ["dev-code", "edit", ""], template_names=["mytemplate"])
        self.assertIn("mytemplate", result)

    def test_edit_index3_returns_empty(self):
        result = self._run(3, ["dev-code", "edit", "mytemplate", ""])
        self.assertEqual(result, [])

    def test_edit_flag_returns_empty(self):
        result = self._run(2, ["dev-code", "edit", "--"])
        self.assertEqual(result, [])

    def test_list_index2_returns_long(self):
        result = self._run(2, ["dev-code", "list", ""])
        self.assertEqual(result, ["--long"])

    def test_list_higher_index_returns_long(self):
        result = self._run(3, ["dev-code", "list", "--long", ""])
        self.assertEqual(result, ["--long"])

    def test_list_prefix_filter_long(self):
        result = self._run(2, ["dev-code", "list", "--l"])
        self.assertEqual(result, ["--long"])

    def test_list_partial_word_no_match_returns_empty(self):
        # list branch fires unconditionally; prefix filter removes --long when current_word="x"
        result = self._run(2, ["dev-code", "list", "x"])
        self.assertEqual(result, [])

    def test_init_returns_empty(self):
        result = self._run(2, ["dev-code", "init", ""])
        self.assertEqual(result, [])

    def test_ps_returns_empty(self):
        result = self._run(2, ["dev-code", "ps", ""])
        self.assertEqual(result, [])

    def test_ps_flags_completion(self):
        result = self._run(2, ["dev-code", "ps", "-"])
        self.assertIn("-a", result)
        self.assertIn("-i", result)

    def test_completion_index2_returns_shells(self):
        result = self._run(2, ["dev-code", "completion", ""])
        self.assertIn("bash", result)
        self.assertIn("zsh", result)

    def test_completion_index2_prefix_filter(self):
        result = self._run(2, ["dev-code", "completion", "b"])
        self.assertEqual(result, ["bash"])

    def test_completion_index3_returns_empty(self):
        result = self._run(3, ["dev-code", "completion", "bash", ""])
        self.assertEqual(result, [])

    def test_empty_complete_words_exits_zero_no_output(self):
        args = argparse.Namespace(complete_words=[], shell=None)
        captured = []
        with patch("builtins.print", side_effect=lambda x: captured.append(x)):
            with self.assertRaises(SystemExit) as cm:
                dev_code.cmd_completion(args)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(captured, [])

    def test_non_integer_cword_exits_zero_no_output(self):
        args = argparse.Namespace(complete_words=["notanint", "dev-code", "open"], shell=None)
        captured = []
        with patch("builtins.print", side_effect=lambda x: captured.append(x)):
            with self.assertRaises(SystemExit) as cm:
                dev_code.cmd_completion(args)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(captured, [])

    def test_out_of_range_cword_exits_zero_no_output(self):
        args = argparse.Namespace(complete_words=["5", "dev-code", "open"], shell=None)
        captured = []
        with patch("builtins.print", side_effect=lambda x: captured.append(x)):
            with self.assertRaises(SystemExit) as cm:
                dev_code.cmd_completion(args)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(captured, [])

    def test_cword_index0_returns_empty(self):
        # index 0 is the command name slot — nothing to complete
        result = self._run(0, ["dev-code"])
        self.assertEqual(result, [])


class TestCmdCompletion(unittest.TestCase):
    def _capture(self, shell):
        args = argparse.Namespace(shell=shell, complete_words=None)
        buf = io.StringIO()
        with patch("builtins.print", side_effect=lambda *a, **kw: buf.write(a[0] if a else "")):
            dev_code.cmd_completion(args)
        return buf.getvalue()

    def test_bash_contains_function_name(self):
        output = self._capture("bash")
        self.assertIn("_dev_code", output)

    def test_bash_contains_complete_registration(self):
        output = self._capture("bash")
        self.assertIn("complete -F", output)

    def test_bash_contains_mapfile(self):
        output = self._capture("bash")
        self.assertIn("mapfile", output)

    def test_zsh_contains_function_name(self):
        output = self._capture("zsh")
        self.assertIn("_dev_code", output)

    def test_zsh_contains_compdef(self):
        output = self._capture("zsh")
        self.assertIn("compdef", output)

    def test_zsh_contains_compinit_warning(self):
        output = self._capture("zsh")
        self.assertIn("compinit", output)

    def test_unknown_shell_exits_1(self):
        args = argparse.Namespace(shell="fish", complete_words=None)
        with self.assertRaises(SystemExit) as cm:
            dev_code.cmd_completion(args)
        self.assertEqual(cm.exception.code, 1)
