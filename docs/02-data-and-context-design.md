# 02 · 数据档案、状态机与三层上下文设计

本文件用于审阅核心业务规则：**系统存什么、状态怎么流转、上下文怎么读/怎么压/怎么交接**。

---

## 1. 核心数据表规划（6 张核心主表 + 1 张日志表）

### 1.1 `planner_threads`（多 Chat 窗口注册表）
每个 ChatGPT 规划窗口在此登记独立身份，实现窗口隔离与白名单控制：
- `planner_thread_id`（主键，如 `CHAT_AR_MAIN`、`CHAT_AR_PAPER`、`CHAT_PDSR`、`CHAT_CAREER`）
- `project_id`（所属项目标识，如 `AR_NORTH_PACIFIC`）
- `title`（窗口名称）
- `role_description`（窗口职责说明，例如“主科研路线与核心方法拍板”）
- `chatgpt_conversation_id`（对应 `chatgpt.com/c/<uuid>` 的 UUID，用于精准路由）
- `chatgpt_url`（完整对话链接）
- `sync_enabled`（布尔值：**只有绑定的白名单窗口为 `true`**，未绑定闲聊不同步）
- `auto_send_enabled`（布尔值：**默认 `false`**，仅在用户手动勾选后允许扩展自动发送问题队列）
- `last_synced_at`、`uncompressed_char_count`、`pending_question_count`

### 1.2 `dialogue_messages`（Dialogue Archive：完整对话档案）
**只存用户与 AI 的可见文字交流（保留 Markdown、公式、引用），不存隐藏推理和冗长工具调用原文。**
- `message_id`（主键）
- `source_platform`（`chatgpt` | `gemini_antigravity` | `deepseek_claudecode` | `gpt_codex`）
- `planner_thread_id`（关联所属窗口；执行节点记录可附带 `task_id` 与 `agent_id`）
- `external_conversation_id` / `external_message_id`
- `turn_index`（对话序号）
- `role`（`user` | `assistant`）
- `content`（可见纯文字/Markdown）
- `content_chars`（字符数，用于快速计算压缩阈值）
- `message_hash`（唯一索引：防重复上传幂等去重）
- `source_timestamp`、`created_at`
- `search_vector`（PostgreSQL 全文检索向量列 + GIN 索引）

### 1.3 `thread_digests`（DeepSeek 滚动阶段摘要表，不覆盖原文）
- `digest_id`、`planner_thread_id`、`project_id`
- `start_turn` ~ `end_turn`（覆盖的原始轮次范围）
- `source_char_count`（压缩前字数）、`digest_char_count`（压缩后字数）
- `digest_content`（结构化阶段纪要：核心背景、已验证结论、被否决方案、关键参数与文件指针）
- `summarizer_model`（如 `deepseek-chat` / 经 Claude Code 触发）
- `created_at`

### 1.4 `decisions`（Decision Archive：追加式不可覆盖决策档案）
- `decision_id`、`project_id`、`planner_thread_id`
- `title`、`decision_content`（正式结论）
- `rationale`（理由与证据）
- `rejected_alternatives`（明确否决的备选方案，防止后续模型走回头路）
- `source_message_ids`（追溯到具体哪几轮 ChatGPT 对话）
- `status`：`PROPOSED`（执行节点提议）| `APPROVED`（仅用户/Chat Bridge 批准）| `SUPERSEDED`（被新版本替代）| `REJECTED`
- `supersedes_decision_id`（指向被替代的旧决策 ID，**历史版本永久保留**）
- `approved_by`、`created_at`、`updated_at`

### 1.5 `execution_tasks` & `task_checkpoints`（Execution Archive：执行档案与租约）
**任务主表 `execution_tasks`：**
- `task_id`、`project_id`、`planner_thread_id`（必填，问题不能跨窗口误投）
- `title`、`goal_description`、`acceptance_criteria`
- `status`（见下文状态机）
- `assigned_agent`（`gemini` | `deepseek` | `gpt` | `null`）
- `lease_owner`、`lease_session_id`、`lease_expires_at`（防止两个模型同时修改同一任务）
- `git_branch` / `worktree_path` / `local_repo_path`
- `created_at`、`updated_at`

**检查点与交接包表 `task_checkpoints`：**
- `checkpoint_id`、`task_id`、`agent_id`、`session_id`
- `checkpoint_type`（`progress` | `handoff` | `quota_exhausted` | `completed`）
- `completed_steps`（已完成清单）、`current_step`（当前位置）、`next_steps`（建议下一步第一件事）
- `blockers`（阻塞原因与报错摘要）
- `execution_digest`（**关键：上一个模型在本机执行该任务的精炼过程与结论总结，供接手模型直读**）
- `execution_trace_excerpt`（从本地 `.jsonl` 提取的精简可见对话流，过滤掉原始工具噪点）
- `git_commit`、`git_diff_patch`（未提交代码补丁文本，Phase 1-2 直存 DB，设大小上限如 512KB；超大补丁走本地文件路径提示）
- `local_artifact_refs`（JSON：本地文件路径指针 + SHA-256）
- `created_at`

### 1.6 `questions`（问题队列）
- `question_id`、`task_id`、`planner_thread_id`（若归属不明先标 `NEEDS_TRIAGE` 进人工确认队列）
- `asked_by_agent`、`question_text`、`evidence_context`、`impact_note`
- `status`：`PENDING` | `IN_CHAT`（已填入/发送至 ChatGPT）| `RESOLVED` | `DISMISSED`
- `answer_text`、`resolution_message_id`（自动关联 ChatGPT 的回答消息 ID）
- `created_at`、`resolved_at`

### 1.7 `audit_logs`（重要操作审计日志）
记录所有决策状态变更、任务领取/交接、自动发送触发、档案归档操作，不含敏感明文密钥。

---

## 2. 任务状态机与“额度耗尽”判定原则

```text
  PLANNED ──(claim_task)──► EXECUTING
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
WAITING_FOR_DECISION     BLOCKED_QUOTA          HANDOFF_PENDING
  (submit_question)     (显式额度报错/上报)      (handoff_task主动发起)
       │                       │                       │
  (问题解决)                    └──────────┬────────────┘
       │                                   ▼
       │                           (claim_handoff)
       │                                   ▼
       │                              HANDED_OFF ──(confirm_handoff)──► RESUMED (等同继续 EXECUTING)
       │                                                                    │
       └─────────────────────────────────────────────────────────► COMPLETED / FAILED
```

> **重要防误判原则**：租约（Lease）超时只代表“当前会话可能闲置或异常断开”，**绝不能仅凭心跳超时就自动判定为 `BLOCKED_QUOTA`**。`BLOCKED_QUOTA` 只能由执行 AI 主动报告、或本机脚本检测到客户端明确额度错误后显式上报。

---

## 3. 三层上下文读取机制与 DeepSeek 1M 滚动压缩参数

针对“一个窗口聊到上百轮、几十万字”的场景，设计以下**严格分层**的读取策略，兼顾不丢脉络与节省额度：

### 第一层：按“任务场景”精确配比默认读取量
1. **场景 A：新任务首次启动（ChatGPT 规划完毕 → 交给执行 AI）**
   - 返回：**全部 `APPROVED` 决策** + **来源 `planner_thread_id` 的历史阶段纪要（如有）** + **最近 `15–20` 轮 ChatGPT 原文** + 任务描述与本地路径指针。
   - 原因：新开工需要完整理解你与 ChatGPT 敲定的研究背景与边界。
2. **场景 B：模型间交接（例如 GPT 额度耗尽 / 主动切换 → DeepSeek 或 Gemini 接手）**
   - 返回：**全部 `APPROVED` 决策** + **ChatGPT 最新 `5` 轮左右原文**（锚定最新规划口径，避免被几百轮旧聊天淹没）+ **重点注入上一模型的交接包**。
   - **避免“一口气读过长日志”的分层交接包结构**：
     - **必读层（默认直接返回，控制在 ~3K–6K 字以内）**：
       1. 上一模型交接摘要（`execution_digest`：做了什么、改了哪些文件、测试结果、卡在哪、下一步第一件事）；
       2. 最新 `3–5` 轮执行节点与你的关键对话结论（来自本地 `.jsonl` 提炼）；
       3. `git_branch`、`git_commit` 与 `git_diff` 摘要。
     - **按需展开层（不默认塞进上下文）**：完整本地执行记录按分段游标存放或留在本机 `.jsonl`，接手模型只有觉得缺细节时才通过 `get_checkpoint_trace(offset, limit)` 或本机路径按需追读。

### 第二层：零额外额度的全文检索（Search）
- 执行 AI 或 ChatGPT 侧边栏随时可通过 `search_conversations(query, project_id, planner_thread_id, limit=8)`，借助 PostgreSQL 全文索引毫秒级召回几十轮前某次具体讨论（比如某个公式、参数设定、报错原因）的**原始精确片段**。

### 第三层：利用 DeepSeek 1M 上下文的滚动压缩规则
鉴于当前 DeepSeek API 具备 **1M 上下文窗口**，可以把单次压缩切片设得更从容、减少碎片化，同时依然严格保留原始全文：
- **保护窗口**：每个 `planner_thread_id` 最新的 **`20` 轮**原文永远保持未压缩原貌。
- **触发条件（满足任一即可，支持手动触发或后台异步触发）**：
  - 被挤出“最新 20 轮保护区”的未压缩旧对话累计达到 **`150K–250K` 字符**（折合约 20 万–38 万 Tokens，仅占 DeepSeek 1M 窗口的 20%–38%，单次吞吐毫无压力且推理质量极高）；
  - 或用户手工点击“立即生成阶段摘要”。
- **输出目标**：由 DeepSeek（通过你本地/服务端配置好的 DeepSeek API 通道）将这一大块旧对话高保真压缩为约 **`4K–6K` 字**的结构化纪要（分模块：核心定论 / 关键数据与指标 / 否决路径 / 遗留问题 / 原始轮次索引号），写入 `thread_digests`。
- **原件不删**：压缩后原始 `dialogue_messages` **一字不删**，随时可通过 `search_conversations` 或 `get_conversation(start_turn, end_turn)` 精准回溯任意原始发言。
