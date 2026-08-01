import tempfile
import unittest
from pathlib import Path

from zxcode.mcp.config import (
    DEFAULT_CALL_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_IDLE_TIMEOUT,
    ConfigError,
    load_config,
)


def write_config(root: Path, content: str) -> Path:
    path = root / "zxcode-servers.toml"
    path.write_text(content, encoding="utf-8")
    return path


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_yields_empty_config(self):
        config = load_config(self.root)
        self.assertEqual(config.servers, ())

    def test_parses_stdio_and_http_servers(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "local"
            transport = "stdio"
            command = ["python", "-m", "server"]
            env = { TOKEN = "${FAKE_TOKEN}" }

            [[servers]]
            name = "remote"
            transport = "http"
            url = "https://example.test/mcp"
            headers = { Authorization = "Bearer ${FAKE_TOKEN}" }
            call_timeout_seconds = 30
            trusted = true
            read_only_tools = ["query"]
            disabled_tools = ["delete"]
            """,
        )
        config = load_config(self.root, {"FAKE_TOKEN": "secret-value"})
        self.assertEqual(len(config.servers), 2)
        local, remote = config.servers
        self.assertEqual(local.transport, "stdio")
        self.assertEqual(local.command, ("python", "-m", "server"))
        self.assertEqual(local.env, {"TOKEN": "secret-value"})
        self.assertEqual(local.cwd, self.root.resolve())
        self.assertEqual(remote.transport, "http")
        self.assertEqual(remote.headers, {"Authorization": "Bearer secret-value"})
        self.assertEqual(remote.call_timeout_seconds, 30)
        self.assertTrue(remote.trusted)
        self.assertEqual(remote.read_only_tools, ("query",))
        self.assertEqual(remote.disabled_tools, ("delete",))

    def test_defaults_applied(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "s"
            transport = "stdio"
            command = "server.exe"
            """,
        )
        server = load_config(self.root).servers[0]
        self.assertEqual(server.connect_timeout_seconds, DEFAULT_CONNECT_TIMEOUT)
        self.assertEqual(server.call_timeout_seconds, DEFAULT_CALL_TIMEOUT)
        self.assertEqual(server.idle_timeout_seconds, DEFAULT_IDLE_TIMEOUT)
        self.assertFalse(server.trusted)

    def test_missing_env_variable_raises(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "s"
            transport = "http"
            url = "http://localhost:1"
            headers = { X = "${MISSING_VAR}" }
            """,
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.root, {})
        self.assertIn("MISSING_VAR", str(ctx.exception))

    def test_invalid_server_name_raises(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "bad name"
            transport = "stdio"
            command = "x"
            """,
        )
        with self.assertRaises(ConfigError):
            load_config(self.root)

    def test_unknown_transport_raises(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "s"
            transport = "sse"
            """,
        )
        with self.assertRaises(ConfigError):
            load_config(self.root)

    def test_stdio_without_command_raises(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "s"
            transport = "stdio"
            """,
        )
        with self.assertRaises(ConfigError):
            load_config(self.root)

    def test_http_without_url_raises(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "s"
            transport = "http"
            """,
        )
        with self.assertRaises(ConfigError):
            load_config(self.root)

    def test_bad_url_scheme_raises(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "s"
            transport = "http"
            url = "ftp://example.test"
            """,
        )
        with self.assertRaises(ConfigError):
            load_config(self.root)

    def test_duplicate_names_raise(self):
        write_config(
            self.root,
            """
            [[servers]]
            name = "s"
            transport = "stdio"
            command = "x"

            [[servers]]
            name = "s"
            transport = "http"
            url = "http://localhost:1"
            """,
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.root)
        self.assertIn("重复", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
