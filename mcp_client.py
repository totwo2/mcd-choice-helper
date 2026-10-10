"""
麦当劳 MCP 客户端（最小实现，纯标准库，仅用于本地联调 / 独立运行 demo）。

- Token 从环境变量 MCD_TOKEN 读取，绝不写死、绝不落盘。
- 协议：Streamable HTTP（POST JSON-RPC 到 https://mcp.mcd.cn）
- 在 WorkBuddy 内使用时，本文件非必须——WorkBuddy 连接器已直连 MCP；
  本文件用于「脱离 WorkBuddy 也能跑通 demo / 做端到端验证」。
"""

from __future__ import annotations
import json
import os
import urllib.request
from typing import Any, Optional

URL = "https://mcp.mcd.cn"


class MCDClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("MCD_TOKEN")
        if not self.token:
            raise RuntimeError("缺少 MCD_TOKEN（请通过环境变量传入）")
        self._hdr = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._sid: Optional[str] = None
        self._seq = 0
        self._init()

    def _post(self, payload: dict) -> dict:
        self._seq += 1
        payload.setdefault("jsonrpc", "2.0")
        payload.setdefault("id", self._seq)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(URL, data=data, headers=self._hdr, method="POST")
        if self._sid:
            req.add_header("Mcp-Session-Id", self._sid)
        with urllib.request.urlopen(req, timeout=30) as r:
            for k, v in r.headers.items():
                if k.lower() == "mcp-session-id":
                    self._sid = v
            raw = r.read().decode()
        if raw.strip().startswith("event:") or "data:" in raw:
            out = []
            for line in raw.splitlines():
                if line.startswith("data:"):
                    out.append(line[5:].strip())
            return json.loads("\n".join(out))
        return json.loads(raw)

    def _init(self):
        r = self._post({
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcd-choice-helper", "version": "1.0.0"},
            },
        })
        # 发送 initialized 通知
        note = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        data = json.dumps(note).encode()
        req = urllib.request.Request(URL, data=data, headers=self._hdr, method="POST")
        if self._sid:
            req.add_header("Mcp-Session-Id", self._sid)
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass
        return r

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        r = self._post({
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        return r.get("result", {})


def get_now(client: MCDClient) -> dict:
    return client.call_tool("now-time-info")


def list_nearby_stores(client: MCDClient, be_type: int = 1, city: str = "成都", keyword: str = "") -> dict:
    """
    实测 schema：beType(1=到店自提,5=得来速) 与 searchType 均必传。
    searchType=1 搜收藏餐厅(默认,无收藏则空) / 2=按位置搜索（实测必须用 2 才有结果）。
    """
    args: dict = {"beType": be_type, "searchType": 2, "city": city}
    if keyword:
        args["keyword"] = keyword
    try:
        return client.call_tool("query-nearby-stores", args)
    except Exception as e:
        return {"error": str(e)}


def first_store_code(stores_raw: dict) -> str | None:
    """从门店查询返回里提取第一个 storeCode（schema: 全数字编码）。"""
    import re, json as _json
    m = re.search(r'"storeCode"\s*:\s*"?(\d+)"?', _json.dumps(stores_raw, ensure_ascii=False))
    return m.group(1) if m else None


def get_meals(client: MCDClient, store_code: str, order_type: int = 1, be_type: int = 1) -> dict:
    """实测 schema：storeCode(全数字) + orderType + beType 必传；到店自取不传 beCode。"""
    return client.call_tool("query-meals", {"storeCode": store_code, "orderType": order_type, "beType": be_type})


def get_store_coupons(client: MCDClient, store_code: str, order_type: int = 1, be_type: int = 1) -> dict:
    return client.call_tool("query-store-coupons", {"storeCode": store_code, "orderType": order_type, "beType": be_type})


def get_available_coupons(client: MCDClient) -> dict:
    return client.call_tool("available-coupons")


def get_history(client: MCDClient) -> dict:
    return client.call_tool("order-list")


if __name__ == "__main__":
    c = MCDClient()
    print("NOW:", json.dumps(get_now(c), ensure_ascii=False)[:300])
    stores = list_nearby_stores(c)
    print("STORES:", json.dumps(stores, ensure_ascii=False)[:600])
