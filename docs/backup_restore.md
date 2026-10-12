# Backup và restore

Database maintenance là công việc của local operator, không phải autonomous tool. Runtime DB, backup directory và source phải tách biệt. Trên Linux, dùng directory private `0700` và file `0600`. Operator nên sao chép backup off-host; một disk đơn không phải disaster recovery.

Ví dụ PowerShell cho workspace thử mới ở [hướng dẫn tiếng Việt](../README-vi.md#thử-vận-hành-không-cần-api-key). Các lệnh bên dưới là CLI đã có, không khởi động provider hoặc service.

## Backup

Chạy `arona backup --operator-id operator-name` (development có thể đặt `--env-file .env` trước command). SQLite backup API tạo snapshot nhất quán, bao gồm WAL data đã commit; không copy raw live file. Connection đóng tường minh. Integrity và Arona schema được nhận diện được kiểm tra trước publish. Thời gian snapshot giới hạn 30 giây.

Artifact có UUID duy nhất, publish mà không thay file có sẵn và có companion `.receipt.json` chứa actor, scope, action và SHA-256. Receipt không chứa Memory content hoặc credential. Đây là operational record, không phải tamper-proof external audit storage. Tool/Policy audit đầy đủ thuộc implementation Phase 3.

## Restore drill

1. Liệt kê verified backup filename với local operator.
2. Chạy `arona restore --operator-id operator-name --backup-name arona_backup_<id>.db --restore-name restored.db`.
3. Kiểm tra record/schema offline. Snapshot v1 restore cần migration trước khi dùng.
4. Con người có thể dừng service và chuyển configured DB path sau khi review.

Restore **chỉ** tạo fresh path dưới configured Memory directory. Nó từ chối destination đã tồn tại, SQLite sidecar và backup path ngoài configured backup directory. `allow_replace=True` bị từ chối rõ ràng. Restore không restart service, không chạm production source và không ghi đè live DB.

## Giới hạn

Directory validation giả định maintenance path là private của local operator. Đây không phải descriptor-based Candidate writer. OS termination đột ngột giữa file publish và receipt completion có thể để lại cặp artifact không hoàn chỉnh: coi artifact không có receipt là chưa được phê duyệt và kiểm tra thủ công. Backup giữ historical data; retention/privacy deletion policy thuộc Memory và operational policy.
