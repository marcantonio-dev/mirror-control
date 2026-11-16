"""Sync every mirror in the org from upstream and advance its `vetted` branch after a cooldown.

A mirror is any fork the GitHub App can see. Each run, for every mirror:

1. The first time a mirror is seen, its parent is recorded in mirrors.json as its upstream. If
   GitHub later reports a different parent (an upstream was deleted and GitHub promoted another
   fork in the network), the mirror is left alone and the run fails.
2. The default branch is fast-forwarded from upstream. A rewritten upstream history is reported
   and never forced, so commits that consumers pin stay in the mirror.
3. The default branch head is staged with the time this job first saw it. A head staged by an
   earlier run (at least COOLDOWN ago) is fast-forwarded onto `vetted`, creating it if needed.

Consumers pin to commits on `vetted`, and bump-pins.yml opens a PR when it moves. The cooldown is
measured from when this job saw a commit, not from commit dates, which an attacker can set.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
COOLDOWN = timedelta(days=6)  # one weekly run, with slack for cron jitter
VETTED = "vetted"
STATE_FILE = "mirrors.json"


def github(method, path, body=None):
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else None


def installation_forks():
    forks, page = [], 1
    while True:
        status, body = github("GET", f"/installation/repositories?per_page=100&page={page}")
        if status != 200:
            raise RuntimeError(f"listing repositories failed with HTTP {status}: {body}")
        forks += [repo["full_name"] for repo in body["repositories"] if repo["fork"]]
        if len(body["repositories"]) < 100:
            return sorted(forks)
        page += 1


def branch_head(mirror, branch):
    status, body = github("GET", f"/repos/{mirror}/git/ref/heads/{branch}")
    return body["object"]["sha"] if status == 200 else None


def fast_forward_upstream(mirror, branch):
    status, body = github("POST", f"/repos/{mirror}/merge-upstream", {"branch": branch})
    if status == 200:
        return None
    message = (body or {}).get("message", "")
    if status == 409:
        return f"upstream history diverged ({message}); not forcing it"
    return f"sync failed with HTTP {status}: {message}"


def advance_vetted(mirror, sha):
    """Fast-forward `vetted` to sha, creating it if missing. Returns an error or None."""
    if branch_head(mirror, VETTED) is None:
        payload = {"ref": f"refs/heads/{VETTED}", "sha": sha}
        status, body = github("POST", f"/repos/{mirror}/git/refs", payload)
    else:
        payload = {"sha": sha, "force": False}
        status, body = github("PATCH", f"/repos/{mirror}/git/refs/heads/{VETTED}", payload)
    if status in (200, 201):
        return None
    return f"moving {VETTED} to {sha[:12]} failed with HTTP {status}: {(body or {}).get('message')}"


def process(mirror, record, now):
    """Update record in place. Returns (action, problem or None)."""
    _, repo = github("GET", f"/repos/{mirror}")
    parent = (repo.get("parent") or {}).get("full_name")
    if "upstream" not in record:
        record["upstream"] = parent
    if parent != record["upstream"]:
        return "skipped", f"parent is now {parent}, expected {record['upstream']}"

    default_branch = repo["default_branch"]
    problem = fast_forward_upstream(mirror, default_branch)
    head = branch_head(mirror, default_branch)
    vetted = branch_head(mirror, VETTED)
    staged = record.get("staged")
    action = "up to date"

    if staged and now - datetime.fromisoformat(staged["seen"]) < COOLDOWN:
        return "cooling down", problem
    if staged and staged["sha"] != vetted:
        error = advance_vetted(mirror, staged["sha"])
        if error:
            return "vetted not moved", problem or error
        vetted = staged["sha"]
        action = f"vetted → {vetted[:12]}"

    if head == vetted:
        record.pop("staged", None)
    else:
        record["staged"] = {"sha": head, "seen": now.isoformat(timespec="seconds")}
        action = f"{action}; staged {head[:12]}" if action != "up to date" else f"staged {head[:12]}"
    return action, problem


def main():
    now = datetime.now(timezone.utc)
    with open(STATE_FILE) as file:
        state = json.load(file)

    rows, problems = [], []
    for mirror in installation_forks():
        record = state.setdefault(mirror, {})
        action, problem = process(mirror, record, now)
        rows.append(f"| `{mirror}` | `{record.get('upstream')}` | {action} |")
        if problem:
            problems.append(f"{mirror}: {problem}")

    with open(STATE_FILE, "w") as file:
        file.write(json.dumps(state, indent=2, sort_keys=True) + "\n")

    summary = ["| mirror | upstream | action |", "|---|---|---|", *rows]
    if problems:
        summary += ["", "**Problems:**", "", *[f"- {p}" for p in problems]]
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as file:
        file.write("\n".join(summary) + "\n")
    print("\n".join(summary))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
