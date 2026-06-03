# Soft-Failure Scanner

This project scans a GitHub repository for `record_soft_failure` occurrences (commonly used in openQA), extracts linked Bugzilla (`bsc#...`) and Jira (`jsc#...`) ticket references, and queries their current status via their respective REST APIs. 

By downloading the repository as an in-memory tarball, it bypasses GitHub's code search limitations and avoids API rate limiting.

## Dependencies

- **Python 3.7+** (relies on `dataclasses` and type annotations)
- **requests** library

Install the required Python package:
```bash
pip install requests
```

## Configuration

You can configure the tool using either command-line arguments or environment variables. Using environment variables is recommended for sensitive tokens.

**Environment Variables:**
- `GITHUB_TOKEN`: GitHub Personal Access Token (prevents rate limits on the Tarball API)
- `BUGZILLA_URL`: Base URL for Bugzilla (e.g., `https://bugzilla.suse.com`)
- `BUGZILLA_API_KEY`: Bugzilla API Key
- `JIRA_URL`: Base URL for Jira (e.g., `https://jira.suse.com`)
- `JIRA_TOKEN`: Jira Personal Access Token (or API token)
- `JIRA_USER`: Jira username (optional, only required if using Basic Auth)

## Usage

Run the main script by specifying the target GitHub repository owner and name. 

### Basic Usage (using environment variables)

```bash
export GITHUB_TOKEN="your_github_token"
export BUGZILLA_URL="[https://bugzilla.suse.com](https://bugzilla.suse.com)"
export BUGZILLA_API_KEY="your_bz_key"
export JIRA_URL="[https://jira.suse.com](https://jira.suse.com)"
export JIRA_TOKEN="your_jira_token"

python3 check_softfails.py --owner os-autoinst --repo os-autoinst-distri-opensuse
```

### Specifying a Branch or Commit

By default, the script queries GitHub for the repository's default branch. You can scan a specific branch, tag, or commit hash using the `--ref` argument:

```bash
python3 check_softfails.py --owner os-autoinst --repo os-autoinst-distri-opensuse --ref my-feature-branch
```

### JSON Output

For integration with other tools, use the `--json` flag to output the results as a structured JSON array instead of human-readable text:

```bash
python3 check_softfails.py --owner os-autoinst --repo os-autoinst-distri-opensuse --json
```

### Passing Credentials via CLI

If you prefer not to use environment variables, you can pass all configuration via CLI flags:

```bash
python3 check_softfails.py \
    --owner os-autoinst \
    --repo os-autoinst-distri-opensuse \
    --gh-token "github_token" \
    --bugzilla-url "[https://bugzilla.suse.com](https://bugzilla.suse.com)" \
    --bugzilla-api-key "bz_key" \
    --jira-url "[https://jira.suse.com](https://jira.suse.com)" \
    --jira-token "jira_token"
```