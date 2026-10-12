# Tổng quan bảo mật Arona 3.0

Trạng thái: các kiểm soát Foundation, Memory, Policy/Tools/Audit/Candidate và Core/Models đã được triển khai trong phạm vi Arona 3.0. Kết quả baseline và giới hạn ở [RELEASE_STATUS.md](../RELEASE_STATUS.md), không phải chứng nhận Production Ready.

Staging chỉ dùng Git cục bộ mới, không mang history gốc. Credential-like findings trong private history cần owner phân loại và revoke/rotate nếu là credential thật; không suy đoán đã xử lý. MIT/attribution đã được chuẩn bị, quyền cấp phép toàn bộ source vẫn cần xác nhận. Heuristic scan không chứng minh không còn secret hoặc lỗ hổng. Xem [phạm vi public snapshot](public_snapshot.md) và [security/contact](../SECURITY.md).

## Mối đe dọa

### Bên ngoài
- Prompt injection, Discord input độc hại, path traversal, symlink/junction.
- Rò rỉ secret, trộn Memory giữa người dùng, model output không hợp lệ.
- Provider timeout và tool privilege escalation.

### Bên trong
- Code execution không an toàn, truy cập dữ liệu trái phép, xử lý secret sai.
- Thiếu isolation, structured output chưa validation, system call sai.
- Logging/audit không đủ và thiếu confirmation cho hành động rủi ro cao.

## Permission level

| Level | Mô tả | Phạm vi Arona 3.0 |
|-------|-------|-------------------|
| P0 | Xử lý nội bộ | Được phép cho tool đã đăng ký |
| P1 | Đọc dữ liệu bên ngoài | Tắt (không có Web Search) |
| P2 | Sửa Candidate | Được phép qua Policy/Dispatcher |
| P3 | Chạy Sandbox | Tắt |
| P4 | Hành động bên ngoài cần confirmation | Tắt; không có P4 tool đăng ký |
| P5 | Quyền hệ thống, cần confirmation và whitelist | Tắt |

## Quản lý secret
- Chỉ đọc từ environment variable.
- Không hard-code trong source, không đưa vào prompt.
- Không log, audit hoặc lưu vào Memory/Candidate.
- Adapter provider không đưa API key vào lỗi; key dùng header phù hợp provider.

## Trusted identity và structured output
- `AuthenticatedIdentity` do trusted transport adapter cung cấp; payload không thể tự khai identity tin cậy.
- Model output không thể tự khai owner, workspace, authorization, permission, receipt hay `PolicyDecision`.
- Structured output luôn validation; không được dùng trực tiếp cho system call.

## Memory và Candidate
- Memory scoped nghiêm ngặt theo `owner_id` và `workspace_id`.
- Không lưu token, password, OTP hay private key.
- Candidate chỉ nhận relative path, chặn escape và secret, ghi atomically, không thực thi code.

## Audit, logging và authorization
- Allowed/denied tool request đi qua Policy → Dispatcher và audit bền vững.
- Secret được redaction; request ID được ghi khi phù hợp.
- Policy receipt chống forge/reuse/mismatch trong process; model không thể cấp quyền cho chính nó.

## Tính năng bị tắt
- Sandbox Runner, Web Search, AIRI, Self-Correction, auto-deployment.
- Root/sudo privilege, Candidate execution và plugin platform.

## Release/environment gate còn lại

Baseline đã nghiệm thu native Debian/systemd offline, có network guard bổ sung
chỉ dành cho validation. Không suy ra unit mặc định chặn mạng. Gemini/Mistral/
Codestral API thật, soak dài và power-loss vẫn chưa được nghiệm thu.
Xem [checklist Linux](linux_validation_checklist.md).
