import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Dom = require("../src/content/dom-adapter.js");
const Tree = require("../src/shared/history-tree.js");
const Guard = require("../src/shared/auto-send-guard.js");
const RetryQ = require("../src/shared/retry-queue.js");
const Api = require("../src/shared/api-client.js");

function makeNode({ nodeType = 1, tagName = "DIV", attrs = {}, textContent = "", childNodes = [] }) {
  return {
    nodeType,
    tagName,
    attributes: attrs,
    textContent,
    childNodes,
    getAttribute(k) {
      return this.attributes[k] ?? null;
    },
    querySelector(sel) {
      if (sel.includes('encoding="application/x-tex"') && this.tagName === "ANNOTATION") return this;
      if (sel === "code" && this.tagName === "CODE") return this;
      for (const c of this.childNodes) {
        if (c.nodeType === 1) {
          const hit = c.querySelector(sel);
          if (hit) return hit;
        }
      }
      return null;
    },
  };
}

test("DOM adapter parses standard & Projects ChatGPT conversation URLs", () => {
  assert.equal(
    Dom.parseConversationIdFromUrl("https://chatgpt.com/c/67b89c10-1234-4abc-9def-0123456789ab"),
    "67b89c10-1234-4abc-9def-0123456789ab"
  );
  assert.equal(
    Dom.parseConversationIdFromUrl("https://chatgpt.com/g/g-p-12345/c/67b89c10-1234-4abc-9def-0123456789ab"),
    "67b89c10-1234-4abc-9def-0123456789ab"
  );
  assert.equal(Dom.parseConversationIdFromUrl("https://chatgpt.com/"), null);
  assert.equal(Dom.parseConversationIdFromUrl("https://google.com/c/12345678"), null);
});

test("DOM adapter preserves code fences, KaTeX TeX source, and handles missing composer", () => {
  const texAnnotation = makeNode({
    tagName: "ANNOTATION",
    attrs: { encoding: "application/x-tex" },
    textContent: "\\nabla \\cdot \\mathbf{Q} = E - P",
  });
  const katexSpan = makeNode({
    tagName: "SPAN",
    attrs: { class: "katex katex-display" },
    childNodes: [texAnnotation],
  });
  const preNode = makeNode({
    tagName: "PRE",
    childNodes: [
      makeNode({
        tagName: "CODE",
        attrs: { class: "language-python" },
        textContent: "ivt = (qu**2 + qv**2)**0.5\n",
      }),
    ],
  });
  const msgEl = makeNode({
    tagName: "DIV",
    attrs: { "data-message-author-role": "assistant", "data-message-id": "msg-1" },
    childNodes: [
      makeNode({ nodeType: 1, tagName: "P", childNodes: [makeNode({ nodeType: 3, textContent: "公式如下：" })] }),
      katexSpan,
      preNode,
    ],
  });

  const mockDoc = {
    querySelectorAll(sel) {
      return sel === Dom.SELECTORS.messageRoleNode ? [msgEl] : [];
    },
    querySelector() {
      return null;
    },
  };

  const turns = Dom.extractVisibleMessages(mockDoc);
  assert.equal(turns.length, 1);
  assert.match(turns[0].content, /\$\$\n\\nabla \\cdot \\mathbf\{Q\} = E - P\n\$\$/);
  assert.match(turns[0].content, /```python\nivt = \(qu\*\*2 \+ qv\*\*2\)\*\*0\.5\n```/);

  // Graceful fallback when DOM changes
  const fallback = Dom.setComposerText(mockDoc, "hello");
  assert.deepEqual(fallback, { ok: false, reason: "SELECTOR_NOT_FOUND" });
});

test("History tree linearizes active branch chronologically without hidden nodes", () => {
  const conv = {
    current_node: "n4",
    mapping: {
      root: { id: "root", parent: null, message: null },
      n1: {
        id: "n1",
        parent: "root",
        message: { id: "m1", author: { role: "system" }, content: { parts: ["sys"] } },
      },
      n2: {
        id: "n2",
        parent: "n1",
        message: { id: "m2", author: { role: "user" }, create_time: 1700000001, content: { parts: ["你好"] } },
      },
      n_branch_abandoned: {
        id: "n_branch_abandoned",
        parent: "n2",
        message: { id: "m_old", author: { role: "assistant" }, content: { parts: ["abandoned"] } },
      },
      n3: {
        id: "n3",
        parent: "n2",
        message: { id: "m3", author: { role: "assistant" }, content: { content_type: "thoughts", parts: ["secret"] } },
      },
      n4: {
        id: "n4",
        parent: "n3",
        message: { id: "m4", author: { role: "assistant" }, create_time: 1700000010, content: { parts: ["已确认方案"] } },
      },
    },
  };
  const turns = Tree.linearizeConversation(conv);
  assert.equal(turns.length, 2);
  assert.equal(turns[0].content, "你好");
  assert.equal(turns[1].content, "已确认方案");
});

test("Auto-send enforces whitelist, cooldown, kill switch, anti-loop, and 5x circuit breaker", () => {
  const threadOn = { sync_enabled: true, auto_send_enabled: true };
  const threadOff = { sync_enabled: true, auto_send_enabled: false };
  const pageIdle = { isGenerating: false, composerHasText: false };
  const q1 = { question_id: "q_1", status: "PENDING", question_text: "Check?" };

  assert.equal(
    Guard.checkAutoSendAllowed({ thread: threadOff, pageState: pageIdle, question: q1 }).reason,
    "AUTO_SEND_DISABLED_FOR_THREAD"
  );
  assert.equal(
    Guard.checkAutoSendAllowed({ globalKillSwitch: true, thread: threadOn, pageState: pageIdle, question: q1 }).reason,
    "GLOBAL_KILL_SWITCH_ACTIVE"
  );
  assert.equal(
    Guard.checkAutoSendAllowed({ thread: threadOn, pageState: pageIdle, question: q1 }).allowed,
    true
  );

  // Cooldown check (15s)
  let gState = Guard.recordAutoSend({}, "q_1", 10000);
  const q2 = { question_id: "q_2", status: "PENDING", question_text: "Next?" };
  assert.equal(
    Guard.checkAutoSendAllowed({ thread: threadOn, pageState: pageIdle, question: q2, guardState: gState, nowMs: 15000 }).reason,
    "COOLDOWN_ACTIVE"
  );
  assert.equal(
    Guard.checkAutoSendAllowed({ thread: threadOn, pageState: pageIdle, question: q1, guardState: gState, nowMs: 40000 }).reason,
    "ANTI_LOOP_DUPLICATE_QUESTION"
  );

  // 5 consecutive sends trip circuit breaker until human interacts
  for (let i = 2; i <= 5; i += 1) {
    gState = Guard.recordAutoSend(gState, `q_${i}`, 40000 + i * 20000);
  }
  const q6 = { question_id: "q_6", status: "PENDING", question_text: "Sixth?" };
  assert.equal(
    Guard.checkAutoSendAllowed({ thread: threadOn, pageState: pageIdle, question: q6, guardState: gState, nowMs: 999999 }).reason,
    "CIRCUIT_BREAKER_TRIPPED"
  );
  gState = Guard.recordHumanInteraction(gState);
  assert.equal(
    Guard.checkAutoSendAllowed({ thread: threadOn, pageState: pageIdle, question: q6, guardState: gState, nowMs: 999999 }).allowed,
    true
  );
});

test("Retry queue deduplicates and flushes in chronological order", async () => {
  const b1 = { planner_thread_id: "T1", external_conversation_id: "c1", messages: [{ role: "user", content: "A" }] };
  const b2 = { planner_thread_id: "T1", external_conversation_id: "c1", messages: [{ role: "assistant", content: "B" }] };
  let q = RetryQ.enqueueBatch([], b1);
  q = RetryQ.enqueueBatch(q, b1); // duplicate skipped
  q = RetryQ.enqueueBatch(q, b2);
  assert.equal(q.length, 2);

  let failOnce = true;
  const delivered = [];
  const res1 = await RetryQ.flushQueue(q, async (payload) => {
    if (failOnce) {
      failOnce = false;
      throw new Error("Offline");
    }
    delivered.push(payload.messages[0].content);
  });
  assert.equal(res1.remaining.length, 2);

  const res2 = await RetryQ.flushQueue(res1.remaining, async (payload) => {
    delivered.push(payload.messages[0].content);
  });
  assert.equal(res2.sentCount, 2);
  assert.equal(res2.remaining.length, 0);
  assert.deepEqual(delivered, ["A", "B"]);
});

test("API client exposes compaction candidates and thread digest helpers", () => {
  assert.equal(typeof Api.getCompactionCandidates, "function");
  assert.equal(typeof Api.listDigests, "function");
});
