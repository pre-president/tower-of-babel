/**
 * Background Service Worker (Manifest V3).
 * Coordinates whitelist bindings, offline retry queue, and 4-layer guarded auto-send.
 */
importScripts(
  "../shared/api-client.js",
  "../shared/auto-send-guard.js",
  "../shared/retry-queue.js"
);

const Api = globalThis.TowerApiClient;
const Guard = globalThis.TowerAutoSendGuard;
const RetryQ = globalThis.TowerRetryQueue;

const DEFAULT_SETTINGS = {
  hubUrl: "https://hub.atmosphericduct.xyz",
  bridgeToken: "",
};

const STORAGE_KEYS = {
  settings: "tower_settings",
  bindings: "tower_bindings",
  retryQueue: "tower_retry_queue",
  killSwitch: "tower_global_kill_switch",
  guardState: "tower_autosend_guard_state",
};

async function getState() {
  const raw = await chrome.storage.local.get(Object.values(STORAGE_KEYS));
  return {
    settings: { ...DEFAULT_SETTINGS, ...(raw[STORAGE_KEYS.settings] || {}) },
    bindings: raw[STORAGE_KEYS.bindings] || {},
    retryQueue: raw[STORAGE_KEYS.retryQueue] || [],
    globalKillSwitch: Boolean(raw[STORAGE_KEYS.killSwitch]),
    guardByThread: raw[STORAGE_KEYS.guardState] || {},
  };
}

async function flushRetryQueue() {
  const st = await getState();
  if (!st.settings.bridgeToken || st.retryQueue.length === 0) {
    return { sentCount: 0, remaining: st.retryQueue.length };
  }
  const { remaining, sentCount } = await RetryQ.flushQueue(st.retryQueue, (payload) =>
    Api.syncMessages(st.settings, payload)
  );
  await chrome.storage.local.set({ [STORAGE_KEYS.retryQueue]: remaining });
  return { sentCount, remaining: remaining.length };
}

async function syncOrQueueBatch(batch) {
  const st = await getState();
  if (!st.settings.bridgeToken) {
    const q = RetryQ.enqueueBatch(st.retryQueue, batch);
    await chrome.storage.local.set({ [STORAGE_KEYS.retryQueue]: q });
    return { queued: true, reason: "NO_BRIDGE_TOKEN" };
  }
  try {
    const res = await Api.syncMessages(st.settings, batch);
    return { queued: false, res };
  } catch (err) {
    const q = RetryQ.enqueueBatch(st.retryQueue, batch);
    await chrome.storage.local.set({ [STORAGE_KEYS.retryQueue]: q });
    return { queued: true, reason: String(err) };
  }
}

async function sendToTabWithInject(tabId, tabUrl, msg) {
  try {
    return await chrome.tabs.sendMessage(tabId, msg);
  } catch {
    if (!tabUrl || !tabUrl.startsWith("https://chatgpt.com")) {
      return null;
    }
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [
          "src/shared/history-tree.js",
          "src/content/dom-adapter.js",
          "src/content/content-script.js",
        ],
      });
      return await chrome.tabs.sendMessage(tabId, msg);
    } catch {
      return null;
    }
  }
}

async function checkAndAutoSendForOpenTabs() {
  const st = await getState();
  if (st.globalKillSwitch || !st.settings.bridgeToken) return;

  const tabs = await chrome.tabs.query({ url: ["https://chatgpt.com/*"] });
  for (const tab of tabs) {
    if (!tab.id || !tab.url) continue;
    const match = tab.url.match(/\/c\/([0-9a-fA-F-]{8,})/);
    if (!match) continue;
    const convId = match[1];
    const binding = st.bindings[convId];
    if (!binding || !binding.sync_enabled || !binding.auto_send_enabled) continue;

    const pageInfo = await sendToTabWithInject(tab.id, tab.url, { type: "TOWER_GET_PAGE_INFO" });
    if (!pageInfo) continue;

    let inbox;
    try {
      inbox = await Api.getInbox(st.settings, binding.planner_thread_id);
    } catch {
      continue;
    }

    // Wait for first IN_CHAT question? No auto-send if one is already waiting in chat
    const inChat = (inbox.pending_questions || []).find((q) => q.status === "IN_CHAT");
    if (inChat) continue;

    const nextQ = (inbox.pending_questions || []).find((q) => q.status === "PENDING");
    if (!nextQ) continue;

    const threadGuard = st.guardByThread[binding.planner_thread_id] || {};
    const check = Guard.checkAutoSendAllowed({
      globalKillSwitch: st.globalKillSwitch,
      thread: binding,
      pageState: pageInfo,
      question: nextQ,
      guardState: threadGuard,
    });
    if (!check.allowed) continue;

    const text = Guard.formatQuestionForChat(nextQ);
    const fillResp = await sendToTabWithInject(tab.id, tab.url, {
      type: "TOWER_FILL_COMPOSER",
      text,
      autoSend: true,
    });
    if (fillResp && fillResp.ok) {
      await Api.markQuestionInChat(st.settings, nextQ.question_id, binding.planner_thread_id);
      st.guardByThread[binding.planner_thread_id] = Guard.recordAutoSend(
        threadGuard,
        nextQ.question_id
      );
      await chrome.storage.local.set({ [STORAGE_KEYS.guardState]: st.guardByThread });
    }
  }
}

chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => { });
  }
  chrome.alarms.create("tower_hub_tick", { periodInMinutes: 1 });
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "tower_hub_tick") {
    await flushRetryQueue();
    await checkAndAutoSendForOpenTabs();
  }
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || !msg.type) return false;

  if (msg.type === "TOWER_QUERY_BINDING") {
    getState().then((st) => {
      sendResponse({ binding: st.bindings[msg.conversationId] || null });
    });
    return true;
  }

  if (msg.type === "TOWER_SYNC_BATCH") {
    (async () => {
      const result = await syncOrQueueBatch(msg.payload);
      if (!result.queued && msg.latestAssistantText) {
        const st = await getState();
        const tid = msg.payload?.planner_thread_id;
        const g = tid ? st.guardByThread[tid] : null;
        if (tid && g && g.activeAutoQuestionId) {
          try {
            await Api.resolveQuestion(st.settings, g.activeAutoQuestionId, {
              answer_text: msg.latestAssistantText,
              resume_task: true,
            });
            st.guardByThread[tid] = Guard.clearActiveAutoQuestion(g);
            await chrome.storage.local.set({ [STORAGE_KEYS.guardState]: st.guardByThread });
          } catch {
            // Keep question pending/in-chat if already resolved manually
          }
        }
      }
      sendResponse(result);
    })();
    return true;
  }

  if (msg.type === "TOWER_HUMAN_INTERACTION") {
    getState().then(async (st) => {
      if (msg.plannerThreadId) {
        st.guardByThread[msg.plannerThreadId] = Guard.recordHumanInteraction(
          st.guardByThread[msg.plannerThreadId]
        );
        await chrome.storage.local.set({ [STORAGE_KEYS.guardState]: st.guardByThread });
      }
      sendResponse({ ok: true });
    });
    return true;
  }

  if (msg.type === "TOWER_FLUSH_QUEUE") {
    flushRetryQueue().then((res) => sendResponse(res));
    return true;
  }

  if (msg.type === "TOWER_NOTIFY_TAB_BINDING") {
    chrome.tabs.query({ url: ["https://chatgpt.com/*"] }).then(async (tabs) => {
      for (const tab of tabs) {
        if (tab.id) {
          sendToTabWithInject(tab.id, tab.url, { type: "TOWER_BINDING_UPDATED" }).catch(() => { });
        }
      }
      sendResponse({ ok: true });
    });
    return true;
  }

  return false;
});
