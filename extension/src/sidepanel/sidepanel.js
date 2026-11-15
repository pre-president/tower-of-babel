/**
 * Tower of Babel Side Panel controller (clean light-card ergonomic UI).
 * Strict CSP-compliant (zero inline scripts or event attributes).
 */
(() => {
  const Api = globalThis.TowerApiClient;
  const Guard = globalThis.TowerAutoSendGuard;
  const Helpers = globalThis.TowerThreadHelpers;

  const STORAGE_KEYS = {
    settings: "tower_settings",
    bindings: "tower_bindings",
    retryQueue: "tower_retry_queue",
    killSwitch: "tower_global_kill_switch",
    projectGroupMap: "tower_project_group_map",
    lastProject: "tower_last_project",
  };

  const COMPACT_THRESHOLD_CHARS = 150000;
  const CONTENT_FILES = [
    "src/shared/history-tree.js",
    "src/content/dom-adapter.js",
    "src/content/content-script.js",
  ];

  let state = {
    settings: { hubUrl: "https://hub.atmosphericduct.xyz", bridgeToken: "" },
    bindings: {},
    hubThreads: [],
    retryQueue: [],
    globalKillSwitch: false,
    projectGroupMap: {},
    lastProject: "",
    activeTabId: null,
    activePageInfo: null,
    lastRenderedConvId: null,
  };

  let toastTimer = null;
  let tabSyncTimer = null;

  const $ = (id) => document.getElementById(id);

  function showToast(text, isError = false) {
    const el = $("toastBanner");
    if (!el) return;
    el.textContent = text;
    el.className = isError ? "toast error" : "toast";
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.add("hidden");
    }, 3400);
  }

  function switchTab(tabKey) {
    document.querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === tabKey);
    });
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.toggle("active", p.id === `tab-${tabKey}`);
    });
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function makeBtn(label, onClick, extraClass = "") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `btn ${extraClass}`.trim();
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  async function loadStorage() {
    const raw = await chrome.storage.local.get(Object.values(STORAGE_KEYS));
    state.settings = { ...state.settings, ...(raw[STORAGE_KEYS.settings] || {}) };
    state.bindings = raw[STORAGE_KEYS.bindings] || {};
    state.retryQueue = raw[STORAGE_KEYS.retryQueue] || [];
    state.globalKillSwitch = Boolean(raw[STORAGE_KEYS.killSwitch]);
    state.projectGroupMap = raw[STORAGE_KEYS.projectGroupMap] || {};
    state.lastProject = raw[STORAGE_KEYS.lastProject] || "";

    $("inputHubUrl").value = state.settings.hubUrl;
    $("inputBridgeToken").value = state.settings.bridgeToken;
    $("globalKillSwitch").checked = state.globalKillSwitch;
    updateKillSwitchVisual();
    $("retryQueueCount").textContent = String(state.retryQueue.length);
    $("tokenReminder").classList.toggle("hidden", Boolean(state.settings.bridgeToken));
  }

  function updateKillSwitchVisual() {
    const wrap = $("killSwitchWrap");
    const lbl = $("killSwitchLabel");
    if (!wrap || !lbl) return;
    wrap.classList.toggle("active", state.globalKillSwitch);
    lbl.textContent = state.globalKillSwitch ? "已急停" : "急停";
  }

  async function sendTabMessageWithAutoInject(tabId, tabUrl, msg) {
    try {
      return await chrome.tabs.sendMessage(tabId, msg);
    } catch {
      if (!tabUrl || !tabUrl.startsWith("https://chatgpt.com") || !chrome.scripting) {
        return null;
      }
      try {
        await chrome.scripting.executeScript({ target: { tabId }, files: CONTENT_FILES });
        return await chrome.tabs.sendMessage(tabId, msg);
      } catch {
        return null;
      }
    }
  }

  async function getActiveChatGptTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id || !tab.url || !tab.url.startsWith("https://chatgpt.com")) {
      state.activeTabId = null;
      state.activePageInfo = tab && tab.url ? { isChatGpt: false, url: tab.url } : null;
      return null;
    }

    state.activeTabId = tab.id;
    const urlConvId = Helpers.parseConversationIdFromUrl(tab.url);
    const projectHint = Helpers.parseProjectSlugFromUrl(tab.url);
    const fallbackTitle = Helpers.cleanChatTitle(tab.title || "");

    const live = await sendTabMessageWithAutoInject(tab.id, tab.url, {
      type: "TOWER_GET_PAGE_INFO",
    });
    if (live) {
      state.activePageInfo = {
        isChatGpt: true,
        ...live,
        conversationId: live.conversationId || urlConvId,
        title: Helpers.cleanChatTitle(live.title) || fallbackTitle,
        url: live.url || tab.url,
        projectHint,
      };
    } else {
      state.activePageInfo = {
        isChatGpt: true,
        conversationId: urlConvId,
        title: fallbackTitle,
        url: tab.url,
        turnCount: 0,
        projectHint,
      };
    }
    return { tab, info: state.activePageInfo };
  }

  function currentBinding() {
    const cid = state.activePageInfo && state.activePageInfo.conversationId;
    return cid ? state.bindings[cid] || null : null;
  }

  async function checkHubHealthAndSyncThreads() {
    const pill = $("hubStatusBtn");
    const txt = $("hubStatusText");
    if (!state.settings.bridgeToken) {
      pill.className = "status-pill status-warn";
      txt.textContent = "需配置 Token";
      return;
    }
    try {
      const h = await Api.health(state.settings);
      if (!h || h.database !== "connected") {
        pill.className = "status-pill status-muted";
        txt.textContent = "Hub 异常";
        return;
      }
      const threads = await Api.listThreads(state.settings);
      state.hubThreads = Array.isArray(threads) ? threads : [];
      // Keep local bindings aligned with server truth without erasing unpushed toggles
      let changed = false;
      for (const t of state.hubThreads) {
        if (t.chatgpt_conversation_id) {
          if (t.sync_enabled) {
            const existing = state.bindings[t.chatgpt_conversation_id];
            if (!existing || existing.updated_at !== t.updated_at) {
              state.bindings[t.chatgpt_conversation_id] = t;
              changed = true;
            }
          } else if (state.bindings[t.chatgpt_conversation_id]) {
            delete state.bindings[t.chatgpt_conversation_id];
            changed = true;
          }
        }
      }
      if (changed) {
        await chrome.storage.local.set({ [STORAGE_KEYS.bindings]: state.bindings });
      }
      pill.className = "status-pill status-ok";
      txt.textContent = "Hub 在线";
    } catch (err) {
      if (String(err).includes("401") || String(err).includes("403")) {
        pill.className = "status-pill status-warn";
        txt.textContent = "Token 无效";
      } else {
        pill.className = "status-pill status-muted";
        txt.textContent = "离线";
      }
    }
  }

  function collectProjectOptions() {
    const set = new Set();
    if (state.lastProject) set.add(state.lastProject);
    for (const t of state.hubThreads) {
      if (t.project_id) set.add(t.project_id);
    }
    for (const b of Object.values(state.bindings)) {
      if (b && b.project_id) set.add(b.project_id);
    }
    return Array.from(set).slice(0, 8);
  }

  function renderProjectChips(selectedValue) {
    const container = $("projectChips");
    container.innerHTML = "";
    const options = collectProjectOptions();
    for (const proj of options) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `chip-pick ${proj === selectedValue ? "active" : ""}`.trim();
      chip.textContent = proj;
      chip.addEventListener("click", () => {
        $("quickProject").value = proj;
        renderProjectChips(proj);
      });
      container.appendChild(chip);
    }
  }

  function renderAllThreadsList() {
    const listEl = $("allThreadsList");
    if (!listEl) return;
    listEl.innerHTML = "";
    const threads = state.hubThreads.length
      ? state.hubThreads
      : Object.values(state.bindings);

    if (!threads || threads.length === 0) {
      listEl.innerHTML = '<p class="meta">暂无已绑定的科研工作窗口。</p>';
      return;
    }

    for (const t of threads) {
      const box = document.createElement("div");
      box.className = "item-box";
      const chars = Number(t.uncompressed_char_count || 0).toLocaleString();
      const statusBadge = t.sync_enabled
        ? '<span class="chip chip-ok">同步中</span>'
        : '<span class="chip chip-muted">已暂停</span>';
      box.innerHTML = `
        <div class="row-between">
          <strong>${escapeHtml(t.title || t.planner_thread_id)}</strong>
          ${statusBadge}
        </div>
        <div class="row-between">
          <span class="meta">${escapeHtml(t.project_id || "-")} · 待压缩 ${chars} 字</span>
          <div class="row-gap btn-slot"></div>
        </div>
      `;
      const slot = box.querySelector(".btn-slot");
      if (t.chatgpt_conversation_id || t.chatgpt_url) {
        slot.appendChild(
          makeBtn(
            "打开",
            async () => {
              const allTabs = await chrome.tabs.query({ url: ["https://chatgpt.com/*"] });
              const found = allTabs.find(
                (tab) =>
                  t.chatgpt_conversation_id &&
                  tab.url &&
                  tab.url.includes(t.chatgpt_conversation_id)
              );
              if (found && found.id) {
                await chrome.tabs.update(found.id, { active: true });
                await chrome.windows.update(found.windowId, { focused: true });
              } else if (t.chatgpt_url) {
                await chrome.tabs.create({ url: t.chatgpt_url });
              }
            },
            "btn-xs btn-soft-primary"
          )
        );
      }
      listEl.appendChild(box);
    }
  }

  async function renderTabState() {
    await getActiveChatGptTab();
    const info = state.activePageInfo;
    const emptyEl = $("viewEmptyTab");
    const unboundEl = $("viewUnbound");
    const boundEl = $("viewBound");

    if (!info || !info.isChatGpt || !info.conversationId) {
      emptyEl.classList.remove("hidden");
      unboundEl.classList.add("hidden");
      boundEl.classList.add("hidden");
      state.lastRenderedConvId = null;

      if (!info || !info.isChatGpt) {
        $("emptyStateTitle").textContent = "未打开 ChatGPT 标签页";
        $("emptyStateDesc").textContent =
          "切换到任意 ChatGPT 对话标签页即可直接同步，或从下方进入已绑定的窗口。";
      } else {
        $("emptyStateTitle").textContent = "当前处于 ChatGPT 主页或新建对话";
        $("emptyStateDesc").textContent =
          "进入任意一条具体会话后，这里会自动识别标题与所属项目，支持一键纳入同步。";
      }
      return;
    }

    const cid = info.conversationId;
    const b = state.bindings[cid];

    if (!b || !b.sync_enabled) {
      emptyEl.classList.add("hidden");
      unboundEl.classList.remove("hidden");
      boundEl.classList.add("hidden");

      const convChanged = state.lastRenderedConvId !== `unbound:${cid}`;
      state.lastRenderedConvId = `unbound:${cid}`;
      $("unboundMsgCount").textContent = `当前可见 ${info.turnCount || 0} 条消息`;

      if (convChanged) {
        $("quickTitle").value = info.title || "未命名科研对话";
        $("quickThreadId").value = "";
        $("quickRoleDesc").value = "";
        $("chkQuickAutoSend").checked = false;

        const hintEl = $("projectHintBadge");
        let guessedProject = state.lastProject || "";
        if (info.projectHint) {
          hintEl.classList.remove("hidden");
          const mapped = state.projectGroupMap[info.projectHint.rawGroupKey];
          guessedProject = mapped || guessedProject || info.projectHint.slug;
        } else {
          hintEl.classList.add("hidden");
        }
        $("quickProject").value = guessedProject;
        renderProjectChips(guessedProject);
      }
      return;
    }

    // Bound workspace view
    emptyEl.classList.add("hidden");
    unboundEl.classList.add("hidden");
    boundEl.classList.remove("hidden");
    state.lastRenderedConvId = `bound:${cid}`;

    $("boundTitleDisplay").textContent = b.title || info.title || b.planner_thread_id;
    $("boundProjectChip").textContent = b.project_id || "未分组";
    $("boundThreadIdText").textContent = b.planner_thread_id;
    $("boundTurnBadge").textContent = `页面可见 ${info.turnCount || 0} 轮`;

    $("editTitle").value = b.title || "";
    $("editProject").value = b.project_id || "";
    $("editRoleDesc").value = b.role_description || "";
    $("chkEditAutoSend").checked = Boolean(b.auto_send_enabled);
  }

  async function injectOrCopyText(text, autoSend = false) {
    if (state.activeTabId) {
      const res = await sendTabMessageWithAutoInject(
        state.activeTabId,
        state.activePageInfo?.url,
        { type: "TOWER_FILL_COMPOSER", text, autoSend }
      );
      if (res && res.ok) {
        showToast(autoSend ? "已填入并发送至 ChatGPT" : "已填入 ChatGPT 输入框");
        return true;
      }
    }
    await navigator.clipboard.writeText(text);
    showToast("未定位到输入框，已复制内容到剪贴板", true);
    return false;
  }

  async function refreshInbox() {
    const qCard = $("cardQuestions");
    const qList = $("questionsList");
    const tList = $("triageList");
    const badge = $("inboxBadge");
    if (!state.settings.bridgeToken) {
      qCard.classList.add("hidden");
      badge.classList.add("hidden");
      return;
    }

    qList.innerHTML = "";
    tList.innerHTML = "";
    const b = currentBinding();
    let count = 0;

    if (b) {
      try {
        const inbox = await Api.getInbox(state.settings, b.planner_thread_id);
        const qs = inbox.pending_questions || [];
        count += qs.length;
        for (const q of qs) {
          const box = document.createElement("div");
          box.className = "item-box";
          box.innerHTML = `
            <div class="row-between">
              <strong>[${escapeHtml(q.asked_by_agent)}]</strong>
              <span class="chip chip-soft">${escapeHtml(q.status)}</span>
            </div>
            <div class="decision-body">${escapeHtml(q.question_text)}</div>
            <div class="btn-row"></div>
          `;
          const btnRow = box.querySelector(".btn-row");
          btnRow.append(
            makeBtn(
              "填入提问框",
              async () => {
                await injectOrCopyText(Guard.formatQuestionForChat(q), false);
                await Api.markQuestionInChat(state.settings, q.question_id, b.planner_thread_id);
                await refreshInbox();
              },
              "btn-xs"
            ),
            makeBtn(
              "采纳最新回复解除阻塞",
              async () => {
                await getActiveChatGptTab();
                const ans = state.activePageInfo?.latestAssistantText || "";
                if (!ans) {
                  showToast("页面暂未检测到助手最新回复", true);
                  return;
                }
                await Api.resolveQuestion(state.settings, q.question_id, {
                  answer_text: ans,
                  resume_task: true,
                });
                showToast("已回传回答，阻塞已解除");
                await refreshInbox();
              },
              "btn-xs btn-primary"
            )
          );
          qList.appendChild(box);
        }
      } catch (err) {
        qList.innerHTML = `<p class="meta">提问拉取失败：${escapeHtml(String(err))}</p>`;
      }
    }

    try {
      const triage = await Api.getTriageQuestions(state.settings);
      count += triage.length;
      for (const q of triage) {
        const box = document.createElement("div");
        box.className = "item-box";
        box.innerHTML = `
          <div class="row-between">
            <strong>[${escapeHtml(q.asked_by_agent)}] 未归属提问</strong>
            <span class="chip chip-amber">待分诊</span>
          </div>
          <div class="decision-body">${escapeHtml(q.question_text)}</div>
          <div class="btn-row"></div>
        `;
        if (b) {
          box.querySelector(".btn-row").appendChild(
            makeBtn(
              `归属到「${b.title || b.planner_thread_id}」并填入`,
              async () => {
                await Api.markQuestionInChat(state.settings, q.question_id, b.planner_thread_id);
                await injectOrCopyText(Guard.formatQuestionForChat(q), false);
                await refreshInbox();
              },
              "btn-xs btn-soft-primary"
            )
          );
        }
        tList.appendChild(box);
      }
    } catch {
      // Ignore transient offline triage failure
    }

    $("questionsCountLabel").textContent = `${count} 条等待答复`;
    qCard.classList.toggle("hidden", count === 0);
    badge.textContent = String(count);
    badge.classList.toggle("hidden", count === 0);
  }

  async function refreshCompactionStatus() {
    const txt = $("compactionSummaryText");
    const chip = $("digestCountChip");
    const bar = $("compactionProgressBar");
    const b = currentBinding();
    if (!txt || !b || !state.settings.bridgeToken) return;

    try {
      const [cands, digests] = await Promise.all([
        Api.getCompactionCandidates(state.settings),
        Api.listDigests(state.settings, b.planner_thread_id),
      ]);
      const match = (cands || []).find((c) => c.planner_thread_id === b.planner_thread_id);
      const count = (digests || []).length;
      chip.textContent = `摘要 ${count} 份`;
      if (!match) {
        txt.textContent = "等待数据同步统计";
        bar.style.width = "0%";
        return;
      }
      const chars = Number(match.eligible_char_count || 0);
      const pct = Math.min(100, Math.round((chars / COMPACT_THRESHOLD_CHARS) * 100));
      bar.style.width = `${pct}%`;
      bar.classList.toggle("high", pct >= 85);
      const kText = chars >= 1000 ? `${(chars / 1000).toFixed(1)}K` : String(chars);
      txt.textContent = match.should_compact
        ? `已达压缩阈值：${kText} / 150K 字（等待本机自动执行）`
        : `保护区外：${kText} / 150K 字`;
    } catch {
      txt.textContent = "压缩状态查询失败";
    }
  }

  async function refreshDecisions() {
    const b = currentBinding();
    const pCard = $("proposedCard");
    const pEl = $("proposedDecisionsList");
    const aEl = $("approvedDecisionsList");
    pEl.innerHTML = "";
    aEl.innerHTML = "";
    if (!b || !state.settings.bridgeToken) {
      pCard.classList.add("hidden");
      aEl.innerHTML = '<p class="meta">绑定当前对话窗口后，即可查看并管理本窗口的正式决策。</p>';
      return;
    }

    try {
      const data = await Api.listDecisions(state.settings, b.planner_thread_id, false);
      const all = data.decisions || [];
      const proposed = all.filter((d) => d.status === "PROPOSED");
      const approved = all.filter((d) => d.status === "APPROVED");

      $("proposedCountChip").textContent = String(proposed.length);
      pCard.classList.toggle("hidden", proposed.length === 0);

      for (const d of proposed) {
        const box = document.createElement("div");
        box.className = "item-box";
        box.innerHTML = `
          <div class="row-between">
            <strong>${escapeHtml(d.title)}</strong>
            <span class="chip chip-soft">由 ${escapeHtml(d.proposed_by)} 提议</span>
          </div>
          <div class="decision-body">${escapeHtml(d.decision_content)}</div>
          <div class="btn-row"></div>
        `;
        box.querySelector(".btn-row").append(
          makeBtn(
            "批准生效",
            async () => {
              await Api.publishDecision(state.settings, { decision_id: d.decision_id });
              showToast(`决策「${d.title}」已批准生效`);
              await refreshDecisions();
            },
            "btn-xs btn-primary"
          ),
          makeBtn(
            "驳回",
            async () => {
              await Api.rejectDecision(state.settings, d.decision_id, "Rejected via Chat Bridge");
              showToast("已驳回该提案");
              await refreshDecisions();
            },
            "btn-xs btn-danger-outline"
          )
        );
        pEl.appendChild(box);
      }

      if (approved.length === 0) {
        aEl.innerHTML = '<p class="meta">暂无生效中的正式决策，点击右上角「+ 发布决策」即可拍板锁定。</p>';
      }
      for (const d of approved) {
        const box = document.createElement("div");
        box.className = "item-box";
        box.innerHTML = `
          <div class="row-between">
            <strong>${escapeHtml(d.title)}</strong>
            <span class="chip chip-ok">v${d.decision_version} 生效中</span>
          </div>
          <div class="decision-body">${escapeHtml(d.decision_content)}</div>
          ${d.rationale ? `<p class="meta">依据：${escapeHtml(d.rationale)}</p>` : ""}
          <div class="btn-row"></div>
        `;
        box.querySelector(".btn-row").appendChild(
          makeBtn(
            "更新换代",
            () => {
              $("inputSupersedesId").value = d.decision_id;
              $("inputDecisionTitle").value = d.title;
              $("inputDecisionContent").value = d.decision_content;
              $("inputDecisionRationale").value = d.rationale || "";
              $("decisionFormTitle").textContent = `更新换代 v${d.decision_version}：${d.title}`;
              $("decisionFormWrap").classList.remove("hidden");
            },
            "btn-xs"
          )
        );
        aEl.appendChild(box);
      }
    } catch (err) {
      aEl.innerHTML = `<p class="meta">决策加载失败：${escapeHtml(String(err))}</p>`;
    }
  }

  async function runHistoryBackfill(binding) {
    const hist = await sendTabMessageWithAutoInject(
      state.activeTabId,
      state.activePageInfo?.url,
      { type: "TOWER_FETCH_SAME_ORIGIN_HISTORY" }
    );
    if (hist && hist.ok && Array.isArray(hist.turns) && hist.turns.length > 0) {
      let totalInserted = 0;
      let totalDup = 0;
      for (let i = 0; i < hist.turns.length; i += 50) {
        const chunk = hist.turns.slice(i, i + 50);
        const resp = await Api.syncMessages(state.settings, {
          planner_thread_id: binding.planner_thread_id,
          source_platform: "chatgpt",
          external_conversation_id: binding.chatgpt_conversation_id,
          messages: chunk,
        });
        totalInserted += resp.inserted_count || 0;
        totalDup += resp.duplicate_count || 0;
      }
      return { ok: true, inserted: totalInserted, duplicates: totalDup };
    }
    // Fallback to currently visible DOM turns
    const vis = await sendTabMessageWithAutoInject(
      state.activeTabId,
      state.activePageInfo?.url,
      { type: "TOWER_SYNC_VISIBLE" }
    );
    return {
      ok: Boolean(vis && vis.ok),
      inserted: vis?.count || 0,
      duplicates: 0,
      fallback: true,
    };
  }

  async function injectLatestContext() {
    const b = currentBinding();
    if (!b) return;
    try {
      const q = $("inputSearchQuery").value.trim() || null;
      const res = await Api.loadContext(state.settings, {
        planner_thread_id: b.planner_thread_id,
        search_query: q,
      });
      $("contextPreview").textContent = res.formatted_prompt;
      $("contextPreview").classList.remove("hidden");
      await injectOrCopyText(res.formatted_prompt, false);
    } catch (err) {
      showToast(`拉取上下文失败：${err}`, true);
    }
  }

  async function refreshAllWorkspace() {
    await loadStorage();
    await checkHubHealthAndSyncThreads();
    await renderTabState();
    renderAllThreadsList();
    await Promise.all([refreshInbox(), refreshDecisions(), refreshCompactionStatus()]);
  }

  function scheduleTabRefresh() {
    if (tabSyncTimer) clearTimeout(tabSyncTimer);
    tabSyncTimer = setTimeout(async () => {
      await renderTabState();
      await Promise.all([refreshInbox(), refreshDecisions(), refreshCompactionStatus()]);
    }, 180);
  }

  function wireEvents() {
    document.querySelectorAll(".tab").forEach((tabBtn) => {
      tabBtn.addEventListener("click", () => switchTab(tabBtn.dataset.tab));
    });

    $("hubStatusBtn").addEventListener("click", () => switchTab("hub"));
    $("btnGoSettings").addEventListener("click", () => switchTab("hub"));
    $("btnJumpAllThreads").addEventListener("click", () => switchTab("hub"));
    $("btnOpenChatGpt").addEventListener("click", () => {
      chrome.tabs.create({ url: "https://chatgpt.com/" });
    });

    $("btnRefreshTab").addEventListener("click", async () => {
      await refreshAllWorkspace();
      showToast("状态已刷新");
    });

    $("globalKillSwitch").addEventListener("change", async (e) => {
      state.globalKillSwitch = e.target.checked;
      updateKillSwitchVisual();
      await chrome.storage.local.set({ [STORAGE_KEYS.killSwitch]: state.globalKillSwitch });
      showToast(state.globalKillSwitch ? "已开启全局自动发送急停" : "已解除全局急停");
    });

    $("btnToggleTokenVis").addEventListener("click", () => {
      const inp = $("inputBridgeToken");
      inp.type = inp.type === "password" ? "text" : "password";
    });

    $("settingsForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("btnSaveAndTest");
      btn.disabled = true;
      try {
        state.settings = {
          hubUrl: $("inputHubUrl").value.trim().replace(/\/+$/, ""),
          bridgeToken: $("inputBridgeToken").value.trim(),
        };
        await chrome.storage.local.set({ [STORAGE_KEYS.settings]: state.settings });
        await Api.listThreads(state.settings);
        await refreshAllWorkspace();
        $("settingsStatusMsg").textContent = "验证成功，已保存并连通 Hub。";
        showToast("Hub 连接验证通过");
      } catch (err) {
        $("settingsStatusMsg").textContent = `连接失败：${err}`;
        await checkHubHealthAndSyncThreads();
        showToast("Token 或网络验证失败", true);
      } finally {
        btn.disabled = false;
      }
    });

    // State B: 1-Click Quick Bind + Optional Full Backfill
    $("quickBindForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      await getActiveChatGptTab();
      const info = state.activePageInfo;
      const cid = info?.conversationId;
      if (!cid) {
        showToast("未检测到对话 UUID，请确认位于具体对话页", true);
        return;
      }
      if (!state.settings.bridgeToken) {
        switchTab("hub");
        showToast("请先填写 Bridge Token", true);
        return;
      }

      const title = $("quickTitle").value.trim();
      const project = $("quickProject").value.trim();
      const customTid = $("quickThreadId").value.trim();
      if (customTid && !/^[A-Za-z0-9_-]{2,64}$/.test(customTid)) {
        showToast("自定义窗口 ID 只能包含字母、数字、下划线或短横线", true);
        return;
      }
      const tid = customTid || Helpers.buildAutoThreadId(title, cid);
      const btn = $("btnQuickBind");
      btn.disabled = true;
      btn.textContent = "正在绑定与同步…";

      try {
        const saved = await Api.registerThread(state.settings, {
          planner_thread_id: tid,
          project_id: project,
          title,
          role_description: $("quickRoleDesc").value.trim(),
          chatgpt_conversation_id: cid,
          chatgpt_url: info.url,
          sync_enabled: true,
          auto_send_enabled: $("chkQuickAutoSend").checked,
        });
        state.bindings[cid] = saved;
        state.lastProject = project;
        if (info.projectHint?.rawGroupKey) {
          state.projectGroupMap[info.projectHint.rawGroupKey] = project;
        }
        await chrome.storage.local.set({
          [STORAGE_KEYS.bindings]: state.bindings,
          [STORAGE_KEYS.lastProject]: state.lastProject,
          [STORAGE_KEYS.projectGroupMap]: state.projectGroupMap,
        });
        await chrome.runtime.sendMessage({ type: "TOWER_NOTIFY_TAB_BINDING" });

        if ($("chkQuickBackfill").checked) {
          const bf = await runHistoryBackfill(saved);
          showToast(
            bf.ok
              ? `已绑定「${saved.title}」，同步 ${bf.inserted} 条（跳过重复 ${bf.duplicates} 条）`
              : `已绑定「${saved.title}」，后续对话将实时增量同步`
          );
        } else {
          showToast(`已纳入同步：「${saved.title}」`);
        }
        await refreshAllWorkspace();
      } catch (err) {
        showToast(`绑定失败：${err}`, true);
      } finally {
        btn.disabled = false;
        btn.textContent = "一键纳入巴别塔并同步";
      }
    });

    // State C: Save updates or Unbind
    $("boundEditForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const b = currentBinding();
      if (!b) return;
      try {
        const saved = await Api.registerThread(state.settings, {
          planner_thread_id: b.planner_thread_id,
          project_id: $("editProject").value.trim(),
          title: $("editTitle").value.trim(),
          role_description: $("editRoleDesc").value.trim(),
          chatgpt_conversation_id: b.chatgpt_conversation_id,
          chatgpt_url: state.activePageInfo?.url || b.chatgpt_url,
          sync_enabled: true,
          auto_send_enabled: $("chkEditAutoSend").checked,
        });
        state.bindings[b.chatgpt_conversation_id] = saved;
        await chrome.storage.local.set({ [STORAGE_KEYS.bindings]: state.bindings });
        await chrome.runtime.sendMessage({ type: "TOWER_NOTIFY_TAB_BINDING" });
        $("boundSettingsFold").open = false;
        showToast("窗口设置已保存");
        await refreshAllWorkspace();
      } catch (err) {
        showToast(`保存失败：${err}`, true);
      }
    });

    $("btnUnbind").addEventListener("click", async () => {
      const b = currentBinding();
      if (!b || !b.chatgpt_conversation_id) return;
      try {
        await Api.registerThread(state.settings, {
          ...b,
          sync_enabled: false,
          auto_send_enabled: false,
        });
      } catch {
        // Always allow local unlink even when offline
      }
      delete state.bindings[b.chatgpt_conversation_id];
      await chrome.storage.local.set({ [STORAGE_KEYS.bindings]: state.bindings });
      await chrome.runtime.sendMessage({ type: "TOWER_NOTIFY_TAB_BINDING" });
      showToast("已将该窗口移出白名单");
      await refreshAllWorkspace();
    });

    $("btnCopyThreadId").addEventListener("click", async () => {
      const b = currentBinding();
      if (!b) return;
      await navigator.clipboard.writeText(b.planner_thread_id);
      showToast(`已复制窗口 ID：${b.planner_thread_id}`);
    });

    $("btnCopyStartPrompt").addEventListener("click", async () => {
      const b = currentBinding();
      if (!b) return;
      await navigator.clipboard.writeText(Helpers.buildStartPrompt(b));
      showToast("已复制开工口令，可直接发给 Gemini / DeepSeek / GPT");
    });

    $("btnQuickInjectContext").addEventListener("click", injectLatestContext);
    $("btnLoadContext").addEventListener("click", injectLatestContext);

    $("btnSyncVisible").addEventListener("click", async () => {
      if (!state.activeTabId) return;
      const res = await sendTabMessageWithAutoInject(
        state.activeTabId,
        state.activePageInfo?.url,
        { type: "TOWER_SYNC_VISIBLE" }
      );
      showToast(
        res && res.ok ? `已触发可见消息同步（${res.count || 0} 条）` : "当前窗口未启用同步",
        !(res && res.ok)
      );
      await refreshCompactionStatus();
    });

    $("btnBackfillHistory").addEventListener("click", async () => {
      const b = currentBinding();
      if (!b) return;
      const btn = $("btnBackfillHistory");
      btn.disabled = true;
      try {
        const bf = await runHistoryBackfill(b);
        showToast(
          bf.ok
            ? `补齐完成：新增 ${bf.inserted} 条（跳过重复 ${bf.duplicates} 条）`
            : "未能读取同源历史，请确保已登录 ChatGPT",
          !bf.ok
        );
        await refreshCompactionStatus();
      } finally {
        btn.disabled = false;
      }
    });

    $("btnFlushQueue").addEventListener("click", async () => {
      const res = await chrome.runtime.sendMessage({ type: "TOWER_FLUSH_QUEUE" });
      await loadStorage();
      showToast(`重传完成：发送 ${res.sentCount} 批，剩余 ${res.remaining} 批`);
    });

    $("btnRefreshInbox").addEventListener("click", refreshInbox);
    $("btnRefreshDecisions").addEventListener("click", refreshDecisions);
    $("btnRefreshCompaction").addEventListener("click", refreshCompactionStatus);
    $("btnRefreshAllThreads").addEventListener("click", async () => {
      await checkHubHealthAndSyncThreads();
      renderAllThreadsList();
      showToast("窗口列表已更新");
    });

    $("btnSearchDialogue").addEventListener("click", async () => {
      const q = $("inputSearchQuery").value.trim();
      if (!q) return;
      const b = currentBinding();
      try {
        const hits = await Api.searchDialogues(state.settings, q, b?.project_id || null);
        const formatted = hits.length
          ? hits
              .map((h) => `[${h.planner_thread_id} #${h.turn_index} ${h.role}]\n${h.content}`)
              .join("\n\n---\n\n")
          : "未检索到匹配的历史对话片段。";
        $("contextPreview").textContent = formatted;
        $("contextPreview").classList.remove("hidden");
      } catch (err) {
        showToast(`检索失败：${err}`, true);
      }
    });

    $("btnToggleNewDecision").addEventListener("click", () => {
      const wrap = $("decisionFormWrap");
      $("inputSupersedesId").value = "";
      $("decisionForm").reset();
      $("decisionFormTitle").textContent = "发布新正式决策";
      wrap.classList.toggle("hidden");
    });

    $("btnCancelDecisionForm").addEventListener("click", () => {
      $("decisionFormWrap").classList.add("hidden");
      $("inputSupersedesId").value = "";
    });

    $("decisionForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const b = currentBinding();
      if (!b) return;
      try {
        await Api.publishDecision(state.settings, {
          project_id: b.project_id,
          planner_thread_id: b.planner_thread_id,
          title: $("inputDecisionTitle").value.trim(),
          decision_content: $("inputDecisionContent").value.trim(),
          rationale: $("inputDecisionRationale").value.trim(),
          rejected_alternatives: $("inputDecisionRejected").value.trim(),
          supersedes_decision_id: $("inputSupersedesId").value.trim() || null,
        });
        $("decisionForm").reset();
        $("inputSupersedesId").value = "";
        $("decisionFormWrap").classList.add("hidden");
        showToast("正式决策已批准并生效");
        await refreshDecisions();
      } catch (err) {
        showToast(`发布失败：${err}`, true);
      }
    });

    if (chrome.tabs && chrome.tabs.onActivated) {
      chrome.tabs.onActivated.addListener(() => scheduleTabRefresh());
    }
    if (chrome.tabs && chrome.tabs.onUpdated) {
      chrome.tabs.onUpdated.addListener((_id, changeInfo, tab) => {
        if (tab && tab.active && (changeInfo.url || changeInfo.title || changeInfo.status === "complete")) {
          scheduleTabRefresh();
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    wireEvents();
    await refreshAllWorkspace();
  });
})();
