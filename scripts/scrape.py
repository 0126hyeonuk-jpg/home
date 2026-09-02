#!/usr/bin/env python3
"""Best-effort scraper for SH / LH / 청년안심주택 list pages.

Runs on GitHub Actions (real internet access — this repo's dev session
cannot reach these domains directly). Fetches each site's list page,
extracts candidate postings (title + date + link), and merges them into
data/live.json:

- Listings marked verified:true (PDF-checked by a human/Claude in chat)
  are NEVER modified or removed by this script.
- Newly discovered postings are added with placeholder "확인 필요" tags —
  this script cannot judge region/area/type match, only that a posting
  exists. A human (or a future chat session) should refine those tags.
- Existing non-verified listings whose title still matches a freshly
  scraped row get their date/link refreshed but keep their curated
  tags/meta/insight untouched.
- Non-verified, non-always listings that fall outside the rolling
  2-month window are dropped.
- If a site fetch/parse fails, that source's listings are left exactly
  as they were and the failure is recorded in scrape_status for the
  page banner / Action log — never silently invent data.
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "live.json"

KST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
TIMEOUT = 20
DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")

SCRAPE_TARGETS = {
    "soco": "https://soco.seoul.go.kr/youth/bbs/BMSR00015/list.do?menuNo=400008",
    "lh": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancList.do?mi=1026",
    "sh": "https://www.i-sh.co.kr/app/lay2/program/S48T1581C563/www/brd/m_247/list.do?multi_itm_seq=2",
}

PLACEHOLDER_TAGS = [
    ["warn", "📍 지역 확인 필요"],
    ["warn", "📐 면적 확인 필요"],
    ["warn", "💑 신혼부부 대상 여부 확인 필요"],
]


def log(msg):
    print(f"[scrape] {msg}", file=sys.stderr)


def parse_date(text):
    m = DATE_RE.search(text)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, tzinfo=KST).date()
    except ValueError:
        return None


def short_id(prefix, title):
    h = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-auto-{h}"


RELATIVE_TIME_RE = re.compile(r"\s*(\d+\s*(일|시간|분)\s*전|방금\s*전)\s*$")

# LH's list page is nationwide, not Seoul-scoped — only keep postings that
# look relevant to our priority region so the page doesn't fill up with
# provincial announcements that have nothing to do with 중랑·광진·강동.
REGION_KEYWORDS = {
    "lh": ["서울", "강동", "광진", "중랑"],
}

# List pages mix genuine new-application notices in with unrelated rows
# (winner announcements, RFP results, schedule tweaks). Keep only rows that
# read like an actual call for applications.
REQUIRE_KEYWORD = "모집"
EXCLUDE_KEYWORDS = ["당첨자", "동호배정"]


def looks_like_posting(title):
    return REQUIRE_KEYWORD in title and not any(kw in title for kw in EXCLUDE_KEYWORDS)


def normalize_title(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = RELATIVE_TIME_RE.sub("", text).strip()
    text = re.sub(r"^(NEW|HOT|N)\s*(?=\[|[가-힣])", "", text).strip()
    return text


def core_title(text):
    """A looser key for de-duplicating near-identical titles across scrapes."""
    t = re.sub(r"\[[^\]]*\]", "", text)  # drop bracketed office/region tags
    t = re.sub(r"\([^)]*\)", "", t)  # drop parenthetical dates/notes
    for suffix in ["예비입주자 모집공고", "입주자 모집공고", "예비입주자 모집", "입주자모집공고", "모집공고"]:
        t = t.replace(suffix, "")
    return re.sub(r"\s+", "", t).strip()


def fetch_rows(url):
    """Fetch a list page and return candidate (title, date, link) rows."""
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    candidates = []
    rows = soup.select("table tr") or soup.select("li")
    for row in rows:
        row_text = row.get_text(" ", strip=True)
        if len(row_text) < 8:
            continue
        d = parse_date(row_text)
        if not d:
            continue
        a = row.find("a")
        title = None
        if a and a.get_text(strip=True):
            title = a.get_text(strip=True)
        else:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "span"]) if c.get_text(strip=True)]
            cells = [c for c in cells if len(c) >= 8 and not DATE_RE.search(c)]
            if cells:
                title = max(cells, key=len)
        if not title or len(title) < 8:
            continue
        link = url
        if a and a.get("href") and not a["href"].startswith("javascript"):
            link = requests.compat.urljoin(url, a["href"])
        candidates.append((normalize_title(title), d, link))

    # de-dup by title, keep first occurrence
    seen = set()
    out = []
    for title, d, link in candidates:
        if title in seen:
            continue
        seen.add(title)
        out.append((title, d, link))
    return out


def scrape_source(key, url):
    try:
        rows = fetch_rows(url)
    except Exception as exc:  # noqa: BLE001 - best-effort scraper, never crash the run
        log(f"{key}: fetch/parse failed — {exc}")
        return None, str(exc)
    if not rows:
        log(f"{key}: fetched OK but found 0 candidate rows (site layout may not match parser)")
        return [], "0 rows parsed — layout may have changed, check parser"

    before = len(rows)
    rows = [r for r in rows if looks_like_posting(r[0])]

    keywords = REGION_KEYWORDS.get(key)
    if keywords:
        rows = [r for r in rows if any(kw in r[0] for kw in keywords)]

    log(f"{key}: found {before} candidate rows, {len(rows)} kept after content/region filtering")
    return rows, None


def merge_source(source, key, rows, today):
    existing_by_core = {core_title(l["title"]): l for l in source["listings"]}
    window_start = today - timedelta(days=60)

    if rows is not None:
        for title, d, link in rows:
            core = core_title(title)
            existing = existing_by_core.get(core)
            if existing is not None:
                if existing.get("verified"):
                    continue  # never touch human-verified entries
                existing["date"] = d.strftime("%Y.%m.%d")
                existing["insight"]["link"] = link
                continue
            if d < window_start:
                continue  # too old, don't bother adding
            new_id = short_id(key, title)
            source["listings"].append({
                "id": new_id,
                "date": d.strftime("%Y.%m.%d"),
                "title": title,
                "status": None,
                "verified": False,
                "tags": [t[:] for t in PLACEHOLDER_TAGS],
                "meta": "자동 수집된 신규 공고입니다 — 지역·면적·신혼부부 해당 여부는 아직 판정되지 않았어요. 원문을 확인해주세요.",
                "insight": {
                    "facts": [["발견", "GitHub Actions 자동 스크래핑"], ["날짜", d.strftime("%Y.%m.%d")]],
                    "text": "이 공고는 자동 스크래핑으로 새로 발견됐지만, 아직 지역·면적·신혼부부 해당 여부를 판정하지 못했어요. 원문 공고를 직접 확인해주세요.",
                    "link": link,
                },
            })
            existing_by_core[core] = source["listings"][-1]

    # drop stale, non-verified, non-always listings outside the window
    kept = []
    for listing in source["listings"]:
        if listing.get("verified"):
            kept.append(listing)
            continue
        status = listing.get("status")
        if status and status.get("cls") == "always":
            kept.append(listing)
            continue
        d = parse_date(listing["date"]) if listing["date"] != "상시" else None
        if d is None or d >= window_start:
            kept.append(listing)
    source["listings"] = kept
    source["listings"].sort(key=lambda l: (l["date"] == "상시", l["date"]), reverse=True)


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    now_kst = datetime.now(KST)
    today = now_kst.date()

    any_ok = False
    for key, url in SCRAPE_TARGETS.items():
        rows, err = scrape_source(key, url)
        data["sources"][key]["scrape_status"] = {
            "ok": rows is not None,
            "note": err,
            "checked_at": now_kst.isoformat(),
            "candidates": len(rows) if rows else 0,
        }
        if rows is not None:
            any_ok = True
        merge_source(data["sources"][key], key, rows, today)

    data["today"] = today.isoformat()
    data["window"] = {"start": (today - timedelta(days=60)).isoformat(), "end": today.isoformat()}
    data["updated_at"] = now_kst.isoformat()

    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"wrote {DATA_PATH} — any source scraped OK: {any_ok}")


if __name__ == "__main__":
    main()
