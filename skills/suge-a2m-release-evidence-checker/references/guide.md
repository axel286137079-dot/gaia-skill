# 记录字段、判定规则与报告规范（A2M 上线证据验收）

## 1. 输入格式（脱敏后的 HTTP 交换记录）

```json
{
  "environment_label": "local|sandbox|staging|test|production|prod|live（如实填写，可省）",
  "claims": {"production_verified": false},
  "records": [
    {
      "id": "req-1",
      "phase": "payment_request | retry_with_proof | delivery | payment_validation | refund",
      "request": {
        "method": "POST",
        "url_path": "/v1/pay",
        "proof_present": false,
        "proof_token": "占位文本（脱敏；脚本只回显 hash）"
      },
      "response": {
        "status": 402,
        "payment_required": {"provider": "...", "expires_at": "...",
                             "resource_id": "...", "amount": "...", "currency": "..."},
        "delivery": {"delivered": true, "fulfillment": "confirmed", "resource_id": "...", "amount": "...", "currency": "...", "trade_no": "..."},
        "validation": {"valid": true, "resource_id": "...", "amount": "...", "currency": "...", "trade_no": "..."},
        "refund": {"refunded": true, "amount": "...", "trade_no": "..."},
        "error": "可选"
      }
    }
  ]
}
```

- `phase` 枚举：`payment_request`（首次请求，预期 402）、`retry_with_proof`（携证明重试）、`delivery`（交付响应）、`payment_validation`（验付）、`refund`（退款证据，可选观察项）。
- `payment_required` / `delivery` / `validation` 均为可空 dict，脚本只在这些 dict 内按白名单键取值；其余键与 body 其它文本一律忽略（含注入指令）。
- 输入上限 200 条记录、2MB。数字字段必须为整数/字符串；布尔字段必须为 true/false。

## 2. 逐跳核对（checks）

| 检查 | 通过条件 |
|---|---|
| initial_402 | 存在 status=402 且带 payment_required 数据的记录 |
| payment_bill_complete | 402 响应含 provider、expires_at、amount、currency、resource_id；Payment-Proof 在支付后生成，不要求出现在首次 402 中 |
| retry_with_proof | 存在携带证明的请求，且响应 200（retry/delivery/payment_request 均可） |
| delivery_200 | 存在带 delivery 数据且 200 的交付记录 |
| fulfillment_confirmed | delivery.delivered=true 或 fulfillment ∈ {confirmed, delivered, success, fulfilled, completed} |
| payment_validation | 存在 validation.valid=true 的验付记录 |
| settlement_consistent | 所有响应中的 amount/currency/resource_id/trade_no 跨记录一致（有值的字段才比） |
| replay_guard | 相同证明 hash 不得出现在多个不同 resource_id/order_id；同订单重复出现记 INFO（依赖服务端幂等） |

任一 FAIL 且属于一致性/重放类 → `FAIL`；缺链上的跳 → `BLOCKED`，`missing_steps` 列出缺哪跳。

## 3. 等级判定（不能混淆）

| level | 含义 |
|---|---|
| LOCAL_PASS | 环境标签 local 且链完整 |
| SANDBOX_PASS | 环境标签 sandbox/staging/test 且链完整 |
| PROD_NOT_PROVEN | 链不完整、环境缺失/未知、或声明 production 但证据不足——一律不得写"已上线" |
| PROD_PASS | 仅当环境标签 production/prod/live 且链完整（依据所提供记录） |

沙箱通过 ≠ 生产上线；缺任一跳生产证据不得宣称已上线；报告只代表用户提供的记录，不代表平台侧终态。

## 4. 脱敏规则

- `proof_token` 只回显 `sha256:摘要`，不保留任何原文前缀。
- Payment-Proof、Authorization、私钥、Access Token、完整用户标识不进入输出；只保留字段存在性、hash 或尾号。
- 用户应在准备输入时就地脱敏；脚本侧再次只做白名单读取。

## 5. 中文门禁报告结构（由代理依据 stdout JSON 生成）

1. 检查对象与用户声明的环境标签（未声明则注明）。
2. 结论框：verdict + level + 一句话理由。
3. 逐跳核对表：检查 / 状态 / 证据记录 id / 说明。
4. 一致性结论：可比对字段数、是否冲突、冲突详情。
5. 缺失步骤与建议补的证据；重放/幂等观察。
6. 声明：离线分析、未代付、仅代表所提供记录、生产最终验证由用户完成。

## 6. 限制

- 不做网络请求、不付款、不签约、不上传平台；不把 sandbox/local 结果当生产。
- 记录真实性由用户负责；本技能不能验证记录是否伪造。
- 不构成法律或财务意见；退款与费率口径以平台后台为准。
- 样例（references/sample.json）为合成沙箱链路演示，不承诺任何真实平台行为。
