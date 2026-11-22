# Security

## Reporting

Open a private security advisory on the GitHub repository (Security tab, "Report a vulnerability"), or email
william.caban@gmail.com. Do not put keys or exploit details in a public issue.

## Known vulnerabilities in dependencies: how they are caught and tracked

imgate has one runtime dependency (`openai`) and one dev dependency (`ruff`), both pinned in `uv.lock`.

| Where | What | When |
|---|---|---|
| pre-commit hook `uv-audit` | `uv audit` on the lockfile (OSV database) blocks the commit on a finding | every commit, once installed |
| `security` workflow | same audit in CI | every push to main, every PR, weekly (Monday) |
| GitHub issue labelled `security` | the weekly or main run opens it, or comments on the open one, with the audit output | when the audit fails |
| Dependabot alerts and security updates | GitHub's own advisory matching, plus a PR that bumps the package | continuous |
| Dependabot version updates | weekly PRs for `uv` and `github-actions` | weekly |

`uv audit` is experimental in uv 0.13.9, so CI pins that uv version. It sends package names and versions to OSV,
nothing else from the repo.

To accept a finding that has no fix, use `--ignore-until-fixed ID` in both `.pre-commit-config.yaml` and
`.github/workflows/security.yml`, and record the reason and the review date in the tracking issue.

## Repository protection (public repo, set 2026-11-22)

- `main` is protected: changes arrive by pull request from a `work/<slug>` branch, and the checks `test (3.12)`,
  `test (3.13)` and `audit` must pass on a branch that is up to date with `main`. History stays linear (squash or
  rebase merges), force-push and deletion are blocked, review conversations must be resolved, and the rules apply to
  administrators too. No approving review is required because there is one maintainer.
- Secret scanning with push protection, private vulnerability reporting and Dependabot security updates are on.
- Hooks and CI are the gate for known vulnerabilities; branch protection is what stops a bypassed hook
  (`--no-verify`) from reaching `main`.

## Local setup

    uvx pre-commit@4.7.0 install

If you are offline, `SKIP=uv-audit git commit ...` skips the audit for that commit. CI still runs it.

## Secrets

API keys are read only from environment variables and never written to config, logs, sidecars or commits
(`redact()` covers error paths). There is no secret scanner in the hooks yet.
