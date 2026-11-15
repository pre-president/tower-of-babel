/**
 * 4-Layer Safety Guardrails for optional whitelisted auto-send.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TowerAutoSendGuard = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const COOLDOWN_MS = 15000;
  const MAX_CONSECUTIVE_AUTO_SENDS = 5;

  function checkAutoSendAllowed({
    globalKillSwitch = false,
    thread = null,
    pageState = null,
    question = null,
    guardState = null,
    nowMs = Date.now(),
  }) {
    if (globalKillSwitch) {
      return { allowed: false, reason: "GLOBAL_KILL_SWITCH_ACTIVE" };
    }
    if (!thread || !thread.sync_enabled) {
      return { allowed: false, reason: "THREAD_NOT_WHITELISTED" };
    }
    if (!thread.auto_send_enabled) {
      return { allowed: false, reason: "AUTO_SEND_DISABLED_FOR_THREAD" };
    }
    if (!question || question.status !== "PENDING") {
      return { allowed: false, reason: "QUESTION_NOT_PENDING" };
    }
    if (!pageState || pageState.isGenerating || pageState.composerHasText) {
      return { allowed: false, reason: "PAGE_BUSY_OR_USER_TYPING" };
    }

    const st = guardState || {};
    const consecutive = Number(st.consecutiveAutoSends || 0);
    if (consecutive >= MAX_CONSECUTIVE_AUTO_SENDS) {
      return { allowed: false, reason: "CIRCUIT_BREAKER_TRIPPED" };
    }

    const lastSentAt = Number(st.lastSentAtMs || 0);
    if (lastSentAt > 0 && nowMs - lastSentAt < COOLDOWN_MS) {
      return {
        allowed: false,
        reason: "COOLDOWN_ACTIVE",
        retryAfterMs: COOLDOWN_MS - (nowMs - lastSentAt),
      };
    }

    const recentQuestionIds = Array.isArray(st.sentQuestionIds) ? st.sentQuestionIds : [];
    if (recentQuestionIds.includes(question.question_id)) {
      return { allowed: false, reason: "ANTI_LOOP_DUPLICATE_QUESTION" };
    }

    return { allowed: true, reason: "OK" };
  }

  function recordAutoSend(guardState, questionId, nowMs = Date.now()) {
    const prev = guardState || {};
    const sentIds = Array.isArray(prev.sentQuestionIds) ? prev.sentQuestionIds.slice(-30) : [];
    if (questionId && !sentIds.includes(questionId)) {
      sentIds.push(questionId);
    }
    return {
      consecutiveAutoSends: Number(prev.consecutiveAutoSends || 0) + 1,
      lastSentAtMs: nowMs,
      sentQuestionIds: sentIds,
      activeAutoQuestionId: questionId || null,
    };
  }

  function clearActiveAutoQuestion(guardState) {
    const prev = guardState || {};
    return {
      ...prev,
      activeAutoQuestionId: null,
    };
  }

  function recordHumanInteraction(guardState) {
    const prev = guardState || {};
    return {
      ...prev,
      consecutiveAutoSends: 0,
    };
  }

  function formatQuestionForChat(question) {
    const lines = [
      `[Tower of Babel 执行节点阻塞提问 | ${question.asked_by_agent || "agent"} | task=${question.task_id || "none"} | q=${question.question_id}]`,
      "",
      question.question_text || "",
    ];
    if (question.evidence_context) {
      lines.push("", `**现场证据 / 上下文**：${question.evidence_context}`);
    }
    if (question.impact_note) {
      lines.push(`**决策影响**：${question.impact_note}`);
    }
    lines.push("", "请针对以上问题给出明确拍板结论（如有更新旧方案请明确指出）。");
    return lines.join("\n");
  }

  return {
    COOLDOWN_MS,
    MAX_CONSECUTIVE_AUTO_SENDS,
    checkAutoSendAllowed,
    recordAutoSend,
    clearActiveAutoQuestion,
    recordHumanInteraction,
    formatQuestionForChat,
  };
});
