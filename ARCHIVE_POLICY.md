# Archive Policy

This repository uses reversible organization only. Archive means a tracked `git mv` that preserves file contents and Git history; it does not mean deletion.

## Rules

- Keep production stages, production dependencies, V12 models, published `site_data/`, database schemas, backups, and deployment helpers in their current locations until their consumers are independently verified.
- Leave pre-existing untracked files untouched.
- Do not archive a path referenced by workflows, imports, subprocess calls, Docker/systemd/launchd definitions, admin routes, tests, or deployment scripts.
- Do not archive model generations until a versioned consumer manifest proves that the files have no current or recovery consumer.
- Keep archive destinations versioned and explicit: `scripts/archive/{historical,legacy,manual,research}/` and `models/archive/`.
- Update `SCRIPT_INVENTORY.md`, documentation, and tests in the same commit as an approved move.

## Restore

For a tracked move, restore the exact path with:

```bash
git mv scripts/archive/<category>/<name> scripts/<name>
```

Then rerun the reference scan, Python compilation, pytest collection/suite, and workflow YAML parsing before committing the restoration.

## Current status

Retired source, historical model generations, notebooks, plots, reports, diagnostics, and the alternate API are preserved under the archive boundaries above. Unknown and untracked helpers remain outside the production tree and are not part of the application contract.