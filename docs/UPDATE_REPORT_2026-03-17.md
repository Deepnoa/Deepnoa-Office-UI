# Update Report — 2026-03-17

## What Changed

- Added a minimal Openclaw bridge layer in [`backend/services/openclaw_bridge.py`](../backend/services/openclaw_bridge.py)
- Added shared event/state schema helpers in [`backend/services/schemas.py`](../backend/services/schemas.py)
- Added stable polling endpoints and fixed them as the current API contract:
  - `/api/public/state`
  - `/api/internal/state`
  - `/api/internal/events`
- Kept legacy endpoints (`/public-state`, `/internal-state`) for compatibility, now marked deprecated with `Deprecation`, `Sunset`, and `Link` headers
- Extended the public view with a signboard-style summary bar:
  - Active Agents
  - Active Tasks
  - Blocked
  - Awaiting Approval
  - Done Today
  - Alerts
- Extended the internal view with flow-watch sections for:
  - blocked tasks
  - pending approvals
  - connector health
  - internal alerts

## Structure Direction

This update starts moving the app toward the roadmap's three-surface model:

- Public Office View: shareable, public-safe summary and activity
- Internal Ops View: operational visibility for blocked work, approvals, and connector health
- Asset / Scene Studio: guarded controls remain in the internal surface for now

The implementation is intentionally incremental. Route files and frontend JS/CSS splitting are not fully done yet, but the new bridge/schema modules create a cleaner boundary for the next refactor.

## Current API Contract

Canonical API routes:

- `GET /health`
- `GET /api/public/state`
- `GET /api/internal/state`
- `GET /api/internal/events?since=<iso8601>`

Deprecated compatibility routes:

- `GET /public-state` -> use `/api/public/state`
- `GET /internal-state` -> use `/api/internal/state`

Response contract notes:

- `backend/services/schemas.py` is now the source of truth for:
  - schema version
  - canonical agent states
  - canonical internal states
  - event types
  - public abstraction rules
- `backend/services/openclaw_bridge.py` is now the source of truth for:
  - event normalization
  - agent/task/approval/connector/alert normalization
  - public/internal payload assembly

`/api/internal/events` contract:

- ordering: newest first (`timestamp desc`)
- `since` rule: return events with `timestamp > since`
- retention: latest 200 normalized events are kept in the bridge snapshot
- response limit: latest 100 events max per call
- `event_id`: deterministic hash over normalized source/event/task/summary/timestamp
- `timestamp`: preserved from upstream payload when provided, otherwise assigned at normalization time
- `provenance`: `actual` or `derived`
- `provenance`: `actual`, `derived`, or `backfilled`
- dedupe: if actual and derived events share the same `(event_type, agent_id, task_id, timestamp, approval_status)` key, actual wins

## Real Input Connection Status

Connected real inputs today:

- `manager-state.json`
  - source of posted manager events from `scripts/post_manager_event.py`, `scripts/run_role_agent.py`, and sync jobs
  - actual lifecycle source when Openclaw-facing scripts post canonical task / approval events
- `agents-state.json`
  - source of remote Openclaw guest agent presence via `POST /agent-push`
- `state.json`
  - source of local/main agent state
- `~/.openclaw/cron/jobs.json`
  - source of cron connector health when present
- `~/bot/github_queue_local/log/worker.log`
- `~/bot/github_queue_local/log/deploy.log`
  - source of GitHub worker freshness when present

Still effectively dummy or partial:

- approvals
  - `approval.requested` is now structured-first from Openclaw tool-result `details.status == approval-pending` plus `approvalId`
  - `approval.resolved` prefers structured decision fields such as `decision` / `approvalDecision` when surfaced
  - if the CLI result does not expose structured approval resolution, fallback text matching still applies as a temporary compatibility path
- task lifecycle beyond started/error
  - `scripts/run_role_agent.py` now emits actual `task.created` / `task.assigned` / `task.started` / `task.completed` / `task.failed`
  - legacy manager events and snapshot-only inputs can still produce derived lifecycle fill-ins when canonical events are missing
- connector auth detail
  - currently summarized only, not sourced from dedicated connector runtimes

## Connector Health Criteria

Current code and bridge rules:

- OpenClaw runtime
  - `connected`: `manager-state.json.updated_at` age <= 180s
  - `degraded`: age <= 900s
  - `error`: age > 900s or missing timestamp
- OpenClaw cron
  - `connected`: cron jobs file exists and enabled jobs have no `error`
  - `degraded`: any enabled job is `running` or `queued`
  - `error`: file missing or any enabled job has `lastStatus == error`
- GitHub worker
  - `connected`: latest worker/deploy log mtime age <= 300s
  - `degraded`: age <= 1800s
  - `error`: age > 1800s or no log available

## Public Abstraction Rules

Public responses intentionally abstract internal data. The current contract is:

- never expose raw task titles when they may contain internal detail
- never expose customer names or tenant names
- replace internal file paths with placeholders
- replace internal/private links with placeholders
- never expose queue payloads, tokens, or connector internals
- show approvals as counts only on public surfaces
- show abstract health on public surfaces, keep raw errors internal
- the bridge sanitizes public summaries before they enter `activity`, `recent_work`, and `intake`
- public surfaces never expose approval ids, raw approval payloads, or provenance labels

## I18n Policy

- canonical contract values remain fixed in English
  - example: event types, states, approval statuses, provenance values
- UI translates display labels only
  - headings
  - summary labels
  - empty states
  - loading and fetch error strings
  - enum chips such as approval status, signal kind, and provenance
  - operational status messages shown inside internal/public panels
- current UI dictionaries are prepared for:
  - `ja`
  - `en`
  - `zh`
- page-level language state is shared through UI state only; backend payload shape does not change by locale

## I18n QA Status

Browser-side QA was re-run against:

- `/office?lang=ja`
- `/office?lang=en`
- `/office?lang=zh`
- `/internal?lang=ja`
- `/internal?lang=en`
- `/internal?lang=zh`

Current result:

- major internal/public headings switch correctly across `ja / en / zh`
- summary bar labels switch correctly across `ja / en / zh`
- approval / signal / provenance chips switch correctly where canonical enum labels are used
- empty, loading, and fetch-error copy added in this phase switches correctly
- no obvious desktop layout break was observed at a 1440px-wide browser viewport, including the longer Japanese labels

Known remaining gaps:

- runtime-provided summaries and role descriptions are still English when the upstream payload itself is English
- public gateway detail and some internal activity summaries remain source-language text until payload-level localization is introduced
- asset-lab controls are only partially localized; operations-critical internal/public surfaces are prioritized first

Upstream localization follow-up:

- runtime payload summaries are now tracked as an upstream payload-localization concern
- frontend i18n should translate labels, headings, and enum chips only
- source-language summaries should be localized at the Openclaw payload producer or bridge-summary layer, not by ad-hoc frontend rewriting

## Approval Signal Conditions

Structured-first approval mapping now follows this order:

- preferred:
  - Openclaw transcript `toolResult.details.status == approval-pending`
  - Openclaw transcript or result payload fields such as `approvalId`, `expiresAtMs`, `decision`, `approvalDecision`, `approval_status`
- fallback:
  - text matching on the JSON result blob only when the structured fields above are absent for the same lifecycle edge

Canonical approval statuses:

- `pending`
- `approved`
- `rejected`
- `expired`

Decision mapping:

- `allow-once` / `allow-always` -> `approved`
- `deny` -> `rejected`
- timeout-style decisions -> `expired`

Lifecycle contract:

- `approval.requested` starts the approval lifecycle and is not task-terminal
- `approval.resolved` is terminal for approval only
- `task.completed` and `task.failed` remain the only task-terminal events
- if a rejection also ends the task, emit `approval.resolved(rejected)` before `task.failed`

Known payload gaps:

- Openclaw internal tool results clearly carry structured approval request fields
- approval resolution is not always surfaced back through `openclaw agent --json`
- when resolution is only visible in assistant text, the current runner still falls back to text matching until Openclaw surfaces the decision field consistently

## Internal UI Priority Rules

The internal view now interprets lifecycle signals in this order:

## Public Office Background Workflow

Current public floor wiring:

- `/office` serves `frontend/public.html`
- the office scene uses `/static/office_bg_small.webp?v={{VERSION_TIMESTAMP}}` as its background
- `/assets/generate-rpg-background` writes the generated image directly to `frontend/office_bg_small.webp`
- the same flow archives generated outputs into `assets/bg-history/` when generation succeeds

Current status on 2026-03-17:

- `office_bg_small.webp` and `assets/room-reference.webp` are byte-identical
- `assets/bg-history/` does not exist yet on the current server, so there is no generated history to restore from
- runtime config is set to `gemini_model = nanobanana-pro`
- both `quality` and `fast` generation attempts failed with Gemini `429 RESOURCE_EXHAUSTED`

Meaning:

- public background wiring is complete
- Nano Banana Pro is configured, but not currently usable with the active API key quota
- the current public floor is still showing the fixed reference background, not a newly generated variant

### Public Background Prompt

Recommended prompt for the public office floor:

```text
Pixel-art office floor background for a public AI office dashboard, slight isometric top-down view, wide 16:9 composition, dark navy and muted teal base colors with a soft futuristic atmosphere, cute but not childish, clean walkable lanes for exactly three overlaid staff sprites to move through without obstruction, clear floor paths in the center and lower third, desks with dual monitors, a small meeting nook, plants, shelves, glass partition hints, subtle ambient screens, cozy lighting, readable silhouette zones, medium contrast floor tiles, no people baked into the image, no text, no logos, no UI panels, no secret documents, no readable screens, background-only scene designed for overlaying animated dev ops research sprites, pixel art with crisp shapes and gentle highlights, visually calm and uncluttered.
```

Alternate shorter prompt:

```text
Pixel-art public office floor for an AI dashboard, slight isometric top-down, dark navy and teal palette, clean walking lanes for three animated overlaid sprites, desks, meeting nook, plants, monitor glow, cute but professional, no text, no people, no logos, uncluttered 16:9 background with strong sprite readability.
```

### Selection Criteria

Choose the generated background that best preserves:

- a readable central walkway for `dev`, `ops`, and `research`
- medium contrast behind moving sprites
- no baked-in characters or text
- desks and meeting spaces that suggest an office without becoming visually busy
- navy-first tone so the floor still feels like Deepnoa rather than a generic pastel office

## Public Floor Composition

`/office` now aims to read as one living office floor instead of a dashboard-first card wall.

Surface split:

- `/office`
  - public mission-control surface
  - keeps summary, live activity, connected systems, and the public office floor together
- `/scene`
  - scene-only public surface for homepage embeds and hero sections
  - removes cards, detail lists, and long copy
  - keeps only the office background, staff sprites, a minimal legend, and a small live badge
- `/live-office`
  - alias of `/scene` for embed-friendly linking

Current public floor structure:

- background:
  - `frontend/office_bg_small.webp`
  - generated via the guarded background flow when Gemini quota is available
- visual zones over the background:
  - `desk zone` on the left
  - `meeting` pocket near the upper center
  - `desk zone` on the right
  - central `corridor`
  - lower `walkway` for the clearest sprite readability
- moving public agents:
  - `dev`
  - `ops`
  - `research`
  - `reception` remains outside the floor scene and is treated as gateway status, not a walking staff sprite

### Sprite and Motion Mapping

Public floor sprites are lightweight DOM sprites, not canvas. They are intentionally small and readable over the background.

Role styling:

- `dev`
  - cool blue palette
  - routes through left desks and the center walkway
- `ops`
  - warm amber palette
  - patrols the lower walkway and right-side desk lane
- `research`
  - green palette
  - moves around the meeting pocket and center corridor

State to motion mapping:

- `idle`
  - slow patrol with light sway
- `executing`
  - faster route traversal with tighter pacing
- `researching`
  - slower route traversal with a small scan drift
- `syncing` / `routing`
  - medium patrol with a subtle orbit-like wobble
- `writing`
  - medium movement with a softer desk-side rhythm
- `error`
  - stop in place and pulse red

Route behavior:

- routes are waypoint loops, not free-floating random motion
- some waypoints include a short pause so agents appear to stop near desks or the meeting zone
- the lower walkway is kept visually clearer than the desk areas so three agents remain readable in motion

## Scene-Only Surface

`/scene` is layered as a small 2D stage:

- background layer
  - `office_bg_small.webp`
- floor-zone layer
  - desk bands, meeting pocket, corridor, walkway
- accent layer
  - soft light pools that give the floor more life without adding text or data cards
- sprite layer
  - three staff-like agents for `dev`, `ops`, and `research`
- minimal UI layer
  - tiny brand label
  - state legend
  - live/update indicator

Design rules for `/scene`:

- no card lists
- no task or system detail
- no long description blocks
- no always-on name labels over sprites
- public-safe only; background must not contain text or internal artifacts

## DOM Scene vs PixiJS

Current implementation uses DOM sprites rather than canvas or PixiJS.

Why DOM is acceptable right now:

- only three moving agents
- simple waypoint motion
- easy to tune with existing CSS and public-state polling
- easy to embed into a homepage iframe or standalone route

Current DOM limits:

- sprite animation is still CSS-driven, so motion variety is modest
- layering and per-frame effects are harder to scale once more agents or props are added
- richer lighting, occlusion, or tile-aware pathing would become awkward

If the scene grows beyond the current homepage use:

- move `/scene` first, not `/office`
- adopt a dedicated 2D scene layer such as PixiJS
- keep `/api/public/state` as the data contract
- preserve the same five layers conceptually, but render background, props, and sprites on canvas

## Homepage Embed

Recommended homepage usage:

- embed `/scene?lang=ja` or `/scene?lang=en` in an iframe
- keep a 16:9 box so the walkways and meeting pocket stay readable
- avoid stacking extra chrome around the embed; `/scene` already carries the minimal label and live indicator

Recommended URL patterns:

- standard scene view:
  - `/scene?lang=ja`
  - `/scene?lang=en`
- embed-friendly scene:
  - `/scene?lang=ja&embed=1`
  - `/scene?lang=ja&embed=1&legend=0`
  - `/scene?lang=ja&embed=1&legend=0&live=0&brand=0`
- alias:
  - `/live-office?lang=ja&embed=1`

Embed options:

- `embed=1`
  - removes page padding and outer chrome so the scene fills the embed box
- `legend=0`
  - hides the lower-left legend
- `live=0`
  - hides the lower-right live indicator
- `brand=0`
  - hides the upper-left brand label

## Scene QA Results

Server reflection:

- the live listener on port `19000` was restarted
- `/scene?lang=ja` now returns `frontend/scene.html`
- `/live-office?lang=ja` now returns the same scene-only surface

Viewport checks completed with headless Chromium:

- `1920x1080`
- `1440x900`
- `1280x720`
- `900x620`

Observed results:

- three sprites remain readable at all four sizes
- the scene legend stays compact and does not overlap the sprites
- the live indicator is visible but no longer dominates the scene
- always-on hover badges were removed; the scene now works without interaction
- the brand block was reduced to a small corner label so the floor remains the focus

Final scene UI policy:

- keep only:
  - compact brand label
  - minimal legend
  - small live/update indicator
- remove:
  - card lists
  - long copy
  - persistent agent labels
  - task/system detail

Recommended embed sizes:

- preferred:
  - `16:9` at `1280x720` or larger
- good:
  - `1440x900` hero-style container
- minimum comfortable embed:
  - around `900px` width

## Remaining Visual Limits

Current scene quality is good enough for homepage embedding, but a few limits remain:

- the active background still comes from the fixed reference image because Nano Banana generation is currently blocked by Gemini quota
- that reference background contains faint baked-in text from older internal art, so the scene uses stronger zone overlays to suppress it
- DOM sprites work well for three agents, but richer occlusion, tile-aware pathing, or larger casts would be better served by a dedicated 2D renderer such as PixiJS

## Public Scene Background Refresh

Once Gemini quota is available again, the public scene can be refreshed without changing the scene UI:

1. authenticate with `POST /assets/auth`
2. generate a new public floor with `POST /assets/generate-rpg-background`
3. poll `GET /assets/generate-rpg-background/poll?task_id=...`
4. confirm `frontend/office_bg_small.webp` changed
5. reload `/scene` or `/office`

The scene surface is intentionally designed so replacing `office_bg_small.webp` is enough to refresh the look.

## Adopted Bright Office Background

Current adopted source:

- latest downloaded bright office image
- local source used for the current refresh:
  - `~/Downloads/Generated Image March 17, 2026 - 2_12PM.png`

Current background application:

- converted into the active `frontend/office_bg_small.webp`
- verified on both:
  - `/scene`
  - `/office`

Layout anchors used for motion tuning:

- left desk bands
- center half-round operations desk
- right upper meeting table
- right-side desks
- sofa lounge
- lower entry-side open area

## Scene Waypoint Design

The current `/scene` motion is tuned to the adopted bright office background rather than the old dark reference layout.

Role patterns:

- `dev`
  - mainly uses the left desk bands
  - occasionally walks into the center aisle
  - sometimes pauses near the sofa-side lower-left area
- `ops`
  - mainly occupies the center half-round operations desk
  - does short loops through the center aisle
  - pauses longest near the center console
- `research`
  - mainly moves between the right meeting table and right-side desks
  - occasionally visits the sofa-side lower area
  - spends longer pauses near the meeting table

Stop-point behavior:

- stop points are intentionally longer than before
- most pauses now last about `2` to `5` seconds
- while paused, sprites use a tiny idle-breathe motion instead of freezing completely
- `error` still stays in place with a stronger accent

Current waypoint clusters:

- `dev`
  - lower-left desk front
  - middle-left desk front
  - upper-left desk front
  - center aisle edge
  - sofa-side lower-left pause
- `ops`
  - left side of center console
  - front-center console seat
  - right side of center console
  - upper inner aisle
  - lower console exit
- `research`
  - right upper desk cluster
  - round meeting table edge
  - lower-right desk cluster
  - lower-right open area
  - sofa-side crossover stop

## Sprite Art Alignment

The adopted bright office background is cleaner and slightly more illustrative than the earlier sprite style, so `/scene` now softens the sprite treatment to reduce the “separate sticker layer” look.

Current tuning:

- sprite size
  - reduced toward a compact `~32x42` visual footprint
  - still readable in homepage embeds, but less blocky against the background
- outline rule
  - moved from near-black edges to muted navy outlines
  - this matches the desk/chair linework better
- shadow rule
  - each agent now uses a small low-opacity oval floor shadow
  - shadow stays subtle so the scene remains airy
- motion rule
  - walk bob is smaller than before
  - paused idle motion is almost imperceptible
  - stop points are preferred over exaggerated movement

Role differentiation:

- `dev`
  - cool blue outfit
- `ops`
  - warm amber outfit
- `research`
  - soft mint outfit

These differences stay small on purpose so the three agents feel like staff in one office, not game avatars from different sets.

If further alignment is needed later:

- replace DOM-built sprite shapes with hand-drawn 24x24 or 32x32 illustrated sprites exported to PNG/WebP
- add seat-aware occlusion for desk edges
- tune the background lightly with selective sharpening only after the final generated background is chosen

## Simple 2.5D Direction

`/scene` is now moving from “sprites on a single image” toward a lightweight 2.5D scene.

Current approach:

- no full 3D
- no physics
- no mesh or perspective camera
- use:
  - layered rendering
  - walkmap-style floor rules
  - soft occluders for desk and sofa fronts

Current layer order:

- background layer
- floor-zone guidance layer
- accent layer
- shadow layer
- sprite layer
- occluder layer
- minimal UI layer

Why this helps:

- sprites read as moving on floor lanes rather than on top of furniture
- desk fronts and sofa fronts can partially overlap staff when they pass “behind” them
- agent depth also follows screen `y`, so lower positions feel closer to the viewer

## Walkmap Model

Current walkmap is lightweight and data-driven.

It defines:

- walkable floor bands
  - left desk lane
  - upper cross lane
  - center ops lane
  - right lane
  - lounge lane
- stop points
  - role-specific desk-front and meeting-front anchors
- snapping
  - if a point lands outside walkable floor, it snaps back to the nearest walkable band
- transit points
  - routes now pass through explicit corridor points instead of jumping directly between task stops
  - this is what prevents obvious desk-crossing in the top-down office layout

This is intentionally not full pathfinding.
It is enough to prevent obvious “walking on desks” behavior while keeping the scene easy to tune.

Top-down stop-point design:

- `dev`
  - left desk lower front
  - left desk middle front
  - left desk upper front
  - left room door / center gate
- `ops`
  - center circular desk outer ring
  - top, left, right, and lower arc stops around the operations console
- `research`
  - right meeting-table edge
  - right-side desk front
  - right-room lower front
- `lounge`
  - sofa-front pause near the lower-left room

Routing rule:

- stops are not connected by straight lines anymore
- each role follows corridor-first transit points
- the practical effect is: agents stay on floor lanes and avoid desk or wall crossings

## Needed Image Structure

The current 2.5D pass still uses one base background image, but the scene now behaves as if the art were split into:

- floor/base
- furniture fronts used as occluders
- staff sprites
- floor shadows

If we want a cleaner next step without going 3D, the best asset format would be:

- `floor.webp`
- `occluders.webp`
- `props.webp` or optional accent layer
- `staff/*.webp`

That would let us replace the current CSS occluders with art-matched front pieces.

## Limits Without Full 3D

What this 2.5D pass can solve well:

- no more obvious desk-top walking
- clearer floor ownership
- better sense of front/back for desks and lounge furniture
- cleaner role-specific movement lanes

What it still cannot solve perfectly:

- true collision with irregular curved furniture
- accurate leg hiding behind every chair edge
- perspective-correct turns or seated states
- dynamic pathfinding around moving actors

When to add true pathfinding later:

- if agent count grows beyond three and they begin to block each other
- if stop points must be assigned dynamically from task data
- if we need obstacle-aware rerouting instead of fixed corridor loops

Until then, corridor-based walkmap routing is the simpler and more stable choice for the homepage scene.

## Image-Based Walkmask Upgrade

The top-down office background made the old lane/rect approach too approximate.
Even with corridor points, agents could still appear to cut across desks or walls because the movement rules were simpler than the art.

Current `/scene` now uses:

- `frontend/scene-walkmask.png`
  - same logical footprint as the background scene
  - white = walkable
  - black = non-walkable

Why the switch was needed:

- the background is no longer abstract enough for broad rectangular walk lanes
- furniture placement matters visually
- a correct floor mask is more important than decorative “show the path” overlays

Current movement rules:

- transit and stop points are authored for the top-down layout
- every route point is snapped onto a walkable pixel
- every animated frame is nudged back toward the nearest walkable pixel if needed
- direct movement is still waypoint-based, but the walkmask now acts as the final floor rule

Design effect:

- agents stay on white floor / hall areas much more reliably
- desks, walls, sofas, plants, and tables are treated as non-walkable
- the bright floor overlay could be reduced because movement now follows the floor logic directly

What the old lane/rect method was still good at:

- fast iteration
- rough zoning
- keeping routes human-readable in code

Where lane/rect breaks down:

- top-down rooms with dense furniture
- narrow passages near desks or doors
- scenes where visual trust depends on “never walks on furniture”

That tradeoff is intentional.
For the homepage scene, layered 2.5D gives most of the value without the weight of a real 3D system.

## Scene Experience Shift

`/scene` is no longer tuned as a patrol loop.
The current experience is now seat-first:

- `idle`
  - stay near the owned desk or immediate work area
- `executing`
  - remain at the main desk with only tiny work motion
- `researching`
  - favor the meeting edge or research-side desk
- `syncing` / `routing`
  - allow short corridor trips, then return to the owned area
- `error`
  - stop in place and keep a stronger visual accent

Why this changed:

- a human office feels more believable when people mostly stay in their area
- the scene should answer “who is doing what” before it answers “who is moving”
- excessive roaming made the office look busy but not understandable

## Scene Hover / Click Detail

`/scene` now exposes a very small amount of interaction:

- hover
  - shows:
    - agent name
    - role
    - current status
    - one-line work summary
- click
  - opens a compact fixed detail card
  - shows:
    - agent name
    - role
    - current status
    - short work summary
    - last update
    - source when present

Interaction rules:

- no large side panel
- no task list
- no long copy
- clicking the same staff again closes the card
- clicking outside closes the card

## Scene State Colors And Legend

The lower-left legend is now the reading key for the scene, not only decoration.

Current color mapping:

- `idle`
  - mint
- `executing`
  - amber
- `researching`
  - blue
- `syncing` / `routing`
  - violet
- `error`
  - red

The same state colors are reused for:

- legend chips
- the small badge on each staff sprite
- hover status pill
- click detail status pill

## Scene Locale Policy

The public scene follows the same rule as the rest of the UI:

- canonical payload values stay in English
- scene display labels only are localized

Current scene-localized surfaces:

- legend labels
- role labels
- hover status text
- detail card labels

This keeps the scene readable in Japanese without mutating the public-state contract.

## Scene Public Role Framing

`/scene` is now framed as a public-facing AI staff floor rather than an internal technical-role view.

Public display labels now prefer business-facing roles:

- `dev`
  - display name: `情報システム担当`
  - role label: `情報システム`
- `ops`
  - display name: `業務管理担当`
  - role label: `業務管理`
- `research`
  - display name: `経営企画担当`
  - role label: `経営企画`

Important boundary:

- internal enums and internal role keys remain unchanged
- only public-scene labels and summaries are rewritten

This lets the same runtime keep its technical structure while the homepage scene reads as:

- “AI staff working inside the company”
- not:
  - “internal engineering roles moving on a floor map”

Public-summary direction:

- technical phrases are softened into business-friendly copy where possible
- examples:
  - `GitHubキューを処理中` -> `システム更新を処理中`
  - `接続状態に異常` -> `システム状況に異常`
  - `ログ分析中` -> `稼働状況を確認中`

Future public-role candidates:

- 経理担当
- 営業担当
- 業務管理担当
- 経営企画担当
- 情報システム担当

## Scene External Contact Event

`/scene` now includes one very small external-contact event near the entrance.

Current behavior:

- a single visitor appears near the entrance at intervals
- the visitor is temporary, not a permanent cast member
- `業務管理担当` briefly shifts toward the entrance-side support route
- the public summary changes to:
  - `問い合わせ対応中`
  - or `依頼受付中`

Why this was added:

- the scene should communicate that work also comes in from outside the company
- the main cast should still be the AI staff inside the office
- a temporary entrance event is enough to imply:
  - inquiries
  - requests
  - coordination

Why this was not implemented as a permanent extra person:

- homepage use should stay calm and readable
- always-on extra characters would make the scene feel busier than the underlying story
- a small temporary event gives the external-connection signal without stealing focus

Likely future extensions:

- 営業担当
- 経理担当
- visitor types such as:
  - 問い合わせ
  - 商談
  - 資料請求
  - 面談調整

### Update Steps

When Nano Banana generation is available:

1. authenticate with `POST /assets/auth`
2. start generation with `POST /assets/generate-rpg-background`
   - use `speed_mode = quality` for Nano Banana Pro
   - use `speed_mode = fast` only as a fallback
3. poll `GET /assets/generate-rpg-background/poll?task_id=...`
4. on success, verify `frontend/office_bg_small.webp` mtime and file size changed
5. reload `/office`; cache-busting is handled by `?v={{VERSION_TIMESTAMP}}`

### Fallback Rules

If Nano Banana Pro cannot run:

- first fallback: retry later after Gemini quota resets
- second fallback: use `speed_mode = fast` (`nanobanana-2`) for a temporary public floor
- third fallback: keep `office_bg_small.webp` as-is and do not overwrite the active background
- operational restore: `POST /assets/restore-reference-background`

Do not treat the current fixed reference background as a successful generated public floor refresh.

1. pending approvals
2. blocked tasks
3. failed tasks
4. degraded or error connectors
5. completed tasks
6. low-priority activity such as `task.started`, `task.assigned`, `task.created`, and agent status churn

Operator interpretation:

- pending approval
  - action is waiting on a human decision
  - this is not the same thing as task failure
- blocked task
  - runtime cannot progress without an external unblock or missing dependency
- failed task
  - task lifecycle has terminated with `task.failed`
- rejected approval
  - approval lifecycle terminated with rejection
  - task may still be separate unless runtime also emitted `task.failed`
- degraded connector
  - runtime may still be operating, but external sync or control is stale
- completed task
  - lifecycle ended normally and is shown below urgent states

Internal UI rows now show:

- provenance: `actual`, `derived`, or `backfilled`
- approval link on task rows when a task has an approval state
- approval signal kind:
  - `structured` when built from Openclaw fields
  - `fallback` when inferred from compatibility text matching
  - `runtime` when source metadata exists but no finer label is available

## Adapter Notes

Lifecycle input adapters now live in:

- `backend/services/source_adapters.py`

Current adapter split:

- actual:
  - manager activity canonical events posted by role runners and sync scripts
  - public intake events
- derived:
  - local/main state snapshots
  - agents-state presence snapshots
- backfilled:
  - missing lifecycle backfill (`task.created`, `task.assigned`) when only later task events exist

## Next TODO

- Move bridge-backed API handlers into dedicated route modules
- Split `frontend/index.html` and `frontend/public.html` scripts into separate JS files
- Feed real Openclaw task / approval / connector events into the normalized schema instead of deriving mostly from manager activity
- Add retention and cleanup policy for generated bridge events and background jobs
- Add richer internal task cards with retry / inspect / approval actions
- Add SSE or WebSocket transport on top of the new `/api/...` response shapes

## Known Constraints

- The current Openclaw bridge now reads real local inputs, but it still depends on file freshness and manager-posted events rather than a dedicated streaming Openclaw runtime API
- `blocked`, `awaiting approval`, connector health, and `done today` are normalized correctly at the schema layer, but upstream Openclaw runtime is not yet emitting the full event set
- approval request detection is structured-first, but approval resolution still falls back to text when Openclaw CLI output omits decision fields
- Asset Studio is still rendered inside the internal view instead of a dedicated `/internal/assets` surface
- `backend/app.py` and `frontend/index.html` are still large; this change creates extraction seams but does not complete the full split
- Verification on this machine uses a temporary `.codex-venv` because the checked-in `.venv` points at an old absolute path and could not be repaired in place

## Legacy Route Removal Conditions

Safe removal conditions for `/public-state` and `/internal-state`:

- all frontend surfaces use `/api/public/state` and `/api/internal/state`
- gateway page no longer fetches `/public-state`
- external scripts and dashboards have been migrated or confirmed unused
- one release cycle has passed with deprecated headers in place

Planned earliest removal target:

- after 2026-06-30, if no remaining consumers are found

Current removal pre-check on 2026-03-17:

- frontend pages now use `/api/public/state` and `/api/internal/state`
- deprecated routes remain only in backend compatibility handlers and docs
- no in-repo frontend consumer still fetches `/public-state` or `/internal-state`
- unknown external dashboards or scripts remain the only blocker to actual removal

Final removal status:

- in-repo frontend migration is complete
- browser QA confirmed the active internal/public surfaces run on `/api/public/state` and `/api/internal/state`
- remaining blocker is external-consumer certainty only
- next safe step is to remove the compatibility routes in `backend/app.py` together with the matching metadata in `backend/services/schemas.py` once one deprecation window has been honored

Removal decision procedure:

1. keep deprecated handlers enabled for one full confirmation window after logging is added
2. inspect `deprecated-route-access.jsonl` for any hits on:
   - `/public-state`
   - `/internal-state`
3. confirm these checks stay true during the window:
   - no in-repo frontend fetches deprecated routes
   - browser QA for `/office` and `/internal` only uses `/api/public/state` and `/api/internal/state`
   - no external automation, dashboard, or reverse-proxy health check is still documented against deprecated routes
4. if the log file shows zero hits for the full window, remove:
   - compatibility route handlers in `backend/app.py`
   - matching metadata in `backend/services/schemas.py`
   - deprecated-route notes that are no longer needed in docs

Recommended confirmation window:

- minimum: 14 days in active use
- preferred: through the current `Sunset` date of `2026-06-30`
- practical operating rule: if `deprecated-route-access.jsonl` stays at zero hits for 7 to 14 consecutive days in normal use, prepare the compatibility-route removal PR
- if any hit appears, identify the caller first and reset the confirmation window after migration

Current observability status:

- backend now emits `app.logger.warning(...)` on every deprecated-route hit
- backend also appends each hit to `deprecated-route-access.jsonl`
- this gives a concrete audit trail for the removal decision instead of relying only on repo search

## Next-Phase Note: Internal Detail Drilldown

The next useful UX step after this i18n pass is an internal drilldown surface for operator diagnosis:

- selected task panel
  - task lifecycle timeline
  - linked approval state
  - connector touchpoints
  - latest actual vs derived events
- selected approval panel
  - approval requester
  - pending / approved / rejected / expired state
  - provenance and signal kind
  - linked task terminal outcome
- selected connector panel
  - current health reason
  - freshness timestamp
  - pending actions
  - recent related alerts

Interaction model:

- keep the existing lifecycle-priority overview as the primary landing surface
- allow selection from:
  - pending approvals
  - blocked tasks
  - failed tasks
  - completed tasks
  - alerts
- open detail in a persistent lower pane or right-side detail panel instead of navigating away
- preserve operator context so the urgent list stays visible while the selected item is inspected

Current minimal implementation scope:

- selectable lists:
  - blocked tasks
  - pending approvals
  - failed tasks
- presentation:
  - desktop uses a right-column detail pane
  - narrow layouts use the same detail content as a drawer-style panel
- minimal detail fields:
  - title / type
  - current status
  - related `task_id` / `approval_id`
  - provenance
  - recent lifecycle events
  - connector / source impact
  - raw internal summary
  - timestamp
- data source:
  - built from existing `/api/internal/state`
  - lifecycle is reconstructed from `events`
  - connector impact is inferred from current internal connector snapshot plus related event sources
- operational polish:
  - selected detail is kept in the URL through `detailKind` / `detailKey`
  - detail link can be copied from the pane
  - polling keeps the selected item when it still exists after refresh
  - hidden tabs use a slower refresh interval and refocus triggers an immediate fetch

Suggested task detail shape:

- task identity
  - `task_id`
  - agent / role
  - current lifecycle state
  - approval status when linked
- recent lifecycle
  - ordered normalized events for `task.created -> ... -> task.completed|task.failed`
  - provenance shown per event as `actual / derived / backfilled`
- related approvals
  - approval lifecycle summary
  - signal kind (`structured / fallback / runtime`)
  - decision and resolution timestamp
- source / connector impact
  - connectors touched by the task or failing around the same time
  - related runtime alerts
  - recent connector degradation correlated to the task window
- raw/internal summary
  - bridge-provided internal summary
  - raw payload excerpt only on the internal surface
  - explicit marker when the summary is upstream English text

Suggested approval detail shape:

- approval id and current status
- linked task id and agent id
- request timestamp, expiry timestamp, resolution timestamp
- provenance and signal kind
- downstream task outcome:
  - still pending
  - continued after approval
  - separately failed after rejection

Suggested connector detail shape:

- canonical connector health state
- rule that triggered `connected / degraded / error / offline`
- last fresh timestamp
- pending actions count
- latest related lifecycle and alert events

Expansion candidates:

- selected-item deep links in the URL
- richer task timeline grouping by actual / derived / backfilled
- explicit related connector events instead of source-based inference
- raw payload excerpt toggles for internal-only debugging
