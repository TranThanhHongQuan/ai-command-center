"""RSS/Atom: chuẩn hóa URL, phân tích feed, lưu tin chống trùng. Không phụ thuộc FastAPI."""
from __future__ import annotations
import datetime as dt
import http.client
import ipaddress
import re
import socket
import ssl
import time
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


ALLOWED_PORTS = {80, 443}
TIMEOUT = 10          # giây cho mỗi thao tác socket
TOTAL_DEADLINE = 25   # giây cho cả một lần tải (kể cả redirect)
MAX_REDIRECTS = 3


class FetchError(ValueError):
    pass


def _is_public(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str.split('%')[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _validate(url: str, resolver=None) -> tuple[str, str, int, list[str]]:
    """Kiểm tra URL và phân giải DNS một lần. Trả (scheme, host, port, ips công khai).
    Từ chối nếu BẤT KỲ địa chỉ nào không công khai (loopback, riêng tư, link-local, metadata...)."""
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port
    except ValueError as ex:
        raise FetchError('URL không hợp lệ') from ex
    scheme = p.scheme.lower()
    if scheme not in ('http', 'https') or not p.hostname:
        raise FetchError('URL nguồn phải là http(s)')
    if p.username or p.password:
        raise FetchError('URL không được chứa tài khoản')
    port = port or (443 if scheme == 'https' else 80)
    if port not in ALLOWED_PORTS:
        raise FetchError(f'Cổng {port} không được phép')
    host = p.hostname
    try:
        infos = (resolver or socket.getaddrinfo)(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as ex:
        raise FetchError(f'Không phân giải được tên miền: {host}') from ex
    ips = list(dict.fromkeys(i[4][0] for i in infos))
    if not ips:
        raise FetchError(f'Không phân giải được tên miền: {host}')
    if not all(_is_public(i) for i in ips):
        raise FetchError('Không cho phép địa chỉ nội bộ/riêng tư')
    return scheme, host, port, ips


def check_feed_url(url: str, resolver=None) -> None:
    """Dùng khi thêm nguồn. Ném ValueError (FetchError) nếu URL không an toàn."""
    _validate(url, resolver)


def _open_socket(ip: str, port: int, timeout: float):
    return socket.create_connection((ip, port), timeout)


def fetch_feed(url: str, resolver=None) -> bytes:
    """Tải feed an toàn: kiểm tra từng bước redirect, kết nối thẳng tới IP đã kiểm tra
    (chống DNS rebinding), giới hạn timeout, số redirect và dung lượng."""
    deadline = time.monotonic() + TOTAL_DEADLINE
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, ips = _validate(current, resolver)
        sock = _open_socket(ips[0], port, TIMEOUT)
        try:
            if scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            sock.settimeout(TIMEOUT)
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
            conn.sock = sock  # dùng socket đã ghim IP, không phân giải lại
            sp = urllib.parse.urlsplit(current)
            target = (sp.path or '/') + ('?' + sp.query if sp.query else '')
            conn.request('GET', target, headers={'User-Agent': 'AICommandCenter/1.0 RSS Reader',
                                                 'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml',
                                                 'Accept-Encoding': 'identity'})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader('Location')
                if not loc:
                    raise FetchError('Redirect thiếu Location')
                current = urllib.parse.urljoin(current, loc)
                continue
            if resp.status != 200:
                raise FetchError(f'HTTP {resp.status}')
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise FetchError('Quá thời gian tải feed')
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise FetchError('Feed vượt quá giới hạn dung lượng')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    raise FetchError(f'Quá {MAX_REDIRECTS} lần redirect')


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
