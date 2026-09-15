#!/usr/bin/env python3
"""
rate_repos.py
=============
Reads a CSV of teams + GitHub repository links, evaluates each repository
across four engineering-practice categories using Claude on AWS Bedrock,
and writes an output CSV with a score and one-line remark per category.

Categories rated (see CATEGORIES below for the exact rubric text sent to
the model):
    1. Specification and Architecture
    2. Working Software and Delivery
    3. Agent Engineering and Code Quality
    4. Testing and Verification

USAGE
-----
    python rate_repos.py \
        --input teams.csv \
        --output repo_ratings.csv \
        --repo-column "GitHub Repo" \
        --team-column "Team ID"

REQUIREMENTS
------------
    pip install boto3 pandas requests

    - AWS credentials must be available via the standard boto3 chain
      (environment variables, ~/.aws/credentials, an instance/role, or
      AWS SSO). This script never hardcodes credentials.
    - The Bedrock model you pass via --model-id must be enabled for your
      account/region in the Bedrock console. Some Claude models on Bedrock
      require an inference-profile ARN rather than the bare model ID
      (e.g. "us.anthropic.claude-3-5-sonnet-20241022-v2:0") - if you get a
      ValidationException about on-demand throughput not being supported,
      that's why; switch --model-id to the profile ARN/ID shown in the
      Bedrock console for that model.
    - Optional: set GITHUB_TOKEN (a GitHub personal access token) to raise
      the GitHub API rate limit from 60 to 5000 requests/hour. Public repos
      work fine without a token, just more slowly and with a lower ceiling.

OUTPUT
------
A CSV with one row per input repo:
    Team ID, Repository,
    Specification and Architecture Score, Specification and Architecture Remark,
    Working Software and Delivery Score, Working Software and Delivery Remark,
    Agent Engineering and Code Quality Score, Agent Engineering and Code Quality Remark,
    Testing and Verification Score, Testing and Verification Remark,
    Error
"""

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from typing import Optional, Tuple

import boto3
import pandas as pd
import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"
DEFAULT_MODEL_ID = ""
SCORE_SCALE = "1 (poor/absent) to 10 (excellent)"

CATEGORIES = {
    "specification_and_architecture": {
        "label": "Specification and Architecture",
        "description": (
            "Looking for deliberate, documented decisions and a clear line "
            "from intent to implementation. Reasoning matters more than "
            "paperwork."
        ),
    },
    "working_software_and_delivery": {
        "label": "Working Software and Delivery",
        "description": (
            "Does it actually run, and can someone else bring it up and see "
            "it work? We are looking at the finished application against "
            "what you set out to build, and at how automated and repeatable "
            "the path from your repository to a running app is."
        ),
    },
    "agent_engineering_and_code_quality": {
        "label": "Agent Engineering and Code Quality",
        "description": (
            "How deliberately did you shape and steer your AI tooling, "
            "rather than prompting a generic assistant? We are looking at "
            "how you kept your agent effective as the project grew, and at "
            "the state of what came out the other end."
        ),
    },
    "testing_and_verification": {
        "label": "Testing and Verification",
        "description": (
            "Is the application verified automatically, and does that "
            "verification run as part of your pipeline? This is the "
            "smallest band and the one teams most often abandon."
        ),
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rate_repos")


# --------------------------------------------------------------------------
# GitHub helpers
# --------------------------------------------------------------------------

def parse_owner_repo(url_or_slug: str) -> Optional[Tuple[str, str]]:
    """Extract (owner, repo) from a GitHub URL or an 'owner/repo' slug."""
    if not isinstance(url_or_slug, str) or not url_or_slug.strip():
        return None
    s = url_or_slug.strip().rstrip("/")
    s = re.sub(r"\.git$", "", s)
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+)", s)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([\w.-]+)/([\w.-]+)$", s)
    if m:
        return m.group(1), m.group(2)
    return None


class GitHubClient:
    def __init__(self, token: Optional[str] = None):
        self.session = requests.Session()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.session.headers.update(headers)

    def _get(self, url, **kwargs):
        resp = None
        for attempt in range(3):
            resp = self.session.get(url, timeout=20, **kwargs)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - time.time(), 1)
                log.warning("GitHub rate limit hit, sleeping %.0fs", wait)
                time.sleep(min(wait, 120))
                continue
            return resp
        return resp

    def repo_meta(self, owner, repo):
        r = self._get(f"{GITHUB_API}/repos/{owner}/{repo}")
        return r.json() if r is not None and r.status_code == 200 else {}

    def readme(self, owner, repo):
        r = self._get(f"{GITHUB_API}/repos/{owner}/{repo}/readme")
        if r is None or r.status_code != 200:
            return ""
        data = r.json()
        try:
            return base64.b64decode(data.get("content", "")).decode("utf-8", "ignore")
        except Exception:
            return ""

    def file_tree(self, owner, repo, branch):
        r = self._get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        if r is None or r.status_code != 200:
            return []
        return [item["path"] for item in r.json().get("tree", []) if item.get("type") == "blob"]

    def file_content(self, owner, repo, path, ref=None):
        params = {"ref": ref} if ref else {}
        r = self._get(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}", params=params)
        if r is None or r.status_code != 200:
            return ""
        data = r.json()
        if isinstance(data, list) or data.get("encoding") != "base64":
            return ""
        try:
            return base64.b64decode(data["content"]).decode("utf-8", "ignore")
        except Exception:
            return ""


# --------------------------------------------------------------------------
# Repo -> context digest
# --------------------------------------------------------------------------

INTERESTING_PATTERNS = {
    "ci": re.compile(r"^\.github/workflows/.*\.ya?ml$", re.I),
    "docker": re.compile(r"(^|/)(Dockerfile|docker-compose\.ya?ml)$", re.I),
    "tests": re.compile(r"(^|/)(tests?|__tests__|spec)/", re.I),
    "agent_config": re.compile(
        r"(^|/)(CLAUDE\.md|\.cursorrules|\.clinerules|AGENTS?\.md|copilot-instructions\.md)$",
        re.I,
    ),
    "spec_docs": re.compile(r"(^|/)(docs?/|ARCHITECTURE\.md|DESIGN\.md|SPEC\.md|adr)", re.I),
    "package_manifest": re.compile(
        r"(^|/)(package\.json|pyproject\.toml|requirements\.txt|go\.mod|Cargo\.toml|pom\.xml)$",
        re.I,
    ),
}

MAX_FILE_CHARS = 4000        # cap per fetched file, to keep prompts bounded
MAX_FILES_FETCHED = 12       # cap total files fetched in full detail
MAX_TREE_ENTRIES_LISTED = 300


def build_repo_digest(gh: GitHubClient, owner: str, repo: str) -> str:
    """Assemble a bounded-size text digest of the repo for the model to read."""
    meta = gh.repo_meta(owner, repo)
    if not meta:
        return ""

    branch = meta.get("default_branch", "main")
    tree = gh.file_tree(owner, repo, branch)
    readme = gh.readme(owner, repo)

    matched = {k: [] for k in INTERESTING_PATTERNS}
    for path in tree:
        for key, pattern in INTERESTING_PATTERNS.items():
            if pattern.search(path):
                matched[key].append(path)

    to_fetch = []
    for key in ("ci", "agent_config", "docker", "spec_docs", "package_manifest"):
        to_fetch.extend(matched[key][:3])
    to_fetch.extend(matched["tests"][:3])
    to_fetch = to_fetch[:MAX_FILES_FETCHED]

    fetched_files = {}
    for path in to_fetch:
        content = gh.file_content(owner, repo, path, ref=branch)
        if content:
            fetched_files[path] = content[:MAX_FILE_CHARS]

    lines = []
    lines.append(f"Repository: {owner}/{repo}")
    lines.append(f"Description: {meta.get('description') or '(none)'}")
    lines.append(f"Default branch: {branch}")
    lines.append(
        f"Stars: {meta.get('stargazers_count', 0)}  "
        f"Open issues: {meta.get('open_issues_count', 0)}  "
        f"Last pushed: {meta.get('pushed_at', 'unknown')}"
    )
    lines.append(f"Language (primary, per GitHub): {meta.get('language', 'unknown')}")
    lines.append("")
    lines.append(f"--- File tree ({len(tree)} files, showing up to {MAX_TREE_ENTRIES_LISTED}) ---")
    lines.append("\n".join(tree[:MAX_TREE_ENTRIES_LISTED]))
    lines.append("")
    lines.append("--- Detected signals ---")
    for key, paths in matched.items():
        lines.append(f"{key}: {len(paths)} match(es) -> {paths[:10]}")
    lines.append("")
    lines.append("--- README (truncated) ---")
    lines.append(readme[:6000] if readme else "(no README found)")
    lines.append("")
    for path, content in fetched_files.items():
        lines.append(f"--- File: {path} (truncated) ---")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Bedrock call
# --------------------------------------------------------------------------

def build_category_rubric() -> str:
    parts = []
    for key, cat in CATEGORIES.items():
        parts.append(f'- "{key}" ({cat["label"]}): {cat["description"]}')
    return "\n".join(parts)


SYSTEM_PROMPT = f"""You are an experienced engineering reviewer assessing team \
software repositories. You will be given a digest of a GitHub repository \
(metadata, file tree, detected CI/test/docs signals, README, and a few key file \
contents). Rate the repository on exactly four categories:

{build_category_rubric()}

For each category, give:
- "score": an integer from 1 to 5, where {SCORE_SCALE}
- "remark": a single sentence (no more than about 20 words) explaining the score

Base your judgment only on evidence visible in the provided digest. If evidence for a \
category is entirely absent, score it low (1-2) and say so in the remark rather than \
guessing. Do not be swayed by star counts or surface polish alone; look for substance.

Respond with ONLY a JSON object, no other text, no markdown fences, in exactly this shape:
{{
  "specification_and_architecture": {{"score": <int>, "remark": "<one sentence>"}},
  "working_software_and_delivery": {{"score": <int>, "remark": "<one sentence>"}},
  "agent_engineering_and_code_quality": {{"score": <int>, "remark": "<one sentence>"}},
  "testing_and_verification": {{"score": <int>, "remark": "<one sentence>"}}
}}
"""


def call_bedrock(bedrock_client, model_id: str, repo_digest: str, max_retries: int = 4) -> dict:
    user_msg = (
        "Here is the repository digest to evaluate:\n\n"
        f"{repo_digest}\n\n"
        "Return the JSON rating now."
    )
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = bedrock_client.converse(
                modelId=model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": user_msg}]}],
                inferenceConfig={"maxTokens": 1000, "temperature": 0},
            )
            text = "".join(
                block.get("text", "") for block in resp["output"]["message"]["content"]
            ).strip()
            text = re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip()
            return json.loads(text)
        except (json.JSONDecodeError, KeyError) as e:
            last_err = e
            log.warning("Bad model output (attempt %d/%d): %s", attempt + 1, max_retries, e)
            time.sleep(1)
        except bedrock_client.exceptions.ThrottlingException as e:
            last_err = e
            wait = 2 ** attempt
            log.warning("Bedrock throttled, sleeping %ds", wait)
            time.sleep(wait)
        except Exception as e:
            last_err = e
            log.warning("Bedrock call failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Bedrock call failed after {max_retries} attempts: {last_err}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Rate GitHub repos listed in a CSV across four categories using Bedrock.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True, help="Path to input CSV")
    ap.add_argument("--output", default="repo_ratings.csv", help="Path to output CSV")
    ap.add_argument("--repo-column", default="GitHub Repo", help="Column holding the repo URL/slug")
    ap.add_argument("--team-column", default="Team ID", help="Column holding the Team ID")
    ap.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="Bedrock model ID or inference-profile ARN")
    ap.add_argument("--aws-region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN"))
    ap.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep between repos")
    args = ap.parse_args()

    df = pd.read_csv(args.input)
    for col in (args.repo_column, args.team_column):
        if col not in df.columns:
            log.error("Column %r not found in %s. Available columns: %s",
                       col, args.input, list(df.columns))
            sys.exit(1)

    gh = GitHubClient(token=args.github_token)
    bedrock = boto3.client("bedrock-runtime", region_name=args.aws_region)

    rows = []
    total = len(df)
    for i, row in df.iterrows():
        team_id = row[args.team_column]
        repo_ref = row[args.repo_column]
        log.info("[%d/%d] Team %s -> %s", i + 1, total, team_id, repo_ref)

        result_row = {"Team ID": team_id, "Repository": repo_ref}
        for _, cat in CATEGORIES.items():
            result_row[f"{cat['label']} Score"] = ""
            result_row[f"{cat['label']} Remark"] = ""
        result_row["Error"] = ""

        parsed = parse_owner_repo(repo_ref)
        if not parsed:
            log.warning("Could not parse repo reference: %r", repo_ref)
            result_row["Error"] = "Could not parse repository URL/slug"
            rows.append(result_row)
            continue

        owner, repo = parsed
        try:
            digest = build_repo_digest(gh, owner, repo)
            if not digest:
                raise RuntimeError("Repository not found or inaccessible")
            ratings = call_bedrock(bedrock, args.model_id, digest)
            for key, cat in CATEGORIES.items():
                entry = ratings.get(key, {})
                result_row[f"{cat['label']} Score"] = entry.get("score", "")
                result_row[f"{cat['label']} Remark"] = entry.get("remark", "")
        except Exception as e:
            log.error("Failed for %s/%s: %s", owner, repo, e)
            result_row["Error"] = str(e)

        rows.append(result_row)
        time.sleep(args.sleep)

    out_df = pd.DataFrame(rows)
    out_df.to_csv(args.output, index=False)
    log.info("Wrote %d rows to %s", len(out_df), args.output)


if __name__ == "__main__":
    main()
