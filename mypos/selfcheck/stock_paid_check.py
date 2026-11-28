def check_stock_paid():
	"""(a) Tồn khả dụng đúng sau khi thanh toán (mypos 0.3.0, docs/rules/stock.md).

	    bench --site <site> execute mypos.selfcheck.stock_paid_check.check_stock_paid

	Quán thử ZZTEST-* (test_shop) + tồn tạm (stock_fixture): STK quản lý tồn = 5; bundle DRK = 2 ING + 1 ICE, ING = 10, ICE = 3.
	  A1 số ban đầu: STK 5 (kind stock), DRK 3 (= min(10/2, 3/1), kind bundle); món không quản lý tồn không có số.
	  A2 khớp ERPNext: tồn (không tính đơn nháp) = erpnext pos_invoice.get_available_stock_qty.
	  A3 thanh toán STK x2 -> STK 3; ERPNext cũng 3 (đã bán chưa gộp).
	  A4 thanh toán DRK x1 -> ING 8, ICE 2 -> DRK 2; Packed Item được ERPNext tính.
	  A5 nhập / xuất kho (Stock Ledger Entry) -> phiên bản thay đổi của POS Profile dùng kho tăng (change_feed.on_stock_ledger_change).
	  A6 get_stock_availability trả cùng số (khi POS Profile của Administrator là quán thử; khác -> bỏ qua có ghi chú).
	Báo trạng thái Stock Settings -> Allow Negative Stock (mypos chặn độc lập; ERPNext bỏ qua kiểm tra khi bật).
	Dọn trong finally (stock_fixture drop -> zz_test_shop drop, thử lại khi deadlock).
	"""
	import frappe
	from frappe.utils import flt

	from mypos.mypos.api import pos_api as P
	from mypos.mypos.doctype.pos_invoice_hooks import change_feed
	from mypos.mypos.doctype.pos_invoice_hooks import stock_availability as SA
	from mypos.selfcheck.stock_fixture import zz_stock_fixture
	from mypos.selfcheck.test_shop import zz_test_shop

	results = []

	def ok(case, cond, extra=""):
		results.append(bool(cond))
		print(f"{'PASS' if cond else 'FAIL'} {case} {extra}")

	frappe.set_user("Administrator")
	shop = zz_test_shop("create")
	if not shop.get("ok"):
		print(f"SKIP: {shop.get('reason')}")
		print("KẾT QUẢ: SKIP")
		return None
	try:
		neg = frappe.db.get_single_value("Stock Settings", "allow_negative_stock")
		print(f"INFO Stock Settings -> Allow Negative Stock = {neg}"
			+ (" (ĐANG BẬT: ERPNext bỏ qua kiểm tra tồn khi submit; mypos vẫn tự chặn khi tạo đơn)" if neg else ""))
		fx = zz_stock_fixture(shop, stock={"STK": {"qty": 5, "menu": 1, "kitchen": 0}, "ING": {"qty": 10}, "ICE": {"qty": 3}},
			bundles={"DRK": {"items": {"ING": 2, "ICE": 1}, "kitchen": 1}})
		wh, c = fx["warehouse"], fx["codes"]
		from mypos.mypos.doctype.mypos_menu.mypos_menu import MYPOSMenu

		menu_codes = MYPOSMenu.menu_item_codes(shop["price_list"])

		snap = SA.stock_snapshot(menu_codes, wh)
		ok("A1 STK = 5 (stock)", snap.get(c["STK"], {}).get("qty") == 5 and snap[c["STK"]]["kind"] == "stock", str(snap.get(c["STK"])))
		ok("A1 DRK = 3 (bundle)", snap.get(c["DRK"], {}).get("qty") == 3 and snap[c["DRK"]]["kind"] == "bundle", str(snap.get(c["DRK"])))
		ok("A1 món không quản lý tồn không có số", shop["item"] not in snap)

		def erp(code):
			from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_available_stock_qty

			return flt(get_available_stock_qty({(code, wh): 1})[0][(code, wh)])

		def ours(code):
			return flt(SA.available_map([code], wh, include_drafts=False)[code][0])

		ok("A2 khớp ERPNext (STK, ING, ICE)", all(erp(c[k]) == ours(c[k]) for k in ("STK", "ING", "ICE")),
			str({k: (erp(c[k]), ours(c[k])) for k in ("STK", "ING", "ICE")}))

		def sell(code, qty):
			r = P.create_draft_order([{"item_code": code, "qty": qty, "options": []}], "Take Away",
				mode_of_payment=shop["mop_cash"], branch_name=shop["branch"], price_list=shop["price_list"])
			P.pay_cash(r["invoice_name"])
			frappe.db.commit()
			return r["invoice_name"]

		sell(c["STK"], 2)
		snap = SA.stock_snapshot(menu_codes, wh)
		ok("A3 thanh toán STK x2 -> 3", snap[c["STK"]]["qty"] == 3 and erp(c["STK"]) == 3, f"mypos {snap[c['STK']]['qty']}, erpnext {erp(c['STK'])}")

		inv = sell(c["DRK"], 1)
		packed = frappe.get_all("Packed Item", filters={"parent": inv}, fields=["item_code", "qty"])
		snap = SA.stock_snapshot(menu_codes, wh)
		ok("A4 thanh toán DRK x1 -> ING 8, ICE 2, DRK 2",
			erp(c["ING"]) == 8 and erp(c["ICE"]) == 2 and snap[c["DRK"]]["qty"] == 2,
			f"ING {erp(c['ING'])}, ICE {erp(c['ICE'])}, DRK {snap[c['DRK']]['qty']}, packed {[(p.item_code, p.qty) for p in packed]}")

		before = int(change_feed.get_version(shop["pos_profile"]))
		frappe.flags.mypos_chg_immediate = True
		try:
			change_feed.on_stock_ledger_change(frappe._dict(warehouse=wh))
		finally:
			frappe.flags.mypos_chg_immediate = False
		ok("A5 sổ kho đổi -> phiên bản POS Profile tăng", int(change_feed.get_version(shop["pos_profile"])) > before)

		try:
			own = P._get_pos_profile().name
		except Exception:  # noqa: BLE001
			own = None
		if own == shop["pos_profile"]:
			api = P.get_stock_availability()
			ok("A6 get_stock_availability", api["items"].get(c["STK"], {}).get("qty") == 3 and api["warehouse"] == wh)
		else:
			print(f"INFO A6 bỏ qua: POS Profile của Administrator là {own!r}, không phải quán thử (A1-A4 đã kiểm cùng hàm)")
	finally:
		frappe.db.rollback()
		frappe.flags.mypos_chg_immediate = False
		zz_test_shop("drop", shop["tag"])  # đơn trước (ERPNext chặn xóa Product Bundle còn đơn đã submit), rồi bundle / Item
		zz_stock_fixture(shop, "drop")  # Bin còn sót (nếu có)

	print(f"KẾT QUẢ: {sum(results)}/{len(results)} PASS")
	return all(results)
