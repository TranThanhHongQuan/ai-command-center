"""Editor Agent (Bước 2): lọc, gom nhóm sự kiện và chấm điểm tin bằng Python thuần — không gọi AI.
Không sửa/xóa bảng `news`; kết quả nằm ở `editor_items` và `editor_groups` (ghi lại hoàn toàn mỗi lần phân tích, nên chạy lặp là idempotent)."""
from __future__ import annotations
import datetime as dt
import json
import os
import re

import rss
import scoring

SCHEMA = '''
CREATE TABLE IF NOT EXISTS editor_items (news_id TEXT PRIMARY KEY, group_id TEXT, status TEXT NOT NULL, filter_reason TEXT, is_rep INTEGER NOT NULL DEFAULT 0, analyzed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS editor_groups (group_id TEXT PRIMARY KEY, rep_news_id TEXT NOT NULL, title TEXT NOT NULL, event_date TEXT, size INTEGER NOT NULL, source_count INTEGER NOT NULL, score INTEGER NOT NULL, grade TEXT NOT NULL, breakdown TEXT NOT NULL, reasons TEXT NOT NULL, items TEXT NOT NULL, status TEXT NOT NULL, auto_recommended INTEGER NOT NULL DEFAULT 0, selected INTEGER, analyzed_at TEXT NOT NULL);
'''
GRADE_LABELS = {'very_high': 'Rất đáng nghiên cứu', 'high': 'Đáng xem xét', 'watch': 'Theo dõi thêm', 'low': 'Ưu tiên thấp'}
FILTER_LABELS = {'invalid': 'Thiếu tiêu đề/URL hợp lệ', 'duplicate_url': 'Trùng URL', 'duplicate_title': 'Trùng tiêu đề cùng nguồn',
                 'stale': 'Quá cũ', 'not_tech': 'Không liên quan công nghệ'}
DEFAULT_WEIGHTS = {'freshness': 30, 'tech': 25, 'vietnam': 20, 'source': 15, 'novelty': 10}
DEFAULT_THRESHOLDS = {'very_high': 80, 'high': 65, 'watch': 40}
SOURCE_RANK = {'official': 4, 'international_press': 3, 'vietnam_tech': 2, 'vietnam_press': 2, 'other': 1}
SOURCE_BASE = {'official': 9, 'international_press': 6, 'vietnam_tech': 5, 'vietnam_press': 5, 'other': 3}
WINDOW_DAYS = 30        # chỉ phân tích tin thu thập trong 30 ngày gần nhất
SAME_EVENT_HOURS = 96   # hai tin cùng sự kiện phải cách nhau không quá 96 giờ

STOP = set('the a an of to in on for and or with at by from as is are be its it this that new says say after how why what will may more than into over about vs '
           'của và là các những một cho với trong khi được này đã sẽ để từ có không về như theo sau trước ra'.split())
EVENT_CLASSES = {
    'launch': r'launch\w*|unveil\w*|announc\w*|introduc\w*|debut\w*|releas\w*|reveal\w*|ra mắt|công bố|giới thiệu|trình làng',
    'review': r'review\w*|hands-on|benchmark\w*|tested|đánh giá|thử nghiệm|trên tay',
    'security': r'vulnerab\w*|breach\w*|hack\w*|exploit\w*|ransomware|malware|flaw|cve-\d+|lỗ hổng|bảo mật|tấn công',
    'price': r'pric\w*|discount\w*|giá|giảm giá',
    'business': r'earnings|revenue|acquir\w*|acquisition|layoffs?|lawsuit|sues?|funding|raises|ipo|stock|doanh thu|mua lại|sa thải|gọi vốn|cổ phiếu',
    'leak': r'leak\w*|rumou?r\w*|rò rỉ|tin đồn',
}


def load_config(env=None) -> dict:
    env = os.environ if env is None else env

    def kv(name, default):
        out = dict(default)
        for part in (env.get(name) or '').split(','):
            k, _, v = part.partition('=')
            if k.strip() in out:
                try:
                    out[k.strip()] = float(v)
                except ValueError:
                    return dict(default)
        return out
    w = kv('AICC_EDITOR_WEIGHTS', DEFAULT_WEIGHTS)
    total = sum(max(0, x) for x in w.values())
    w = {k: max(0, v) * 100 / total for k, v in w.items()} if total > 0 else dict(DEFAULT_WEIGHTS)
    th = kv('AICC_EDITOR_THRESHOLDS', DEFAULT_THRESHOLDS)
    if not th['very_high'] >= th['high'] >= th['watch'] >= 0:
        th = dict(DEFAULT_THRESHOLDS)

    def num(name, d):
        try:
            return max(1, int(env.get(name, d)))
        except ValueError:
            return d
    return {'weights': w, 'thresholds': th, 'daily_max': num('AICC_EDITOR_DAILY_MAX', 5), 'max_age_days': num('AICC_EDITOR_MAX_AGE_DAYS', 7)}


def grade(score: float, th: dict) -> str:
    return 'very_high' if score >= th['very_high'] else 'high' if score >= th['high'] else 'watch' if score >= th['watch'] else 'low'


def _dt(s):
    try:
        d = dt.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None
    return d.replace(tzinfo=dt.timezone.utc) if d and d.tzinfo is None else d


def _tokens(title: str) -> set[str]:
    return {t for t in re.findall(r'\w+', (title or '').casefold()) if t not in STOP and (len(t) >= 2 or t.isdigit())}


def _classes(title: str) -> set[str]:
    t = (title or '').casefold()
    return {k for k, pat in EVENT_CLASSES.items() if re.search(rf'(?<!\w)(?:{pat})(?!\w)', t)}


def similarity(a: str, b: str) -> tuple[float, float, int]:
    ta, tb = _tokens(a), _tokens(b)
    shared = len(ta & tb)
    if not ta or not tb or not shared:
        return 0.0, 0.0, 0
    return shared / len(ta | tb), shared / min(len(ta), len(tb)), shared


def same_event(a: dict, b: dict) -> float:
    """Trả điểm tương đồng (>0) nếu hai tin cùng một sự kiện, 0 nếu không. Heuristic tiết kiệm, không dùng embedding."""
    if abs((a['et'] - b['et']).total_seconds()) > SAME_EVENT_HOURS * 3600:
        return 0.0
    da = {t for t in _tokens(a['title']) if any(ch.isdigit() for ch in t)}
    db_ = {t for t in _tokens(b['title']) if any(ch.isdigit() for ch in t)}
    if da and db_ and not (da & db_):
        return 0.0  # RTX 5090 ≠ RTX 5080, iPhone 16 ≠ iPhone 17
    ca, cb = _classes(a['title']), _classes(b['title'])
    if ca and cb and not (ca & cb):
        return 0.0  # ra mắt ≠ lỗ hổng bảo mật ≠ đánh giá
    jac, ovl, shared = similarity(a['title'], b['title'])
    if jac >= 0.6 or (shared >= 3 and ovl >= 0.6 and da & db_):
        return jac + ovl
    return 0.0


def analyze(db, now: dt.datetime | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec='seconds')
    cutoff = (now - dt.timedelta(days=WINDOW_DAYS)).isoformat(timespec='seconds')
    with db() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT n.id,n.title,n.source,n.url,n.summary,n.published,n.created_at,COALESCE(s.category,'other') AS category,
               EXISTS(SELECT 1 FROM jobs j WHERE j.news_id=n.id AND j.status!='failed') AS processed
               FROM news n LEFT JOIN sources s ON s.id=n.source_id WHERE n.created_at>=? ORDER BY n.created_at,n.id""", (cutoff,))]
        article_titles = [r[0] for r in c.execute('SELECT title FROM articles')]
        chosen = {r[0]: r[1] for r in c.execute('SELECT group_id,selected FROM editor_groups WHERE selected IS NOT NULL')}

    # A. lọc
    items, cands, seen_url, seen_title = {}, [], set(), set()
    for r in rows:
        title = (r['title'] or '').strip()
        norm = rss.normalize_url(r['url'] or '')
        pub = _dt(r['published'])
        r['et'] = pub or _dt(r['created_at']) or now
        r['dated'] = pub is not None
        r['p'] = scoring.parts(title, r['summary'] or '', r['et'].isoformat(), now)
        tkey = (r['source'], ' '.join(sorted(_tokens(title))))
        reason = None
        if not title or not norm:
            reason = 'invalid'
        elif norm in seen_url:
            reason = 'duplicate_url'
        elif (now - r['et']).total_seconds() > cfg['max_age_days'] * 86400:
            reason = 'stale'
        elif r['p']['tech_hits'] == 0 and r['category'] != 'official':  # blog chính thức luôn thuộc mảng công nghệ (điểm công nghệ thấp nếu không có từ khóa)
            reason = 'not_tech'
        elif tkey in seen_title:
            reason = 'duplicate_title'
        if norm:
            seen_url.add(norm)
        if reason is None:
            seen_title.add(tkey)
            cands.append(r)
        items[r['id']] = {'status': 'filtered' if reason else 'candidate', 'reason': reason, 'group': None, 'rep': 0}

    # B. gom nhóm: so với tin đầu tiên (seed) của từng nhóm để tránh nhóm bị "trôi" theo chuỗi
    groups: list[dict] = []
    for r in sorted(cands, key=lambda x: (x['et'], x['id'])):
        best, best_s = None, 0.0
        for g in groups:
            sc = same_event(g['seed'], r)
            if sc > best_s:
                best, best_s = g, sc
        if best:
            best['members'].append(r)
        else:
            groups.append({'id': 'g_' + r['id'], 'seed': r, 'members': [r]})

    processed_titles = list(article_titles) + [r['title'] for r in rows if r['processed']]
    out_groups = []
    for g in groups:
        mem = g['members']
        rep = max(mem, key=lambda m: (SOURCE_RANK.get(m['category'], 1), len(m['summary'] or ''), -m['et'].timestamp()))
        cats = [m['category'] for m in mem]
        sources = sorted({m['source'] for m in mem})
        w = cfg['weights']
        best_fresh = max(((m['p']['fresh'] / 40) * (1 if m['dated'] else 0.4)) for m in mem)
        tech = max(m['p']['tech'] for m in mem) / 40
        vn = max(m['p']['vn'] for m in mem) / 20
        src = min(1.0, (SOURCE_BASE[max(cats, key=lambda x: SOURCE_RANK.get(x, 1))] + min(6, 2 * (len(sources) - 1))) / 15)
        overlap = max((similarity(rep['title'], t)[1] if similarity(rep['title'], t)[2] >= 3 else 0.0 for t in processed_titles), default=0.0)
        nov = 0.2 if overlap >= 0.6 else 0.6 if overlap >= 0.4 else 1.0
        bd = {'freshness': best_fresh * w['freshness'], 'tech': tech * w['tech'], 'vietnam': vn * w['vietnam'],
              'source': src * w['source'], 'novelty': nov * w['novelty']}
        total = max(0, min(100, round(sum(bd.values()))))
        reasons = list(rep['p']['reasons'])
        if not rep['dated']:
            reasons[0:1] = ['không rõ ngày xuất bản (điểm độ mới bị giảm)']
        best_cat = max(cats, key=lambda x: SOURCE_RANK.get(x, 1))
        reasons.append(('nguồn chính thức' if best_cat == 'official' else 'báo công nghệ') + (f', {len(sources)} nguồn đưa tin' if len(sources) > 1 else ''))
        if nov < 1:
            reasons.append('gần với nội dung đã xử lý')
        processed = any(m['processed'] for m in mem)
        out_groups.append({'id': g['id'], 'rep': rep, 'members': mem, 'sources': sources, 'score': total, 'breakdown': {k: round(v, 1) for k, v in bd.items()},
                           'reasons': reasons, 'processed': processed, 'date': max(m['et'] for m in mem).date().isoformat()})
        for m in mem:
            items[m['id']].update(group=g['id'], rep=int(m is rep))

    # D. đề xuất tối đa N sự kiện mỗi ngày
    by_day: dict[str, list] = {}
    for g in out_groups:
        if not g['processed'] and g['score'] >= cfg['thresholds']['high']:
            by_day.setdefault(g['date'], []).append(g)
    auto = {g['id'] for day in by_day.values() for g in sorted(day, key=lambda x: (-x['score'], x['id']))[:cfg['daily_max']]}

    with db() as c:
        c.execute('DELETE FROM editor_items')
        c.execute('DELETE FROM editor_groups')
        for nid, it in items.items():
            c.execute('INSERT INTO editor_items VALUES(?,?,?,?,?,?)', (nid, it['group'], it['status'], it['reason'], it['rep'], stamp))
        for g in out_groups:
            if g['processed']:
                for m in g['members']:
                    c.execute("UPDATE editor_items SET status='filtered',filter_reason='processed' WHERE news_id=?", (m['id'],))
            urls = [{'news_id': m['id'], 'source': m['source'], 'url': m['url'], 'title': m['title'], 'published': m['published']} for m in g['members']]
            c.execute('INSERT INTO editor_groups VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (g['id'], g['rep']['id'], g['rep']['title'], g['date'], len(g['members']), len(g['sources']), g['score'], grade(g['score'], cfg['thresholds']),
                       json.dumps(g['breakdown']), json.dumps(g['reasons'], ensure_ascii=False), json.dumps(urls, ensure_ascii=False),
                       'processed' if g['processed'] else 'candidate', int(g['id'] in auto), chosen.get(g['id']), stamp))
    return {'analyzed': len(rows), 'groups': len(out_groups), 'filtered': sum(1 for i in items.values() if i['status'] == 'filtered')}


def overview(db, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    with db() as c:
        total = c.execute('SELECT COUNT(*) FROM news').fetchone()[0]
        filt = dict(c.execute("SELECT filter_reason,COUNT(*) FROM editor_items WHERE status='filtered' GROUP BY filter_reason").fetchall())
        gs = [dict(r) for r in c.execute('SELECT * FROM editor_groups ORDER BY score DESC,group_id LIMIT 300')]
        analyzed_at = c.execute('SELECT MAX(analyzed_at) FROM editor_groups').fetchone()[0]
    for g in gs:
        for k in ('breakdown', 'reasons', 'items'):
            g[k] = json.loads(g[k])
        g['recommended'] = g['status'] == 'candidate' and (g['selected'] == 1 or (g['selected'] is None and g['auto_recommended'] == 1))
        g['grade_label'] = GRADE_LABELS[g['grade']]
    cand = [g for g in gs if g['status'] == 'candidate']
    forced = [g for g in cand if g['selected'] == 1]
    auto = [g for g in cand if g['selected'] is None and g['auto_recommended']]
    top = (forced + auto)[:max(cfg['daily_max'], len(forced))]
    same = sum(g['size'] - 1 for g in gs if g['size'] > 1)
    stats = {'total_news': total, 'filtered': sum(filt.values()), 'filtered_by_reason': {FILTER_LABELS.get(k, k): v for k, v in filt.items()},
             'duplicates': same + filt.get('duplicate_url', 0) + filt.get('duplicate_title', 0), 'same_event_extra': same, 'events': len(cand),
             'passing': sum(1 for g in cand if g['score'] >= cfg['thresholds']['high']), 'recommended': len(top), 'analyzed_at': analyzed_at}
    return {'stats': stats, 'top': top, 'groups': gs, 'config': {**cfg, 'grade_labels': GRADE_LABELS}}


def select(db, gid: str, choice) -> bool:
    with db() as c:
        return c.execute('UPDATE editor_groups SET selected=? WHERE group_id=?', (choice, gid)).rowcount > 0
