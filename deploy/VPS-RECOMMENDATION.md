# 网站部署方式与 VPS 推荐

> 结论：**不需要额外的静态托管**。Tindalos 单进程已同时充当「网站 + 后端」——
> FastAPI 统一服务前端静态页（`frontend/dist`）+ `/api/*` + `/files/*`。
> 「部署网站」 = 「把 Tindalos 部署到一台 VPS」。推荐 **腾讯云 / 阿里云轻量应用服务器（香港，2 核 4G，Ubuntu 22.04）**，IP 直连，`http://<IP>:8347` 访问，免备案。

---

## 1. 为什么不用静态托管 / Serverless（先说清楚）

| 方案 | 为什么不适合 |
|---|---|
| GitHub Pages / Vercel / Netlify / Cloudflare Pages | 纯静态。本应用是**有状态全栈**：SQLite 历史库、上传的 PDF 与提取图（落盘到卷）、RAG 索引（Chroma + BM25）、SSE 流式生成、DeepSeek 出站调用——全都要服务端进程。静态托管只能摆个落地页，承载不了实际功能。 |
| 云函数 / Serverless | 理论上能跑，但 RAG 索引、文件存储、~872MB 容器、长连接 SSE 与 Serverless 模型摩擦大（冷启动、存储、限制多），运维复杂度远高于一台 VPS，还未必更便宜。 |
| **轻量云 VPS + Docker（推荐）** | 一台机器、一个容器、命名卷持久化、DeepSeek 走云端 API。与已建产物（`deploy/`）完全匹配，`./deploy/deploy.sh` 一键起。成本最低、运维最简单。 |

---

## 2. VPS 推荐（2026-08 参考价，以官网实时活动为准）

### 首选：腾讯云轻量应用服务器 · 香港 · 2 核 4G

| 档位 | 价格 | 带宽/流量 | 适合 |
|---|---|---|---|
| 新用户促销 | **约 36 元/月** | 50Mbps 峰值 / 1.2TB 月流量 | 预算优先，日常够用 |
| 官方降价后主力 | **约 95 元/月** | 200Mbps 峰值 / **不限流量** | 模块插图多、常看 /files 图，省心 |

- 2025 年 9 月腾讯云对香港地域多款套餐**官方降价**，现在是入手时机。
- 建议：新用户先领「新用户专享-香港节点加速包」再下单。

### 备选：阿里云轻量应用服务器 · 香港 · 2 核 4G（国际型）

| 档位 | 价格 | 带宽/流量 |
|---|---|---|
| 国际型 2 核 4G | **约 78 元/月** | 200Mbps 峰值 / **不限流量**，ESSD 50GB |

- 阿里云国际型走 **BGP（非中国优化）**：大陆访问该线路**可能不理想**；若主要从大陆访问，优先腾讯云香港或阿里云「通用型」（BGP 优化）档位。

### 配置理由（为什么是 2 核 4G）

- Tindalos 空闲占用极小（SQLite 单用户、本地 RAG），2C4G 是**舒适下限**：容器 + 前端构建（`npm ci` 首次会吃内存）+ Chroma 索引一起跑不紧张。
- 更低的 2C2G 能跑但偏紧；更高的 4C8G（约 156 元/月）对你当前用途是浪费。
- **同一台 2C4G 还能顺带跑你的个人 sandbox**（之前调研的 harness 优化方向）：Docker 在宿主机上，Tindalos 常驻占用小，剩余资源足够跑轻量 agent 容器。

---

## 3. 线路质量诚实提示

- 大陆 → 香港的访问速度**因具体 IP 而异**，同一家不同实例差别可能很大（有测评显示腾讯云港 IP 三网直连去程、但联通回程绕路）。
- 对本应用的实际影响有限：**生成耗时的瓶颈是 DeepSeek 云端 API 的出站调用**（~34s 里绝大部分是等 DeepSeek 返回），不是 VPS 线路；VPS 线路只影响页面加载、模块插图（`/files`）和本地 RAG 检索。
- 建议下单后先 `ping <IP>` + 浏览器开首页粗测；不满意再退换（轻量支持退款/更换）。

---

## 4. 购买要点清单（照做）

1. **地域**：香港（免备案，开箱即用）。国内节点要 ICP 备案，不做。
2. **系统**：**Ubuntu 22.04**。轻量购买页一般可选；**下单时就把系统选成 Linux**（腾讯云香港不支持 Linux/Windows 互换，先选 Linux 省后续折腾）。
3. **下单前**：领新用户券 / 加速包；年付通常更便宜。
4. **购买后**：云控制台「防火墙/安全组」放行 **TCP 8347**（来源 `0.0.0.0/0`），否则外部进不来。
5. 打一个**系统盘快照**作为保险（轻量控制台一键快照）。

---

## 5. 部署（照 `deploy/README.md` 走，最快路径）

```bash
# SSH 登录后
sudo apt-get update && sudo apt-get install -y git curl
git clone https://github.com/SgtBaixiao/tindalos.git
cd tindalos
nano deploy/.env                 # 填真实 DeepSeek key（绝不要提交）
chmod +x deploy/deploy.sh
./deploy/deploy.sh               # 装依赖 + 构建 + 启动 + 健康检查，一条龙
```

浏览器打开 `http://<服务器IP>:8347` 即可用。**验收按 `deploy/TEST-REPORT.md` 第 5 节清单逐条核对**（尤其第 7 步持久化：`down` + `up` 后数据还在 = 修复生效）。

---

## 6. 后续可选升级（本次不做，留作路线图）

- **绑域名 + HTTPS**：IP 直连是 HTTP 明文，个人用可接受；想升级就买域名 → 反向代理（nginx/Caddy）→ Let's Encrypt 证书，几分钟的事。
- **接入个人 sandbox**：同一台 VPS 上再起 Docker 容器跑 agent harness，与网站协同（你在 `deploy/README.md` 安全须知里已看到 8347 放行方式，sandbox 另行开端口即可）。
- **备份自动化**：`deploy/README.md` 已给出卷备份命令（`docker run --rm -v tindalos_data ...`），可挂 cron 每周自动打 `tindalos-data-YYYY-MM-DD.tar.gz`。

---

## 参考价格来源

- 腾讯云香港轻量价格测评与促销：[腾讯云香港服务器测速指南](https://www.177idc.com/post/1632.html)、[腾讯云香港服务器租用价格信息](https://www.cnzhuji.com/article/57_120963.html)、[腾讯轻量云中国香港测评](https://zhaogeyun.com/deals/ip-1781424084019)、[做跨境电商选腾讯云香港服务器](https://www.xymww.com/zuo-kua-jing-dian-shang-xuan-teng-xun-yun-xiang-gang-fu-wu.html#1)
- 阿里云香港轻量价格：[阿里云2026年香港服务器价格更新](https://developer.aliyun.com/article/1714003)、[阿里云香港服务器最低价格](https://developer.aliyun.com/article/1713215)、[阿里云香港服务器真实收费价格](https://developer.aliyun.com/article/1712992)
- 2026 香港服务器机型盘点：[kfglobal](https://www.kfglobal.hk/blog/kuifangnews-20260616)
