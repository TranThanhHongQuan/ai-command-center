"""Xác minh nguồn do model đưa ra: chỉ coi là đã xác minh khi tải được trang (HTTP 200, qua fetch chống SSRF).
Nếu model kèm trích dẫn `quote`, kiểm tra trích dẫn có thật trong trang."""
from __future__ import annotations
import html
import re

import rss

MAX_SOURCES = 5


def _text(raw: bytes) -> str:
    t = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', raw.decode('utf-8', 'ignore'))
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t))).casefold()


def verify_sources(sources: list, extra_urls: list[str] = (), fetcher=None) -> list[dict]:
    """Trả danh sách {title,url,verified,quote_found,error}. URL trùng (sau chuẩn hóa) chỉ kiểm tra một lần."""
    fetch = fetcher or (lambda u: rss.fetch_feed(u, accept='text/html,application/xhtml+xml,*/*;q=0.5'))
    cands, seen = [], set()
    for s in list(sources) + [{'title': '', 'url': u, 'origin': 'rss'} for u in extra_urls]:
        if not isinstance(s, dict):
            continue
        url = str(s.get('url', '')).strip()
        norm = rss.normalize_url(url)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        cands.append((s, url))
    out = []
    for s, url in cands[:MAX_SOURCES]:
        item = {'title': str(s.get('title', ''))[:200], 'url': url[:2000], 'verified': False, 'quote_found': None, 'error': None}
        if s.get('origin'):
            item['origin'] = s['origin']
        try:
            page = _text(fetch(url))
            item['verified'] = True
            quote = re.sub(r'\s+', ' ', str(s.get('quote') or '')).strip().casefold()
            if len(quote) >= 20:
                item['quote_found'] = quote in page
        except Exception as ex:  # noqa: BLE001
            item['error'] = f'{type(ex).__name__}: {str(ex)[:120]}'
        out.append(item)
    return out
