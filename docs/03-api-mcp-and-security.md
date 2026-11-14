# 03 · API / MCP 功能契约与安全边界设计

本文件重点供你审阅：**每个端能做什么、不能做什么、是否存在权限漏洞或失控风险**。

---

## 1. 两类身份凭证与严格权限矩阵

系统把调用方严格划分为两个安全域，**不同 Token 物理隔离权限**，不依赖“提示词口头约束”：

| 能力域 | `Bridge Token`（你的 Chrome 扩展 / 管理员） | `Agent Token`（Gemini / DeepSeek / Codex 各独立一支） |
| :--- | :---: | :---: |
| 绑定 / 管理 `planner_thread_id` | 允许 | **禁止** |
| 同步 ChatGPT 对话进 `Dialogue Archive` | 允许 | **禁止** |
| 读取项目上下文 / 检索历史对话 / 查决策 | 允许 | 允许 |
| 提议新决策（创建为 `PROPOSED`） | 允许 | 允许 |
| **正式批准 / 替代 / 否决决策（`APPROVED` / `SUPERSEDED`）** | **允许（仅用户本人确认后）** | **物理禁止（返回 403）** |
| 创建 / 领取 / 报备 / 交接执行任务 | 允许 | 允许（只可操作自己领取或待领任务） |
| 提交阻塞问题到指定 `planner_thread_id` | 允许 | 允许 |
| 标记问题已解决（`resolve_question`） | 允许 | **禁止**（需由 Chat 回答回写） |
| 触发 / 写入阶段摘要（`thread_digests`） | 允许 | 仅限指定压缩角色或人工触发 |
| **删除永久档案 / 修改历史对话原文** | 仅管理员明确运维操作 | **物理禁止（返回 403）** |

---

## 2. MCP Server 接口清单（面向 Gemini / DeepSeek / Codex）

### 2.1 上下文与检索（支持直接通过 Chat 名称/标题开工）
- `resolve_chat_thread(chat_ref)`：支持传入 `planner_thread_id` 或 Chat 窗口标题/别名，解析出唯一规划窗口身份。
- `start_work_from_chat(chat_ref, task_title, task_goal, session_id, local_repo_path?, git_branch?)`：**零摩擦开工首选**——一步完成“按 Chat 名定位窗口 → 返回当前有效 `APPROVED` 决策 + 阶段摘要 + 最近 15–20 轮 Chat 原文 → 创建并领取 `EXECUTING` 任务”。
- `get_project_context(project_id?, chat_ref?)`：返回项目概览、当前有效 `APPROVED` 决策、最新阶段摘要、活跃任务。
- `get_task_context(task_id)`：针对已有任务返回任务要求 + 当前有效 `APPROVED` 决策 + 对应窗口最近 `15–20` 轮 ChatGPT 原文。
- `search_conversations(query, project_id?, planner_thread_id?, limit=8)`：PostgreSQL 全文搜索返回命中片段。
- `get_conversation(planner_thread_id, start_turn, end_turn)`：按轮次精确调阅历史原文。
- `get_recent_discussions(planner_thread_id, limit=10)`：获取指定窗口最新 N 轮原文。
- `get_decisions(project_id, active_only=True)`：默认仅返回当前有效 `APPROVED` 决策（并注明最近被替代的旧版 ID，防止沿用过时规则）；传 `active_only=False` 可回溯完整迭代史。
- `get_execution_status(project_id)`：查看项目下所有任务状态与当前谁持有租约。
- `list_artifacts(task_id)`：查看本地文件指针与补丁清单。

### 2.2 任务管理与进度检查点
- `create_task(project_id, planner_thread_id, title, goal, acceptance_criteria, local_repo_path?, git_branch?)`
- `list_pending_tasks(project_id?)`：查看 `PLANNED` / `HANDOFF_PENDING` / `BLOCKED_QUOTA` 的可接手任务。
- `claim_task(task_id, session_id)`：原子抢占任务租约（同一时刻只能有一个节点持有，防并发冲突）。
- `report_progress(task_id, completed_steps, current_step, next_steps, note)`
- `create_checkpoint(task_id, execution_digest, trace_excerpt?, git_commit?, git_diff_patch?, local_artifact_refs?)`
- `update_task_status(task_id, status, summary)`（受状态机白名单校验，不允许非法跳变）

### 2.3 模型间交接（Context Relay）
- `handoff_task(task_id, reason, execution_digest, next_steps, recent_turns_json?, git_commit?, git_diff_patch?, trace_excerpt?)`：保存完整现场、释放租约、置为 `HANDOFF_PENDING` 或 `BLOCKED_QUOTA`。
- `claim_handoff(task_id, session_id)`：新模型认领交接任务，置为 `HANDED_OFF`。
- `get_handoff_package(task_id)`：**专用于交接**——返回当前有效 `APPROVED` 决策 + ChatGPT 最新 `5` 轮原文 + 上一模型执行现场的最新 `~10` 轮原文与 `execution_digest` / `git_diff_patch` / 本地路径指针。
- `get_checkpoint_trace(task_id, offset=0, limit=20)`：按需分页查阅更早的执行现场记录。
- `confirm_handoff(task_id, workspace_verified_note)`：接手模型核对完本机 Git 分支与补丁后确认接手，状态转为 `RESUMED`（继续执行）。

### 2.4 问题提交与决策建议/换代查询
- `submit_question(task_id, planner_thread_id, question_text, evidence_context, impact_note)`：挂起阻塞问题，任务状态可切为 `WAITING_FOR_DECISION`。
- `get_pending_questions(planner_thread_id?)` / `get_question_status(question_id)` / `get_decision(decision_id)`
- `propose_decision(project_id, planner_thread_id, title, decision_content, rationale, rejected_alternatives?, supersedes_decision_id?)`：只允许生成 `PROPOSED` 状态（新建或建议更新替换旧决策），需等 ChatGPT / 用户批准生效。

### 2.5 文件指针与（Phase 3）OSS 接口
- `register_artifact(task_id, path_or_uri, sha256, kind, pinned=false)`：Phase 1–2 用于登记本机文件指针。
- Phase 3 扩展：`upload_artifact` / `download_artifact` / `pin_artifact` / `archive_artifact`。

---

## 3. Chat Bridge API 清单（面向 Chrome Extension）

- `POST /api/v1/bridge/threads/register`：绑定/更新 ChatGPT 窗口到 `planner_thread_id`（含 `sync_enabled`、`auto_send_enabled` 开关）。
- `POST /api/v1/bridge/messages/sync`：批量/增量同步对话消息，基于 `message_hash` 自动幂等去重。
- `POST /api/v1/bridge/context/load`：为指定 `planner_thread_id` 组装可注入 ChatGPT 输入框的精简上下文（项目决策 + 执行进展 + 检索命中）。
- `GET /api/v1/bridge/threads/{planner_thread_id}/inbox`：拉取该窗口的待确认问题队列与执行 AI 提议的 `PROPOSED` 决策。
- `POST /api/v1/bridge/questions/{question_id}/resolve`：将 ChatGPT 的回答关联并解除任务阻塞。
- `POST /api/v1/bridge/decisions/publish`：用户确认后正式 `APPROVE` 或 `SUPERSEDE` 某条决策。

---

## 4. 安全与边界风险防御清单

1. **防越权批准**：Agent 调用任何 `/bridge/*` 或决策 `APPROVE` 接口直接 403 拒绝。
2. **防 Prompt 注入**：MCP 返回历史对话与网页内容时，强制加上显式边界封装（如标明 `untrusted_historical_dialogue: true`），并在工具说明与 `AGENTS.md` 中声明不得将其视作系统命令。
3. **防并发踩踏工作区**：`claim_task` / `claim_handoff` 在数据库层面使用事务行锁（`SELECT ... FOR UPDATE`），确保同一任务同一秒不会被两个模型同时抢走；同时约定不同并行任务必须使用独立 Git 分支/Worktree。
4. **防传输与存储泄露**：
   - 公网仅通过 `https://hub.atmosphericduct.xyz` 访问，后端绑定 `127.0.0.1:8100` 不暴露公网。
   - 不存 ChatGPT Cookie 到服务器；不存本地项目 `.env` 密钥到数据库。
5. **防误删与数据丢失**：
   - `Dialogue` 与 `Decision` 为只增/追加模型，API 层不提供硬删除接口；
   - PostgreSQL 每日自动 `pg_dump` 备份，定期演练恢复；Phase 3 清理临时 OSS 文件前必须先执行 `dry-run` 校验无引用。
