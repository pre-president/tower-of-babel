/**
 * URL parsing, title cleanup, and ergonomic ID generators for Tower of Babel Chat Bridge.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TowerThreadHelpers = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  function parseConversationIdFromUrl(urlStr) {
    if (!urlStr) return null;
    try {
      const u = new URL(urlStr);
      if (!u.hostname.endsWith("chatgpt.com") && !u.hostname.endsWith("chat.openai.com")) {
        return null;
      }
      const m = u.pathname.match(/\/c\/([0-9a-fA-F-]{8,})/);
      return m ? m[1] : null;
    } catch {
      return null;
    }
  }

  function parseProjectSlugFromUrl(urlStr) {
    if (!urlStr) return null;
    try {
      const u = new URL(urlStr);
      if (!u.hostname.endsWith("chatgpt.com") && !u.hostname.endsWith("chat.openai.com")) {
        return null;
      }
      // Example: /g/g-p-6ad802679d748191b0f1a532ffbd38f5-da-qi-he-yan-jiu/c/<uuid>
      const m = u.pathname.match(/\/g\/(g-p-[0-9a-fA-F]+)(?:-([^/]+))?/);
      if (!m) return null;
      return {
        rawGroupKey: m[1],
        slug: m[2] || m[1],
      };
    } catch {
      return null;
    }
  }

  function cleanChatTitle(rawTitle) {
    return String(rawTitle || "")
      .replace(/\s*[-|]\s*ChatGPT$/i, "")
      .trim();
  }

  function buildAutoThreadId(title, conversationId) {
    const shortId = String(conversationId || "")
      .replace(/[^A-Za-z0-9]/g, "")
      .slice(0, 8)
      .toUpperCase();
    const asciiSlug = String(title || "")
      .toUpperCase()
      .replace(/[^A-Z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 28);
    if (asciiSlug && shortId) {
      return `CHAT_${asciiSlug}_${shortId}`.slice(0, 64);
    }
    if (shortId) {
      return `CHAT_${shortId}`;
    }
    return `CHAT_${asciiSlug || "MAIN"}`.slice(0, 64);
  }

  function buildStartPrompt(thread) {
    const title = thread.title || thread.planner_thread_id;
    return `请先通过 Tower of Babel 读取规划窗口「${title}」（ID: ${thread.planner_thread_id}）的最新生效决策、摘要与近期对话上下文，然后再开始执行工作。`;
  }

  return {
    parseConversationIdFromUrl,
    parseProjectSlugFromUrl,
    cleanChatTitle,
    buildAutoThreadId,
    buildStartPrompt,
  };
});
