def check_kitchen_mixed():
	"""(e) Đơn hỗn hợp: món pha chế + món lấy tại quầy (mypos 0.3.0, docs/rules/stock.md §5).

	    bench --site <site> execute mypos.selfcheck.kitchen_mixed_check.check_kitchen_mixed

	Quán thử ZZTEST-*: CF cờ "Gửi pha chế" = 0 (lấy tại quầy), TS = 1 (pha chế).
	  E1 đơn TS x1 + CF x2, thanh toán -> "Chưa làm" (có món pha chế).
	  E2 get_kitchen_orders (Chưa làm): đủ 2 món, cờ TS = 1, CF = 0 (màn Pha chế chỉ vẽ TS, CF ghi "Lấy tại quầy").
	  E3 get_order_detail: send_to_kitchen theo dòng (phiếu pha chế / tem ly chỉ in TS).
	  E4 pha chế "Đã làm" -> /pos/status cột Sẵn sàng; đơn hoàn thành theo bước của bếp ("Đã hoàn thành" bởi thu ngân / quản lý).
	  E5 đơn chỉ TS -> "Chưa làm" như trước (không đổi luồng cũ).
	Dọn trong finally.
	"""
	import frappe

	from mypos.mypos.api import order_status_api as S
	from mypos.mypos.api import pos_api as P
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
		zz_stock_fixture(shop, flags={"CF": 0, "TS": 1})
		cf, ts = shop["item"], shop["item_options"]

		def paid(lines):
			r = P.create_draft_order(lines, "Take Away", mode_of_payment=shop["mop_cash"], branch_name=shop["branch"],
				price_list=shop["price_list"])
			P.pay_cash(r["invoice_name"])
			frappe.db.commit()
			return r["invoice_name"]

		name = paid([{"item_code": ts, "qty": 1, "options": []}, {"item_code": cf, "qty": 2, "options": []}])
		status = frappe.db.get_value("POS Invoice", name, "custom_kitchen_status")
		ok("E1 đơn hỗn hợp -> Chưa làm", status == "Chưa làm", status)

		ko = {o["name"]: o for o in P.get_kitchen_orders(status="Chưa làm")}.get(name)
		flags = {i["item_code"]: i["send_to_kitchen"] for i in (ko or {}).get("items", [])}
		ok("E2 màn Pha chế: TS = 1, CF = 0", flags == {ts: 1, cf: 0}, str(flags))

		detail = P.get_order_detail(name)
		dflags = {i["item_code"]: i["send_to_kitchen"] for i in detail["items"]}
		ok("E3 chi tiết đơn: send_to_kitchen theo dòng", dflags == {ts: 1, cf: 0}, str(dflags))

		P.set_kitchen_status(name, "Đã làm")
		frappe.db.commit()
		board = S.get_order_status_board(shop["pos_profile"])
		code = S.short_order_code(name)
		ok("E4 bếp Đã làm -> /pos/status Sẵn sàng", any(x["code"] == code for x in board["ready"]))
		P.set_kitchen_status(name, "Đã hoàn thành")
		frappe.db.commit()
		ok("E4 Đã hoàn thành", frappe.db.get_value("POS Invoice", name, "custom_kitchen_status") == "Đã hoàn thành")

		only_ts = paid([{"item_code": ts, "qty": 1, "options": []}])
		ok("E5 đơn chỉ món pha chế -> Chưa làm", frappe.db.get_value("POS Invoice", only_ts, "custom_kitchen_status") == "Chưa làm")
	finally:
		frappe.db.rollback()
		zz_stock_fixture(shop, "drop")
		zz_test_shop("drop", shop["tag"])

	print(f"KẾT QUẢ: {sum(results)}/{len(results)} PASS")
	return all(results)
