"""
tools.py — Shared tool implementations and Anthropic tool schemas for Navi agents.

Every tool function returns a plain dict so results can be JSON-serialised
and sent back as tool_result messages to the Anthropic API.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_REPO = "cardosoitsm/navimvp"
REPO_ROOT = Path(__file__).parent.parent.parent  # navimvp/


# ---------------------------------------------------------------------------
# Local tools
# ---------------------------------------------------------------------------

def run_bash(command: str) -> dict[str, Any]:
    """Execute a shell command in the repository root directory."""
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "success": result.returncode == 0,
    }


def read_file(path: str) -> dict[str, Any]:
    """Read a file relative to the repository root."""
    try:
        full_path = REPO_ROOT / path
        return {"content": full_path.read_text(encoding="utf-8"), "success": True}
    except Exception as exc:
        return {"content": "", "success": False, "error": str(exc)}


def write_file(path: str, content: str) -> dict[str, Any]:
    """Write content to a file relative to the repository root."""
    try:
        full_path = REPO_ROOT / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return {"success": True, "path": str(full_path)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _gh_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN environment variable is not set.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh(method: str, path: str, data: dict | None = None) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    resp = requests.request(
        method, url, json=data, headers=_gh_headers(), timeout=30
    )
    try:
        return resp.json()
    except Exception:
        return {"error": resp.text, "status_code": resp.status_code}


# ---------------------------------------------------------------------------
# GitHub tools
# ---------------------------------------------------------------------------

def github_create_issue(
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Create a GitHub issue and return the created issue object."""
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    return _gh("POST", "issues", payload)


def github_get_issue(issue_number: int) -> dict[str, Any]:
    """Fetch a GitHub issue by number."""
    return _gh("GET", f"issues/{issue_number}")


def github_update_issue(
    issue_number: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing GitHub issue (title, body, state, labels)."""
    data: dict[str, Any] = {}
    if title is not None:
        data["title"] = title
    if body is not None:
        data["body"] = body
    if state is not None:
        data["state"] = state
    if labels is not None:
        data["labels"] = labels
    return _gh("PATCH", f"issues/{issue_number}", data)


def github_search_issues(query: str) -> dict[str, Any]:
    """Search GitHub issues using a query string."""
    url = f"https://api.github.com/search/issues?q={requests.utils.quote(query)}+repo:{GITHUB_REPO}"
    resp = requests.get(url, headers=_gh_headers(), timeout=30)
    try:
        return resp.json()
    except Exception:
        return {"error": resp.text}


def github_add_comment(issue_number: int, body: str) -> dict[str, Any]:
    """Add a comment to an issue or pull request."""
    return _gh("POST", f"issues/{issue_number}/comments", {"body": body})


def github_create_pull_request(
    title: str,
    body: str,
    head: str,
    base: str = "main",
) -> dict[str, Any]:
    """Open a pull request from head branch into base branch."""
    return _gh(
        "POST",
        "pulls",
        {"title": title, "body": body, "head": head, "base": base},
    )


def github_get_pull_request(pr_number: int) -> dict[str, Any]:
    """Fetch a pull request by number."""
    return _gh("GET", f"pulls/{pr_number}")


def github_create_review(
    pr_number: int,
    body: str,
    event: str,
) -> dict[str, Any]:
    """
    Submit a PR review.
    event must be one of: APPROVE, REQUEST_CHANGES, COMMENT.
    """
    return _gh(
        "POST",
        f"pulls/{pr_number}/reviews",
        {"body": body, "event": event},
    )


def github_merge_pull_request(
    pr_number: int,
    commit_title: str,
    merge_method: str = "squash",
) -> dict[str, Any]:
    """Merge a pull request. merge_method: squash | merge | rebase."""
    return _gh(
        "PUT",
        f"pulls/{pr_number}/merge",
        {"commit_title": commit_title, "merge_method": merge_method},
    )


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS: dict[str, Any] = {
    "run_bash": run_bash,
    "read_file": read_file,
    "write_file": write_file,
    "github_create_issue": github_create_issue,
    "github_get_issue": github_get_issue,
    "github_update_issue": github_update_issue,
    "github_search_issues": github_search_issues,
    "github_add_comment": github_add_comment,
    "github_create_pull_request": github_create_pull_request,
    "github_get_pull_request": github_get_pull_request,
    "github_create_review": github_create_review,
    "github_merge_pull_request": github_merge_pull_request,
}


def execute_tool(name: str, inputs: dict[str, Any]) -> Any:
    """Dispatch a tool call by name and return its result."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**inputs)
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Anthropic tool schemas
# ---------------------------------------------------------------------------

BASH_TOOL = {
    "name": "run_bash",
    "description": (
        "Execute a shell command in the repository root. "
        "Use for git operations, running tests, linting, or any CLI task."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."},
        },
        "required": ["command"],
    },
}

READ_FILE_TOOL = {
    "name": "read_file",
    "description": "Read a file from the repository. Path is relative to the repo root (e.g. 'app/main.py').",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to repo root."},
        },
        "required": ["path"],
    },
}

WRITE_FILE_TOOL = {
    "name": "write_file",
    "description": "Write or overwrite a file in the repository. Path is relative to the repo root.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to repo root."},
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["path", "content"],
    },
}

CREATE_ISSUE_TOOL = {
    "name": "github_create_issue",
    "description": "Create a new GitHub issue in cardosoitsm/navimvp.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Issue title."},
            "body": {"type": "string", "description": "Issue body in Markdown."},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of label names to apply.",
            },
        },
        "required": ["title", "body"],
    },
}

GET_ISSUE_TOOL = {
    "name": "github_get_issue",
    "description": "Fetch a GitHub issue by its number.",
    "input_schema": {
        "type": "object",
        "properties": {
            "issue_number": {"type": "integer", "description": "The issue number."},
        },
        "required": ["issue_number"],
    },
}

UPDATE_ISSUE_TOOL = {
    "name": "github_update_issue",
    "description": "Update a GitHub issue's title, body, state, or labels.",
    "input_schema": {
        "type": "object",
        "properties": {
            "issue_number": {"type": "integer"},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "state": {"type": "string", "enum": ["open", "closed"]},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["issue_number"],
    },
}

SEARCH_ISSUES_TOOL = {
    "name": "github_search_issues",
    "description": "Search GitHub issues in the navimvp repo using a keyword query.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords."},
        },
        "required": ["query"],
    },
}

ADD_COMMENT_TOOL = {
    "name": "github_add_comment",
    "description": "Add a comment to a GitHub issue or pull request by number.",
    "input_schema": {
        "type": "object",
        "properties": {
            "issue_number": {"type": "integer", "description": "Issue or PR number."},
            "body": {"type": "string", "description": "Comment body in Markdown."},
        },
        "required": ["issue_number", "body"],
    },
}

CREATE_PR_TOOL = {
    "name": "github_create_pull_request",
    "description": "Open a pull request from a feature branch into main.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string", "description": "PR description in Markdown."},
            "head": {"type": "string", "description": "Source branch name."},
            "base": {"type": "string", "description": "Target branch (default: main)."},
        },
        "required": ["title", "body", "head"],
    },
}

GET_PR_TOOL = {
    "name": "github_get_pull_request",
    "description": "Fetch a pull request by its number.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pr_number": {"type": "integer"},
        },
        "required": ["pr_number"],
    },
}

CREATE_REVIEW_TOOL = {
    "name": "github_create_review",
    "description": "Submit a pull request review. event must be APPROVE, REQUEST_CHANGES, or COMMENT.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pr_number": {"type": "integer"},
            "body": {"type": "string", "description": "Review body in Markdown."},
            "event": {
                "type": "string",
                "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
            },
        },
        "required": ["pr_number", "body", "event"],
    },
}

MERGE_PR_TOOL = {
    "name": "github_merge_pull_request",
    "description": "Merge a pull request. Use squash merge method by default.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pr_number": {"type": "integer"},
            "commit_title": {"type": "string", "description": "Squash commit title."},
            "merge_method": {
                "type": "string",
                "enum": ["squash", "merge", "rebase"],
                "description": "Merge strategy (default: squash).",
            },
        },
        "required": ["pr_number", "commit_title"],
    },
}

# Tool sets per agent role
PM_TOOLS = [BASH_TOOL, READ_FILE_TOOL, CREATE_ISSUE_TOOL, GET_ISSUE_TOOL,
            UPDATE_ISSUE_TOOL, SEARCH_ISSUES_TOOL, ADD_COMMENT_TOOL, GET_PR_TOOL]

DEV_TOOLS = [BASH_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL, GET_ISSUE_TOOL,
             UPDATE_ISSUE_TOOL, ADD_COMMENT_TOOL, CREATE_PR_TOOL]

QA_TOOLS = [BASH_TOOL, READ_FILE_TOOL, GET_ISSUE_TOOL, GET_PR_TOOL,
            ADD_COMMENT_TOOL, CREATE_REVIEW_TOOL, MERGE_PR_TOOL, UPDATE_ISSUE_TOOL]
