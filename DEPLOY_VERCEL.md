# Vercel 版 Bybit Mainnet 机器人

当前版本已按实盘需求接入 Bybit 正式网，交易使用真实资金；旧企划书仅作历史记录。不需要 Docker 或常驻进程。
入口仍为 `bot.py:app`，由 `pyproject.toml` 指定；`vercel.json` 指定 Flask 和 60 秒函数时限。
本目录仅保留 Vercel 版本，原有 JavaScript 轮询、Docker 部署及镜像构建文件已移除。

## 功能

- `/id`：任何用户都可查询自己的 Telegram ID，便于配置白名单。
- `/status`：仅白名单私聊可用，实际调用 Bybit 正式网认证接口；连接成功不代表余额、权限和交易对均满足下单条件。
- `/buy`、`/sell`：仅白名单私聊可用，固定正式网现货 `USDTUSD`、市价、`1 USDT`（baseCoin）。卖出已有 USDT，不开空、不借币。
- 不支持交易对或最小数量/金额不满足时，返回 Bybit 错误码及原因，不自动换币、不增加数量。
- 返回“订单已受理”不代表成交；成交结果在 Bybit 正式网订单历史查看。

## 本地验证

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
```

测试使用模拟网络，不发送 Telegram 消息、不实际下单。
本地启动可用 `.\.venv\Scripts\python -m flask --app bot run`。
程序读取进程环境变量，不自动加载 `.env`。

## Vercel 部署步骤

1. 将本地改动审核、提交到 `bybit`，再推送该分支。不要合并到 `main`。
2. 在 Vercel 为新版建立独立项目，导入同一仓库，将 Production Branch 设为 `bybit`，Framework Preset 选 Flask，Root Directory 为仓库根目录。沿用原项目会影响旧机器人的部署，请使用独立项目和独立 Telegram Bot。
3. 在新项目 Production 环境添加下面的变量。不要把真实值写进代码或提交 Git；不要在聊天中发送密钥。

| 变量 | 内容 |
| --- | --- |
| BOT_TOKEN | 新机器人的 BotFather token |
| BOT_USERNAME | 机器人用户名，不含 @；用于识别 /buy@机器人用户名 |
| TELEGRAM_WEBHOOK_SECRET | 自行随机生成，1–256 位字母、数字、下划线或连字符 |
| ALLOWED_TELEGRAM_IDS | 允许操作的用户数字 ID，逗号或空白分隔 |
| API_KEY | Bybit **Mainnet** API key，具有现货交易权限 |
| API_SECRET | 对应 Mainnet API secret |

可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成 Webhook 密钥。
若还不知道自己的 ID，可以先完成 BOT_TOKEN 和 Webhook 密钥配置，注册 Webhook 后用 `/id` 查询，再补白名单并重新部署。

4. 部署后访问首页，应显示 MAINNET。确保公网可以访问 `/webhook`，不被 Vercel Deployment Protection 登录页拦截；只调整这个新项目。首页健康不表示 Bybit 已连接。
5. 在本机 PowerShell 设置 `$env:BOT_TOKEN`、`$env:TELEGRAM_WEBHOOK_SECRET` 为与 Vercel 一致的值，再注册 Webhook（把地址替换为新项目固定生产域名）：

```powershell
$webhook = 'https://YOUR-PROJECT.vercel.app/webhook'
$body = @{
  url = $webhook
  secret_token = $env:TELEGRAM_WEBHOOK_SECRET
  allowed_updates = @('message')
  drop_pending_updates = $true
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$($env:BOT_TOKEN)/setWebhook" -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri "https://api.telegram.org/bot$($env:BOT_TOKEN)/getWebhookInfo"
```

`drop_pending_updates` 会丢弃此机器人的积压消息，避免启用时执行旧交易指令。一个 token 只能注册一个 Webhook，也不能同时运行其他 Telegram 轮询程序。
6. 先私聊发送 `/id`、`/status` 检查配置。需要真实买卖时再发送 `/buy` 或 `/sell`，每条新指令都会提交真实订单。准备正式网真实资产：买入需 USD，卖出需 USDT。交易对可用性和最小量由正式网决定；USDTUSD 或 1 USDT 若不满足规则，交易所会拒绝订单，程序不会擅自换币或加大数量。
7. 用另一个用户测试 `/buy`，应被拒绝。Bybit 网络异常、地区访问限制或密钥权限问题需在实际 Vercel 部署中验证；请核实账户对应的 API 域名及部署地区是否受 Bybit 支持。

## 重复消息与失败处理

Webhook 校验 Telegram 的 secret_token 请求头。除公开的 `/id` 外，命令检查白名单；交易只接受 120 秒内的指令。
每个 Telegram update 生成稳定的、跨实例一致的 Bybit orderLinkId，重投使用同一个标识，由 Bybit 的唯一订单标识约束拒绝重复。
此方案不额外使用数据库，不承诺端到端 exactly-once，也不自动重试订单。网络超时可能发生在交易所已受理之后，机器人会提示“结果待确认”；先凭订单标识查看正式网订单记录，确认后再发新指令。
发送 Telegram 回复失败时仍确认收到 Webhook，避免因此重放交易；可能出现订单已受理但没有消息的情况。不会输出密钥或完整上游异常到日志。

## 官方参考

- [Vercel Flask 部署与入口配置](https://vercel.com/docs/frameworks/backend/flask)
- [Telegram setWebhook 与 secret_token](https://core.telegram.org/bots/api#setwebhook)
- [Bybit V5 签名与主网地址](https://bybit-exchange.github.io/docs/v5/guide)
- [Bybit 下单、marketUnit 和唯一 orderLinkId](https://bybit-exchange.github.io/docs/v5/order/create-order)

## Webhook 密钥的大白话解释

`TELEGRAM_WEBHOOK_SECRET` 是 Telegram 和你的网站约定的接头暗号，不是 Bybit 密钥，也不是 BotFather token。`replace_with_random_secret` 是“请替换成随机暗号”的占位文字。运行上面的随机生成命令，将结果填入 Vercel 的同名环境变量，注册 Webhook 时的 `secret_token` 也填相同的结果。两个地方不一致，网站就会拒绝消息。修改 Vercel 环境变量后需要重新部署；修改这个暗号后还需要重新注册 Webhook。
