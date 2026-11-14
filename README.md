# 巴别塔 · Babel Tower (Personal Multi-Agent Collaboration Hub)

> **中国海洋大学大气科学 郑江羽 个人开源开发项目**  
> **Personal Open-Source Project by Zheng Jiangyu (Atmospheric Sciences, Ocean University of China)**

**巴别塔（Babel Tower）** 是一个轻量级、模型无关的个人多 AI 协同工作平台：以 **ChatGPT Chat** 为最高规划与决策中枢，以 **阿里云轻量中枢（FastAPI + PostgreSQL + MCP Server）** 为共享记忆与任务状态总线，让 **Gemini（Antigravity IDE）**、**DeepSeek（Claude Code）** 与 **GPT（Codex）** 共享同一套上下文、协同开发并无缝交接任务。

---

## 核心定位与设计原则

1. **不做套壳，不依赖 OpenAI API 规划代理**：完全保留你在网页端 `chatgpt.com` 的原始对话与规划习惯。
2. **零多余额度消耗的上下文维护**：
   - ChatGPT 对话由浏览器扩展**原样抓取纯文本**入库，不要求 ChatGPT 额外输出总结。
   - 执行节点（Antigravity / Claude Code / Codex）的本地 `.jsonl` 会话日志由本机轻量脚本提取**可见结论与关键操作**，即使某个模型额度突发耗尽也能完整救回执行上下文。
3. **三层读取 + DeepSeek 滚动压缩 + 关键词检索**：
   - 默认只读必要窗口与正式决策，不无限塞满上下文。
   - 借助 DeepSeek 1M 上下文窗口对超长旧历史做阶段性高保真压缩（原始原文永久保留、可随时检索回溯）。
4. **严格权限与人机边界**：
   - 执行 AI 可以读取上下文、提出建议、记录进度、发起交接、向指定 Chat 窗口提问，但**无权擅自批准正式决策**，也**无权删除永久档案**。
   - 历史网页对话在 MCP 中严格标记为**只读参考数据**，防止 Prompt 注入越权。
5. **本机文件留本机，中枢只管文本与指针（Phase 1–2）**：
   - 大型科研数据集、正式代码库和图表留在本地硬盘与 Git，中枢只存文本档案、本地路径指针与 `git diff` 补丁。

---

## 总体四层架构

```text
+-------------------------------------------------------------------+
| A. Planner Layer (最高决策中枢: ChatGPT Chat 多窗口)               |
|    - CHAT_AR_MAIN / CHAT_AR_PAPER / CHAT_PDSR / ...               |
|    - Chrome Extension (Chat Bridge): 白名单同步 / 问题队列 / 决策  |
+---------------------------------+---------------------------------+
                                  | HTTPS (hub.atmosphericduct.xyz)
                                  v
+-------------------------------------------------------------------+
| B. Central Hub (阿里云中枢: FastAPI + MCP Server + PostgreSQL)      |
|    - 三类档案: Dialogue / Decision (追加式) / Execution (租约+检查点)|
|    - Thread Registry / 全文检索 / Rolling Digest / Handoff 路由    |
+---------------------------------+---------------------------------+
                                  | HTTPS MCP (/mcp) + Agent Token
                                  v
+-------------------------------------------------------------------+
| C. Agent Layer (平级执行节点: 本地同一工作区)                       |
|    - Gemini (Antigravity IDE)                                     |
|    - DeepSeek (Claude Code)  <-- 主动交接 / 额度耗尽接力 -->       |
|    - GPT (Codex)                                                  |
+---------------------------------+---------------------------------+
                                  | 本地文件系统 + Git Branch/Worktree
                                  v
+-------------------------------------------------------------------+
| D. Storage Layer                                                  |
|    - Phase 1-2: PostgreSQL 纯文本档案 + 本地路径/Git 指针          |
|    - Phase 3 (可选): 阿里云 OSS 归档与异地备份                     |
+-------------------------------------------------------------------+
```

---

## 仓库目录结构

```text
巴别塔/
├── README.md                    # 项目总览与设计原则
├── LICENSE                      # MIT 开源协议
├── SECURITY.md                  # 安全规范与漏洞/密钥防护说明
├── AGENTS.md                    # 面向 Gemini / DeepSeek / Codex 的统一协作约定
├── .gitignore                   # 严格排除密钥、本地日志、数据库与临时文件
├── docs/                        # 阶段零：功能、数据结构、接口与安全审阅文档
│   ├── 01-architecture.md       # 四层架构、服务器资源约束与部署拓扑
│   ├── 02-data-and-context-design.md  # 三类档案结构、状态机、三层读取与 1M 滚动压缩策略
│   ├── 03-api-mcp-and-security.md     # API / MCP 功能契约、权限矩阵与安全边界分析
│   ├── 04-chat-bridge-design.md       # Chrome 扩展设计（白名单同步、同源补齐、可选自动发送）
│   └── 05-review-checklist.md         # 面向项目负责人的功能与风险审阅清单
├── server/                      # [Phase 1-2] 阿里云中枢后端 (Python 3.11 + FastAPI + MCP)
├── extension/                   # [Phase 4] Chrome Extension Chat Bridge (Manifest V3)
├── client-bridge/               # [Phase 2/5] 本地 stdio-MCP 代理与 .jsonl 交接提取脚本
└── deploy/                      # [Phase 1+] Nginx / Systemd / 数据库备份运维模板
```

---

## 实施路线图

- **Phase 0（已完成）**：阿里云服务器只读审计、磁盘释放、Nginx/SSL 自动续签配置、`hub` 子域名解析与 HTTPS 预留、项目设计文档定稿。
- **Phase 1**：PostgreSQL 表结构 + FastAPI 核心 + Token 鉴权 + 每日备份。
- **Phase 2**：MCP Server 挂载 + 本地 `.jsonl` 交接提取工具 + Antigravity / Claude Code / Codex 三端接通与跨模型交接实测。
- **Phase 3（按需）**：DeepSeek 滚动压缩接入 + 可选 OSS 云端异地备份与附件托管。
- **Phase 4**：Chrome Extension Chat Bridge（白名单增量同步、同源历史补齐、问题队列与可选自动发送、决策发布）。

---

## 开源与安全声明

本项目开源前将严格遵循配置与代码分离原则：**仓库中绝不包含任何服务器 IP 凭据、API Key、Bearer Token、个人对话样本或私有数据库快照**。所有敏感配置均通过本地 `.env` 或环境变量注入。
