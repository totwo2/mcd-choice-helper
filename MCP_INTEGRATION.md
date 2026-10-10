# MCP 接入说明（MCP_INTEGRATION.md）

> 本文件为参赛必须项，说明本项目实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。

## 1. 使用的 MCP Server

- **服务方**：麦当劳中国官方 MCP（`M-China/mcd-mcp-server`）
- **接入地址**：`https://mcp.mcd.cn`（Streamable HTTP）
- **认证**：请求头 `Authorization: Bearer <YOUR_MCP_TOKEN>`，Token 在 `https://open.mcd.cn/mcp` 用手机号申请
- **限流**：600 次/分钟（Token 维度）

## 2. 实际调用的 Tool（及用途）

| Tool | 调用场景 | 在本项目中的作用 |
|---|---|---|
| `now-time-info` | 每次打开 | 判定早/午/晚/夜时段，决定推荐偏向 |
| `query-nearby-stores` | 用户给位置/门店时 | 获取 `storeCode` 作为后续入参 |
| `query-meals` | 拿到门店后 | 拉取当前门店可售菜单（分类/编码/价格/标签） |
| `auto-bind-coupons` | 推荐前 | 一键领取麦麦省可用券 |
| `query-store-coupons` | 推荐前 | 查当前门店+订单类型可用券 |
| `query-my-coupons` | 推荐前 | 查用户卡包已有券 |
| `available-coupons` | 推荐前 | 查可领券列表（兜底） |
| `order-list` | 可选 | 历史订单 → 提取口味偏好 |
| `calculate-price` | 组合确定后 | 校验实付价与优惠，避免口头估算 |

> 未使用：`create-order` / `cancel-order` / 商城与派对类工具——下单需用户显式确认，本助手默认不动用户钱包。

## 3. 调用流程

```
now-time-info ──► 时段
query-nearby-stores ──► storeCode
query-meals(storeCode) ──► 菜单
auto-bind-coupons + query-store-coupons + query-my-coupons ──► 券池
                                          │
                          （可选）order-list ──► 口味偏好
                                          ▼
                        recommend(菜单, 券池, 预算?, 时段, 偏好)
                                          ▼
                        calculate-price(选定组合) ──► 校验实付/优惠 ──► 输出 Top3
```

## 4. 业务价值

- **降低决策成本**：选择困难用户打开即得结论，不用自己比对菜单与券。
- **真实省钱**：券+组合一起算，避免"领了券却没凑到门槛"或"为凑门槛买了一堆不需要的饮料"。
- **个性化**：有历史订单时按口味偏好推荐，无历史时按时段兜底，体验不空。
- **安全**：默认只读+算价，下单需用户确认，不碰支付。

## 5. 配置示例（WorkBuddy 连接器 / 或本地 mcp_client.py）

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_TOKEN}" }
    }
  }
}
```

> 注意：提交材料中**不包含真实 Token**，统一用 `${MCD_TOKEN}` 环境变量占位符。
