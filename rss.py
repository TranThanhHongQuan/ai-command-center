"""RSS/Atom: chuẩn hóa URL, phân tích feed, lưu tin chống trùng. Không phụ thuộc FastAPI."""
from __future__ import annotations
import datetime as dt
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

ATOM = '{http://www.w3.org/2005/Atom}'
TRACKING = {'fbclid', 'gclid', 'dclid', 'msclkid', 'mc_cid', 'mc_eid', 'igshid', 'ref', 'ref_src', 'yclid', '_hsenc', '_hsmi'}
MAX_BYTES = 2_000_000
MAX_ITEMS = 25


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def normalize_url(url: str) -> str | None:
    """Trả về URL chuẩn hóa để so trùng, hoặc None nếu không hợp lệ."""
    try:
        p = urllib.parse.urlsplit((url or '').strip())
        host = (p.hostname or '').lower().rstrip('.')
        if p.scheme.lower() not in ('http', 'https') or not host:
            return None
        scheme = p.scheme.lower()
        port = p.port
    except ValueError:
        return None
    if host.startswith('www.'):
        host = host[4:]
    netloc = f'[{host}]' if ':' in host else host
    if port and not ((scheme == 'http' and port == 80) or (scheme == 'https' and port == 443)):
        netloc += f':{port}'
    path = re.sub(r'/{2,}', '/', p.path or '/')
    if len(path) > 1:
        path = path.rstrip('/')
    query = sorted((k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith('utm_') and k.lower() not in TRACKING)
    # scheme được gộp về https: http/https của cùng bài là một tin
    return urllib.parse.urlunsplit(('https', netloc, path, urllib.parse.urlencode(query), ''))


def check_feed_url(url: str) -> None:
    """Chặn URL nội bộ (SSRF). Ném ValueError nếu không an toàn."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError('URL nguồn phải là http(s)')
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
    except socket.gaierror as ex:
        raise ValueError(f'Không phân giải được tên miền: {p.hostname}') from ex
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError('Không cho phép địa chỉ nội bộ/riêng tư')


def fetch_feed(url: str) -> bytes:
    check_feed_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': 'AICommandCenter/1.0 RSS Reader'})
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.read(MAX_BYTES)


def _clean(text: str | None, limit: int) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text or '')).strip()[:limit]


def _iso(raw: str) -> str:
    """Chuẩn hóa ngày xuất bản sang ISO; không đoán – rỗng nếu không đọc được."""
    raw = (raw or '').strip()
    if not raw:
        return ''
    try:
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return ''
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat(timespec='seconds')


def parse_feed(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    items = []
    entries = root.findall('.//item')
    if entries:
        for x in entries[:MAX_ITEMS]:
            items.append({'title': _clean(x.findtext('title'), 500), 'link': (x.findtext('link') or '').strip(),
                          'published': _iso(x.findtext('pubDate') or ''), 'summary': _clean(x.findtext('description'), 1000)})
    else:
        for x in root.findall(f'{ATOM}entry')[:MAX_ITEMS]:
            links = x.findall(f'{ATOM}link')
            alt = next((l for l in links if l.get('rel', 'alternate') == 'alternate'), links[0] if links else None)
            items.append({'title': _clean(x.findtext(f'{ATOM}title'), 500), 'link': (alt.get('href', '') if alt is not None else '').strip(),
                          'published': _iso(x.findtext(f'{ATOM}updated') or x.findtext(f'{ATOM}published') or ''),
                          'summary': _clean(x.findtext(f'{ATOM}summary') or x.findtext(f'{ATOM}content'), 1000)})
    return items


def store_items(conn, source: dict, items: list[dict]) -> tuple[int, int]:
    """Lưu tin; trả về (số tin mới, số tin trùng/bỏ qua). Chống trùng bằng url_norm UNIQUE."""
    added = skipped = 0
    for it in items:
        norm = normalize_url(it['link'])
        if not it['title'] or not norm:
            skipped += 1
            continue
        cur = conn.execute(
            'INSERT OR IGNORE INTO news(id,title,source,source_id,url,url_norm,summary,published,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()), it['title'], source['name'], source['id'], it['link'][:2000], norm,
             it['summary'], it['published'], now(), 'new'))
        added += cur.rowcount
        skipped += 1 - cur.rowcount
    return added, skipped


def collect_source(db, source: dict, fetcher=None) -> dict:
    """Thu thập một nguồn. Mạng chạy ngoài khóa DB; chỉ ghi DB sau khi tải xong.
    db: context manager trả về kết nối. Lỗi một nguồn không dừng nguồn khác."""
    started = now()
    try:
        items = parse_feed((fetcher or fetch_feed)(source['url']))
        with db() as conn:
            added, skipped = store_items(conn, source, items)
        res = {'source': source['name'], 'ok': True, 'added': added, 'skipped': skipped, 'error': None}
    except Exception as ex:  # noqa: BLE001 - báo lỗi thật cho UI
        res = {'source': source['name'], 'ok': False, 'added': 0, 'skipped': 0, 'error': f'{type(ex).__name__}: {str(ex)[:160]}'}
    with db() as conn:
        conn.execute('UPDATE sources SET last_fetched_at=?,last_status=?,last_error=?,last_added=? WHERE id=?',
                     (started, 'ok' if res['ok'] else 'error', res['error'], res['added'], source['id']))
    return res
