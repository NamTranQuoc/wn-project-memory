# MÔ TẢ TRIỂN KHAI: HIERARCHICAL AGENTIC MEMORY SERVICE

Hệ thống là một Agentic Memory Service chạy độc lập, giao tiếp với các LLM Editor (Cursor, Claude Code) thông qua Model Context Protocol (MCP) và cung cấp REST API/SSE để nạp dữ liệu ngoại vi. Trọng tâm là quản lý "ngữ cảnh vận hành", định tuyến chính xác theo từng thư mục dự án (VD: `/workspace/my-org/my-project`).

---

## PHẦN 1: MANDATORY IMPLEMENTATION RULES (CẤU HÌNH AI EDITOR)

Agent bắt buộc tạo 2 file cấu hình dưới đây trước khi bắt đầu code:

### 1.1. Core Rules cho Claude (`CLAUDE.md`)
Tạo file `CLAUDE.md` ở root directory:

```markdown
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
```

### 1.2. Tham chiếu cho Cursor (`.cursorrules`)
Tạo file `.cursorrules` ở root directory:
```markdown
# CURSOR INSTRUCTIONS
You are an expert Agentic AI developer. 
**MANDATORY ACTION:** Read and strictly apply ALL rules defined in `CLAUDE.md` in the root directory. Pay special attention to "Automated Partitioning & Retention", "API Retry Mechanism", and "Context Window Protection".
```

---

## PHẦN 2: SYSTEM ARCHITECTURE & STACK
*   **Core:** FastAPI, LiteLLM, MCP Python SDK (stdio).
*   **Task Queue / Background Jobs:** `APScheduler` hoặc async retry loop để quản lý Partition và quá trình chưng cất dữ liệu.
*   **Database:** PostgreSQL + `pgvector` (Semantic Search) + `pg_trgm` (Fuzzy Search).
*   **ORM:** SQLAlchemy (Async).

---

## PHẦN 3: DATABASE SCHEMAS & INDEXING (4-LAYER MEMORY)

1.  **Tầng 1 (L1_WorkingMemory):** Lưu context phiên hiện tại.
    *   *Columns:* `id`, `project_path`, `current_focus_text`, `updated_at`.
2.  **Tầng 2 (L2_MetaMemory):** Lưu cấu trúc và luật chơi cơ bản.
    *   *Columns:* `id`, `project_path`, `environment_setup`, `project_structure`.
3.  **Tầng 3 (L3_DistilledKnowledge - Single Source of Truth):** Đã bóc tách ý.
    *   *Columns:* `id`, `project_path`, `entity_key`, `content`, `content_hash`, `source_hash`, `raw_event_id`, `embedding`, `last_verified_at`.
    *   *Indexes:* Unique `(project_path, entity_key)`. HNSW trên `embedding`. GIN trên `content`. B-Tree trên các FK.
4.  **Tầng 4 (L4_RawEvents - Data Lake):** Lưu vết thô (Append-only).
    *   *Columns:* `id`, `project_path`, `event_type`, `raw_content`, `source_hash`, `created_at`, `distillation_status` (Enum: pending, processed, failed - Phục vụ logic Retry).
    *   *Partitioning:* `PARTITION BY RANGE (created_at)`.
    *   *Indexes:* B-Tree trên `(project_path, created_at)`.
    *   *Automation:* Cronjob tự động tạo partition mới & DROP partition > 6 tháng tuổi.

---

## PHẦN 4: CORE MCP TOOLS (EXPOSED FUNCTIONS)

Output của các hàm dưới đây phải luôn đi qua `sanitize_and_truncate(text, max_len=1500)`.

1.  **`init_project_memory(project_path, initial_context)`**: Khởi tạo L2, L1.
2.  **`upsert_distilled_rule(project_path, entity_key, content, raw_event_id, source_hash)`**: Cập nhật L3 dựa trên hash mutation.
3.  **`search_memory(project_path, query, search_type="hybrid", limit=5)`**: Hỗ trợ semantic, keyword, hoặc hybrid.
4.  **`query_deep_memory_sql(project_path, sql_query)`**: Đọc L4 bằng SQL (tự bọc LIMIT 10).
5.  **`log_raw_event(project_path, event_type, content, source_hash)`**: Lưu vào L4 với `distillation_status='pending'`. Kích hoạt Background Task chưng cất (kèm Retry).
6.  **`get_raw_context(project_path, raw_event_id)`**: Trả về ngữ cảnh gốc từ L4.

---

## PHẦN 5: PROJECT INTEGRATION SKILL (SKILL INSTRUCTION)

**Nội dung `memory_agent_skill.md` (Để user inject vào dự án của họ):**
```markdown
# AGENTIC MEMORY SKILL INTEGRATION

You are connected to a Hierarchical Project Memory via MCP. This memory tracks architectural decisions, conventions, and context specific to this local project path.

**YOUR OPERATING RULES:**
0. **Bootstrapping (First Action):** When you enter this project, if you do not know the project's meta context, immediately call `init_project_memory` to initialize the database for this path.
1. **Always Check Context First:** Before making structural changes or writing large features, call `search_memory` (try `hybrid` search_type) to check for established rules.
2. **Handle Truncated Outputs:** If a search result ends with `... [truncated]`, use `get_raw_context` with the provided `raw_event_id` to retrieve the rest.
3. **Optimized SQL Queries:** When calling `query_deep_memory_sql`, ALWAYS include a `created_at` time-range. The backend drops data older than 6 months.
4. **Continuous Learning:** Upon receiving feedback or bug fixes, call `log_raw_event`. 
5. **Working Memory:** Use `update_working_memory` to leave scratchpad notes.
```

---

## PHẦN 6: GITHUB PUBLIC README (`README.md`)

```markdown
# Agentic Memory Service MCP

A Hierarchical, Agentic Memory Service designed to attach to Claude Code or Cursor via the Model Context Protocol (MCP). Protects LLM context windows while providing deep, project-specific operational memory. Built for Enterprise scale with automated retention and resilient APIs.

## Features
- **4-Layer Architecture:** Working Memory (L1), Meta (L2), Distilled Semantic Rules (L3), and Raw Data Lake (L4).
- **High Performance & Auto-Maintenance:** Implements automated PostgreSQL Table Partitioning. Automatically drops data older than 6 months to save disk space.
- **Resilient Background Distillation:** Uses robust retry mechanisms (handling Rate Limits) to guarantee raw events are converted into searchable rules.
- **Hybrid Search:** pgvector + pg_trgm integration.
- **Context Window Protection:** Aggressive sanitization, truncation, and read limits.

## Installation & Setup

### 1. Start Database
\`\`\`bash
docker-compose up -d  # Spins up Postgres + pgvector
\`\`\`

### 2. Local Setup
\`\`\`bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Add your API keys for LiteLLM
\`\`\`

### 3. Run Server
\`\`\`bash
uvicorn src.main:app --reload
\`\`\`
*(The background scheduler will automatically bootstrap your initial database partitions upon startup).*

## Integration
In any local project where you want your AI to use this memory, run:
\`\`\`bash
curl -sSL [https://raw.githubusercontent.com/](https://raw.githubusercontent.com/)<YOUR_GITHUB_HANDLE>/<REPO_NAME>/main/memory_agent_skill.md -o .agent_memory_rules.md
\`\`\`
Then instruct Cursor/Claude: *"Read `.agent_memory_rules.md` and follow the memory usage instructions."*
```