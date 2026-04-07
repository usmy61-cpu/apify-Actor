"""
Normalizer — converts raw scraper output into a clean, unified job schema
suitable for Apify Dataset output.
"""

import re
from typing import Any


# Canonical job type normalization
_JOB_TYPE_MAP = {
    r"full.?time|vollzeit|plein.?temps|FULL_TIME":     "Full-time",
    r"part.?time|teilzeit|temps.?partiel|PART_TIME":   "Part-time",
    r"contract|freelance|contractor|mandats?":         "Contract",
    r"intern|internship|praktikum|stage":              "Internship",
    r"temporary|temp|befristet":                       "Temporary",
    r"remote|homeoffice|home.?office|télétravail":     "Remote",
    r"hybrid":                                         "Hybrid",
    r"volunteer|ehrenamt":                             "Volunteer",
}


def normalize_job(
    raw: dict,
    source: str,
    source_url: str,
    keyword: str,
    scraped_at: str,
) -> dict:
    """
    Takes a raw job dict from any scraper and returns a clean unified record.
    """
    title       = _clean_str(raw.get("title"))
    company     = _clean_str(raw.get("company"))
    location    = _clean_str(raw.get("location"))
    description = _clean_str(raw.get("description"))
    requirements = _clean_str(raw.get("requirements"))
    url         = _clean_str(raw.get("url"))
    posted_date = _clean_str(raw.get("postedDate"))
    is_remote   = raw.get("isRemote")

    # Normalize job type
    raw_type = _clean_str(raw.get("jobType"))
    job_type = _normalize_job_type(raw_type, title, description, is_remote)

    # Salary
    salary_text = _build_salary_text(
        raw.get("salary"),
        raw.get("salaryMin"),
        raw.get("salaryMax"),
        raw.get("salaryCurrency"),
    )

    # Extract requirements from description if not separately provided
    if not requirements and description:
        requirements = _extract_requirements(description)

    return {
        # Core fields
        "title":        title,
        "company":      company,
        "location":     location,
        "jobType":      job_type,
        "salary":       salary_text,
        "salaryMin":    raw.get("salaryMin"),
        "salaryMax":    raw.get("salaryMax"),
        "salaryCurrency": raw.get("salaryCurrency") or _infer_currency(location),
        "description":  description,
        "requirements": requirements,
        "isRemote":     bool(is_remote) if is_remote is not None else _infer_remote(job_type, title),
        # Meta
        "postedDate":   posted_date,
        "url":          url,
        "source":       source,
        "sourceUrl":    source_url,
        "keyword":      keyword,
        "scrapedAt":    scraped_at,
    }


# ── Private helpers ──────────────────────────────────────────────────────────

def _clean_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    # Remove excessive whitespace
    s = re.sub(r"\s{2,}", " ", s)
    # Remove HTML tags if any leaked through
    s = re.sub(r"<[^>]+>", " ", s).strip()
    return s if s else None


def _normalize_job_type(raw_type: str | None, title: str | None, desc: str | None, is_remote: Any) -> str | None:
    combined = " ".join(filter(None, [raw_type, title, desc]))
    for pattern, normalized in _JOB_TYPE_MAP.items():
        if re.search(pattern, combined, re.IGNORECASE):
            return normalized
    return raw_type or None


def _build_salary_text(
    salary: Any,
    sal_min: Any,
    sal_max: Any,
    currency: Any,
) -> str | None:
    """Build a human-readable salary string."""
    currency = currency or "CHF"

    if salary and str(salary).strip():
        return _clean_str(str(salary))

    if sal_min or sal_max:
        if sal_min and sal_max:
            return f"{currency} {_fmt_number(sal_min)} – {_fmt_number(sal_max)}"
        if sal_min:
            return f"From {currency} {_fmt_number(sal_min)}"
        if sal_max:
            return f"Up to {currency} {_fmt_number(sal_max)}"

    return None


def _fmt_number(val: Any) -> str:
    try:
        n = float(str(val).replace(",", "").replace("'", ""))
        if n >= 1000:
            return f"{n:,.0f}"
        return str(int(n))
    except Exception:
        return str(val)


def _extract_requirements(description: str) -> str | None:
    """
    Attempt to extract a requirements/qualifications section from a job description
    by looking for common section headers.
    """
    patterns = [
        r"(?i)(requirements?|qualifications?|what you.ll need|what we.re looking for|your profile"
        r"|dein profil|anforderungen|voraussetzungen|profil recherché)"
        r"[:\s]*\n((?:.+\n?){1,30})",
    ]
    for p in patterns:
        m = re.search(p, description)
        if m:
            return m.group(2).strip()
    return None


def _infer_currency(location: str | None) -> str:
    if not location:
        return "CHF"
    loc_lower = location.lower()
    if any(w in loc_lower for w in ("switzerland", "schweiz", "suisse", "svizzera", "zurich", "zürich", "geneva", "bern", "basel")):
        return "CHF"
    if any(w in loc_lower for w in ("germany", "deutschland", "berlin", "munich", "münchen")):
        return "EUR"
    if any(w in loc_lower for w in ("united kingdom", "uk", "london", "england")):
        return "GBP"
    if any(w in loc_lower for w in ("united states", "usa", "new york", "san francisco")):
        return "USD"
    return "CHF"


def _infer_remote(job_type: str | None, title: str | None) -> bool | None:
    if job_type and "remote" in job_type.lower():
        return True
    if title and re.search(r"\bremote\b", title, re.IGNORECASE):
        return True
    return None
