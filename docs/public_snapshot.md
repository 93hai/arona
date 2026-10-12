# Phạm vi source công khai

Source **Arona 3.0 — 3.0.0a1 Experimental Core** đã được công bố tại [93hai/arona](https://github.com/93hai/arona). History public bắt đầu từ một commit gốc mới của snapshot 106 tệp đã review; không copy Git history private gốc. Các commit tài liệu tiếp theo không đồng nghĩa có phiên bản hoặc capability runtime mới.

## Những gì được giữ và loại trừ

Runtime source, SQL migrations, prompt resources, dependency locks và tests được giữ từ candidate đã kiểm chứng. Tài liệu public có thể được cập nhật để khớp trạng thái hiện tại. Các runtime prompt dưới `src/arona/prompts/` là contract ứng dụng đọc, không phải nội dung giới thiệu để dịch.

Không đưa private keys, `.env` thật, database, dữ liệu người dùng, log, backup, cache, build output, kế hoạch nội bộ hoặc test evidence archives vào public repository. Wheel/sdist và source archive không chứa `.git`. Một checkout Git có public commit history mới; khác với source archive không có Git metadata.

`.env.example` chứa template key trống. Credential markers và Windows paths giả trong tests là fixture tổng hợp để kiểm tra redaction/path traversal, không phải dữ liệu cá nhân. Một legacy Discord file được giữ chỉ vì security regression đọc AST để xác nhận default credential rỗng; nó không được import, chạy hoặc cài như runtime Arona 3.0.

## License và mức bằng chứng

Maintainer/public identity là **93hai**; MIT, copyright **2026 93hai**, đã được owner phê duyệt. Dependency vẫn giữ giấy phép riêng. Không tìm thấy xung đột quyền cụ thể trong lượt review, nhưng AI assistance hay việc không có copied-code marker không chứng minh mọi quyền đã được chứng nhận pháp lý độc lập.

Owner báo credential/application cũ đã được xử lý; đó là **owner-reported**, chưa **provider-confirmed**. Rủi ro tài khoản lịch sử được phân biệt với độ sạch của source public. **PRODUCTION: NOT READY**; xem [RELEASE_STATUS.md](../RELEASE_STATUS.md) và [SECURITY.md](../SECURITY.md).

Các report riêng, manifest/hash audit và artifact kiểm chứng được giữ ngoài repository. Git-aware regression cần tracked files trong Git checkout và không được sửa/skip để lấy PASS.
