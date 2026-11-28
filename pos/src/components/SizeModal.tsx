import { formatMoney } from "../store/cart";
import { usePos } from "../lib/pos-context";
import type { MenuItem, MenuSize } from "../types";
import { useStock } from "../store/stock";
import ItemModalFrame from "./ItemModalFrame";
import { isOut, StockBadge } from "./MenuGrid";

interface Props {
	item: MenuItem;
	sizes: MenuSize[];
	open: boolean;
	onClose: () => void;
	onPick: (size: MenuSize) => void;
}

/** Chọn size: mỗi size một hàng "nhãn ……… giá" (giá cột phải, thẳng hàng). Bấm hàng = thêm ngay vào giỏ. */
export default function SizeModal({ item, sizes, open, onClose, onPick }: Props) {
	const { config } = usePos();
	const currency = config?.profile?.currency || "VND";
	const stock = useStock((state) => state.items);

	if (!open) return null;

	return (
		<ItemModalFrame item={item} hint="Chọn size" onClose={onClose}>
			{sizes.length ? (
				<div className="pos-size-rows">
					{sizes.map((size) => {
						const info = stock[size.item_code];
						const out = isOut(info);
						const disabled = size.rate == null || out;
						return (
							<button
								type="button"
								key={size.item_code}
								className={`pos-size-row${disabled ? " is-disabled" : ""}`}
								disabled={disabled}
								onClick={() => onPick(size)}
							>
								<span className="pos-size-row__label">
									{size.label || size.item_code}
									<StockBadge info={info} className="pos-size-row__stock" />
									{out && info?.short?.length ? (
										<span className="pos-size-row__short">Hết: {info.short.join(", ")}</span>
									) : null}
								</span>
								<span className="pos-size-row__price">
									{size.rate == null
										? "Chưa có giá"
										: out
											? "Hết hàng"
											: formatMoney(size.rate as number, currency)}
								</span>
							</button>
						);
					})}
				</div>
			) : (
				<div className="pos-empty">Món này chưa có size nào.</div>
			)}
		</ItemModalFrame>
	);
}
