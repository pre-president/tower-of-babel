"""Local Git workspace snapshot utility for high-fidelity model handoffs."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitSnapshot:
    is_git_repo: bool
    git_branch: str | None
    git_commit: str | None
    status_short: str
    git_diff_patch: str | None
    oversized_patch_ref: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_git_repo": self.is_git_repo,
            "git_branch": self.git_branch,
            "git_commit": self.git_commit,
            "status_short": self.status_short,
            "git_diff_patch": self.git_diff_patch,
            "oversized_patch_ref": self.oversized_patch_ref,
        }


def _run_git(repo: Path, args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return proc.returncode, proc.stdout
    except Exception:
        return 1, ""


def capture_git_snapshot(
    repo_path: str | Path = ".",
    *,
    task_id: str | None = None,
    max_inline_bytes: int = 512 * 1024,
    write_oversize_file: bool = True,
) -> GitSnapshot:
    """Capture current branch, commit SHA, working-tree status, and unified diff."""
    repo = Path(repo_path).expanduser().resolve()
    rc, top = _run_git(repo, ["rev-parse", "--show-toplevel"])
    if rc != 0 or not top.strip():
        return GitSnapshot(
            is_git_repo=False,
            git_branch=None,
            git_commit=None,
            status_short="",
            git_diff_patch=None,
            oversized_patch_ref=None,
        )

    repo_root = Path(top.strip())
    _, branch_raw = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_raw.strip() or None

    rc_sha, sha_raw = _run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    commit = sha_raw.strip() if rc_sha == 0 and sha_raw.strip() else None

    _, status_raw = _run_git(repo_root, ["status", "--short"])
    status_short = status_raw.strip()

    if commit:
        _, diff_raw = _run_git(repo_root, ["diff", "HEAD", "--no-color"])
    else:
        _, staged = _run_git(repo_root, ["diff", "--cached", "--no-color"])
        _, unstaged = _run_git(repo_root, ["diff", "--no-color"])
        diff_raw = (staged + "\n" + unstaged).strip()

    patch_text = diff_raw.strip() or None
    oversize_ref: dict[str, Any] | None = None

    # Keep inline DB patches safely within the Hub limit; spill huge diffs into .git/
    if patch_text and len(patch_text.encode("utf-8")) > max_inline_bytes:
        patch_bytes = patch_text.encode("utf-8")
        sha256 = hashlib.sha256(patch_bytes).hexdigest()
        saved_path = ""
        if write_oversize_file:
            patch_dir = repo_root / ".git"
            if patch_dir.is_dir():
                out_file = patch_dir / f"tower_handoff_{task_id or 'latest'}.patch"
                out_file.write_bytes(patch_bytes)
                saved_path = str(out_file)
        oversize_ref = {
            "kind": "git_patch",
            "path": saved_path,
            "sha256": sha256,
            "bytes": len(patch_bytes),
        }
        snippet = patch_bytes[: min(8192, max_inline_bytes // 4)].decode("utf-8", errors="ignore")
        patch_text = (
            f"# [TRUNCATED] Full diff ({len(patch_bytes)} bytes) exceeded inline limit; "
            f"saved locally at: {saved_path or '(not written)'}\n{snippet}"
        )

    return GitSnapshot(
        is_git_repo=True,
        git_branch=branch,
        git_commit=commit,
        status_short=status_short,
        git_diff_patch=patch_text,
        oversized_patch_ref=oversize_ref,
    )