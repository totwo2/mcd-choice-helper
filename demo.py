"""
demo.py —— 麦门选择困难症助手 运行入口

用法：
  python demo.py                     # 内嵌样例演示（无需 Token，随时可跑）
  python demo.py --budget 30         # 指定预算演示
  python demo.py --keyword 春熙路    # 真数据：搜店→真实时段窗→真实菜单+券→推荐（需 MCD_TOKEN）
  python demo.py --store 3450410     # 指定门店编码直接拉真实菜单

真数据链路（2026-10-10 实测打通）：
  门店搜索(searchType=2) → 解析门店真实时段窗(早餐05:14-10:15等，按店按日变)
  → query-meals 真实菜单(categories+meals映射) → 时段可用性过滤 → 推荐
设计：在 WorkBuddy 内，Agent 通过「连接器」直连麦当劳 MCP，无需本文件；
      本文件用于独立验证 / 评委本地复现。
"""

from __future__ import annotations
import re
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from combo_optimizer import (
    recommend, summarize_history, SAMPLE_MENU, SAMPLE_COUPONS,
)
import menu_adapter as ma


def detect_period(now_hhmm: str | None = None) -> str:
    """
    本地默认分界（兜底用，仅当门店真实时段窗解析不到时）。
    真实可用性以门店 reservationTimeOptions 为准（见 menu_adapter.parse_time_windows）。
    """
    text = now_hhmm or datetime.now().strftime("%H:%M")
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (datetime.now().hour, datetime.now().minute)
    t = hh * 60 + mm
    if t < 10 * 60 + 15:      # 实测早餐窗约至 10:15（按店有差异，此处仅兜底）
        return "breakfast"
    if t < 14 * 60 + 15:
        return "lunch"
    if t < 17 * 60:
        return "afternoon"
    if t < 21 * 60 + 45:
        return "dinner"
    return "late"


def _print(title: str, combos):
    print(f"\n### {title}")
    if not combos:
        print("  （无符合约束的组合）")
        return
    for i, c in enumerate(combos, 1):
        d = c.to_dict()
        print(f"  {i}. {' + '.join(d['items'])}")
        print(f"     原价合计 ¥{d['subtotal']} | 券【{d['coupon']}】减 ¥{d['discount']} "
              f"| 实付 ¥{d['payable']} | 省 ¥{d['saved']}")


def load_real(keyword: str | None, store: str | None):
    """真数据链路：搜店→时段窗→菜单→券→历史偏好。"""
    from mcp_client import (
        MCDClient, get_meals, get_store_coupons, get_history, get_now,
        list_nearby_stores, first_store_code,
    )
    c = MCDClient()
    now_raw = get_now(c)
    now_text = (now_raw.get("content") or [{}])[0].get("text", "")
    windows: dict = {}
    store_code = store
    try:
        stores_raw = list_nearby_stores(c, city="成都", keyword=keyword or "春熙路")
        windows = ma.parse_time_windows(stores_raw)
        store_code = store_code or first_store_code(stores_raw)
    except Exception as e:
        print(f"[门店/时段窗解析失败] {e}")
    if not store_code:
        raise RuntimeError("未拿到门店编码（搜不到门店），无法拉菜单")

    now_m = re.search(r"(\d{1,2}:\d{2})", now_text)
    now_hhmm = now_m.group(1) if now_m else datetime.now().strftime("%H:%M")
    period = ma.period_from_windows(windows, now_hhmm) or detect_period(now_hhmm)

    menu = ma.meal_to_items(get_meals(c, store_code, order_type=1))
    coupons = ma.coupons_to_models(get_store_coupons(c, store_code, order_type=1))
    pref: list[str] = []
    try:
        hist = get_history(c)
        hist_data = ma.extract_data(hist)
        pref = summarize_history(hist_data.get("list") or hist_data.get("orders") or [])
    except Exception:
        pass
    return store_code, menu, coupons, pref, period, windows


def main():
    store = None
    budget = None
    keyword = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--store" and i + 1 < len(args):
            store = args[i + 1]
        elif a == "--budget" and i + 1 < len(args):
            budget = float(args[i + 1])
        elif a == "--keyword" and i + 1 < len(args):
            keyword = args[i + 1]

    # —— 取数据 ——
    if (store or keyword) and os.environ.get("MCD_TOKEN"):
        try:
            store_code, menu, coupons, pref, period, windows = load_real(keyword, store)
            src = f"[真实数据] 门店 {store_code} | 菜单 {len(menu)} 项 | 券 {len(coupons)} 张"
        except Exception as e:
            print(f"[真实数据拉取失败，回退样例] {e}")
            menu, coupons, pref = SAMPLE_MENU, SAMPLE_COUPONS, []
            period, windows = detect_period(), {}
            src = "[样例数据回退]"
    else:
        menu, coupons, pref = SAMPLE_MENU, SAMPLE_COUPONS, []
        period, windows = detect_period(), {}
        src = "[样例数据] 未提供 --store/--keyword 或 MCD_TOKEN，使用内嵌样例演示。"

    print(src)
    print(f"真实时段窗: {windows or '（未解析到，用本地默认分界）'}")
    print(f"当前时段判定：{period}"
          + (f" | 历史偏好Top：{pref}" if pref else ""))

    _print(f"打开即推：最划算组合（{period}）", recommend(menu, coupons, meal_period=period, preferred_keywords=pref))
    _print("按预算拼单：¥30", recommend(menu, coupons, budget=30, meal_period=period, preferred_keywords=pref))
    if budget:
        _print(f"按预算拼单：¥{budget}", recommend(menu, coupons, budget=budget, meal_period=period, preferred_keywords=pref))


if __name__ == "__main__":
    main()
