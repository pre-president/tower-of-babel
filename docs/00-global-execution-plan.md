# 00 · 巴别塔（Babel Tower）全局执行总规划

> **归档日期**：2026-11-14  
> **维护者**：中国海洋大学大气科学 郑江羽  
> **当前基线（Phase 0 已验证）**：
> - 阿里云 ECS（`123.56.109.31`）：Alibaba Cloud Linux 3.2104 / 2 vCPU / 1.8 GiB RAM（+4G Swap）/ 剩余磁盘 **18G**。
> - 原生波导站点 `atmosphericduct.xyz`（`127.0.0.1:5000`）运行正常，已开启开机自启并切换至自动续签 Let's Encrypt 证书。
> - 新域名 `hub.atmosphericduct.xyz` DNS 已解析、独立 Nginx 443 + Let's Encrypt 证书已生效（当前 `/health` 占位返回 200）。
> - 阿里云软件源已核实自带 `postgresql-server 13.23`、`postgresql-contrib 13.23` 与 `python3.11 3.11.13`，零第三方仓库风险。
> - 本机（Windows）：Python 3.13.14、Node v24.15.0、Git 2.54.0；Claude Code / Codex / Antigravity 本地 `.jsonl` 路径已实测存在。

---

## 一、全局执行铁律（防止后续翻车）

1. **绝不干扰现有波导服务**：新中枢独占 `127.0.0.1:8100`、独占独立 `postgresql` 数据目录/数据库、独占 `python3.11` 虚拟环境与 `hub.atmosphericduct.xyz` 配置，绝不改动 `/opt/atmosphericduct` 或系统默认 Python 3.6。
2. **先本地全量单测，再部署阿里云**：每个阶段的代码先在本机用 `pytest` / mock 验证逻辑、权限越权、状态机、去重与交接流程全部通过后，再同步部署到阿里云并做端到端冒烟验证。
3. **绝对保密与可开源结构**：从第一行代码起坚持“密钥与代码彻底分离”，所有真实 Token、API Key、数据库连接串只存于服务器 `/etc/babel-tower/hub.env`（`chmod 600`）与本机未入库 `.env`，仓库只留 `.env.example`。
4. **每阶段一个可独立验收的里程碑**：不贪大求全，上一阶段端到端打通并验证通过后，再开启下一阶段。

---

## 二、分阶段详细执行路线图

### Stage 1（Phase 1）：中央数据库与 FastAPI 核心骨架
**目标**：在阿里云搭起稳健的“大脑与账本”，通过 API 完成全部读写与权限隔离验证。
1. **服务器基础设施安装与低内存调优**（阿里云）：
   - 安装 `postgresql-server`（13.23）、`postgresql-contrib`（含 `pg_trgm`）、`python3.11`、`python3.11-devel`。
   - 初始化 PostgreSQL 并调优参数（`shared_buffers=128MB`、`work_mem=4MB`、`max_connections=25`），创建专用数据库 `babel_hub` 与最小权限用户。
   - 启用每日凌晨自动 `pg_dump` 压缩备份（保留最近 14 天，含一键恢复演练脚本）。
2. **核心代码实现**（本地 `server/`）：
   - Alembic 数据库迁移脚本（覆盖 `planner_threads`、`dialogue_messages`、`thread_digests`、`decisions`、`execution_tasks`、`task_checkpoints`、`questions`、`artifacts`、`audit_logs`）。
   - 双域鉴权中间件（`Bridge Token` vs 3 个独立 `Agent Token`），通过服务端单元测试硬性验证：**Agent 绝不能批准决策、绝不能删除档案**。
   - 实现核心服务逻辑：
     - 幂等消息入库（`message_hash` 去重 + PostgreSQL `tsvector`/`pg_trgm` 全文检索）；
     - 决策版本迭代（批准新版 `APPROVED` 自动将旧版流转为 `SUPERSEDED`）；
     - 任务租约行锁（`SELECT FOR UPDATE` 防并发抢单）；
     - 状态机强校验（显式区分 `BLOCKED_QUOTA` 与租约超时）。
3. **部署与 Stage 1 验收门禁**：
   - 将 `/etc/nginx/conf.d/hub.atmosphericduct.conf` 切换为反代 `127.0.0.1:8100`，配好 `babel-hub.service`（开机自启）。
   - 验证：`https://hub.atmosphericduct.xyz/health` 返回 DB 连通正常；`atmosphericduct.xyz` 未受任何影响；`pytest` 全部通过（含 403 越权测试、决策换代测试、重复同步幂等测试）。

---

### Stage 2（Phase 2）：MCP Server 与本地三端模型接力实测
**目标**：让 **Gemini（Antigravity）/ DeepSeek（Claude Code）/ GPT（Codex）** 真正打通，能用 Chat 名直接开工、并实现**带最近 ~10 轮执行现场与 `git diff` 的无缝交接**。
1. **服务端 `/mcp` 挂载**（`server/app/mcp/`）：
   - 在同一个 FastAPI 进程内挂载标准 MCP（Streamable HTTP + 兼容 SSE），直接复用 Stage 1 鉴权与业务层，不增加第二个后台进程。
   - 实现全部核心 MCP Tools，重点包含：
     - **`start_work_from_chat(chat_ref, ...)`**：允许直接传 Chat 名称/标题定位 `planner_thread_id`，一次性返还 `APPROVED` 决策 + 摘要 + 最近 15–20 轮对话并建单领取；
     - **`get_handoff_package(task_id)`**：按“ChatGPT 最新 ~5 轮 + 当前有效 `APPROVED` 决策 + 上一模型执行现场最新 ~10 轮原文 + 交接摘要 + `git diff` 补丁”组装返回；
     - **`get_checkpoint_trace(task_id, offset, limit)`**：按需分页追查更早执行记录；
     - 历史对话返回一律包裹不可信只读标记（防 Prompt 注入）。
2. **本机桥接与 `.jsonl` 交接提取器**（`client-bridge/`）：
   - **本地 `.jsonl` 解析器**：分别适配 Claude Code（`~/.claude/projects/`）、Codex（`~/.codex/sessions/`）、Antigravity（`~/.gemini/antigravity/brain/`），自动剔除冗长工具 JSON 噪点，精确提取最近 ~10 轮用户与助手可见文字对话。
   - **`handoff-cli.py`**：支持主动交接或额度耗尽中断后一键运行——抓取当前工作区 `git branch`、`git commit`、`git diff` 补丁与 `.jsonl` 最近 ~10 轮，生成结构化交接包上传至 Hub。
   - **`mcp-stdio-proxy.py`**：若三端中任一客户端暂不直接支持远程 Bearer HTTP MCP，可通过该极简本地脚本转发，确保三端接口 100% 一致。
3. **Stage 2 验收门禁**：
   - 先用模拟数据 + 本地客户端实测完整跑通：**在 Antigravity 用 `CHAT_AR_MAIN` 开工 → 修改本地测试文件 → 触发交接 → 在 Claude Code / Codex 里认领并拿到最近 ~10 轮现场与补丁继续完成**。

---

### Stage 3（Phase 4 提前或并行：Chrome Extension Chat Bridge）
**目标**：打通浏览器端 `chatgpt.com` 右侧边栏与增量自动同步、问题队列及可选自动发送。
1. **本地开发者模式加载（无需应用商店上架）**：
   - Manifest V3 + `sidePanel` + `storage` + `activeTab`（仅限 `chatgpt.com` 与 `hub.atmosphericduct.xyz`）。
2. **核心模块**：
   - **`dom-adapter.js` 隔离层**：只用语义属性定位消息、输入框与按钮，方便应对页面改版且失效自动降级剪贴板。
   - **白名单同步引擎**：未绑定窗口零动作；已绑定窗口在“发送下一轮”主触发 + “停止生成 3 秒”兜底触发时增量上传；提供手动按钮从当前页同源上下文一次性补齐老对话历史。
   - **侧边栏交互**：窗口绑定开关、上下文一键读取注入、待回答问题队列、决策批准与**更新换代（Supersede）**按钮。
   - **白名单可选自动发送护栏**：默认关闭；仅勾选窗口生效；带 15 秒冷却、连续 5 次自动熔断与顶部急停开关。
3. **Stage 3 验收门禁**：
   - 在真实 Chrome 开发者模式加载测试：绑定测试对话、验证未绑定窗口不上传、验证刷新/重启离线队列补传、验证从执行 AI 提问 → ChatGPT 回答 → 回传解除阻塞全链路闭环。

---

### Stage 4（Phase 3 收尾增强：DeepSeek 1M 滚动压缩 + 可选 OSS）
**目标**：解决长期运行上百轮/几十万字后的记忆体积与成本控制。
1. **DeepSeek 1M 滚动压缩任务**：
   - 保护区：每个窗口最新 20 轮原文不动；
   - 触发器：更早未压缩原文累计达到 `150K–250K` 字符（或手动点击“立即压缩”），调用 DeepSeek API（密钥仅从 `.env` 读取）将其压为 `4K–6K` 字结构化 `thread_digest`；原文永不删。
2. **可选 OSS 挂载（若后续有需求）**：
   - 按需在阿里云绑定最小权限 ECS RAM 角色（禁用 `/permanent/` 删除），对接附件托管与异地备份。

---

### Stage 5（总验收与 GitHub 开源脱敏发布）
1. 执行全链路 14 项验收（多窗口隔离、决策换代、三端切换、额度耗尽模拟、服务器重启自启、数据库 `pg_dump` 恢复演练）。
2. 运行自动化脱敏检查脚本（确认零密钥、零私密日志、`.env.example` 完整、文档与 `AGENTS.md` 齐备），正式推送 GitHub 公开仓库。
