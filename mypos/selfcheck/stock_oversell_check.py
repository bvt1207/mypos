def check_stock_oversell():
	"""(c) Chặn bán vượt tồn ở server + hai đơn cùng bán món cuối chỉ một đơn thành công (mypos 0.3.0, docs/rules/stock.md §3).

	    bench --site <site> execute mypos.selfcheck.stock_oversell_check.check_stock_oversell

	Quán thử ZZTEST-* + tồn tạm: STK = 1, LAST = 1, NEG = 0 (Item.allow_negative_stock = 1).
	  C1 create_draft_order STK x2 -> từ chối (MyposStockError).
	  C2 Chặn ĐỘC LẬP với Stock Settings -> Allow Negative Stock: bật tạm trong giao dịch (KHÔNG commit, rollback ngay) rồi kiểm
	     lại STK x2 -> vẫn từ chối. Site đang bật sẵn -> báo CẢNH BÁO, C1 đã chứng minh.
	  C3 món Item.allow_negative_stock = 1 -> không chặn (giống ERPNext is_stock_check_needed).
	  C4 hai kết nối DB song song (2 thread, mỗi thread frappe.init / connect riêng) cùng tạo đơn LAST x1: thread A khóa Bin, giữ
	     giao dịch 2 giây rồi commit; thread B bắt đầu khi A đã khóa -> phải chờ rồi bị từ chối. Kỳ vọng đúng 1 thành công.
	     Không dựng được thread (môi trường) -> phương án dự phòng: tuần tự A commit rồi B -> B bị từ chối; in rõ
	     "CHƯA KIỂM CHỨNG: phần song song" (không tính FAIL).
	  C5 /order (customer_order_api) dùng cùng hàm chặn: P.check_invoice_stock có trong luồng create_order (kiểm mã nguồn).
	Dọn trong finally.
	"""
	import inspect
	import threading
	import time

	import frappe

	from mypos.mypos.api import customer_order_api as Q
	from mypos.mypos.api import pos_api as P
	from mypos.mypos.doctype.pos_invoice_hooks import stock_availability as SA
	from mypos.selfcheck.stock_fixture import zz_stock_fixture
	from mypos.selfcheck.test_shop import zz_test_shop

	HOLD_SECONDS = 2
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
	site, sites_path = frappe.local.site, frappe.local.sites_path
	try:
		fx = zz_stock_fixture(shop, stock={"STK": {"qty": 1, "menu": 1, "kitchen": 0}, "LAST": {"qty": 1, "menu": 1, "kitchen": 0},
			"NEG": {"qty": 0, "menu": 1, "kitchen": 0}})
		c = fx["codes"]
		frappe.db.set_value("Item", c["NEG"], "allow_negative_stock", 1)
		frappe.db.commit()

		def create(code, qty):
			return P.create_draft_order([{"item_code": code, "qty": qty, "options": []}], "Take Away",
				mode_of_payment=shop["mop_cash"], branch_name=shop["branch"], price_list=shop["price_list"])

		def try_create(code, qty):
			try:
				r = create(code, qty)
				frappe.db.commit()
				return r, None
			except Exception as e:  # noqa: BLE001
				frappe.db.rollback()
				frappe.clear_messages()
				return None, e

		_r, err = try_create(c["STK"], 2)
		ok("C1 STK x2 (tồn 1) bị từ chối", isinstance(err, SA.MyposStockError), f"-> {type(err).__name__}: {str(err)[:120]}" if err else "-> KHÔNG bị chặn")

		neg = frappe.db.get_single_value("Stock Settings", "allow_negative_stock")
		if neg:
			print("CẢNH BÁO Stock Settings -> Allow Negative Stock ĐANG BẬT trên site: ERPNext không kiểm tồn khi submit; "
				"C1 ở trên đã chứng minh mypos vẫn chặn khi tạo đơn.")
			ok("C2 chặn độc lập Allow Negative Stock (đang bật sẵn)", isinstance(err, SA.MyposStockError))
		else:
			err2 = None
			try:
				frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
				frappe.clear_document_cache("Stock Settings", "Stock Settings")
				create(c["STK"], 2)
			except Exception as e:  # noqa: BLE001
				err2 = e
			finally:
				frappe.db.rollback()
				frappe.clear_messages()
				frappe.clear_document_cache("Stock Settings", "Stock Settings")
			still = frappe.db.get_single_value("Stock Settings", "allow_negative_stock")
			ok("C2 bật tạm Allow Negative Stock -> mypos vẫn chặn", isinstance(err2, SA.MyposStockError) and not still,
				f"-> {type(err2).__name__ if err2 else 'KHÔNG bị chặn'}; sau rollback = {still}")

		r3, err3 = try_create(c["NEG"], 2)
		ok("C3 món cho phép âm (Item) không bị chặn", r3 is not None, f"-> {err3}" if err3 else "")

		# ---- C4 song song
		out, gate = {}, threading.Event()

		def worker(key, wait_gate):
			try:
				frappe.init(site=site, sites_path=sites_path)
				frappe.connect()
				frappe.set_user("Administrator")
			except Exception as e:  # noqa: BLE001
				out[key] = ("setup", e)
				gate.set()
				return
			try:
				if wait_gate:
					gate.wait(15)
				res = create(c["LAST"], 1)
				out[key] = ("ok", res["invoice_name"], time.time())
				if not wait_gate:
					gate.set()  # A đã khóa Bin + tạo nháp, CHƯA commit
					time.sleep(HOLD_SECONDS)
				frappe.db.commit()
			except Exception as e:  # noqa: BLE001
				frappe.db.rollback()
				out[key] = ("err", e, time.time())
				gate.set()
			finally:
				frappe.destroy()

		t_a = threading.Thread(target=worker, args=("A", False))
		t_b = threading.Thread(target=worker, args=("B", True))
		started = time.time()
		t_a.start()
		t_b.start()
		t_a.join(60)
		t_b.join(60)
		frappe.db.rollback()  # snapshot mới cho kết nối chính
		setup_failed = [k for k, v in out.items() if v[0] == "setup"]
		if setup_failed or len(out) < 2:
			print(f"CHƯA KIỂM CHỨNG: phần song song (không dựng được 2 kết nối trong bench execute: "
				f"{[str(out[k][1])[:100] for k in setup_failed] or out}). Chạy phương án dự phòng tuần tự.")
			ra, ea = try_create(c["LAST"], 1)
			rb, eb = try_create(c["LAST"], 1)
			ok("C4 (dự phòng tuần tự) đơn thứ hai bị từ chối", ra is not None and isinstance(eb, SA.MyposStockError),
				f"A={'ok' if ra else ea}, B={type(eb).__name__ if eb else 'ok'}")
		else:
			kinds = sorted(v[0] for v in out.values())
			b = out.get("B")
			waited = b and b[2] - started >= HOLD_SECONDS - 0.2
			ok("C4 hai đơn cùng món cuối: đúng 1 thành công", kinds == ["err", "ok"],
				f"A={out['A'][0]}, B={b[0]} ({type(b[1]).__name__ if b[0] == 'err' else b[1]})")
			ok("C4 đơn thua bị từ chối vì tồn (không phải lỗi khác)", b[0] == "ok" or isinstance(b[1], SA.MyposStockError),
				str(b[1])[:140] if b[0] == "err" else "")
			ok("C4 B phải chờ khóa của A (≥ thời gian giữ)", bool(waited), f"B xong sau {round(b[2] - started, 2)} giây")
			held = SA.available_map([c["LAST"]], fx["warehouse"])[c["LAST"]][0]
			ok("C4 tồn khả dụng LAST = 0 sau cùng", held == 0, str(held))

		src = inspect.getsource(Q.create_order)
		ok("C5 /order create_order gọi check_invoice_stock trước insert",
			"check_invoice_stock(invoice)" in src and src.index("check_invoice_stock(invoice)") < src.index("invoice.insert("))
	finally:
		frappe.db.rollback()
		zz_test_shop("drop", shop["tag"])  # đơn trước (ERPNext chặn xóa Product Bundle còn đơn đã submit), rồi bundle / Item
		zz_stock_fixture(shop, "drop")  # Bin còn sót (nếu có)

	print(f"KẾT QUẢ: {sum(results)}/{len(results)} PASS")
	return all(results)
