# Job sources

Each source is an adapter in `backend/kestrel/sources/` that declares a `SourcePolicy`
(terms URL, minimum request interval, attribution, per-scan request budget).

| Source | Key needed | What we read | Limits we apply | Attribution |
|---|---|---|---|---|
| Greenhouse job board | No | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`, plus `/jobs/{id}?questions=true` when you ask for help with a form | ≥ 2 s between calls; each board at most every 6 h | Original posting link |
| Lever postings | No | `api.lever.co/v0/postings/{site}?mode=json` (or `api.eu.lever.co`) | same | Original posting link |
| Ashby job board | No | `api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` | same | Original posting link |
| USAJOBS | Yes (free, per user) | `data.usajobs.gov/api/search` | ≥ 3 s between calls; ≤ 10 calls per scan; searches at most hourly | "Source: USAJOBS"; you apply on USAJOBS |
| Adzuna | Yes (free, per user) | `api.adzuna.com/v1/api/jobs/{country}/search/1` | ≥ 3 s between calls; ≤ 10 calls per scan; searches at most every 3 h (≤ 80/day, default quota is 250/day) | "Jobs by Adzuna" with a link back |
| Web (optional) | No | Allowlisted company careers pages, read-only | See [COMPLIANCE.md §4](COMPLIANCE.md#4-optional-browsing-is-opt-in-allowlisted-and-read-only) | "Read from <domain>" + original link |

## Terms to read

- Greenhouse Job Board API: https://developers.greenhouse.io/job-board.html
- Lever Postings API: https://github.com/lever/postings-api
- Ashby public job posting API: https://developers.ashbyhq.com/docs/public-job-posting-api
- USAJOBS API terms: https://developer.usajobs.gov/guides/terms-of-use
- Adzuna API terms: https://developer.adzuna.com/docs/terms_of_service

**USAJOBS** keys are for the registrant's own use. Each Kestrel user registers their own key,
and data is shown unaltered with USAJOBS credited.

**Adzuna** allows personal use. Commercial use needs Adzuna's agreement, so if you build a
commercial product on Kestrel, contact Adzuna first.

## Finding a company's board slug

Look at the company's careers page URL:

- `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` → `ats: greenhouse`
- `jobs.lever.co/<slug>` → `ats: lever` (`jobs.eu.lever.co` → also set `region: eu`)
- `jobs.ashbyhq.com/<slug>` → `ats: ashby`

With browsing enabled, Kestrel detects these automatically and adds them for you.

## Custom feeds

On the Companies page, under **Other job feeds**, you can add any RSS 2.0, Atom or JSON Feed that a
site publishes for job listings. No code is needed. Kestrel:

- saves a feed only after you confirm the site allows automated reading of it. Check the site's terms
  first: that responsibility is yours;
- never reads a site on the [denylist](COMPLIANCE.md), and skips a feed when robots.txt disallows it;
- reads each feed at most every 6 hours, waits at least 10 seconds between requests to the same site
  (longer if robots.txt sets a crawl delay), and reads at most 200 items and 1 MB per feed;
- only reads. It never posts anything to the site, and every job links back to the original posting
  with a "via" credit to the feed;
- parses XML with protections against entity-expansion and external-reference attacks, and keeps only
  ordinary web links.

## Adding a new source

See the checklist in [CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-job-source).
