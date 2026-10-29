# Policy reference: `pedigree.toml`

`pedigree verify` loads `pedigree.toml` from the repository root, or the file named by `--policy`. Create a starter file with `pedigree policy-init`. See the [user guide](user-guide.md) for the commands.

The loader is strict. An unknown key, a wrong type, a bad enum value or a duplicate rule name is an error, and `verify` exits 3 with a message naming the key and the rule. Only two top-level keys exist: `[policy]` and `[[rule]]`.

## `[policy]`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `require_attestation_branches` | array of strings | `[]` | Branch globs on which every commit in the verified range must carry an attestation |
| `signed_required` | boolean | `false` | When true, on those branches the attestation must also have signature state `valid` |

Details:

- The globs are matched against the currently checked-out branch, not against the range. On a detached HEAD there is no branch, and the check is skipped.
- An empty list turns branch enforcement off.
- A missing note produces an `attestation_required` error. With `signed_required = true`, a note whose signature state is not `valid` (`unsigned`, `no_key`, `invalid`) also produces an `attestation_required` error.
- `signed_required` only has an effect on branches matched by `require_attestation_branches`. To require signatures elsewhere, use a rule with `require = ["signed_attestation"]`.

## `[[rule]]`

Each `[[rule]]` table is evaluated against every commit in the range. Rule names must be unique.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | non-empty string | required | Shown in findings and used as the SARIF rule id |
| `paths` | array of strings | required | Globs matched against the commit's changed file paths |
| `applies_to` | `"agent"`, `"human"` or `"any"` | `"any"` | Which author type the rule covers |
| `require` | array of strings | `[]` | Requirements the commit must meet (below) |
| `max_diff_lines` | non-negative integer | unset | Largest allowed insertions plus deletions in the commit |
| `forbid` | boolean | `false` | Any commit the rule applies to is an error |

### When a rule applies

A rule applies to a commit when `applies_to` is `any` or equals the commit's author type, and at least one changed file matches one of the `paths` globs. An empty `paths = []` matches every commit. Renames are matched by their new path. Merge commits list no files, so path-scoped rules do not apply to them.

All findings produced by rules have severity `error`.

### Path globs

Globs are split on `/` and matched segment by segment.

- Within a segment, `*`, `?` and `[...]` behave as in Python's `fnmatch`, case-sensitive. A single `*` never crosses a `/`.
- A segment that is exactly `**` matches zero or more whole directories.
- The pattern must match the whole path.

| Pattern | Matches | Does not match |
|---------|---------|----------------|
| `src/**` | `src/a.py`, `src/x/y/b.py` | `lib/a.py` |
| `**/*.py` | `a.py`, `src/a.py`, `src/x/b.py` | `src/a.txt` |
| `docs/*.md` | `docs/a.md` | `docs/x/a.md` |
| `**` | every file | nothing |

### `require` values

| Value | Satisfied when | Error message |
|-------|----------------|---------------|
| `attestation` | An attestation exists and is bound to this commit's sha | `commit has no valid attestation` |
| `signed_attestation` | Signature state is `valid`: signed, bound, and matching `PEDIGREE_HMAC_KEY` | `attestation is not validly signed (signature state: ...)` |
| `human_review_trailer` | The commit message has a `Reviewed-by` or `Human-reviewed` trailer (case-insensitive) | `commit lacks a Reviewed-by or Human-reviewed trailer` |
| `contract_id` | The attestation has a non-empty `contract_id` | `attestation carries no contract_id` |

Each unmet requirement produces its own finding. An attestation bound to a different commit is ignored, so it fails `attestation`, `signed_attestation` and `contract_id`. Trailers are read from the last paragraph of the message, and every line in that paragraph must be a trailer.

### `max_diff_lines`

Insertions plus deletions for the whole commit are compared with the limit, not just the lines in matching files. Binary files count as zero. A commit above the limit gets the error `diff changes N lines, limit is M`. `0` forbids any change in commits the rule applies to.

### `forbid`

`forbid = true` makes every commit the rule applies to an error: `<author type> commits may not change <path> (forbidden by rule)`. Combine it with `applies_to` and `paths` to fence off areas. A `forbid` rule with `paths = []` and `applies_to = "any"` rejects every commit.

## Findings outside rules

These come from the engine itself, not from `[[rule]]` tables.

| Rule id | Severity | Cause |
|---------|----------|-------|
| `attestation_malformed` | error | The note is not a valid `pedigree/1` JSON document |
| `attestation_binding` | error | The attestation's `commit` is not this commit's sha |
| `attestation_signature` | error | The signature does not verify with the configured key |
| `attestation_signature` | warning | Unsigned attestation, or signed with no key configured |
| `attestation_required` | error | The branch requirement from `[policy]` is not met |

## Example 1: open-source maintainer

Goal: accept agent-assisted contributions, keep CI and release files human-only, and require review for agent changes to source.

```toml
[policy]
require_attestation_branches = []

[[rule]]
name = "agent-no-ci-or-release"
paths = [".github/**", "pyproject.toml", "CHANGELOG.md"]
applies_to = "agent"
forbid = true

[[rule]]
name = "agent-src-reviewed"
paths = ["src/**"]
applies_to = "agent"
require = ["human_review_trailer"]

[[rule]]
name = "agent-large-diff"
paths = ["**"]
applies_to = "agent"
max_diff_lines = 400
```

Contributors need no key. Agent commits are recognised from trailers or e-mail, and the maintainer adds a `Reviewed-by:` trailer when merging.

## Example 2: regulated team

Goal: every commit on release branches is attested and validly signed, and agent work traces to a contract and a reviewer.

```toml
[policy]
require_attestation_branches = ["main", "release/*"]
signed_required = true

[[rule]]
name = "agent-needs-signed-contract"
paths = ["**"]
applies_to = "agent"
require = ["signed_attestation", "contract_id", "human_review_trailer"]

[[rule]]
name = "agent-no-infra"
paths = [".github/**", "infra/**", "migrations/**"]
applies_to = "agent"
forbid = true

[[rule]]
name = "agent-small-diffs"
paths = ["**"]
applies_to = "agent"
max_diff_lines = 800

[[rule]]
name = "human-src-attested"
paths = ["src/**"]
applies_to = "human"
require = ["attestation"]
```

`release/*` matches `release/1.0` but not `release/1.0/hotfix`; use `release/**` for that. Run verify on the branch itself, not on a detached checkout, or the branch requirement is skipped.

## Example 3: solo founder

Goal: minimal friction, an audit trail for agent work, and one hard guardrail.

```toml
[policy]
require_attestation_branches = ["main"]

[[rule]]
name = "agent-no-secrets-or-ci"
paths = [".github/**", ".env*", "deploy/**"]
applies_to = "agent"
forbid = true

[[rule]]
name = "agent-tracks-contract"
paths = ["src/**"]
applies_to = "agent"
require = ["attestation", "contract_id"]
max_diff_lines = 1500
```

Unsigned attestations only produce warnings here. Add `signed_required = true` once `PEDIGREE_HMAC_KEY` is set everywhere you attest.
