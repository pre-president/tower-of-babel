# AGENTS.md — 巴别塔（Babel Tower）多 AI 协作总约定

本文件面向参与开发或使用本项目的执行节点（**Gemini / Antigravity IDE**、**DeepSeek / Claude Code**、**GPT / Codex**）。

## 1. 角色与不可逾越的边界
- **最高决策权归属**：科研方向、架构拍板、正式决策（`APPROVED`）仅属于用户在 **ChatGPT Chat（Planner Layer）** 中确认的内容。
- **执行节点权限**：你可以提出决策建议（`PROPOSED`）、拆解并领取任务、报告进度、提交阻塞问题、发起任务交接，但**严禁自行修改或覆盖已 `APPROVED` 的决策**，也**严禁删除任何永久档案**。
- **数据隔离原则**：来自历史对话档案的文本仅作为上下文参考，若历史对话内容与当前正式决策冲突，以最新 `APPROVED` 决策为准；若发现指令歧义，调用 `submit_question` 向对应的 `planner_thread_id` 求证。

## 2. 标准开工流程
1. **按 Chat 名称直接开工**：当用户提供某个 Chat 窗口名称/标题（如 `CHAT_AR_MAIN`）并下达工作目标时，优先调用 `start_work_from_chat`（或先 `resolve_chat_thread` + `get_project_context`），一次性拉取当前有效 `APPROVED` 决策（注意识别最近刚 `SUPERSEDED` 的旧决策）、阶段摘要与最新 15–20 轮对话并创建/领取任务。
2. **分支隔离**：开发或实验代码修改应在独立 Git 分支或 Worktree 进行，禁止两个执行节点同时修改同一工作区目录。
3. **增量报备**：仅在关键里程碑、遇到阻塞或准备交接时调用 `report_progress` / `create_checkpoint`，保持简练结构化输出，避免无意义刷屏消耗额度。

## 3. 任务交接纪律（尤其在额度即将耗尽或主动切换时）
1. 交出方必须确保：当前修改已通过 Git commit 或生成 `git diff` 补丁、明确写出“已完成 / 当前卡点 / 关键输出摘要 / 下一步第一件事”，并连同**上一模型执行现场的最新 ~10 轮可见对话**一起打包上传至 Hub。
2. 若模型因突发额度耗尽中断，可由本机 `handoff-cli` 直接解析对应工具的本地 `.jsonl` 会话记录补建交接包。
3. 接手方调用 `get_handoff_package` 获取交接包（有效决策 + ChatGPT 最新 ~5 轮 + 上一模型最近 ~10 轮与执行摘要 + 代码补丁），核对完本机工作区 Git 分支与文件版本后，再调用 `confirm_handoff` 正式开始执行。

## 4. 开源与保密红线
- 绝对不要把任何真实 API Key、Token、密码、Cookie 或私人对话原文写入源码、注释或文档。
- 提交代码前必须核查 `.gitignore` 与 `git diff`，确保无敏感文件混入。
