import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const Dom = require("../src/content/dom-adapter.js");
const Tree = require("../src/shared/history-tree.js");
const Guard = require("../src/shared/auto-send-guard.js");
const RetryQ = require("../src/shared/retry-queue.js");
const Api = require("../src/shared/api-client.js");
const Helpers = require("../src/shared/thread-helpers.js");

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

test("ThreadHelpers parses ChatGPT Project and standard conversation URLs", () => {
  const projUrl =
    "https://chatgpt.com/g/g-p-6ad802679d748191b0f1a532ffbd38f5-da-qi-he-yan-jiu/c/6af862ba-048c-83e8-b11f-b80d1e370d10";
  assert.equal(Helpers.parseConversationIdFromUrl(projUrl), "6af862ba-048c-83e8-b11f-b80d1e370d10");
  assert.deepEqual(Helpers.parseProjectSlugFromUrl(projUrl), {
    rawGroupKey: "g-p-6ad802679d748191b0f1a532ffbd38f5",
    slug: "da-qi-he-yan-jiu",
  });

  const stdUrl = "https://chatgpt.com/c/12345678-1234-1234-1234-1234567890ab";
  assert.equal(Helpers.parseConversationIdFromUrl(stdUrl), "12345678-1234-1234-1234-1234567890ab");
  assert.equal(Helpers.parseProjectSlugFromUrl(stdUrl), null);

  assert.equal(Helpers.parseConversationIdFromUrl("https://chatgpt.com/"), null);
  assert.equal(Helpers.parseConversationIdFromUrl("https://example.com/c/12345678-1234"), null);
});

test("ThreadHelpers cleans titles, builds deterministic IDs, and formats start prompts", () => {
  assert.equal(Helpers.cleanChatTitle("第十二次组会 - ChatGPT"), "第十二次组会");
  assert.equal(Helpers.cleanChatTitle("AR Research | ChatGPT"), "AR Research");

  const idCn = Helpers.buildAutoThreadId("第十二次组会", "6af862ba-048c-83e8-b11f-b80d1e370d10");
  assert.equal(idCn, "CHAT_6AF862BA");
  assert.match(idCn, /^[A-Za-z0-9_-]{2,64}$/);

  const idEn = Helpers.buildAutoThreadId("AR Main Line", "6af862ba-048c-83e8-b11f-b80d1e370d10");
  assert.equal(idEn, "CHAT_AR_MAIN_LINE_6AF862BA");
  assert.match(idEn, /^[A-Za-z0-9_-]{2,64}$/);

  const prompt = Helpers.buildStartPrompt({ title: "第十二次组会", planner_thread_id: idCn });
  assert.ok(prompt.includes("第十二次组会"));
  assert.ok(prompt.includes(idCn));
});

test("Sidepanel JS element IDs all exist in sidepanel.html", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const html = fs.readFileSync(path.join(here, "../src/sidepanel/sidepanel.html"), "utf8");
  const js = fs.readFileSync(path.join(here, "../src/sidepanel/sidepanel.js"), "utf8");

  const htmlIds = new Set();
  for (const m of html.matchAll(/\bid=["']([^"']+)["']/g)) {
    htmlIds.add(m[1]);
  }

  const missing = [];
  for (const m of js.matchAll(/\$\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g)) {
    if (!htmlIds.has(m[1])) {
      missing.push(m[1]);
    }
  }
  assert.deepEqual(missing, []);
});
