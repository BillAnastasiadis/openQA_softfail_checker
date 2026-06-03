#!/usr/bin/env python3
"""
Soft-failure scanner:

- Scan a GitHub repo for `record_soft_failure(...)` occurrences.
- Extract referenced bugs:
    - Bugzilla: bsc#<id>
    - Jira:     jsc#<identifier>
- Query Bugzilla / Jira for status and summary.
- Return a structured list that can be used from other scripts,
  or print results when used as a CLI.

Config can come from env vars *or* CLI options.

Env vars:
    GITHUB_TOKEN
    BUGZILLA_URL
    BUGZILLA_API_KEY
    JIRA_URL
    JIRA_TOKEN
    JIRA_USER
"""

from __future__ import annotations

import os
import sys
import re
import json
import argparse
from typing import Any, Dict, List, Optional, Tuple

from gh_parser import find_record_soft_failures
from bsc_parser import BugzillaClient, BugzillaError, BugNotFound
from jira_parser import JiraClient, JiraError, IssueNotFound


# bsc#123456 (Bugzilla)
BUGZILLA_RE = re.compile(r"\bb[a-zA-Z]+#(\d+)\b")

# jsc#SOME-IDENTIFIER (Jira) — identifier can be numeric or alphanumeric
JIRA_RE = re.compile(r"\bjsc#([A-Za-z0-9_-]+)\b", re.IGNORECASE)


def extract_bug_reference(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract bug reference from a line of code or message text.

    Returns:
        (tracker, bug_token, bug_id_or_key)

        tracker: "bugzilla" | "jira" | None
        bug_token: e.g. "bsc#1234" or "jsc#FOO-42"
        bug_id_or_key: e.g. "1234" or "FOO-42"

    If no bug reference is found, returns (None, None, None).
    """
    m = BUGZILLA_RE.search(text)
    if m:
        bug_id = m.group(1)
        bug_token = m.group(0)
        return "bugzilla", bug_token, bug_id

    m = JIRA_RE.search(text)
    if m:
        bug_key = m.group(1)
        bug_token = m.group(0)
        return "jira", bug_token, bug_key

    return None, None, None

def scan_soft_failures_with_bug_status(
    owner: str,
    repo: str,
    ref: Optional[str] = None,
    gh_token: Optional[str] = None,
    bugzilla_url: Optional[str] = None,
    bugzilla_api_key: Optional[str] = None,
    jira_url: Optional[str] = None,
    jira_token: Optional[str] = None,
    jira_user: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    High-level function that ties everything together.

    Returns a list of dicts, one per soft-failure occurrence, e.g.:

        {
          "repo_owner": "os-autoinst",
          "repo_name": "os-autoinst-distri-opensuse",
          "path": "tests/...",
          "line": 123,
          "text": "    record_soft_failure 'bsc#123456 - ...';",
          "tracker": "bugzilla",
          "bug_token": "bsc#123456",
          "bug_id": "123456",
          "bug_status": "RESOLVED",
          "bug_resolution": "FIXED",
          "bug_summary": "Some bug title",
          "bug_is_closed": True,
          "bug_error": None,            # or error string if lookup failed
        }

    This function does NOT print; it just returns data.
    """

    # Fallback to environment variables if not provided
    gh_token = gh_token or os.environ.get("GITHUB_TOKEN")

    bugzilla_url = bugzilla_url or os.environ.get("BUGZILLA_URL")
    bugzilla_api_key = bugzilla_api_key or os.environ.get("BUGZILLA_API_KEY")

    jira_url = jira_url or os.environ.get("JIRA_URL")
    jira_token = jira_token or os.environ.get("JIRA_TOKEN")
    jira_user = jira_user or os.environ.get("JIRA_USER")

    # Scan GitHub for record_soft_failure occurrences
    occurrences = find_record_soft_failures(
        owner=owner,
        repo=repo,
        ref=ref,
        token=gh_token,
    )

    # Initialize clients only if URLs are provided
    bz_client: Optional[BugzillaClient] = None
    if bugzilla_url:
        bz_client = BugzillaClient(base_url=bugzilla_url, api_key=bugzilla_api_key)

    jira_client: Optional[JiraClient] = None
    if jira_url:
        jira_client = JiraClient(base_url=jira_url, token=jira_token, username=jira_user)

    # Caches to avoid repeated API calls
    bugzilla_cache: Dict[str, Dict[str, Any]] = {}
    jira_cache: Dict[str, Dict[str, Any]] = {}

    results: List[Dict[str, Any]] = []

    for occ in occurrences:
        text = occ["text"]
        tracker, bug_token, bug_id = extract_bug_reference(text)

        result_entry: Dict[str, Any] = {
            "repo_owner": owner,
            "repo_name": repo,
            "path": occ["path"],
            "line": occ["line"],
            "text": occ["text"],
            "tracker": tracker,
            "bug_token": bug_token,
            "bug_id": bug_id,
            "bug_status": None,
            "bug_resolution": None,
            "bug_summary": None,
            "bug_is_closed": None,
            "bug_error": None,
        }

        # If no bug reference, just append as-is
        if not tracker or not bug_id:
            results.append(result_entry)
            continue

        if tracker == "bugzilla":
            if not bz_client:
                result_entry["bug_error"] = "Bugzilla URL/API key not configured"
                results.append(result_entry)
                continue

            if bug_id in bugzilla_cache:
                info = bugzilla_cache[bug_id]
            else:
                try:
                    bug = bz_client.get_bug(bug_id)
                    info = {
                        "status": bug.status,
                        "resolution": bug.resolution,
                        "summary": bug.summary,
                        "is_closed": bz_client.is_closed(bug),
                        "error": None,
                    }
                except BugNotFound as e:
                    info = {
                        "status": None,
                        "resolution": None,
                        "summary": None,
                        "is_closed": None,
                        "error": f"Bug not found: {e}",
                    }
                except BugzillaError as e:
                    info = {
                        "status": None,
                        "resolution": None,
                        "summary": None,
                        "is_closed": None,
                        "error": f"Bugzilla error: {e}",
                    }
                bugzilla_cache[bug_id] = info

            result_entry["bug_status"] = info["status"]
            result_entry["bug_resolution"] = info["resolution"]
            result_entry["bug_summary"] = info["summary"]
            result_entry["bug_is_closed"] = info["is_closed"]
            result_entry["bug_error"] = info["error"]

        elif tracker == "jira":
            if not jira_client:
                result_entry["bug_error"] = "Jira URL/token not configured"
                results.append(result_entry)
                continue

            if bug_id in jira_cache:
                info = jira_cache[bug_id]
            else:
                try:
                    issue = jira_client.get_issue(bug_id)
                    info = {
                        "status": issue.status,
                        "resolution": issue.resolution,
                        "summary": issue.summary,
                        "is_closed": jira_client.is_done(issue),
                        "error": None,
                    }
                except IssueNotFound as e:
                    info = {
                        "status": None,
                        "resolution": None,
                        "summary": None,
                        "is_closed": None,
                        "error": f"Issue not found: {e}",
                    }
                except JiraError as e:
                    info = {
                        "status": None,
                        "resolution": None,
                        "summary": None,
                        "is_closed": None,
                        "error": f"Jira error: {e}",
                    }
                jira_cache[bug_id] = info

            result_entry["bug_status"] = info["status"]
            result_entry["bug_resolution"] = info["resolution"]
            result_entry["bug_summary"] = info["summary"]
            result_entry["bug_is_closed"] = info["is_closed"]
            result_entry["bug_error"] = info["error"]

        results.append(result_entry)

    return results

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan record_soft_failure occurrences in a GitHub repo and check Bugzilla/Jira status.",
    )
    parser.add_argument("--owner", required=True, help="GitHub owner/org (e.g. os-autoinst)")
    parser.add_argument("--repo", required=True, help="GitHub repo name (e.g. os-autoinst-distri-opensuse)")
    parser.add_argument("--ref", default=None, help="Branch/commit/tag (default: repo default branch)")

    # GitHub auth
    parser.add_argument("--gh-token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token (or env GITHUB_TOKEN)")

    # Bugzilla config
    parser.add_argument("--bugzilla-url", default=os.environ.get("BUGZILLA_URL"), help="Bugzilla base URL")
    parser.add_argument("--bugzilla-api-key", default=os.environ.get("BUGZILLA_API_KEY"), help="Bugzilla API key")

    # Jira config
    parser.add_argument("--jira-url", default=os.environ.get("JIRA_URL"), help="Jira base URL")
    parser.add_argument("--jira-token", default=os.environ.get("JIRA_TOKEN"), help="Jira token")
    parser.add_argument("--jira-user", default=os.environ.get("JIRA_USER"), help="Jira username (for Basic auth, optional)")

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a human-readable report.",
    )

    args = parser.parse_args()

    results = scan_soft_failures_with_bug_status(
        owner=args.owner,
        repo=args.repo,
        ref=args.ref,
        gh_token=args.gh_token,
        bugzilla_url=args.bugzilla_url,
        bugzilla_api_key=args.bugzilla_api_key,
        jira_url=args.jira_url,
        jira_token=args.jira_token,
        jira_user=args.jira_user,
    )

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    # Human-readable output
    for r in results:
        print("=" * 80)
        print(f"{r['path']}:{r['line']}")
        print(f"code:  {r['text']}")
        print(f"bug:   {r['bug_token']} (tracker={r['tracker']})")
        print(f"status: {r['bug_status']}  resolution: {r['bug_resolution']}")
        print(f"summary: {r['bug_summary']}")
        print(f"is_closed: {r['bug_is_closed']}")
        if r['bug_error']:
            print(f"error: {r['bug_error']}")

    print("\nTotal occurrences:", len(results), file=sys.stderr)


if __name__ == "__main__":
    main()

