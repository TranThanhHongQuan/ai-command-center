"""Chấm điểm tin bằng Python thuần (0-100): độ mới (40) + từ khóa công nghệ (40) + phù hợp độc giả Việt Nam (20)."""
from __future__ import annotations
import datetime as dt
import re

TECH = ['ai', 'trí tuệ nhân tạo', 'chatgpt', 'openai', 'gemini', 'claude', 'copilot', 'llm', 'nvidia', 'chip', 'bán dẫn', 'semiconductor',
        'iphone', 'ios', 'android', 'windows', 'macos', 'samsung', 'apple', 'google', 'microsoft', 'meta', 'tesla', 'starlink',
        'bảo mật', 'security', 'lỗ hổng', 'vulnerability', 'ransomware', 'hack', 'phishing', '5g', 'wifi', 'cloud', 'robot', 'gpu',
        'laptop', 'smartphone', 'điện thoại', 'ra mắt', 'launch', 'release', 'update', 'cập nhật', 'open source', 'github', 'linux']
VN_DIRECT = ['việt nam', 'vietnam', 'viettel', 'vnpt', 'fpt', 'zalo', 'vinfast', 'vingroup', 'momo', 'tiếng việt', 'vietnamese', 'hà nội', 'hồ chí minh']
VN_BROAD = ['iphone', 'samsung', 'windows', 'android', 'chatgpt', 'facebook', 'tiktok', 'youtube', 'google', 'apple', 'whatsapp', 'wifi', 'pc']


def _has(text: str, term: str) -> bool:
    return re.search(rf'(?<![\w]){re.escape(term)}(?![\w])', text, re.I) is not None


def score_item(title: str, summary: str = '', published: str = '', now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    reasons: list[str] = []
    if published:
        try:
            age_h = max(0.0, (now - dt.datetime.fromisoformat(published)).total_seconds() / 3600)
        except ValueError:
            age_h = None
    else:
        age_h = None
    if age_h is None:
        fresh = 5; reasons.append('không rõ ngày xuất bản')
    else:
        fresh = 40 if age_h <= 6 else 32 if age_h <= 24 else 20 if age_h <= 72 else 10 if age_h <= 168 else 0
        reasons.append(f'đăng {age_h:.0f} giờ trước')
    t_hits = [k for k in TECH if _has(title, k)]
    s_hits = [k for k in TECH if k not in t_hits and _has(summary, k)]
    tech = min(40, 12 * len(t_hits) + 6 * len(s_hits))
    if t_hits or s_hits:
        reasons.append('từ khóa: ' + ', '.join((t_hits + s_hits)[:5]))
    blob = f'{title} {summary}'
    if any(_has(blob, k) for k in VN_DIRECT):
        vn = 20; reasons.append('liên quan Việt Nam')
    elif any(_has(blob, k) for k in VN_BROAD):
        vn = 10; reasons.append('sản phẩm phổ biến tại Việt Nam')
    else:
        vn = 0
    return {'score': fresh + tech + vn, 'reasons': reasons}
