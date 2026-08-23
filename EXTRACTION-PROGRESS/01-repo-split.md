# Phase 1 — Repo split with history

**New repo:** `/Users/jonas/Dev/forge-scad-editor` (sibling to this repo, i.e. `../forge-scad-editor`)

## What happened

1. Installed `git-filter-repo` (`brew install git-filter-repo` — not preinstalled).
2. Cloned `fordge-ledger` into a scratch directory and ran `git filter-repo`
   with the exact `--path` list from the plan (§Phase 1), covering the
   editor-owned frontend files, `lang-openscad/`, the tools backend, and
   `backend/app/utils.py` (needed for `slugify`/`safe_join`).
3. `git log --oneline` on the filtered clone: **17 → 12 commits**, all
   touching only editor-owned paths, oldest-first blame intact back to
   `408a88f "first version"`.
4. Moved the filtered clone to `../forge-scad-editor`. No restructuring
   commit was needed beyond this move — the `--path` list already used the
   plan's target relative layout (`backend/app/api/tools.py`,
   `frontend/src/components/…`, etc.), so the filtered tree landed directly
   in the right shape.

## Deviations from the plan

- **No push to a fresh GitHub remote.** The plan's Phase 1 pushes to
  `git@github.com:Huntler/forge-scad-editor.git`. That repository doesn't
  exist and creating one plus pushing is an outward-facing, credentialed
  action — out of scope for an unattended extraction. `../forge-scad-editor`
  is a complete, self-contained local git repo (`git log` shows the full
  filtered history); pushing it to GitHub is a follow-up the user can do
  with `git remote add origin … && git push -u origin main` whenever ready.
- `git remote remove origin` was run on the filtered clone (it pointed back
  at `fordge-ledger`, which is not this repo's origin).

## Verified

```
$ cd ../forge-scad-editor && git log --oneline | wc -l
12
$ git log --oneline
7d27be6 add file references within a project
f4d0845 fix share tools between scad files of the same project
d72c79e show parts in tree with hide/show
6a6ac49 add build plate & axes to renderer
47f51c4 add nut; add colored icons; remove private modules from tooltip
39a5319 add tools autocomplete and tooltip info
d4b3caa add tools
6cfc043 add faster scad renderer
b920f93 add quality select for render and export stl
64ec3e4 add language tools to scad
b4752c9 add scad editor
408a88f first version
```

Next: [Phase 2 — standalone editor](02-standalone-editor.md).
