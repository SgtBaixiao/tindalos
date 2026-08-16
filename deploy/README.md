# Tindalos VPS 部署手册（IP 直连 · 无域名）

在轻量云服务器上把本仓库部署成**随时可访问的完整站点**：一键装好 Python 后端 + React 前端 +
DeepSeek 云端大模型，浏览器访问 `http://<服务器IP>:8347` 即可使用。**不需要域名**，IP 直连模式。

> 本手册面向"照着做就能跑通"的读者：每个命令都写全，直接复制粘贴即可。

---

## 0. 采购前提（简述）

- **云服务器**：腾讯云 / 阿里云 **轻量应用服务器**，**2 核 4G** 起步（内存越大，前端构建越顺）。
- **系统**：**Ubuntu 22.04**（本手册所有命令都基于它）。
- **节点**：**香港节点免备案**，买完开箱即用；**国内节点需要 ICP 备案**才能通过 80/443 被公网访问，
  建议直接用香港或海外节点省去备案。
- 服务器上需要 **Docker**（含 `docker compose` 插件）。第 4 步的脚本会自动检查，缺失时会打印一键安装命令。

---

## 第 1 步：把仓库推到 GitHub

在本机仓库目录执行（如果还没推过）：

```bash
git push origin main
```

- 仓库地址（公网）：`https://github.com/SgtBaixiao/tindalos.git`
- 确保 `deploy/` 目录下的 `.env`（含 API key）**没有被提交**——仓库 `.gitignore` 已排除
  `deploy/.env`，正常 `git push` 不会带上去。

---

## 第 2 步：在服务器上拉取仓库

SSH 登录服务器，然后克隆仓库：

```bash
# 先装好基础工具（首次登录建议执行）
sudo apt-get update && sudo apt-get install -y git curl

# 克隆仓库（放到用户主目录即可）
git clone https://github.com/SgtBaixiao/tindalos.git
cd tindalos
```

---

## 第 3 步：配置 DeepSeek API Key（deploy/.env）

在仓库根目录执行：

```bash
cp deploy/.env.example deploy/.env
```

然后用编辑器打开 `deploy/.env`，把这一行的占位内容换成你的真实 key：

```bash
nano deploy/.env
```

```dotenv
# 把下面这一行的 sk-在这里填你的DeepSeekKey 换成你的真实 DeepSeek API key
TINDALOS_API_KEY=sk-在这里填你的DeepSeekKey
# 以下四项一般不用改
TINDALOS_API_BASE=https://api.deepseek.com/v1
TINDALOS_MODEL=deepseek-chat
TINDALOS_LLM_ENABLED=1
TINDALOS_STYLE_GUIDE=1
```

- DeepSeek key 在 [platform.deepseek.com](https://platform.deepseek.com) 申请。
- **绝不要提交 `.env`**：它含真实凭据，仓库 `.gitignore` 已排除；改完 key 也不要 `git add deploy/.env`。

> 提示：第 4 步的脚本在 `deploy/.env` 不存在时也会自动从 `.env.example` 生成，并**交互式**询问
> 填入 API key（输入不可见）。所以哪怕跳过本步，脚本也会帮你补上——但建议先手动填好更省事。

---

## 第 4 步：一键部署（装依赖 + 构建 + 启动 + 健康检查）

在**仓库根目录**执行（注意是 `deploy/deploy.sh`，脚本里已做路径定位，请在仓库根运行）：

```bash
chmod +x deploy/deploy.sh
./deploy/deploy.sh
```

脚本会自动完成：

1. 检查 `git` / `docker` / `docker compose` 是否就绪（缺失时打印安装指引）；
2. 读取 `deploy/.env`（缺失则自动生成并询问 API key）；
3. `docker compose` 构建镜像并后台启动服务（端口 **8347**）；
4. 轮询健康检查 `http://127.0.0.1:8347/api/health`（最长 60 秒）；
5. 成功后打印访问地址。

首次构建要拉基础镜像 + 装 npm / pip 依赖，**需要几分钟**，请耐心等待。

---

## 第 5 步：访问站点

部署成功后，在浏览器打开：

```
http://<服务器IP>:8347
```

- 服务器 IP 就是轻量服务器的公网 IP（脚本成功输出里也会打印）。
- 本机调试用 `http://127.0.0.1:8347`。
- 页面打不开？先看下文【故障排查】和【安全须知】里的防火墙放行。

---

## 日常运维

所有命令都在**仓库根目录**执行（`cd ~/tindalos`）。

| 操作 | 命令 |
|---|---|
| 查看日志（实时跟踪） | `docker compose -f deploy/docker-compose.yml logs -f` |
| 重启服务 | `docker compose -f deploy/docker-compose.yml restart` |
| 停止服务 | `docker compose -f deploy/docker-compose.yml down` |
| 查看容器状态 | `docker compose -f deploy/docker-compose.yml ps` |
| **更新到最新代码** | `git pull` 然后重新执行 `./deploy/deploy.sh` |

> `./deploy/deploy.sh` 是幂等的：重复运行会重建镜像并重启服务，不会清空数据。

> ⚠️ **`restart` ≠ 重新加载配置**：`restart` 只重启进程，环境变量照旧。凡是改了 `.env`（API key、模型名等）都要用 `up -d` 重建容器才生效。

---

## 数据备份与恢复（重要）

所有用户数据（剧本、备团笔记、记忆库、检查点等）都存在 Docker 命名卷 **`tindalos_data`** 里，
挂在容器的 `/app/data` 目录。**重装系统 / 删容器都不会丢**，但建议定期备份。

### 备份（一键打包到当前目录）

在仓库根目录执行：

```bash
docker run --rm -v tindalos_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/tindalos-data-$(date +%F).tar.gz -C /data .
```

执行后会在当前目录生成一个 `tindalos-data-YYYY-MM-DD.tar.gz`，把这个文件下载/拷贝到安全的地方即可。

> 小贴士：想拿**一致快照**，可先 `docker compose -f deploy/docker-compose.yml down` 停服再备份，
> 备份完再 `./deploy/deploy.sh` 拉起来。平时低流量下直接备份也基本没问题。

### 恢复

```bash
# 1. 先停掉服务（避免写入冲突）
docker compose -f deploy/docker-compose.yml down

# 2. 把备份包解压回卷里（把文件名换成你的实际备份文件）
docker run --rm -v tindalos_data:/data -v "$PWD":/backup alpine \
  sh -c "cd /data && tar xzf /backup/tindalos-data-YYYY-MM-DD.tar.gz"

# 3. 重新启动
./deploy/deploy.sh
```

---

## 安全须知

- **API key 只走 `.env`**：DeepSeek key 写在 `deploy/.env`，绝不入库、绝不写进代码或文档；
  脚本会把 `.env` 权限设为 `600`（仅属主可读写）。
- **防火墙放行 8347**：公网访问需要放行 TCP 端口 8347。
  - 服务器系统防火墙（UFW）示例：
    ```bash
    sudo ufw allow 8347/tcp
    ```
  - **轻量服务器控制台**（腾讯云 / 阿里云）通常还有一层"防火墙 / 安全组"，也要在控制台里
    加一条放行 `8347` 的规则（来源 `0.0.0.0/0`），否则系统防火墙开了也进不来。
- **HTTPS 是后续可选**：当前 IP 直连走 **HTTP**，数据不加密。无域名时只能这样；
  建议仅在内网 / 可信网络使用，或后续绑定域名后接 HTTPS（反向代理 + Let's Encrypt）。

---

## 故障排查

| 现象 | 排查 |
|---|---|
| **页面打不开** | 先看防火墙：`sudo ufw status` 确认 8347 已放行；再去**云控制台安全组**确认放行 8347；最后确认服务在跑：`docker compose -f deploy/docker-compose.yml ps` |
| **部署脚本健康检查超时** | 脚本失败输出里会带最近日志；手动看：`docker compose -f deploy/docker-compose.yml logs --tail=100` |
| **页面能开但生成一直无响应 / 一直转圈** | 很可能 LLM key 没生效或 key 无效。看后端日志确认：
  `docker compose -f deploy/docker-compose.yml logs -f`
  重点看有没有 `TINDALOS_API_KEY` 相关告警、HTTP 401/429/5xx 报错。确认 `deploy/.env` 里的 key 填对了、没有留占位符。 |
| **生成回退成了模板化结果** | LLM 调用失败时会按设计回退到离线确定性模板（服务本身不崩）。看日志定位失败原因（key 无效 / 余额不足 / 模型名错误）。 |
| **改完 .env 不生效** | 环境变量在容器**创建时**固化，`restart` **不会**重新读取 env_file。改完 key 必须**重建容器**：`docker compose -f deploy/docker-compose.yml up -d`（或重跑 `./deploy/deploy.sh`）。这是实测踩过的坑：`restart` 后服务还在用旧 key。 |
