// Usage: node probe.cjs <allowedPageUrl> <blockedPageUrl> <apiBase>
const { chromium } = require("playwright");
const [allowedPage, blockedPage, apiBase] = process.argv.slice(2);
(async () => {
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const out = {};
for (const [label, url] of [["allowed_origin", allowedPage], ["disallowed_origin", blockedPage]]) {
  const page = await browser.newPage();
  await page.goto(url);
  out[label] = await page.evaluate((base) => window.probe(base), apiBase);
  await page.close();
}
await browser.close();
console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
