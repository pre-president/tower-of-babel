/**
 * Pure ChatGPT backend-api conversation tree linearizer.
 * Converts mapping graph nodes -> chronological visible user/assistant turns.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TowerHistoryTree = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const ALLOWED_CONTENT_TYPES = new Set(["text", "multimodal_text"]);

  function isVisibleDialogueMessage(msg) {
    if (!msg) return false;
    if (msg.metadata && msg.metadata.is_visually_hidden_from_conversation) return false;

    const role = msg.author && msg.author.role;
    if (role !== "user" && role !== "assistant") return false;

    // Exclude internal ChatGPT tool calls (python, file_search, web, browser, canmore, etc.)
    if (typeof msg.recipient === "string" && msg.recipient.trim() !== "" && msg.recipient !== "all") {
      return false;
    }
    // Exclude hidden reasoning commentary preambles before final reply
    if (typeof msg.channel === "string" && msg.channel.trim() !== "" && msg.channel !== "final") {
      return false;
    }
    const ct = msg.content && msg.content.content_type;
    if (typeof ct === "string" && ct.trim() !== "" && !ALLOWED_CONTENT_TYPES.has(ct)) {
      return false;
    }
    return true;
  }

  function extractPartsText(message) {
    if (!message || !message.content) return "";
    const content = message.content;

    const ct = content.content_type;
    if (typeof ct === "string" && ct.trim() !== "" && !ALLOWED_CONTENT_TYPES.has(ct)) {
      return "";
    }
    if (typeof content.text === "string" && content.text.trim()) {
      return content.text.trim();
    }
    if (Array.isArray(content.parts)) {
      const pieces = [];
      for (const part of content.parts) {
        if (typeof part === "string" && part.trim()) {
          pieces.push(part);
        } else if (part && typeof part === "object" && typeof part.text === "string" && part.text.trim()) {
          pieces.push(part.text);
        }
      }
      return pieces.join("\n").trim();
    }
    return "";
  }

  function linearizeConversation(convJson) {
    if (!convJson || typeof convJson !== "object" || !convJson.mapping) {
      return [];
    }
    const mapping = convJson.mapping;
    let cursor = convJson.current_node;
    const chain = [];
    const visited = new Set();

    while (cursor && mapping[cursor] && !visited.has(cursor)) {
      visited.add(cursor);
      chain.push(mapping[cursor]);
      cursor = mapping[cursor].parent;
    }

    if (chain.length === 0) {
      // Fallback sorted by create_time if current_node is absent
      for (const node of Object.values(mapping)) {
        if (node && node.message) chain.push(node);
      }
      chain.sort((a, b) => (a.message?.create_time || 0) - (b.message?.create_time || 0));
    } else {
      chain.reverse();
    }

    const turns = [];
    for (const node of chain) {
      const msg = node && node.message;
      if (!isVisibleDialogueMessage(msg)) continue;
      const role = msg.author && msg.author.role;
      const text = extractPartsText(msg);
      if (!text) continue;

      const ts =
        typeof msg.create_time === "number" && msg.create_time > 0
          ? new Date(msg.create_time * 1000).toISOString()
          : null;

      turns.push({
        role,
        content: text,
        external_message_id: String(msg.id || node.id || ""),
        source_timestamp: ts,
      });
    }
    return turns;
  }

  return { linearizeConversation, extractPartsText };
});
