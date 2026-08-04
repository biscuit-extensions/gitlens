from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import tomllib

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gitlens import setup
from src.gitlens.extension import (
    BlameCommit,
    BlameLine,
    CommitDetails,
    GitLens,
    calculate_relative_time,
)

SAMPLE_PORCELAIN = """\
c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0 1 1 2
author Alice Engineer
author-mail <alice@example.com>
author-time 1700000000
author-tz +0000
committer Alice Engineer
committer-mail <alice@example.com>
committer-time 1700000000
committer-tz +0000
summary Initial commit for feature
filename test_sample.py
\tdef hello_world():
c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0 2 2
filename test_sample.py
\t    return "hello"
d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1 1 3 1
author Bob Architect
author-mail <bob@example.com>
author-time 1700086400
author-tz -0500
committer Bob Architect
committer-mail <bob@example.com>
committer-time 1700086400
committer-tz -0500
summary Add main runner
previous 0000000000000000000000000000000000000000 test_sample.py
filename test_sample.py
\tprint(hello_world())
"""


class TestGitLensExtension:
    def test_extension_setup_and_registration(self, app_instance) -> None:
        setup(app_instance.api)
        # Should be registered under gitlens
        assert app_instance.api is not None

    def test_manifest_schema_and_dependencies(self) -> None:
        toml_path = Path(__file__).parent.parent / "extension.toml"
        assert toml_path.exists(), "extension.toml must exist"
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        assert data["extension"]["id"] == "gitlens"
        assert data["extension"]["version"] == "0.2.0"
        assert data["extension"]["author"] == "tomlin7"
        assert "dependencies" in data
        assert "git" in data["dependencies"]["tools"]
        assert "contributes" in data
        assert len(data["contributes"]["commands"]) >= 4

    def test_calculate_relative_time(self) -> None:
        now = 1700000000
        assert calculate_relative_time(now - 30, now_secs=now) == "just now"
        assert calculate_relative_time(now - 90, now_secs=now) == "1 minute ago"
        assert calculate_relative_time(now - 600, now_secs=now) == "10 minutes ago"
        assert calculate_relative_time(now - 3700, now_secs=now) == "1 hour ago"
        assert calculate_relative_time(now - 14400, now_secs=now) == "4 hours ago"
        assert calculate_relative_time(now - 90000, now_secs=now) == "1 day ago"
        assert calculate_relative_time(now - 86400 * 5, now_secs=now) == "5 days ago"
        assert calculate_relative_time(now - 86400 * 35, now_secs=now) == "1 month ago"
        assert calculate_relative_time(now - 86400 * 90, now_secs=now) == "3 months ago"
        assert calculate_relative_time(now - 86400 * 400, now_secs=now) == "1 year ago"
        assert calculate_relative_time(now - 86400 * 800, now_secs=now) == "2 years ago"

    def test_format_blame_annotation(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        ann = ext.format_blame_annotation("Alice", "2 days ago", "Fix parser bug")
        assert ann == "Alice, 2 days ago • Fix parser bug"

    def test_parse_porcelain_blame_multi_block(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        lines = ext.parse_porcelain_blame(SAMPLE_PORCELAIN)
        assert len(lines) == 3

        # Line 1
        assert lines[0].line_no == 1
        assert lines[0].content == "def hello_world():"
        assert lines[0].commit.author == "Alice Engineer"
        assert lines[0].commit.author_mail == "alice@example.com"
        assert lines[0].commit.summary == "Initial commit for feature"
        assert lines[0].commit.author_time == 1700000000

        # Line 2: reused commit cache
        assert lines[1].line_no == 2
        assert lines[1].content == "    return \"hello\""
        assert lines[1].commit.sha == "c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0"
        assert lines[1].commit.author == "Alice Engineer"

        # Line 3: second commit
        assert lines[2].line_no == 3
        assert lines[2].content == "print(hello_world())"
        assert lines[2].commit.author == "Bob Architect"
        assert lines[2].commit.author_tz == "-0500"

    def test_parse_porcelain_empty_or_whitespace(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        assert ext.parse_porcelain_blame("") == []
        assert ext.parse_porcelain_blame("   \n\n  ") == []

    def test_parse_blame_output_compatibility(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        # Legacy format test
        legacy_raw = "1a2b3c Alice 2026-10-18 Initial commit"
        parsed = ext.parse_blame_output(legacy_raw)
        assert len(parsed) == 1
        assert parsed[0]["sha"] == "1a2b3c"
        assert parsed[0]["author"] == "Alice"

        # Porcelain format via parse_blame_output
        parsed_porcelain = ext.parse_blame_output(SAMPLE_PORCELAIN)
        assert len(parsed_porcelain) == 3
        assert parsed_porcelain[0]["author"] == "Alice Engineer"
        assert parsed_porcelain[0]["content"] == "def hello_world():"

    def test_get_commit_details_parsing(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        mock_show = (
            "c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0\x00"
            "Alice Engineer\x00"
            "alice@example.com\x00"
            "1700000000\x00"
            "Fix parser crash on empty file\x00"
            "Detailed commit message body explaining the root cause.\x00"
            " src/parser.py | 10 +++++-----\n tests/test_parser.py | 5 ++++\n 2 files changed, 9 insertions(+), 6 deletions(-)"
        )

        with patch.object(ext, "_run_git", return_value=(0, mock_show, "")):
            details = ext.get_commit_details("c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0")
            assert details is not None
            assert details.sha == "c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0"
            assert details.author == "Alice Engineer"
            assert details.author_mail == "alice@example.com"
            assert details.summary == "Fix parser crash on empty file"
            assert "Detailed commit message body" in details.body
            assert "src/parser.py" in details.files_changed
            assert "tests/test_parser.py" in details.files_changed

    def test_get_commit_details_zero_sha_or_error(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        assert ext.get_commit_details("0000000000000000000000000000000000000000") is None
        assert ext.get_commit_details("") is None

        with patch.object(ext, "_run_git", return_value=(128, "", "fatal: bad object")):
            assert ext.get_commit_details("1234567") is None

    def test_get_file_blame_and_lookup_by_line(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        with patch.object(ext, "_run_git", return_value=(0, SAMPLE_PORCELAIN, "")):
            lines = ext.get_file_blame("test_sample.py")
            assert len(lines) == 3
            # Check caching
            assert "test_sample.py" in ext.cached_blame

            # Lookup specific line
            line_2 = ext.get_blame_for_line("test_sample.py", 2)
            assert line_2 is not None
            assert line_2.line_no == 2
            assert line_2.commit.author == "Alice Engineer"

            line_missing = ext.get_blame_for_line("test_sample.py", 999)
            assert line_missing is None

    def test_get_file_history_and_line_history(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        mock_log = (
            "c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0\x00Alice\x00alice@example.com\x001700000000\x00Fix bug\n"
            "d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1\x00Bob\x00bob@example.com\x001699000000\x00Initial\n"
        )
        with patch.object(ext, "_run_git", return_value=(0, mock_log, "")):
            history = ext.get_file_history("sample.py")
            assert len(history) == 2
            assert history[0]["sha"] == "c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0"
            assert history[0]["author"] == "Alice"
            assert history[1]["author"] == "Bob"

            line_history = ext.get_line_history("sample.py", 1)
            assert len(line_history) == 2

    def test_update_cursor_blame_and_statusbar(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        ext.install()
        mock_btn = MagicMock()
        ext.statusbar_button = mock_btn

        with patch.object(ext, "_run_git", return_value=(0, SAMPLE_PORCELAIN, "")):
            annotation = ext.update_cursor_blame("test_sample.py", 1)
            assert "Alice Engineer" in annotation
            mock_btn.set_text.assert_called()

        # Toggle blame off
        ext.toggle_blame()
        assert ext.blame_enabled is False
        disabled_ann = ext.update_cursor_blame("test_sample.py", 1)
        assert disabled_ann == ""
        mock_btn.set_text.assert_called_with("Blame: Disabled")

    def test_ui_commands_execution(self, app_instance) -> None:
        ext = GitLens(app_instance.api)
        ext.install()

        # Fake last blame line
        commit = BlameCommit(sha="c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0", author="Alice", summary="Initial")
        ext.last_blame_line = BlameLine(line_no=1, orig_line_no=1, commit=commit, content="code")

        # Copy commit sha
        sha = ext.copy_commit_sha()
        assert sha == "c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0"

        # Show commit details
        with patch.object(ext, "get_commit_details") as mock_details:
            mock_details.return_value = CommitDetails(
                sha="c1a2b3c4",
                author="Alice",
                author_mail="alice@example.com",
                timestamp=1700000000,
                relative_time="2 days ago",
                summary="Initial",
            )
            res = ext.show_commit_details()
            assert res is not None
            assert res.author == "Alice"
