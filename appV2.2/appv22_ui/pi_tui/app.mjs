#!/usr/bin/env node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  Container,
  Input,
  Markdown,
  ProcessTerminal,
  Spacer,
  Text,
  TUI,
  matchesKey,
} from "../../../pi/packages/tui/dist/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "../..");

const args = parseArgs(process.argv.slice(2));
const bridge = spawn(args.python, [
  resolve(appRoot, "scripts/appv22_tui_bridge.py"),
  "--workspace",
  args.workspace,
  "--dotenv",
  args.dotenv,
  "--max-turns",
  String(args.maxTurns),
], {
  cwd: resolve(appRoot, ".."),
  stdio: ["pipe", "pipe", "pipe"],
});

const tui = new TUI(new ProcessTerminal());
const root = new Container();
const chat = new Container();
const status = new Container();
const input = new Input();
let buffered = "";
let currentSession = null;

root.addChild(chat);
root.addChild(status);
root.addChild(new Spacer(1));
root.addChild(input);
tui.addChild(root);
tui.setFocus(input);

input.onSubmit = (value) => {
  const text = value.trim();
  input.setValue("");
  if (!text) {
    tui.requestRender();
    return;
  }
  if (text === "/exit" || text === "/quit") {
    send({ type: "exit" });
    bridge.stdin.end();
    tui.stop();
    process.exit(0);
  }
  if (text === "/status") {
    send({ type: "status" });
    return;
  }
  addUserMessage(text);
  setStatus("working");
  send({ type: "prompt", text });
};

tui.addInputListener((data) => {
  if (matchesKey(data, "ctrl+c")) {
    send({ type: "exit" });
    bridge.stdin.end();
    tui.stop();
    process.exit(0);
  }
  return undefined;
});

bridge.stdout.on("data", (chunk) => {
  buffered += chunk.toString("utf8");
  let newline = buffered.indexOf("\n");
  while (newline !== -1) {
    const line = buffered.slice(0, newline).trim();
    buffered = buffered.slice(newline + 1);
    if (line) handleBridgeLine(line);
    newline = buffered.indexOf("\n");
  }
});

bridge.stderr.on("data", (chunk) => {
  addSystemMessage(chunk.toString("utf8").trim());
});

bridge.on("exit", (code) => {
  if (code && code !== 0) {
    addSystemMessage(`bridge exited with ${code}`);
  }
});

addSystemMessage("AppV2.2");
setStatus("idle");
send({ type: "status" });
tui.start();

function handleBridgeLine(line) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    addSystemMessage(line);
    return;
  }
  if (message.session) {
    currentSession = message.session;
  }
  if (message.type === "status") {
    renderStatus(message.session);
  } else if (message.type === "event") {
    renderEvent(message.event);
  } else if (message.type === "result") {
    renderResult(message);
  } else if (message.type === "error") {
    addSystemMessage(message.message || "unknown bridge error");
    renderStatus(message.session);
  } else if (message.type === "exit") {
    tui.stop();
    process.exit(0);
  }
  tui.requestRender();
}

function renderResult(message) {
  const result = message.result || {};
  if (result.assistant_message) {
    chat.addChild(new Markdown(String(result.assistant_message).trim(), 1, 0, markdownTheme()));
  }
  renderStatus(message.session);
}

function renderEvent(event) {
  const type = String(event?.event_type || event?.type || "");
  if (type === "ToolCallCompleted") {
    const payload = event.payload || {};
    setStatus(`tool ${payload.tool_id || "completed"}`);
  } else if (type === "ModeChanged") {
    setStatus(`mode ${event.payload?.mode || ""}`.trim());
  }
}

function renderStatus(session) {
  status.clear();
  const source = session || currentSession || {};
  const metrics = source.ui_context_metrics || {};
  const compact = metrics.compaction_count || 0;
  status.addChild(
    new Text(
      `status ${source.status || "empty"}  mode ${source.mode || "IDLE"}  refs ${source.world_ref_count || 0}  compact ${compact}`,
      1,
      0,
    ),
  );
}

function setStatus(text) {
  status.clear();
  status.addChild(new Text(text, 1, 0));
  tui.requestRender();
}

function addUserMessage(text) {
  chat.addChild(new Spacer(1));
  chat.addChild(new Text(`> ${text}`, 1, 0));
}

function addSystemMessage(text) {
  if (!text) return;
  chat.addChild(new Text(text, 1, 0));
  tui.requestRender();
}

function send(payload) {
  bridge.stdin.write(`${JSON.stringify(payload)}\n`);
}

function markdownTheme() {
  const passthrough = (text) => text;
  return {
    heading: passthrough,
    link: passthrough,
    linkUrl: passthrough,
    code: passthrough,
    codeBlock: passthrough,
    codeBlockBorder: passthrough,
    quote: passthrough,
    quoteBorder: passthrough,
    hr: passthrough,
    listBullet: passthrough,
    bold: passthrough,
    italic: passthrough,
    strikethrough: passthrough,
    underline: passthrough,
  };
}

function parseArgs(argv) {
  const parsed = {
    workspace: ".",
    dotenv: ".env",
    maxTurns: 12,
    python: process.env.APPV22_PYTHON || "python3",
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--workspace") parsed.workspace = argv[++i] || parsed.workspace;
    else if (arg === "--dotenv") parsed.dotenv = argv[++i] || parsed.dotenv;
    else if (arg === "--max-turns") parsed.maxTurns = Number(argv[++i] || parsed.maxTurns);
    else if (arg === "--python") parsed.python = argv[++i] || parsed.python;
  }
  return parsed;
}
