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

from click.testing import CliRunner

# Load src/devcode.py as a module
spec = importlib.util.spec_from_file_location(
    "dev_code",
    os.path.join(os.path.dirname(__file__), "..", "src", "devcode.py"),
)
devcode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(devcode)
sys.modules["devcode"] = devcode


class TestSmoke(unittest.TestCase):
    def test_existing_helpers_present(self):
        assert callable(devcode.is_wsl)
        assert callable(devcode.wsl_to_windows)
        assert callable(devcode.build_devcontainer_uri)
        assert callable(devcode.resolve_template_search_path)
        assert callable(devcode._write_template_dir)
        assert callable(devcode.resolve_template)

    def test_banner_is_string(self):
        assert isinstance(devcode.BANNER, str)
        assert len(devcode.BANNER) > 0


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
        data, cli_used = devcode.parse_devcontainer_json(path)
        self.assertEqual(data["name"], "Dev")
        self.assertFalse(cli_used)

    def test_fallback_strips_line_comments(self):
        content = '// top comment\n{"name": "Dev"}'
        path = self._write_json(content)
        data, cli_used = devcode.parse_devcontainer_json(path)
        self.assertEqual(data["name"], "Dev")

    def test_fallback_strips_trailing_commas(self):
        content = '{"features": {"uv": {},}}'
        path = self._write_json(content)
        data, _ = devcode.parse_devcontainer_json(path)
        self.assertIn("features", data)

    def test_fallback_preserves_url_strings(self):
        content = '{"image": "https://example.com/image"}'
        path = self._write_json(content)
        data, _ = devcode.parse_devcontainer_json(path)
        self.assertEqual(data["image"], "https://example.com/image")

    def test_fallback_parse_error_exits(self):
        path = self._write_json("not json at all {{{")
        with self.assertRaises(SystemExit):
            devcode.parse_devcontainer_json(path)

    def test_devcontainer_cli_used_when_available(self):
        config = {"customizations": {"dev-code": []}}
        mock_result = MagicMock(returncode=0, stdout=json.dumps(config))
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/devcontainer" if x == "devcontainer" else None):
            with patch("subprocess.run", return_value=mock_result):
                data, cli_used = devcode.parse_devcontainer_json("/fake/devcontainer.json")
        self.assertTrue(cli_used)
        self.assertIn("customizations", data)

    def test_jq_used_when_devcontainer_unavailable(self):
        config = {"name": "test"}
        mock_result = MagicMock(returncode=0, stdout=json.dumps(config))
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/jq" if x == "jq" else None):
            with patch("subprocess.run", return_value=mock_result):
                path = self._write_json('{"name": "test"}')
                data, cli_used = devcode.parse_devcontainer_json(path)
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
                    cid = devcode.wait_for_container("/fake.json", "/myproject", timeout=60)
        self.assertEqual(cid, "abc123")

    def test_times_out_and_exits(self):
        # Always return empty
        with patch("subprocess.run", return_value=self._make_docker_result("")):
            with patch("time.sleep"):
                # time.time() exceeds deadline immediately after first check
                with patch("time.time", side_effect=[0, 0, 61, 61, 61]):
                    with self.assertRaises(SystemExit):
                        devcode.wait_for_container("/fake.json", "/myproject", timeout=60)

    def test_timeout_message_includes_label_value(self):
        with patch("subprocess.run", return_value=self._make_docker_result("")):
            with patch("time.sleep"):
                with patch("time.time", side_effect=[0, 0, 61, 61, 61]):
                    with self.assertLogs("devcode", level="WARNING") as cm:
                        with self.assertRaises(SystemExit):
                            devcode.wait_for_container("/fake.json", "/my/project", timeout=60)
        self.assertTrue(any("/my/project" in line for line in cm.output))

    def test_warns_on_multiple_containers(self):
        result = self._make_docker_result("abc123\ndef456\n")
        with patch("subprocess.run", return_value=result):
            with patch("time.sleep"):
                with patch("time.time", side_effect=[0, 1, 1, 1, 1]):
                    with self.assertLogs("devcode", level="WARNING") as cm:
                        cid = devcode.wait_for_container("/fake.json", "/myproject", timeout=60)
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
                    with patch.object(devcode, "is_wsl", return_value=True):
                        with patch.object(devcode, "wsl_to_windows", return_value=r"C:\myproject"):
                            devcode.wait_for_container("/fake.json", "/myproject", timeout=60)
        label_filter = next(a for a in calls[0] if "devcontainer.local_folder" in a)
        self.assertIn(r"C:\myproject", label_filter)


class TestFindContainerConfigForProject(unittest.TestCase):
    def _mock_result(self, output, returncode=0):
        return MagicMock(returncode=returncode, stdout=output)

    def test_returns_config_from_running_container(self):
        output = "2024-01-15 10:00:00 +0000 UTC\t/path/to/devcontainer.json\n"
        with patch("subprocess.run", return_value=self._mock_result(output)):
            result = devcode._find_container_config_for_project("/myproject")
        self.assertEqual(result, "/path/to/devcontainer.json")

    def test_returns_most_recent_when_multiple_running(self):
        output = (
            "2024-01-14 10:00:00 +0000 UTC\t/path/old.json\n"
            "2024-01-15 10:00:00 +0000 UTC\t/path/new.json\n"
        )
        with patch("subprocess.run", return_value=self._mock_result(output)):
            result = devcode._find_container_config_for_project("/myproject")
        self.assertEqual(result, "/path/new.json")

    def test_falls_back_to_stopped_when_no_running(self):
        running_result = self._mock_result("")
        stopped_result = self._mock_result(
            "2024-01-15 10:00:00 +0000 UTC\t/stopped.json\n"
        )
        with patch("subprocess.run", side_effect=[running_result, stopped_result]):
            result = devcode._find_container_config_for_project("/myproject")
        self.assertEqual(result, "/stopped.json")

    def test_returns_none_when_no_containers(self):
        with patch("subprocess.run", return_value=self._mock_result("")):
            result = devcode._find_container_config_for_project("/myproject")
        self.assertIsNone(result)

    def test_returns_none_on_docker_failure(self):
        with patch("subprocess.run", return_value=self._mock_result("", returncode=1)):
            result = devcode._find_container_config_for_project("/myproject")
        self.assertIsNone(result)

    def test_wsl_converts_path(self):
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            return self._mock_result("")
        with patch("subprocess.run", side_effect=fake_run):
            with patch.object(devcode, "is_wsl", return_value=True):
                with patch.object(devcode, "wsl_to_windows", return_value=r"C:\myproject"):
                    devcode._find_container_config_for_project("/myproject")
        label_filter = next(a for a in calls[0] if "devcontainer.local_folder" in a)
        self.assertIn(r"C:\myproject", label_filter)

    def test_returns_none_when_docker_not_installed(self):
        with patch("subprocess.run", side_effect=OSError("docker not found")):
            result = devcode._find_container_config_for_project("/myproject")
        self.assertIsNone(result)


class TestCmdOpen(unittest.TestCase):
    def test_errors_on_nonexistent_projectpath(self):
        with self.assertRaises(SystemExit):
            devcode._do_open("/nonexistent/path/xyz123", "mytemplate", None, 300, False)

    def test_errors_on_root_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            # patch abspath to return "/" so the root guard triggers
            with patch("os.path.abspath", return_value="/"):
                with patch("os.path.exists", return_value=True):
                    with self.assertRaises(SystemExit):
                        devcode._do_open(tmp, "mytemplate", None, 300, False)

    def test_uses_explicit_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(devcode, "resolve_template", return_value="/fake/devcontainer.json") as mock_rt:
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("shutil.which", return_value="/usr/bin/code"):
                        with patch("subprocess.Popen"):
                            with patch.object(devcode, "run_post_launch"):
                                devcode._do_open(tmp, "mytemplate", None, 300, False)
            mock_rt.assert_called_once_with("mytemplate")

    def test_auto_detects_from_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(devcode, "_find_container_config_for_project",
                               return_value="/found/devcontainer.json") as mock_find:
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("shutil.which", return_value="/usr/bin/code"):
                        with patch("subprocess.Popen"):
                            with patch.object(devcode, "run_post_launch"):
                                devcode._do_open(tmp, None, None, 300, False)
            mock_find.assert_called_once()

    def test_auto_detects_falls_back_to_default_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(devcode, "_find_container_config_for_project", return_value=None):
                with patch.object(devcode, "_load_settings",
                                   return_value={"default_template": "dev-code"}):
                    with patch.object(devcode, "resolve_template",
                                       return_value="/fake/devcontainer.json") as mock_rt:
                        with patch.object(devcode, "_git_repo_root", return_value=None):
                            with patch("shutil.which", return_value="/usr/bin/code"):
                                with patch("subprocess.Popen"):
                                    with patch.object(devcode, "run_post_launch"):
                                        devcode._do_open(tmp, None, None, 300, False)
                mock_rt.assert_called_once_with("dev-code")

    def test_errors_when_no_container_and_no_default_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(devcode, "_find_container_config_for_project", return_value=None):
                with patch.object(devcode, "_load_settings", return_value={"default_template": ""}):
                    with patch.object(devcode, "_git_repo_root", return_value=None):
                        with self.assertRaises(SystemExit):
                            devcode._do_open(tmp, None, None, 300, False)


class TestConfDir(unittest.TestCase):
    def test_uses_devcode_conf_dir_override(self):
        with patch.dict(os.environ, {"DEVCODE_CONF_DIR": "/custom/conf"}):
            self.assertEqual(devcode._conf_dir(), "/custom/conf")

    def test_falls_back_to_xdg_config_home(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("DEVCODE_CONF_DIR", "XDG_CONFIG_HOME")}
        env["XDG_CONFIG_HOME"] = "/xdg/config"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(devcode._conf_dir(), os.path.join("/xdg/config", "dev-code"))

    def test_falls_back_to_default_config_dir(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("DEVCODE_CONF_DIR", "XDG_CONFIG_HOME")}
        with patch.dict(os.environ, env, clear=True):
            result = devcode._conf_dir()
        self.assertTrue(result.endswith(os.path.join(".config", "dev-code")))


class TestLoadSettings(unittest.TestCase):
    def test_creates_default_settings_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"DEVCODE_CONF_DIR": tmp}):
                settings = devcode._load_settings()
        self.assertEqual(settings["default_template"], "dev-code")
        self.assertIn("template_sources", settings)

    def test_creates_settings_file_on_disk_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf_dir = os.path.join(tmp, "conf")
            with patch.dict(os.environ, {"DEVCODE_CONF_DIR": conf_dir}):
                devcode._load_settings()
            self.assertTrue(os.path.exists(os.path.join(conf_dir, "settings.json")))

    def test_reads_existing_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = os.path.join(tmp, "settings.json")
            with open(settings_path, "w") as f:
                json.dump({"template_sources": ["/custom"], "default_template": "mytemplate"}, f)
            with patch.dict(os.environ, {"DEVCODE_CONF_DIR": tmp}):
                settings = devcode._load_settings()
        self.assertEqual(settings["default_template"], "mytemplate")
        self.assertEqual(settings["template_sources"], ["/custom"])

    def test_invalid_json_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = os.path.join(tmp, "settings.json")
            with open(settings_path, "w") as f:
                f.write("not valid json {{{")
            with patch.dict(os.environ, {"DEVCODE_CONF_DIR": tmp}):
                with self.assertLogs("devcode", level="WARNING"):
                    settings = devcode._load_settings()
        self.assertEqual(settings["default_template"], "dev-code")


class TestResolveTemplateSearchPath(unittest.TestCase):
    def test_uses_template_sources_from_settings(self):
        with patch.object(devcode, "_load_settings", return_value={"template_sources": ["/a", "/b"]}):
            self.assertEqual(devcode.resolve_template_search_path(), ["/a", "/b"])

    def test_expands_tilde_in_sources(self):
        with patch.object(devcode, "_load_settings", return_value={"template_sources": ["~/templates"]}):
            result = devcode.resolve_template_search_path()
        self.assertTrue(os.path.isabs(result[0]))
        self.assertNotIn("~", result[0])

    def test_falls_back_to_xdg_data_home_when_no_sources(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"}
        env["XDG_DATA_HOME"] = "/xdg/data"
        with patch.object(devcode, "_load_settings", return_value={}):
            with patch.dict(os.environ, env, clear=True):
                result = devcode.resolve_template_search_path()
        self.assertEqual(result, [os.path.join("/xdg/data", "dev-code", "templates")])

    def test_skips_empty_entries(self):
        with patch.object(devcode, "_load_settings", return_value={"template_sources": ["/a", "", "/b"]}):
            self.assertEqual(devcode.resolve_template_search_path(), ["/a", "/b"])

    def test_write_template_dir_returns_first_entry(self):
        with patch.object(devcode, "_load_settings", return_value={"template_sources": ["/first", "/second"]}):
            self.assertEqual(devcode._write_template_dir(), "/first")


class TestGetBuiltinTemplatePath(unittest.TestCase):
    def test_returns_path_for_known_builtin(self):
        with tempfile.TemporaryDirectory() as d:
            builtin_dir = os.path.join(d, "dev_code_templates", "dev-code")
            os.makedirs(builtin_dir)
            with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                result = devcode.get_builtin_template_path("dev-code")
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("dev-code"))

    def test_returns_none_for_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                result = devcode.get_builtin_template_path("nonexistent")
        self.assertIsNone(result)


class TestIsValidTemplate(unittest.TestCase):
    def test_valid_template_with_devcontainer_json(self):
        with tempfile.TemporaryDirectory() as d:
            dc = os.path.join(d, ".devcontainer")
            os.makedirs(dc)
            open(os.path.join(dc, "devcontainer.json"), "w").close()
            self.assertTrue(devcode._is_valid_template(d))

    def test_invalid_template_missing_devcontainer_json(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(devcode._is_valid_template(d))

    def test_invalid_template_has_dir_not_file(self):
        with tempfile.TemporaryDirectory() as d:
            dc = os.path.join(d, ".devcontainer")
            os.makedirs(os.path.join(dc, "devcontainer.json"))  # dir, not file
            self.assertFalse(devcode._is_valid_template(d))


class TestFindTemplateInSearchPath(unittest.TestCase):
    def _make_template(self, search_dir, name):
        root = os.path.join(search_dir, name)
        dc = os.path.join(root, ".devcontainer")
        os.makedirs(dc)
        open(os.path.join(dc, "devcontainer.json"), "w").close()
        return root

    def test_finds_template_in_first_dir(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                root = self._make_template(d1, "mytemplate")
                with patch.object(devcode, "resolve_template_search_path", return_value=[d1, d2]):
                    result = devcode._find_template_in_search_path("mytemplate")
        self.assertEqual(result, root)

    def test_finds_template_in_second_dir(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                root = self._make_template(d2, "mytemplate")
                with patch.object(devcode, "resolve_template_search_path", return_value=[d1, d2]):
                    result = devcode._find_template_in_search_path("mytemplate")
        self.assertEqual(result, root)

    def test_first_match_wins(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                root1 = self._make_template(d1, "mytemplate")
                self._make_template(d2, "mytemplate")
                with patch.object(devcode, "resolve_template_search_path", return_value=[d1, d2]):
                    result = devcode._find_template_in_search_path("mytemplate")
        self.assertEqual(result, root1)

    def test_returns_none_when_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(devcode, "resolve_template_search_path", return_value=[d]):
                result = devcode._find_template_in_search_path("no-such")
        self.assertIsNone(result)

    def test_skips_dirs_without_devcontainer_json(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "mytemplate"))
            with patch.object(devcode, "resolve_template_search_path", return_value=[d]):
                result = devcode._find_template_in_search_path("mytemplate")
        self.assertIsNone(result)


class TestResolveTemplate(unittest.TestCase):
    def test_finds_user_template(self):
        with tempfile.TemporaryDirectory() as d:
            tpath = os.path.join(d, "mytemplate", ".devcontainer")
            os.makedirs(tpath)
            cfg = os.path.join(tpath, "devcontainer.json")
            open(cfg, "w").close()
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d]}):
                result = devcode.resolve_template("mytemplate")
        self.assertEqual(result, cfg)

    def test_exits_when_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d]}):
                with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                    with self.assertRaises(SystemExit):
                        devcode.resolve_template("no-such-template")

    def test_resolves_file_path_when_no_template(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "devcontainer.json")
            open(cfg, "w").close()
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d + "_templates"]}):
                with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                    result = devcode.resolve_template(cfg)
        self.assertEqual(result, cfg)

    def test_resolves_directory_path_when_no_template(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, ".devcontainer")
            os.makedirs(sub)
            cfg = os.path.join(sub, "devcontainer.json")
            open(cfg, "w").close()
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d + "_templates"]}):
                with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                    result = devcode.resolve_template(d)
        self.assertEqual(result, cfg)

    def test_dot_slash_prefix_resolves_as_path_not_template(self):
        """./mydev resolves as path even if a template named mydev exists."""
        with tempfile.TemporaryDirectory() as d:
            tpath = os.path.join(d, "templates", "mydev", ".devcontainer")
            os.makedirs(tpath)
            open(os.path.join(tpath, "devcontainer.json"), "w").close()
            local = os.path.join(d, "mydev", ".devcontainer")
            os.makedirs(local)
            local_cfg = os.path.join(local, "devcontainer.json")
            open(local_cfg, "w").close()
            old_cwd = os.getcwd()
            os.chdir(d)
            try:
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [os.path.join(d, "templates")]}):
                    with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                        result = devcode.resolve_template("./mydev")
            finally:
                os.chdir(old_cwd)
        self.assertEqual(os.path.realpath(result), os.path.realpath(local_cfg))

    def test_absolute_path_resolves_as_path_not_template(self):
        """An absolute path resolves as path even if its basename matches a template."""
        with tempfile.TemporaryDirectory() as d:
            tpath = os.path.join(d, "templates", "mydev", ".devcontainer")
            os.makedirs(tpath)
            open(os.path.join(tpath, "devcontainer.json"), "w").close()
            local = os.path.join(d, "mydev", ".devcontainer")
            os.makedirs(local)
            local_cfg = os.path.join(local, "devcontainer.json")
            open(local_cfg, "w").close()
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [os.path.join(d, "templates")]}):
                with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                    result = devcode.resolve_template(os.path.join(d, "mydev"))
        self.assertEqual(result, local_cfg)

    def test_ambiguity_warns_and_uses_template(self):
        with tempfile.TemporaryDirectory() as d:
            dirname = os.path.basename(d)
            tpath = os.path.join(d + "_templates", dirname, ".devcontainer")
            os.makedirs(tpath)
            template_cfg = os.path.join(tpath, "devcontainer.json")
            open(template_cfg, "w").close()
            local = os.path.join(d, ".devcontainer")
            os.makedirs(local)
            open(os.path.join(local, "devcontainer.json"), "w").close()
            old_cwd = os.getcwd()
            os.chdir(os.path.dirname(d))
            try:
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [d + "_templates"]}):
                    with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                        with self.assertLogs("devcode", level="WARNING") as cm:
                            result = devcode.resolve_template(dirname)
            finally:
                os.chdir(old_cwd)
        self.assertEqual(result, template_cfg)
        self.assertTrue(any("matches both" in line for line in cm.output))
        self.assertTrue(any("Use './" in line for line in cm.output))

    def test_exits_with_wrong_filename_in_path(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "custom.json")
            open(cfg, "w").close()
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d + "_t"]}):
                with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                    with self.assertRaises(SystemExit):
                        devcode.resolve_template(cfg)

    def test_exits_when_path_prefix_dir_has_no_devcontainer(self):
        with tempfile.TemporaryDirectory() as d:
            empty_dir = os.path.join(d, "myproject")
            os.makedirs(empty_dir)
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d + "_t"]}):
                with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                    with self.assertRaises(SystemExit):
                        devcode.resolve_template(empty_dir)

    def test_exits_when_path_prefix_but_path_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            nonexistent = os.path.join(d, "nonexistent")
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d + "_t"]}):
                with patch.object(devcode, "__file__", os.path.join(d, "devcode.py")):
                    with self.assertLogs("devcode", level="ERROR") as cm:
                        with self.assertRaises(SystemExit):
                            devcode.resolve_template(nonexistent)
            self.assertTrue(any("path not found" in line for line in cm.output))

    def test_finds_template_via_search_path(self):
        with tempfile.TemporaryDirectory() as d:
            tpath = os.path.join(d, "mytemplate", ".devcontainer")
            os.makedirs(tpath)
            cfg = os.path.join(tpath, "devcontainer.json")
            open(cfg, "w").close()
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d]}):
                result = devcode.resolve_template("mytemplate")
        self.assertEqual(result, cfg)

    def test_does_not_fall_back_to_builtin(self):
        """Built-in is no longer a fallback for resolve_template."""
        with tempfile.TemporaryDirectory() as user_dir:
            with tempfile.TemporaryDirectory() as pkg_dir:
                builtin = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
                os.makedirs(builtin)
                open(os.path.join(builtin, "devcontainer.json"), "w").close()
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                    with patch.object(devcode, "__file__", os.path.join(pkg_dir, "devcode.py")):
                        with self.assertRaises(SystemExit):
                            devcode.resolve_template("dev-code")

    def test_finds_in_second_search_dir(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                tpath = os.path.join(d2, "mytemplate", ".devcontainer")
                os.makedirs(tpath)
                cfg = os.path.join(tpath, "devcontainer.json")
                open(cfg, "w").close()
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [d1, d2]}):
                    result = devcode.resolve_template("mytemplate")
        self.assertEqual(result, cfg)


class TestResolveAsPath(unittest.TestCase):
    def test_valid_file_path(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "devcontainer.json")
            open(cfg, "w").close()
            result = devcode._resolve_as_path(cfg)
        self.assertEqual(result, cfg)

    def test_file_wrong_name_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "custom.json")
            open(cfg, "w").close()
            result = devcode._resolve_as_path(cfg)
        self.assertIsNone(result)

    def test_directory_with_devcontainer_subdir(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, ".devcontainer")
            os.makedirs(sub)
            cfg = os.path.join(sub, "devcontainer.json")
            open(cfg, "w").close()
            result = devcode._resolve_as_path(d)
        self.assertEqual(result, cfg)

    def test_directory_with_bare_devcontainer_json(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "devcontainer.json")
            open(cfg, "w").close()
            result = devcode._resolve_as_path(d)
        self.assertEqual(result, cfg)

    def test_directory_no_devcontainer_json_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            result = devcode._resolve_as_path(d)
        self.assertIsNone(result)

    def test_nonexistent_path_returns_none(self):
        result = devcode._resolve_as_path("/nonexistent/path/that/does/not/exist")
        self.assertIsNone(result)

    def test_tilde_expansion(self):
        home = os.path.expanduser("~")
        with tempfile.TemporaryDirectory(dir=home) as d:
            cfg = os.path.join(d, "devcontainer.json")
            open(cfg, "w").close()
            rel = "~/" + os.path.relpath(d, home)
            result = devcode._resolve_as_path(rel)
        self.assertEqual(result, cfg)

    def test_directory_prefers_subdir_over_bare(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, ".devcontainer")
            os.makedirs(sub)
            subdir_cfg = os.path.join(sub, "devcontainer.json")
            open(subdir_cfg, "w").close()
            bare_cfg = os.path.join(d, "devcontainer.json")
            open(bare_cfg, "w").close()
            result = devcode._resolve_as_path(d)
        self.assertEqual(result, subdir_cfg)


class TestHasPathPrefix(unittest.TestCase):
    def test_dot_slash(self):
        self.assertTrue(devcode._has_path_prefix("./foo"))

    def test_dot_dot_slash(self):
        self.assertTrue(devcode._has_path_prefix("../foo"))

    def test_absolute(self):
        self.assertTrue(devcode._has_path_prefix("/foo/bar"))

    def test_tilde(self):
        self.assertTrue(devcode._has_path_prefix("~/foo"))

    def test_dot_alone(self):
        self.assertTrue(devcode._has_path_prefix("."))

    def test_plain_name(self):
        self.assertFalse(devcode._has_path_prefix("mydev"))

    def test_plain_name_with_dash(self):
        self.assertFalse(devcode._has_path_prefix("my-template"))


class TestMain(unittest.TestCase):
    def setUp(self):
        # Create a minimal template dir for tests
        self.tmpdir = tempfile.mkdtemp()
        tpl = os.path.join(self.tmpdir, "claude", ".devcontainer")
        os.makedirs(tpl)
        open(os.path.join(tpl, "devcontainer.json"), "w").close()
        self.patch_settings = patch.object(devcode, "_load_settings", return_value={"template_sources": [self.tmpdir]})
        self.patch_settings.start()
        # Snapshot logger handlers so we can restore after CliRunner calls install one
        self._logger_handlers = list(devcode.logger.handlers)

    def tearDown(self):
        self.patch_settings.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        # Remove any logger handlers added during the test (CliRunner installs a StreamHandler
        # pointing at its fake stderr; that stream becomes invalid after CliRunner exits)
        devcode.logger.handlers[:] = self._logger_handlers

    def test_projectpath_root_exits(self):
        runner = CliRunner()
        result = runner.invoke(devcode.cli, ["open", "/", "claude"])
        self.assertNotEqual(result.exit_code, 0)

    def test_code_not_on_path_exits(self):
        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch.object(devcode, "_git_repo_root", return_value=None):
                with patch("shutil.which", return_value=None):
                    result = runner.invoke(devcode.cli, ["open", "/myproject", "claude"])
        self.assertNotEqual(result.exit_code, 0)

    def test_launches_vscode_with_folder_uri(self):
        launched = []
        def fake_popen(cmd, **kw):
            launched.append(cmd)
            return MagicMock()

        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("subprocess.Popen", side_effect=fake_popen):
                        with patch.object(devcode, "run_post_launch"):
                            result = runner.invoke(devcode.cli, ["open", "/myproject", "claude"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0][0], "code")
        self.assertEqual(launched[0][1], "--folder-uri")
        self.assertIn("vscode-remote://dev-container+", launched[0][2])

    def test_default_container_folder(self):
        launched = []
        def fake_popen(cmd, **kw):
            launched.append(cmd)
            return MagicMock()

        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("subprocess.Popen", side_effect=fake_popen):
                        with patch.object(devcode, "run_post_launch"):
                            result = runner.invoke(devcode.cli, ["open", "/myproject", "claude"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("/workspaces/myproject", launched[0][2])

    def test_custom_container_folder(self):
        launched = []
        def fake_popen(cmd, **kw):
            launched.append(cmd)
            return MagicMock()

        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("subprocess.Popen", side_effect=fake_popen):
                        with patch.object(devcode, "run_post_launch"):
                            result = runner.invoke(
                                devcode.cli,
                                ["open", "/myproject", "claude", "--container-folder", "/workspace/custom"]
                            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("/workspace/custom", launched[0][2])

    def test_timeout_passed_to_run_post_launch(self):
        captured = {}
        def fake_rpl(config_file, project_path, timeout):
            captured["timeout"] = timeout

        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("subprocess.Popen", return_value=MagicMock()):
                        with patch.object(devcode, "run_post_launch", side_effect=fake_rpl):
                            result = runner.invoke(
                                devcode.cli,
                                ["open", "/myproject", "claude", "--timeout", "42"]
                            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(captured["timeout"], 42)

    def test_default_timeout_is_300(self):
        captured = {}
        def fake_rpl(config_file, project_path, timeout):
            captured["timeout"] = timeout

        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch("shutil.which", return_value="/usr/bin/code"):
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("subprocess.Popen", return_value=MagicMock()):
                        with patch.object(devcode, "run_post_launch", side_effect=fake_rpl):
                            result = runner.invoke(devcode.cli, ["open", "/myproject", "claude"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(captured["timeout"], 300)


class TestGitSubdirGuard(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        tpl = os.path.join(self.tmpdir, "claude", ".devcontainer")
        os.makedirs(tpl)
        open(os.path.join(tpl, "devcontainer.json"), "w").close()
        self.patch_settings = patch.object(devcode, "_load_settings", return_value={"template_sources": [self.tmpdir]})
        self.patch_settings.start()
        # Snapshot logger handlers so we can restore after CliRunner calls install one
        self._logger_handlers = list(devcode.logger.handlers)

    def tearDown(self):
        self.patch_settings.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        # Remove any logger handlers added during the test (CliRunner installs a StreamHandler
        # pointing at its fake stderr; that stream becomes invalid after CliRunner exits)
        devcode.logger.handlers[:] = self._logger_handlers

    def test_subdir_of_git_repo_exits(self):
        """Guard fires when git root differs from project_path."""
        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch.object(devcode, "_git_repo_root", return_value="/repo"):
                result = runner.invoke(devcode.cli, ["open", "/repo/subproject", "claude"])
        self.assertNotEqual(result.exit_code, 0)

    def test_project_is_git_root_does_not_exit(self):
        """Guard does not fire when project_path IS the git root."""
        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch.object(devcode, "_git_repo_root", return_value="/repo"):
                with patch("shutil.which", return_value="/usr/bin/code"):
                    with patch("subprocess.Popen", return_value=MagicMock()):
                        with patch.object(devcode, "run_post_launch"):
                            result = runner.invoke(devcode.cli, ["open", "/repo", "claude"])
        self.assertEqual(result.exit_code, 0)

    def test_not_in_git_repo_does_not_exit(self):
        """Guard does not fire when _git_repo_root returns None."""
        runner = CliRunner()
        with patch("os.path.exists", return_value=True):
            with patch.object(devcode, "_git_repo_root", return_value=None):
                with patch("shutil.which", return_value="/usr/bin/code"):
                    with patch("subprocess.Popen", return_value=MagicMock()):
                        with patch.object(devcode, "run_post_launch"):
                            result = runner.invoke(devcode.cli, ["open", "/myproject", "claude"])
        self.assertEqual(result.exit_code, 0)

    def test_git_not_available_does_not_exit(self):
        """_git_repo_root returns None when git is unavailable (OSError)."""
        with patch("devcode.subprocess.run", side_effect=OSError("git not found")):
            result = devcode._git_repo_root("/some/path")
        self.assertIsNone(result)


class TestListDirChildren(unittest.TestCase):
    def test_returns_absolute_paths(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "file.txt"), "w").close()
            result = devcode._list_dir_children(d)
        self.assertEqual(result, [os.path.join(d, "file.txt")])

    def test_includes_dot_files(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, ".hidden"), "w").close()
            result = devcode._list_dir_children(d)
        self.assertIn(os.path.join(d, ".hidden"), result)

    def test_empty_dir_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            result = devcode._list_dir_children(d)
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
            with patch.object(devcode, "parse_devcontainer_json", patches["parse_devcontainer_json"]):
                with patch.object(devcode, "wait_for_container", patches["wait_for_container"]):
                    with patch("subprocess.run", side_effect=fake_run):
                        devcode.run_post_launch("/fake/devcontainer.json", "/myproject", 300)

        return docker_calls

    def test_no_entries_skips_docker(self):
        calls = self._run([])
        self.assertEqual(calls, [])

    def test_absent_key_skips_docker(self):
        config = {"customizations": {}}
        with patch.object(devcode, "parse_devcontainer_json",
                          return_value=(config, False)):
            with patch.object(devcode, "wait_for_container") as mock_wait:
                devcode.run_post_launch("/fake.json", "/proj", 300)
        mock_wait.assert_not_called()

    def test_none_value_skips_docker(self):
        # dev-code key present but null — not a dict, skip silently
        config = {"customizations": {"dev-code": None}}
        with patch.object(devcode, "parse_devcontainer_json",
                          return_value=(config, False)):
            with patch.object(devcode, "wait_for_container") as mock_wait:
                devcode.run_post_launch("/fake.json", "/proj", 300)
        mock_wait.assert_not_called()

    def test_non_dict_value_exits(self):
        # dev-code key exists but is not a dict (e.g. old flat-list format)
        config = {"customizations": {"dev-code": "bad"}}
        with patch.object(devcode, "parse_devcontainer_json",
                          return_value=(config, False)):
            with self.assertRaises(SystemExit):
                devcode.run_post_launch("/fake.json", "/proj", 300)

    def test_absent_cp_key_skips_docker(self):
        # dev-code is a dict but has no cp key — silent no-op
        config = {"customizations": {"dev-code": {}}}
        with patch.object(devcode, "parse_devcontainer_json",
                          return_value=(config, False)):
            with patch.object(devcode, "wait_for_container") as mock_wait:
                devcode.run_post_launch("/fake.json", "/proj", 300)
        mock_wait.assert_not_called()

    def test_cp_non_list_exits(self):
        # cp key exists but is not a list
        config = {"customizations": {"dev-code": {"cp": "oops"}}}
        with patch.object(devcode, "parse_devcontainer_json",
                          return_value=(config, False)):
            with self.assertRaises(SystemExit):
                devcode.run_post_launch("/fake.json", "/proj", 300)

    def test_missing_source_warns_and_skips(self):
        entries = [{"target": "/home/vscode/.claude"}]
        with self.assertLogs("devcode", level="WARNING") as cm:
            calls = self._run(entries)
        self.assertTrue(any("source" in line.lower() for line in cm.output))
        docker_cp_calls = [c for c in calls if "cp" in c]
        self.assertEqual(docker_cp_calls, [])

    def test_missing_target_warns_and_skips(self):
        entries = [{"source": "/home/.claude"}]
        with self.assertLogs("devcode", level="WARNING") as cm:
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
        with self.assertLogs("devcode", level="WARNING") as cm:
            self._run(entries)
        self.assertTrue(any("Override" in line for line in cm.output))

    def test_unknown_field_warns(self):
        """Any field not in the known schema triggers a warning."""
        entries = [{"source": "/src", "target": "/tgt", "typo_field": "bad", "override": True}]
        with self.assertLogs("devcode", level="WARNING") as cm:
            self._run(entries)
        self.assertTrue(any("typo_field" in line for line in cm.output))

    def test_unknown_field_warns_once_for_dir_expansion(self):
        """Unknown-field warning fires once for the parent entry, not once per expanded child."""
        entries = [{"source": "/src/dotfiles/.", "target": "/home/user/", "bad_key": "x", "override": True}]
        config = self._make_config(entries)
        children = ["/src/dotfiles/.bashrc", "/src/dotfiles/.zshrc"]
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=True):
                            with patch.object(devcode, "_list_dir_children", return_value=children):
                                with self.assertLogs("devcode", level="WARNING") as cm:
                                    devcode.run_post_launch("/fake.json", "/proj", 300)
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
            with self.assertLogs("devcode", level="WARNING"):
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
            with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
                with patch.object(devcode, "wait_for_container", return_value="cid"):
                    with patch("subprocess.run", side_effect=fake_run):
                        with patch("os.path.exists", return_value=True):
                            with patch("os.path.isdir", return_value=False):
                                devcode.run_post_launch(config_file, "/proj", 300)
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

        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", return_value=MagicMock(returncode=1)):
                    with patch("os.path.exists", return_value=False):
                        with self.assertLogs("devcode", level="WARNING") as cm:
                            devcode.run_post_launch("/fake/devcontainer.json", "/proj", 300)
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
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            devcode.run_post_launch("/fake.json", "/proj", 300)
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
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            devcode.run_post_launch("/fake.json", "/proj", 300)
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
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            devcode.run_post_launch("/fake.json", "/proj", 300)
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
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", side_effect=isdir_side_effect):
                            with patch.object(devcode, "_list_dir_children",
                                              return_value=children):
                                devcode.run_post_launch("/fake.json", "/proj", 300)
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
            with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
                with patch.object(devcode, "wait_for_container", return_value="cid"):
                    with patch("subprocess.run", side_effect=fake_run):
                        with patch("os.path.exists", return_value=True):
                            with patch("os.path.isdir", side_effect=isdir_side_effect):
                                with patch.object(devcode, "_list_dir_children",
                                                  return_value=children):
                                    devcode.run_post_launch(config_file, "/proj", 300)
        # Must expand to one docker cp per child, NOT one cp of the whole dir
        self.assertEqual(len(cp_calls), 2)
        cp_sources = [c[2] for c in cp_calls]
        self.assertIn(os.path.join(dotfiles_dir, ".bashrc"), cp_sources)
        self.assertIn(os.path.join(dotfiles_dir, ".gitconfig"), cp_sources)

    def test_dir_contents_source_without_trailing_slash_target_warns(self):
        """source/. with target not ending in / produces a warning."""
        entries = [{"source": "/src/dotfiles/.", "target": "/home/user", "override": True}]
        config = self._make_config(entries)
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                    with patch("os.path.exists", return_value=True):
                        with patch.object(devcode, "_list_dir_children", return_value=[]):
                            with self.assertLogs("devcode", level="WARNING") as cm:
                                devcode.run_post_launch("/fake.json", "/proj", 300)
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
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=True):
                            with patch.object(devcode, "_list_dir_children", return_value=[]):
                                devcode.run_post_launch("/fake.json", "/proj", 300)
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
        with patch.object(devcode, "parse_devcontainer_json", return_value=(config, False)):
            with patch.object(devcode, "wait_for_container", return_value="cid"):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.isdir", return_value=False):
                            calls = []
                            original_run = subprocess.run
                            def tracking_run(cmd, **kw):
                                calls.append(cmd)
                                return fake_run(cmd, **kw)
                            with patch("subprocess.run", side_effect=tracking_run):
                                devcode.run_post_launch("/fake.json", "/proj", 300)
        chown_calls = [c for c in calls if "chown" in c]
        self.assertEqual(chown_calls, [], "chown must not run after failed cp")


class TestCmdList(unittest.TestCase):
    def _make_template(self, base_dir, name):
        p = os.path.join(base_dir, name, ".devcontainer")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "devcontainer.json"), "w") as f:
            f.write("{}")

    def _make_template_with_name(self, base_dir, template_name, devcontainer_name):
        p = os.path.join(base_dir, template_name, ".devcontainer")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "devcontainer.json"), "w") as f:
            json.dump({"name": devcontainer_name}, f)

    def _run_list(self, search_path, long=False):
        lines = []
        dirs = [d for d in search_path.split(os.pathsep) if d]
        with patch.object(devcode, "_load_settings", return_value={"template_sources": dirs}):
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.list_command.callback(long=long)
        return lines

    def test_short_lists_user_templates(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_template(d, "mytemplate")
            lines = self._run_list(d)
        self.assertIn("mytemplate", lines)

    def test_short_does_not_include_builtins(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            b = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
            os.makedirs(b)
            open(os.path.join(b, "devcontainer.json"), "w").close()
            with tempfile.TemporaryDirectory() as d:
                with patch.object(devcode, "__file__", os.path.join(pkg_dir, "devcode.py")):
                    lines = self._run_list(d)
        self.assertNotIn("dev-code", lines)

    def test_short_excludes_invalid_templates(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "nojson"))  # no devcontainer.json
            self._make_template(d, "valid")
            lines = self._run_list(d)
        self.assertIn("valid", lines)
        self.assertNotIn("nojson", lines)

    def test_short_deduplicates_across_dirs(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                self._make_template(d1, "shared")
                self._make_template(d2, "shared")
                lines = self._run_list(os.pathsep.join([d1, d2]))
        self.assertEqual(lines.count("shared"), 1)

    def test_long_shows_header_and_rows(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                self._make_template_with_name(d1, "alpha", "Alpha Env")
                self._make_template_with_name(d2, "beta", "Beta Env")
                lines = self._run_list(os.pathsep.join([d1, d2]), long=True)
        combined = "\n".join(lines)
        self.assertIn("NAME", combined)
        self.assertIn("DESC", combined)
        self.assertIn("PATH", combined)
        self.assertIn("alpha", combined)
        self.assertIn("beta", combined)
        self.assertIn("Alpha Env", combined)
        self.assertIn("Beta Env", combined)

    def test_long_no_templates_shows_hint(self):
        with tempfile.TemporaryDirectory() as d:
            lines = self._run_list(d, long=True)
        combined = "\n".join(str(l) for l in lines)
        self.assertIn("(no templates)", combined)
        self.assertNotIn("devcode init", combined)

    def test_long_ignores_nonexistent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_template(d, "mytemplate")
            nonexistent = os.path.join(d, "no-such-dir")
            lines = self._run_list(os.pathsep.join([d, nonexistent]), long=True)
        # Header + 1 data row = 2 lines total
        self.assertEqual(len(lines), 2)
        self.assertIn("mytemplate", "\n".join(lines))

    def test_no_templates_shows_empty_message(self):
        with tempfile.TemporaryDirectory() as d:
            lines = self._run_list(d)
        combined = "\n".join(str(l) for l in lines)
        self.assertIn("(no templates)", combined)
        self.assertNotIn("devcode init", combined)

    def test_long_desc_from_devcontainer_name(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_template_with_name(d, "mytemplate", "My Template")
            lines = self._run_list(d, long=True)
        self.assertEqual(len(lines), 2)  # header + 1 data row
        self.assertIn("mytemplate", lines[1])
        self.assertIn("My Template", lines[1])

    def test_long_path_tilde_abbreviated(self):
        home = os.path.expanduser("~")
        with tempfile.TemporaryDirectory(dir=home) as d:
            self._make_template(d, "mytemplate")
            lines = self._run_list(d, long=True)
        combined = "\n".join(lines)
        self.assertIn("~", combined)
        self.assertNotIn(home + os.sep + os.path.basename(d), combined)

    def test_long_empty_desc_when_no_name_field(self):
        with tempfile.TemporaryDirectory() as d:
            # _make_template now writes {} — valid JSON with no "name" field
            self._make_template(d, "mytemplate")
            lines = self._run_list(d, long=True)
        self.assertEqual(len(lines), 2)
        data_row = lines[1]
        self.assertIn("mytemplate", data_row)
        # PATH appears in data row; no desc value between name and path
        template_path = os.path.join(d, "mytemplate")
        self.assertIn(template_path, data_row)

    def test_long_malformed_json_shows_empty_desc(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "broken", ".devcontainer")
            os.makedirs(p)
            with open(os.path.join(p, "devcontainer.json"), "w") as f:
                f.write("{ not valid json }")
            lines = self._run_list(d, long=True)
        self.assertEqual(len(lines), 2)  # header + 1 data row
        data_row = lines[1]
        self.assertIn("broken", data_row)
        template_path = os.path.join(d, "broken")
        self.assertIn(template_path, data_row)

    def test_long_deduplication_first_match_wins(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                self._make_template_with_name(d1, "shared", "First")
                self._make_template_with_name(d2, "shared", "Second")
                lines = self._run_list(os.pathsep.join([d1, d2]), long=True)
        combined = "\n".join(lines)
        self.assertIn("First", combined)
        self.assertNotIn("Second", combined)
        # Only one data row (header + 1)
        self.assertEqual(len(lines), 2)


class TestCmdNew(unittest.TestCase):
    def _setup_pkg(self, pkg_dir):
        """Create a fake dev-code built-in in pkg_dir."""
        b = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
        os.makedirs(b)
        open(os.path.join(b, "devcontainer.json"), "w").close()

    def test_creates_template_from_default_base_legacy_env(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            self._setup_pkg(pkg_dir)
            with tempfile.TemporaryDirectory() as user_dir:
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                    with patch.object(devcode, "__file__", os.path.join(pkg_dir, "devcode.py")):
                        devcode.new_command.callback(name="myapp", base=None, edit=False)
                self.assertTrue(os.path.isdir(os.path.join(user_dir, "myapp")))

    def test_creates_template_from_explicit_base(self):
        with tempfile.TemporaryDirectory() as user_dir:
            # Create a base template in user dir
            base_path = os.path.join(user_dir, "mybase", ".devcontainer")
            os.makedirs(base_path)
            open(os.path.join(base_path, "devcontainer.json"), "w").close()

            with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                devcode.new_command.callback(name="myapp", base="mybase", edit=False)
            self.assertTrue(os.path.isdir(os.path.join(user_dir, "myapp")))

    def test_exits_if_name_already_exists(self):
        with tempfile.TemporaryDirectory() as user_dir:
            existing = os.path.join(user_dir, "myapp")
            os.makedirs(existing)
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                with self.assertRaises(SystemExit):
                    devcode.new_command.callback(name="myapp", base=None, edit=False)

    def test_exits_if_base_not_found(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            with tempfile.TemporaryDirectory() as user_dir:
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                    with patch.object(devcode, "__file__", os.path.join(pkg_dir, "devcode.py")):
                        with self.assertRaises(SystemExit):
                            devcode.new_command.callback(name="myapp", base="no-such-base", edit=False)

    def test_edit_flag_calls_do_open(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            self._setup_pkg(pkg_dir)
            with tempfile.TemporaryDirectory() as user_dir:
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                    with patch.object(devcode, "__file__", os.path.join(pkg_dir, "devcode.py")):
                        with patch.object(devcode, "_do_open") as mock_open:
                            devcode.new_command.callback(name="myapp", base=None, edit=True)
                mock_open.assert_called_once()
                self.assertEqual(mock_open.call_args.kwargs["template"], "myapp")
                self.assertEqual(mock_open.call_args.kwargs["projectpath"], os.path.join(user_dir, "myapp"))

    def test_creates_template_from_default_base(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            self._setup_pkg(pkg_dir)
            with tempfile.TemporaryDirectory() as user_dir:
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                    with patch.object(devcode, "__file__", os.path.join(pkg_dir, "devcode.py")):
                        devcode.new_command.callback(name="myapp", base=None, edit=False)
                self.assertTrue(os.path.isdir(os.path.join(user_dir, "myapp")))

    def test_base_found_in_second_search_dir(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                base = os.path.join(d2, "mybase", ".devcontainer")
                os.makedirs(base)
                open(os.path.join(base, "devcontainer.json"), "w").close()
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [d1, d2]}):
                    devcode.new_command.callback(name="myapp", base="mybase", edit=False)
                # writes to first dir
                self.assertTrue(os.path.isdir(os.path.join(d1, "myapp")))


class TestCmdEdit(unittest.TestCase):
    def _make_template(self, base_dir, name):
        root = os.path.join(base_dir, name)
        dc = os.path.join(root, ".devcontainer")
        os.makedirs(dc)
        open(os.path.join(dc, "devcontainer.json"), "w").close()
        return root

    def test_named_template_opens_root_in_code(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._make_template(d, "mytemplate")
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d]}):
                with patch("subprocess.run") as mock_run:
                    devcode.edit_command.callback(template="mytemplate")
            mock_run.assert_called_once_with(["code", root])

    def test_named_template_found_in_second_dir(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                root = self._make_template(d2, "mytemplate")
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [d1, d2]}):
                    with patch("subprocess.run") as mock_run:
                        devcode.edit_command.callback(template="mytemplate")
                mock_run.assert_called_once_with(["code", root])

    def test_named_template_not_found_exits(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d]}):
                with self.assertRaises(SystemExit):
                    devcode.edit_command.callback(template="no-such")

    def test_no_arg_is_missing_argument_error(self):
        """edit requires a template argument; omitting it should exit non-zero."""
        runner = CliRunner()
        result = runner.invoke(devcode.cli, ["edit"])
        self.assertNotEqual(result.exit_code, 0)

    def test_does_not_call_do_open(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_template(d, "mytemplate")
            with patch.object(devcode, "_load_settings", return_value={"template_sources": [d]}):
                with patch.object(devcode, "_do_open") as mock_open:
                    with patch("subprocess.run"):
                        devcode.edit_command.callback(template="mytemplate")
            mock_open.assert_not_called()


class TestTemplateNameFromConfig(unittest.TestCase):
    def test_extracts_name(self):
        path = "/home/user/.local/share/dev-code/templates/claude/.devcontainer/devcontainer.json"
        self.assertEqual(devcode._template_name_from_config(path), "claude")

    def test_fallback_on_no_devcontainer(self):
        path = "/some/arbitrary/path/devcontainer.json"
        # Should not raise; returns some string
        result = devcode._template_name_from_config(path)
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
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.ps_command.callback(show_all=False, interactive=False)
        combined = "\n".join(lines)
        self.assertIn("claude", combined)
        self.assertIn("abc123def456", combined)
        self.assertIn("#", combined)   # header row has # column
        self.assertIn("1", combined)   # row 1

    def test_no_containers_message(self):
        mock_result = MagicMock(returncode=0, stdout="")
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.ps_command.callback(show_all=False, interactive=False)
        self.assertTrue(any("no running devcontainers" in str(l) for l in lines))

    def test_docker_unavailable_exits(self):
        mock_result = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=mock_result):
            with self.assertRaises(SystemExit):
                devcode.ps_command.callback(show_all=False, interactive=False)

    def test_malformed_row_skipped(self):
        # A row with fewer than 4 fields after dropping CreatedAt should be skipped silently
        malformed = "2026-03-24 10:00:00 +0000 UTC\tabc123\t/home/user/myapp"  # only 3 fields after drop
        good = "2026-03-24 11:00:00 +0000 UTC\tbbb222\t/home/user/other\t/some/config\tUp 1 hour"
        stdout = malformed + "\n" + good + "\n"
        mock_result = MagicMock(returncode=0, stdout=stdout)
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.ps_command.callback(show_all=False, interactive=False)  # must not raise
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
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.ps_command.callback(show_all=True, interactive=False)
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
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.ps_command.callback(show_all=False, interactive=False)
        combined = "\n".join(lines)
        self.assertNotIn("aaa111", combined)   # stopped excluded
        self.assertIn("bbb222", combined)      # running shown

    def test_all_flag_empty_message(self):
        mock_result = MagicMock(returncode=0, stdout="")
        lines = []
        with patch("subprocess.run", return_value=mock_result):
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.ps_command.callback(show_all=True, interactive=False)
        self.assertTrue(any("no devcontainers" in str(l) and "running" not in str(l) for l in lines))

    def test_interactive_valid_selection_opens_container(self):
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
                with patch("devcode._do_open") as mock_open:
                    devcode.ps_command.callback(show_all=False, interactive=True)
        mock_open.assert_called_once_with(
            projectpath="/home/user/myapp",
            template="/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
            container_folder="/workspaces/myapp",
            timeout=300,
            dry_run=False,
        )

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
                with patch("devcode._do_open") as mock_open:
                    devcode.ps_command.callback(show_all=False, interactive=True)
        self.assertEqual(mock_open.call_args.kwargs["container_folder"], "/workspaces/myapp")

    def test_interactive_invalid_selection_exits(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", return_value="99"):
                with self.assertRaises(SystemExit):
                    devcode.ps_command.callback(show_all=False, interactive=True)

    def test_interactive_non_integer_exits(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "/home/user/.local/share/dev-code/templates/python/.devcontainer/devcontainer.json",
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", return_value="abc"):
                with self.assertRaises(SystemExit):
                    devcode.ps_command.callback(show_all=False, interactive=True)

    def test_interactive_missing_config_label_exits(self):
        rows = [
            ("2026-03-24 10:00:00 +0000 UTC", "abc123def456", "/home/user/myapp",
             "",   # empty config_file label
             "Up 2 hours"),
        ]
        ls_result = MagicMock(returncode=0, stdout=self._docker_output(rows))
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", return_value="1"):
                with self.assertRaises(SystemExit):
                    devcode.ps_command.callback(show_all=False, interactive=True)

    def test_interactive_empty_table_no_prompt(self):
        ls_result = MagicMock(returncode=0, stdout="")
        prompted = []
        with patch("subprocess.run", return_value=ls_result):
            with patch("builtins.input", side_effect=lambda _: prompted.append(True) or "1"):
                devcode.ps_command.callback(show_all=False, interactive=True)
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
                with patch("devcode._do_open") as mock_open:
                    devcode.ps_command.callback(show_all=True, interactive=True)
        mock_open.assert_called_once()
        self.assertEqual(mock_open.call_args.kwargs["projectpath"], "/home/user/myapp")

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
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                devcode.ps_command.callback(show_all=False, interactive=False)
        # Find data rows (skip header)
        data_rows = [l for l in lines if "older222" in l or "newer111" in l]
        self.assertEqual(len(data_rows), 2)
        # older222 must appear before newer111
        idx_older = next(i for i, l in enumerate(lines) if "older222" in l)
        idx_newer = next(i for i, l in enumerate(lines) if "newer111" in l)
        self.assertLess(idx_older, idx_newer)
        # older222 must be row #1
        self.assertIn("1", lines[idx_older])


class TestDoOpen(unittest.TestCase):
    def test_errors_on_nonexistent_projectpath(self):
        with self.assertRaises(SystemExit):
            devcode._do_open(
                projectpath="/nonexistent/path/xyz123",
                template="mytemplate",
                container_folder=None,
                timeout=300,
                dry_run=False,
            )


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
        with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
            with patch("os.path.exists", return_value=True):
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                        devcode._do_open(projectpath, template, container_folder, 300, True)
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
                with patch.object(devcode, "_load_settings", return_value={"template_sources": [user_dir]}):
                    with patch("os.path.exists", return_value=True):
                        with patch.object(devcode, "_git_repo_root", return_value=None):
                            lines = []
                            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(a[0] if a else "")):
                                devcode._do_open("/myproject", "mytemplate", None, 300, True)
        combined = "\n".join(lines)
        self.assertIn("<unset:", combined)


class TestBanner(unittest.TestCase):
    def test_help_contains_tagline(self):
        """Banner tagline appears in --help output."""
        runner = CliRunner()
        result = runner.invoke(devcode.cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("project · editor · container", result.output)


class TestListTemplateNames(unittest.TestCase):
    def _make_template(self, base_dir, name):
        p = os.path.join(base_dir, name, ".devcontainer")
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, "devcontainer.json"), "w").close()

    def test_returns_sorted_names(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_template(d, "zebra")
            self._make_template(d, "alpha")
            with patch.object(devcode, "resolve_template_search_path", return_value=[d]):
                names = devcode._list_template_names()
        self.assertEqual(names, ["alpha", "zebra"])

    def test_returns_globally_sorted_across_dirs(self):
        """Names from multiple dirs must be globally sorted, not insertion-ordered."""
        with tempfile.TemporaryDirectory() as tmp_path:
            d1 = os.path.join(tmp_path, "d1")
            d2 = os.path.join(tmp_path, "d2")
            # zebra is in d1, alpha is in d2 — insertion order would give ["zebra", "alpha"]
            for d, name in [(d1, "zebra"), (d2, "alpha")]:
                p = os.path.join(d, name, ".devcontainer")
                os.makedirs(p, exist_ok=True)
                open(os.path.join(p, "devcontainer.json"), "w").close()
            with patch.object(devcode, "resolve_template_search_path", return_value=[d1, d2]):
                result = devcode._list_template_names()
        self.assertEqual(result, ["alpha", "zebra"])

    def test_excludes_dirs_without_devcontainer_json(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_template(d, "valid")
            os.makedirs(os.path.join(d, "invalid"))  # no devcontainer.json
            with patch.object(devcode, "resolve_template_search_path", return_value=[d]):
                names = devcode._list_template_names()
        self.assertEqual(names, ["valid"])

    def test_deduplicates_across_dirs(self):
        with tempfile.TemporaryDirectory() as d1:
            with tempfile.TemporaryDirectory() as d2:
                self._make_template(d1, "shared")
                self._make_template(d2, "shared")
                self._make_template(d2, "unique")
                with patch.object(devcode, "resolve_template_search_path", return_value=[d1, d2]):
                    names = devcode._list_template_names()
        self.assertEqual(names, ["shared", "unique"])

    def test_does_not_include_builtins(self):
        with tempfile.TemporaryDirectory() as pkg_dir:
            b = os.path.join(pkg_dir, "dev_code_templates", "dev-code", ".devcontainer")
            os.makedirs(b)
            open(os.path.join(b, "devcontainer.json"), "w").close()
            with tempfile.TemporaryDirectory() as d:
                with patch.object(devcode, "resolve_template_search_path", return_value=[d]):
                    with patch.object(devcode, "__file__", os.path.join(pkg_dir, "devcode.py")):
                        names = devcode._list_template_names()
        self.assertEqual(names, [])

class TestClickCLI(unittest.TestCase):
    def test_no_args_shows_banner(self):
        runner = CliRunner()
        result = runner.invoke(devcode.cli, [])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("project · editor · container", result.output)

    def test_version_flag(self):
        runner = CliRunner()
        result = runner.invoke(devcode.cli, ["--version"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("devcode", result.output)

    def test_open_dry_run(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.makedirs("mytemplate/.devcontainer")
            with open("mytemplate/.devcontainer/devcontainer.json", "w") as f:
                f.write('{"name": "test"}')
            project = os.path.abspath(".")
            with patch.object(devcode, "_load_settings",
                               return_value={"template_sources": [os.path.abspath(".")]}):
                with patch.object(devcode, "_git_repo_root", return_value=None):
                    result = runner.invoke(
                        devcode.cli, ["open", project, "mytemplate", "--dry-run"]
                    )
        self.assertEqual(result.exit_code, 0)

    def test_list_command(self):
        runner = CliRunner()
        with patch.object(devcode, "_list_template_names", return_value=["tmpl-a", "tmpl-b"]):
            result = runner.invoke(devcode.cli, ["list"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("tmpl-a", result.output)
        self.assertIn("tmpl-b", result.output)

    def test_new_command_unknown_base_exits(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch.object(devcode, "_load_settings",
                               return_value={"template_sources": [os.path.abspath(".")]}):
                result = runner.invoke(devcode.cli, ["new", "myapp", "no-such-base"])
        self.assertNotEqual(result.exit_code, 0)

    def test_ps_no_docker_exits(self):
        runner = CliRunner()
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            result = runner.invoke(devcode.cli, ["ps"])
        self.assertNotEqual(result.exit_code, 0)


class TestCompleteTemplates(unittest.TestCase):
    def test_returns_all_when_incomplete_is_empty(self):
        with patch.object(devcode, "_list_template_names", return_value=["alpha", "beta", "gamma"]):
            result = devcode._complete_templates(None, None, "")
        names = [item.value for item in result]
        self.assertEqual(sorted(names), ["alpha", "beta", "gamma"])

    def test_filters_by_prefix(self):
        with patch.object(devcode, "_list_template_names", return_value=["alpha", "beta", "gamma"]):
            result = devcode._complete_templates(None, None, "al")
        names = [item.value for item in result]
        self.assertEqual(names, ["alpha"])

    def test_returns_empty_when_no_match(self):
        with patch.object(devcode, "_list_template_names", return_value=["alpha", "beta"]):
            result = devcode._complete_templates(None, None, "z")
        self.assertEqual(result, [])
