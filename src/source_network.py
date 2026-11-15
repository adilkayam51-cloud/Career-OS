"""CareerOS read-only, bounded, multi-provider public job discovery.

This module does not apply to jobs, infer candidate eligibility, harvest personal
emails, or access an authenticated ATS.  Submitted applications still require
the separate guarded execution and receipt flow.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import json
import re
from typing import Callable, Iterable
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")
_VALID_SOURCES = {"greenhouse", "lever", "ashby"}
_MAX_BOARDS = 12
_MAX_JOBS_PER_BOARD = 40
_MAX_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True)
class Board:
    provider: str
    slug: str


@dataclass(frozen=True)
class Lead:
    provider: str
    company_board: str
    provider_job_id: str
    title: str
    job_url: str
    location: str
    remote_claimed: bool
    # Neither hiring-country eligibility nor application readiness can be
    # established merely from a public job-board listing.
    status: str = "SOURCE_DISCOVERED_NEEDS_JD_AND_ROUTE_VERIFICATION"


def _validate_board(board: Board) -> None:
    if board.provider not in _VALID_SOURCES:
        raise ValueError("Unsupported public source")
    if not _SLUG.fullmatch(board.slug):
        raise ValueError("Invalid employer board slug")


def public_url(board: Board) -> str:
    """Build from fixed, allowlisted hosts; never fetch user-provided URLs."""
    _validate_board(board)
    slug = quote(board.slug, safe="")
    if board.provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    if board.provider == "lever":
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"


def fetch_public_json(url: str) -> object:
    if not any(url.startswith(prefix) for prefix in (
        "https://boards-api.greenhouse.io/v1/boards/",
        "https://api.lever.co/v0/postings/",
        "https://api.ashbyhq.com/posting-api/job-board/",
    )):
        raise ValueError("Source URL is not an allowlisted public job feed")
    request = Request(url, headers={"User-Agent": "CareerOS-public-job-discovery/0.1", "Accept": "application/json"})
    # Never follow an upstream redirect to an unexpected or private host.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None

    with build_opener(NoRedirect).open(request, timeout=7) as response:
        # The length bound avoids accidentally pulling huge or unexpected data.
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ValueError("Feed response too large")
    return json.loads(payload)


def _safe_str(value: object, max_len: int = 320) -> str:
    return str(value or "").strip()[:max_len]


def _safe_https_link(value: object) -> str:
    from urllib.parse import urlparse
    link = _safe_str(value, 1500)
    parsed = urlparse(link)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return link


def parse_public_jobs(board: Board, data: object) -> list[Lead]:
    _validate_board(board)
    if board.provider == "greenhouse":
        rows = data.get("jobs", []) if isinstance(data, dict) else []
    elif board.provider == "lever":
        rows = data if isinstance(data, list) else []
    else:
        rows = data.get("jobs", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    leads: list[Lead] = []
    for item in rows[:_MAX_JOBS_PER_BOARD]:
        if not isinstance(item, dict):
            continue
        if board.provider == "greenhouse":
            loc = item.get("location") or {}
            location = loc.get("name", "") if isinstance(loc, dict) else loc
            name = item.get("title")
            url = item.get("absolute_url")
        elif board.provider == "lever":
            cats = item.get("categories") or {}
            location = cats.get("location", "") if isinstance(cats, dict) else ""
            name = item.get("text")
            url = item.get("hostedUrl") or item.get("applyUrl")
        else:
            location = item.get("location") or item.get("address", "")
            if isinstance(location, dict):
                location = location.get("name", "")
            name = item.get("title")
            url = item.get("jobUrl") or item.get("applyUrl")
        job_id = _safe_str(item.get("id"), 120)
        safe_url = _safe_https_link(url)
        if not (job_id and _safe_str(name) and safe_url):
            continue
        remote_flag = bool(item.get("isRemote")) or "remote" in _safe_str(location).lower()
        leads.append(Lead(board.provider, board.slug, job_id, _safe_str(name),
                          safe_url, _safe_str(location), remote_flag))
    return leads


def boolean_queries(lane: str) -> list[str]:
    """Cross-provider Boolean seeds; adapters must adapt unsupported operators."""
    if lane == "remote_india":
        return [
            '("remote India" OR "work from anywhere in India") ("B2B sales" OR "business development") ("apply" OR "send CV")',
            'site:jobs.lever.co/ ("account executive" OR "partnerships") "India"',
            'site:jobs.ashbyhq.com/ ("business development" OR "sales") "India"',
            '("SaaS" OR "AI automation") "remote" "India" ("careers@" OR "hiring@")',
        ]
    if lane == "geo_ncr_jaipur":
        return [
            '("Noida" OR "Gurugram" OR "Jaipur") ("B2B SaaS" OR "AI solutions") ("send resume" OR "apply")',
            'site:job-boards.greenhouse.io/ ("sales" OR "account manager") "India"',
            'site:linkedin.com/posts/ "hiring" "business development" ("Noida" OR "Gurgaon" OR "Jaipur")',
            '("business development manager" OR "strategic accounts") ("Noida" OR "Delhi NCR") "email CV"',
        ]
    raise ValueError("Unknown search lane")


def discover(boards: Iterable[Board], *, fetch: Callable[[str], object] = fetch_public_json) -> dict:
    """Bounded independent GET collectors. One source failure cannot stop others."""
    requested = list(boards)
    if len(requested) > _MAX_BOARDS:
        raise ValueError("Maximum 12 employer boards per bounded batch")
    for board in requested:
        _validate_board(board)
    results: list[Lead] = []
    failures: list[dict[str, str]] = []
    unique = list(dict.fromkeys(requested))
    with ThreadPoolExecutor(max_workers=min(4, max(len(unique), 1))) as pool:
        futures = {pool.submit(fetch, public_url(b)): b for b in unique}
        for future in as_completed(futures):
            board = futures[future]
            try:
                results.extend(parse_public_jobs(board, future.result()))
            except Exception as exc:
                # No stack traces or secret-bearing provider responses in logs.
                failures.append({"provider": board.provider, "board": board.slug,
                                 "error_type": type(exc).__name__})
    distinct: dict[tuple[str, str, str], Lead] = {}
    for item in results:
        distinct[(item.provider, item.company_board.lower(), item.provider_job_id)] = item
    return {
        "mode": "PUBLIC_JOB_DISCOVERY_ONLY_NO_APPLICATION_SUBMISSION",
        "boards_requested": len(unique),
        "lead_count": len(distinct),
        "leads": [asdict(x) for x in distinct.values()],
        "failures": sorted(failures, key=lambda item: (item["provider"], item["board"])),
        "required_next_step": "VERIFY_JD_FIT_INDIA_ELIGIBILITY_ROUTE_DEDUP_PACKET_BEFORE_SEND",
    }
