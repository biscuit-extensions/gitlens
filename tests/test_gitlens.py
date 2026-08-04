from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gitlens import setup
from src.gitlens.extension import GitLens


class TestGitLensExtension:
    def test_extension_setup(self, app_instance) -> None:
        setup(app_instance.api)
        ext = GitLens(app_instance.api)
        ann = ext.format_blame_annotation("Alice", "2 days ago", "Fix parser bug")
        assert "Alice" in ann and "Fix parser bug" in ann
        parsed = ext.parse_blame_output("1a2b3c Alice 2026-10-18 Initial commit")
        assert len(parsed) == 1
        assert parsed[0]["sha"] == "1a2b3c"
