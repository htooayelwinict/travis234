const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const packageRoot = path.resolve(__dirname, "..");
const packageJson = require("../package.json");
const {
  buildDockerCommand,
  buildPullCommand,
  parseArgs,
  prepareSandboxImports,
  recordPullSuccess,
  shouldUseIsolatedDockerConfig,
} = require("../bin/appv231.js");

test("package exposes appv231 binaries only", () => {
  assert.equal(packageJson.name, "@htooayelwinict/appv231");
  assert.equal(packageJson.bin.appv231, "bin/appv231.js");
  assert.equal(packageJson.bin["appv231-sandbox"], "bin/appv231.js");
  assert.equal(Object.hasOwn(packageJson.bin, "appv23"), false);
  assert.equal(Object.hasOwn(packageJson.bin, "appv23-sandbox"), false);
  assert.equal(fs.existsSync(path.join(packageRoot, packageJson.bin.appv231)), true);
});

test("package prompts prevent parent rereads after bounded subagent summaries", () => {
  const agentsPrompt = fs.readFileSync(path.join(packageRoot, "agents", "AGENTS.md"), "utf8");
  const subagentSkill = fs.readFileSync(path.join(packageRoot, "skills", "subagent-delegation", "SKILL.md"), "utf8");

  assert.match(agentsPrompt, /name is Travis/i);
  assert.match(agentsPrompt, /appv231|v231/i);
  assert.match(agentsPrompt, /latest Lewis request is the active contract/i);
  assert.match(agentsPrompt, /generated docs, reports, plans, summaries/i);
  assert.match(agentsPrompt, /tests pass but encode the opposite/i);
  assert.match(agentsPrompt, /subagents? (are|must remain) read-only/i);
  assert.match(agentsPrompt, /subagents? must not write files/i);
  assert.match(agentsPrompt, /child should inspect.*parent should write/is);
  assert.match(agentsPrompt, /truncated child result is not a failed child result/i);
  assert.match(agentsPrompt, /pre-read, find, list, grep, or resolve delegated target files/i);
  assert.match(agentsPrompt, /do not re-read child-scoped files/i);
  assert.match(agentsPrompt, /forbidden fallback/i);
  assert.match(agentsPrompt, /do not say.*read the key files directly/is);
  assert.match(agentsPrompt, /only allowed recovery paths/i);
  assert.match(agentsPrompt, /expand_subagent_result/i);
  assert.match(agentsPrompt, /Subagent system contract/i);
  assert.match(agentsPrompt, /Do not drop leading project directories/i);
  assert.match(agentsPrompt, /Allowed tools are its complete tool catalog/i);
  assert.match(agentsPrompt, /For child file discovery, tell it to use `find` or `ls`/i);
  assert.doesNotMatch(agentsPrompt, /glob is not available unless/i);
  assert.match(agentsPrompt, /After two failed attempts/i);
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

test("release image combines python 3.13 with official Node 20 without Debian npm", () => {
  const dockerfile = fs.readFileSync(path.resolve(packageRoot, "..", "..", "Dockerfile.appv231.release"), "utf8");

  assert.match(dockerfile, /^FROM python:3\.13-slim/m);
  assert.match(dockerfile, /^FROM node:20-bookworm-slim AS node-runtime$/m);
  assert.match(dockerfile, /COPY --from=node-runtime \/usr\/local\/bin\/node \/usr\/local\/bin\/node/);
  assert.match(dockerfile, /COPY --from=node-runtime \/usr\/local\/lib\/node_modules \/usr\/local\/lib\/node_modules/);
  const aptInstall = dockerfile.match(/apt-get install[\s\S]*?&& rm -rf/);
  assert.ok(aptInstall);
  assert.doesNotMatch(aptInstall[0], /\bnodejs\b|\bnpm\b/);
  assert.match(dockerfile, /COPY appV2\.3\.1 \/tmp\/allthebest\/appV2\.3\.1/);
  assert.doesNotMatch(dockerfile, /git clone/);
  assert.match(dockerfile, /pip install --no-cache-dir \/tmp\/allthebest\/appV2\.3\.1/);
  assert.match(dockerfile, /pip install --no-cache-dir pytest/);
  assert.match(dockerfile, /ENTRYPOINT \["appv231"\]/);
  assert.match(dockerfile, /\bsudo\b/);
  assert.match(dockerfile, /\bnpm\b/);
  assert.match(dockerfile, /useradd .*appv231/);
  assert.match(dockerfile, /env_keep \+= "DEBIAN_FRONTEND"/);
  assert.match(dockerfile, /appv231 ALL=.*NOPASSWD:.*apt-get/);
  assert.match(dockerfile, /USER appv231/);
});

test("local development image creates the appv231 user with apt sudo access", () => {
  const dockerfile = fs.readFileSync(path.resolve(packageRoot, "..", "..", "appV2.3.1", "Dockerfile.appv231"), "utf8");

  assert.match(dockerfile, /^FROM python:3\.13-slim/m);
  assert.match(dockerfile, /\bsudo\b/);
  assert.match(dockerfile, /\bnodejs\b/);
  assert.match(dockerfile, /\bnpm\b/);
  assert.match(dockerfile, /useradd .*appv231/);
  assert.match(dockerfile, /env_keep \+= "DEBIAN_FRONTEND"/);
  assert.match(dockerfile, /appv231 ALL=.*NOPASSWD:.*apt-get/);
  assert.match(dockerfile, /USER appv231/);
});

test("ghcr workflow targets appv231 production image", () => {
  const workflow = fs.readFileSync(path.resolve(packageRoot, "..", "..", ".github", "workflows", "appv231-release-image.yml"), "utf8");

  assert.match(workflow, /^name: appv231 release image/m);
  assert.match(workflow, /IMAGE_NAME: ghcr\.io\/\$\{\{ github\.repository_owner \}\}\/appv231/);
  assert.match(workflow, /file: Dockerfile\.appv231\.release/);
  assert.doesNotMatch(workflow, /appv23 release image/);
  assert.doesNotMatch(workflow, /Dockerfile\.appv23\.release/);
});

test("package defaults to appv231 production GHCR image and auto pull", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const config = parseArgs(["--agent-home", path.join(root, "agent-home")]);

  assert.equal(config.image, "ghcr.io/htooayelwinict/appv231:production");
  assert.equal(config.pull, "auto");
  assert.deepEqual(buildPullCommand(config), ["docker", "pull", "ghcr.io/htooayelwinict/appv231:production"]);
  assert.equal(shouldUseIsolatedDockerConfig(config, {}), true);
});

test("package auto pull skips when pull cache is fresh", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const config = parseArgs(["--agent-home", path.join(root, "agent-home")]);

  recordPullSuccess(config, { nowMs: 1000 });

  assert.deepEqual(buildPullCommand(config, { nowMs: 2000 }), []);
});

test("package auto pull runs when pull cache is stale", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const config = parseArgs(["--agent-home", path.join(root, "agent-home")]);

  recordPullSuccess(config, { nowMs: 1000 });

  assert.deepEqual(
    buildPullCommand(config, { nowMs: 1000 + 6 * 60 * 60 * 1000 + 1 }),
    ["docker", "pull", "ghcr.io/htooayelwinict/appv231:production"],
  );
});

test("package pull flags override auto pull cache", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const agentHome = path.join(root, "agent-home");
  const forceConfig = parseArgs(["--agent-home", agentHome, "--pull"]);
  const skipConfig = parseArgs(["--agent-home", agentHome, "--no-pull"]);

  recordPullSuccess(forceConfig, { nowMs: 1000 });

  assert.deepEqual(buildPullCommand(forceConfig, { nowMs: 2000 }), [
    "docker",
    "pull",
    "ghcr.io/htooayelwinict/appv231:production",
  ]);
  assert.deepEqual(buildPullCommand(skipConfig, { nowMs: 1000 + 6 * 60 * 60 * 1000 + 1 }), []);
});

test("package builds install-capable docker command for npx-style use", () => {
  const workspace = path.join(packageRoot, "fixtures", "workspace");
  const config = parseArgs(["--cwd", workspace, "--", "hello"]);
  const command = buildDockerCommand(config, { uid: 501, gid: 20, pid: 24680 });

  assert.deepEqual(command.slice(0, 5), ["docker", "run", "--rm", "-it", "--name"]);
  assert.equal(command.includes("--cap-drop"), false);
  assert.equal(command.includes("--security-opt"), false);
  assert.equal(command.includes("no-new-privileges"), false);
  assert.ok(command.includes("--pids-limit"));
  assert.ok(command.includes("512"));
  assert.equal(command[command.indexOf("--user") + 1], "appv231");
  assert.ok(command.includes("DEBIAN_FRONTEND=noninteractive"));
  assert.ok(command.includes("APPV231_SANDBOX=1"));
  assert.ok(command.includes("APPV231_WORKSPACE_ROOT=/workspace"));
  assert.ok(command.includes("APPV231_AGENT_HOME=/agent-home"));
  assert.ok(command.includes("APPV231_NO_VENV_REEXEC=1"));
  assert.ok(command.includes("APPV231_CODING_AGENT_DIR=/agent-home/agent"));
  assert.ok(command.includes(`${workspace}:/workspace:rw`));
  assert.ok(command.includes(`${config.agentHome}:/agent-home:rw`));
  assert.equal(command.some((value) => value === "/:/workspace:rw" || value.includes("docker.sock")), false);
  assert.ok(command.includes("ghcr.io/htooayelwinict/appv231:production"));
  assert.deepEqual(command.slice(-4), ["ghcr.io/htooayelwinict/appv231:production", "--cwd", "/workspace", "hello"]);
});

test("package copies bundled skills into the app-owned sandbox agent directory", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
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
  assert.equal(fs.existsSync(path.join(hostHome, ".agents")), false);
  assert.equal(fs.existsSync(path.join(agentHome, ".agents")), false);
});

test("package seeds bundled AGENTS.md into the host app-owned agent directory when missing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledAgentsDir = path.join(syntheticPackageRoot, "agents");
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledAgentsDir, { recursive: true });
  fs.mkdirSync(hostHome, { recursive: true });
  fs.writeFileSync(path.join(bundledAgentsDir, "AGENTS.md"), "Bundled appv231 kernel\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(hostHome, ".appv231", "agent", "AGENTS.md"), "utf8"),
    "Bundled appv231 kernel\n",
  );
  const imported = fs.readFileSync(path.join(agentHome, "agent", "AGENTS.md"), "utf8");
  assert.match(imported, /Bundled appv231 kernel/);
  assert.equal(fs.existsSync(path.join(hostHome, ".agents")), false);
});

test("package seeds bundled skills into the host app-owned agent directory without overwriting user skills", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledWebSearch = path.join(syntheticPackageRoot, "skills", "web-search");
  const hostHome = path.join(root, "host-home");
  const userWebSearch = path.join(hostHome, ".appv231", "agent", "skills", "web-search");
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
    fs.readFileSync(path.join(hostHome, ".appv231", "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nUser search\n",
  );
  assert.equal(
    fs.readFileSync(path.join(agentHome, "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nUser search\n",
  );
});

test("package seeds bundled skills into the host app-owned agent directory when missing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
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
    fs.readFileSync(path.join(hostHome, ".appv231", "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nBundled search\n",
  );
  assert.equal(
    fs.readFileSync(path.join(agentHome, "agent", "skills", "web-search", "SKILL.md"), "utf8"),
    "---\nname: web-search\n---\nBundled search\n",
  );
});

test("package app-owned user skills override bundled skills", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const syntheticPackageRoot = path.join(root, "package");
  const bundledSkill = path.join(syntheticPackageRoot, "skills", "subagent-delegation");
  const hostHome = path.join(root, "host-home");
  const userSkill = path.join(hostHome, ".appv231", "agent", "skills", "subagent-delegation");
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
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-cli-"));
  const hostHome = path.join(root, "host-home");
  const userAgentsDir = path.join(hostHome, ".appv231", "agent");
  const agentHome = path.join(root, "agent-home");
  const syntheticPackageRoot = path.join(root, "package");
  fs.mkdirSync(userAgentsDir, { recursive: true });
  fs.mkdirSync(syntheticPackageRoot, { recursive: true });
  fs.writeFileSync(path.join(userAgentsDir, "AGENTS.md"), "Global appv231 kernel\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot: syntheticPackageRoot },
  );

  const imported = fs.readFileSync(path.join(agentHome, "agent", "AGENTS.md"), "utf8");
  assert.match(imported, /appv231 sandbox instructions/);
  assert.match(imported, /Global appv231 kernel/);
});
