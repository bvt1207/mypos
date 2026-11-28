# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/); số phiên bản theo [SemVer](https://semver.org/lang/vi/)
(trước 1.0.0: tăng số giữa khi có thay đổi phá vỡ tương thích).

## [0.3.0] — 2026-11-29

Tính năng mới: tồn kho trên `/pos`, chặn bán vượt tồn, cờ "Gửi pha chế". Có **thay đổi hành vi** — đọc trước khi nâng cấp.

### Thay đổi hành vi
- **Chặn bán vượt tồn khả dụng** khi tạo đơn ở `/pos` và `/order` (server, khóa Bin trong cùng giao dịch: hai thu ngân cùng bán món cuối
  chỉ một đơn thành công). Tồn khả dụng = tồn sổ − giữ chỗ (Stock Reservation) − đã bán chưa gộp − **đơn nháp đang giữ chỗ**. Chặn độc lập
  với Stock Settings → Allow Negative Stock; chỉ món có `Item.allow_negative_stock = 1` không bị chặn. Kho luôn là kho của POS Profile
  (API không còn nhận `warehouse` từ client).
- Đơn nháp giữ chỗ tồn từ lúc tạo; hủy (tay / tự hủy) trả lại.
- **Màn Pha chế lọc theo cờ "Gửi pha chế"** của dòng MYPOS Menu: chỉ món cờ 1 hiện / in tem ly / phiếu pha chế; món cờ 0 ghi "Lấy tại quầy".
  Đơn chỉ gồm món cờ 0 chuyển thẳng "Đã làm" khi thanh toán (cột Sẵn sàng ở `/pos/status`), pha chế không thấy.
- **Món cũ mặc định "Gửi pha chế" = 1** (migrate thêm cột mặc định 1) → hành vi cũ giữ nguyên cho tới khi quản lý bỏ chọn trên menu.
- Thu tiền mặt khi tồn đã bị trừ ngoài POS → lỗi tiếng Việt "Không đủ hàng để thanh toán…". Webhook SePay trong trường hợp này: không submit,
  đơn giữ Draft, ghi nhận để quản lý xử lý (`needs_review`, Notification Log).

### Thêm
- `/pos`: số tồn khả dụng (xanh; 0 đỏ + thẻ mờ) cho món quản lý tồn; món có Product Bundle hiện số ly tối đa theo định mức ở bước chọn size,
  hết thì "Hết: <nguyên liệu>". Giỏ: "+" dừng ở mức tồn, nhập tay vượt tự hạ + thông báo. Cập nhật theo phiên bản thay đổi + realtime.
- `/order`: "Hết hàng" (không hiện số).
- Field `MYPOS Menu Item.send_to_kitchen` ("Gửi pha chế"), Custom Field `POS Invoice Item.custom_send_to_kitchen`.
- Nhập / xuất kho (Stock Ledger Entry) tăng phiên bản thay đổi của POS Profile dùng kho đó → số tồn trên `/pos` cập nhật ngay.
- API `pos_api.get_stock_availability`. Selfcheck: `stock_paid_check`, `stock_draft_hold_check`, `stock_oversell_check`,
  `kitchen_flag_check`, `kitchen_mixed_check`, `bundle_capacity_check` (tổng 31 test). Quy tắc: `docs/rules/stock.md`.

### Nâng cấp từ 0.2.x
1. Cập nhật code trong `apps/mypos`.
2. `bench --site <site> migrate` — thêm "Gửi pha chế" (MYPOS Menu Item) + Custom Field dòng hóa đơn (mặc định 1).
3. `cd apps/mypos/pos && npm run build` (không thêm package; site mới: `npm install` trước).
4. `bench build --app mypos`.
5. Khởi động lại bench (`bench restart` hoặc dừng / chạy lại `bench start`) — đổi Python + hooks (doc_events Stock Ledger Entry).
6. Desk → MYPOS Menu: bỏ chọn "Gửi pha chế" cho món giao tại quầy (bánh, nước đóng chai...).
7. Kiểm tồn đầu kỳ của món quản lý tồn (món tồn 0 sẽ không bán được). Không bật Stock Settings → Allow Negative Stock.
8. `bench --site <site> execute mypos.selfcheck.check_everything.check_everything` → 31 test.

## [0.2.0] — 2026-11-28

Bản sửa **bảo mật** và thay đổi hành vi. Đọc mục "Nâng cấp từ 0.1.x" trước khi cập nhật.

### Bảo mật
- Webhook SePay (`mypos.mypos.doctype.sepay.sepay.sepay_webhook`) chỉ nhận request có chữ ký **HMAC-SHA256** của SePay
  (`X-SePay-Signature: sha256=<hex>` + `X-SePay-Timestamp`, ký trên `"{timestamp}." + raw body`), so bằng secret của chi nhánh của đơn
  (`Branch.sepay_webhook_secret`). Từ chối: thiếu / sai chữ ký, timestamp lệch quá 300 giây, body bị sửa, gửi lại đúng request đã xử lý
  (chống phát lại), mọi cách xác thực khác. Không nâng quyền user khi xác thực.
- Xóa field cũ của Branch dùng cho xác thực webhook trước đây, cùng giá trị đã lưu (patch `v1_remove_sepay_api_key`, chạy khi migrate).

### Thay đổi hành vi
- Mode of Payment **chỉ** lấy từ bảng Payments của POS Profile (dòng Default trước, rồi thứ tự dòng): thu tiền mặt = mode loại Cash,
  QR / webhook / `/order` = mode loại Bank của profile. Profile thiếu mode cần dùng → báo lỗi rõ (trước đây lấy mode bất kỳ của công ty).
- Màn `/pos`: `get_menu_items` mặc định chỉ trả MYPOS Menu theo Price List bán của POS Profile (trước đây gộp menu mọi Price List → món
  trùng khi có nhiều chi nhánh).
- `/order` yêu cầu Branch có secret webhook HMAC (thiếu → "chưa bật thanh toán chuyển khoản").

### Thêm
- Logo MYPOS mới (đỏ / trắng).
- Selfcheck: `payment_mode_check`, `sepay_hmac_check`, `remove_api_key_patch_check` (tổng 25 test, `docs/testing.md`).

### Nâng cấp từ 0.1.x
1. Cập nhật code (`git pull` trong `apps/mypos`), `cd apps/mypos/pos && npm install && npm run build`, `bench build --app mypos`.
2. `bench --site <site> migrate` — tạo field "Secret webhook SePay (HMAC)" trên Branch, xóa field cũ (giá trị cũ bị xóa, không khôi phục).
3. SePay (my.sepay.vn → Webhooks): sửa webhook, bước **Bảo mật** chọn **HMAC-SHA256**, tạo Secret Key, copy ngay.
4. ERPNext → từng Branch dùng SePay → dán vào "Secret webhook SePay (HMAC)" → Save.
5. POS Profile: bảng Payments có 1 mode Cash (Default) + 1 mode Bank (có tài khoản) nếu dùng QR / `/order`.
6. Khởi động lại bench; chạy `bench --site <site> execute mypos.selfcheck.check_everything.check_everything`.
7. Đồng hồ server phải đúng (NTP): lệch quá 5 phút → mọi webhook bị từ chối.

Giữa bước 2 và bước 4, webhook SePay bị từ chối (403) — làm liền mạch, ngoài giờ bán nếu có thể. Chi tiết: `docs/rules/sepay.md` §1.

## [0.1.0] — 2026-11-27

Phát hành công khai đầu tiên: POS F&B (white-label) trên Frappe / ERPNext 16.61 — `/pos` (bán, đơn hàng, pha chế), `/order` (đặt món QR),
màn khách `/pos/customer`, màn theo dõi đơn `/pos/status`, SePay VietQR, Sidebar / màn Apps theo role.
