import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const packageRoot = path.resolve(__dirname, "..");
const packageJson = JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"));
import {
  buildDockerCommand,
  buildPullCommand,
  parseArgs,
  prepareSandboxImports,
  recordPullSuccess,
  shouldUseIsolatedDockerConfig,
} from "../bin/travis234.js";

test("package exposes travis234 binaries only", () => {
  assert.equal(packageJson.name, "@htooayelwinict/travis234");
  assert.deepEqual(packageJson.bin, { travis234: "bin/travis234.js" });
  assert.equal(fs.existsSync(path.join(packageRoot, packageJson.bin.travis234)), true);
});

test("npm bin symlink invokes the launcher entrypoint", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-bin-"));
  const launcher = path.join(root, "travis234");
  fs.symlinkSync(path.join(packageRoot, packageJson.bin.travis234), launcher);

  const result = spawnSync(launcher, ["--help"], { encoding: "utf8" });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /^Travis234$/m);
  assert.match(result.stdout, /^Usage:$/m);
});

test("package does not bundle a mandatory global agent prompt", () => {
  const subagentSkill = fs.readFileSync(path.join(packageRoot, "skills", "subagent-delegation", "SKILL.md"), "utf8");

  assert.equal(fs.existsSync(path.join(packageRoot, "agents", "AGENTS.md")), false);
  assert.doesNotMatch(subagentSkill, /\bLewis\b/i);
  assert.match(subagentSkill, /truncated child result is not a failed child result/i);
  assert.match(subagentSkill, /subagents? (are|must remain) read-only/i);
  assert.match(subagentSkill, /must not write files/i);
  assert.match(subagentSkill, /child should inspect.*parent should write/is);
  assert.match(subagentSkill, /pre-read, find, list, grep, or resolve delegated target files/i);
  assert.match(subagentSkill, /do not re-read files in the parent/i);
  assert.match(subagentSkill, /forbidden fallback/i);
  assert.match(subagentSkill, /do not say.*read the key files directly/is);
  assert.match(subagentSkill, /only allowed recovery paths/i);
  assert.match(subagentSkill, /expand_subagent_result/i);
  assert.match(subagentSkill, /spawn a narrower follow-up child task/i);
  assert.match(subagentSkill, /Subagent system contract/i);
  assert.match(subagentSkill, /Current working directory/i);
  assert.match(subagentSkill, /Do not drop leading project directories/i);
  assert.match(subagentSkill, /Allowed tools are the child's complete tool catalog/i);
  assert.match(subagentSkill, /For file discovery, use `find` or `ls`/i);
  assert.doesNotMatch(subagentSkill, /glob is not available unless/i);
  assert.match(subagentSkill, /After two failed attempts/i);
});

test("package web-search skill uses curl-only network retrieval", () => {
  const webSearchSkill = fs.readFileSync(path.join(packageRoot, "skills", "web-search", "SKILL.md"), "utf8");

  assert.match(webSearchSkill, /curl --fail --location --silent --show-error/i);
  assert.match(webSearchSkill, /sed|awk|xmllint|perl/i);
  assert.doesNotMatch(webSearchSkill, /python3?\b/i);
  assert.doesNotMatch(webSearchSkill, /urllib/i);
  assert.doesNotMatch(webSearchSkill, /xml\.etree/i);
});

test("release image combines Python 3.13 and Node 20 without passwordless sudo", () => {
  const dockerfile = fs.readFileSync(path.resolve(packageRoot, "..", "..", "Dockerfile.release"), "utf8");

  assert.match(dockerfile, /^FROM node:20-bookworm-slim AS node-runtime$/m);
  assert.match(dockerfile, /^FROM python:3\.13-slim$/m);
  assert.match(dockerfile, /COPY --from=node-runtime \/usr\/local\/bin\/node \/usr\/local\/bin\/node/);
  assert.match(dockerfile, /COPY --from=node-runtime \/usr\/local\/lib\/node_modules \/usr\/local\/lib\/node_modules/);
  assert.match(dockerfile, /ENTRYPOINT \["travis234"\]/);
  assert.match(dockerfile, /useradd --create-home --home-dir \/travis-home .* travis/);
  assert.match(dockerfile, /USER travis/);
  assert.doesNotMatch(dockerfile, /NOPASSWD|\bsudo\b/);
});

test("local development image creates the travis user with limited package sudo", () => {
  const dockerfile = fs.readFileSync(path.resolve(packageRoot, "..", "..", "Dockerfile"), "utf8");

  assert.match(dockerfile, /^FROM python:3\.13-slim/m);
  assert.match(dockerfile, /\bsudo\b/);
  assert.match(dockerfile, /\bnodejs\b/);
  assert.match(dockerfile, /\bnpm\b/);
  assert.match(dockerfile, /useradd .* travis/);
  assert.match(dockerfile, /env_keep \+= "DEBIAN_FRONTEND"/);
  assert.match(dockerfile, /travis ALL=.*NOPASSWD:.*apt-get/);
  assert.doesNotMatch(dockerfile, /NOPASSWD: ALL/);
  assert.match(dockerfile, /USER travis/);
});

test("ghcr workflow targets travis234 production image", () => {
  const workflow = fs.readFileSync(path.resolve(packageRoot, "..", "..", ".github", "workflows", "travis234-release-image.yml"), "utf8");

  assert.match(workflow, /^name: travis234 release image/m);
  assert.match(workflow, /IMAGE_NAME: ghcr\.io\/\$\{\{ github\.repository_owner \}\}\/travis234/);
  assert.match(workflow, /file: Dockerfile\.release/);
});

test("package defaults to travis234 production GHCR image and auto pull", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const config = parseArgs(["--agent-home", path.join(root, "agent-home")]);

  assert.equal(config.image, "ghcr.io/htooayelwinict/travis234:production");
  assert.equal(config.pull, "auto");
  assert.deepEqual(buildPullCommand(config), ["docker", "pull", "ghcr.io/htooayelwinict/travis234:production"]);
  assert.equal(shouldUseIsolatedDockerConfig(config, {}), true);
});

test("package auto pull skips when pull cache is fresh", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const config = parseArgs(["--agent-home", path.join(root, "agent-home")]);

  recordPullSuccess(config, { nowMs: 1000 });

  assert.deepEqual(buildPullCommand(config, { nowMs: 2000 }), []);
});

test("package auto pull runs when pull cache is stale", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const config = parseArgs(["--agent-home", path.join(root, "agent-home")]);

  recordPullSuccess(config, { nowMs: 1000 });

  assert.deepEqual(
    buildPullCommand(config, { nowMs: 1000 + 6 * 60 * 60 * 1000 + 1 }),
    ["docker", "pull", "ghcr.io/htooayelwinict/travis234:production"],
  );
});

test("package pull flags override auto pull cache", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const agentHome = path.join(root, "agent-home");
  const forceConfig = parseArgs(["--agent-home", agentHome, "--pull"]);
  const skipConfig = parseArgs(["--agent-home", agentHome, "--no-pull"]);

  recordPullSuccess(forceConfig, { nowMs: 1000 });

  assert.deepEqual(buildPullCommand(forceConfig, { nowMs: 2000 }), [
    "docker",
    "pull",
    "ghcr.io/htooayelwinict/travis234:production",
  ]);
  assert.deepEqual(buildPullCommand(skipConfig, { nowMs: 1000 + 6 * 60 * 60 * 1000 + 1 }), []);
});

test("package builds install-capable docker command for npx-style use", () => {
  const workspace = path.join(packageRoot, "fixtures", "workspace");
  const config = parseArgs(["--cwd", workspace, "--", "hello"]);
  const command = buildDockerCommand(config, { uid: 501, gid: 20, pid: 24680 });

  assert.deepEqual(command.slice(0, 5), ["docker", "run", "--rm", "-it", "--name"]);
  assert.ok(command.includes("--cap-drop"));
  assert.ok(command.includes("ALL"));
  assert.ok(command.includes("--security-opt"));
  assert.ok(command.includes("no-new-privileges"));
  assert.ok(command.includes("--pids-limit"));
  assert.ok(command.includes("512"));
  assert.equal(command[command.indexOf("--user") + 1], "501:20");
  assert.ok(command.includes("DEBIAN_FRONTEND=noninteractive"));
  assert.ok(command.includes("TRAVIS234_SANDBOX=1"));
  assert.ok(command.includes("TRAVIS234_WORKSPACE_ROOT=/workspace"));
  assert.ok(command.includes("TRAVIS234_AGENT_HOME=/travis-home"));
  assert.ok(command.includes("TRAVIS234_NO_VENV_REEXEC=1"));
  assert.ok(command.includes("TRAVIS234_CODING_AGENT_DIR=/travis-home/agent"));
  assert.ok(command.includes(`${workspace}:/workspace:rw`));
  assert.ok(command.includes(`${config.agentHome}:/travis-home:rw`));
  assert.equal(command.some((value) => value === "/:/workspace:rw" || value.includes("docker.sock")), false);
  assert.ok(command.includes("ghcr.io/htooayelwinict/travis234:production"));
  assert.deepEqual(command.slice(-4), ["ghcr.io/htooayelwinict/travis234:production", "--cwd", "/workspace", "hello"]);
});

test("package does not forward host provider credentials into the sandbox", () => {
  const workspace = path.join(packageRoot, "fixtures", "workspace");
  const config = parseArgs(["--cwd", workspace], {
    env: {
      OPENROUTER_API_KEY: "host-secret",
      TRAVIS234_IMAGE: "ghcr.io/htooayelwinict/travis234:production",
    },
  });
  const command = buildDockerCommand(config, { uid: 501, gid: 20, pid: 24680 });
  const rendered = command.join(" ");

  assert.doesNotMatch(rendered, /host-secret|OPENROUTER_API_KEY|--env-file/);
  assert.doesNotMatch(rendered, /\.travis234\/agent(?:\/|\b)/);
});

test("package forwards session modes while mounting persistent app-owned state", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-session-cli-"));
  const workspace = path.join(root, "workspace");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(workspace, { recursive: true });

  for (const appArgs of [
    ["--continue"],
    ["--resume"],
    ["--session", "saved-session-id"],
    ["--no-session"],
  ]) {
    const config = parseArgs(["--cwd", workspace, "--agent-home", agentHome, "--", ...appArgs]);
    const command = buildDockerCommand(config, { pid: 24680 });

    assert.ok(command.includes(`${agentHome}:/travis-home:rw`));
    assert.ok(command.includes("TRAVIS234_CODING_AGENT_DIR=/travis-home/agent"));
    assert.deepEqual(
      command.slice(-(appArgs.length + 3)),
      ["ghcr.io/htooayelwinict/travis234:production", "--cwd", "/workspace", ...appArgs],
    );
  }
});

test("package copies bundled skills into the app-owned sandbox agent directory", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledSkill = path.join(syntheticPackageRoot, "skills", "subagent-delegation");
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledSkill, { recursive: true });
  fs.mkdirSync(hostHome, { recursive: true });
  fs.writeFileSync(path.join(bundledSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nBundled policy\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(agentHome, "agent", "skills", "subagent-delegation", "SKILL.md"), "utf8"),
    "---\nname: subagent-delegation\n---\nBundled policy\n",
  );
});

test("package skill imports exclude dotenv and auth credential files", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledSkill = path.join(syntheticPackageRoot, "skills", "credential-audit");
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledSkill, { recursive: true });
  fs.mkdirSync(hostHome, { recursive: true });
  fs.writeFileSync(path.join(bundledSkill, "SKILL.md"), "---\nname: credential-audit\n---\nAudit\n");
  fs.writeFileSync(path.join(bundledSkill, ".env"), "OPENROUTER_API_KEY=secret\n");
  fs.writeFileSync(path.join(bundledSkill, "auth.json"), '{"openrouter":"secret"}\n');

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  const imported = path.join(agentHome, "agent", "skills", "credential-audit");
  assert.equal(fs.existsSync(path.join(imported, "SKILL.md")), true);
  assert.equal(fs.existsSync(path.join(imported, ".env")), false);
  assert.equal(fs.existsSync(path.join(imported, "auth.json")), false);
});

test("package does not seed AGENTS.md into the host agent directory", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledAgentsDir = path.join(syntheticPackageRoot, "agents");
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledAgentsDir, { recursive: true });
  fs.mkdirSync(hostHome, { recursive: true });
  fs.writeFileSync(path.join(bundledAgentsDir, "AGENTS.md"), "Bundled travis234 kernel\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(fs.existsSync(path.join(hostHome, ".travis234", "agent", "AGENTS.md")), false);
  assert.equal(fs.existsSync(path.join(agentHome, "agent", "AGENTS.md")), false);
});

test("package seeds bundled skills into the host app-owned agent directory without overwriting user skills", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledWebSearch = path.join(syntheticPackageRoot, "skills", "web-search");
  const hostHome = path.join(root, "host-home");
  const userWebSearch = path.join(hostHome, ".travis234", "agent", "skills", "web-search");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledWebSearch, { recursive: true });
  fs.mkdirSync(userWebSearch, { recursive: true });
  fs.writeFileSync(path.join(bundledWebSearch, "SKILL.md"), "---\nname: web-search\n---\nBundled search\n");
  fs.writeFileSync(path.join(userWebSearch, "SKILL.md"), "---\nname: web-search\n---\nUser search\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(hostHome, ".travis234", "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nUser search\n",
  );
  assert.equal(
    fs.readFileSync(path.join(agentHome, "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nUser search\n",
  );
});

test("package seeds bundled skills into the host app-owned agent directory when missing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledWebSearch = path.join(syntheticPackageRoot, "skills", "web-search");
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledWebSearch, { recursive: true });
  fs.mkdirSync(hostHome, { recursive: true });
  fs.writeFileSync(path.join(bundledWebSearch, "SKILL.md"), "---\nname: web-search\n---\nBundled search\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(hostHome, ".travis234", "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nBundled search\n",
  );
  assert.equal(
    fs.readFileSync(path.join(agentHome, "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nBundled search\n",
  );
});

test("package app-owned user skills override bundled skills", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledSkill = path.join(syntheticPackageRoot, "skills", "subagent-delegation");
  const hostHome = path.join(root, "host-home");
  const userSkill = path.join(hostHome, ".travis234", "agent", "skills", "subagent-delegation");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledSkill, { recursive: true });
  fs.mkdirSync(userSkill, { recursive: true });
  fs.writeFileSync(path.join(bundledSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nBundled policy\n");
  fs.writeFileSync(path.join(userSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nUser policy\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(agentHome, "agent", "skills", "subagent-delegation", "SKILL.md"), "utf8"),
    "---\nname: subagent-delegation\n---\nUser policy\n",
  );
});

test("package copies app-owned user AGENTS.md into sandbox agent context by default", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-cli-"));
  const hostHome = path.join(root, "host-home");
  const userAgentsDir = path.join(hostHome, ".travis234", "agent");
  const agentHome = path.join(root, "agent-home");
  const syntheticPackageRoot = path.join(root, "package");
  fs.mkdirSync(userAgentsDir, { recursive: true });
  fs.mkdirSync(syntheticPackageRoot, { recursive: true });
  fs.writeFileSync(path.join(userAgentsDir, "AGENTS.md"), "Global travis234 kernel\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  const imported = fs.readFileSync(path.join(agentHome, "agent", "AGENTS.md"), "utf8");
  assert.match(imported, /travis234 sandbox instructions/);
  assert.match(imported, /Global travis234 kernel/);
});
