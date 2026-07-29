import io
import unittest
from unittest.mock import patch

from mewcode.client import ConfigError, Settings
from mewcode.__main__ import main


class MainTests(unittest.TestCase):
    @patch("mewcode.__main__.MewCodeApp")
    @patch("mewcode.__main__.Settings.from_env", side_effect=ConfigError("missing"))
    def test_missing_config_prints_error_and_returns_two(self, _, app):
        stderr = io.StringIO()

        with patch("sys.stderr", stderr):
            result = main()

        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue().strip(), "missing")
        app.assert_not_called()

    @patch("mewcode.__main__.MewCodeApp")
    @patch("mewcode.__main__.Settings.from_env")
    def test_valid_config_runs_app(self, from_env, app):
        settings = Settings("secret", "https://example.test/v1", "model-a")
        from_env.return_value = settings

        result = main()

        self.assertEqual(result, 0)
        app.assert_called_once_with(settings)
        app.return_value.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
