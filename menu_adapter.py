"""
菜单/优惠券适配器：把麦当劳 MCP 返回转成 combo_optimizer 的 Item / Coupon。

【2026-10-10 按真实返回重写】字段映射基于实测（门店 3450410，query-meals 真实返回 67KB）：
- 返回在 content[0].text 里，格式为 "markdown 字段说明 + ## Original Response + 纯JSON"
- data.categories[]: {name, meals: [{code, tags}]}
- data.meals: {code: {name, image, currentPrice, originalPrice, discountType, canWithOrder, ...}}
- data.frequent: 常点餐品 {code, tags}

时段可用性（实测结论）：
- query-meals 返回【不是】纯当前可点集——午餐时段(11:47)返回里仍含早餐商品（脆薯饼/厚松饼猪柳堡等）
- 菜单无机器可读的供应时段字段；仅个别商品 tags 带"全天供应"
- 真实时段窗来自门店查询返回的 reservationTimeOptions（如"早餐(05:14至10:15)"，按店按日变）
- 因此早餐类商品判定 = 名称关键词启发式 且 无"全天供应"标记 → 仅早餐时段可点
"""

from __future__ import annotations
import json
import re
from typing import Any, Optional
from combo_optimizer import Item, Coupon

# 名称命中即视为早餐类商品（启发式，真实边界无字段可依）
_BREAKFAST_KEYWORDS = (
    "麦满分", "猪柳", "吉士蛋", "早餐", "热香饼", "松饼", "薯饼", "脆薯饼",
    "大脆薯", "油条", "豆浆",
)


def extract_data(raw: Any) -> dict:
    """
    MCP 返回 → 业务 data dict。
    实测 text 有多种包装形态（说明+JSON+尾部提示 / 字段说明+Original Response+JSON），
    统一策略：定位 '{"success"' 起始用 raw_decode 解析（JSON 夹在文字中间也能解）。
    """
    if isinstance(raw, dict):
        content = raw.get("content")
        text = content[0].get("text") if isinstance(content, list) and content else None
        if text and "{" in text:
            idx = text.find('{"success"')
            if idx < 0:
                idx = text.find("{")
            dec = json.JSONDecoder()
            while idx >= 0:
                try:
                    parsed, _ = dec.raw_decode(text[idx:])
                    if isinstance(parsed, dict) and "data" in parsed:
                        return parsed.get("data") or {}
                except ValueError:
                    pass
                idx = text.find("{", idx + 1)
        if "success" in raw:          # 已是业务 JSON
            return raw.get("data") or {}
        d = raw.get("data")
        if isinstance(d, dict):
            return d
    return {}


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace("¥", "").strip())
    except (TypeError, ValueError):
        return default


def meal_to_items(raw: dict) -> list[Item]:
    """query-meals 真实返回 → Item 列表（categories 顺序 + meals 详情映射）。"""
    data = extract_data(raw)
    meals_map: dict = data.get("meals") or {}
    cats = data.get("categories") or []
    if not isinstance(meals_map, dict):
        meals_map = {}
    items: list[Item] = []
    seen: set[str] = set()
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        cat_name = (cat.get("name") or "").replace("\n", "")
        for ref in cat.get("meals") or []:
            if not isinstance(ref, dict):
                continue
            code = str(ref.get("code") or "")
            if not code or code in seen:
                continue
            info = meals_map.get(code) or {}
            name = info.get("name") or f"商品{code}"
            tags = [str(t) for t in (ref.get("tags") or [])]
            all_day = any("全天供应" in t for t in tags)
            is_combo = "套餐" in name or "件套" in name or any("套餐" in t for t in tags)
            # 早餐类：关键词命中且未标"全天供应" → 仅早餐时段可点
            serve = [] if all_day else (
                ["breakfast"] if any(k in name for k in _BREAKFAST_KEYWORDS) else []
            )
            items.append(Item(
                id=code,
                name=name,
                price=_to_float(info.get("currentPrice")),
                orig_price=_to_float(info.get("originalPrice")) or None,
                category=cat_name,
                tags=tags,
                is_combo=is_combo,
                serve_periods=serve,
            ))
            seen.add(code)
    return items


def coupons_to_models(raw: dict) -> list[Coupon]:
    """query-store-coupons / available-coupons 返回 → Coupon 列表（尽力解析，防御式）。"""
    data = extract_data(raw)
    lst = data if isinstance(data, list) else (data.get("list") or data.get("items") or data.get("coupons") or [])
    coupons: list[Coupon] = []
    for d in lst if isinstance(lst, list) else []:
        if not isinstance(d, dict):
            continue
        name = d.get("name") or d.get("title") or "优惠券"
        ctype = "full_reduce"
        threshold = _to_float(d.get("threshold") or d.get("fullAmount"))
        reduce = _to_float(d.get("reduce") or d.get("minus"))
        rate = _to_float(d.get("rate") or d.get("discount"), 0.0)
        if rate and 0 < rate < 1:
            ctype = "discount"
        elif d.get("itemId") or d.get("itemKeywords"):
            ctype = "item_cut"
        coupons.append(Coupon(
            name=name, ctype=ctype, threshold=threshold, reduce=reduce, rate=rate,
            item_keywords=list(d.get("itemKeywords") or []),
        ))
    return coupons


# ---------- 真实时段窗解析（来自门店 reservationTimeOptions） ----------

_PERIOD_ZH2EN = {"早餐": "breakfast", "午餐": "lunch", "下午茶": "afternoon", "夜市": "dinner", "宵夜": "late"}


def parse_time_windows(stores_raw: dict) -> dict[str, tuple[str, str]]:
    """
    门店查询返回 → {"breakfast": ("05:14","10:15"), "lunch": ("10:44","14:15"), ...}
    取今天(today=true 或首条)的 reservationOptionText 解析；解析失败返回 {}。
    """
    data = extract_data(stores_raw)
    lst = data if isinstance(data, list) else (data.get("data") or data.get("list") or [])
    if isinstance(lst, dict):
        lst = lst.get("stores") or []
    today_opt = None
    for s in lst if isinstance(lst, list) else []:
        if not isinstance(s, dict):
            continue
        for opt in (s.get("reservationTimeOptions") or []):
            if isinstance(opt, dict) and (opt.get("today") or today_opt is None):
                today_opt = opt.get("reservationOptionText") or today_opt
        if today_opt:
            break
    if not today_opt:
        return {}
    windows: dict[str, tuple[str, str]] = {}
    for zh, en in _PERIOD_ZH2EN.items():
        m = re.search(rf"{zh}\((\d{{1,2}}:\d{{2}})至([\d:]+)\)", today_opt)
        if m:
            windows[en] = (m.group(1), m.group(2))
    return windows


def period_from_windows(windows: dict[str, tuple[str, str]], now_hhmm: str) -> Optional[str]:
    """当前 HH:MM 落在哪个真实时段窗内 → 时段名；不在任何窗内返回 None。"""
    def mins(t: str) -> int:
        h, m = t.split(":")[:2]
        return int(h) * 60 + int(m)
    t = mins(now_hhmm)
    for en, (start, end) in windows.items():
        s, e = mins(start), mins(end)
        if s <= t <= e or (e < s and (t >= s or t <= e)):   # 兼容跨夜窗
            return en
    return None
