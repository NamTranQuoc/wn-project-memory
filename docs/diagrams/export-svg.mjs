#!/usr/bin/env node
// Export the canonical dual-theme SVG of archify HTML diagrams, the same file the
// viewer's "Export → SVG" menu downloads. Uses headless Chrome over the DevTools
// protocol; needs Node >= 22 (built-in WebSocket) and Chrome (set CHROME to override).
//
//   node docs/diagrams/export-svg.mjs docs/diagrams/architecture.html [...more.html]

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const CHROME =
  process.env.CHROME ||
  (process.platform === "darwin"
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : "google-chrome");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function devtoolsUrl(port) {
  for (let i = 0; i < 100; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (res.ok) return (await res.json()).webSocketDebuggerUrl;
    } catch {
      // Chrome is still starting.
    }
    await sleep(100);
  }
  throw new Error("Chrome did not open its DevTools port");
}

function client(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let next = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve: ok, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else ok(msg.result);
    }
  };
  const ready = new Promise((ok) => (ws.onopen = ok));
  const send = (method, params = {}, sessionId) =>
    new Promise((ok, reject) => {
      next += 1;
      pending.set(next, { resolve: ok, reject });
      ws.send(JSON.stringify({ id: next, method, params, sessionId }));
    });
  return { ready, send, close: () => ws.close() };
}

// Runs inside the page: capture the blob the viewer would download as SVG.
const CAPTURE = `new Promise((resolve, reject) => {
  const realCreate = URL.createObjectURL;
  URL.createObjectURL = (blob) => {
    if (blob && String(blob.type).startsWith("image/svg+xml")) blob.text().then(resolve, reject);
    return realCreate.call(URL, blob);
  };
  HTMLAnchorElement.prototype.click = function () {};
  const button = document.querySelector('button[data-format="svg"]');
  if (!button) return reject(new Error("no SVG export button"));
  button.click();
  setTimeout(() => reject(new Error("SVG export timed out")), 15000);
})`;

async function exportOne(cdp, htmlPath) {
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  await cdp.send("Page.enable", {}, sessionId);
  await cdp.send("Emulation.setDeviceMetricsOverride",
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  await cdp.send("Page.navigate", { url: pathToFileURL(resolve(htmlPath)).href }, sessionId);
  for (let i = 0; i < 100; i += 1) {
    const { result } = await cdp.send("Runtime.evaluate",
      { expression: "document.readyState === 'complete' && !!document.querySelector('.diagram-container svg')",
        returnByValue: true }, sessionId);
    if (result.value) break;
    await sleep(100);
  }
  await sleep(500);
  const { result, exceptionDetails } = await cdp.send("Runtime.evaluate",
    { expression: CAPTURE, awaitPromise: true, returnByValue: true }, sessionId);
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || "export failed");
  const out = htmlPath.replace(/\.html$/, ".svg");
  writeFileSync(out, result.value);
  await cdp.send("Target.closeTarget", { targetId });
  return out;
}

async function main(files) {
  if (!files.length) throw new Error("usage: export-svg.mjs <diagram.html> [...]");
  const profile = mkdtempSync(join(tmpdir(), "archify-export-"));
  const port = 9300 + Math.floor(Math.random() * 500);
  const chrome = spawn(CHROME, [
    "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--allow-file-access-from-files", "about:blank",
  ], { stdio: "ignore" });
  try {
    const cdp = client(await devtoolsUrl(port));
    await cdp.ready;
    for (const file of files) console.log(`✓ ${await exportOne(cdp, file)}`);
    cdp.close();
  } finally {
    chrome.kill();
    await sleep(300);
    rmSync(profile, { recursive: true, force: true });
  }
}

main(process.argv.slice(2)).catch((err) => {
  console.error(`✗ ${err.message}`);
  process.exit(1);
});
