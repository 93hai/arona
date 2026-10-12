# Bảo mật và liên hệ

Arona **3.0.0a1 — Experimental Core** đã công bố source tại [93hai/arona](https://github.com/93hai/arona). **PRODUCTION: NOT READY**. Chưa có stable release được hỗ trợ hoặc cam kết thời gian xử lý vulnerability.

## Báo lỗi

Với lỗi cài đặt, tài liệu hoặc hành vi không nhạy cảm, dùng [GitHub Issues](https://github.com/93hai/arona/issues). Ghi phiên bản Python/OS, lệnh đã chạy và kết quả đã che thông tin riêng.

Private vulnerability reporting chưa bật tại thời điểm review tài liệu. Chưa có private security inbox được maintainer xác nhận. [93hai](https://github.com/93hai) là public identity của maintainer, không phải một endpoint nhận exploit hoặc secret riêng tư. Nếu cần báo lỗi nhạy cảm, trước hết yêu cầu maintainer cung cấp kênh riêng qua một issue không chứa chi tiết nhạy cảm. Không đăng credential, dữ liệu cá nhân, exploit details, database hoặc log riêng vào issue/comment/PR công khai.

## Dữ liệu và cấu hình

- Giữ API key trong process environment hoặc một file development được chọn tường minh và không chia sẻ cùng source. Không commit `.env`, database, backup, log hoặc dữ liệu người dùng.
- Production chỉ lấy environment, cấm dotenv và yêu cầu Linux/non-root. Hướng dẫn Windows là development và maintenance offline.
- Arona 3.0 không có HTTP/WebSocket listener công khai hoặc Discord runtime transport. Legacy reference không phải chương trình được hỗ trợ.
- Model không tự cấp identity hoặc quyền. Candidate không thực thi code; không bỏ các guard để làm test PASS.

## Credential lịch sử

Owner báo key Gemini/Mistral cũ và Discord bot application cũ đã được xóa. Mức bằng chứng là **OWNER-REPORTED REMEDIATED**, không phải **PROVIDER-CONFIRMED REVOKED**. Các ảnh được mô tả chỉ phản ánh account/project/workspace đã chọn; việc thay bot hoặc ảnh Deleted User không độc lập chứng minh mọi token cũ đã revoked.

Repository công khai không mang Git history private gốc. Source/secret scan sạch không xác minh trạng thái tài khoản provider và không bảo đảm không còn mọi loại secret/lỗ hổng chưa biết. Không yêu cầu người báo lỗi gửi token để thử authenticate. Xem [trạng thái và bằng chứng](RELEASE_STATUS.md) và [security design](docs/SECURITY.md).
