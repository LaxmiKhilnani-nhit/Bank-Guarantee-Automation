"""Checks for the small local ``.env`` loader in ``bgauto.config``."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bgauto import config


class DotEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".env"

    def test_reads_keys_comments_and_quotes(self) -> None:
        self.path.write_text(
            "\n".join(
                [
                    "# comment",
                    "",
                    "DATALAB_API_KEY=secret-value",
                    'BG_DATALAB_MODE="accurate"',
                    "export BG_WEB_PORT=8010",
                    "BROKEN_LINE",
                ]
            ),
            encoding="utf-8",
        )
        clean = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DATALAB_API_KEY", "BG_DATALAB_MODE", "BG_WEB_PORT"}
        }
        with patch.dict(os.environ, clean, clear=True):
            config._load_dotenv(self.path)
            self.assertEqual(os.environ["DATALAB_API_KEY"], "secret-value")
            self.assertEqual(os.environ["BG_DATALAB_MODE"], "accurate")
            self.assertEqual(os.environ["BG_WEB_PORT"], "8010")

    def test_process_environment_wins(self) -> None:
        self.path.write_text("DATALAB_API_KEY=from-file\n", encoding="utf-8")
        with patch.dict(os.environ, {"DATALAB_API_KEY": "from-env"}, clear=False):
            config._load_dotenv(self.path)
            self.assertEqual(os.environ["DATALAB_API_KEY"], "from-env")

    def test_missing_file_is_ignored(self) -> None:
        config._load_dotenv(Path(self._tmp.name) / "absent.env")


class PipelineConfigTests(unittest.TestCase):
    def test_pipeline_defaults_are_sane(self) -> None:
        self.assertTrue(config.DATALAB_PIPELINE_ID.startswith("pl_"))
        self.assertTrue(
            config.DATALAB_PIPELINE_VERSION is None
            or isinstance(config.DATALAB_PIPELINE_VERSION, int)
        )
        self.assertIn(
            config.DATALAB_EXTRACTION_MODE, config.DATALAB_PIPELINE_MODES
        )


if __name__ == "__main__":
    unittest.main()
