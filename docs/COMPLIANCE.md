# Compliance

Kestrel is built so that **recruiters and job sites never have a reason to object to it**.
Every rule below is enforced in code and covered by tests (`backend/tests/test_compliance.py`,
`backend/tests/test_browse_policy.py`). If you contribute, these rules are non-negotiable.

## 1. Kestrel never applies for you

- No code path submits an application, fills a form field or uploads a file on a third-party site.
- The browser interface (`browse/base.py`) has only `load`, `read_more`, `start` and `close`.
  It has no click, type or submit operations. The Cua Driver backend refuses every tool outside a
  read-only allowlist.
- Kestrel never sends an HTTP `POST` to a job site. A test checks this. The only POSTs go to
  the classification and LLM endpoints that *you* configure.
- Answer suggestions are shown in your dashboard for you to review, edit and copy. You always apply
  yourself, on the employer's own site.

## 2. Official APIs first

Jobs come from APIs that their owners publish for this purpose:
- the public Greenhouse, Lever and Ashby job-board APIs
- USAJOBS
- Adzuna

See [SOURCES.md](SOURCES.md) for each source's terms and how we follow them.

## 3. Sites that forbid bots are never contacted

`compliance/denylist.py` lists sites whose terms prohibit scraping or automated access, including
LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, Dice, Wellfound and Naukri. The list:
- is checked before **every** request, on every redirect hop, and in the browser
- also catches country domains such as `indeed.co.uk`
- cannot be turned off through configuration

To remove an entry, open a pull request that links to the site's written permission.

## 4. Optional browsing is opt-in, allowlisted and read-only

Browsing is for company careers pages that have no API. It is **off by default**. When enabled:
- **The lightest method comes first.** Kestrel fetches the page with one plain HTTP request and reads
  the schema.org `JobPosting` data that the site publishes for search engines. It follows at most 5
  job links per site this way. A browser (Cua Driver or Playwright) is launched only for sites that
  publish no job data at all.
- Only domains you explicitly list can be visited, and never a denylisted one. A denylisted site is
  not even asked for its robots.txt.
- `robots.txt` is honoured for our token `Kestrel`. If robots.txt can't be read because of an
  error or a 401/403, the site is not crawled.
- Page loads to the same site are spaced at least 10 seconds apart, or by the site's
  `Crawl-delay` if that is longer.
- Each site has a page budget per run (default 15).
- The browser starts with a **fresh, isolated profile**. It has no cookies and is never signed
  in to anything.
- A CAPTCHA or bot check, a login wall, or HTTP 401/403/429 **stops** browsing that site and
  pauses it for 24 hours. Kestrel never tries to solve, evade or bypass any of these.
- If a careers page is hosted on Greenhouse, Lever or Ashby, browsing stops. The company is added
  to your list and its official API is used instead.

## 5. Polite API usage

- Every request has an honest User-Agent: `Kestrel/<version> (+<project URL>; contact: <your email>)`.
  USAJOBS is the exception: it requires your registered email as the User-Agent.
- Requests are rate-limited per domain. Each source has a per-scan request budget well under its
  published quota.
- ATS boards are fetched at most every 6 hours. Keyword searches are spaced 1–3 hours apart.
  Automatic scans run at most every 6 hours.
- `429`/`503` responses with `Retry-After` are honoured. Long back-offs pause the source for at
  least an hour.

## 6. Attribution and data integrity

- Postings link to their original URL, and "Apply" always goes to the employer's or source's own page.
- Adzuna listings carry "Jobs by Adzuna" with a link back. USAJOBS listings credit USAJOBS.
- Data stays on your machine. Kestrel never republishes or redistributes feeds.

## 7. Honest answers

- The answer assistant may only use facts in your profile. Anything missing becomes a
  `[fill in: …]` placeholder rather than a guess. Eligibility questions (work authorization,
  sponsorship, clearance) are never answered unless your profile states the answer.
- Equal-opportunity, demographic and self-identification questions are **never** drafted. They are
  voluntary and personal.
- Acknowledgements, consents and attestations, such as privacy notices and "I certify…" statements,
  are **never** drafted. You read and confirm them yourself.
- If an employer asks applicants **not to use AI tools**, the assistant refuses to draft answers for
  that job. It checks both the posting and the application form, because many employers put this in
  an "AI usage attestation" question. A deterministic keyword check always runs alongside the
  classifier, so the safeguard never depends on a model.

## 8. Transparency

Every outbound request is written to a local audit log, including requests that were *blocked* and
why. Query strings are dropped so keys are never logged. You can view the log under
Settings → Audit log.

## Reporting a problem

If you operate a site and believe Kestrel behaves badly toward it, please open an issue. We will
add your site to the denylist on request.
