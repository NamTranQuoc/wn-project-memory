# CORE IMPLEMENTATION RULES & CONVENTIONS

## 1. SECURITY & CONFIGURATION
- **NO HARDCODED SECRETS:** Tuyệt đối không hardcode API keys, DB credentials.
- **Environment Variables:** Load từ `os.getenv` hoặc Pydantic `BaseSettings`.
- **.env Template:** Duy trì `.env.example`. File `.env` BẮT BUỘC nằm trong `.gitignore`.

## 2. DATABASE, PERFORMANCE & SCALABILITY (CRITICAL)
- **Automated Partitioning & Retention:** Bảng L4 BẮT BUỘC dùng Table Partitioning (PostgreSQL) theo thời gian. Hệ thống phải có Cronjob chạy ngầm để:
  1. Tự động tạo sẵn partition cho tháng tiếp theo.
  2. **Data Retention:** Tự động Drop (xóa) các partition ở L4 đã cũ hơn 6 tháng để giải phóng ổ cứng (vì tri thức đã được chưng cất lên L3).
- **Strict Indexing:** TUYỆT ĐỐI không để xảy ra Full Table Scan. Đánh chỉ mục: B-Tree (các khóa tra cứu), HNSW (cho `embedding`), GIN (cho Full-text `content`).
- **Data Atomicity:** L3 BẮT BUỘC phải phân tách các ý độc lập (tách nhỏ 1 message thành nhiều rule riêng).
- **Freshness & Dual-Hashing:** Áp dụng băm kép (`content_hash` và `source_hash`). Ghi đè (Overwrite) triệt để các rule cũ nếu hash thay đổi.

## 3. RESILIENCE & CONTEXT WINDOW PROTECTION
- **API Retry Mechanism:** Tác vụ chưng cất chạy ngầm (gọi LiteLLM) BẮT BUỘC phải có cơ chế Retry (sử dụng thư viện Queue như ARQ/Celery hoặc retry loop dựa trên state ở Database) để chống rớt mạng hoặc Rate Limit từ nhà cung cấp LLM.
- **Strict Output Limits:** Mọi Tool trả về dữ liệu phải có giới hạn chặt chẽ (default limit = 5 records).
- **Data Sanitization & Truncation:** Text > 1500 chars phải được cắt ngắn và thêm suffix `... [truncated, use get_raw_context to read full]`.
- **Safe SQL Execution:** Hàm `query_deep_memory_sql` phải tự động bọc `LIMIT 10` vào query.

## 4. CODE STYLE & ARCHITECTURE
- Python 3.10+, Type Hinting đầy đủ. Kiến trúc FastAPI phân lớp: `routers/`, `services/`, `models/`, `core/`. Sử dụng `LiteLLM` làm Gateway.
