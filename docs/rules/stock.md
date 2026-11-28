# Module Rules: Tồn khả dụng trên /pos, chặn bán vượt tồn, cờ "Gửi pha chế" (0.3.0)

> Paths: `mypos/mypos/doctype/pos_invoice_hooks/stock_availability.py` (tính tồn + chặn), `pos_invoice_hooks.py` (`has_kitchen_items`,
> `on_submit`), `change_feed.py` (`on_stock_ledger_change`), `api/pos_api.py` (`get_stock_availability`, `create_draft_order`, `pay_cash`,
> `_build_invoice`, `get_kitchen_orders`, `get_order_detail`), `api/customer_order_api.py` (`get_menu` → `sold_out`, `create_order`),
> `doctype/sepay/sepay.py` (`_record_stock_blocked_payment`), `doctype/mypos_menu/` (`send_to_kitchen`, `kitchen_flags`, `menu_item_codes`),
> `fixtures/custom_field.json` (`POS Invoice Item-custom_send_to_kitchen`), frontend `pos/src/store/stock.ts`, `store/cart.ts`,
> `components/MenuGrid.tsx`, `SizeModal.tsx`, `CartPanel.tsx`, `pages/Kitchen.tsx`, `CupLabels.tsx`, `ReceiptModal.tsx`, `order/components/*`.
> Kiểm: `stock_paid_check`, `stock_draft_hold_check`, `stock_oversell_check`, `kitchen_flag_check`, `kitchen_mixed_check`, `bundle_capacity_check`.

## 1. Tồn khả dụng (hướng A — không đổi cách ERPNext ghi sổ kho)

ERPNext vẫn trừ kho thật khi đóng ca (gộp POS Invoice → Sales Invoice). Kho = `POS Profile.warehouse` (client không gửi được kho).

```
khả dụng = Bin.actual_qty − Bin.reserved_stock          (tồn sổ − giữ chỗ Stock Reservation Entry)
         − đã bán chưa gộp                              (POS Invoice Item.stock_qty + Packed Item.qty; docstatus 1; consolidated_invoice trống)
         − giữ chỗ bởi đơn NHÁP                         (POS Invoice docstatus 0, không phải trả hàng; bundle tách ra nguyên liệu)
```

- Ba thành phần đầu theo đúng điều kiện ERPNext 16.61 (`pos_invoice.get_available_stock_qty`, `get_pos_reserved_qty_from_table`; đã đọc mã).
  Dùng lại trực tiếp `get_active_bundle_items`, `get_stock_item_details`, `is_stock_check_needed`. Truy vấn tồn viết lại trong
  `stock_availability.py` vì khi chặn bán phải đọc bằng locking read (§3); `stock_paid_check` A2 so với hàm ERPNext để bắt lệch.
- Trừ tồn tính từ lúc **thanh toán** (submit): đơn chuyển từ "giữ chỗ nháp" sang "đã bán chưa gộp" — tổng không đổi. "Đã làm" của bếp không
  ảnh hưởng tồn.
- Đơn nháp giữ chỗ từ lúc tạo; hủy (tay, job tự hủy 2 giờ, QR hết hạn 15 + 5 phút) → docstatus 2 → trả chỗ. Đơn QR đã quá 15 phút nhưng
  chưa bị job hủy vẫn giữ chỗ (webhook còn nhận tiền trong 5 phút ân hạn).
- `Item.allow_negative_stock = 1` (và không có Stock Reservation): hiện số, **không chặn** (như ERPNext). **Stock Settings → Allow Negative
  Stock KHÔNG ảnh hưởng mypos**: mypos vẫn chặn khi tạo đơn (ERPNext bỏ qua kiểm tra khi submit nếu bật). Không bật cờ này.

## 2. Hiển thị (`get_stock_availability`)

- Một lần cho cả menu của POS Profile (`MYPOSMenu.menu_item_codes`: món thường + variant đang bật của template), ~8 truy vấn, cache Redis
  `mypos_stock:<kho>:<Price List>:<phiên bản>` `CACHE_SECONDS = 15`. Trả `{item_code: {kind, qty, enforce, short}}`.
- `kind = "stock"` (Maintain Stock = 1): `qty = floor(khả dụng)`, không âm.
- `kind = "bundle"` (Product Bundle **đang bật**): số ly tối đa = `min(floor(khả dụng nguyên liệu / định mức mỗi ly))` theo nguyên liệu cần
  kiểm; `short` = tên nguyên liệu thiếu cho 1 ly ("Hết: Đá viên"). Mỗi size (S / M / L) là mã riêng → tính riêng, hiện ở bước chọn size.
  **Đây là ước tính theo định mức, độc lập từng món** (không cộng dồn: hai món cùng dùng một nguyên liệu đều hiện số tối đa của riêng mình).
- Món không quản lý tồn và không có bundle: không có số. Hiện chưa nạp Product Bundle → đồ uống chưa có số; nạp bundle là tự hiện.
- /pos: số xanh = còn; 0 đỏ + thẻ mờ, không bấm (món 1 size). Món nhiều size: số ở SizeModal; mọi size hết → thẻ mờ. Không có ngưỡng "sắp hết".
- /order (khách): chỉ cờ `sold_out` (size + món), **không** có số tồn; thẻ "Hết hàng", size hết không chọn được.

## 3. Chặn bán vượt tồn

- **Giao diện** (`store/cart.ts`, `store/stock.ts`): giới hạn theo TỔNG các dòng cùng mã (khác tùy chọn). Thêm món vượt → chỉ thêm phần còn
  lại; nút "+" dừng ở mức tồn (tắt nút); nhập tay vượt → tự hạ về mức còn lại cho dòng (tối thiểu 1, không tự xóa dòng) + toast
  "<món>: chỉ còn N" / "<món> đã hết hàng" (3 giây). Giao diện chỉ chặn sớm; server là chốt chặn thật.
- **Server** `check_invoice_stock(invoice)` trong `create_draft_order` (/pos) và `customer_order_api.create_order` (/order), trước `insert`,
  cùng giao dịch:
  1. tách bundle → nguyên liệu, cộng nhu cầu theo (mã, kho) cho cả đơn;
  2. `SELECT … FROM tabBin … ORDER BY name FOR UPDATE` (cùng thứ tự khóa ERPNext `stock/utils.py`) → hai thu ngân cùng bán món cuối:
     người sau chờ người trước commit;
  3. đã bán chưa gộp + giữ chỗ nháp đọc bằng `LOCK IN SHARE MODE` — DB chạy **REPEATABLE-READ** (đã kiểm `@@tx_isolation`), đọc thường
     dùng snapshot cũ và không thấy đơn nháp vừa commit. Truy vấn nháp lọc theo `item_code` (mã + bundle chứa mã) để khóa ít dòng;
  4. thiếu → `MyposStockError`: "Không đủ hàng để tạo đơn: X chỉ còn 1; Y — Hết: Đá viên."
- `pay_cash`: trước khi submit kiểm theo công thức ERPNext (`submit_shortfall`, không trừ nháp) → lỗi tiếng Việt thay cho thông báo
  tiếng Anh của ERPNext.
- **Webhook SePay — tiền về nhưng thiếu tồn** (tồn bị trừ ngoài POS sau khi đơn giữ chỗ, vd. Stock Entry / Sales Invoice trên Desk):
  không submit, đơn giữ Draft; ghi giao dịch vào `sepay_payment_log` (`stock_blocked`), Comment, Error Log "SePay: tiền về nhưng thiếu tồn
  để submit", Notification Log cho quản lý của POS Profile; trả `{success, needs_review}`; idempotent theo `transaction_id`. Quản lý: nhập /
  điều chỉnh kho rồi submit đơn trên Desk (mode chuyển khoản), hoặc hủy + hoàn tiền. Lưu ý: đơn QR Draft vẫn bị job tự hủy sau 20 phút
  (giao dịch vẫn nằm trong `sepay_payment_log`).

## 4. Cập nhật số giữa các thu ngân (độ trễ)

- Đơn mới / thanh toán / hủy: phiên bản thay đổi của POS Profile tăng (đã có, `realtime.md`) → khóa cache đổi → số mới.
- Nhập / xuất kho ngoài POS: doc_events `Stock Ledger Entry.on_submit` → `change_feed.on_stock_ledger_change` gom kho theo giao dịch, SAU
  commit tăng phiên bản mọi POS Profile đang bật dùng kho đó (rollback → bỏ; lỗi chỉ Error Log). Bin đổi bằng `frappe.db.set_value` (không có
  doc_events) nên móc vào Stock Ledger Entry. Không sửa core.
- Màn Bán món nạp tồn qua `usePosRefresh` (`SERVE_POLL_MS = 15000`). Độ trễ tối đa để số trên /pos phản ánh tồn mới:
  | Nguồn thay đổi | Socket realtime đang kết nối | Không socket (poll) |
  |---|---|---|
  | Đơn / thanh toán / hủy; Stock Entry, Purchase Receipt, Stock Reconciliation, gộp đơn đóng ca (có Stock Ledger Entry) | ~1–2 giây | ≤ 15 giây (+1 request) |
  | Thay đổi KHÔNG qua Stock Ledger Entry (Stock Reservation Entry đổi `reserved_stock`, repost / sửa Bin bằng SQL) | ≤ 60 + 15 = 75 giây | ≤ 75 giây |
  (60 giây = lưới an toàn của `usePosRefresh`; 15 giây = TTL cache cùng phiên bản.) Lỗi tạo đơn → nạp lại tồn ngay.
- Hai POS Profile dùng chung một kho: đơn của profile này không tăng phiên bản profile kia → profile kia trễ tối đa 75 giây (bình thường mỗi
  chi nhánh một profile, một kho). Server vẫn chặn đúng.

## 5. Cờ "Gửi pha chế"

- `MYPOS Menu Item.send_to_kitchen` (Check, mặc định 1). Món có size: đặt trên template, áp cho mọi size. Migrate thêm cột mặc định 1 →
  món cũ = 1.
- Ghi lên dòng hóa đơn lúc tạo đơn: `POS Invoice Item.custom_send_to_kitchen` (Custom Field, chỉ đọc, mặc định 1; `MYPOSMenu.kitchen_flags`
  theo Price List của đơn; không có dòng menu → 1). Sửa menu sau đó không đổi đơn cũ. Dòng trống (đơn trước 0.3.0) = cần pha chế.
- Đơn **chỉ** gồm món cờ 0: `on_submit` đặt "Đã làm" + `kitchen_done_at` ngay khi thanh toán → cột "Sẵn sàng nhận món" ở /pos/status, hàng
  chờ thu ngân (toast); thu ngân / quản lý bấm "Đã hoàn thành" ở tab "Đã làm" của màn Pha chế như luồng chuẩn. Đơn QR → khách nhận banner
  "mời đến quầy nhận". Pha chế (chỉ POS KITCHEN) **không thấy** đơn này ở mọi tab.
- Đơn hỗn hợp: "Chưa làm" như cũ; màn Pha chế chỉ vẽ món cờ 1, dòng "Lấy tại quầy: …" cho món cờ 0; tem ly / phiếu pha chế chỉ món cờ 1
  (phiếu ghi thêm "Lấy tại quầy: …"); đơn hoàn thành theo bước của bếp. Hóa đơn khách, /pos/status, Orders: mọi món.
- Dữ liệu demo: `tools/seed` (`config.MENU_NO_KITCHEN`, bước `s19_mypos_menu`, idempotent) — không đặt trong app (white-label).

## 6. Hướng B (chưa làm, để Van quyết)

Bán thẳng bằng Sales Invoice (`update_stock = 1`): tồn sổ đúng tức thì. Chi phí lớn (chuyển mọi Custom Field / doc_events, đóng ca, webhook,
~mọi selfcheck), rủi ro cao (hủy đơn đã thu tiền phải đảo GL + kho; trái nguyên tắc giữ POS Invoice). [Inference] vài ngày công.

## 7. Tồn đọng / rủi ro

- Giỏ chỉ giới hạn theo từng mã; hai đồ uống khác nhau cùng dùng hết một nguyên liệu → server chặn khi tạo đơn ("Hết: …").
- [Inference] Deadlock với bút toán kho của ERPNext ít khả năng (cùng thứ tự khóa Bin); nếu có, người dùng thấy lỗi và thử lại.
- [Unverified] Cột `item_code` của `POS Invoice Item` có index (giảm số dòng bị khóa chia sẻ khi chặn bán).
- Số bundle là ước tính theo định mức; không trừ hao hụt.
