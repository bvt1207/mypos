def check_bundle_capacity():
	"""(f) Số ly tối đa của món có Product Bundle = min theo nguyên liệu, tính riêng từng size (mypos 0.3.0, docs/rules/stock.md §2).

	    bench --site <site> execute mypos.selfcheck.bundle_capacity_check.check_bundle_capacity

	Quán thử ZZTEST-* + tồn tạm. Mỗi size là một mã riêng có bundle riêng (như S / M / L thật):
	  S = 1 ING + 1 ICE, M = 2 ING + 1 ICE; ING = 5, ICE = 4.
	  F1 S = min(5/1, 4/1) = 4; M = min(5/2, 4/1) = 2 (làm tròn xuống).
	  F2 ICE = 0 -> S = 0, M = 0, short = [tên ICE] ("Hết: ...").
	  F3 tạo đơn S x1 khi ICE = 0 -> từ chối, thông báo có "Hết:" + tên ICE.
	  F4 ICE = 4, ING = 1 -> S = 1, M = 0, M.short = [ING]; tạo đơn M x1 -> từ chối; S x1 -> được.
	  F5 số độc lập từng món (không cộng dồn): sau nháp S x1, M vẫn = 0 vì ING khả dụng 0, S = 0.
	  F6 bundle tắt (disabled) -> món không còn số.
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
		fx = zz_stock_fixture(shop, stock={"ING": {"qty": 5}, "ICE": {"qty": 4}},
			bundles={"S": {"items": {"ING": 1, "ICE": 1}}, "M": {"items": {"ING": 2, "ICE": 1}}})
		wh, c = fx["warehouse"], fx["codes"]
		ice_name = frappe.db.get_value("Item", c["ICE"], "item_name")
		ing_name = frappe.db.get_value("Item", c["ING"], "item_name")

		def snap():
			return SA.stock_snapshot([c["S"], c["M"]], wh)

		def try_create(code):
			try:
				r = P.create_draft_order([{"item_code": code, "qty": 1, "options": []}], "Take Away",
					mode_of_payment=shop["mop_cash"], branch_name=shop["branch"], price_list=shop["price_list"])
				frappe.db.commit()
				return r["invoice_name"], None
			except Exception as e:  # noqa: BLE001
				frappe.db.rollback()
				frappe.clear_messages()
				return None, e

		s = snap()
		ok("F1 S = 4, M = 2", s[c["S"]]["qty"] == 4 and s[c["M"]]["qty"] == 2 and s[c["S"]]["kind"] == "bundle", str(s))

		zz_stock_fixture(shop, "qty", set_qty={"ICE": 0})
		s = snap()
		ok("F2 ICE = 0 -> S = M = 0, Hết: ICE", s[c["S"]]["qty"] == 0 and s[c["M"]]["qty"] == 0
			and s[c["S"]]["short"] == [ice_name] and s[c["M"]]["short"] == [ice_name], str(s))
		_n, err = try_create(c["S"])
		ok("F3 đơn S khi hết ICE bị từ chối (Hết: ICE)", isinstance(err, SA.MyposStockError) and "Hết:" in str(err) and ice_name in str(err),
			str(err)[:160] if err else "KHÔNG bị chặn")

		zz_stock_fixture(shop, "qty", set_qty={"ICE": 4, "ING": 1})
		s = snap()
		ok("F4 ING = 1 -> S = 1, M = 0 (Hết: ING)", s[c["S"]]["qty"] == 1 and s[c["M"]]["qty"] == 0 and s[c["M"]]["short"] == [ing_name], str(s))
		_n, err = try_create(c["M"])
		ok("F4 đơn M bị từ chối", isinstance(err, SA.MyposStockError), str(err)[:120] if err else "KHÔNG bị chặn")
		made, err = try_create(c["S"])
		ok("F4 đơn S x1 được", bool(made), str(err)[:120] if err else "")

		s = snap()
		ok("F5 sau nháp S: S = 0, M = 0 (ING khả dụng 0)", s[c["S"]]["qty"] == 0 and s[c["M"]]["qty"] == 0, str(s))

		frappe.db.set_value("Product Bundle", {"new_item_code": c["M"]}, "disabled", 1)
		frappe.db.commit()
		ok("F6 bundle tắt -> M không còn số", c["M"] not in snap())
	finally:
		frappe.db.rollback()
		zz_test_shop("drop", shop["tag"])  # đơn trước (ERPNext chặn xóa Product Bundle còn đơn đã submit), rồi bundle / Item
		zz_stock_fixture(shop, "drop")  # Bin còn sót (nếu có)

	print(f"KẾT QUẢ: {sum(results)}/{len(results)} PASS")
	return all(results)
