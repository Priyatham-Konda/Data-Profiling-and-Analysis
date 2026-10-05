# DQ Accelerator — Frontend build spec

Companion to `CLAUDE.md` (the backend/engine context). This file is for the
React dashboard specifically. Visual reference: the Design canvas at
https://claude.ai/artifact/NHEzHFt1UYFcRpEu13sNro — three artboards (Upload,
Home, and an isolated reference view of the processing state). Build against
this spec; treat the canvas as the visual source of truth for spacing,
copy, and layout down to the pixel where it's unambiguous.

## The one rule that shapes everything else

**Home never blocks on a run finishing.** Uploading a file does not route
the user into a dedicated "processing" page they have to sit on. It drops
them on Home, which shows nothing selected by default. The new run appears
in the sidebar with a live status dot. The user finds out how it's going
**only when they click it** — same as any other run in the list. This is not
a visual preference, it's the navigation model: there is no route, modal, or
screen state in this app that exists solely to make someone wait.

## Screens

### 1. Upload (`/upload`)

- A dropzone (`<label>` + hidden `<input type="file" accept=".csv">`,
  native file picker, not a custom drag layer for v1).
- Client-side validation before anything hits the network: reject non-`.csv`
  and anything over 200 MB (FR-1.1), show the error inline, don't call the
  API with an invalid file.
- Once a valid file is chosen: show filename + size, a way to clear and
  re-pick, and a "Start assessment" action.
- "Start assessment" fires the upload and navigates to `/` (Home) —
  it does **not** navigate to a run-specific page and does not wait for the
  upload response before navigating; the toast (below) is what confirms
  success.

### 2. Home (`/`) — the persistent shell

Two permanent regions:

- **Sidebar** — app mark, a "New assessment" link back to Upload, and the
  runs list. Every item: filename (mono), a status dot, and one line of
  status text. Clicking an item selects it; nothing else about the shell
  changes.
- **Main panel** — renders one of four states based on the *selected* run.
  Nothing here is time-based or route-based; it's a pure function of
  `(selectedRunId, runsById[selectedRunId]?.status)`.

| State | Trigger | Shows |
|---|---|---|
| Empty | No run selected (default, including right after upload) | "Select a run to see its report" |
| Processing | Selected run's status is `processing` | Current stage, a short progress bar, explicit copy that it's safe to leave and come back |
| Failed | Selected run's status is `failed` | The parse/profile error, a link back to Upload |
| Completed | Selected run's status is `completed` | Header (filename, run id, record/CDE counts, overall score), Download button, six dimension tiles |

### 3. Dimension detail (drawer, not a route)

Double-clicking a dimension tile opens a right-side drawer over the current
Home state — backdrop + slide-in panel, closable via an `×` button. Shows
the dimension's score, its band (`Healthy` / `Needs attention` / `Critical`),
and the rules applied with each rule's pass rate. This is local UI state on
the Home page (`selectedDimensionKey`), not a navigation — closing it just
returns to whatever Home state was showing underneath.

### Toast (global, not page-scoped)

Fires once, right after a successful upload response: `"<filename> has been
received and is being processed"`. Auto-dismisses after ~4s, has a manual
close, and is completely decoupled from whatever the main panel is showing —
it can fire while the user is looking at a totally different run, or with
nothing selected at all.

## Component architecture

```
<AppShell>                     shared sidebar + main-panel layout
  <RunsSidebar>                 fetches + lists runs, owns selection state (lifted to AppShell or a route param)
    <RunListItem status="…" />
  <UploadPanel>                 dropzone, client-side validation, upload mutation
  <HomePanel>                   the state-switch described above
    <EmptyState>
    <ProcessingView>
    <FailedView>
    <CompletedView>
      <ScoreHeader>
      <DownloadMenu>
      <DimensionGrid>
        <DimensionTile onDoubleClick />
      <DimensionDrawer>          rules table, close button
<Toast>                          global, mounted once at the app root
```

## Data fetching

Use React Query (TanStack Query). Don't hand-roll polling with
`useEffect`/`setInterval`.

- **Upload**: `useMutation`, `POST /runs` as `multipart/form-data`. On
  success: invalidate the runs list query, fire the toast, navigate to `/`.
- **Runs list** (sidebar): `useQuery(['runs'])`. Poll lightly (or better,
  SSE/websocket if the backend offers it) just to keep status dots current
  — this must never trigger a redirect or steal focus, only update badges.
- **Selected run detail**: `useQuery(['run', runId], { enabled: !!selectedRunId })`.
  Poll only while `status === 'processing'`, and only when that run is the
  one currently selected:

  ```tsx
  useQuery({
    queryKey: ['run', runId],
    queryFn: () => fetchRun(runId),
    enabled: selectedRunId === runId,
    refetchInterval: (data) => data?.status === 'processing' ? 3000 : false,
  });
  ```

- **Dimension rule detail** (drawer): fetch lazily, only when a tile is
  double-clicked (`enabled: !!selectedDimensionKey`), not preloaded with the
  rest of the run.
- **Download**: not a fetch-then-blob — two plain links, so the browser
  handles the download natively:
  `GET /runs/{id}/report?type=summary` and `?type=in-depth`, expecting
  `Content-Disposition: attachment` from the backend's `dqa.report` endpoint.

## Design tokens (as built in the canvas)

| Token | Value | Use |
|---|---|---|
| Page background | `#F6F7F5` | body |
| Surface | `#FFFFFF` | cards, sidebar, drawer |
| Border | `#E2E4DF` | hairlines |
| Text primary | `#16181B` | headings, values |
| Text secondary | `#5B6169` | body copy |
| Text muted | `#8B9097` | metadata, hints |
| Accent (brand) | `#0E6E58` | primary buttons, active nav, logo mark |
| Accent hover | `#0B5A47` | |
| Band — healthy (≥90) | `#1F8A55` | score ≥ 90, per FR-4.5 |
| Band — needs attention (70–89) | `#C08A1E` | score 70–89 |
| Band — critical (<70) | `#C23B3B` | score < 70, also the failed-run and error states |

- **Fonts**: Manrope (UI text, weights 400/500/600/700/800) + IBM Plex Mono
  (filenames, run ids, scores, rule ids) — both via Google Fonts.
- **Radii**: 8–9px on controls, 14px on cards, 999px on pills/badges.
- **Sidebar**: fixed 240px. Content padding: 32–40px. Card gap: 16px.

## What the backend needs to expose (see `CLAUDE.md` for the engine side)

- `POST /runs` — multipart upload, returns `{ id, status: 'processing' }`
- `GET /runs` — list, each with `id`, `file`, `status`, `overall` (if completed)
- `GET /runs/{id}` — full detail: status, stage/progress (if processing),
  error (if failed), or scores per dimension + record/CDE counts (if completed)
- `GET /runs/{id}/dimensions/{dim}` — rule-level breakdown for the drawer
- `GET /runs/{id}/report?type=summary|in-depth` — PDF, `Content-Disposition: attachment`

These map directly to the `dqa.api` routes already scoped in `CLAUDE.md`;
nothing here should require a new backend shape, just wiring.
