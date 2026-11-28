import { create } from "zustand";
import type { CartLine, StockInfo } from "../types";
import { pushToast } from "./toasts";

/**
 * Tồn khả dụng theo kho của POS Profile (2026-11-29, `docs/rules/stock.md`). Nạp ở màn Bán món qua `usePosRefresh`
 * (phiên bản thay đổi + realtime + lưới an toàn). Giao diện chỉ chặn sớm; server (`create_draft_order`) kiểm lại và khóa Bin.
 */
type StockState = {
	items: Record<string, StockInfo>;
	loaded: boolean;
	set: (items: Record<string, StockInfo>) => void;
};

export const useStock = create<StockState>((set) => ({
	items: {},
	loaded: false,
	set: (items) => set({ items, loaded: true }),
}));

/** Giới hạn số lượng cho một mã bán; null = không giới hạn (món không quản lý tồn / cho phép âm / chưa nạp). */
export function stockLimit(itemCode: string, items = useStock.getState().items): number | null {
	const info = items[itemCode];
	if (!info || !info.enforce) return null;
	return Math.max(0, Math.floor(info.qty));
}

/** Số lượng tối đa cho dòng `key` = giới hạn của mã - tổng các dòng KHÁC cùng mã (khác tùy chọn). null = không giới hạn. */
export function lineCap(lines: CartLine[], itemCode: string, excludeKey: string | null, keyOf: (l: CartLine) => string): number | null {
	const limit = stockLimit(itemCode);
	if (limit === null) return null;
	const others = lines
		.filter((l) => l.item_code === itemCode && (excludeKey === null || keyOf(l) !== excludeKey))
		.reduce((sum, l) => sum + l.qty, 0);
	return Math.max(0, limit - others);
}

/** Thông báo ngắn khi giỏ bị hạ về mức tồn (một toast cho mỗi mã). */
export function notifyCapped(name: string, limit: number) {
	pushToast(limit > 0 ? `${name}: chỉ còn ${limit}` : `${name} đã hết hàng`, { key: `stock-cap-${name}`, ms: 3000 });
}
