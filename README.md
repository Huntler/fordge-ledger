# Forge Ledger

A self-hosted app to manage 3D printing projects, custom designs, and MakerWorld
publishing. Single user, no login, LAN only.

Built to the spec in [`3d-print-manager-plan.md`](3d-print-manager-plan.md).

---

## The governing principle

**The folders are the product. The database is a cache.**

Every piece of durable metadata is written into the project folder in a format
you can read without this app:

| What | Where |
|---|---|
| Title, status, tags, licence, attribution, models | `project.yaml` |
| Free-text notes | `notes.md` |
| Print settings *and* the job record | `prints/<name>.json` next to the 3MF |
| Snapshot metadata | `_versions/vNNN__date__label/version.yaml` |
| Image variants and their source files | `project.yaml` under `images:` |
| Publish draft | `publish/makerworld/description.md` + `fields.yaml` |
| Templates and snippets | `_shared/templates/`, `_shared/snippets/` |
| LLM connection and system prompt | `_shared/llm.yaml` |

SQLite exists only to make the UI fast. Delete `data/forge.db`, restart, and a
full rescan rebuilds it from the folders. Backups are a folder copy. There are
no schema migrations for the same reason: when the cache schema changes the app
drops it and rescans, because the folders already hold everything.

---

## Try it first, without touching your files

A throwaway instance seeded with four sample projects — a published one with a
failed print and a snapshot, one with an unfiled file, one mid-print, and an
empty one for the empty states. Nothing it does can reach your real library.

```bash
./test-instance.sh          # builds if needed, runs in the foreground
./test-instance.sh -d       # detached
./test-instance.sh stop
```

Then open <http://localhost:8001>. `/library` and `/data` are tmpfs, so the
sample library lives in RAM and is gone when the container stops. There are no
bind mounts at all.

**Port 8001 is always the throwaway one; the real instance is 8000.** It is easy
to point a tool at the wrong one and wonder where three unfamiliar projects came
from, so the demo instance now says what it is: `/api/health` reports
`demo_instance: true`, the MCP server calls itself `forge-ledger-demo` and leads
its handshake instructions with a warning, and `tools/forge-upload.py` refuses to
be quiet about it.

Or with compose, if you prefer:

```bash
container-compose -f docker-compose.test.yml up -d --build
container-compose -f docker-compose.test.yml down
```

Same result, one caveat: container-compose ignores the `tmpfs` key, so the data
sits on the container's own filesystem instead of in RAM. It is still discarded
— `down` then `up` comes back with the pristine samples — and it still never
touches the host. The script is the one that gives you real tmpfs.

---

## Deploying to the NAS

**Build on the NAS, not on your Mac.** The DXP4800 Plus is an Intel i5, so it
needs an **amd64** image, and anything built on Apple Silicon is arm64 and will
not start there. Building on the NAS sidesteps the whole issue, and its i5 is
faster at it than emulation would be.

1. **Copy the source onto the NAS.** It is a file server — put this folder on a
   share, over SMB in Finder or with rsync:

   ```bash
   rsync -av --exclude node_modules --exclude .venv --exclude data \
     ~/Dev/Privat/"Forge Ledger"/ jonas@<nas>:/volume1/docker/forge-ledger/
   ```

2. **Point it at your designs.** SSH in, then:

   ```bash
   cd /volume1/docker/forge-ledger
   cp .env.example .env && nano .env      # set LIBRARY_PATH
   ```

3. **Start it.**

   ```bash
   sudo docker compose up -d --build
   ```

   If `docker compose` is missing on UGOS, `docker-compose up -d --build` or the
   Docker app's own compose/project import will do the same job.

Open `http://<nas>:8000`. Check `http://<nas>:8000/api/health` shows
`"demo_instance": false` and a `library_path` with your real project count.

The first boot scans the whole library, so give it a minute if the folder is
large. `/data` holds the SQLite cache and thumbnails — keep it on disk so a
restart does not trigger a full rescan, but it is rebuildable if you lose it.

### If you would rather not build on the NAS

Apple's `container` does cross-compile correctly — verified, and it takes about
a minute:

```bash
container build --arch amd64 -t forge-ledger:latest .
container image save forge-ledger:latest -o forge-ledger-amd64.tar   # ~74MB
```

Copy the tar over and `sudo docker load -i forge-ledger-amd64.tar`. One caveat:
`container image save` writes an **OCI-layout** archive, which recent Docker
accepts but older builds reject with "invalid tar header". If it refuses, fall
back to building on the NAS. Then run the compose file with `build:` removed, or
create the container in the UGOS Docker app and set the two volumes
(`/library`, `/data`) and port 8000 by hand.

### LLM polish

Configured in the UI under Settings → Local LLM, and `FORGE_OLLAMA_URL` in
`.env` seeds it for the very first boot.

### Local development

```bash
# Backend — http://localhost:8000
cd backend
uv venv && uv pip install -e '.[dev]'
FORGE_LIBRARY_PATH=../library FORGE_DATA_PATH=../data \
  .venv/bin/uvicorn app.main:app --reload

# Frontend — http://localhost:5173, proxies /api to :8000
cd frontend && npm install && npm run dev
```

Tests: `cd backend && .venv/bin/python -m pytest`

---

## The 3MF parser

Milestone M1 in the plan, and deliberately built first: if the format
assumptions are wrong, you want to know before there is a UI on top of them.
It runs standalone.

```bash
forge-parse3mf ~/prints/*.3mf                       # summary per file
forge-parse3mf tray.3mf --json                      # full parse
forge-parse3mf tray.3mf --extract-previews ./out    # plate PNGs
```

**Run this against 20 of your own files before trusting anything downstream.**
Bambu Studio changes these files between releases. Three defences are built in:

- Every result is tagged with `parser_version`.
- The untouched slicer profile is kept as `raw_settings`, so an unrecognised key
  is never lost.
- Missing or malformed sections produce warnings, not exceptions — a file that
  is only half-understood is still ingested.

---

## Connecting a local LLM

Settings → **Local LLM**. Two providers:

| | Default port | Gotcha |
|---|---|---|
| **Ollama** | 11434 | Binds `127.0.0.1` only. Start it with `OLLAMA_HOST=0.0.0.0` or it will refuse every other machine. |
| **LM Studio** | 1234 | Needs *Serve on Local Network* switched on in the Developer/Server tab. |

Enter the LAN address of the machine running it — a bare IP is enough, the port
is filled in for you, and a pasted `.../v1` suffix is trimmed. **Test** probes
the server and lists the models actually installed, without saving, so a wrong
address never clobbers a working one. Pick a model, then Save.

The request is made by the backend, not your browser. That is what lets a NAS
container reach a desktop across the LAN — and it means `localhost` in that box
would be *the container*, not your desktop. The app says so when a connection to
a loopback address fails, because it is by far the easiest way to get this wrong.

**System prompt** is a plain textbox, pre-filled with a default that keeps facts
and Markdown structure intact and forbids inventing settings. Edit it freely;
clearing it restores the default. Everything lives in `_shared/llm.yaml`, so it
is editable by hand — hit *Reload* after doing that.

Polish stays a button, never a gate: with nothing configured it is greyed out
with a link to Settings, and the rest of the app is unaffected.

### While it is thinking

A local model takes 15–60 seconds, which is far too long to leave a button
looking stuck. Clicking **Polish with LLM** opens a dialog straight away showing
the model, a running elapsed counter and a **Cancel** button. When it finishes,
the same dialog becomes the accept/reject diff — nothing touches your text until
you accept.

Cancel is real, not cosmetic. The run is an asyncio task on the server; aborting
it closes the HTTP connection, and both Ollama and LM Studio stop generating when
that happens — so it actually frees the model on the other machine. Closing the
dialog with ✕ or Escape cancels too, rather than leaving a run orphaned.

Mechanically, `POST /api/llm/polish` returns a run id immediately (202), the UI
polls `GET /api/llm/polish/{id}`, and `POST /api/llm/polish/{id}/cancel` stops
it. The progress bar is deliberately indeterminate: neither provider reports
progress for a non-streamed completion, so a percentage would be invented.

---

## MCP server — letting an agent file projects for you

The app exposes an MCP server at `/mcp`, so Claude (or any MCP client) can read
the library and create projects in it. The case it is built for: project folders
sitting on your own computer that you want turned into Forge Ledger projects.

```bash
claude mcp add --transport http forge-ledger http://<nas>:8000/mcp/
```

HTTP rather than stdio, because the agent runs on your machine and the app runs
in a container elsewhere. The server can never see your Mac's filesystem, so a
path is no use to it.

### Getting files in without burning your context

`upload_file` takes base64 inline. That is fine for a program and ruinous for an
LLM calling it directly: in one real session an 88KB image cost about **112,000
tokens**, and ordinary meshes here run to 10–26MB. There are two better routes,
and the tool descriptions now push agents towards them.

**If the agent and the server share a filesystem** — when the library is
bind-mounted somewhere the agent can also see — copy files straight in and call
`rescan_library()`. The concrete test is whether the `folder` path returned by
`create_project()` exists on the agent's machine.

**Otherwise, use the bundled uploader**, which calls the very same MCP endpoint
from a subprocess so the base64 never passes through the model:

```bash
# a whole folder, project created from its README title
tools/forge-upload.py --url http://nas:8000/mcp/ --create-from ~/Designs/Filament\ Guide

# into an existing project
tools/forge-upload.py --project filament-guide ~/Designs/Guide/*.stl

tools/forge-upload.py --project filament-guide --dry-run ~/Designs/Guide/
```

It puts meshes under `models/<name>/`, files them in `project.yaml` so nothing is
left "unfiled", uses the README as the project notes, and warns loudly if it is
pointed at the demo instance. Standard library only. A 15MB STL takes about a
quarter of a second.

| Tool | Does |
|---|---|
| `list_projects` | Browse and filter |
| `get_project` | Models, files, images, prints, unfiled list |
| `read_project_file` | Read `notes.md`, `project.yaml`, … (text only) |
| `print_history` | Stats plus the failure log |
| `create_project` | New project and folder |
| `update_project` | Title, status, tags, licence, notes |
| `upload_file` | One file, filed by extension |
| `attach_files_to_model` | Clear things off the unfiled list |
| `rescan_library` | Re-read the folders |
| `library_info` | Paths, counts, allowed values |

Uploads route the same way as the drag-and-drop zones: CAD to `models/sources/`,
meshes to `models/<model_name>/`, sliced 3MFs into `prints/` where they are
parsed into print jobs, images to `images/photos/` with a variant, editable
originals to `images/sources/`. Cap is 32MB per file — above that, copy into the
library folder and call `rescan_library`.

**There is no delete tool, by design.** Everything here creates or updates.
Removing a project stays a decision you make in the UI. A test asserts no tool
name or argument can delete or purge, so a convenience tool cannot be added
later without someone deliberately deciding an agent should be able to erase a
project folder unprompted.

Set `FORGE_MCP_ENABLED=false` to turn it off. Note it is no more exposed than the
REST API, which is also unauthenticated by design — but it does mean anything on
your LAN can write to the library, and that project notes an agent reads are
untrusted text like any other file.

---

## Notable deviations from the plan

### Snapshots use copy-on-write clones, not hardlinks

§5.3 called for hardlinking unchanged files into `_versions/`. That is unsafe
here. A hardlink is the *same inode*, and CAD tools re-export by truncating the
existing path rather than writing a new file — so re-exporting `tray.stl` from
Fusion would rewrite every snapshot that ever contained it, silently destroying
the history the feature exists to keep. There is a test for exactly this
(`test_re_exporting_a_model_does_not_rewrite_history`).

Instead each file is cloned with `clonefile` on APFS or `FICLONE` on btrfs/XFS.
Same "costs almost nothing on disk" property, but each snapshot is independent.
Filesystems without reflink support fall back to a real copy; `/api/health`
reports which you are getting, and `version.yaml` records it per snapshot.

### A sliced 3MF copied into `prints/` is ingested automatically

The plan says print jobs are "created by dropping in a sliced 3MF", and the
watcher is what keeps the UI current — but originally only the upload button
created a job, so a file copied in via Finder just sat there. Scanning a project
now also ingests any sliced file it has not seen, or has seen but that has been
re-sliced since. It skips everything else, so it stays cheap enough to run on
every scan, and re-slicing never resets a lifecycle state you recorded.

### The print board has buttons as well as drag

Cards drag between columns, but each also carries explicit "→ Done" style
buttons. HTML5 drag-and-drop is unreliable on touch and awkward to test.

---

## Layout

```
/library/
  desk-organizer/
    project.yaml            # authoritative metadata, ULID survives renames
    notes.md
    models/
      sources/tray.step     # CAD originals: .step, .scad, .f3d …
      sources/lid.scad
      tray/tray.stl         # meshes, per model
    prints/2026-08-06_tray-v2.gcode.3mf
    prints/2026-08-06_tray-v2.json      # settings + job record, app-written
    images/
      photos/cover-web.png      # tagged web
      photos/cover-mobile.png   # tagged mobile
      sources/cover-web.psd     # the editable original
      renders/  plates/
    docs/
      bearing-608zz-datasheet.pdf   # PDFs and anything else worth keeping
    publish/makerworld/description.md
    _versions/v001__2026-08-01__initial/
  _shared/templates/  _shared/snippets/
  _trash/                   # deleted projects land here, not /dev/null
```

Rename a folder by hand and the watcher picks it up; the ULID in `project.yaml`
keeps the project's history attached.

### Deleting a project

Edit → **Delete…**, then type the title to confirm. The dialog says what is
about to go — models, prints, images, versions — because a project folder is
usually more than you remember.

Deleting **moves the folder to `_trash/`**; it does not destroy anything.
`_trash` is a reserved name, so the project disappears from the library and does
not come back on the next rescan. Undo is a drag in Finder: move the folder back
out of `_trash/` and hit Rescan, and the project returns with its original ULID,
print history and all. There is a test for that round trip.

Emptying `_trash/` is left to your file manager. That is the one step nothing
here can undo, so nothing here does it. (`DELETE /api/projects/{id}?purge=true`
exists for scripting, and is never called by the UI.)

### Images, variants and source files

Every published image is tagged **web** or **mobile** — or left untagged when one
image serves both. You cut those crops yourself and the app never resamples
them: export copies the bytes straight through into `publish/makerworld/assets/web/`
and `assets/mobile/`.

An image can point at the file it was exported from — a `.psd`, `.pxd`,
`.afphoto`, `.xcf` and so on. Those live in `images/sources/`, are never
rendered in the gallery, and are always one click from downloading. Drop an
image and an original with the same stem and they pair up on their own; an
explicit `source:` in `project.yaml` always wins.

```yaml
images:
  - path: images/photos/cover-web.png
    variant: web
    source: images/sources/cover-web.psd
    cover: true
  - path: images/photos/cover-mobile.png
    variant: mobile
```

Deleting an image keeps its source file unless you explicitly ask for both.

### Documents and other attachments

Not everything is a model, a print or an image. `docs/` holds the PDFs that
otherwise rot in a downloads folder — bearing datasheets, assembly instructions,
a receipt — plus cut lists, and archives of reference material. They are stored
byte-for-byte and never parsed; the Files tab lists them with a drop zone of
their own, and clicking one opens it.

Accepted types are an allowlist, not "anything". Documents (`.pdf`, `.csv`,
`.txt`, Office formats) and a short list of useful binaries (`.zip`, `.dxf`,
`.bin`) go to `docs/`; an unrecognised extension is still refused, so the
library does not quietly become a dumping ground for an `.exe`.

### Drag and drop

Each tab has a drop zone that takes several files at once and files each by its
extension — images to `images/photos/`, editable originals to `images/sources/`,
CAD to `models/sources/`, PDFs and attachments to `docs/`, sliced 3MFs into
`prints/` where they become print jobs. A mixed drop is normal, so every file reports its own result and one bad
file does not sink the batch.

CAD sources show up as **unfiled** until you attach them to a model, which is
the same orphan-detection rule as everywhere else: nothing is absorbed silently.

### The in-browser SCAD editor

Any `.scad` file under `models/sources/` has an **Edit** action, and Sources
has a **+ New .scad** button — both open the same editor with a live 3D
preview. The top-nav **Editor** page opens it library-wide instead: a file
explorer lists every project as a folder with its `.scad` sources underneath
(**+** next to a project name starts a new one there), and picking a file
loads it into the same editor next to the tree. OpenSCAD itself runs as
WebAssembly in a Web Worker (`openscad-wasm`), so none of this needs a server
round-trip and it works offline. **Save** overwrites the file in place (a new
source is created via the same upload path as drag-and-drop, then edits go in
place from there on); **Export STL** compiles the current code straight to an
`.stl` next to the source, same name, always overwriting the previous export.

**Known limitation: reusing one WASM instance across renders is unreliable.**
Verified by hand: a *second* `callMain()` call on the same OpenSCAD module
instance reliably fails with an opaque native crash (a bare stringified
pointer, e.g. `1124712`, instead of a real error message) — even re-rendering
the exact same plain, non-boolean cube twice in a row. It is not specific to
CSG booleans (`difference()`/`union()`/`intersection()`); something in the
module's internal geometry cache does not survive being reused at all. The
fix is a fresh module instance per render (see `openscad.worker.ts`), which
avoids the crash entirely at the cost of paying WASM instantiation again on
every keystroke-triggered render — noticeably slower than editors that keep
one instance alive, but reliable. If a render still crashes with a bare
number, it now means something else (degenerate geometry, an actual sandbox
resource limit), and the editor shows a plain-language hint for that case.
The prebuilt `openscad-wasm` package also isn't compiled with the Manifold
kernel (confirmed via its own stderr:
`Ignoring request to enable unknown feature 'manifold'`), so CSG performance
in general stays on OpenSCAD's older CGAL kernel — slower, though no longer
the reliability problem above. Getting Manifold means sourcing or building an
`openscad-wasm` compiled with `--enable=manifold` support, which
[openscad-playground](https://github.com/openscad/openscad-playground) does
via its own Docker/Emscripten build pipeline — out of scope here for now.

---

## Configuration

All environment variables take a `FORGE_` prefix.

| Variable | Default | Notes |
|---|---|---|
| `LIBRARY_PATH` | `./library` | Your projects |
| `DATA_PATH` | `./data` | SQLite + thumbnail cache, disposable |
| `WATCH_ENABLED` | `true` | Filesystem watcher |
| `WATCH_DEBOUNCE_SECONDS` | `2.0` | Coalesces bursty CAD writes |
| `FULL_RESCAN_HOUR` | `3` | Nightly safety-net rescan |
| `WORKER_THREADS` | `2` | Background renders and scans |
| `DEMO_SEED` | `false` | Seed sample projects into an *empty* library |
| `MCP_ENABLED` | `true` | MCP server at `/mcp` for agents |
| `OLLAMA_URL` | *(unset)* | Seeds `_shared/llm.yaml` on first boot only; after that Settings owns it |
| `OLLAMA_MODEL` | `llama3.1` | Only used alongside `OLLAMA_URL` |
| `FILAMENT_COST_PER_KG` | `22.0` | Print cost estimates |

Turntable rendering needs optional extras (`pip install '.[render]'`) and is
genuinely slow on a GPU-less i5 — budget 1–3s per frame. It is always a queued
background job with SSE progress. Plate previews pulled from ingested 3MFs cover
most of the need for free, and the app degrades cleanly without the extras.

---

## Deliberately out of scope

The app logs and publishes. It does not slice, and it does not talk to printers.
MakerWorld stats sync, filament inventory and cost tracking are future work.
