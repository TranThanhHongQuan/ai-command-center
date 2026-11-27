# Tiến độ

## Giai đoạn 1 — xong
- Bảng `sources`, cột `news.url_norm/source_id/status`, index UNIQUE chống trùng (migration tự động cho DB cũ).
- `rss.py`: chuẩn hóa URL, parse RSS/Atom, chặn SSRF cơ bản, lưu tin, ghi trạng thái nguồn.
- API nguồn + thu thập thủ công; News Center hiển thị nguồn, trạng thái, thời điểm, tin.
- Test: `tests/test_rss.py` (chuẩn hóa URL, chống trùng, lỗi nguồn, API).

## Chưa làm
- Giai đoạn 2: lịch tự động, chống trùng theo nội dung/tiêu đề, đa site (`site_id`), đăng nhập.
- Giai đoạn 3: Master/Scout/Researcher/Writer/Checker, usage. Giai đoạn 4: mở rộng đa website.
- Redirect của feed chưa được kiểm tra lại SSRF; chưa chuyển sang Next.js.
