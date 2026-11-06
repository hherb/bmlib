---
name: finalcheck
description: final pre-merge pass on the current PR — review, fix everything, then drive GitHub CI to green
disable-model-invocation: false
allowed-tools: Skill, Read, Edit, Bash(pwd), Bash(rm -rf dist), Bash(git *), Bash(gh *), Bash(cargo *), Bash(uv *), Bash(uvx *), Bash(env *), Bash(bash *), Bash(docker *), Bash(open -a Docker)
---
Run these steps in order on the PR for the current branch. Sessions here work in sibling
`git worktree`s (`../bmlib-<topic>`), because the Rust port may hold uncommitted work in the main
checkout, so first run `pwd && git branch --show-current && git worktree list` and `cd` into the PR's
worktree. Then find the PR with `gh pr view --json number,url,headRefOid`. If there is none, stop and
tell me.

In a worktree, run Python through its own venv: `env -u VIRTUAL_ENV uv run …` (`VIRTUAL_ENV` points at
the main checkout; create the venv with `uv venv --python 3.12 .venv && uv pip install --python
.venv/bin/python -e ".[all,dev]"` if it is missing, 3.12 being inside CI's 3.11-3.13 matrix). Put any
environment variable after `env` (`env RUSTDOCFLAGS=… cargo doc`), never as a bare `VAR=… cmd` prefix,
which `allowed-tools` does not match.

1. **Review.** Invoke the `pr-review-toolkit:review-pr` skill via the Skill tool and let it finish.
2. **Fix.** Merge `origin/main` into the branch first (`git fetch origin && git merge origin/main`;
   resolve conflicts, never rebase a pushed branch). Then apply the `fixall` skill to that review's
   findings. It is not model-invocable, so read `.claude/skills/fixall/SKILL.md` and follow it, but run
   step 3's commands in place of its own: the CI-pinned `uvx ruff@0.15.20` rather than bare `ruff`, and
   pytest under `env -u VIRTUAL_ENV` with the PostgreSQL DSN and `rerun_rust_oracle.py`. It ends by
   committing and pushing into the PR. Before moving on, confirm the push landed:
   `git fetch origin && git status -sb` must show no `ahead`, and the PR's `headRefOid` must equal
   `git rev-parse HEAD`. Findings deferred to an issue go in the PR body and `HANDOVER.md`, and a
   closing keyword is never written next to an issue number the PR does not close.
3. **Drive CI to green.** Loop:
   - Watch the checks for the pushed head commit with `gh pr checks --watch --fail-fast`. Checks can take a
     minute to register, so wait if it reports none yet. No CI job is path-gated (`CI success` fails on a
     skipped job), so a missing check is stuck, not normal. CodeQL comes from GitHub's default setup and
     does not listen for a PR's `reopened` action: a PR with no CodeQL run needs a fresh commit.
   - All green → done. Report the PR URL and the final check list. "All green" means the `CI success`
     aggregate passed, the ruleset's CodeQL code scanning passed (the `CodeQL`, `Analyze (python)` and
     `Analyze (actions)` checks), **and** `gh pr view --json mergeStateStatus` reads `CLEAN`: the
     `protect_main` ruleset's code-quality rule posts no check of its own and shows up only there. The
     rules are at `gh api repos/hherb/bmlib/rules/branches/main`; `branches/main/protection` returns 404
     by design.
   - A check failed → read the cause: `gh run list --branch "$(git branch --show-current)" --limit 5`, then
     `gh run view <run-id> --log-failed`. Reproduce it locally with the command the failing job runs
     (see `.github/workflows/ci.yml`; the `cargo` commands run in `rust/`):
     - `Lint and format` → `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .` (the CI pin,
       never the venv's ruff)
     - `Types (Python 3.x)` → `env -u VIRTUAL_ENV uv run mypy`, with no arguments; for one matrix version,
       `env -u VIRTUAL_ENV uv run --isolated --python 3.x --extra all --extra dev mypy` (without
       `--isolated`, a version that differs from the venv's makes uv delete and rebuild `.venv` without
       the extras)
     - `Test (Python 3.x)` → `env -u VIRTUAL_ENV BMLIB_TEST_POSTGRESQL_DSN="host=localhost port=5532
       dbname=bmlib_test user=postgres" BMLIB_REQUIRE_POSTGRESQL=1 uv run pytest tests/ -v`, then
       `env -u VIRTUAL_ENV uv run python scripts/rerun_rust_oracle.py` (port 5532 is the local
       PostgreSQL 18, CI's `postgres:18`)
     - `Build distribution` → `rm -rf dist && uv build && uvx twine check --strict dist/*` (a reused
       worktree's `dist/` holds old builds), then install the wheel in a fresh venv and `import bmlib`
     - `Rust lint and format` → `cargo fmt --check`; `cargo clippy --all-targets -- -D warnings`, and the
       same with `--features postgres`, `--features html` and `--features pdf`;
       `cargo clippy -p bmlib --no-default-features --all-targets -- -D warnings`;
       `cargo clippy -p bmlib-ffi --all-targets -- -D warnings` and with `--features http-sketch,bindgen`;
       `env RUSTDOCFLAGS="-D warnings" cargo doc --no-deps` (plus `--features html`, `--features pdf`, and
       `-p bmlib --no-default-features`)
     - `Rust test (<label>)` → `cargo test`, `cargo test --features pdf,html`,
       `cargo test -p bmlib --no-default-features`, or `cargo test -p bmlib-ffi --features http-sketch`
     - `Rust PostgreSQL (live server)` → `env BMLIB_PG_TESTS=1 BMLIB_PG_HOST=localhost BMLIB_PG_PORT=5532
       BMLIB_PG_USER=postgres cargo test --features postgres`
     - `Rust test (embed)` → each command under `env CARGO_PROFILE_DEV_DEBUG=0` (llama.cpp links are
       huge): `cargo clippy --all-targets --features embed -- -D warnings`,
       `env CARGO_PROFILE_DEV_DEBUG=0 RUSTDOCFLAGS="-D warnings" cargo doc -p bmlib --no-deps --features
       embed`, and
       `cargo test -p bmlib --features embed`
     - CodeQL → read the alert (`gh api 'repos/hherb/bmlib/code-scanning/alerts?ref=refs/pull/<n>/merge'`,
       quoted, since zsh globs the `?`) and fix the code it names.

     Judge each command by its **exit code**, never by filtered output: `cmd | tail` reports `tail`'s
     status. Fix the root cause, using TDD where it is a code bug, and check `docs/DECISIONS.md` before
     "fixing" anything that looks deliberate. Confirm the fix passes locally, commit with a message naming
     the CI failure, push, and loop again.
   - **Linux-only failures** (CI is ubuntu-latest and macOS passes locally): reproduce in Docker
     (`python:3.11`/`3.13` or `rust:latest`; start the daemon with `open -a Docker`). Don't guess.
   - **A job that is CANCELLED with no log** was killed, not slow. The `concurrency` group cancels a PR
     run superseded by a newer push, which is normal. Any other cancellation is a fault to explain, not a
     flake to rerun. A link-time `ld` bus error is a full runner disk (fc374986), not a flake either.
   - If the failure is not caused by the code (runner/infra outage, network flake, a crates.io or PyPI
     fetch), rerun once with `gh run rerun <run-id> --failed`. If it fails the same way again, stop and
     report rather than changing code to work around it. The Actions API returns empty or stale data during
     an outage, so re-query before concluding a run never happened.
   - Never "fix" CI by skipping, `#[ignore]`-ing, `pytest.mark.skip`-ping or deleting tests, by weakening
     lint, mypy or CI config, guards or thresholds, by unsetting `BMLIB_REQUIRE_POSTGRESQL`, or by adding a
     `# type: ignore`/`noqa`/`#[allow]` without the reason at the site, without my explicit permission.
     If the same failure survives three fix attempts, stop and report what you tried.
4. **Stop at green.** Do not merge: I review and merge bmlib PRs. Report anything CI does not cover that
   the PR touches, such as the live samplers in `scripts/` (run them only if I ask, since they hit live
   APIs) or the release gates in `release.yml`.
