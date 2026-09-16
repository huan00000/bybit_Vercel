# Telegram Bybit Bot · Docker

Python 长轮询机器人，无需公网端口、域名或 HTTPS。保留 `/id`、`/status`、`/buy`、`/sell`；买卖固定 Bybit 正式网现货 `USDTUSD`，每次 `1 USDT`，使用真实资金。

## 本机或服务器直接运行

要求：Linux 容器运行环境、Docker Engine 和 Docker Compose **2.30.0+**（使用 raw env_file）；Windows 可使用 Docker Desktop 的 Linux containers。

1. 复制 `.env.example` 为 `.env`，填写 Bot token、用户名、用户白名单和 Bybit 正式网密钥。已有 `.env` 可继续使用，多余的 `TELEGRAM_WEBHOOK_SECRET` 会被长轮询入口忽略。
2. 停止同一个 token 的旧 Vercel 服务或其他机器人实例。启动时会调用 `deleteWebhook` 切换到轮询，不丢弃积压消息；超过 120 秒的交易指令会拒绝执行，近期指令仍可能执行。
3. 在项目目录执行：

```sh
docker compose -p tg-bybit-bot up -d --build --wait
docker compose -p tg-bybit-bot logs --tail 100 -f
```

私聊 `/id` 查看 ID，加入 `ALLOWED_TELEGRAM_IDS` 后重新执行 `up -d`，再用 `/status` 检查。`/buy`、`/sell` 会提交真实订单。若 Bybit 不支持交易对或最小数量，会直接返回错误，不自动换币或增量。

停止：`docker compose -p tg-bybit-bot down`。不要加 `-v`，否则会删除消息进度卷。健康检查仅代表最近一次 Telegram 轮询和消息处理完成，不代表交易权限、余额或交易对有效。Docker 自动重启退出的进程；单纯 unhealthy 不会触发自动重启。

## GitHub Actions 构建：build.yml

文件：`.github/workflows/build.yml`。

- 推送 `main`、`bybit`、`v*` 标签或手动触发：运行测试并发布 GHCR 镜像。
- Pull Request：测试并构建，不发布。
- 镜像为 `ghcr.io/<小写所有者>/<小写仓库名>`，支持 amd64/arm64；包含分支/版本标签和 `sha-<完整提交 SHA>` 标签。
- 使用仓库 `GITHUB_TOKEN`，无需填写 Docker Hub 密码；仓库 Actions 必须允许所需的 packages 写入权限。
- 成功后打开工作流 Summary，复制 `sha256:...` 摘要供部署使用。按摘要部署可避免标签移动造成版本不确定。

## GitHub Actions 部署：run.yml

文件：`.github/workflows/run.yml`，仅手动触发，不会在 push 时自动上线。

### 服务器一次性准备

1. 在目标 Linux 服务器安装 Docker 和 Compose 2.30.0+。
2. 按仓库 Settings → Actions → Runners 提示注册 self-hosted runner，添加自定义标签 **tg-bot**，以服务运行。该 runner 用户必须能够运行 Docker、读取下面的环境文件；只给目标服务器添加此标签。
3. 建立 `/opt/tg-bybit-bot/.env`，内容参考 `.env.example`。仅允许管理员和 runner 用户读取（例如 owner 为 runner 用户、权限 600）。密钥留在服务器，不放在仓库或 Actions 输入中。
4. 在 GitHub 创建 `production` Environment。将工作流放在默认分支，使 Actions 页面显示手动执行按钮。GHCR 镜像须允许该仓库的 Actions 读取；同仓库构建发布通常会自动关联。
5. 停止同 token 的旧服务。若曾在本服务器手动运行本项目，使用相同项目名 `tg-bybit-bot`，避免另起一套轮询容器。

### 发布与更新

先运行 Build Docker image，成功后在 Deploy Docker bot → Run workflow 填入构建 Summary 中的 **sha256:...**，选择包含本部署配置的分支。

部署会登录 GHCR、先拉取指定摘要镜像，然后替换容器，等待健康检查通过。不会构建镜像或发送测试交易。检查失败时工作流失败，不自动回滚；需要回滚时重新填入上一个成功摘要运行即可。旧摘要需仍保留在 GHCR。

镜像通过 `BOT_IMAGE` 传入 Compose；生产环境文件固定为 `/opt/tg-bybit-bot/.env`。需要更改路径或 runner 标签时修改 `run.yml` 对应字段。

服务器查看日志：

```sh
docker logs --tail 100 -f tg-bybit-bot-bot-1
```

## 消息进度与失败处理

消息进度保存在 Docker 卷 `tg-bybit-bot_bot-data`，按 Telegram Bot ID 隔离。在处理每条消息前先将下一个 offset 原子写盘，再执行命令。这样进程崩溃不会自动重放已进入处理的交易；代价是写盘后、执行前崩溃可能漏掉该命令。出现无回复时先查看 Bybit 订单，再决定是否重新发指令。

交易仍使用从 Bot ID 和 update ID 派生的稳定 `orderLinkId`。不承诺 exactly-once，不自动重试下单。卷内文件锁阻止共享卷的多个轮询进程；同 token 必须仅运行一个实例，不能通过多个独立卷/服务器水平扩容。网络错误等待 5 秒重试；认证失败或轮询冲突退出并记录不含 token 的错误。

镜像以非 root 用户运行，根文件系统只读；构建上下文只包含 Dockerfile、依赖和两个程序文件，不包含 `.env`、Git 历史或测试数据。`bot.py` 保留旧 Flask 适配层用于回归测试，容器只启动 `polling.py`，不监听 HTTP 端口。旧企划书为历史资料，以本 README 为准。

## 验证

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试模拟 Telegram / Bybit 网络，不会发消息或下单。

官方参考：[Telegram getUpdates](https://core.telegram.org/bots/api#getupdates)、[GitHub 发布 Docker 镜像](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)。
