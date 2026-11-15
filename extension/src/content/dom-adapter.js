/**
 * Isolated ChatGPT DOM & Markdown/KaTeX Adapter.
 * Uses only semantic attributes (never brittle obfuscated class hashes).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TowerDomAdapter = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const SELECTORS = {
    messageRoleNode: "[data-message-author-role]",
    turnRoleFallback: '[data-turn="user"], [data-turn="assistant"]',
    turnArticleFallback: 'article[data-testid^="conversation-turn-"]',
    promptInputs: [
      "#prompt-textarea",
      'div[contenteditable="true"][id="prompt-textarea"]',
      'div.ProseMirror[contenteditable="true"]',
      'textarea[data-id="root"]',
      'div[contenteditable="true"][data-placeholder]',
    ],
    sendButtons: [
      'button[data-testid="send-button"]',
      'button[aria-label*="Send"]',
      'button[aria-label*="发送"]',
    ],
    stopButtons: [
      'button[data-testid="stop-button"]',
      'button[aria-label*="Stop generating"]',
      'button[aria-label*="停止"]',
    ],
  };

  function parseConversationIdFromUrl(urlStr) {
    if (!urlStr) return null;
    try {
      const u = new URL(urlStr);
      if (!u.hostname.endsWith("chatgpt.com") && !u.hostname.endsWith("chat.openai.com")) {
        return null;
      }
      // Matches both /c/<uuid> and ChatGPT Projects /g/<project_id>/c/<uuid>
      const m = u.pathname.match(/\/c\/([0-9a-fA-F-]{8,})/);
      return m ? m[1] : null;
    } catch {
      return null;
    }
  }

  function findFirst(doc, selectorList) {
    if (!doc || typeof doc.querySelector !== "function") return null;
    for (const sel of selectorList) {
      const el = doc.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function getAttr(node, name) {
    if (!node) return null;
    if (typeof node.getAttribute === "function") return node.getAttribute(name);
    return node.attributes && node.attributes[name] ? node.attributes[name] : null;
  }

  function serializeNodeToMarkdown(node) {
    if (!node) return "";
    // Text node (nodeType === 3)
    if (node.nodeType === 3) {
      return node.textContent || "";
    }
    if (node.nodeType !== 1 && node.nodeType !== undefined) {
      return "";
    }

    const tag = String(node.tagName || "").toLowerCase();

    // KaTeX math recovery via <annotation encoding="application/x-tex">
    if (tag === "annotation" && getAttr(node, "encoding") === "application/x-tex") {
      return node.textContent || "";
    }
    if (
      typeof node.querySelector === "function" &&
      ((getAttr(node, "class") || "").includes("katex") || tag === "math")
    ) {
      const texNode = node.querySelector('annotation[encoding="application/x-tex"]');
      if (texNode && texNode.textContent) {
        const tex = texNode.textContent.trim();
        const isDisplay = (getAttr(node, "class") || "").includes("katex-display");
        return isDisplay ? `\n$$\n${tex}\n$$\n` : `$${tex}$`;
      }
    }

    // Code block <pre><code>
    if (tag === "pre") {
      const codeEl = typeof node.querySelector === "function" ? node.querySelector("code") : null;
      const target = codeEl || node;
      const cls = getAttr(target, "class") || "";
      const langMatch = cls.match(/language-([A-Za-z0-9_+-]+)/);
      const lang = langMatch ? langMatch[1] : "";
      const rawCode = target.textContent || "";
      return `\n\`\`\`${lang}\n${rawCode.replace(/\n+$/, "")}\n\`\`\`\n`;
    }

    if (tag === "code") {
      return `\`${node.textContent || ""}\``;
    }

    const children = Array.from(node.childNodes || []);
    const inner = children.map((c) => serializeNodeToMarkdown(c)).join("");

    if (tag === "br") return "\n";
    if (tag === "p" || tag === "div" || tag === "blockquote") {
      return inner.trim() ? `${inner.trim()}\n\n` : "";
    }
    if (tag === "li") {
      return inner.trim() ? `- ${inner.trim()}\n` : "";
    }
    if (tag === "ul" || tag === "ol") {
      return `${inner}\n`;
    }
    if (tag === "tr") {
      const cells = children
        .filter((c) => {
          const ct = String(c.tagName || "").toLowerCase();
          return ct === "td" || ct === "th";
        })
        .map((c) => serializeNodeToMarkdown(c).replace(/\s+/g, " ").trim());
      if (cells.length > 0) {
        return `| ${cells.join(" | ")} |\n`;
      }
      return inner.trim() ? `${inner.trim()}\n` : "";
    }
    if (tag === "table") {
      return inner.trim() ? `\n${inner.trim()}\n\n` : "";
    }
    if (/^h[1-6]$/.test(tag)) {
      const lvl = "#".repeat(Number(tag[1]));
      return inner.trim() ? `\n${lvl} ${inner.trim()}\n\n` : "";
    }
    if (tag === "a") {
      const href = getAttr(node, "href") || "";
      return href ? `[${inner.trim()}](${href})` : inner;
    }
    return inner;
  }

  function resolveMessageRole(el) {
    const direct = getAttr(el, "data-message-author-role") || getAttr(el, "data-turn");
    if (direct === "user" || direct === "assistant") return direct;
    if (typeof el.querySelector === "function") {
      const nestedRole = el.querySelector("[data-message-author-role], [data-turn]");
      if (nestedRole) {
        const r =
          getAttr(nestedRole, "data-message-author-role") || getAttr(nestedRole, "data-turn");
        if (r === "user" || r === "assistant") return r;
      }
      const srHeading = el.querySelector("h5, h6");
      const headingTxt = (srHeading && srHeading.textContent) || "";
      if (/you said|你说/i.test(headingTxt)) return "user";
      if (/chatgpt said|chatgpt/i.test(headingTxt)) return "assistant";
    }
    return null;
  }

  function extractVisibleMessages(doc) {
    if (!doc || typeof doc.querySelectorAll !== "function") return [];
    let nodes = Array.from(doc.querySelectorAll(SELECTORS.messageRoleNode));
    if (nodes.length === 0) {
      nodes = Array.from(doc.querySelectorAll(SELECTORS.turnRoleFallback));
    }
    if (nodes.length === 0) {
      nodes = Array.from(doc.querySelectorAll(SELECTORS.turnArticleFallback));
    }
    const out = [];
    for (let i = 0; i < nodes.length; i += 1) {
      const el = nodes[i];
      const role = resolveMessageRole(el);
      if (role !== "user" && role !== "assistant") continue;
      const content = serializeNodeToMarkdown(el).replace(/\n{3,}/g, "\n\n").trim();
      if (!content) continue;
      const extId =
        getAttr(el, "data-message-id") || getAttr(el, "data-testid") || `dom_${i}_${role}`;
      out.push({
        role,
        content,
        external_message_id: extId,
      });
    }
    return out;
  }

  function getComposerElement(doc) {
    return findFirst(doc, SELECTORS.promptInputs);
  }

  function getSendButton(doc) {
    return findFirst(doc, SELECTORS.sendButtons);
  }

  function isGenerating(doc) {
    return Boolean(findFirst(doc, SELECTORS.stopButtons));
  }

  function readComposerText(doc) {
    const el = getComposerElement(doc);
    if (!el) return "";
    if (typeof el.value === "string") return el.value.trim();
    return String(el.textContent || "").trim();
  }

  function setComposerText(doc, text) {
    const el = getComposerElement(doc);
    if (!el) {
      return { ok: false, reason: "SELECTOR_NOT_FOUND" };
    }
    if (typeof el.focus === "function") el.focus();
    const tag = String(el.tagName || "").toLowerCase();
    if (tag === "textarea" || tag === "input") {
      el.value = text;
    } else {
      el.innerText = text;
      if (typeof el.textContent === "string" && el.innerText !== text) {
        el.textContent = text;
      }
    }
    if (typeof Event === "function" && typeof el.dispatchEvent === "function") {
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return { ok: true, element: el };
  }

  return {
    SELECTORS,
    parseConversationIdFromUrl,
    serializeNodeToMarkdown,
    extractVisibleMessages,
    getComposerElement,
    getSendButton,
    isGenerating,
    readComposerText,
    setComposerText,
  };
});
