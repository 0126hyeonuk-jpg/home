#!/usr/bin/env python3
"""Render docs/index.html from templates/page.html + data/live.json."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "live.json"
TEMPLATE_PATH = ROOT / "templates" / "page.html"
OUTPUT_PATH = ROOT / "docs" / "index.html"
ACTIONS_URL = "https://github.com/0126hyeonuk-jpg/home/actions/workflows/update.yml"
REPO_LIST_URL = "https://github.com/0126hyeonuk-jpg/home/commits/main/data/live.json"

SOURCE_ORDER = ["soco", "lh", "sh"]
SOURCE_IDS = {"soco": "src-soco", "lh": "src-lh", "sh": "src-sh"}


def esc(text):
    return html.escape(str(text), quote=False)


def render_tags(listing):
    parts = []
    if listing.get("verified"):
        parts.append('<span class="pill verified">📄 PDF 확인됨</span>')
    status = listing.get("status")
    if status:
        parts.append(f'<span class="status-badge {status["cls"]}">{esc(status["label"])}</span>')
    for cls, label in listing.get("tags", []):
        parts.append(f'<span class="pill {cls}">{esc(label)}</span>')
    return "\n          ".join(parts)


def render_listing(listing):
    verified_attr = ' data-verified="true"' if listing.get("verified") else ""
    return f'''      <div class="listing clickable" data-id="{esc(listing["id"])}"{verified_attr} role="button" tabindex="0">
        <div class="listing-top">
          <div class="listing-title">{esc(listing["title"])}</div>
          <div class="listing-date-group"><span class="listing-date">{esc(listing["date"])}</span><span class="listing-chevron">›</span></div>
        </div>
        <div class="listing-tags">
          {render_tags(listing)}
        </div>
        <div class="listing-meta">{esc(listing["meta"])}</div>
      </div>'''


def render_source(key, source):
    listings_html = "\n\n".join(render_listing(l) for l in source["listings"])
    notice_html = ""
    if source.get("notice"):
        notice_html = f'\n\n      <div class="listing">\n        <div class="listing-meta" style="font-style: italic;">{esc(source["notice"])}</div>\n      </div>'
    return f'''    <article class="source-section" id="{SOURCE_IDS[key]}">
      <div class="source-head">
        <h2>{esc(source["name"])}</h2>
        <span class="site">{esc(source["site"])}</span>
      </div>

{listings_html}{notice_html}

      <div class="filter-empty" hidden>지금은 표시할 공고가 없어요 — "전체" 보기로 바꿔서 확인해보세요.</div>

      <div class="source-foot">
        <a class="btn ghost" href="{esc(source["foot_url"])}" target="_blank" rel="noopener">{esc(source["foot_label"])}</a>
      </div>
    </article>'''


def build_insights(data):
    insights = {}
    for key, source in data["sources"].items():
        source_label = f'{source["name"]} · {source["site"]}'
        for listing in source["listings"]:
            tags = []
            if listing.get("verified"):
                tags.append(["verified", "📄 PDF 확인됨"])
            status = listing.get("status")
            if status:
                tags.append([status["cls"], status["label"], True])
            tags.extend(listing.get("tags", []))
            ins = listing["insight"]
            insights[listing["id"]] = {
                "source": source_label,
                "title": listing["title"],
                "tags": tags,
                "facts": ins["facts"],
                "insight": ins["text"],
                "link": ins["link"],
            }
    return insights


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    window = data["window"]
    window_label = f'{window["start"][5:7]}.{window["start"][8:10]}~{window["end"][5:7]}.{window["end"][8:10]}'
    updated_dt = data["updated_at"]
    updated_label = f'{updated_dt[0:10]} {updated_dt[11:16]}'
    updated_line = f'최근 갱신 {updated_label} · 다음 자동 갱신 매일 10:00 (KST) · 사이트별 최근 2개월({window_label}) 공고 최신순'

    sections_html = "\n\n".join(render_source(key, data["sources"][key]) for key in SOURCE_ORDER)

    insights = build_insights(data)

    out = template
    out = out.replace("{{UPDATED_LINE}}", esc(updated_line))
    out = out.replace("{{ACTIONS_URL}}", ACTIONS_URL)
    out = out.replace("{{SOURCE_SECTIONS}}", sections_html)
    out = out.replace("{{INSIGHTS_JSON}}", json.dumps(insights, ensure_ascii=False, indent=2))
    out = out.replace("{{TODAY_JSON}}", json.dumps(data["today"], ensure_ascii=False))
    out = out.replace("{{EVENTS_JSON}}", json.dumps(data["events"], ensure_ascii=False, indent=2))
    out = out.replace("{{UNDATED_NOTE_JSON}}", json.dumps(data["undated_note"], ensure_ascii=False))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(out, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
