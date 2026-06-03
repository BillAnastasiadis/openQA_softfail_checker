#!/usr/bin/env python3
"""
Lightweight Bugzilla REST API client.

Focus: what we need for the record_soft_failure scanner:
- Fetch a single bug's data (status, resolution, summary, product, etc.)
- Fetch comments for a bug
- Helper to get bug + comments together
- Helper to decide if a bug is "closed" / "solved"

This is written to work with standard Bugzilla REST APIs like:
  https://bugzilla.opensuse.org/rest/bug/<id>
  https://bugzilla.opensuse.org/rest/bug/<id>/comment
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import time

import requests


class BugzillaError(Exception):
    """Generic Bugzilla client error."""


class BugNotFound(BugzillaError):
    """Raised when a bug does not exist."""


@dataclass
class Bug:
    id: int
    status: str
    resolution: Optional[str]
    summary: str
    product: Optional[str] = None
    component: Optional[str] = None
    severity: Optional[str] = None
    priority: Optional[str] = None
    creator: Optional[str] = None
    last_change_time: Optional[str] = None
    raw: Dict[str, Any] = None  # full bug JSON from Bugzilla


@dataclass
class BugComment:
    id: int
    bug_id: int
    creator: str
    time: str
    text: str
    is_private: bool
    raw: Dict[str, Any] = None  # full comment JSON


class BugzillaClient:
    """
    Small helper around the Bugzilla REST API.

    Example:

        from bugzilla_client import BugzillaClient

        client = BugzillaClient(
            base_url="https://bugzilla.opensuse.org",
            api_key=os.environ.get("BUGZILLA_API_KEY"),
        )

        bug = client.get_bug(123456)
        comments = client.get_bug_comments(123456)

        if client.is_closed(bug):
            print("Bug is closed:", bug.status, bug.resolution)
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls, url_var: str = "BUGZILLA_URL", key_var: str = "BUGZILLA_API_KEY") -> "BugzillaClient":
        """
        Convenience constructor using environment variables:

            BUGZILLA_URL
            BUGZILLA_API_KEY

        Example:
            client = BugzillaClient.from_env()
        """
        base_url = os.environ.get(url_var)
        if not base_url:
            raise ValueError(f"{url_var} is not set")
        api_key = os.environ.get(key_var)
        return cls(base_url=base_url, api_key=api_key)
    
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        params = dict(params or {})
        if self.api_key:
            params.setdefault("api_key", self.api_key)

        try:
            time.sleep(1)
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.Timeout as e:
            # Turn timeout into a BugzillaError so callers can handle it gracefully
            raise BugzillaError(f"Timeout while calling Bugzilla: {url} ({e})") from e
        except requests.exceptions.RequestException as e:
            # Any other network issue (DNS, connection reset, proxy, etc.)
            raise BugzillaError(f"Network error while calling Bugzilla: {url} ({e})") from e

        if resp.status_code == 404:
            raise BugNotFound(f"Bugzilla resource not found: {url}")
        if not resp.ok:
            raise BugzillaError(f"Bugzilla GET {url} failed: {resp.status_code} {resp.text[:500]}")

        try:
            return resp.json()
        except ValueError as e:
            raise BugzillaError(f"Failed to parse Bugzilla JSON response from {url}: {e}") from e

    def get_bug(
        self,
        bug_id: int | str,
        include_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
    ) -> Bug:
        """
        Fetch a single bug and return a Bug dataclass.

        :param bug_id: Bug ID, e.g. 123456
        :param include_fields: Optional list of fields to include (Bugzilla 'include_fields' param)
        :param exclude_fields: Optional list of fields to exclude (Bugzilla 'exclude_fields' param)
        """
        params: Dict[str, Any] = {}
        if include_fields:
            params["include_fields"] = ",".join(include_fields)
        if exclude_fields:
            params["exclude_fields"] = ",".join(exclude_fields)

        data = self._get(f"/rest/bug/{bug_id}", params=params)

        bugs = data.get("bugs") or []
        if not bugs:
            raise BugNotFound(f"Bug {bug_id} not found")

        b = bugs[0]
        return Bug(
            id=b.get("id") or int(bug_id),
            status=b.get("status", ""),
            resolution=b.get("resolution"),
            summary=b.get("summary", ""),
            product=b.get("product"),
            component=b.get("component"),
            severity=b.get("severity"),
            priority=b.get("priority"),
            creator=b.get("creator"),
            last_change_time=b.get("last_change_time"),
            raw=b,
        )

    def get_bug_comments(self, bug_id: int | str) -> List[BugComment]:
        """
        Fetch all comments for a bug.

        Bugzilla returns something like:
            {
              "bugs": {
                "123456": {
                  "comments": [
                    { "id": 1, "creator": "...", "time": "...", "text": "...", "is_private": false },
                    ...
                  ]
                }
              }
            }
        """
        data = self._get(f"/rest/bug/{bug_id}/comment")

        bugs = data.get("bugs") or {}
        bug_data = bugs.get(str(bug_id)) or bugs.get(int(bug_id))  # depending on server
        if not bug_data:
            # Some Bugzilla variants use a list instead; be lenient
            # but in most standard setups this should work.
            raise BugzillaError(f"No comment data for bug {bug_id}")

        comments_raw = bug_data.get("comments", [])
        comments: List[BugComment] = []
        for c in comments_raw:
            comments.append(
                BugComment(
                    id=c.get("id"),
                    bug_id=int(bug_id),
                    creator=c.get("creator", ""),
                    time=c.get("time", ""),
                    text=c.get("text", ""),
                    is_private=bool(c.get("is_private")),
                    raw=c,
                )
            )
        return comments

    def get_bug_with_comments(
        self,
        bug_id: int | str,
        include_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
    ) -> tuple[Bug, List[BugComment]]:
        """
        Convenience helper: fetch bug + comments in one call from your code.
        """
        bug = self.get_bug(bug_id, include_fields=include_fields, exclude_fields=exclude_fields)
        comments = self.get_bug_comments(bug_id)
        return bug, comments

    def is_closed(self, bug: Bug) -> bool:
        """
        Decide whether a bug is considered "closed"/"solved".

        Adjust the sets below to match your Bugzilla instance semantics.
        """
        status = (bug.status or "").upper()
        resolution = (bug.resolution or "").upper()

        closed_statuses = {"RESOLVED", "VERIFIED", "CLOSED"}
        closed_resolutions = {"FIXED", "WONTFIX", "WORKSFORME", "DUPLICATE", "INVALID"}

        return status in closed_statuses or resolution in closed_resolutions

    def is_open(self, bug: Bug) -> bool:
        """
        The inverse of is_closed, kept explicit for readability.
        """
        return not self.is_closed(bug)

