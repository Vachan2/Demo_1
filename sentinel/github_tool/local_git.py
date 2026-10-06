"""
github_tool/local_git.py

A local-git implementation of the remediation tool interface.
Used when GITHUB_TOKEN / GITHUB_REPO are not configured (offline demos,
CI without secrets).  All operations are performed against a local git
repository using GitPython.

Interface mirrors the real GitHub tool so callers are interchangeable.
"""
import logging
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

logger = logging.getLogger("sentinel.github_tool.local")

try:
    import git as gitpython
    _GIT_AVAILABLE = True
except ImportError:
    _GIT_AVAILABLE = False


@dataclass
class PRResult:
    branch: str
    pr_url: str
    pr_number: int
    title: str
    body: str


def _repo() -> "gitpython.Repo":
    repo_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", settings.local_repo_path)
    )
    return gitpython.Repo(repo_path)


def get_file(path: str, ref: str = "HEAD") -> str:
    """Return the content of a file at a given git ref."""
    if not _GIT_AVAILABLE:
        return _read_file_direct(path)
    try:
        repo = _repo()
        blob = repo.commit(ref).tree[path]
        return blob.data_stream.read().decode()
    except Exception:
        return _read_file_direct(path)


def _read_file_direct(path: str) -> str:
    repo_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", settings.local_repo_path)
    )
    with open(os.path.join(repo_path, path)) as fh:
        return fh.read()


def create_branch(branch_name: str) -> str:
    """Create a new branch from the current HEAD. Returns branch name."""
    if not _GIT_AVAILABLE:
        logger.info("git_unavailable_skipping_branch", extra={"branch": branch_name})
        return branch_name
    repo = _repo()
    # Delete existing branch of same name if present
    if branch_name in [b.name for b in repo.branches]:
        repo.delete_head(branch_name, force=True)
    new_branch = repo.create_head(branch_name)
    repo.head.reference = new_branch
    repo.head.reset(index=True, working_tree=True)
    logger.info("branch_created", extra={"branch": branch_name})
    return branch_name


def update_file(path: str, new_content: str, branch_name: str) -> None:
    """Write new_content to path and stage the change on branch_name."""
    repo_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", settings.local_repo_path)
    )
    full_path = os.path.join(repo_path, path)
    with open(full_path, "w") as fh:
        fh.write(new_content)
    if _GIT_AVAILABLE:
        repo = _repo()
        repo.index.add([path])
    logger.info("file_updated", extra={"path": path, "branch": branch_name})


def commit(message: str) -> str:
    """Commit staged changes. Returns the commit sha."""
    if not _GIT_AVAILABLE:
        logger.info("git_unavailable_skipping_commit", extra={"message": message})
        return "local-no-git"
    repo = _repo()
    commit_obj = repo.index.commit(
        message,
        author=gitpython.Actor("Sentinel AI", "sentinel@example.com"),
        committer=gitpython.Actor("Sentinel AI", "sentinel@example.com"),
    )
    sha = commit_obj.hexsha[:8]
    logger.info("committed", extra={"sha": sha, "message": message})
    return sha


def create_pull_request(
    branch_name: str,
    title: str,
    body: str,
    incident_id: str,
) -> PRResult:
    """
    Simulate PR creation locally (prints metadata; in prod this calls GitHub API).
    Returns a PRResult with a local pseudo-URL.
    """
    pr_number = abs(hash(incident_id)) % 900 + 100  # stable pseudo-number
    pr_url = f"local://pull/{pr_number}"

    logger.info(
        "pull_request_created",
        extra={
            "pr_number": pr_number,
            "branch": branch_name,
            "title": title,
            "incident_id": incident_id,
        },
    )

    return PRResult(
        branch=branch_name,
        pr_url=pr_url,
        pr_number=pr_number,
        title=title,
        body=body,
    )
