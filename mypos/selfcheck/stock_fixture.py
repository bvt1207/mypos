def zz_stock_fixture(shop, action="create", stock=None, bundles=None, flags=None, set_qty=None):
	"""Dữ liệu tồn tạm cho selfcheck tồn kho / Gửi pha chế (mypos 0.3.0), dựng TRÊN quán thử của test_shop.zz_test_shop.

	action = "create":
	  stock   = {"STK": {"qty": 5, "menu": 1, "kitchen": 0}, ...}  -> Item quản lý tồn "<tag>-STK" (giá 10.000) + Bin ở kho
	            của POS Profile thử với actual_qty = qty (ghi thẳng Bin, KHÔNG tạo Stock Entry / sổ kho / bút toán: không đụng
	            sổ kho thật; món thử tên riêng ZZTEST nên không lẫn tồn món thật). menu=1 -> thêm vào MYPOS Menu thử.
	  bundles = {"DRK": {"items": {"ING": 2, "ICE": 1}, "kitchen": 1}} -> Item không tồn "<tag>-DRK" + Product Bundle đang bật
	            (nguyên liệu = mã trong `stock`), thêm vào menu.
	  flags   = {"CF": 0, "TS": 1} -> cờ "Gửi pha chế" của dòng menu có sẵn của quán thử ("<tag>-CF"...).
	  Commit. Trả {"warehouse", "codes": {suffix: item_code}}.
	action = "qty": set_qty = {"STK": 0} -> đặt lại Bin.actual_qty, commit.
	action = "drop": xóa Product Bundle + Bin của mọi mã "<tag>-*" (Item / giá / menu / đơn do zz_test_shop("drop") xóa sau).
	  Thử lại 3 lần khi QueryDeadlockError, commit từng bước; còn sót -> in WARN.
	"""
	import time

	import frappe

	PRICE = 10000
	DEADLOCK = getattr(frappe, "QueryDeadlockError", None) or getattr(frappe.exceptions, "QueryDeadlockError", ())
	tag = shop["tag"]
	warehouse = frappe.db.get_value("POS Profile", shop["pos_profile"], "warehouse")

	def code_of(suffix):
		return f"{tag}-{suffix}"

	def retry(fn):
		for attempt in range(3):
			try:
				fn()
				frappe.db.commit()
				return True
			except DEADLOCK:
				frappe.db.rollback()
				if attempt == 2:
					raise
				time.sleep(1)
		return False

	def set_bin(code, qty):
		from erpnext.stock.utils import get_or_make_bin

		name = get_or_make_bin(code, warehouse)
		frappe.db.set_value("Bin", name, {"actual_qty": qty, "reserved_stock": 0}, update_modified=False)

	if action == "qty":
		for suffix, qty in (set_qty or {}).items():
			set_bin(code_of(suffix), qty)
		frappe.db.commit()
		return {"warehouse": warehouse}

	if action == "drop":
		failed = []
		for name in frappe.get_all("Product Bundle", filters={"new_item_code": ["like", f"{tag}-%"]}, pluck="name"):
			try:
				retry(lambda n=name: frappe.delete_doc("Product Bundle", n, force=1, ignore_permissions=True))
			except Exception as e:  # noqa: BLE001
				frappe.db.rollback()
				failed.append(f"Product Bundle {name}: {str(e)[:100]}")
		try:
			retry(lambda: frappe.db.sql("delete from `tabBin` where item_code like %s", (f"{tag}-%",)))
		except Exception as e:  # noqa: BLE001
			frappe.db.rollback()
			failed.append(f"Bin {tag}-*: {str(e)[:100]}")
		for f in failed:
			print(f"WARN dọn tồn thử chưa hết: {f}")
		return failed

	frappe.set_user("Administrator")
	currency = frappe.db.get_value("Price List", shop["price_list"], "currency")
	codes = {}
	menu = frappe.get_doc("MYPOS Menu", shop["menu"])

	def add_item(suffix, is_stock):
		code = code_of(suffix)
		frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": f"{tag} {suffix}", "item_group": shop["item_group"],
			"stock_uom": "Nos", "is_stock_item": is_stock, "is_sales_item": 1,
			"include_item_in_manufacturing": 0}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Item Price", "item_code": code, "price_list": shop["price_list"], "price_list_rate": PRICE,
			"uom": "Nos", "selling": 1, "currency": currency}).insert(ignore_permissions=True)
		codes[suffix] = code
		return code

	for suffix, spec in (stock or {}).items():
		code = add_item(suffix, 1)
		set_bin(code, spec.get("qty", 0))
		if spec.get("menu"):
			menu.append("items", {"item_code": code, "send_to_kitchen": spec.get("kitchen", 1)})
	for suffix, spec in (bundles or {}).items():
		code = add_item(suffix, 0)
		frappe.get_doc({"doctype": "Product Bundle", "new_item_code": code,
			"items": [{"item_code": code_of(c), "qty": q} for c, q in spec["items"].items()]}).insert(ignore_permissions=True)
		menu.append("items", {"item_code": code, "send_to_kitchen": spec.get("kitchen", 1)})
	for row in menu.items:
		for suffix, value in (flags or {}).items():
			if row.item_code == code_of(suffix):
				row.send_to_kitchen = value
	menu.save(ignore_permissions=True)
	frappe.db.commit()
	return {"warehouse": warehouse, "codes": codes}
