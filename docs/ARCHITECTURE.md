# Tổng quan kiến trúc Arona 3.0

## Trạng thái implementation

Arona 3.0 đã triển khai Foundation/Database, Memory Engine, Policy/Tools/Audit/Candidate security và Core/Models/Lifecycle. Trạng thái validation nằm trong [RELEASE_STATUS.md](../RELEASE_STATUS.md). Baseline có native Linux/systemd offline validation; đây không phải Production Ready: live provider, soak dài, power-loss và các điều kiện phát hành vẫn chưa hoàn tất.

SQLite connection chuẩn là `src/arona/memory/db.py`; `src/arona/database/connection.py` là import compatibility shim. `src/arona/database/migrations.py` đọc SQL history bất biến từ `migrations/versions/` ở root. Không tạo placeholder cho capability tương lai.

## Các thành phần chính

### 1. Interaction Gateway
- Nhận request từ adapter tin cậy và chuẩn hóa input.
- Ràng buộc `user_id`, workspace và channel trong payload với `AuthenticatedIdentity` do transport cung cấp.
- Không tự quyết định quyền thực thi.

### 2. Conversation Orchestrator
- Cô lập theo `owner_id`, `workspace_id`, `conversation_id`.
- Ghép short-term history có giới hạn.
- Điều phối Memory, Model Gateway, Policy và Dispatcher.

### 3. Model Gateway
- Interface không phụ thuộc provider cụ thể.
- Provider cấu hình: Gemini, Mistral General và Codestral.
- Có timeout, health/cooldown, fallback hữu hạn, budget token/cost, persistence qua restart và degraded mode.

### 4. Memory Engine
- Loại memory: semantic, episodic, procedural, temporary.
- Lưu bằng SQLite với FTS5; Admission Policy chạy trước write.
- Có view, edit, delete, forget all, export.
- Không trộn owner/workspace.

### 5. Tool Registry
- Mô tả tool, schema và permission.
- Không thực thi tool.

### 6. Policy Engine
- Là authority duy nhất cho authorization decision.
- Level P0–P5; capability ngoài phạm vi 3.0 fail closed.

### 7. Candidate Workspace
- Chỉ hoạt động trong Candidate directory.
- Chặn path traversal, absolute path, symlink/junction và secret.
- Không chạm Runtime/Production, không thực thi code.

### 8. Database
- Migration tuần tự có schema versioning.
- Migration lỗi không được làm corrupt data.

### 9. Observability
- Logging, audit, request ID, correlation và secret redaction.

### 10. Backup/Restore
- Backup database tách riêng, có integrity check.
- Không ghi đè mù quáng database đang chạy.

## Luồng dữ liệu

### Xử lý message
```text
Client → Gateway → Conversation → Memory → Model Gateway → Policy → Dispatcher → Tool/Builder → Audit → Response
```

### Ghi Memory
```text
Client → Gateway → Conversation → Admission Policy → Memory → Audit
```

### Candidate Builder
```text
Client → Gateway → Conversation → Policy → Dispatcher → Builder → Audit → Response
```

### Provider fallback
```text
Client → Gateway → Conversation → Model Gateway → Primary → Fallback → Response
```

## Quy tắc dependency

- Dependency phải hướng về interface/domain; domain không phụ thuộc Discord hay provider implementation cụ thể.
- `core/` không truy cập SQLite trực tiếp hoặc gọi provider ngoài Model Gateway.
- Model chỉ đề xuất; không thể tạo identity, permission hay PolicyDecision.
- Tool handler chỉ chạy thông qua Policy và Dispatcher.

## Nguyên tắc an toàn

- Policy Engine đưa ra quyết định cuối cùng.
- Candidate không chạm Runtime/Production.
- Không hard-code hoặc log secret.
- Memory luôn scoped theo `owner_id` và `workspace_id`.
- Không dùng root/sudo.
- Không báo thành công chỉ vì không có exception.
