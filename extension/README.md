# Tower of Babel — Chrome Extension (Chat Bridge)

No Chrome Web Store publishing or build step required. Load directly via Chrome Developer Mode:

1. Open `chrome://extensions/` in Google Chrome.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this `extension/` folder.
4. Click the extension icon in Chrome's toolbar to open the right-hand **Side Panel**.
5. In the **Settings** tab, enter your Hub URL (default `https://hub.atmosphericduct.xyz`) and your `Bridge Token` (stored only inside local `chrome.storage.local`).

## Safety Guarantees

- **Strict Whitelist**: Unbound ChatGPT windows are never scraped, synced, or modified.
- **Same-Origin History Backfill**: Triggered only on manual button click inside a bound tab; browser cookies never leave your machine.
- **4-Layer Auto-Send Guard**: Auto-send defaults to `OFF`, applies only to explicitly checked threads, enforces a 15s cooldown, breaks circuit after 5 consecutive sends without human interaction, and obeys the top global emergency stop toggle.
- **DOM Selector Isolation**: All ChatGPT DOM selectors live in `src/content/dom-adapter.js` with clipboard fallback if OpenAI updates page markup.
