# Tiến độ

## Giai đoạn 1 — xong
- Bảng `sources`, cột `news.url_norm/source_id/status`, index UNIQUE chống trùng (migration tự động cho DB cũ).
- `rss.py`: chuẩn hóa URL, parse RSS/Atom, chặn SSRF cơ bản, lưu tin, ghi trạng thái nguồn.
- API nguồn + thu thập thủ công; News Center hiển thị nguồn, trạng thái, thời điểm, tin.
- Test: `tests/test_rss.py` (chuẩn hóa URL, chống trùng, lỗi nguồn, API).

## Giai đoạn 2 — xong (RSS tự động)
- `scheduler.py`: Scheduler thread (tắt mặc định, `AICC_SCHEDULER=1`), lease SQLite chống chạy trùng, bảng `collect_runs`, `GET /api/scheduler`, thẻ lịch quét trong News Center.
- `rss.fetch_feed`: SSRF an toàn theo từng redirect, ghim IP chống DNS rebinding, giới hạn timeout/dung lượng/redirect.
- Test: `tests/test_scheduler.py` (SSRF/redirect/rebinding, lease, scheduler, lỗi nguồn) — 29 test pass.
- **Chưa kiểm chứng:** tải RSS thật qua Internet và nhánh HTTPS/TLS (môi trường phát triển không có mạng ra ngoài; test dùng HTTP server cục bộ); nhiều worker thật chạy song song.

## Chưa làm
- Chống trùng theo nội dung/tiêu đề, đa site (`site_id`), đăng nhập.
- Giai đoạn 3: Master/Scout/Researcher/Writer/Checker, usage. Giai đoạn 4: mở rộng đa website.
- Chưa chuyển sang Next.js; chưa hỗ trợ nhiều instance dùng DB tách biệt.
