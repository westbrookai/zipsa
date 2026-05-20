# requires-demo Skill

A fixture for spec.requires + dynamic mount tests.

## What it does

List the directories that appear under `/projects/` (these come from
the user's `requires.project_roots` setting via dynamic mount). Emit
the contract JSON with the count.

## Steps

1. `ls /projects/` to enumerate the mounted entries.
2. Emit final JSON: `{"status":"ok","result":{"project_count": N}}`
