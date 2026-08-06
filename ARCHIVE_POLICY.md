# Archive Policy

This repository uses reversible organization only. Archive means a tracked `git mv` that preserves file contents and Git history; it does not mean deletion.

## Rules

- Keep production stages, production dependencies, V12 models, published `site_data/`, database schemas, backups, and deployment helpers in their current locations until their consumers are independently verified.
- Leave pre-existing untracked files untouched.
- Do not archive a path referenced by workflows, imports, subprocess calls, Docker/systemd/launchd definitions, admin routes, tests, or deployment scripts.
- Do not archive model generations until a versioned consumer manifest proves that the files have no current or recovery consumer.
- Keep archive destinations versioned and explicit: `scripts/archive/manual/` or `scripts/archive/historical/`.
- Update `SCRIPT_INVENTORY.md`, documentation, and tests in the same commit as an approved move.

## Restore

For a tracked move, restore the exact path with:

```bash
git mv scripts/archive/<category>/<name> scripts/<name>
```

Then rerun the reference scan, Python compilation, pytest collection/suite, and workflow YAML parsing before committing the restoration.

## Current status

No additional utility is approved for movement in the current pass. The existing `scripts/diagnostics/` and `old_scripts/` locations are already documented archive boundaries. Unknown and untracked helpers remain in place pending separate review.