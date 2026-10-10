# Contributing

Thanks for helping! Please read [docs/COMPLIANCE.md](docs/COMPLIANCE.md) first. Pull requests that
weaken any compliance rule will not be merged.

## Development

```bash
# backend
cd backend
uv sync
uv run kestrel            # http://127.0.0.1:8000

# checks (CI runs all of these)
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
# KESTREL_API_DOCS=1 uv run kestrel   serves interactive API docs at /docs while developing

# frontend
cd frontend
npm install
npm run dev               # http://localhost:3000, with live reload
npm run lint && npm run typecheck && npm run build
```

## Adding a job source

A site that publishes an RSS, Atom or JSON job feed needs no code: users add it on the Companies page
(see [docs/SOURCES.md](docs/SOURCES.md#custom-feeds)). For anything else, a new source must meet **all** of these:

1. **Permission.** It is an official API, or a feed whose terms explicitly allow this use. Link the
   terms in the adapter's `SourcePolicy.terms_url` and in `docs/SOURCES.md`.
2. **No scraping of sites that forbid it.** If the site's terms prohibit automated access, it goes
   on the denylist instead.
3. **Rate limits.** Set `min_interval_seconds` and `max_requests_per_scan` well below the published
   quota, and space out repeated searches with `recently_fetched` / `mark_fetched`.
4. **Attribution.** Fill `attribution_text` and `attribution_url` if the terms require them.
5. **Read-only.** Use `PoliteClient.get` only. Never submit anything to the source.
6. **Tests.** Add recorded-response tests with `respx`, including the attribution and spacing.

## Classifier and browser backends

Both are pluggable. Jev (`classify/jev.py`) and Cua Driver (`browse/cua.py`) are the recommended
defaults. A new classifier backend must implement `Classifier.ask` with the Noul, Choice and Score
primitives. A new browser backend must implement only the read-only `BrowserBackend` protocol.
`GuardedBrowser` applies every policy check.
