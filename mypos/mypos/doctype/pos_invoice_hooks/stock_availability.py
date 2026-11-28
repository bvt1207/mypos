"""Tồn khả dụng theo kho của POS Profile + chặn bán vượt tồn khi tạo đơn (2026-11-29, `docs/rules/stock.md`).

Hướng A: không đổi cách ERPNext ghi sổ kho (vẫn trừ kho thật khi đóng ca / gộp đơn).

    khả dụng = Bin.actual_qty - Bin.reserved_stock                      (tồn sổ kho - giữ chỗ Stock Reservation Entry)
             - đã bán chưa gộp ("reserved for POS")                    (POS Invoice Item.stock_qty + Packed Item.qty,
                                                                         docstatus 1, consolidated_invoice trống)
             - giữ chỗ bởi đơn NHÁP                                     (POS Invoice docstatus 0, không phải trả hàng;
                                                                         món có bundle tách ra nguyên liệu)

Ba thành phần đầu theo ĐÚNG điều kiện của ERPNext 16.61 (`pos_invoice.get_available_stock_qty` /
`get_pos_reserved_qty_from_table`). Dùng lại trực tiếp `get_active_bundle_items`, `get_stock_item_details`,
`is_stock_check_needed`. Truy vấn tồn viết lại ở đây vì một lý do: khi chặn bán, phải đọc bằng LOCKING READ
(`FOR UPDATE` / `LOCK IN SHARE MODE`) — DB chạy REPEATABLE-READ, đọc thường dùng snapshot cũ và không thấy đơn nháp
mà thu ngân khác vừa commit. Selfcheck `stock_paid_check` so kết quả với hàm của ERPNext để bắt lệch.

Khác ERPNext có chủ đích:
- Không phụ thuộc Stock Settings -> Allow Negative Stock (ERPNext bỏ qua toàn bộ kiểm tra khi bật). mypos vẫn chặn;
  chỉ món có Item.allow_negative_stock = 1 (và không có Stock Reservation) mới không chặn — như `is_stock_check_needed`.
- Trừ thêm đơn nháp (ERPNext không tính).
"""

from collections import defaultdict
from math import floor

import frappe
from frappe import _
from frappe.utils import flt

CACHE_PREFIX = "mypos_stock:"
CACHE_SECONDS = 15
EPS = 1e-9


class MyposStockError(frappe.ValidationError):
	"""Không đủ tồn khả dụng để tạo đơn."""


def _pi():
	from erpnext.accounts.doctype.pos_invoice import pos_invoice

	return pos_invoice


def _lock_clause(lock):
	if lock == "update":
		return " FOR UPDATE"
	if lock == "share":
		return " LOCK IN SHARE MODE"
	return ""


# ---------------------------------------------------------------------------
# Các thành phần tồn (một kho)
# ---------------------------------------------------------------------------


def _bin_map(codes, warehouse, lock=None):
	"""{item_code: (actual_qty, reserved_stock)}; lock="update" khóa dòng Bin theo thứ tự tên (như ERPNext stock/utils)."""
	if not codes:
		return {}
	rows = frappe.db.sql(
		"""select name, item_code, actual_qty, reserved_stock from `tabBin`
		where warehouse = %(wh)s and item_code in %(codes)s order by name""" + _lock_clause(lock),
		{"wh": warehouse, "codes": tuple(codes)},
		as_dict=True,
	)
	return {r.item_code: (flt(r.actual_qty), flt(r.reserved_stock)) for r in rows}


def _pos_reserved_map(codes, warehouse, lock=None):
	"""Đã bán chưa gộp — cùng điều kiện ERPNext `get_pos_reserved_qty_from_table` (không lọc is_return, như ERPNext)."""
	result = defaultdict(float)
	if not codes:
		return result
	for table, column in (("POS Invoice Item", "stock_qty"), ("Packed Item", "qty")):
		rows = frappe.db.sql(
			f"""select c.item_code, sum(c.`{column}`) as qty
			from `tabPOS Invoice` p, `tab{table}` c
			where p.name = c.parent and ifnull(p.consolidated_invoice, '') = '' and c.docstatus = 1
				and c.item_code in %(codes)s and c.warehouse = %(wh)s
			group by c.item_code""" + _lock_clause(lock),
			{"codes": tuple(codes), "wh": warehouse},
			as_dict=True,
		)
		for r in rows:
			result[r.item_code] += flt(r.qty)
	return result


def _bundles_containing(codes):
	"""Mã bundle đang bật có nguyên liệu thuộc `codes` (định nghĩa bundle đọc thường)."""
	if not codes:
		return set()
	return {
		r[0]
		for r in frappe.db.sql(
			"""select distinct pb.new_item_code from `tabProduct Bundle` pb, `tabProduct Bundle Item` pbi
			where pbi.parent = pb.name and pb.disabled = 0 and pbi.item_code in %(codes)s""",
			{"codes": tuple(codes)},
		)
	}


def _draft_hold_map(codes, warehouse, lock=None, exclude=None):
	"""Giữ chỗ bởi đơn nháp (docstatus 0, không phải trả hàng) của kho, món có bundle tách ra nguyên liệu.

	Đơn bị hủy (tay / job tự hủy sau 2 giờ / QR hết hạn) chuyển docstatus 2 -> tự trả chỗ.
	"""
	result = defaultdict(float)
	if not codes:
		return result
	# Chỉ đọc dòng của chính các mã + các bundle có chứa chúng (lọc theo item_code -> khóa ít dòng khi chặn bán).
	candidates = set(codes) | _bundles_containing(codes)
	rows = frappe.db.sql(
		"""select c.item_code, sum(c.stock_qty) as qty
		from `tabPOS Invoice` p, `tabPOS Invoice Item` c
		where p.name = c.parent and c.parenttype = 'POS Invoice' and p.docstatus = 0 and ifnull(p.is_return, 0) = 0
			and c.item_code in %(codes)s and c.warehouse = %(wh)s and p.name != %(exclude)s
		group by c.item_code""" + _lock_clause(lock),
		{"codes": tuple(candidates), "wh": warehouse, "exclude": exclude or ""},
		as_dict=True,
	)
	if not rows:
		return result
	bundles = _pi().get_active_bundle_items({r.item_code for r in rows})
	wanted = set(codes)
	for r in rows:
		for comp in bundles.get(r.item_code) or [frappe._dict(item_code=r.item_code, qty=1)]:
			if comp.item_code in wanted:
				result[comp.item_code] += flt(comp.qty) * flt(r.qty)
	return result


def available_map(codes, warehouse, lock=None, exclude=None, include_drafts=True):
	"""{item_code: (khả dụng, có Stock Reservation hay không)} cho các mã trong một kho.

	lock=None: đọc thường (hiển thị). lock="update": khóa Bin FOR UPDATE + đọc khóa chia sẻ phần còn lại (chặn bán).
	"""
	codes = sorted({c for c in codes if c})
	bins = _bin_map(codes, warehouse, lock="update" if lock else None)
	reading = "share" if lock else None
	reserved = _pos_reserved_map(codes, warehouse, lock=reading)
	drafts = _draft_hold_map(codes, warehouse, lock=reading, exclude=exclude) if include_drafts else defaultdict(float)
	result = {}
	for code in codes:
		actual, sre = bins.get(code, (0.0, 0.0))
		result[code] = (actual - sre - reserved[code] - drafts[code], sre > 0)
	return result


# ---------------------------------------------------------------------------
# Hiển thị cho màn /pos và /order
# ---------------------------------------------------------------------------


def stock_snapshot(item_codes, warehouse):
	"""{item_code: {kind, qty, enforce, short}} cho các mã của menu (một lần cho cả menu).

	- kind "stock": món Maintain Stock = 1; qty = floor(khả dụng), không âm.
	- kind "bundle": món có Product Bundle đang bật; qty = min(floor(khả dụng nguyên liệu / định mức mỗi đơn vị)) theo
	  nguyên liệu cần kiểm; short = tên nguyên liệu đang thiếu cho 1 đơn vị. Ước tính ĐỘC LẬP từng món (không cộng dồn).
	- enforce 0: món cho phép âm (Item.allow_negative_stock) -> hiện số, không chặn.
	- Món không quản lý tồn và không có bundle (hoặc bundle không có nguyên liệu cần kiểm): không có trong kết quả.
	"""
	pi = _pi()
	codes = {c for c in item_codes if c}
	if not codes or not warehouse:
		return {}
	bundles = pi.get_active_bundle_items(codes)
	component_codes = {comp.item_code for comps in bundles.values() for comp in comps}
	details = pi.get_stock_item_details(codes | component_codes)
	stock_codes = {c for c in codes - set(bundles) if (details.get(c) or {}).get("is_stock_item")}
	avail = available_map(stock_codes | component_codes, warehouse)

	result = {}
	for code in stock_codes:
		qty, has_sre = avail[code]
		result[code] = {
			"kind": "stock",
			"qty": max(0, floor(qty + EPS)),
			"enforce": 1 if pi.is_stock_check_needed(details.get(code), has_sre) else 0,
			"short": [],
		}
	names = None
	for code, comps in bundles.items():
		checked = [c for c in comps if pi.is_stock_check_needed(details.get(c.item_code), avail[c.item_code][1])]
		if not checked:
			continue
		per_comp = {}
		for comp in checked:
			need = flt(comp.qty)
			per_comp[comp.item_code] = floor(avail[comp.item_code][0] / need + EPS) if need > 0 else 0
		qty = max(0, min(per_comp.values()))
		short_codes = [c for c, n in per_comp.items() if n < 1]
		if short_codes and names is None:
			names = _item_names(component_codes)
		result[code] = {
			"kind": "bundle",
			"qty": qty,
			"enforce": 1,
			"short": [names.get(c) or c for c in short_codes] if short_codes else [],
		}
	return result


def _item_names(codes):
	if not codes:
		return {}
	return {
		r.name: r.item_name
		for r in frappe.get_all("Item", filters={"name": ("in", list(codes))}, fields=["name", "item_name"])
	}


def cached_snapshot(item_codes, warehouse, cache_tag):
	"""stock_snapshot có cache Redis CACHE_SECONDS theo (kho, cache_tag). cache_tag gồm phiên bản thay đổi của POS Profile
	-> đơn mới / thanh toán / hủy / nhập xuất kho (bump) đổi khóa ngay, không chờ hết hạn."""
	key = f"{CACHE_PREFIX}{warehouse}:{cache_tag}"
	try:
		cached = frappe.cache.get_value(key)
	except Exception:
		cached = None
	if cached is not None:
		return cached
	data = stock_snapshot(item_codes, warehouse)
	try:
		frappe.cache.set_value(key, data, expires_in_sec=CACHE_SECONDS)
	except Exception:
		pass
	return data


# ---------------------------------------------------------------------------
# Chặn bán vượt tồn (server, cùng giao dịch tạo đơn)
# ---------------------------------------------------------------------------


def check_invoice_stock(invoice):
	"""Chặn tạo đơn khi vượt tồn khả dụng. Gọi TRƯỚC invoice.insert(), trong cùng giao dịch.

	Khóa các dòng Bin liên quan (FOR UPDATE, thứ tự tên — cùng thứ tự ERPNext dùng) -> hai thu ngân cùng bán món cuối:
	người sau chờ người trước commit rồi đọc lại (locking read thấy đơn nháp vừa tạo) -> bị từ chối.
	Không đọc Stock Settings -> Allow Negative Stock (mypos chặn độc lập).
	"""
	if invoice.get("is_return"):
		return
	pi = _pi()
	rows = [d for d in invoice.get("items") or [] if d.item_code and flt(d.stock_qty or d.qty) > 0]
	if not rows:
		return
	bundles = pi.get_active_bundle_items({d.item_code for d in rows})

	required = defaultdict(float)  # (code, warehouse) -> qty
	for d in rows:
		qty = flt(d.stock_qty) or flt(d.qty) * (flt(d.conversion_factor) or 1)
		for comp in bundles.get(d.item_code) or [frappe._dict(item_code=d.item_code, qty=1)]:
			required[(comp.item_code, d.warehouse)] += flt(comp.qty) * qty

	details = pi.get_stock_item_details({code for code, _wh in required})
	by_wh = defaultdict(set)
	for code, wh in required:
		if (details.get(code) or {}).get("is_stock_item"):
			by_wh[wh].add(code)

	avail = {}
	for wh in sorted(by_wh):
		for code, value in available_map(by_wh[wh], wh, lock="update", exclude=invoice.name).items():
			avail[(code, wh)] = value

	short = {}
	for key, need in required.items():
		if key not in avail:
			continue
		qty, has_sre = avail[key]
		if not pi.is_stock_check_needed(details.get(key[0]), has_sre):
			continue
		if qty + EPS < need:
			short[key] = max(0.0, qty)
	if not short:
		return

	names = _item_names({code for code, _wh in short} | {d.item_code for d in rows})
	messages = []
	seen = set()
	for d in rows:
		comps = bundles.get(d.item_code)
		label = names.get(d.item_code) or d.item_code
		if comps:
			missing = [names.get(c.item_code) or c.item_code for c in comps if (c.item_code, d.warehouse) in short]
			if missing and d.item_code not in seen:
				messages.append(_("{0} — Hết: {1}").format(label, ", ".join(missing)))
				seen.add(d.item_code)
		elif (d.item_code, d.warehouse) in short and d.item_code not in seen:
			left = short[(d.item_code, d.warehouse)]
			if left <= EPS:
				messages.append(_("{0} đã hết hàng").format(label))
			else:
				messages.append(_("{0} chỉ còn {1}").format(label, floor(left + EPS)))
			seen.add(d.item_code)
	frappe.throw(
		_("Không đủ hàng để tạo đơn: {0}.").format("; ".join(messages)),
		title=_("Không đủ hàng"),
		exc=MyposStockError,
	)


def submit_shortfall(invoice):
	"""Món sẽ bị ERPNext từ chối khi SUBMIT đơn nháp này vì thiếu tồn (cùng công thức ERPNext: KHÔNG trừ đơn nháp).

	Dùng ở webhook SePay trước khi submit (Q4, 2026-11-29): tiền đã về nhưng tồn bị trừ ngoài POS sau khi đơn giữ chỗ
	(Stock Entry, Sales Invoice trên Desk...). Stock Settings bật Allow Negative Stock -> ERPNext không kiểm -> [].
	Trả danh sách tên món / nguyên liệu thiếu (rỗng = submit được).
	"""
	if invoice.get("is_return") or frappe.get_cached_doc("Stock Settings").allow_negative_stock:
		return []
	pi = _pi()
	rows = [d for d in invoice.get("items") or [] if d.item_code and not d.get("serial_and_batch_bundle")]
	if not rows:
		return []
	bundles = pi.get_active_bundle_items({d.item_code for d in rows})
	required = defaultdict(float)
	for d in rows:
		qty = flt(d.stock_qty) or flt(d.qty) * (flt(d.conversion_factor) or 1)
		for comp in bundles.get(d.item_code) or [frappe._dict(item_code=d.item_code, qty=1)]:
			required[(comp.item_code, d.warehouse)] += flt(comp.qty) * qty
	details = pi.get_stock_item_details({code for code, _wh in required})
	by_wh = defaultdict(set)
	for code, wh in required:
		if (details.get(code) or {}).get("is_stock_item"):
			by_wh[wh].add(code)
	missing = []
	for wh, codes in by_wh.items():
		avail = available_map(codes, wh, include_drafts=False)
		for code in codes:
			qty, has_sre = avail[code]
			if pi.is_stock_check_needed(details.get(code), has_sre) and qty + EPS < required[(code, wh)]:
				missing.append(code)
	names = _item_names(set(missing))
	return [names.get(c) or c for c in sorted(missing)]
