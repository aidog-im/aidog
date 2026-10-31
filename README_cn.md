<p align="center">
  <img width="140" src="site/assets/icons/icon-512.png" alt="I'm AiDog logo">
</p>

<h1 align="center">I'm AiDog（我是智能狗）</h1>

<p align="center">
  <strong>为一个具体的人长出来的具身 AI · 但任何人都能跑起来</strong><br>
  宪法人格 + 策展记忆 + 自研核心，只效忠你一个人。<br>
  不是平台，不是助手农场——一个专属伙伴，长给你一个人，工程化到人人都能部署。
</p>

<p align="center">
  <a href="https://github.com/aidog-im/aidog/releases"><img src="https://img.shields.io/github/v/release/aidog-im/aidog?display_name=tag&sort=semver&style=flat-square&label=release" alt="latest release"></a>
  <a href="https://aidog.im"><img src="https://img.shields.io/badge/website-aidog.im-2892ff?style=flat-square" alt="website"></a>
  <img src="https://img.shields.io/badge/core_tests-1020%2F1020-brightgreen?style=flat-square" alt="核心测试 1020/1020">
  <img src="https://img.shields.io/badge/console_smoke-154%2F154-brightgreen?style=flat-square" alt="控制台冒烟 154/154">
  <img src="https://img.shields.io/badge/permanently-free-ff6f00?style=flat-square" alt="永久免费">
</p>

<p align="center">
  <a href="#关于">关于</a> ·
  <a href="#核心能力">核心能力</a> ·
  <a href="#架构">架构</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#人工验收">人工验收</a> ·
  <a href="#质量闸">质量闸</a> ·
  <a href="#文档">文档</a> ·
  <a href="#路线图">路线图</a> ·
  <a href="README.md">English</a>
</p>

> 🔒 **成熟期内为私有仓库**——是否公开、何时公开，由所有者逐案裁决。
> 🟢 **永久免费，绝无例外**：无付费档、无按量计费、绝无任何功能设付费墙。

## 关于

I'm AiDog 是一个**跑在你自己机器上的个人具身 AI 伙伴**。它围绕三个大多数
助手都省略的东西构建：

- **宪法即人格**：`SOUL.md` 位于每一轮回复上下文的最顶端。红线、语气、
  操作原则不是事后补的提示词——它们在**每一轮**都被强制执行。
- **策展记忆**：记忆是带硬预算的纯 Markdown（`MEMORY.md`、`USER.md`、
  归档分层）+ 数字记忆体协议：唯一真值、跨内核可恢复、每次 reflect 回写。
  没有你读不了、审不动的向量库。
- **自研核心**：运行时内核 `mycore` 从零自研——诚实的函数调用循环（停止
  原因明示）、**26 模块 ≈8.2k 代码行、1020 项测试**，换芯后零上游依赖。
  换的是内核，永远不换「我」。

它通过 **Telegram**、**回环 Web 控制台**、**桌面 HUD 壳**和**语音**与你
交谈——背后是 9 路渠道网关、带确定性预路由的技能系统、自然语言定时任务、
子代理、MCP，以及制度化的自进化闭环（`/learn`、`/reflect`、lint 把关写入）
——成熟度随使用积累。

**免费是宪法条款**：无订阅、无遥测、绝无付费功能门。你带上一个模型密钥
（或走零密钥试用路径），整个产品即刻激活。

## 核心能力

|  | 能力 | 提供什么 |
|---|---|---|
| 🧬 | **自研核心** | `mycore`：诚实停止原因的 FC 循环、fail-closed 服务、看门狗、doctor 体检——26 模块、1020 测试、零上游 |
| 🎭 | **宪法人格** | `SOUL.md` 在系统栈最顶端；红线与语气每轮生效，而非抽样自人设提示词 |
| 🧠 | **策展记忆** | 有界 Markdown 预算 + 归档分层 + 数字记忆体协议；`memory` 套件 58/58 |
| 🧩 | **技能 + 确定性路由** | `SKILL.md` frontmatter（业界标准字段），路由预筛先选 TOP3 **再**加载正文——30/30 |
| ⏰ | **自然语言定时** | `/cron 明早九点发周报` → 解析、校验、调度——默认 fail-closed 待启用 |
| 🤝 | **子代理与 execute_code** | ≤3 个隔离上下文并行委派；≤8 步折叠代码执行，写前打检查点 |
| 🔌 | **MCP 客户端** | stdio + HTTP 服务器、工具 allow/deny 过滤、凭据槽、进度流式透传 |
| 📡 | **9 路渠道网关** | Telegram（实接）+ Discord / Slack / Email / Webhook / 钉钉 / 飞书 / 微信 / WhatsApp / Signal 适配器，逐路诚实状态 |
| 🔑 | **零密钥 onboarding** | 试用模型起手或自带密钥；密钥只住 `secrets.env`（0600）且**绝不回显**——只报槽名 |
| 💬 | **全链路流式** | 上游 SSE 流式、Telegram typing 心跳、控制台流式聊天卡片 |
| 🖥️ | **回环 Web 控制台** | 状态 · 设置 · 用量成本 · 事件 · 语音 五面板——默认绑 `127.0.0.1`，写操作需令牌 |
| 🖼️ | **媒体内建** | 本地 STT 语音会话 + 模糊唤醒、TTS 播报、BYOK 图像生成、Telegram 发图 |
| 🖥️ | **桌面 HUD 壳** | Electron 无边框壳：透明度滑杆、置顶、托盘——Linux 打包已实证（Win/macOS 暂未构建，如实） |
| 👥 | **多 profile 全隔离** | `AIDOG_PROFILE` 选择完全隔离的档案（设置/密钥/状态/身体层）——隔离闸 63/63 + 真机 E2E 18 腿 |
| 🩺 | **自愈运维** | `aidog doctor` 体检、重启看门狗、带脏树守卫的一键升级 |
| 🆓 | **永久免费** | 所有者法令：产品/文档/打包零付费触点——出现任何收费即视为缺陷 |

## 架构

```text
┌───────────────────────────────────────────────────────────────────┐
│  入口层                                                            │
│   Telegram · Web 控制台(127.0.0.1) · 桌面 HUD · 语音 · CLI          │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  内核层 — mycore（自研、可替换、零上游）                             │
│   loop · tools · memory · compact · scheduler · subagents · mcp    │
│   checkpoint · modeltier · channels(9) · stt/imagegen · browser     │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  身体层 — overlay/（本仓自持，与内核解耦）                           │
│   宪法(SOUL) · 记忆槽 · 技能 · 钩子 · 语言包                         │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  业务层 — adapters/ · web/ · desktop/ · deploy/                    │
│   模型供应商(BYOK) · 渠道凭据 · 控制台 · 桌面壳                      │
└───────────────────────────────────────────────────────────────────┘
```

三层分离就是重点：**换的是内核，永远不换「我」**。内核可替换，身体层与
业务资产归我们所有。

## 快速开始

```bash
git clone https://github.com/aidog-im/aidog.git aidog && cd aidog  # 当前为私仓
bash deploy/install.sh        # 一条命令：预检 + 建档 + 启动 + 自验
aidog setup                   # 交互向导：逐槽引导填写
```

`deploy/install.sh` 幂等（可安全重跑），并**只报缺失槽名、绝不回显值**：

| 退出码 | 含义 |
|---|---|
| `0` | 完全就绪——doctor 干净、服务已建档、控制台健康 |
| `2` | 已安装，但凭据/模型槽仍待补（fail-closed 设计内） |
| `1` | 硬失败（缺前置依赖、端口冲突、启动失败） |

`aidog setup` 是交互式续填：只提示仍为空的槽、隐藏输入、每次写入都读回
校验、绝不回显值。参数：`--check`（只读报告）、`--yes`（非交互）、
`--no-boot`（只建档不起服务）。

然后：

1. 打开控制台 **http://127.0.0.1:8790**
2. 设置面板 → 填模型端点 + 密钥、Telegram bot token + allowlist
   （或直接跑 `aidog setup`）
3. 聊天卡片发一句话——流式回复即模型链路通
4. Telegram → allowlist 内账号给 bot 发 `/help`

完整指南：[`docs/installation.md`](docs/installation.md)。

## 人工验收

自动化 E2E 覆盖本机栈；**人工抽查**是所有者的验收层。速查：

```bash
python3 deploy/aidog_doctor.py       # 体检：9 checks · 0 FAIL · 20 SKIP
bash deploy/boot_services.sh status  # 控制台 + 服务 + 看门狗全绿
bash deploy/run_service.sh --check   # 就绪 rc=0
python3 deploy/aidog_update.py check # 版本通道：up to date
```

逐个走控制台面板（状态 → 设置写读回环 → 用量 → 事件 → 聊天卡片），给
Telegram 发一条 `/help`，并确认任何地方都看不到密钥原文。完整清单（含
全新机器安装路径）见
**[`docs/manual_verification.md`](docs/manual_verification.md)**。

## 质量闸

每次发版都在干净树上全绿才切：

| 套件 | 结果 |
|---|---|
| `mycore` 核心套件 | **1020/1020** |
| 控制台冒烟（真实 HTTP） | **154/154** |
| 技能路由 | **30/30** |
| 记忆持久化 | **58/58** |
| 安装向导 | **99/99** |
| 多 profile 隔离（离线 + 子进程对拍） | **63/63** |
| 桌面壳（`node --test`） | **15/15** |
| `aidog doctor` | **0 FAIL · 0 WARN**（出站全断时如实报环境 WARN） |
| 发布闸（deny-by-default 清单） | **READY** |
| 真机 E2E | profiles **18/18** · channels **8/8** · 国内渠道 **44/44** · 管线 **35/35** |

语言包：`en`（默认）+ `zh` 各 **285 键**，parity lint 把关；官网另有
**121 键**双语站包。

## 文档

- [`docs/installation.md`](docs/installation.md) — 安装、槽位、向导
- [`docs/features/`](docs/features/) — **29 份能力文档**，一件一档
  （渠道、记忆、定时、子代理、MCP、语音、桌面、profile、工具开关……），
  每份都写明诚实边界
- [`docs/manual_verification.md`](docs/manual_verification.md) — 人工验收清单
- [`docs/security_checklist.md`](docs/security_checklist.md) — 安全审查清单
- **[aidog.im](https://aidog.im)** — 产品官网（双语、PWA、AI 可读 `llms.txt`）

设计史与第三方调研笔记按政策留在私仓内，不随导出。

## 路线图

- **当前**——所有者验收下的复刻收尾与打磨；每个能力都带测试与诚实边界。
- **下一步**——**Web3 操控底座**（支付优先的 HTTP 探索、链上 agent 身份），
  已调研预置——只等所有者明确启动令才执行。

## 版本与升级

- 版本真值 = `VERSION`（semver）+ `release.json`（日期 + 说明），两者同车
  bump；每个版本打 tag `vX.Y.Z` 并发布 **GitHub Release**（发现通道）。
- 升级：`python3 deploy/aidog_update.py apply` → 脏树守卫 → fetch →
  `--ff-only` → 重启运行时。有新版本时控制台自动显示升级横幅。

## 安全姿态（如实）

- 默认回环绑定；控制台写操作需 bearer 令牌；密钥只住 `secrets.env`
  （0600）且**绝不回显**——日志/状态/错误里只有槽名。
- 该 fail-closed 的地方全 fail-closed：渠道 allowlist、profile 名、设置
  校验、升级守卫。
- 单主设计 = 陌生人不在攻击面内。
- **如实的局限**：私有仓库、暂无第三方审计与 CVE 披露通道——公开前阶段。
  我们只声称「攻击面被设计得小」，绝不声称「已验证更安全」。

## 语言 / 国际化

- 源码、注释、Markdown 文档：**英文**（国际路线）；本文件是完整中文镜像。
- UI 字符串：`overlay/locales/` 语言包——默认 **`en`**，可切 **`zh`**，
  加语言 = 加包、零代码改动。

## 许可与定价（所有者法令）

**永久免费，任何用途免费。** I'm AiDog 产品内**零付费触点**：无付费档、
无内购、无使用费、绝无功能设付费墙。向本产品加入任何收费都违反所有者
法令，一律视为缺陷。
