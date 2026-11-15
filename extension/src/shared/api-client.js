/**
 * Bridge REST API client for Tower of Babel Central Hub.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TowerApiClient = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  async function request(settings, method, path, body = null) {
    const base = String(settings.hubUrl || "https://hub.atmosphericduct.xyz").replace(/\/+$/, "");
    const url = `${base}${path}`;
    const headers = { "Content-Type": "application/json" };
    if (settings.bridgeToken) {
      headers.Authorization = `Bearer ${settings.bridgeToken}`;
    }
    const resp = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await resp.text();
    let parsed = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = text;
    }
    if (!resp.ok) {
      const detail = parsed && parsed.detail ? parsed.detail : text || `HTTP ${resp.status}`;
      const err = new Error(`${resp.status}: ${detail}`);
      err.statusCode = resp.status;
      throw err;
    }
    return parsed;
  }

  return {
    health: (s) => request(s, "GET", "/health"),
    listThreads: (s) => request(s, "GET", "/api/v1/bridge/threads"),
    registerThread: (s, payload) => request(s, "POST", "/api/v1/bridge/threads/register", payload),
    syncMessages: (s, payload) => request(s, "POST", "/api/v1/bridge/messages/sync", payload),
    getInbox: (s, threadId) =>
      request(s, "GET", `/api/v1/bridge/threads/${encodeURIComponent(threadId)}/inbox`),
    getTriageQuestions: (s) => request(s, "GET", "/api/v1/bridge/inbox/triage"),
    markQuestionInChat: (s, qId, threadId) =>
      request(s, "POST", `/api/v1/bridge/questions/${encodeURIComponent(qId)}/in-chat`, {
        planner_thread_id: threadId || null,
      }),
    resolveQuestion: (s, qId, payload) =>
      request(s, "POST", `/api/v1/bridge/questions/${encodeURIComponent(qId)}/resolve`, payload),
    loadContext: (s, payload) => request(s, "POST", "/api/v1/bridge/context/load", payload),
    searchDialogues: (s, q, projectId = null) => {
      const params = new URLSearchParams({ q, limit: "8" });
      if (projectId) params.set("project_id", projectId);
      return request(s, "GET", `/api/v1/hub/dialogues/search?${params.toString()}`);
    },
    listDecisions: (s, threadId, activeOnly = false) => {
      const params = new URLSearchParams({
        planner_thread_id: threadId,
        active_only: activeOnly ? "true" : "false",
      });
      return request(s, "GET", `/api/v1/hub/decisions?${params.toString()}`);
    },
    publishDecision: (s, payload) => request(s, "POST", "/api/v1/bridge/decisions/publish", payload),
    rejectDecision: (s, decisionId, reason = "") =>
      request(s, "POST", `/api/v1/bridge/decisions/${encodeURIComponent(decisionId)}/reject`, {
        reason,
      }),
    getCompactionCandidates: (s) =>
      request(s, "GET", "/api/v1/bridge/compaction/candidates"),
    listDigests: (s, threadId) =>
      request(s, "GET", `/api/v1/hub/threads/${encodeURIComponent(threadId)}/digests`),
  };
});
