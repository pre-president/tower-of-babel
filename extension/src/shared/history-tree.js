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
  function extractPartsText(message) {
    if (!message || !message.content) return "";
    const content = message.content;

    if (content.content_type === "thoughts") {
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
      if (!msg) continue;
      if (msg.metadata && msg.metadata.is_visually_hidden_from_conversation) continue;
      const role = msg.author && msg.author.role;
      if (role !== "user" && role !== "assistant") continue;
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
