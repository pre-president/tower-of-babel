# 01 · 系统总体架构与服务器部署设计

## 1. 设计目标与核心约束回顾
- **规划中枢**：保留网页端 `chatgpt.com` 多窗口作为最高决策层（`CHAT_AR_MAIN`、`CHAT_AR_PAPER`、`CHAT_PDSR`、`CHAT_CAREER` 等），不使用 OpenAI API 替代。
- **执行平级化**：Gemini（Antigravity IDE）、DeepSeek（Claude Code）、GPT（Codex）共享同一套 MCP Server 与中央档案，任意节点额度耗尽或不可用时可由其他节点无缝接手。
- **低成本与零冗余**：
  - 不要求 AI 额外“写摘要上传”，由扩展与本地脚本以 **0 Token** 抓取/提取文本。
  - Phase 1–2 仅在阿里云保存纯文本档案与本地文件/Git 指针，大型代码与科研数据集保留在本机硬盘。

---

## 2. 阿里云 ECS 资源边界与部署拓扑

基于对阿里云服务器（`123.56.109.31`，Alibaba Cloud Linux 3）的实测盘点：
- **CPU / 内存**：2 vCPU / **1.8 GiB 物理内存**（+ 4.0 GiB Swap），当前可用物理内存 ~1.35 GiB。
- **磁盘空间**：系统盘 40G，清理重复旧备份后剩余 **18G**。
- **共存业务**：现有南海波导查询网关运行在 `127.0.0.1:5000`（`atmosphericduct.xyz`）。

### 为何采用“轻量一体化原生部署”而非重型微服务 Docker
在 1.8 GiB 物理内存下，如果同时启动 Docker Daemon + 多个独立容器（FastAPI + MCP + Redis + 未调优 PG），内存极易吃满并频繁换页（Swap），拖慢现有波导服务。因此采用**进程合二为一 + 原生低内存调优**方案：

```text
Internet (HTTPS 443)
   │
   ├── atmosphericduct.xyz ──► Nginx ──► 127.0.0.1:5000 (现有波导服务，完全隔离不动)
   │
   └── hub.atmosphericduct.xyz ──► Nginx (TLS 1.2/1.3)
                                      │
                                      ▼
                     127.0.0.1:8100 (Python 3.11 独立 venv, systemd 托管)
                     ├── /api/v1/*   (REST & Chat Bridge API)
                     ├── /mcp        (MCP Server: Streamable HTTP / SSE)
                     └── 后台轻量调度 (滚动摘要触发检查 / 租约清理)
                                      │
                                      ▼
                     127.0.0.1:5432 (PostgreSQL 16, 低内存调优 ~80-100 MB)
                     ├── 全文检索索引 (tsvector + pg_trgm)
                     └── 每日 pg_dump 自动备份
```

### 资源预算估算
| 组件 | 预估常驻内存 | 预估磁盘增量（第一年） |
| :--- | :--- | :--- |
| 现有波导系统 + 系统/云监控组件 | ~450 MB | 20G（已占用） |
| PostgreSQL（`shared_buffers=128MB`） | ~80–120 MB | 0.2–1.0 GB（纯文本+索引） |
| Hub 单体服务（FastAPI + MCP, 1–2 workers） | ~90–150 MB | ~200 MB（独立 venv + 代码） |
| **合计** | **~650–720 MB（远低于 1.8 GiB）** | **剩余 ~16G+ 安全余量** |

> **关键结论**：Phase 1–2 不安装 Redis，也不在阿里云服务器上运行任何本地 Embedding 向量推理模型，既保证系统流畅，又确保不干扰现有 `atmosphericduct.xyz`。

---

## 3. 本地三端执行环境与“本地日志提取”优势

经实测核查，三个执行节点在本机均自动持久化完整对话与工具记录（JSONL 格式）：
- **Claude Code（DeepSeek）**：`~/.claude/projects/<project>/<session_id>.jsonl`
- **Codex（GPT）**：`~/.codex/sessions/.../rollout-*.jsonl`
- **Antigravity IDE（Gemini）**：`~/.gemini/antigravity/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`

这一特性带来两个核心能力：
1. **高保真交接**：切换模型时，本机辅助脚本可直接从 JSONL 解析上一个模型的**用户指令、可见文字输出、关键文件改动与命令结果**（过滤掉杂乱的底层 JSON 参数），无需让即将耗尽额度的模型“临时回忆”。
2. **断点救火**：即便某个模型在中途突发额度报错中断，本机脚本仍可提取中断前的最后一轮有效文本与当前 `git diff`，无缝打包交接给下一个模型。

---

## 4. 演进路线（Phase 0 → Phase 4）

- **Phase 0（本阶段，设计审阅）**：完成服务器域名/证书/磁盘整理，并定稿 `docs/` 下全部功能与安全设计。
- **Phase 1（数据与中心 API）**：部署 PostgreSQL + FastAPI 基础骨架、Token 鉴权、三类档案存储、每日 `pg_dump` 备份。
- **Phase 2（MCP 与模型接力闭环）**：上线 `/mcp` 接口、本机 `.jsonl` 交接提取 CLI，在 Antigravity / Claude Code / Codex 上实测任务创建、进度同步与跨模型交接。
- **Phase 3（智能压缩与可选 OSS）**：接入 DeepSeek 1M 滚动压缩工作流；如未来产生跨机访问图片/异地备份需求，再以 ECS RAM 角色挂载阿里云 OSS。
- **Phase 4（Chat Bridge 浏览器扩展）**：开发 Chrome Extension，打通白名单窗口自动增量同步、同源历史补齐、问题队列与可选自动发送、决策发布。
