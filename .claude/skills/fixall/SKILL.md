---
name: fixall
description: Use when a code review has produced issues that need to be addressed and the pull request finalized on the bmlib project.
disable-model-invocation: true
allowed-tools: Read, Edit, Bash(git add *), Bash(git commit *), Bash(git push *), Bash(git status *), Bash(git diff *), Bash(gh issue *), Bash(gh pr *), Bash(uv run pytest *), Bash(ruff *), Bash(uv run ruff *)
---

Address all issues identified in the code review one by one. If fixing them appears manageable within this session, fix them now. If not, lodge the issue on GitHub. Once all issues have been addressed, run the test suite with `uv run pytest tests/ -v` plus `ruff check .` and `ruff format --check .`, and review the code changes thoroughly against the coding conventions in CLAUDE.md. If satisfied no issues are left open, update HANDOVER.md and ROADMAP.md ONLY if necessary to reflect these changes. Then commit and push the changes into the PR.
