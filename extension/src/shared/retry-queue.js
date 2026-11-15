/**
 * Offline/restart retry queue for incremental dialogue uploads.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TowerRetryQueue = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const MAX_QUEUE_BATCHES = 100;

  function enqueueBatch(queue, batch) {
    const next = Array.isArray(queue) ? [...queue] : [];
    const signature = JSON.stringify({
      t: batch.planner_thread_id,
      c: batch.external_conversation_id,
      m: (batch.messages || []).map((x) => [x.role, x.external_message_id || "", x.content.slice(0, 80)]),
    });
    if (next.some((item) => item.signature === signature)) {
      return next;
    }
    next.push({
      id: `q_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      signature,
      payload: batch,
      attempts: 0,
      enqueuedAt: Date.now(),
    });
    if (next.length > MAX_QUEUE_BATCHES) {
      return next.slice(-MAX_QUEUE_BATCHES);
    }
    return next;
  }

  async function flushQueue(queue, senderFn) {
    const remaining = [];
    let sentCount = 0;
    let failedCount = 0;
    const list = Array.isArray(queue) ? queue : [];

    for (let i = 0; i < list.length; i += 1) {
      const item = list[i];
      try {
        await senderFn(item.payload);
        sentCount += 1;
      } catch (err) {
        failedCount += 1;
        remaining.push({ ...item, attempts: (item.attempts || 0) + 1, lastError: String(err) });
        // Preserve strict chronological order on failure by deferring tail items too
        for (let j = i + 1; j < list.length; j += 1) {
          remaining.push(list[j]);
        }
        break;
      }
    }
    return { remaining, sentCount, failedCount };
  }

  return { enqueueBatch, flushQueue, MAX_QUEUE_BATCHES };
});
