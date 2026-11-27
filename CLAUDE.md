# CLAUDE.md
Stack: FastAPI + SQLite (`server.py`), `rss.py` (RSS/dedupe/fetch an toàn), `scheduler.py` (lịch quét + lease), UI tĩnh `static/`. Chưa dùng Next.js.
- Test: `pip install -r requirements.txt httpx && python -m unittest discover -s tests`
- Chạy: `python -m uvicorn server:app --port 8787`
- Không mock dữ liệu; không commit secrets (.env, data/); không tự publish WordPress (chỉ draft).
- Không gọi API AI trả phí. Tiết kiệm token: không subagent, không đọc file không liên quan.
- Tiến độ: `docs/PROGRESS.md`. Trả lời tiếng Việt, ngắn gọn.
