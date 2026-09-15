# repo_rater

Rates the GitHub repos listed in a CSV on four categories using Gemini, and
writes the scores + one-line remarks to an output CSV.

## Categories scored

1. **Specification and Architecture** — documented, deliberate decisions and a
   clear line from intent to implementation.
2. **Working Software and Delivery** — does it run, and how repeatable is the
   path from repo to a running app?
3. **Agent Engineering and Code Quality** — deliberate AI-agent steering vs.
   generic-assistant output, and the quality of what came out the other end.
4. **Testing and Verification** — automated verification, wired into the
   pipeline (not just present locally).

## Setup

```bash
pip install -r requirements.txt

export GEMINI_API_KEY="your-key-here"
export GITHUB_TOKEN="your-github-token"   # strongly recommended — see below
```

Without `GITHUB_TOKEN`, the GitHub API is limited to 60 requests/hour, which
you will burn through in a handful of repos (each repo takes ~4-6 API calls).
A [personal access token](https://github.com/settings/tokens) (no special
scopes needed for public repos) raises that to 5,000/hour.

## Usage

```bash
python rate_repos.py --input teams.csv --output ratings.csv
```

By default the script expects a `Team ID` column and a `Repo URL` column. If
your CSV uses different headers:

```bash
python rate_repos.py \
  --input teams.csv --output ratings.csv \
  --id-column "TeamID" --repo-column "GitHub Link"
```

Useful flags:

| Flag | Default | Purpose |
|---|---|---|
| `--scale` | `5` | Top of the rating scale (scores are 1..N) |
| `--model` | `gemini-2.5-flash` | Gemini model to call |
| `--limit N` | none | Only process the first N rows — good for a quick test |
| `--workers N` | `1` | Parallel workers. Keep this low to stay under API rate limits |
| `--no-score` | off | Only run the GitHub inspection step and skip Gemini (useful to sanity-check repo parsing before spending API calls) |

**Resumable:** if `--output` already has rows for some team IDs, the script
skips those and only processes what's left — so a crash or rate-limit hit
partway through a big batch doesn't cost you a re-run from scratch.

## What gets sent to Gemini

For each repo the script pulls (via the GitHub REST API, no cloning):

- Repo metadata (description, language, stars, last push, archived status)
- The full file tree (used to detect CI config, test directories, Docker/IaC
  files, architecture docs, and AI-agent config files like `CLAUDE.md`,
  `AGENTS.md`, `.cursorrules`)
- The README, truncated
- An architecture/design doc excerpt, if one exists
- An agent-config file excerpt, if one exists
- A CI workflow file excerpt, if one exists

That evidence is assembled into a text dossier and sent to Gemini with a
JSON schema forcing a `{score, remark}` pair per category, so parsing is
reliable.

## Output columns

`Team ID, Repo URL, Status, <Category> - Score, <Category> - Remark (x4),
Overall Average, Notes`

`Status` is `OK` or `ERROR` (repo not found, unparseable URL, API failure,
etc. — the reason goes in `Notes`).

## Limitations / things worth knowing

- Scoring is only as good as what's detectable from the repo's file tree and
  a few key files — it doesn't run the code or check that CI actually passes,
  only that CI/tests are configured.
- Very large repos may have a truncated file tree (GitHub caps the tree API);
  this is flagged internally but won't block scoring.
- Private repos need a `GITHUB_TOKEN` with access to them.
