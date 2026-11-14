# 巴别塔 · Tower of Babel

**Personal Multi-Agent Collaboration Hub | 面向个人研究者与开发者的多 AI 协同工作平台**

> **开源作者 / Author**：郑江羽（Zheng Jiangyu）  
> **单位 / Affiliation**：中国海洋大学 大气科学（Atmospheric Sciences, Ocean University of China）  
> **开源协议 / License**：[MIT License](LICENSE)

---

## 一、我们为什么要建「巴别塔」（计划设想）

在日常科研与工程开发中，很多高阶工作流已经从“单窗口问答”演变为**多模型接力**：
- 我们在网页端 **ChatGPT** 开了多个长期规划窗口（主课题思路、论文撰写、实验工程、职业发展），不断敲定方向和细节；
- 回到本地电脑，我们在 **Antigravity IDE（Gemini）**、**Claude Code（DeepSeek）**、**Codex（GPT）** 之间来回切换写代码、跑实验。

但目前存在四个普遍痛点：
1. **记忆割裂**：网页聊天记录留在浏览器里，本地执行 AI 看不到你和规划模型聊过的最新决策与否决原因。
2. **切换成本极高**：一旦某个模型额度耗尽，或者你想让 DeepSeek 接手 GPT 干到一半的任务，新模型只能从头摸索代码库，之前踩过的坑、跑过的命令和阶段结论全部丢失。
3. **盲目压缩费钱又失真**：为了同步上下文而让 AI 额外“写摘要”，不仅多烧额度，还会把关键约束改偏。
4. **权限与多窗口串线风险**：执行模型可能误改已经拍板的研究路线，或者在多课题并行时把问题问错窗口。

**「Tower of Babel（巴别塔）」的设想，就是打通这些“语言不通、记忆孤岛”的 AI 工具**：  
以你习惯的 **ChatGPT 多窗口作为最高规划中枢**，以一台超轻量云端中枢作为**统一记忆与任务总线**，让所有执行节点共享同一份结构化上下文、遵守同一套版本化正式决策，并能在额度耗尽或模型切换时无缝接力。

---

## 二、希望达成的核心目标

1. **零额外额度持续留存规划记忆**：通过浏览器侧边栏直接抓取可见纯文本增量同步（仅同步主动绑定的工作窗口，日常闲聊完全隔离），不依赖 OpenAI 付费 API，不消耗额外模型额度。
2. **开箱即用的“按 Chat 名称开工”与高保真交接**：
   - 新任务启动时，只需要把 Chat 窗口名字或标题告诉执行 AI，它即可一次拉取**当前有效正式决策 + 阶段纪要 + 最新 15–20 轮规划原文 + 本地路径指针**直接开工。
   - 模型切换或额度耗尽时，借助本机轻量脚本直接解析 **Claude Code / Codex / Antigravity** 的本地 `.jsonl` 会话记录与 `git diff`，为接手模型组装**“正式决策 + 规划最新 ~5 轮 + 上一模型执行现场最新 ~10 轮原文 + 结构化交接摘要 + 代码补丁”**，既不丢现场，又避免一口气塞爆上下文。
3. **三层读取 + 超长对话高保真压缩**：平时只读近期窗口与决策；需要翻旧账时走 PostgreSQL 全文检索；超过保护区的长对话支持利用 1M 上下文模型（如 DeepSeek）滚动压成阶段纪要，同时永久保留全部原始逐轮文字。
4. **严格的权责与安全边界**：
   - 执行节点只能提议决策（`PROPOSED`），**无权批准或擅改正式决策（`APPROVED`）**；
   - 正式决策采用**版本换代（`APPROVED -> SUPERSEDED`）**机制，保留完整演化原因；
   - 历史网页对话在接口中显式标记为只读参考数据，防范 Prompt 注入；
   - 本地代码与大型数据集留在本地磁盘与 Git，不强迫上传云端。
5. **先打透刚需，核心接口自然泛化**：聚焦自身核心工作流的同时，通过 Token 映射扩展、本地日志适配器注册表、OpenAI-compatible 摘要接口与平台无关的数据表结构，方便开源社区接入 Cursor、Windsurf、Cline、Claude.ai、Ollama 等更多工具。

---

## 三、系统四层架构

```text
+-----------------------------------------------------------------------+
| A. Planner Layer（最高规划与决策层：ChatGPT Chat 多窗口）              |
|    - CHAT_AR_MAIN / CHAT_AR_PAPER / CHAT_PDSR / ...                   |
|    - Chrome Extension (Manifest V3 侧边栏): 白名单同步 / 问题队列 / 决策 |
+-----------------------------------+-----------------------------------+
                                    | HTTPS (443) + Bridge Token
                                    v
+-----------------------------------------------------------------------+
| B. Central Hub（云端中枢：FastAPI + PostgreSQL + MCP Server）          |
|    - 三类档案：Dialogue Archive / Decision Archive / Execution Archive |
|    - Thread Registry / 全文检索 / Rolling Digest / 行锁租约与交接路由   |
+-----------------------------------+-----------------------------------+
                                    | HTTPS (/mcp & /api/v1/hub) + Agent Token
                                    v
+-----------------------------------------------------------------------+
| C. Agent Layer（平级执行节点：本地共享同一套代码仓库）                   |
|    - Gemini (Antigravity IDE)                                         |
|    - DeepSeek (Claude Code)  <══ 主动交接 / 额度耗尽接力 ══>           |
|    - GPT (Codex)                                                      |
+-----------------------------------+-----------------------------------+
                                    | 本地文件系统 + Git Branch/Worktree
                                    v
+-----------------------------------------------------------------------+
| D. Storage Layer                                                      |
|    - Phase 1–2：PostgreSQL 纯文本档案 + 本地路径 / Git 指针            |
|    - Phase 3（可选）：阿里云 OSS 归档与异地备份                         |
+-----------------------------------------------------------------------+
```

---

## 四、当前项目进度（Stage 1 已实现并上线运行）

本项目按渐进式里程碑稳步推进：

- [x] **Stage 0 · 基础设施审计与蓝图定稿**
  - 完成轻量云服务器（2 vCPU / 1.8 GiB RAM）评估、独立子域名 HTTPS（Let's Encrypt 自动续签）及架构/安全规范文档化。
- [x] **Stage 1 · Central Hub 核心后端与数据库（已完成 & 部署上线）**
  - 完成 `server/` 全部数据模型与 Alembic 迁移脚本（10 张核心表，含 PostgreSQL `pg_trgm` + `tsvector` GIN 全文索引）。
  - 实现双域 Token 鉴权（`Bridge Token` 与 `Agent Token` 权限物理隔离，Agent 调用管理与批准接口强制 `403`）。
  - 实现白名单窗口注册与灵活检索（支持直接按窗口 ID 或中文标题解析）、幂等消息增量同步（`message_hash` 去重）、`APPROVED -> SUPERSEDED` 决策版本迭代、任务行锁租约防冲突、问题阻塞与解除、以及面向接力的上下文聚合接口。
  - 完成本地 6 项 `pytest` 核心与边界测试（全绿通过），并已在阿里云生产环境跑通 PostgreSQL 13 低内存调优（总增量内存约 +62 MiB）与每日 `pg_dump` 自动备份。
- [ ] **Stage 2 · MCP Server 挂载与本地三端 `.jsonl` 交接实测（下一阶段）**
- [ ] **Stage 3 · Chrome Extension Chat Bridge（开发者模式本地加载）**
- [ ] **Stage 4 · 长对话滚动压缩接入与可选 OSS 归档**
- [ ] **Stage 5 · 全链路 14 项总验收**

---

## 五、快速体验与本地测试（Stage 1）

> 生产部署可参照 [deploy/](deploy/) 与 [server/README.md](server/README.md)；本地开发与运行测试无需配置 PostgreSQL（默认支持 SQLite）：

```bash
cd server
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -v
```

---

## 六、仓库结构与设计文档索引

```text
tower-of-babel/
├── README.md                  # 项目愿景、目标、架构与当前进展
├── LICENSE                    # MIT 开源协议
├── SECURITY.md                # 安全红线、密钥管理与 Prompt 注入防御说明
├── AGENTS.md                  # 面向执行 AI 的统一协作约定
├── docs/
│   ├── 00-global-execution-plan.md    # 全局分阶段执行路线与验收门禁
│   ├── 01-architecture.md             # 轻量部署拓扑与资源预算
│   ├── 02-data-and-context-design.md  # 三类档案、状态机与三层上下文机制
│   ├── 03-api-mcp-and-security.md     # API / MCP 契约与权限矩阵
│   ├── 04-chat-bridge-design.md       # Chrome 扩展交互与防失控护栏
│   └── 05-review-checklist.md         # 功能与边界审阅清单
├── server/                    # [Stage 1 已实现] FastAPI + SQLAlchemy + Alembic + Pytest
└── deploy/                    # Systemd / Nginx HTTPS / PostgreSQL 每日备份脚本模板
```

---

## 七、安全与开源声明

本仓库严格执行配置与代码分离原则：**不包含任何真实服务器密码、API Key、Bearer Token、个人账号 Cookie 或私有对话数据**。详情请参阅 [SECURITY.md](SECURITY.md)。
