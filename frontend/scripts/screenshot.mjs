/** Render each console page in a real browser and capture it, both themes. */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:3010";
const OUT = process.argv[2] ?? "./shots";
mkdirSync(OUT, { recursive: true });

const PAGES = [
  ["batches", "/"],
  ["queue", "/batches/1"],
  ["item", "/batches/1/items/500840315"],
  ["config", "/config"],
  ["chat", "/chat"],
];

const browser = await chromium.launch();
let problems = 0;

for (const theme of ["light", "dark"]) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    colorScheme: theme,
  });
  // seed the role-switcher so pages load as an engineer
  await ctx.addInitScript(() =>
    localStorage.setItem("bom-session", JSON.stringify({ user: "alice", role: "engineer" })));

  for (const [name, path] of PAGES) {
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });

    await page.goto(`${BASE}${path}`, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(800);

    // horizontal overflow check -- the page body must never scroll sideways
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);

    const text = await page.evaluate(() => document.body.innerText);
    await page.screenshot({ path: `${OUT}/${theme}-${name}.png`, fullPage: true });

    const bad = errors.filter((e) => !e.includes("favicon"));
    if (bad.length || overflow) problems++;
    console.log(`${theme.padEnd(5)} ${name.padEnd(8)} chars=${String(text.length).padStart(5)} ` +
                `h-overflow=${overflow} errors=${bad.length}${bad.length ? " :: " + bad[0].slice(0, 90) : ""}`);
    await page.close();
  }
  await ctx.close();
}

await browser.close();
console.log(problems ? `\n${problems} page(s) with problems` : "\nall pages clean");
process.exit(problems ? 1 : 0);
