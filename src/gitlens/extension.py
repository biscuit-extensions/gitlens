from __future__ import annotations

import typing

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class GitLens(Extension):
    """Git supercharged — inline blame annotations, file history, and commit navigation."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.api.logger.info("GitLens extension initialised.")

    def format_blame_annotation(self, author: str, relative_date: str, summary: str) -> str:
        """Format an inline git blame annotation string."""
        return f"{author}, {relative_date} • {summary}"

    def parse_blame_output(self, raw_blame: str) -> list[dict]:
        """Parse raw porcelain git blame output into structured records."""
        records = []
        for line in raw_blame.splitlines():
            parts = line.strip().split(maxsplit=3)
            if len(parts) >= 4:
                records.append({
                    "sha": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "summary": parts[3]
                })
        return records

    def install(self) -> None:
        self.api.logger.info("GitLens extension installed.")
