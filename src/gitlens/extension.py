from __future__ import annotations

import os
import subprocess
import time
import typing
from dataclasses import dataclass, field
from pathlib import Path

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


@dataclass
class BlameCommit:
    """Represents commit metadata associated with one or more blamed lines."""

    sha: str
    author: str = "Unknown"
    author_mail: str = ""
    author_time: int = 0
    author_tz: str = "+0000"
    committer: str = "Unknown"
    committer_mail: str = ""
    committer_time: int = 0
    committer_tz: str = "+0000"
    summary: str = ""
    previous_sha: str | None = None
    filename: str = ""


@dataclass
class BlameLine:
    """Represents a single blamed source line with its commit metadata."""

    line_no: int
    orig_line_no: int
    commit: BlameCommit
    content: str = ""


@dataclass
class CommitDetails:
    """Detailed inspection of a specific Git commit."""

    sha: str
    author: str
    author_mail: str
    timestamp: int
    relative_time: str
    summary: str
    body: str = ""
    files_changed: list[str] = field(default_factory=list)
    stat: str = ""


def calculate_relative_time(epoch_secs: int, now_secs: int | None = None) -> str:
    """Calculate a human-readable relative time string from an epoch timestamp."""
    if now_secs is None:
        now_secs = int(time.time())

    diff = max(0, now_secs - int(epoch_secs))
    if diff < 60:
        return "just now"
    if diff < 120:
        return "1 minute ago"
    if diff < 3600:
        return f"{diff // 60} minutes ago"
    if diff < 7200:
        return "1 hour ago"
    if diff < 86400:
        return f"{diff // 3600} hours ago"
    if diff < 172800:
        return "1 day ago"
    if diff < 2592000:
        return f"{diff // 86400} days ago"
    if diff < 5184000:
        return "1 month ago"
    if diff < 31536000:
        return f"{diff // 2592000} months ago"
    if diff < 63072000:
        return "1 year ago"
    return f"{diff // 31536000} years ago"


class GitLens(Extension):
    """Git supercharged — inline blame annotations, file history, and commit navigation."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.blame_enabled: bool = True
        self.cached_blame: dict[str, list[BlameLine]] = {}
        self.last_commit_details: CommitDetails | None = None
        self.last_blame_line: BlameLine | None = None
        self.statusbar_button = None

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("GitLens extension initialised.")

    def _run_git(self, args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
        """Execute a git command safely and return (returncode, stdout, stderr)."""
        try:
            res = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                cwd=cwd,
                check=False,
            )
            return res.returncode, res.stdout, res.stderr
        except FileNotFoundError:
            return -1, "", "git executable not found on PATH"
        except Exception as exc:
            return -1, "", str(exc)

    def format_blame_annotation(self, author: str, relative_date: str, summary: str) -> str:
        """Format an inline git blame annotation string."""
        return f"{author}, {relative_date} • {summary}"

    def parse_porcelain_blame(self, raw_blame: str) -> list[BlameLine]:
        """Parse raw porcelain git blame output into structured BlameLine records."""
        if not raw_blame or not raw_blame.strip():
            return []

        commits_by_sha: dict[str, BlameCommit] = {}
        lines: list[BlameLine] = []

        current_sha: str | None = None
        orig_lineno: int = 0
        final_lineno: int = 0

        for raw_line in raw_blame.splitlines():
            if raw_line.startswith("\t"):
                # Code content line - finishes current blame record
                content = raw_line[1:]
                commit = commits_by_sha.get(current_sha) or BlameCommit(sha=current_sha or "0000000")
                lines.append(
                    BlameLine(
                        line_no=final_lineno,
                        orig_line_no=orig_lineno,
                        commit=commit,
                        content=content,
                    )
                )
                continue

            parts = raw_line.split()
            if not parts:
                continue

            first_token = parts[0]
            # Check for header: <sha> <orig-lineno> <final-lineno> [<num-lines>]
            if len(first_token) == 40 and all(c in "0123456789abcdefABCDEF" for c in first_token):
                current_sha = first_token
                orig_lineno = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                final_lineno = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else orig_lineno
                if current_sha not in commits_by_sha:
                    commits_by_sha[current_sha] = BlameCommit(sha=current_sha)
                continue

            if not current_sha:
                continue

            current_commit = commits_by_sha[current_sha]
            key = parts[0]
            value = raw_line[len(key) :].strip()

            if key == "author":
                current_commit.author = value
            elif key == "author-mail":
                current_commit.author_mail = value.strip("<>")
            elif key == "author-time" and value.isdigit():
                current_commit.author_time = int(value)
            elif key == "author-tz":
                current_commit.author_tz = value
            elif key == "committer":
                current_commit.committer = value
            elif key == "committer-mail":
                current_commit.committer_mail = value.strip("<>")
            elif key == "committer-time" and value.isdigit():
                current_commit.committer_time = int(value)
            elif key == "committer-tz":
                current_commit.committer_tz = value
            elif key == "summary":
                current_commit.summary = value
            elif key == "previous":
                current_commit.previous_sha = parts[1] if len(parts) > 1 else None
            elif key == "filename":
                current_commit.filename = value

        return lines

    def parse_blame_output(self, raw_blame: str) -> list[dict]:
        """Parse git blame output into structured records.

        Supports both standard porcelain format and legacy whitespace-separated format.
        """
        if not raw_blame or not raw_blame.strip():
            return []

        # If porcelain format (contains tab prefix lines or 40-char hashes with author headers)
        if any(line.startswith("\t") or line.startswith("author ") for line in raw_blame.splitlines()):
            parsed_lines = self.parse_porcelain_blame(raw_blame)
            records = []
            for item in parsed_lines:
                rel_time = calculate_relative_time(item.commit.author_time) if item.commit.author_time else ""
                records.append(
                    {
                        "sha": item.commit.sha,
                        "author": item.commit.author,
                        "date": rel_time or str(item.commit.author_time),
                        "summary": item.commit.summary,
                        "line": item.line_no,
                        "content": item.content,
                    }
                )
            return records

        # Fallback simple whitespace line parser
        records = []
        for line in raw_blame.splitlines():
            parts = line.strip().split(maxsplit=3)
            if len(parts) >= 4:
                records.append(
                    {
                        "sha": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "summary": parts[3],
                    }
                )
        return records

    def get_file_blame(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        cwd: str | None = None,
    ) -> list[BlameLine]:
        """Fetch and parse git blame for a specific file or line range."""
        args = ["blame", "--porcelain"]
        if start_line is not None and end_line is not None:
            args.extend(["-L", f"{start_line},{end_line}"])
        elif start_line is not None:
            args.extend(["-L", f"{start_line},{start_line}"])
        args.append(str(file_path))

        work_dir = cwd or (str(Path(file_path).parent) if os.path.exists(file_path) else None)
        code, stdout, stderr = self._run_git(args, cwd=work_dir)
        if code != 0:
            if hasattr(self.api, "logger") and self.api.logger:
                self.api.logger.warning(f"GitLens blame failed for {file_path}: {stderr.strip()}")
            return []

        blame_lines = self.parse_porcelain_blame(stdout)
        self.cached_blame[str(file_path)] = blame_lines
        return blame_lines

    def get_blame_for_line(
        self,
        file_path: str,
        line_no: int,
        cwd: str | None = None,
    ) -> BlameLine | None:
        """Lookup blame details for a specific line number."""
        if str(file_path) in self.cached_blame:
            for item in self.cached_blame[str(file_path)]:
                if item.line_no == line_no:
                    self.last_blame_line = item
                    return item
            return None

        # Query directly for the line range
        lines = self.get_file_blame(file_path, start_line=line_no, end_line=line_no, cwd=cwd)
        for item in lines:
            if item.line_no == line_no:
                self.last_blame_line = item
                return item
        return None

    def get_commit_details(self, sha: str, cwd: str | None = None) -> CommitDetails | None:
        """Query and parse detailed commit information using git show."""
        if not sha or sha == "0000000000000000000000000000000000000000":
            return None

        fmt = "%H%x00%an%x00%ae%x00%at%x00%s%x00%b%x00"
        args = ["show", "--stat", f"--format={fmt}", sha]
        code, stdout, stderr = self._run_git(args, cwd=cwd)
        if code != 0:
            return None

        parts = stdout.split("\x00")
        if len(parts) < 6:
            return None

        commit_sha = parts[0].strip()
        author = parts[1].strip()
        author_mail = parts[2].strip()
        timestamp = int(parts[3].strip()) if parts[3].strip().isdigit() else 0
        summary = parts[4].strip()
        body = parts[5].strip()
        stat = parts[6].strip() if len(parts) > 6 else ""

        # Parse changed files from stat lines
        files_changed: list[str] = []
        for line in stat.splitlines():
            line_str = line.strip()
            if "|" in line_str:
                fname = line_str.split("|")[0].strip()
                if fname:
                    files_changed.append(fname)

        details = CommitDetails(
            sha=commit_sha,
            author=author,
            author_mail=author_mail,
            timestamp=timestamp,
            relative_time=calculate_relative_time(timestamp),
            summary=summary,
            body=body,
            files_changed=files_changed,
            stat=stat,
        )
        self.last_commit_details = details
        return details

    def get_file_history(
        self,
        file_path: str,
        max_commits: int = 50,
        cwd: str | None = None,
    ) -> list[dict]:
        """Query git commit history for a file."""
        fmt = "%H%x00%an%x00%ae%x00%at%x00%s"
        args = ["log", f"--format={fmt}", f"-n{max_commits}", "--follow", "--", str(file_path)]

        work_dir = cwd or (str(Path(file_path).parent) if os.path.exists(file_path) else None)
        code, stdout, stderr = self._run_git(args, cwd=work_dir)
        if code != 0:
            return []

        commits = []
        for line in stdout.splitlines():
            parts = line.split("\x00")
            if len(parts) >= 5:
                ts = int(parts[3]) if parts[3].isdigit() else 0
                commits.append(
                    {
                        "sha": parts[0],
                        "author": parts[1],
                        "author_mail": parts[2],
                        "timestamp": ts,
                        "relative_time": calculate_relative_time(ts),
                        "summary": parts[4],
                    }
                )
        return commits

    def get_line_history(
        self,
        file_path: str,
        line_no: int,
        cwd: str | None = None,
    ) -> list[dict]:
        """Query git commit history for a specific line number range."""
        fmt = "%H%x00%an%x00%ae%x00%at%x00%s"
        args = ["log", f"-L{line_no},{line_no}:{file_path}", f"--format={fmt}", "-n20"]

        work_dir = cwd or (str(Path(file_path).parent) if os.path.exists(file_path) else None)
        code, stdout, stderr = self._run_git(args, cwd=work_dir)
        if code != 0:
            return []

        commits = []
        for line in stdout.splitlines():
            parts = line.split("\x00")
            if len(parts) >= 5:
                ts = int(parts[3]) if parts[3].isdigit() else 0
                commits.append(
                    {
                        "sha": parts[0],
                        "author": parts[1],
                        "author_mail": parts[2],
                        "timestamp": ts,
                        "relative_time": calculate_relative_time(ts),
                        "summary": parts[4],
                    }
                )
        return commits

    def update_cursor_blame(self, file_path: str, line_no: int) -> str:
        """Update active line blame annotation and statusbar."""
        if not self.blame_enabled:
            if self.statusbar_button and hasattr(self.statusbar_button, "set_text"):
                self.statusbar_button.set_text("Blame: Disabled")
            return ""

        blame_line = self.get_blame_for_line(file_path, line_no)
        if not blame_line:
            if self.statusbar_button and hasattr(self.statusbar_button, "set_text"):
                self.statusbar_button.set_text("Git: Uncommitted changes")
            return ""

        rel_time = calculate_relative_time(blame_line.commit.author_time)
        annotation = self.format_blame_annotation(
            blame_line.commit.author, rel_time, blame_line.commit.summary
        )
        if self.statusbar_button and hasattr(self.statusbar_button, "set_text"):
            short_sha = blame_line.commit.sha[:7]
            self.statusbar_button.set_text(f"{short_sha} ({blame_line.commit.author}, {rel_time})")
        return annotation

    def toggle_blame(self) -> bool:
        """Toggle inline blame annotations on or off."""
        self.blame_enabled = not self.blame_enabled
        status_msg = "enabled" if self.blame_enabled else "disabled"
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(f"GitLens blame annotations {status_msg}.")
        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info(f"GitLens blame annotations {status_msg}.")
        return self.blame_enabled

    def show_commit_details(self) -> CommitDetails | None:
        """Display details of the commit for the currently active line."""
        if self.last_blame_line:
            details = self.get_commit_details(self.last_blame_line.commit.sha)
            if details and hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(
                    f"Commit {details.sha[:8]} by {details.author} ({details.relative_time}): {details.summary}"
                )
            return details
        return None

    def copy_commit_sha(self) -> str | None:
        """Copy the commit SHA of the current line to clipboard."""
        if self.last_blame_line:
            sha = self.last_blame_line.commit.sha
            if hasattr(self.api, "base") and hasattr(self.api.base, "clipboard_clear"):
                try:
                    self.api.base.clipboard_clear()
                    self.api.base.clipboard_append(sha)
                except Exception:
                    pass
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(f"Copied commit SHA: {sha[:8]}")
            return sha
        return None

    def show_file_history(self) -> list[dict]:
        """Fetch and show file history for active file."""
        active_editor = getattr(self.api.editors, "active_editor", None)
        path = getattr(active_editor, "path", None)
        if path:
            commits = self.get_file_history(path)
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(f"Loaded {len(commits)} commits for {Path(path).name}")
            return commits
        return []

    def install(self) -> None:
        """Register GitLens commands and UI contributions."""
        if hasattr(self.api, "commands") and hasattr(self.api.commands, "register_command"):
            self.api.commands.register_command("gitlens.toggle_blame", self.toggle_blame)
            self.api.commands.register_command("gitlens.show_commit_details", self.show_commit_details)
            self.api.commands.register_command("gitlens.copy_commit_sha", self.copy_commit_sha)
            self.api.commands.register_command("gitlens.show_file_history", self.show_file_history)

        if hasattr(self.api, "statusbar") and hasattr(self.api.statusbar, "add_button"):
            try:
                self.statusbar_button = self.api.statusbar.add_button(
                    text="GitLens",
                    icon="git-commit",
                    callback=self.show_commit_details,
                    description="GitLens inline blame and commit details",
                )
            except Exception:
                pass

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("GitLens extension installed.")
