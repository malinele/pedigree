# Pedigree

Git-native provenance and policy for AI-written code: who wrote it, under which contract, and does it pass your rules.

Pedigree has zero runtime dependencies: it needs only Python and `git`.

## Why

Teams increasingly cannot answer who wrote a commit: a human or an agent, under which contract, and with what evidence. Audits and AI regulations are starting to ask exactly that. Git itself does not record it, and commit trailers are free text that anyone can edit or forge. Pedigree stores the answer next to the code, in the repository, where it can be checked.

## How it works

- **Attestations** are JSON documents stored in git notes on `refs/notes/pedigree`, one per commit. They record the author type (human or agent), tool, model, contract id and an evidence hash. Notes do not rewrite history, so commits keep their shas.
- **Signing** is optional. With an HMAC-SHA256 key (read from `PEDIGREE_HMAC_KEY`), the signature is bound to the commit sha, so an attestation cannot be moved to a different commit.
- **Policy** is declarative: a `pedigree.toml` file with path globs and requirement types states which changes need which kind of attestation.
- **CI gate**: `pedigree verify` checks a range of commits against the policy and reports through exit codes, or as JSON or SARIF for code scanning.

When a commit has both an attestation and trailers, the attestation wins when classifying the author.

## Quickstart

```bash
pedigree policy-init
pedigree attest --author-type agent --tool claude --model sonnet
pedigree verify origin/main..HEAD
pedigree report
```

`policy-init` writes a starter `pedigree.toml`. `attest` attaches an attestation to `HEAD` (use `--commit SHA` for another commit). `verify` checks the range against the policy. `report` prints provenance statistics. This is the output of `pedigree report` on this repository:

```text
Pedigree report
commits            9
attested           7 (77.8%)
signed (valid)     7
human commits      2
agent commits      7
AI share of lines  97.8% (3015 of 3083)
tools:
  claude  7
```

This repository attests its own AI-written commits, using evidence from its guarded-runner build.

## Guarded runner integration

`attest --from-cco-run DIR` ingests the artifacts of a guarded-runner run directory instead of taking fields by hand. It reads `task_outcome.json` and `execution_envelope.json` and fills in:

- the tool and model (from the executor in the envelope),
- the contract id (from the outcome),
- the evidence hash (the SHA-256 of `task_outcome.json`).

The author type is set to agent.

```bash
pedigree attest --from-cco-run path/to/run-dir
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | success |
| 1 | policy violations or verification failure |
| 2 | usage error or command not implemented yet |
| 3 | repository or environment error |

## Documentation

- [User guide](docs/user-guide.md)
- [Policy reference](docs/policy.md)
- [CI integration](docs/ci.md)
- [Changelog](CHANGELOG.md)

## License

Pedigree is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
For commercial use, see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).
For contributions, see [CONTRIBUTING.md](CONTRIBUTING.md).
