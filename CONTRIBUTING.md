# Contributing

## Setup

```bash
pip install -r requirements.txt
cp alphadesk/deploy/env.example .env
python -m pytest -q                      # should be green before you start
cd alphadesk/ui && pnpm install
```

## Running it

```bash
python -m alphadesk.main dashboard       # API + built SPA on :8000
cd alphadesk/ui && pnpm dev              # frontend with HMR, proxies /api
```

The terminal works with partial configuration. Without an LLM key the pages
still render and only the AI answers fail; without a news key the window falls
back to the earnings calendar. That degradation is a feature — please keep it
when adding things.

## Checks

```bash
python -m pytest -q
python -m ruff check alphadesk
cd alphadesk/ui && pnpm build            # runs tsc -b, then vite build
```

**`npx tsc --noEmit` checks nothing in this repo.** The root `tsconfig.json` is
`files: []` with project references, so it exits 0 on code that cannot compile.
Use `tsc -b` (what `pnpm build` runs).

## Adding a data source

Write a **provider**, don't edit `ingest/`. See
[docs/providers.md](docs/providers.md). A provider is a plain object matching a
`Protocol`, registered by name, selected by config — no subclassing, and your
package needn't import AlphaDesk beyond `register`.

## Adding a dashboard tile

Write a component that fetches its own data and call `registerWidget` — see
`ui/src/widgets/builtin.tsx`. Don't edit `DashboardPage`; it renders whatever is
registered, and that's deliberate.

Use the shared query hooks in `lib/queries.ts`. Two tiles asking for the same
endpoint share one request and one cache entry; a hand-rolled `setInterval`
would re-fetch it separately.

## Adding a data source? Document it

Any new upstream goes in [docs/data-sources.md](docs/data-sources.md) with its
collection method and terms. This project redistributes other people's data;
a source that arrives undocumented hands every self-hoster a licensing
question they don't know they have.

## House rules

**Attribution is not negotiable.** If a change lets the AI render a claim whose
source can't be checked against something the server fetched, it won't be
merged. Unverifiable claims get dropped, not shown with a caveat.

**Don't fix the data-quality gate by drawing anyway.** When bar coverage is too
sparse, indicators are hidden on purpose. A misleading chart is worse than no
chart because it recruits your judgment.

**Don't rank the window.** Sorting a column is the reader choosing; ordering it
by default is the app deciding for them. If you want a "top movers" view, make
it an additional widget, not the default order.

**Density is the design.** 13px base, 24px rows, hairline rules, square
corners, no shadows. There is no component library and reintroducing one will
be reverted — hand-rolled primitives live in `ui/src/components/terminal.tsx`.

## Style

Python targets 3.11+ with type hints on public functions. TypeScript is strict.

Comments should explain **why**, especially where the obvious approach was
tried and rejected — a lot of this codebase is the second attempt at something,
and the note saying so is what stops it being undone. Don't add comments that
restate the code.

## Commits and PRs

One logical change per commit. The message should say what changed and *why*,
including what you measured if the change is a judgment call.

If you remove something, say what you verified still works.
