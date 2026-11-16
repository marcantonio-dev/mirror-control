# mirror-control

Supply-chain mirrors for code I load straight from git (pi packages, Claude plugins, GitHub
Actions and the like). Every other repo in `marcantonio-dev` is a fork of an upstream I depend on.

## How it works

- **Mirrors** are forks in this org. Actions are turned off for every repo in the org except this
  one, so upstream workflows never run here.
- **Weekly sync** (`.github/workflows/sync.yml`, Mondays 14:00 UTC) runs `sync.py` as the
  `marcantonio-dev mirror sync` GitHub App, which covers every repo in the org. For each mirror it:
  - fast-forwards the default branch from upstream. It never force-pushes, so a commit someone
    pinned can't disappear. A rewritten upstream history fails the run.
  - checks the parent against the upstream recorded in `mirrors.json` the first time the mirror
    was seen. If an upstream is deleted, GitHub can promote another fork to parent; the mirror is
    then left alone and the run fails.
  - stages the new head, and once it has sat for a week (one run), fast-forwards the mirror's
    **`vetted`** branch to it. The week is counted from when this job saw the commit, since
    commit dates can be faked.
- **`mirrors.json`** is committed back after each run, so its history is an audit log of what was
  staged and when.
- **Consumers** pin to full commit SHAs on `vetted`, for example
  `git:github.com/marcantonio-dev/ECC@<sha>` in a pi `settings.json`. Renovate opens a PR when
  `vetted` moves.

## Adding a mirror

```bash
gh repo fork <owner>/<repo> --org marcantonio-dev --clone=false --default-branch-only
```

That's all. The next run records its upstream and stages its head, and the run after that creates
`vetted`. To use it sooner, create `vetted` yourself at a commit you've reviewed (a release tag's
commit, say):

```bash
gh api repos/marcantonio-dev/<repo>/git/refs -f ref=refs/heads/vetted -f sha=<full sha>
```

## Renovate preset

`default.json` is a shared Renovate preset. A consumer repo's `renovate.json` only needs:

```json
{ "extends": ["github>marcantonio-dev/mirror-control"] }
```

It brings:

- a 7-day `minimumReleaseAge` for registry packages (npm, PyPI, crates) and GitHub Actions
- pinned Action digests
- a custom manager that bumps `git:github.com/marcantonio-dev/<repo>@<sha>` pins in JSON, YAML
  and TOML files to the head of `vetted`, with no extra wait because the mirror already had one

Forks only protect code installed from git. For registry packages, the protection is lockfiles
with integrity hashes plus the cooldown above.

## Setup

- GitHub App `marcantonio-dev mirror sync`, installed on all repos in the org, with Contents and
  Workflows read/write. Its client ID is the `MIRROR_SYNC_CLIENT_ID` variable and its private key
  is the `MIRROR_SYNC_PRIVATE_KEY` secret on this repo.
- Org Actions policy: enabled for selected repositories, only `mirror-control`.
- Renovate app installed on the consumer repos.
