#!/usr/bin/env python3
"""
Lightweight Jira REST API client.

Focus for the record_soft_failure scanner:
- Fetch a single issue's data (status, resolution, summary, etc.)
- Fetch comments for an issue
- Helper to decide if an issue is "done"/"closed"

Designed to work with Jira Server/Data Center style APIs, e.g.:

    https://jira.suse.com/rest/api/2/issue/KEY-123
    https://jira.suse.com/rest/api/2/issue/KEY-123/comment

Auth:
- Default: Bearer token (PAT) via "Authorization: Bearer <token>"
- Optional: Basic auth (username + token), if needed for some setups
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from requests.auth import HTTPBasicAuth


class JiraError(Exception):
    """Generic Jira client error."""


class IssueNotFound(JiraError):
    """Raised when an issue does not exist."""


@dataclass
class Issue:
    key: str
    id: str
    summary: str
    status: str
    status_category: Optional[str]
    resolution: Optional[str]
    issue_type: Optional[str]
    priority: Optional[str]
    creator: Optional[str]
    assignee: Optional[str]
    raw: Dict[str, Any]  # full JSON from Jira


@dataclass
class IssueComment:
    id: str
    issue_key: str
    author: str
    created: str
    body: str
    raw: Dict[str, Any]


class JiraClient:
    """
    Small helper around the Jira REST API.

    Example:

        from jira_client import JiraClient

        client = JiraClient(
            base_url="https://jira.suse.com",
            token=os.environ["JIRA_TOKEN"],
        )

        issue = client.get_issue("QAM-1234")
        comments = client.get_issue_comments("QAM-1234")

        if client.is_done(issue):
            print("Issue is done:", issue.status, issue.resolution)
    """

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        username: Optional[str] = None,
        session: Optional[requests.Session] = None,
        timeout: int = 10,
    ) -> None:
        """
        :param base_url: e.g. 'https://jira.suse.com'
        :param token: PAT or API token
        :param username: optional username (for Basic auth). If not provided,
                         Bearer token auth is used when token is set.
        """
        if not base_url:
            raise ValueError("base_url is required")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.username = username
        self.timeout = timeout
        self.session = session or requests.Session()

    @classmethod
    def from_env(
        cls,
        url_var: str = "JIRA_URL",
        token_var: str = "JIRA_TOKEN",
        user_var: str = "JIRA_USER",
    ) -> "JiraClient":
        """
        Convenience constructor using environment variables:

            JIRA_URL
            JIRA_TOKEN
            JIRA_USER (optional, only if you want Basic auth)
        """
        base_url = os.environ.get(url_var)
        if not base_url:
            raise ValueError(f"{url_var} is not set")
        token = os.environ.get(token_var)
        username = os.environ.get(user_var)
        return cls(base_url=base_url, token=token, username=username)

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Internal GET wrapper. Uses Bearer or Basic auth depending on
        whether `username` is set.
        """
        url = f"{self.base_url}{path}"
        params = params or {}

        headers: Dict[str, str] = {
            "Accept": "application/json",
        }
        auth = None

        if self.username and self.token:
            # Basic auth (username + API token)
            auth = HTTPBasicAuth(self.username, self.token)
        elif self.token:
            # Bearer token
            headers["Authorization"] = f"Bearer {self.token}"

        resp = self.session.get(url, headers=headers, params=params, auth=auth, timeout=self.timeout)

        if resp.status_code == 404:
            raise IssueNotFound(f"Jira issue not found: {url}")
        if not resp.ok:
            raise JiraError(f"Jira GET {url} failed: {resp.status_code} {resp.text[:500]}")

        try:
            return resp.json()
        except ValueError as e:
            raise JiraError(f"Failed to parse Jira JSON response from {url}: {e}") from e

    def get_issue(
        self,
        issue_key: str,
        fields: Optional[List[str]] = None,
    ) -> Issue:
        """
        Fetch a single issue and return an Issue dataclass.

        :param issue_key: e.g. 'QAM-1234'
        :param fields: Optional list of field names to request, e.g.
                       ['summary', 'status', 'resolution']
                       If None, Jira will return the default set.
        """
        params: Dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)

        data = self._get(f"/rest/api/2/issue/{issue_key}", params=params)

        fields_data = data.get("fields", {})
        status_data = fields_data.get("status") or {}
        status_category = status_data.get("statusCategory") or {}

        return Issue(
            key=data.get("key", issue_key),
            id=str(data.get("id", "")),
            summary=fields_data.get("summary", ""),
            status=status_data.get("name", ""),
            status_category=status_category.get("key") or status_category.get("name"),
            resolution=(fields_data.get("resolution") or {}).get("name"),
            issue_type=(fields_data.get("issuetype") or {}).get("name"),
            priority=(fields_data.get("priority") or {}).get("name"),
            creator=(fields_data.get("creator") or {}).get("displayName")
                or (fields_data.get("reporter") or {}).get("displayName"),
            assignee=(fields_data.get("assignee") or {}).get("displayName"),
            raw=data,
        )

    def get_issue_comments(self, issue_key: str) -> List[IssueComment]:
        """
        Fetch all comments for an issue.

        Jira returns:
           { "comments": [ { "id": "...", "body": "...", ... }, ... ], ... }
        """
        data = self._get(f"/rest/api/2/issue/{issue_key}/comment")

        comments_raw = data.get("comments", [])
        comments: List[IssueComment] = []
        for c in comments_raw:
            author = c.get("author") or {}
            comments.append(
                IssueComment(
                    id=str(c.get("id", "")),
                    issue_key=issue_key,
                    author=author.get("displayName") or author.get("name", ""),
                    created=c.get("created", ""),
                    body=c.get("body", ""),
                    raw=c,
                )
            )
        return comments

    def get_issue_with_comments(
        self,
        issue_key: str,
        fields: Optional[List[str]] = None,
    ) -> tuple[Issue, List[IssueComment]]:
        """
        Convenience helper: fetch issue + comments together.
        """
        issue = self.get_issue(issue_key, fields=fields)
        comments = self.get_issue_comments(issue_key)
        return issue, comments

    def search_issues_jql(
        self,
        jql: str,
        fields: Optional[List[str]] = None,
        max_results: int = 50,
    ) -> List[Issue]:
        """
        Simple JQL search helper.

        :param jql: Jira Query Language string, e.g. "project = QAM AND status = 'Done'"
        :param fields: Optional list of fields to request.
        :param max_results: Number of issues to return (Jira default is 50).
        """
        params: Dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results,
        }
        if fields:
            params["fields"] = ",".join(fields)

        data = self._get("/rest/api/2/search", params=params)
        issues = []

        for item in data.get("issues", []):
            fields_data = item.get("fields", {})
            status_data = fields_data.get("status") or {}
            status_category = status_data.get("statusCategory") or {}

            issues.append(
                Issue(
                    key=item.get("key", ""),
                    id=str(item.get("id", "")),
                    summary=fields_data.get("summary", ""),
                    status=status_data.get("name", ""),
                    status_category=status_category.get("key") or status_category.get("name"),
                    resolution=(fields_data.get("resolution") or {}).get("name"),
                    issue_type=(fields_data.get("issuetype") or {}).get("name"),
                    priority=(fields_data.get("priority") or {}).get("name"),
                    creator=(fields_data.get("creator") or {}).get("displayName")
                        or (fields_data.get("reporter") or {}).get("displayName"),
                    assignee=(fields_data.get("assignee") or {}).get("displayName"),
                    raw=item,
                )
            )

        return issues

    def get_board_sprints(
        self,
        board_id: str,
        state: Optional[str] = None,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all sprints for a given board using the Jira Agile API.

        Args:
            board_id (str): The board ID.
            state (str|None): Optional state filter. Jira supports:
                              "active", "closed", "future" or a comma-separated
                              combination like "active,closed".
                              If None, all states are returned.
            max_results (int): Page size for pagination.

        Returns:
            list[dict]: Raw sprint JSON objects from Jira (as dicts).
        """
        all_sprints: List[Dict[str, Any]] = []
        start_at = 0

        while True:
            params: Dict[str, Any] = {
                "startAt": start_at,
                "maxResults": max_results,
            }
            if state:
                params["state"] = state

            data = self._get(f"/rest/agile/1.0/board/{board_id}/sprint", params=params)

            values = data.get("values", [])
            if not values:
                break

            all_sprints.extend(values)

            if data.get("isLast", True):
                break

            start_at += max_results

        return all_sprints

    def get_sprint_issues(
        self,
        sprint_id: str,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all issues for a given sprint using the Jira Agile API.

        Args:
            sprint_id (str): The sprint ID.
            max_results (int): Page size for pagination.

        Returns:
            list[dict]: Raw issue JSON objects from Jira (as dicts).
                        Each item is the same structure as in /rest/api/2/search.
        """
        all_issues: List[Dict[str, Any]] = []
        start_at = 0

        while True:
            params: Dict[str, Any] = {
                "startAt": start_at,
                "maxResults": max_results,
            }
            data = self._get(f"/rest/agile/1.0/sprint/{sprint_id}/issue", params=params)

            issues = data.get("issues", [])
            if not issues:
                break

            all_issues.extend(issues)

            total = data.get("total", 0)
            if start_at + max_results >= total:
                break

            start_at += max_results

        return all_issues

    def is_done(self, issue: Issue) -> bool:
        """
        Decide whether an issue is considered "done"/"closed".

        For Jira this is usually either:
          - statusCategory.key == "done"
          - or status name in {"Done", "Closed", "Resolved", ...}

        Adjust the sets below to match your Jira workflow.
        """
        status_name = (issue.status or "").upper()
        status_cat = (issue.status_category or "").lower()

        if status_cat == "done":
            return True

        done_names = {"DONE", "CLOSED", "RESOLVED", "VERIFIED"}
        return status_name in done_names

    def is_open(self, issue: Issue) -> bool:
        """
        The opposite of is_done, kept explicit for readability.
        """
        return not self.is_done(issue)

