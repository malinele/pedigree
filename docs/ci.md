# Running Pedigree in CI

`pedigree verify` is designed to be a CI gate. It exits 0 when there are no `error` findings, 1 when there are, and 3 when the environment is broken (missing policy, git failure). See the [user guide](user-guide.md) for the exit-code table and [policy.md](policy.md) for the rules.

Two things are required in any CI job:

1. **Full history.** The range `origin/main..HEAD` needs both sides. Use `fetch-depth: 0` on checkout.
2. **The notes ref.** `actions/checkout` does not fetch `refs/notes/*`. Without it every commit looks unattested. Fetch it explicitly:

   ```
   git fetch origin refs/notes/pedigree:refs/notes/pedigree
   ```

   This fails if the remote has no `refs/notes/pedigree` yet. Authors publish notes with `git push origin refs/notes/pedigree` after attesting.

## SARIF job with code scanning upload

This job writes a SARIF file and uploads it to GitHub code scanning, so findings appear as alerts.

```yaml
name: pedigree
on:
  pull_request:
  push:
    branches: [main]

jobs:
  verify:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Pedigree
        run: pip install pedigree

      - name: Fetch attestation notes
        run: git fetch origin refs/notes/pedigree:refs/notes/pedigree

      - name: Verify provenance
        id: verify
        continue-on-error: true
        env:
          PEDIGREE_HMAC_KEY: ${{ secrets.PEDIGREE_HMAC_KEY }}
        run: pedigree verify origin/main..HEAD --format sarif --output pedigree.sarif

      - name: Upload SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: pedigree.sarif
          category: pedigree

      - name: Fail on policy errors
        if: steps.verify.outcome == 'failure'
        run: exit 1
```

Notes:

- With `--output`, the SARIF goes to the file and a one-line summary (`N commits, N attested, N errors, N warnings`) goes to stderr, so it appears in the job log.
- `continue-on-error: true` keeps the job going when verify exits 1, so the upload step always runs. The last step then fails the job. If verify exits 3 (for example, no policy file), no SARIF file exists and the upload step will itself fail.
- Each SARIF result uses the finding's rule name as `ruleId`, level `error` or `warning`, the commit sha as a logical location, and the commit's first changed file as the physical location.
- `pip install pedigree` assumes the package is available from your index. Otherwise install from a checkout (`pip install .`) or a git URL.
- Code scanning uploads must be enabled for the repository (private repositories need GitHub Advanced Security).

### Pull-request checkouts are detached

On `pull_request` events `actions/checkout` checks out GitHub's merge commit on a detached HEAD, so there is no branch name. `require_attestation_branches` is skipped for that run, while `[[rule]]` checks still run. To enforce branch rules, also run verify on `push` to the protected branch, where the checkout is on a named branch. The job above triggers on both.

On a pull request, `origin/main..HEAD` includes the merge commit. Merge commits list no files, so path-scoped rules ignore them, but a branch-wide requirement or a rule with `paths = []` would still apply. To verify only the contributor's commits, use `origin/main..${{ github.event.pull_request.head.sha }}`.

## Plain exit-code gating

If you do not need code scanning, let the exit code fail the job:

```yaml
name: pedigree-gate
on: [pull_request]

jobs:
  gate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pedigree
      - run: git fetch origin refs/notes/pedigree:refs/notes/pedigree
      - name: Verify provenance
        env:
          PEDIGREE_HMAC_KEY: ${{ secrets.PEDIGREE_HMAC_KEY }}
        run: pedigree verify origin/main..${{ github.event.pull_request.head.sha }}
```

The step fails on exit 1 (policy errors) and exit 3 (environment errors). The text output lists each finding as `<sha7> <rule> <severity> <message>`. Use `--format json` if a later step needs to parse the results.

## The `PEDIGREE_HMAC_KEY` secret

Signature checking needs the key that was used to sign. Store it as a repository or organisation secret named `PEDIGREE_HMAC_KEY` (Settings, Secrets and variables, Actions) and pass it through `env:` as shown above. Never write the key into a workflow file, a policy file or a note.

| Situation in CI | Signature state | Effect |
|-----------------|-----------------|--------|
| Secret set, note signed with that key | `valid` | No finding |
| Secret set, note signed with another key, or altered | `invalid` | Error; the job fails |
| Secret not set, note signed | `no_key` | Warning only; `signed_required` and `signed_attestation` still fail |
| Note unsigned | `unsigned` | Warning; fails if the policy requires signing |

GitHub does not pass secrets to workflows triggered by pull requests from forks. There the key is empty, signed attestations show as `no_key`, and policies that require signatures will fail. Verify fork pull requests in a separate trusted workflow, or accept the weaker check for them.
