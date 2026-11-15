/**
 * Content Script running on https://chatgpt.com/*.
 * Strict whitelist rule: unbound windows are completely ignored with zero DOM observation.
 */
(() => {
  if (globalThis.__TOWER_CONTENT_SCRIPT_READY__) {
    return;
  }
  globalThis.__TOWER_CONTENT_SCRIPT_READY__ = true;

  const Dom = globalThis.TowerDomAdapter;
  const Tree = globalThis.TowerHistoryTree;

  let currentConvId = Dom.parseConversationIdFromUrl(window.location.href);
  let activeBinding = null;
  let observer = null;
  let settleTimer = null;
  let lastTurnSignature = "";

  function computeSignature(msgs) {
    if (!msgs || msgs.length === 0) return "0";
    const last = msgs[msgs.length - 1];
    return `${msgs.length}:${last.role}:${last.external_message_id}:${last.content.length}`;
  }

  function syncVisibleNow(reason = "manual") {
    if (!activeBinding || !activeBinding.sync_enabled) {
      return { ok: false, reason: "NOT_WHITELISTED" };
    }
    const messages = Dom.extractVisibleMessages(document);
    const sig = computeSignature(messages);
    if (reason !== "force" && sig === lastTurnSignature) {
      return { ok: true, skipped: true, count: messages.length };
    }
    lastTurnSignature = sig;
    if (messages.length === 0) {
      return { ok: true, count: 0 };
    }
    chrome.runtime.sendMessage({
      type: "TOWER_SYNC_BATCH",
      payload: {
        planner_thread_id: activeBinding.planner_thread_id,
        source_platform: "chatgpt",
        external_conversation_id: currentConvId,
        messages,
      },
      latestAssistantText:
        [...messages].reverse().find((m) => m.role === "assistant")?.content || "",
    });
    return { ok: true, count: messages.length };
  }

  function scheduleSettledCapture() {
    if (!activeBinding || !activeBinding.sync_enabled) return;
    if (settleTimer) clearTimeout(settleTimer);
    // Secondary fallback: wait until stop button disappears + 3s idle stability
    settleTimer = setTimeout(() => {
      if (!Dom.isGenerating(document)) {
        syncVisibleNow("settled_3s");
      } else {
        scheduleSettledCapture();
      }
    }, 3000);
  }

  function startWhitelistObservers() {
    stopObservers();
    if (!activeBinding || !activeBinding.sync_enabled) return;

    observer = new MutationObserver(() => {
      scheduleSettledCapture();
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function stopObservers() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (settleTimer) {
      clearTimeout(settleTimer);
      settleTimer = null;
    }
  }

  async function refreshBindingForCurrentUrl() {
    currentConvId = Dom.parseConversationIdFromUrl(window.location.href);
    lastTurnSignature = "";
    if (!currentConvId) {
      activeBinding = null;
      stopObservers();
      return;
    }
    try {
      const resp = await chrome.runtime.sendMessage({
        type: "TOWER_QUERY_BINDING",
        conversationId: currentConvId,
      });
      activeBinding = resp && resp.binding && resp.binding.sync_enabled ? resp.binding : null;
      if (activeBinding) {
        startWhitelistObservers();
      } else {
        stopObservers();
      }
    } catch {
      activeBinding = null;
      stopObservers();
    }
  }

  // Primary trigger: submitting next prompt means previous turn is 100% finished
  document.addEventListener(
    "keydown",
    (ev) => {
      if (!activeBinding || !activeBinding.sync_enabled) return;
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
        const composer = Dom.getComposerElement(document);
        if (composer && (ev.target === composer || composer.contains(ev.target))) {
          syncVisibleNow("before_next_send");
          chrome.runtime.sendMessage({
            type: "TOWER_HUMAN_INTERACTION",
            plannerThreadId: activeBinding.planner_thread_id,
          });
        }
      }
    },
    true
  );

  document.addEventListener(
    "click",
    (ev) => {
      if (!activeBinding || !activeBinding.sync_enabled) return;
      const sendBtn = Dom.getSendButton(document);
      if (sendBtn && (ev.target === sendBtn || sendBtn.contains(ev.target))) {
        syncVisibleNow("before_send_click");
        if (ev.isTrusted) {
          chrome.runtime.sendMessage({
            type: "TOWER_HUMAN_INTERACTION",
            plannerThreadId: activeBinding.planner_thread_id,
          });
        }
      }
    },
    true
  );

  async function fetchSameOriginHistory(conversationId) {
    if (!conversationId) {
      return { ok: false, error: "No conversation ID found in current URL" };
    }
    let headers = {};
    try {
      const sessResp = await fetch("/api/auth/session", { credentials: "include" });
      if (sessResp.ok) {
        const sess = await sessResp.json();
        if (sess && sess.accessToken) {
          headers.Authorization = `Bearer ${sess.accessToken}`;
        }
      }
    } catch {
      // Fallback to cookie-only same-origin request
    }

    const convResp = await fetch(`/backend-api/conversation/${encodeURIComponent(conversationId)}`, {
      credentials: "include",
      headers,
    });
    if (!convResp.ok) {
      return { ok: false, error: `HTTP ${convResp.status} reading conversation tree` };
    }
    const convJson = await convResp.json();
    const turns = Tree.linearizeConversation(convJson);
    return {
      ok: true,
      title: convJson.title || document.title,
      turns,
    };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || !msg.type) return false;

    if (msg.type === "TOWER_BINDING_UPDATED") {
      refreshBindingForCurrentUrl().then(() => sendResponse({ ok: true }));
      return true;
    }

    if (msg.type === "TOWER_GET_PAGE_INFO") {
      const visible = Dom.extractVisibleMessages(document);
      const latestAssistant = [...visible].reverse().find((m) => m.role === "assistant");
      sendResponse({
        conversationId: Dom.parseConversationIdFromUrl(window.location.href),
        url: window.location.href,
        title: document.title.replace(/\s*[-|]\s*ChatGPT$/i, "").trim(),
        isGenerating: Dom.isGenerating(document),
        composerHasText: Boolean(Dom.readComposerText(document)),
        turnCount: visible.length,
        latestAssistantText: latestAssistant ? latestAssistant.content : "",
      });
      return false;
    }

    if (msg.type === "TOWER_SYNC_VISIBLE") {
      sendResponse(syncVisibleNow("force"));
      return false;
    }

    if (msg.type === "TOWER_FILL_COMPOSER") {
      const fill = Dom.setComposerText(document, msg.text || "");
      if (!fill.ok) {
        sendResponse(fill);
        return false;
      }
      if (msg.autoSend) {
        setTimeout(() => {
          const btn = Dom.getSendButton(document);
          if (btn && !btn.disabled) {
            btn.click();
          }
        }, 120);
      }
      sendResponse({ ok: true, autoSent: Boolean(msg.autoSend) });
      return false;
    }

    if (msg.type === "TOWER_FETCH_SAME_ORIGIN_HISTORY") {
      const cid = Dom.parseConversationIdFromUrl(window.location.href);
      fetchSameOriginHistory(cid)
        .then((res) => sendResponse(res))
        .catch((err) => sendResponse({ ok: false, error: String(err) }));
      return true;
    }

    return false;
  });

  // Watch ChatGPT SPA URL changes
  let lastUrl = window.location.href;
  setInterval(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      refreshBindingForCurrentUrl();
    }
  }, 1200);

  refreshBindingForCurrentUrl();
})();
