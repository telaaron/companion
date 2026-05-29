<!--
Thanks for the PR. Please fill in the sections below so reviewers can
move fast. The CI will run all five gates automatically.
-->

## Summary

<!-- One-paragraph description: what changed and why? -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] Feature (non-breaking change that adds capability)
- [ ] Breaking change (fix or feature that requires migration)
- [ ] Documentation only
- [ ] Refactor (no behavioural change)
- [ ] CI / build / tooling

## Related issues

<!-- Closes #123, related to #456 -->

## Screenshots / screen recordings

<!-- For UI changes: before + after. Drop a PNG or short MP4 here. -->

## Checklist

- [ ] Conventional-commit messages (`fix(scope): ...`, `feat(scope): ...`, …)
- [ ] Tests added or updated
- [ ] `uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest` passes locally
- [ ] No `# type: ignore` or `# ty: ignore` added
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` if user-visible
- [ ] Docs updated if needed (README, `docs/`, in-app help text)

## Migration notes

<!-- If this changes the DB schema, an env var, an HTTP shape, or the
auto-updater payload — describe the upgrade path. Otherwise: N/A -->

## Reviewer notes

<!-- Anything tricky a reviewer should look at first? Edge cases you
weren't sure how to handle? Performance trade-offs? -->
