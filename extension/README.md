# Tower of Babel — Chrome Extension (Chat Bridge v0.3.1)

No Chrome Web Store publishing or build step required. Load directly via Chrome Developer Mode:

1. Open `chrome://extensions/` in Google Chrome.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this `extension/` folder. (After updating files, click the **Reload `↻`** icon on the extension card and approve permissions if prompted.)
4. Click the extension icon in Chrome's toolbar to open the right-hand **Side Panel**.
5. Open the **工作台与设置** tab, enter your Hub URL (default `https://hub.atmosphericduct.xyz`) and your `Bridge Token` (stored only inside local `chrome.storage.local`).

## UX & Compatibility Highlights (v0.3.1)

- **Standard + ChatGPT Project Tabs & Pre-Opened Tabs**: Works immediately on both `/c/<uuid>` and ChatGPT Projects `/g/g-p-.../c/<uuid>` URLs, automatically injecting the content script into already-open tabs via `chrome.scripting` without requiring manual page refreshes.
- **3-Tab Light Porcelain Card UI (`当前窗口` · `决策与检索` · `工作台与设置`)**:
  - **One-Click Onboarding**: Automatically parses chat title, URL project hints, and deterministic `planner_thread_id` (collapsible under advanced options), with one-click bind + optional full history backfill.
  - **Bound Workspace Card**: Priority question alert when pending `ASK_PLANNER` items exist, one-click `复制开工口令` for Antigravity / Claude Code / Codex, one-click `填入最新上下文`, manual sync controls, and live rolling-compaction progress bar.
  - **Window Switcher**: Click any synced window card in `工作台与设置` to focus its open tab or jump directly to it.

## Safety Guarantees

- **Strict Whitelist**: Unbound ChatGPT windows are never scraped, synced, or modified.
- **Same-Origin History Backfill**: Triggered on user click or explicit opt-in during bind inside a bound tab; browser cookies never leave your machine.
- **4-Layer Auto-Send Guard**: Auto-send defaults to `OFF`, applies only to explicitly checked threads, enforces a 15s cooldown, breaks circuit after 5 consecutive sends without human interaction, and obeys the top global `急停` emergency toggle.
- **DOM Selector Isolation**: All ChatGPT DOM selectors live in `src/content/dom-adapter.js` with clipboard fallback if OpenAI updates page markup.
