/**
 * Tower of Babel Side Panel controller.
 * Strict CSP-compliant (zero inline script/event attributes).
 */
(() => {
  const Api = globalThis.TowerApiClient;
  const Guard = globalThis.TowerAutoSendGuard;

  const STORAGE_KEYS = {
    settings: "tower_settings",
    bindings: "tower_bindings",
    retryQueue: "tower_retry_queue",
    killSwitch: "tower_global_kill_switch",
  };

  let state = {
    settings: { hubUrl: "https://hub.atmosphericduct.xyz", bridgeToken: "" },
    bindings: {},
    retryQueue: [],
    globalKillSwitch: false,
    activeTabId: null,
    activePageInfo: null,
  };

  const $ = (id) => document.getElementById(id);

  function setStatus(id, text, isError = false) {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    el.className = isError ? "status-msg error" : "status-msg";
  }

  async function loadStorage() {
    const raw = await chrome.storage.local.get(Object.values(STORAGE_KEYS));
    state.settings = { ...state.settings, ...(raw[STORAGE_KEYS.settings] || {}) };
    state.bindings = raw[STORAGE_KEYS.bindings] || {};
    state.retryQueue = raw[STORAGE_KEYS.retryQueue] || [];
    state.globalKillSwitch = Boolean(raw[STORAGE_KEYS.killSwitch]);

    $("inputHubUrl").value = state.settings.hubUrl;
    $("inputBridgeToken").value = state.settings.bridgeToken;
    $("globalKillSwitch").checked = state.globalKillSwitch;
    $("retryQueueCount").textContent = String(state.retryQueue.length);
  }

  async function getActiveChatGptTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id || !tab.url || !tab.url.startsWith("https://chatgpt.com")) {
      state.activeTabId = null;
      state.activePageInfo = null;
      return null;
    }
    state.activeTabId = tab.id;
    try {
      const info = await chrome.tabs.sendMessage(tab.id, { type: "TOWER_GET_PAGE_INFO" });
      state.activePageInfo = info;
      return { tab, info };
    } catch {
      state.activePageInfo = { conversationId: null, title: tab.title || "", url: tab.url };
      return { tab, info: state.activePageInfo };
    }
  }

  function currentBinding() {
    const cid = state.activePageInfo && state.activePageInfo.conversationId;
    return cid ? state.bindings[cid] || null : null;
  }

  async function renderTabState() {
    await getActiveChatGptTab();
    const info = state.activePageInfo;
    if (!info || !info.conversationId) {
      $("activeTabBanner").textContent = "当前标签页不是 chatgpt.com/c/... 具体对话";
      $("convIdText").textContent = "-";
      $("whitelistStateText").textContent = "未检测到对话 UUID";
      return;
    }

    $("activeTabBanner").textContent = `${info.title || "ChatGPT 对话"} (${info.turnCount || 0} 条可见消息)`;
    $("convIdText").textContent = info.conversationId;

    const b = state.bindings[info.conversationId];
    if (b && b.sync_enabled) {
      $("whitelistStateText").textContent = `已绑定 -> ${b.planner_thread_id}${b.auto_send_enabled ? " (自动发送已开启)" : ""}`;
      $("inputThreadId").value = b.planner_thread_id;
      $("inputProjectId").value = b.project_id;
      $("inputTitle").value = b.title;
      $("inputRoleDesc").value = b.role_description || "";
      $("chkSyncEnabled").checked = Boolean(b.sync_enabled);
      $("chkAutoSend").checked = Boolean(b.auto_send_enabled);
    } else {
      $("whitelistStateText").textContent = "未绑定（隐私隔离，零抓取）";
      if (!$("inputTitle").value && info.title) {
        $("inputTitle").value = info.title;
      }
      $("chkAutoSend").checked = false;
    }
  }

  async function checkHubHealth() {
    try {
      const h = await Api.health(state.settings);
      if (h && h.database === "connected") {
        $("hubStatus").textContent = "Hub 在线";
        $("hubStatus").className = "badge badge-ok";
      }
    } catch {
      $("hubStatus").textContent = "离线 / 未连通";
      $("hubStatus").className = "badge badge-muted";
    }
  }

  async function injectOrCopyText(text, autoSend = false) {
    if (state.activeTabId) {
      try {
        const res = await chrome.tabs.sendMessage(state.activeTabId, {
          type: "TOWER_FILL_COMPOSER",
          text,
          autoSend,
        });
        if (res && res.ok) {
          setStatus("syncStatusMsg", autoSend ? "已注入并发送至 ChatGPT。" : "已填入 ChatGPT 输入框。");
          return true;
        }
      } catch {
        // Fallback below
      }
    }
    await navigator.clipboard.writeText(text);
    setStatus("syncStatusMsg", "未定位到输入框，已复制内容到剪贴板。", true);
    return false;
  }

  async function refreshInbox() {
    if (!state.settings.bridgeToken) return;
    const b = currentBinding();
    const qList = $("questionsList");
    const tList = $("triageList");
    qList.innerHTML = "";
    tList.innerHTML = "";

    let count = 0;
    if (b) {
      try {
        const inbox = await Api.getInbox(state.settings, b.planner_thread_id);
        const qs = inbox.pending_questions || [];
        count += qs.length;
        if (qs.length === 0) {
          qList.innerHTML = '<p class="meta">当前窗口暂无阻塞提问。</p>';
        }
        for (const q of qs) {
          const box = document.createElement("div");
          box.className = "item-box";
          box.innerHTML = `
            <div class="row-between"><strong>[${q.asked_by_agent}]</strong><span class="pill">${q.status}</span></div>
            <div>${escapeHtml(q.question_text)}</div>
            <div class="btn-row"></div>
          `;
          const btnRow = box.querySelector(".btn-row");
          const fillBtn = makeBtn("填入提问", async () => {
            await injectOrCopyText(Guard.formatQuestionForChat(q), false);
            await Api.markQuestionInChat(state.settings, q.question_id, b.planner_thread_id);
            await refreshInbox();
          });
          const resolveBtn = makeBtn("用最新回复解决", async () => {
            await getActiveChatGptTab();
            const ans = state.activePageInfo?.latestAssistantText || "";
            if (!ans) {
              setStatus("syncStatusMsg", "页面未检测到助手回答，请先生成回复或手动填写。", true);
              return;
            }
            await Api.resolveQuestion(state.settings, q.question_id, {
              answer_text: ans,
              resume_task: true,
            });
            await refreshInbox();
          }, "btn-primary");
          btnRow.append(fillBtn, resolveBtn);
          qList.appendChild(box);
        }
      } catch (err) {
        qList.innerHTML = `<p class="status-msg error">${escapeHtml(String(err))}</p>`;
      }
    } else {
      qList.innerHTML = '<p class="meta">绑定当前窗口后显示专属提问队列。</p>';
    }

    try {
      const triage = await Api.getTriageQuestions(state.settings);
      count += triage.length;
      if (triage.length === 0) {
        tList.innerHTML = '<p class="meta">暂无未指派归属的提问。</p>';
      }
      for (const q of triage) {
        const box = document.createElement("div");
        box.className = "item-box";
        box.innerHTML = `
          <div><strong>[${q.asked_by_agent}]</strong> ${escapeHtml(q.question_text)}</div>
          <div class="btn-row"></div>
        `;
        if (b) {
          const assignBtn = makeBtn(`指派到 ${b.planner_thread_id} 并填入`, async () => {
            await Api.markQuestionInChat(state.settings, q.question_id, b.planner_thread_id);
            await injectOrCopyText(Guard.formatQuestionForChat(q), false);
            await refreshInbox();
          });
          box.querySelector(".btn-row").appendChild(assignBtn);
        }
        tList.appendChild(box);
      }
    } catch {
      // Ignore triage load failure when offline
    }
    $("inboxCount").textContent = String(count);
  }

  async function refreshDecisions() {
    const b = currentBinding();
    const pEl = $("proposedDecisionsList");
    const aEl = $("approvedDecisionsList");
    pEl.innerHTML = "";
    aEl.innerHTML = "";
    if (!b || !state.settings.bridgeToken) {
      aEl.innerHTML = '<p class="meta">绑定当前窗口后查看与管理正式决策。</p>';
      return;
    }
    try {
      const data = await Api.listDecisions(state.settings, b.planner_thread_id, false);
      const all = data.decisions || [];
      const proposed = all.filter((d) => d.status === "PROPOSED");
      const approved = all.filter((d) => d.status === "APPROVED");

      if (proposed.length === 0) pEl.innerHTML = '<p class="meta">暂无执行 AI 提交的待批提案。</p>';
      for (const d of proposed) {
        const box = document.createElement("div");
        box.className = "item-box";
        box.innerHTML = `
          <div class="row-between"><strong>${escapeHtml(d.title)}</strong><span class="pill">by ${escapeHtml(d.proposed_by)}</span></div>
          <div>${escapeHtml(d.decision_content)}</div>
          <div class="btn-row"></div>
        `;
        const row = box.querySelector(".btn-row");
        row.append(
          makeBtn("批准生效", async () => {
            await Api.publishDecision(state.settings, { decision_id: d.decision_id });
            await refreshDecisions();
          }, "btn-primary"),
          makeBtn("驳回", async () => {
            await Api.rejectDecision(state.settings, d.decision_id, "Rejected via Chat Bridge");
            await refreshDecisions();
          }, "btn-danger-outline")
        );
        pEl.appendChild(box);
      }

      if (approved.length === 0) aEl.innerHTML = '<p class="meta">尚无生效正式决策。</p>';
      for (const d of approved) {
        const box = document.createElement("div");
        box.className = "item-box";
        box.innerHTML = `
          <div class="row-between"><strong>[v${d.decision_version}] ${escapeHtml(d.title)}</strong><span class="badge badge-ok">APPROVED</span></div>
          <div>${escapeHtml(d.decision_content)}</div>
          <div class="btn-row"></div>
        `;
        box.querySelector(".btn-row").appendChild(
          makeBtn("更新换代 (Supersede)", () => {
            $("inputSupersedesId").value = d.decision_id;
            $("inputDecisionTitle").value = d.title;
            $("inputDecisionContent").value = d.decision_content;
            $("decisionFormTitle").textContent = `更新换代旧决策 v${d.decision_version}: ${d.title}`;
            $("btnCancelSupersede").classList.remove("hidden");
          })
        );
        aEl.appendChild(box);
      }
    } catch (err) {
      aEl.innerHTML = `<p class="status-msg error">${escapeHtml(String(err))}</p>`;
    }
  }

  function makeBtn(label, onClick, extraClass = "") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `btn btn-sm ${extraClass}`.trim();
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function wireEvents() {
    document.querySelectorAll(".tab").forEach((tabBtn) => {
      tabBtn.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
        tabBtn.classList.add("active");
        $(`tab-${tabBtn.dataset.tab}`).classList.add("active");
      });
    });

    $("globalKillSwitch").addEventListener("change", async (e) => {
      state.globalKillSwitch = e.target.checked;
      await chrome.storage.local.set({ [STORAGE_KEYS.killSwitch]: state.globalKillSwitch });
    });

    $("btnRefreshTab").addEventListener("click", async () => {
      await renderTabState();
      await refreshInbox();
      await refreshDecisions();
    });

    $("settingsForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      state.settings = {
        hubUrl: $("inputHubUrl").value.trim().replace(/\/+$/, ""),
        bridgeToken: $("inputBridgeToken").value.trim(),
      };
      await chrome.storage.local.set({ [STORAGE_KEYS.settings]: state.settings });
      await checkHubHealth();
      setStatus("settingsStatusMsg", "设置已保存至本地浏览器存储。");
    });

    $("btnTestConn").addEventListener("click", async () => {
      try {
        await Api.listThreads({
          hubUrl: $("inputHubUrl").value.trim(),
          bridgeToken: $("inputBridgeToken").value.trim(),
        });
        setStatus("settingsStatusMsg", "连接与 Bridge Token 鉴权验证通过。");
      } catch (err) {
        setStatus("settingsStatusMsg", `验证失败: ${err}`, true);
      }
    });

    $("bindForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      await getActiveChatGptTab();
      const cid = state.activePageInfo?.conversationId;
      if (!cid) {
        setStatus("syncStatusMsg", "请先打开具体的 chatgpt.com/c/... 对话页。", true);
        return;
      }
      const payload = {
        planner_thread_id: $("inputThreadId").value.trim(),
        project_id: $("inputProjectId").value.trim(),
        title: $("inputTitle").value.trim(),
        role_description: $("inputRoleDesc").value.trim(),
        chatgpt_conversation_id: cid,
        chatgpt_url: state.activePageInfo.url,
        sync_enabled: $("chkSyncEnabled").checked,
        auto_send_enabled: $("chkAutoSend").checked,
      };
      try {
        const saved = await Api.registerThread(state.settings, payload);
        state.bindings[cid] = saved;
        await chrome.storage.local.set({ [STORAGE_KEYS.bindings]: state.bindings });
        await chrome.runtime.sendMessage({ type: "TOWER_NOTIFY_TAB_BINDING" });
        await renderTabState();
        await refreshInbox();
        await refreshDecisions();
        setStatus("syncStatusMsg", `已绑定到 ${saved.planner_thread_id}`);
      } catch (err) {
        setStatus("syncStatusMsg", `绑定失败: ${err}`, true);
      }
    });

    $("btnUnbind").addEventListener("click", async () => {
      const cid = state.activePageInfo?.conversationId;
      if (!cid || !state.bindings[cid]) return;
      const prev = state.bindings[cid];
      try {
        await Api.registerThread(state.settings, { ...prev, sync_enabled: false, auto_send_enabled: false });
      } catch {
        // Always allow local unlink even if offline
      }
      delete state.bindings[cid];
      await chrome.storage.local.set({ [STORAGE_KEYS.bindings]: state.bindings });
      await chrome.runtime.sendMessage({ type: "TOWER_NOTIFY_TAB_BINDING" });
      await renderTabState();
      setStatus("syncStatusMsg", "已移出白名单，此窗口停止一切同步。");
    });

    $("btnSyncVisible").addEventListener("click", async () => {
      if (!state.activeTabId) return;
      const res = await chrome.tabs.sendMessage(state.activeTabId, { type: "TOWER_SYNC_VISIBLE" });
      setStatus("syncStatusMsg", res?.ok ? `已触发同步（${res.count || 0} 条可见消息）` : `未执行：${res?.reason}`, !res?.ok);
      await loadStorage();
    });

    $("btnBackfillHistory").addEventListener("click", async () => {
      const b = currentBinding();
      if (!b || !state.activeTabId) {
        setStatus("syncStatusMsg", "请先将当前对话加入白名单再补齐历史。", true);
        return;
      }
      setStatus("syncStatusMsg", "正在同源拉取完整对话树…");
      const hist = await chrome.tabs.sendMessage(state.activeTabId, {
        type: "TOWER_FETCH_SAME_ORIGIN_HISTORY",
      });
      if (!hist || !hist.ok) {
        setStatus("syncStatusMsg", `历史拉取失败：${hist?.error || "未知错误"}`, true);
        return;
      }
      const turns = hist.turns || [];
      let totalInserted = 0;
      let totalDup = 0;
      for (let i = 0; i < turns.length; i += 50) {
        const chunk = turns.slice(i, i + 50);
        const resp = await Api.syncMessages(state.settings, {
          planner_thread_id: b.planner_thread_id,
          source_platform: "chatgpt",
          external_conversation_id: b.chatgpt_conversation_id,
          messages: chunk,
        });
        totalInserted += resp.inserted_count || 0;
        totalDup += resp.duplicate_count || 0;
      }
      setStatus("syncStatusMsg", `全量历史补齐完成：新增 ${totalInserted} 条，去重跳过 ${totalDup} 条。`);
    });

    $("btnFlushQueue").addEventListener("click", async () => {
      const res = await chrome.runtime.sendMessage({ type: "TOWER_FLUSH_QUEUE" });
      await loadStorage();
      setStatus("syncStatusMsg", `重传完成：发送 ${res.sentCount} 批，剩余 ${res.remaining} 批。`);
    });

    $("btnRefreshInbox").addEventListener("click", refreshInbox);
    $("btnRefreshDecisions").addEventListener("click", refreshDecisions);

    $("btnLoadContext").addEventListener("click", async () => {
      const b = currentBinding();
      if (!b) return;
      const q = $("inputSearchQuery").value.trim() || null;
      const res = await Api.loadContext(state.settings, {
        planner_thread_id: b.planner_thread_id,
        search_query: q,
      });
      $("contextPreview").textContent = res.formatted_prompt;
      $("contextPreview").classList.remove("hidden");
      await injectOrCopyText(res.formatted_prompt, false);
    });

    $("btnSearchDialogue").addEventListener("click", async () => {
      const q = $("inputSearchQuery").value.trim();
      if (!q) return;
      const b = currentBinding();
      const hits = await Api.searchDialogues(state.settings, q, b?.project_id || null);
      const formatted = hits.length
        ? hits.map((h) => `[${h.planner_thread_id} #${h.turn_index} ${h.role}]\n${h.content}`).join("\n\n---\n\n")
        : "未检索到匹配的历史片段。";
      $("contextPreview").textContent = formatted;
      $("contextPreview").classList.remove("hidden");
    });

    $("btnCancelSupersede").addEventListener("click", () => {
      $("inputSupersedesId").value = "";
      $("decisionFormTitle").textContent = "发布新正式决策（APPROVED）";
      $("btnCancelSupersede").classList.add("hidden");
    });

    $("decisionForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const b = currentBinding();
      if (!b) return;
      const supersedesId = $("inputSupersedesId").value.trim() || null;
      await Api.publishDecision(state.settings, {
        project_id: b.project_id,
        planner_thread_id: b.planner_thread_id,
        title: $("inputDecisionTitle").value.trim(),
        decision_content: $("inputDecisionContent").value.trim(),
        rationale: $("inputDecisionRationale").value.trim(),
        rejected_alternatives: $("inputDecisionRejected").value.trim(),
        supersedes_decision_id: supersedesId,
      });
      $("decisionForm").reset();
      $("inputSupersedesId").value = "";
      $("decisionFormTitle").textContent = "发布新正式决策（APPROVED）";
      $("btnCancelSupersede").classList.add("hidden");
      await refreshDecisions();
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    wireEvents();
    await loadStorage();
    await checkHubHealth();
    await renderTabState();
    await refreshInbox();
    await refreshDecisions();
  });
})();
