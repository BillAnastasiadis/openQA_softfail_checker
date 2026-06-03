#!/usr/bin/env python3
import os
import sys
import argparse
import base64
import requests
import io
import tarfile

SEARCH_QUERY = "record_soft_failure"

def github_session(token: str | None) -> requests.Session:
    s = requests.Session()
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "record-soft-failure-scanner",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    s.headers.update(headers)
    return s

def find_record_soft_failures(
    owner: str,
    repo: str,
    ref: str | None = None,
    token: str | None = None,
):
    """
    Downloads the repo tarball for the given ref and searches it in-memory.
    Returns the exact same list of dictionaries as the old function.
    """
    session = github_session(token)
    
    # Default to the main branch if no ref is provided
    if not ref:
        repo_info_url = f"https://api.github.com/repos/{owner}/{repo}"
        repo_info_resp = session.get(repo_info_url, timeout=30)
        if repo_info_resp.ok:
            target_ref = repo_info_resp.json().get("default_branch", "master")
        else:
            target_ref = "master" # fallback
    else:
        target_ref = ref
    url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{target_ref}"
    
    print(f"Downloading repository archive for {target_ref}...", file=sys.stderr)
    resp = session.get(url, timeout=60)
    
    if not resp.ok:
        print(f"Failed to download archive: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    results = []
    
    # Open the downloaded tarball directly from memory
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            # Skip directories
            if not member.isfile():
                continue
                
            # OpenQA tests are primarily Perl modules (.pm). 
            # You can remove this check if you want to search all file types.
            if not member.name.endswith(".pm"):
                continue

            f = tar.extractfile(member)
            if f is None:
                continue

            try:
                # Decode the file and search line by line
                text = f.read().decode("utf-8")
                
                for lineno, line in scan_text_for_pattern(text, SEARCH_QUERY):
                    # GitHub tarballs prefix files with 'owner-repo-sha/'. 
                    # We strip the first folder out to match your old path format.
                    clean_path = "/".join(member.name.split("/")[1:])
                    
                    results.append(
                        {
                            "path": clean_path,
                            "line": lineno,
                            "text": line.rstrip(),
                        }
                    )
            except UnicodeDecodeError:
                # If a file isn't valid UTF-8 text, just skip it
                continue

    return results

def scan_text_for_pattern(text: str, pattern: str):
    """
    Yield (line_no, line_text) for each line containing the pattern.
    """
    for lineno, line in enumerate(text.splitlines(), start=1):
        if pattern in line:
            yield lineno, line

def main():
    parser = argparse.ArgumentParser(
        description="Scan a GitHub repo for record_soft_failure() occurrences."
    )
    parser.add_argument("--owner", required=True, help="GitHub owner/org (e.g. os-autoinst)")
    parser.add_argument("--repo", required=True, help="GitHub repo name (e.g. os-autoinst-distri-opensuse)")
    parser.add_argument("--ref", default=None, help="Branch/commit/tag (default: repo default branch)")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token (or env GITHUB_TOKEN)")
    args = parser.parse_args()

    if not args.token:
        print("Warning: no GITHUB_TOKEN provided – you may hit rate limits.", file=sys.stderr)

    print(
        f"Searching GitHub repo {args.owner}/{args.repo} for '{SEARCH_QUERY}'...",
        file=sys.stderr,
    )

    occurrences = find_record_soft_failures(
        owner=args.owner,
        repo=args.repo,
        ref=args.ref,
        token=args.token,
    )

    print(f"Found {len(occurrences)} occurrence(s).", file=sys.stderr)

    total_hits = 0
    for occ in occurrences:
        total_hits += 1
        print(f"{occ['path']}:{occ['line']}: {occ['text']}")

    if total_hits == 0:
        print("No occurrences found (after scanning candidate files).", file=sys.stderr)
    else:
        print(f"\nTotal occurrences: {total_hits}", file=sys.stderr)


if __name__ == "__main__":
    main()
