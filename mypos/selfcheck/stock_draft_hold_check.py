def check_stock_draft_hold():
	"""(b) Đơn nháp giữ chỗ tồn; hủy thì trả lại (mypos 0.3.0, docs/rules/stock.md §1).

	    bench --site <site> execute mypos.selfcheck.stock_draft_hold_check.check_stock_draft_hold

	Quán thử ZZTEST-* + tồn tạm: STK = 3, bundle DRK = 1 ING (ING = 4).
	  B1 nháp STK x2 -> tồn khả dụng STK = 1 (ERPNext không trừ nháp: vẫn 3).
	  B2 nháp thứ hai STK x2 -> bị từ chối (MyposStockError, "chỉ còn 1").
	  B3 hủy nháp đầu (cancel_draft_order, có lý do) -> STK = 3; tạo lại nháp STK x2 được.
	  B4 nháp DRK x3 -> ING khả dụng 1 (bundle tách ra nguyên liệu) -> DRK = 1.
	  B5 hủy nháp DRK -> ING 4, DRK 4.
	Dọn trong finally.
	"""
	import frappe

	from mypos.mypos.api import pos_api as P
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
		fx = zz_stock_fixture(shop, stock={"STK": {"qty": 3, "menu": 1, "kitchen": 0}, "ING": {"qty": 4}},
			bundles={"DRK": {"items": {"ING": 1}}})
		wh, c = fx["warehouse"], fx["codes"]

		def draft(code, qty):
			r = P.create_draft_order([{"item_code": code, "qty": qty, "options": []}], "Take Away",
				mode_of_payment=shop["mop_cash"], branch_name=shop["branch"], price_list=shop["price_list"])
			frappe.db.commit()
			return r["invoice_name"]

		def avail(code):
			return SA.available_map([code], wh)[code][0]

		def snap(code):
			return SA.stock_snapshot([code], wh).get(code, {}).get("qty")

		first = draft(c["STK"], 2)
		no_drafts = SA.available_map([c["STK"]], wh, include_drafts=False)[c["STK"]][0]
		ok("B1 nháp STK x2 -> khả dụng 1", avail(c["STK"]) == 1 and no_drafts == 3, f"mypos {avail(c['STK'])}, không tính nháp {no_drafts}")

		err = None
		try:
			draft(c["STK"], 2)
		except Exception as e:  # noqa: BLE001
			frappe.db.rollback()
			frappe.clear_messages()
			err = e
		ok("B2 nháp thứ hai STK x2 bị từ chối", isinstance(err, SA.MyposStockError) and "chỉ còn 1" in str(err),
			f"-> {type(err).__name__}: {str(err)[:140]}" if err else "-> KHÔNG bị chặn")

		P.cancel_draft_order(first, reason="Khách đổi ý")
		frappe.db.commit()
		ok("B3 hủy nháp -> trả chỗ (3)", avail(c["STK"]) == 3, str(avail(c["STK"])))
		again = None
		try:
			again = draft(c["STK"], 2)
		except Exception as e:  # noqa: BLE001
			frappe.db.rollback()
			frappe.clear_messages()
			print(f"   lỗi: {e}")
		ok("B3 tạo lại nháp STK x2 được", bool(again))

		drk = draft(c["DRK"], 3)
		ok("B4 nháp DRK x3 -> ING 1, DRK 1", avail(c["ING"]) == 1 and snap(c["DRK"]) == 1, f"ING {avail(c['ING'])}, DRK {snap(c['DRK'])}")
		P.cancel_draft_order(drk, reason="Khách đổi ý")
		frappe.db.commit()
		ok("B5 hủy nháp DRK -> ING 4, DRK 4", avail(c["ING"]) == 4 and snap(c["DRK"]) == 4, f"ING {avail(c['ING'])}, DRK {snap(c['DRK'])}")
	finally:
		frappe.db.rollback()
		zz_test_shop("drop", shop["tag"])  # đơn trước (ERPNext chặn xóa Product Bundle còn đơn đã submit), rồi bundle / Item
		zz_stock_fixture(shop, "drop")  # Bin còn sót (nếu có)

	print(f"KẾT QUẢ: {sum(results)}/{len(results)} PASS")
	return all(results)
