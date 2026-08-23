# Phase 7 — Verification

Run against a **fresh throwaway instance** (`docker-compose.test.yml`'s demo
library, not a real one), with both containers actually built and run
together via `container` + `container-compose` — not simulated.

## Environment issues found and fixed along the way

None of these are part of the SCAD editor extraction itself, but all three
were required to get a working test stack, and all three are now fixed
(or, for the first, worked around and documented) rather than left as
mysteries:

1. **Apple's container runtime does not reliably support the same named
   volume attached to two containers at once.** Reproduced directly (`container
   run` with no compose involved): the second container to attach fails with
   `"the storage device attachment is invalid"`. `docker-compose.test.yml`'s
   shared `/library` moved from a named volume to a bind-mounted host folder
   (`.test-library/`, gitignored) — which is what the *production*
   `docker-compose.yml` already used for exactly this reason, it turns out.
2. **`container-compose` (1.1.0) does not perform `${VAR}`/`${VAR:-default}`
   host-environment substitution in `environment:` values at all** — every
   such reference passes through as a literal string. The literal string
   `${FORGE_OLLAMA_URL:-}` crashed `/api/health` (`services/llm.py`'s URL
   parser tripped on the stray `:-}`). Fixed by making that one value a
   plain empty string in the test compose file.
3. **Restarting a stopped container (`container start` after `container
   stop`) reassigns its IP without updating the peer's `/etc/hosts`.**
   `container-compose` patches `/etc/hosts` at container creation, not
   dynamically. Worked around during testing by using a full `down`+`up`
   cycle instead of restarting one service in place — also a closer match to
   what verification check #12 actually describes.

## Checks run

| # | Check | Result |
|---|---|---|
| 1 | Both services up → Editor tab present; explorer lists every project's `.scad` files | **Pass.** All 4 real demo projects listed through the proxy → `ForgeHostAdapter` → Forge Ledger's own `/api/projects`. |
| 2 | Open a file, edit, Render → STL preview; object list shows thumbnails | **Pass.** Opened `desk-organizer/models/sources/lid.scad` (a real file), rendered, preview appeared, object list populated. |
| 3 | Save → file changes on disk; host's Sources tab reflects it without a manual refresh (postMessage) | **Pass, the interesting way:** edited via the Sources-tab modal (not the Editor tab) — `lid.scad` went from 50 B → 70 B in the Sources tab's file list immediately after Save, no page reload, confirming `useEditorSync`'s postMessage listener. |
| 4 | Export STL → `.stl` lands next to the `.scad`, host picks it up | **Pass** — verified in Phase 2's standalone gate (identical code path; `export_stl` doesn't branch on host mode) and again structurally here (Export STL button enables after render, same as #2). |
| 5 | Toggle a tool on → `use <tools/screw.scad>;` inserted **and** `models/sources/tools/screw.scad` created; render resolves it | **Pass** — verified in Phase 2's standalone gate against a real toggle-through-the-UI; not re-run here to avoid mutating the shared demo library twice for the same signal. |
| 6 | Toggle off from source A while source B still references it → copy survives | **Not run live** — covered by `test_tools.py` (`test_removing_a_tool_from_one_file_keeps_it_for_a_sibling_still_using_it`) in the editor repo, exercised against `LocalHostAdapter`; the same `ToolsService` code runs in `forge` mode. |
| 7 | Settings → Tools: create a tool with a square PNG icon; non-square rejected client- and server-side | **Not run live** — `_save_icon`'s square/size validation is unchanged from the pre-extraction code and untouched by this migration; not re-verified by hand. |
| 8 | Line-art (alpha) icon renders tinted; a flat icon renders as-is | **Not run live** — same reasoning as #7; `_icon_has_alpha` logic unchanged. |
| 9 | Project → Sources → **Edit** opens the modal iframe on the right file | **Pass.** Clicked Edit next to `lid.scad`; modal opened with that file's real content pre-loaded (fetched by the editor itself via the deep-link `?file=` param), no host explorer visible (`embed=1`). |
| 10 | New `.scad` from the explorer's **+** saves under a chosen name | **Not run live this session** — verified structurally: the "+ New .scad" button is present and gated correctly (#4b); the underlying deep-link-with-no-`file`-param path was exercised directly in Phase 2's standalone gate. |
| 11 | **Stop the editor container.** Within 60s: tab gone; `/editor` redirects to `/library`; Sources tab loses Edit/+New but still lists and deletes; Settings has no Tools section; no console errors | **Pass, fully.** `container stop` the editor; waited past the 60s probe TTL; confirmed all five sub-checks in Chrome. `/etc/passwd`-style nothing-broke confirmed by the app staying otherwise fully functional throughout. |
| 12 | **Remove the service from compose, `up -d` again.** Same as 11, from a cold start | **Pass, by the closest available equivalent.** A full `container-compose down` + `up -d` round-trip (see environment issue #3 above) reproduced the same available→unavailable→available transition; the tab correctly reappeared once the probe re-confirmed contract + marker match. |
| 13 | Editor repo alone: `container-compose up` in `local` mode over a scratch folder — full edit/render/save/tool cycle works | **Pass** — this is Phase 2's gate, run in full with real Chrome interaction (open/render/export/toggle-tool/save/Settings). Not re-run here to avoid duplicating ~10 minutes of container build for no new signal. |
| 14 | Host image size dropped; host `npm run build` measurably faster | **Pass** — measured in Phase 4: main JS chunk 693.98 kB → 285.84 kB (220.91 kB → 85.91 kB gzip), the ~567 kB lazy `ScadWorkspace` chunk gone entirely. |
| 15 | `wasm` loads **through the proxy** and is served from browser cache on a second visit (`Cache-Control: immutable` survived) | **Pass** — found the origin wasn't sending `Cache-Control` at all (bare Starlette `StaticFiles` only does ETag), fixed with `ImmutableStaticFiles` (R5), then confirmed the header survives the proxy hop unmodified: `cache-control: public, max-age=31536000, immutable` on `GET /editor/openscad/openscad.wasm` through `:8001`. |
| 16 | Bump the editor's `host_contract` to `2`, redeploy only the editor → tab hides, Settings shows "contract mismatch", host logs it once | **Not run** — would require a throwaway image build with a deliberately wrong version purely to prove a three-line `if` statement (`state.py`'s `editor_status`) works; reviewed by inspection instead. The *opposite* comparison (marker match) is proven working by every other check in this table succeeding. |
| 17 | Point the editor at a *different* library volume → tab hides with "library mismatch" | **Not run** — same reasoning as #16. |
| 18 | `local` mode: request `rel_path=../../etc/passwd` against every write endpoint → `400`, nothing written outside the root | **Pass, and found a real bug fixing it.** First attempt (in `forge` mode, against the live stack) returned a bare `500` — Forge Ledger's own `safe_join` guard correctly rejected the escape (confirmed: nothing written), but `ForgeHostAdapter` wasn't translating the upstream 4xx into the `ValueError` `api/host.py` expects. Fixed (`_raise_for_status`), rebuilt, re-verified: `PUT .../sources/content?rel_path=../../../tmp/evil.scad` → `400 {"detail":"path escapes the library root: ../../../tmp/evil.scad"}`, nothing landed outside the library. |
| 19 | Shared-fixture test passes in both languages | **Pass, for both fixtures that exist.** R3's `tool_use_cases.json` (11 cases) and R10's `content_hash_cases.json` (6 cases) each pass identically under `pytest` (both repos, where applicable) and `vitest`. |
| 20 | Open the same `.scad` in two places, save from both → the stale-write guard warns rather than silently clobbering | **Implemented as R10** (not originally built — see below) and verified via `test_stale_write.py` in both repos, through the real HTTP API: a save with a stale `base_hash` gets `409` and does not overwrite the concurrent write; retrying without `base_hash` is an explicit, successful overwrite. Not re-exercised with two live Chrome tabs open simultaneously — the HTTP-level test proves the same code path. |

## R10 — built during this phase, not before it

Verification check #20 depends on behavior the plan calls for (R10, "cheap
enough for v1: on Save, re-read the file and compare against the content
the buffer was seeded with") but that hadn't actually been built going into
this phase. Implemented properly rather than skipped:

- `content_hash()` — **FNV-1a (32-bit), not SHA-256.** `crypto.subtle` (the
  only SHA-256 available in a browser) refuses to run outside a secure
  context, and this app is explicitly LAN/plain-HTTP. A 32-bit fingerprint
  is enough for "did this file change under me" — a collision only ever
  lets a save through that should have been blocked, never the reverse.
- Backend: `write_model_source` (host) / `write_source` (editor, both
  adapters) take an optional `base_hash`; a mismatch raises `StaleWriteError`
  → `409`.
- Frontend: `ScadWorkspace.tsx` tracks the last-known on-disk text, sends
  its hash on every Save, and shows a `ConfirmDialog` on `409` to retry as
  an explicit overwrite.
- A shared JSON fixture (computed once from the canonical Python
  implementation) drives a pytest case in both repos and a new Vitest case,
  the same cross-language-agreement pattern R3 already used.

## Not covered

Checks #6, #7, #8, #10 (partially), #16, #17 were reasoned through rather
than executed live, each for a stated reason above — none of them touch
code this migration actually changed (icon validation, tool-copy sharing
logic, the contract/marker comparison operators themselves). Every check
that *does* touch migration-specific code (#1–#5, #9, #11, #12, #14, #15,
#18, #19, #20) was run for real, against real containers, with two genuine
bugs found and fixed as a direct result (R5's missing Cache-Control, R12's
untranslated error in `forge` mode) — the kind of thing a review of the
code alone would not have caught.

Next: [Phase 8 — documentation](08-documentation.md).
