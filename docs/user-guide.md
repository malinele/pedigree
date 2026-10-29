# Pedigree user guide

Pedigree is a git-native provenance and policy tool for AI-written code. It records who wrote each commit (a human or an agent), with which tool and model, and under which contract. Each record is an attestation: a small JSON document, optionally HMAC-signed, stored as a git note on `refs/notes/pedigree`. A declarative policy file, `pedigree.toml`, is evaluated against a range of commits by `pedigree verify`, which is meant to run in CI.

Pedigree needs Python 3.12 or newer and `git`, and has no other dependencies. Install it so the `pedigree` console script (`pedigree.cli:main`) is on your `PATH`.

Related: [policy reference](policy.md), [CI integration](ci.md).

## Commands

| Command | Purpose |
|---------|---------|
| `pedigree policy-init` | Write a starter `pedigree.toml` |
| `pedigree attest` | Attach an attestation to a commit |
| `pedigree verify [RANGE]` | Evaluate the policy against a commit range |
| `pedigree report [RANGE]` | Print provenance statistics for a range |

Global options: `--repo PATH` (repository path, default `.`) and `--version`. They go before the command: `pedigree --repo ../app verify main..HEAD`.

## Quickstart

### 1. Create a policy

```
pedigree policy-init
```

This writes `pedigree.toml` in the repository root and prints its path. If the file exists, the command exits 1 unless you pass `--force`. The starter policy has three rules: agent commits touching `src/**` need an attestation, agents may not change `.github/**`, and agent commits changing more than 800 lines need a `contract_id`. See [policy.md](policy.md).

### 2. Attest a commit

```
pedigree attest --author-type agent --tool claude-code --model claude-opus-4 --contract-id pg-042
```

Options:

- `--commit SHA`: commit to attest (default `HEAD`).
- `--author-type {agent,human}`: required, unless `--from-cco-run` is used.
- `--tool`, `--model`, `--contract-id`: free text, stored as empty strings when omitted.
- `--evidence FILE`: records the SHA-256 of that file. The file itself is not stored.
- `--force`: overwrite an existing attestation. Without it, attesting an already-attested commit exits 1.

The command prints the stored JSON. Fields: `schema` (`pedigree/1`), `commit`, `author_type`, `tool`, `model`, `contract_id`, `evidence_sha256`, `created_at` (UTC) and `signature`.

### 3. Attest from a guarded-runner run

```
pedigree attest --from-cco-run path/to/run-dir
```

The run directory must contain two JSON files:

- `task_outcome.json`: its `contract_id` becomes the attestation's contract id, and the SHA-256 of the file's exact bytes becomes `evidence_sha256`.
- `execution_envelope.json`: `executor.name` becomes `tool` and `executor.model` becomes `model`.

Missing or non-string values become empty strings. The author type is always `agent`. `--from-cco-run` cannot be combined with `--tool`, `--model`, `--contract-id` or `--evidence`, and it rejects `--author-type human`; both are usage errors (exit 2). A missing file or invalid JSON in the run directory exits 3.

### 4. Verify a range

```
pedigree verify origin/main..HEAD
```

`RANGE` is any git revision range. With no argument only `HEAD` is checked. Options:

- `--policy PATH`: policy file (default `<repo>/pedigree.toml`). A missing policy file exits 3.
- `--format {text,json,sarif}`: default `text`.
- `--output PATH`: write the report to a file. The one-line summary then goes to stderr.

Text output has one line per finding, `<sha7> <rule> <severity> <message>`, then a summary such as `12 commits, 9 attested, 1 errors, 2 warnings`.

Only findings with severity `error` fail the run. Warnings (an unsigned attestation, or a signed one when no key is configured) do not.

### 5. Report on a range

```
pedigree report origin/main..HEAD
pedigree report origin/main..HEAD --format json --policy pedigree.toml
```

The report gives total commits, attested count and percentage, validly signed count, human and agent commit counts, the share of changed lines (insertions plus deletions) that came from agent commits, per-tool attestation counts, and up to five unattested agent commits. `--policy` adds an error and warning count. `report` exits 0 whenever it can run, whatever the findings.

## How commits are classified

A commit is `agent` or `human`. If an attestation bound to the commit exists, its `author_type` decides. Otherwise Pedigree uses heuristics, in this order:

1. A `Pedigree-Author-Type: agent` trailer.
2. A `Co-Authored-By` trailer containing `claude`, `copilot`, `gpt`, `gemini` or `bot`.
3. An author e-mail ending in `[bot]@users.noreply.github.com`.

Anything else is `human`. A person can omit these hints, so rules that matter should use `require = ["attestation"]`.

## Signing

Set `PEDIGREE_HMAC_KEY` to a secret string before running `attest` and `verify`:

```
export PEDIGREE_HMAC_KEY='<your key>'
```

With the key set, `attest` adds an HMAC-SHA256 `signature` over the canonical JSON of every other field. Without it, `attest` stores an unsigned attestation and warns on stderr. The key is shared, so anyone who holds it can create valid attestations.

At verify time each commit has one signature state:

| State | Meaning | Finding |
|-------|---------|---------|
| `valid` | Signed, and the signature matches the key | none |
| `invalid` | Signature does not match the key, or the attestation is bound to a different commit | error |
| `unsigned` | The attestation has no signature | warning |
| `no_key` | Signed, but `PEDIGREE_HMAC_KEY` is unset at verify time | warning |
| `none` | No attestation on the commit | none |

### Tamper binding

The commit sha is part of the signed payload. If someone copies a note from one commit onto another, the attestation's `commit` field no longer equals the commit it sits on. Verify reports an `attestation_binding` error and ignores that attestation: the commit counts as unattested and is classified by the heuristics above. Editing any field of a signed attestation breaks the signature (`invalid`).

An unsigned attestation is still bound to its sha, but anyone with write access to the notes ref can forge it. Use `signed_required` or the `signed_attestation` requirement when that matters.

Notes are keyed by commit sha. After a rebase, amend or squash the commits have new shas and their old notes no longer apply; re-attest them.

## Sharing notes between machines

Git does not push or fetch notes refs by default. Push them explicitly:

```
git push origin refs/notes/pedigree
```

Fetch them with a refspec:

```
git fetch origin refs/notes/pedigree:refs/notes/pedigree
```

If two people added notes independently, that fetch is rejected as non-fast-forward. Either force it (this discards local-only notes):

```
git fetch origin +refs/notes/pedigree:refs/notes/pedigree
```

or fetch to a temporary ref and merge:

```
git fetch origin refs/notes/pedigree:refs/notes/pedigree-remote
git notes --ref=refs/notes/pedigree merge refs/notes/pedigree-remote
```

## Branch rules and detached HEAD

`require_attestation_branches` is matched against the currently checked-out branch, not against the range. On a detached HEAD there is no branch name and the check is skipped. Pull-request checkouts in CI are typically detached; see [ci.md](ci.md).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success. For `verify`: no `error` findings |
| 1 | `verify` found errors; or `attest` / `policy-init` refused to overwrite without `--force` |
| 2 | Usage error (bad or conflicting arguments) |
| 3 | Repository or environment error: git failure, missing or invalid policy, bad run directory, unreadable evidence file, unwritable `--output` |
