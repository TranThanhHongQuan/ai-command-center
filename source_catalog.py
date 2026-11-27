"""Danh mục 25 nguồn do người dùng chọn.

source_type='rss': địa chỉ feed RSS/Atom có thể quét; 'website': trang tham khảo, KHÔNG quét RSS.
Các feed do người dùng cung cấp là ứng viên RSS; chỉ xác nhận thu thập thành công qua lần chạy local.
"""
from __future__ import annotations

# (category, name, url, source_type)
CATALOG = [
    ('official', 'OpenAI News', 'https://openai.com/news/rss.xml', 'rss'),
    ('official', 'Google Blog', 'https://blog.google/rss/', 'rss'),
    ('official', 'Microsoft Blog', 'https://blogs.microsoft.com/feed/', 'rss'),
    ('official', 'NVIDIA Blog', 'https://blogs.nvidia.com/feed/', 'rss'),
    ('official', 'GitHub Blog', 'https://github.blog/feed/', 'rss'),
    ('official', 'Cloudflare Blog', 'https://blog.cloudflare.com/rss/', 'rss'),
    ('official', 'AWS News Blog', 'https://aws.amazon.com/blogs/aws/feed/', 'rss'),

    ('vietnam_tech', 'Tinh tế', 'https://tinhte.vn/', 'website'),
    ('vietnam_tech', 'GenK', 'https://genk.vn/', 'website'),
    ('vietnam_tech', 'Thế Giới Di Động', 'https://www.thegioididong.com/', 'website'),
    ('vietnam_tech', 'Znews', 'https://znews.vn/', 'website'),
    ('vietnam_tech', 'CellphoneS', 'https://cellphones.com.vn/', 'website'),
    ('vietnam_tech', 'Tin Công Nghệ', 'https://www.tincongnghe.net/', 'website'),
    ('vietnam_tech', 'AnonyViet', 'https://anonyviet.com/', 'website'),

    ('vietnam_press', 'CafeF Công nghệ', 'https://cafef.vn/cong-nghe.html', 'website'),
    ('vietnam_press', 'VOV Công nghệ', 'https://vov.vn/cong-nghe', 'website'),
    ('vietnam_press', 'VietnamNet Công nghệ', 'https://vietnamnet.vn/cong-nghe', 'website'),
    ('vietnam_press', 'VnExpress Khoa học Công nghệ', 'https://vnexpress.net/khoa-hoc-cong-nghe', 'website'),
    ('vietnam_press', '24h Công nghệ', 'https://www.24h.com.vn/tin-tuc-cong-nghe-c453.html', 'website'),
    ('vietnam_press', 'Soha Công nghệ', 'https://soha.vn/cong-nghe.htm', 'website'),

    ('international_press', 'TechCrunch', 'https://techcrunch.com/feed/', 'rss'),
    ('international_press', 'The Verge', 'https://www.theverge.com/rss/index.xml', 'rss'),
    ('international_press', 'Ars Technica', 'https://feeds.arstechnica.com/arstechnica/index', 'rss'),
    ('international_press', 'Engadget', 'https://www.engadget.com/rss.xml', 'rss'),
    ('international_press', 'Hacker News', 'https://hnrss.org/frontpage', 'rss'),
]

MIGRATION = 'catalog_2026_11_27_25'
CATEGORY_LABELS = {
    'official': 'Blog công nghệ chính thức',
    'vietnam_tech': 'Trang công nghệ Việt Nam',
    'vietnam_press': 'Báo công nghệ Việt Nam',
    'international_press': 'Báo công nghệ quốc tế',
    'other': 'Nguồn thêm thủ công',
}


def install_catalog(conn, make_id, now):
    """Import đúng một lần, giữ nguyên nguồn RSS đã có và lựa chọn bật/tắt của người dùng.

    Nếu URL HTML trong danh mục đã bị thêm thủ công như RSS, chuyển về tham khảo
    và tắt quét để tránh HTTP 503/không phải XML. Không xóa lịch sử hay tin đã lưu.
    """
    conn.execute('CREATE TABLE IF NOT EXISTS catalog_migrations (key TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
    if conn.execute('SELECT 1 FROM catalog_migrations WHERE key=?', (MIGRATION,)).fetchone():
        return 0
    added = 0
    for category, name, url, source_type in CATALOG:
        existing = conn.execute('SELECT id,name,source_type,enabled FROM sources WHERE url=?', (url,)).fetchone()
        if existing:
            # Giữ tên và trạng thái cho RSS đã thêm; chặn nguồn HTML giả RSS.
            if source_type == 'website':
                conn.execute("UPDATE sources SET source_type='website', enabled=0, category=? WHERE id=?",
                             (category, existing['id']))
            else:
                conn.execute("UPDATE sources SET category=?, source_type='rss' WHERE id=?",
                             (category, existing['id']))
            continue
        conn.execute(
            'INSERT INTO sources(id,name,url,enabled,created_at,source_type,category) VALUES(?,?,?,?,?,?,?)',
            (make_id(), name, url, int(source_type == 'rss'), now(), source_type, category))
        added += 1
    conn.execute('INSERT INTO catalog_migrations(key,applied_at) VALUES(?,?)', (MIGRATION, now()))
    return added
