"""
麦门选择困难症助手 —— 组合优化器（核心源代码，可独立运行/测试）

职责：
  给定麦当劳门店菜单 + 可用优惠券 + 用户预算/时段/历史订单，
  产出「最划算」的点餐组合。

硬约束（用户明确要求）：
  ❌ 绝不允许出现「为了凑价、全都是饮料」的套餐。
  ✅ 每个推荐组合必须至少包含 1 个非饮料类正餐/主食。

本文件不依赖任何外部库，纯标准库，方便评委/用户直接 `python combo_optimizer.py` 自测。
真实菜单数据由 mcp_client.py 拉取；无 Token 时 demo.py 用内嵌样例数据。
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Optional


# ---------- 数据模型 ----------

@dataclass
class Item:
    id: str
    name: str
    price: float                 # 当前售价
    orig_price: Optional[float] = None   # 原价（部分单品有）
    category: str = ""           # 分类，如 招牌汉堡/小食/饮料/甜品/早餐
    tags: list[str] = field(default_factory=list)
    is_combo: bool = False       # 是否官方套餐（套餐本身已是合理组合）
    serve_periods: list[str] = field(default_factory=list)  # 供应时段（breakfast/lunch/...）；空 = 全天可点

    @property
    def value(self) -> float:
        """用户感知价值：用原价兜底，避免无原价时价值为 0。"""
        return self.orig_price if self.orig_price else self.price


@dataclass
class Coupon:
    name: str
    ctype: str                   # full_reduce | discount | item_cut
    threshold: float = 0.0       # 满减门槛 / 折扣门槛
    reduce: float = 0.0          # 满减金额 / 单品立减金额
    rate: float = 0.0            # 折扣率（0.85 = 85 折，即减 15%）
    item_keywords: list[str] = field(default_factory=list)  # item_cut 命中关键词


@dataclass
class Combo:
    items: list[Item]
    coupon_name: str = ""
    discount: float = 0.0

    @property
    def subtotal(self) -> float:
        return round(sum(i.price for i in self.items), 2)

    @property
    def payable(self) -> float:
        return round(max(0.0, self.subtotal - self.discount), 2)

    @property
    def saved(self) -> float:
        return round(self.subtotal - self.payable, 2)

    @property
    def original_value(self) -> float:
        return round(sum(i.value for i in self.items), 2)

    def to_dict(self) -> dict:
        return {
            "items": [i.name for i in self.items],
            "subtotal": self.subtotal,
            "coupon": self.coupon_name,
            "discount": self.discount,
            "payable": self.payable,
            "saved": self.saved,
        }


# ---------- 饮料判定（硬约束的核心） ----------

_DRINK_KEYWORDS = (
    "饮料", "饮品", "可乐", "雪碧", "咖啡", "豆浆", "牛奶", "麦旋酷",
    "橙汁", "红茶", "绿茶", "美汁源", "柠檬茶", "冰封", "气泡", "拿铁",
    "美式", "椰汁", "玉米饮", "热巧", "巧克力（饮",
)

_DRINK_CATEGORIES = ("饮料", "饮品")


def is_drink(item: Item) -> bool:
    """判断单品是否为饮料。套餐(is_combo)一律不算饮料。"""
    if item.is_combo:
        return False
    if item.category in _DRINK_CATEGORIES:
        return True
    hay = (item.name + " " + " ".join(item.tags)).lower()
    return any(k.lower() in hay for k in _DRINK_KEYWORDS)


def has_real_food(items: list[Item]) -> bool:
    """组合里至少要有 1 个非饮料单品（套餐视为正餐）。"""
    return any(not is_drink(i) for i in items)


# ---------- 优惠券计算 ----------

def _coupon_applies(coupon: Coupon, items: list[Item], subtotal: float) -> float:
    if coupon.ctype == "full_reduce":
        if subtotal >= coupon.threshold:
            return min(coupon.reduce, subtotal)
        return 0.0
    if coupon.ctype == "discount":
        if subtotal >= coupon.threshold:
            return round(subtotal * (1 - coupon.rate), 2)
        return 0.0
    if coupon.ctype == "item_cut":
        hit = any(
            any(k.lower() in (i.name + " ".join(i.tags)).lower() for k in coupon.item_keywords)
            for i in items
        )
        return min(coupon.reduce, subtotal) if hit else 0.0
    return 0.0


def best_coupon(items: list[Item], coupons: list[Coupon]) -> tuple[float, str]:
    """返回 (最大优惠金额, 券名)。"""
    subtotal = sum(i.price for i in items)
    best_amt, best_name = 0.0, ""
    for c in coupons:
        amt = _coupon_applies(c, items, subtotal)
        if amt > best_amt:
            best_amt, best_name = amt, c.name
    return round(best_amt, 2), best_name


# ---------- 时段可用性（硬约束） ----------

def filter_available(menu: list[Item], meal_period: Optional[str]) -> list[Item]:
    """
    ★ 硬约束：只保留当前时段可点的商品。
    - serve_periods 为空 = 全天可点；非空则必须包含当前时段，否则从候选池剔除。
    - 真实路径下菜单来自 query-meals（只返回当前可点商品，过点商品根本不在返回里），
      本函数是双保险；样例/离线路径下由 serve_periods 保证（如早餐商品过点即被剔除）。
    """
    if not meal_period:
        return list(menu)
    return [i for i in menu if not i.serve_periods or meal_period in i.serve_periods]


# ---------- 推荐主逻辑 ----------

def _period_bias(meal_period: Optional[str], item: Item) -> float:
    """早/午/晚时段对单品做轻微加权，仅影响排序不淘汰。"""
    if not meal_period:
        return 0.0
    p = meal_period
    if p == "breakfast" and any("早餐" in t or "麦满分" in t or "粥" in t for t in item.tags):
        return 1.0
    if p == "lunch" and any("午餐" in t or "超值" in t for t in item.tags):
        return 0.5
    if p == "dinner" and any("晚餐" in t or "下班" in t for t in item.tags):
        return 0.5
    return 0.0


def recommend(
    menu: list[Item],
    coupons: list[Coupon],
    budget: Optional[float] = None,
    meal_period: Optional[str] = None,
    preferred_keywords: Optional[list[str]] = None,
) -> list[Combo]:
    """
    产出推荐组合（最多 3 个）。

    - 无预算：推「官方套餐里最划算的几个」+「按时段优选的单品组合」。
    - 有预算：在预算内贪心拼单，最大化省钱/价值，强制含正餐。
    """
    preferred_keywords = preferred_keywords or []
    # ★ 硬约束：时段可用性过滤，不在当前时段供应的商品直接出候选池
    menu = filter_available(menu, meal_period)
    combos: list[Combo] = []

    singles = [i for i in menu if not i.is_combo]
    combos_menu = [i for i in menu if i.is_combo]

    def make_combo(items: list[Item]) -> Optional[Combo]:
        if not items:
            return None
        if not has_real_food(items):           # ★ 硬约束：禁止全是饮料
            return None
        d, n = best_coupon(items, coupons)
        return Combo(items=items, coupon_name=n, discount=d)

    # —— 路径 A：官方套餐（本身合理，直接作为候选）——
    for c in combos_menu:
        cb = make_combo([c])
        if cb and (budget is None or cb.payable <= budget):
            combos.append(cb)

    # —— 路径 B：单品自由拼（保证有正餐）——
    mains = [i for i in singles if not is_drink(i)]
    drinks = [i for i in singles if is_drink(i)]
    # 偏好加权排序（历史口味 / 时段）
    def score(i: Item) -> float:
        s = _period_bias(meal_period, i)
        if any(k.lower() in (i.name + " ".join(i.tags)).lower() for k in preferred_keywords):
            s += 2.0
        return s

    mains.sort(key=score, reverse=True)
    drinks.sort(key=lambda i: i.price)  # 饮料挑便宜的凑

    if budget is None:
        # 无预算：给一个「1 主食 + 1 小食 + 1 饮料」的均衡组合
        pick = []
        if mains:
            pick.append(mains[0])
        side = [i for i in singles if not is_drink(i) and i not in pick]
        side.sort(key=score, reverse=True)
        if side:
            pick.append(side[0])
        if drinks:
            pick.append(drinks[0])
        cb = make_combo(pick)
        if cb:
            combos.append(cb)
    else:
        # 有预算：主食打底，随后每一步在「塞得下」的候选里挑实付最低（=省最多）者
        def pay_of(items: list[Item]) -> float:
            d, _ = best_coupon(items, coupons)
            return max(0.0, sum(i.price for i in items) - d)

        def add_best(pool: list[Item], cur: list[Item]) -> list[Item]:
            """枚举 pool 中加进去后实付仍 ≤ 预算的候选，取实付最低者；都不行则原样返回。"""
            best, best_pay = cur, pay_of(cur)
            for cand in pool:
                p = pay_of(cur + [cand])
                if p <= budget + 0.01 and p < best_pay:
                    best, best_pay = cur + [cand], p
            return best

        for main in mains[:5]:
            if pay_of([main]) > budget + 0.01:
                continue          # 主食单独就超预算，放弃该候选线
            pick = [main]
            sides = [i for i in singles if not is_drink(i) and i is not main]
            pick = add_best(sides, pick)
            pick = add_best(drinks, pick)
            cb = make_combo(pick)
            if cb and cb.payable <= budget + 0.01:
                combos.append(cb)

    # 去重 + 排序：省钱多者优先，再按接近预算
    seen = set()
    uniq = []
    for c in combos:
        key = tuple(sorted(i.id for i in c.items))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    uniq.sort(key=lambda c: (c.saved, -abs(c.payable - (budget or 1e9))), reverse=True)
    return uniq[:3]


def summarize_history(orders: list[dict]) -> list[str]:
    """从历史订单提取高频单品关键词（用于口味偏好）。"""
    from collections import Counter
    cnt: Counter = Counter()
    for o in orders:
        for it in o.get("items", []):
            cnt[it.get("name", "")] += 1
    return [k for k, _ in cnt.most_common(5) if k]


# ---------- 内嵌样例（无 Token 也能自测） ----------

SAMPLE_MENU = [
    Item("h1", "巨无霸", 24.0, 24.0, "招牌汉堡", ["午餐"], False),
    Item("h2", "麦辣鸡腿堡", 21.0, 21.0, "招牌汉堡", ["午餐"], False),
    Item("h3", "板烧鸡腿堡", 22.0, 22.0, "招牌汉堡", [], False),
    Item("b1", "麦满分（猪柳）", 13.0, 13.0, "早餐", ["早餐"], False, serve_periods=["breakfast"]),
    Item("s1", "薯条（中）", 12.0, 12.0, "小食", [], False),
    Item("s2", "麦辣鸡翅（2块）", 15.0, 15.0, "小食", [], False),
    Item("n1", "玉米杯", 9.0, 9.0, "小食", [], False),
    Item("c1", "可乐（中）", 10.0, 10.0, "饮料", ["可乐"], False),
    Item("c2", "雪碧（中）", 10.0, 10.0, "饮料", ["雪碧"], False),
    Item("c3", "美汁源果粒橙", 11.0, 11.0, "饮料", ["橙汁"], False),
    Item("cm1", "超值午餐A（巨无霸+薯条+可乐）", 42.0, 46.0, "套餐", ["午餐", "超值"], True),
    Item("cm2", "早餐全餐（麦满分+咖啡+薯饼）", 25.0, 28.0, "套餐", ["早餐"], True, serve_periods=["breakfast"]),
]

SAMPLE_COUPONS = [
    Coupon("满30减5", "full_reduce", threshold=30, reduce=5),
    Coupon("满40减8", "full_reduce", threshold=40, reduce=8),
    Coupon("可乐立减2", "item_cut", reduce=2, item_keywords=["可乐"]),
    Coupon("全场85折(满20)", "discount", threshold=20, rate=0.85),
]


def _self_test():
    print("=== 自测 1：无预算，推最划算组合（午餐时段）===")
    for cb in recommend(SAMPLE_MENU, SAMPLE_COUPONS, meal_period="lunch"):
        print(json.dumps(cb.to_dict(), ensure_ascii=False))

    print("\n=== 自测 2：预算 30 元，拼组合（硬约束：不能全是饮料）===")
    for cb in recommend(SAMPLE_MENU, SAMPLE_COUPONS, budget=30, meal_period="lunch"):
        print(json.dumps(cb.to_dict(), ensure_ascii=False))

    print("\n=== 自测 3：故意只选饮料，应被拒绝（has_real_food=False）===")
    only_drinks = [i for i in SAMPLE_MENU if is_drink(i)]
    cb = Combo(items=only_drinks)
    print("全是饮料的组合是否通过硬约束:", has_real_food(cb.items), "(应为 False → 不推荐)")

    print("\n=== 自测 4：时段可用性硬约束（午餐时段早餐商品必须被剔除）===")
    lunch_ids = {i.id for i in filter_available(SAMPLE_MENU, "lunch")}
    leaked = lunch_ids & {"b1", "cm2"}
    print("午餐候选池是否含早餐商品:", bool(leaked), "(应为 False，漏掉:", leaked or "无", ")")
    bf_ids = {i.id for i in filter_available(SAMPLE_MENU, "breakfast")}
    print("早餐候选池是否含早餐商品:", {"b1", "cm2"} <= bf_ids, "(应为 True)")
    for cb in recommend(SAMPLE_MENU, SAMPLE_COUPONS, meal_period="lunch"):
        names = ",".join(i.name for i in cb.items)
        assert "麦满分" not in names and "早餐全餐" not in names, f"早餐商品漏进午餐推荐！{names}"
    print("午餐推荐结果均不含早餐商品 ✅")

    print("\n=== 自测 5：预算模式结果永不超预算、必含正餐 ===")
    for b in (20, 25, 30, 45):
        for cb in recommend(SAMPLE_MENU, SAMPLE_COUPONS, budget=b, meal_period="lunch"):
            assert cb.payable <= b + 0.01, f"实付 ¥{cb.payable} 超预算 ¥{b}：{cb.to_dict()}"
            assert has_real_food(cb.items), f"组合缺正餐：{cb.to_dict()}"
    print("预算 20/25/30/45 所有组合：实付≤预算 且 含正餐 ✅")


if __name__ == "__main__":
    _self_test()
