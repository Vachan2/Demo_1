"""
github_tool/github_api.py

Real GitHub implementation using PyGitHub.
Used when GITHUB_TOKEN and GITHUB_REPO are both configured.

All write operations target a new branch — never main directly.
"""
import base64
import logging
import sys
import os
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings
from github_tool.local_git import PRResult

logger = logging.getLogger("sentinel.github_tool.api")


def _gh_repo():
    from github import Github
    g = Github(settings.github_token)
    return g.get_repo(settings.github_repo)


def get_file(path: str, ref: str | None = None) -> str:
    repo = _gh_repo()
    kwargs = {"ref": ref} if ref else {}
    contents = repo.get_contents(path, **kwargs)
    return base64.b64decode(contents.content).decode()


def _get_sha(path: str, ref: str) -> str:
    repo = _gh_repo()
    try:
        contents = repo.get_contents(path, ref=ref)
        return contents.sha
    except Exception:
        return ""


def create_branch(branch_name: str) -> str:
    repo = _gh_repo()
    base_ref = repo.get_git_ref(f"heads/{settings.github_base_branch}")
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.object.sha)
    except Exception:
        # Branch already exists — delete and recreate
        existing = repo.get_git_ref(f"heads/{branch_name}")
        existing.delete()
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.object.sha)
    logger.info("branch_created", extra={"branch": branch_name})
    return branch_name


def update_file(path: str, new_content: str, branch_name: str) -> None:
    repo = _gh_repo()
    sha = _get_sha(path, branch_name)
    encoded = base64.b64encode(new_content.encode()).decode()
    if sha:
        repo.update_file(
            path=path,
            message=f"sentinel: update {path}",
            content=encoded,
            sha=sha,
            branch=branch_name,
        )
    else:
        repo.create_file(
            path=path,
            message=f"sentinel: create {path}",
            content=encoded,
            branch=branch_name,
        )
    logger.info("file_updated", extra={"path": path, "branch": branch_name})


def commit(message: str) -> str:
    # On GitHub API, commits are created per-file update.
    # This is a no-op here; individual update_file calls handle commits.
    return "github-api"


def create_pull_request(
    branch_name: str,
    title: str,
    body: str,
    incident_id: str,
) -> PRResult:
    repo = _gh_repo()
    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=settings.github_base_branch,
    )
    logger.info(
        "pull_request_created",
        extra={"pr_number": pr.number, "url": pr.html_url, "incident_id": incident_id},
    )
    return PRResult(
        branch=branch_name,
        pr_url=pr.html_url,
        pr_number=pr.number,
        title=title,
        body=body,
    )
