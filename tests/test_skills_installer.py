import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

from zxcode.skills.installer import (
    SkillInstallError,
    _validated_files,
    install_skill,
    parse_skills_url,
)
from zxcode.skills.loader import scan_skills
from zxcode.tools import ReadFile, ToolRegistry


URL = "https://www.skills.sh/anthropics/skills/frontend-design"
ENTRY = (
    "---\n"
    "name: demo\n"
    "description: demo skill\n"
    "mode: shared\n"
    "---\n"
    "body\n"
)


def payload(*files, **overrides):
    data = {
        "files": [
            {"path": path, "contents": contents} for path, contents in files
        ]
    }
    data.update(overrides)
    return json.dumps(data).encode("utf-8")


class FakeOpener:
    def __init__(self, body: bytes, error: Exception | None = None):
        self.body = body
        self.error = error
        self.urls = []

    def open(self, url, timeout=None):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.body)


class ParseSkillsUrlTests(unittest.TestCase):
    def test_parses_example_url(self):
        self.assertEqual(
            parse_skills_url(URL),
            ("anthropics", "skills", "frontend-design"),
        )

    def test_accepts_bare_host_and_trailing_slash(self):
        self.assertEqual(
            parse_skills_url("https://skills.sh/anthropics/skills/frontend-design/"),
            ("anthropics", "skills", "frontend-design"),
        )

    def test_rejects_other_hosts_and_missing_or_extra_segments(self):
        for url in (
            "https://example.com/anthropics/skills/frontend-design",
            "https://www.skills.sh/anthropics/skills",
            "https://www.skills.sh/anthropics/skills/a/b",
        ):
            with self.subTest(url=url):
                with self.assertRaises(SkillInstallError):
                    parse_skills_url(url)

    def test_rejects_dot_and_unsafe_skill_segments(self):
        for url in (
            "https://www.skills.sh/owner/repo/.",
            "https://www.skills.sh/owner/repo/..",
            "https://www.skills.sh/owner/repo/bad name",
        ):
            with self.subTest(url=url):
                with self.assertRaises(SkillInstallError):
                    parse_skills_url(url)


class InstallSkillTests(unittest.TestCase):
    def test_install_writes_entry_and_extra_files_then_scans(self):
        with tempfile.TemporaryDirectory() as directory:
            opener = FakeOpener(
                payload(
                    ("SKILL.md", ENTRY),
                    ("LICENSE.txt", "MIT"),
                    ("examples/use.md", "example"),
                )
            )

            target = install_skill(Path(directory), URL, opener=opener)

            self.assertEqual(
                target,
                Path(directory) / ".zxcode" / "skills" / "frontend-design",
            )
            self.assertTrue((target / "skill.md").exists())
            self.assertEqual(
                (target / "skill.md").read_text(encoding="utf-8"), ENTRY
            )
            self.assertEqual(
                (target / "examples" / "use.md").read_text(encoding="utf-8"),
                "example",
            )
            self.assertEqual(
                opener.urls[0],
                "https://skills.sh/api/download/anthropics/skills/frontend-design",
            )
            index = scan_skills(
                Path(directory),
                Path(directory) / "user",
                Path(directory) / "builtin",
                ToolRegistry([ReadFile()]),
            )
            self.assertIn("demo", index.by_name)

    def test_install_rejects_path_traversal_without_writing(self):
        bad = payload(("SKILL.md", ENTRY), ("../evil.txt", "x"))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillInstallError):
                install_skill(Path(directory), URL, opener=FakeOpener(bad))

            self.assertFalse((Path(directory) / ".zxcode").exists())

    def test_install_rejects_disallowed_extension(self):
        bad = payload(("SKILL.md", ENTRY), ("hook.exe", "MZ"))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillInstallError):
                install_skill(Path(directory), URL, opener=FakeOpener(bad))

            self.assertFalse((Path(directory) / ".zxcode").exists())

    def test_install_rejects_payload_without_entry_file(self):
        bad = payload(("LICENSE.txt", "MIT"))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillInstallError):
                install_skill(Path(directory), URL, opener=FakeOpener(bad))

            self.assertFalse((Path(directory) / ".zxcode").exists())

    def test_install_rolls_back_invalid_frontmatter(self):
        bad_entry = ENTRY.replace("mode: shared", "mode: nope")
        bad = payload(("SKILL.md", bad_entry))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillInstallError):
                install_skill(Path(directory), URL, opener=FakeOpener(bad))

            self.assertFalse((Path(directory) / ".zxcode").exists())

    def test_install_injects_default_mode_when_missing(self):
        no_mode = ENTRY.replace("mode: shared\n", "")
        with tempfile.TemporaryDirectory() as directory:
            target = install_skill(
                Path(directory),
                URL,
                opener=FakeOpener(payload(("SKILL.md", no_mode))),
            )

            self.assertIn(
                "mode: shared",
                (target / "skill.md").read_text(encoding="utf-8"),
            )
            index = scan_skills(
                Path(directory),
                Path(directory) / "user",
                Path(directory) / "builtin",
                ToolRegistry([ReadFile()]),
            )
            self.assertEqual(index.by_name["demo"].mode, "shared")

    def test_install_refuses_to_overwrite_existing_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / ".zxcode" / "skills" / "frontend-design"
            dest.mkdir(parents=True)
            (dest / "keep.md").write_text("keep", encoding="utf-8")

            with self.assertRaises(SkillInstallError) as caught:
                install_skill(
                    Path(directory),
                    URL,
                    opener=FakeOpener(payload(("SKILL.md", ENTRY))),
                )

            self.assertIn("already exists", str(caught.exception))
            self.assertTrue((dest / "keep.md").exists())

    def test_download_failure_is_reported(self):
        opener = FakeOpener(b"", urllib.error.URLError("offline"))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillInstallError):
                install_skill(Path(directory), URL, opener=opener)

    def test_invalid_json_response_is_rejected(self):
        opener = FakeOpener(b"not json")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillInstallError):
                install_skill(Path(directory), URL, opener=opener)

    @unittest.skipUnless(os.name == "nt", "Windows drive-relative paths")
    def test_validated_files_rejects_drive_qualified_path(self):
        with self.assertRaises(SkillInstallError):
            _validated_files(
                {
                    "files": [
                        {"path": "SKILL.md", "contents": ENTRY},
                        {"path": "Q:evil.txt", "contents": "x"},
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
