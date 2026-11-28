def check_kitchen_flag():
	"""(d) Món cờ "Gửi pha chế" = 0: không xuất hiện ở bếp; đơn chỉ gồm món này nhảy thẳng "Sẵn sàng" (mypos 0.3.0).

	    bench --site <site> execute mypos.selfcheck.kitchen_flag_check.check_kitchen_flag

	Quán thử ZZTEST-*: dòng menu CF đặt cờ = 0; user tạm "zz.test.kflag.k@example.com" (POS KITCHEN, thuộc Applicable for Users).
	  D1 menu payload trả send_to_kitchen = 0 cho CF, 1 cho TS; dòng hóa đơn ghi custom_send_to_kitchen = 0.
	  D2 đơn chỉ CF, thanh toán -> custom_kitchen_status = "Đã làm" + kitchen_done_at (không qua "Chưa làm").
	  D3 /pos/status: đơn ở cột "Sẵn sàng nhận món" (ready), không ở "Đang chuẩn bị".
	  D4 thu ngân / quản lý: thấy ở tab "Đã làm" (để giao + bấm "Đã hoàn thành"); món có send_to_kitchen = 0.
	  D5 pha chế (POS KITCHEN): không thấy đơn ở mọi tab được phép, kể cả sau khi "Đã hoàn thành".
	  D6 đơn cũ / dòng không có cờ (custom_send_to_kitchen trống) vẫn coi là cần pha chế (has_kitchen_items).
	Dọn trong finally (user tạm -> quán thử).
	"""
	import time

	import frappe

	from mypos.mypos.api import order_status_api as S
	from mypos.mypos.api import pos_api as P
	from mypos.mypos.doctype.mypos_menu.mypos_menu import MYPOSMenu
	from mypos.mypos.doctype.pos_invoice_hooks.pos_invoice_hooks import has_kitchen_items
	from mypos.selfcheck.stock_fixture import zz_stock_fixture
	from mypos.selfcheck.test_shop import zz_test_shop

	KITCHEN_USER = "zz.test.kflag.k@example.com"
	DEADLOCK = getattr(frappe, "QueryDeadlockError", None) or getattr(frappe.exceptions, "QueryDeadlockError", ())
	results = []

	def ok(case, cond, extra=""):
		results.append(bool(cond))
		print(f"{'PASS' if cond else 'FAIL'} {case} {extra}")

	def drop_user():
		for _attempt in range(3):
			try:
				frappe.db.rollback()
				for c in frappe.get_all("Contact", filters={"email_id": KITCHEN_USER}, pluck="name"):
					frappe.delete_doc("Contact", c, force=1, ignore_permissions=True)
				if frappe.db.exists("User", KITCHEN_USER):
					frappe.delete_doc("User", KITCHEN_USER, force=1, ignore_permissions=True)
				frappe.db.commit()
				return
			except DEADLOCK:
				frappe.db.rollback()
				time.sleep(1)
			except Exception as e:  # noqa: BLE001
				frappe.db.rollback()
				print(f"WARN không xóa được user tạm {KITCHEN_USER}: {str(e)[:120]}")
				return

	frappe.set_user("Administrator")
	drop_user()
	shop = zz_test_shop("create")
	if not shop.get("ok"):
		print(f"SKIP: {shop.get('reason')}")
		print("KẾT QUẢ: SKIP")
		return None
	try:
		zz_stock_fixture(shop, flags={"CF": 0, "TS": 1})
		frappe.get_doc({"doctype": "User", "email": KITCHEN_USER, "first_name": "ZZTEST KFLAG", "user_type": "System User",
			"send_welcome_email": 0, "enabled": 1, "roles": [{"role": "POS KITCHEN"}]}).insert(ignore_permissions=True)
		prof = frappe.get_doc("POS Profile", shop["pos_profile"])
		prof.append("applicable_for_users", {"user": KITCHEN_USER, "default": 1})
		prof.save(ignore_permissions=True)
		frappe.db.commit()

		payload = {i.get("sizes", [{}])[0].get("item_code"): i.get("send_to_kitchen")
			for m in MYPOSMenu.get_menu_with_items(price_list=shop["price_list"]) for i in m["items"]}
		ok("D1 menu payload: CF = 0, TS = 1", payload.get(shop["item"]) == 0 and payload.get(shop["item_options"]) == 1, str(payload))

		r = P.create_draft_order([{"item_code": shop["item"], "qty": 1, "options": []}], "Take Away",
			mode_of_payment=shop["mop_cash"], branch_name=shop["branch"], price_list=shop["price_list"])
		name = r["invoice_name"]
		flag = frappe.db.get_value("POS Invoice Item", {"parent": name}, "custom_send_to_kitchen")
		ok("D1 dòng hóa đơn ghi custom_send_to_kitchen = 0", flag == 0, str(flag))
		P.pay_cash(name)
		frappe.db.commit()
		st = frappe.db.get_value("POS Invoice", name, ["custom_kitchen_status", "kitchen_done_at"], as_dict=True)
		ok("D2 thanh toán -> Đã làm ngay (có kitchen_done_at)", st.custom_kitchen_status == "Đã làm" and st.kitchen_done_at, str(st))

		board = S.get_order_status_board(shop["pos_profile"])
		code = S.short_order_code(name)
		ok("D3 /pos/status: cột Sẵn sàng, không ở Đang chuẩn bị",
			any(x["code"] == code for x in board["ready"]) and not any(x["code"] == code for x in board["preparing"]),
			f"code {code}")

		frappe.set_user("Administrator")
		mgr = {o["name"]: o for o in P.get_kitchen_orders(status="Đã làm")}
		ok("D4 quản lý thấy ở tab Đã làm, món send_to_kitchen = 0",
			name in mgr and all(i["send_to_kitchen"] == 0 for i in mgr[name]["items"]))

		def kitchen_sees():
			frappe.set_user(KITCHEN_USER)
			try:
				return name in {o["name"] for o in P.get_kitchen_orders()}
			finally:
				frappe.set_user("Administrator")

		ok("D5 pha chế không thấy (đơn Đã làm)", not kitchen_sees())
		P.set_kitchen_status(name, "Đã hoàn thành")
		frappe.db.commit()
		ok("D5 pha chế không thấy (tab Đã hoàn thành)", not kitchen_sees())

		legacy = frappe._dict(items=[frappe._dict(item_code="X"), frappe._dict(item_code="Y", custom_send_to_kitchen=0)])
		ok("D6 dòng không có cờ = cần pha chế", has_kitchen_items(legacy) and not has_kitchen_items(
			frappe._dict(items=[frappe._dict(custom_send_to_kitchen=0)])))
	finally:
		frappe.db.rollback()
		frappe.set_user("Administrator")
		drop_user()
		zz_stock_fixture(shop, "drop")
		zz_test_shop("drop", shop["tag"])

	print(f"KẾT QUẢ: {sum(results)}/{len(results)} PASS")
	return all(results)
