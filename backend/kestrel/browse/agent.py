"""Read-only browsing agent for company careers pages that have no official job API.

For each allowlisted site:
  1. Lightweight pass (no browser): fetch the start page politely; hand off to the official API if
     it is hosted on Greenhouse/Lever/Ashby, otherwise read the schema.org JobPosting data the
     site publishes (see structured.py).
  2. Only if the site publishes no job data, use the read-only browser backend: load the page,
     ask the classifier whether it is a single job posting (if so, keep it), otherwise pick a few
     same-site links that most likely lead to job postings.
Link choice uses the configured LLM when one is set, or a keyword heuristic otherwise.
Every page load goes through GuardedBrowser (allowlist, denylist, robots.txt, rate limit, block detection).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import deque
from urllib.parse import urldefrag, urljoin

import httpx

from kestrel.browse.ats_detect import detect_ats
from kestrel.browse.base import PageSnapshot, get_backend
from kestrel.browse.policy import DomainBlocked, GuardedBrowser
from kestrel.browse.structured import SiteBlocked, job_links, read_page
from kestrel.classify.base import Classifier, ClassifierError, Noul, NoulAnswer, get_classifier
from kestrel.compliance.denylist import PolicyViolation, domain_of, registrable_matches
from kestrel.compliance.http import PoliteClient
from kestrel.compliance.useragent import user_agent
from kestrel.config import get_settings
from kestrel.llm.provider import ChatClient, LLMConfig, LLMError, LLMNotConfigured
from kestrel.profile.schema import CandidateProfile
from kestrel.profile.store import load_profile
from kestrel.sources.base import JobPosting
from kestrel.sources.companies import add_company

log = logging.getLogger(__name__)

IS_POSTING = Noul(
    "This web page is a single, specific job posting with a role description "
    "(not a list of jobs, a general careers landing page, or a blog post).",
)
_JOBBY = re.compile(r"job|career|position|opening|vacanc|role|join|hiring|work-with-us|apply", re.I)
_SKIP = re.compile(r"\.(pdf|jpg|jpeg|png|gif|svg|zip|docx?)$|mailto:|tel:|javascript:|/login|/signin|/apply", re.I)


def start_urls(entries: list[str]) -> list[tuple[str, str]]:
    """Settings entries may be a domain ('example.com') or a start URL ('https://example.com/careers')."""
    out = []
    for entry in entries:
        e = entry.strip()
        if not e:
            continue
        url = e if e.startswith(("http://", "https://")) else f"https://{e.removeprefix('www.')}/"
        out.append((domain_of(url).removeprefix("www."), url))
    return out


def candidate_links(snapshot: PageSnapshot, domain: str, seen: set[str]) -> list[tuple[str, str]]:
    out = []
    for link in snapshot.links:
        href, _ = urldefrag(urljoin(snapshot.url, link.href))
        if href in seen or _SKIP.search(href) or not registrable_matches(domain_of(href), domain):
            continue
        out.append((link.text, href))
        seen.add(href)
    return out


async def choose_links(links: list[tuple[str, str]], profile: CandidateProfile, limit: int = 5) -> list[str]:
    if not links:
        return []
    try:
        client = ChatClient(LLMConfig.for_drafting())
        listing = [{"i": i, "text": t[:100], "url": u} for i, (t, u) in enumerate(links[:150])]
        data = await client.chat_json(
            [
                {
                    "role": "user",
                    "content": "We are reading a company careers site to find job postings for someone targeting: "
                    f"{', '.join(profile.target_titles) or ', '.join(profile.skills[:8])}.\n"
                    f"Pick up to {limit} links most likely to lead to relevant job postings or job "
                    "listings. Never pick login, apply, or account links. "
                    'Return JSON {"pick": [<i>, ...]}.\n' + json.dumps(listing),
                }
            ],
            temperature=0.0,
            max_tokens=200,
        )
        picks = data.get("pick", []) if isinstance(data, dict) else []
        chosen = [links[int(i)][1] for i in picks if str(i).isdigit() and int(i) < len(links)]
        if chosen:
            return chosen[:limit]
    except (LLMNotConfigured, LLMError, httpx.HTTPError):  # no model, or its server isn't running
        pass
    ranked = sorted(links, key=lambda tu: -len(_JOBBY.findall(tu[0] + " " + tu[1])))
    return [u for t, u in ranked if _JOBBY.search(t + " " + u)][:limit]


def posting_from_page(snapshot: PageSnapshot, company: str) -> JobPosting:
    title = snapshot.title.split("|")[0].split(" - ")[0].strip() or snapshot.title
    return JobPosting(
        source="web",
        external_id=hashlib.sha1(snapshot.url.encode(), usedforsecurity=False).hexdigest()[:16],
        company=company,
        title=title,
        url=snapshot.url,
        apply_url=snapshot.url,
        description=snapshot.text[:20000],
        attribution_text=f"Read from {domain_of(snapshot.url)}",
        attribution_url=snapshot.url,
    )


async def is_posting(classifier: Classifier, snapshot: PageSnapshot) -> bool:
    state = f"URL: {snapshot.url}\nTITLE: {snapshot.title}\n\n{snapshot.text[:8000]}"
    ans = (await classifier.ask(state, {"is_posting": IS_POSTING}))["is_posting"]
    return isinstance(ans, NoulAnswer) and ans.value >= 0.6


STRUCTURED_FOLLOW = 5  # extra pages read per site when the start page lists jobs without structured data


async def read_structured(http: PoliteClient, domain: str, start: str, company: str, say) -> list[JobPosting] | None:
    """Browser-free pass. Returns postings, [] when nothing was found, or None to stop this site."""
    try:
        page = await read_page(http, start, domain)
        if page is None:
            say(f"robots.txt disallows {start}; skipping {domain}.")
            return None
        ats = detect_ats([page.url] + [u for _, u in page.links], company)
        if ats:
            added = add_company(ats)
            say(
                f"{domain} uses {ats.ats} ({ats.slug}); "
                f"{'added it to your companies' if added else 'already in your companies'}. "
                "Its official API will be used instead of browsing."
            )
            return None
        found = list(page.postings)
        if not found:
            seen = {start, page.url}
            for url in job_links(page.links, domain, seen)[:STRUCTURED_FOLLOW]:
                sub = await read_page(http, url, domain)
                if sub:
                    found.extend(sub.postings)
        if found:
            say(f"{domain}: {len(found)} postings from the site's published job data (no browser needed).")
        return found
    except SiteBlocked as e:
        say(str(e))
        return None
    except PolicyViolation as e:
        say(f"Skipped {domain}: {e}")
        return None
    except httpx.HTTPError as e:  # one unreachable site must not stop the others
        say(f"Couldn't reach {domain} ({type(e).__name__}); skipped it this time.")
        return None


async def browse_sites(log_cb=None) -> list[JobPosting]:
    settings = get_settings()
    if not settings.browse_enabled:
        raise PolicyViolation("Browsing is disabled. Enable it in Settings → Browsing first.")
    say = log_cb or log.info
    sites = start_urls(settings.browse_domains)
    postings: list[JobPosting] = []
    needs_browser: list[tuple[str, str]] = []

    # 1. Lightweight pass: ATS hand-off and structured job data over plain, polite HTTP.
    async with PoliteClient() as http:
        for domain, start in sites:
            found = await read_structured(http, domain, start, domain.split(".")[0].capitalize(), say)
            if found:
                postings.extend(found)
            elif found is not None:
                needs_browser.append((domain, start))
    if not needs_browser:
        return postings

    # 2. Only pages that render their listings with JavaScript need a real (read-only) browser.
    postings.extend(await _browse_with_browser(needs_browser, say))
    return postings


async def _browse_with_browser(needs_browser: list[tuple[str, str]], say) -> list[JobPosting]:
    settings = get_settings()
    profile = load_profile()
    postings: list[JobPosting] = []
    names = ", ".join(d for d, _ in needs_browser)
    try:
        classifier = get_classifier()  # before launching a browser, so a missing key can't leave one running
    except ClassifierError as e:
        say(f"No published job data on {names}, and reading pages needs a classifier: {e}")
        return postings
    try:
        backend = get_backend(settings.browser, user_agent())
        await backend.start()
    except Exception as e:  # browser extra not installed or failed to launch
        say(
            f"No published job data on {names}, and the browser backend isn't available "
            f"({type(e).__name__}). Install it with: uv sync --extra browse"
        )
        return postings
    guarded = GuardedBrowser(backend, [d for d, _ in needs_browser], settings.browse_max_pages_per_domain)
    try:
        for domain, start in needs_browser:
            postings.extend(await _browse_site(guarded, classifier, profile, domain, start, say))
    finally:
        await backend.close()
    return postings


async def _browse_site(guarded: GuardedBrowser, classifier, profile, domain: str, start: str, say) -> list[JobPosting]:
    postings: list[JobPosting] = []
    queue, seen = deque([start]), {start}
    company = domain.split(".", maxsplit=1)[0].capitalize()
    while queue:
        url = queue.popleft()
        try:
            snap = await guarded.load(url)
        except DomainBlocked as e:
            say(str(e))
            break
        except PolicyViolation as e:
            say(f"Skipped {url}: {e}")
            if "budget" in str(e):
                break
            continue
        ats = detect_ats([snap.url] + [link.href for link in snap.links], company)
        if ats:
            added = add_company(ats)
            say(
                f"{domain} uses {ats.ats} ({ats.slug}); "
                f"{'added it to your companies' if added else 'already in your companies'}. "
                "Its official API will be used instead of browsing."
            )
            break
        try:
            posting = await is_posting(classifier, snap)
        except ClassifierError as e:
            say(f"Stopped reading {domain}: the classifier failed ({e}).")
            break
        if posting:
            postings.append(posting_from_page(snap, company))
            continue
        queue.extend(await choose_links(candidate_links(snap, domain, seen), profile))
    return postings
