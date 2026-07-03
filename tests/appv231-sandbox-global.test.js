const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  buildDockerCommand,
  buildPullEnv,
  buildPullCommand,
  parseArgs,
  prepareSandboxImports,
  shouldUseIsolatedDockerConfig,
} = require("../bin/appv231-sandbox.js");

test("global wrapper defaults to appv231 production GHCR image and pull", () => {
  const config = parseArgs([]);

  assert.equal(config.image, "ghcr.io/htooayelwinict/appv231:production");
  assert.equal(config.pull, true);
  assert.deepEqual(buildPullCommand(config), ["docker", "pull", "ghcr.io/htooayelwinict/appv231:production"]);
});

test("global wrapper builds hardened docker command without host-home mounts", () => {
  const workspace = path.join(os.tmpdir(), "appv231-workspace");
  const agentHome = path.join(os.tmpdir(), "appv231-agent-home");
  const config = parseArgs([
    "--cwd",
    workspace,
    "--agent-home",
    agentHome,
    "--image",
    "appv231:test",
    "--",
    "--model",
    "openrouter/qwen/qwen3.6-flash",
    "--dotenv",
    ".env",
  ]);

  const command = buildDockerCommand(config, { uid: 501, gid: 20, pid: 12345 });
  const joined = command.join("\0");

  assert.deepEqual(command.slice(0, 5), ["docker", "run", "--rm", "-it", "--name"]);
  assert.ok(command.includes("--cap-drop"));
  assert.ok(command.includes("ALL"));
  assert.ok(command.includes("--security-opt"));
  assert.ok(command.includes("no-new-privileges"));
  assert.ok(command.includes("--pids-limit"));
  assert.ok(command.includes("512"));
  assert.ok(command.includes("--user"));
  assert.ok(command.includes("501:20"));
  assert.ok(command.includes(`${workspace}:/workspace:rw`));
  assert.ok(command.includes(`${agentHome}:/agent-home:rw`));
  assert.ok(command.includes("HOME=/agent-home"));
  assert.ok(command.includes("APPV231_CODING_AGENT_DIR=/agent-home/agent"));
  assert.equal(command.some((value) => value.startsWith("APPV23_")), false);
  assert.ok(command.includes("appv231:test"));
  assert.ok(command.includes("--cwd"));
  assert.ok(command.includes("/workspace"));
  assert.ok(command.includes("--model"));
  assert.ok(command.includes("openrouter/qwen/qwen3.6-flash"));
  assert.equal(joined.includes(`${os.homedir()}:/`), false);
  assert.equal(joined.includes("--env-file"), false);
  assert.equal(joined.includes("--dotenv"), false);
  assert.equal(joined.includes(".env"), false);
});

test("global wrapper supports local image without pulling", () => {
  const config = parseArgs(["--image", "appv231:local", "--no-pull"]);

  assert.equal(config.image, "appv231:local");
  assert.equal(config.pull, false);
  assert.deepEqual(buildPullCommand(config), []);
});

test("global wrapper bypasses stale docker credentials only for public appv231 pulls", () => {
  const config = parseArgs([]);
  const customImage = parseArgs(["--image", "example.com/private/appv231:latest"]);

  assert.equal(shouldUseIsolatedDockerConfig(config, {}), true);
  assert.equal(shouldUseIsolatedDockerConfig(config, { DOCKER_CONFIG: "/tmp/docker" }), false);
  assert.equal(shouldUseIsolatedDockerConfig(config, { APPV231_DOCKER_CONFIG: "/tmp/docker" }), false);
  assert.equal(shouldUseIsolatedDockerConfig(customImage, {}), false);
  assert.equal(buildPullEnv(config, "/tmp/docker", {}).DOCKER_CONFIG, "/tmp/docker");
  assert.equal(buildPullEnv(config, "/tmp/ignored", { APPV231_DOCKER_CONFIG: "/tmp/explicit" }).DOCKER_CONFIG, "/tmp/explicit");
  assert.equal(buildPullEnv(config, "/tmp/isolated", {}).DOCKER_CONFIG, "/tmp/isolated");
});

test("global wrapper copies user agents skills into sandbox home", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-sandbox-global-"));
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  const skills = path.join(hostHome, ".agents", "skills");
  fs.mkdirSync(skills, { recursive: true });
  fs.writeFileSync(path.join(skills, "web_search.md"), "---\nname: web-search\n---\nUse curl.\n");
  fs.writeFileSync(path.join(skills, ".env"), "SECRET=not-copied\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome },
  );

  assert.equal(
    fs.readFileSync(path.join(agentHome, ".agents", "skills", "web_search.md"), "utf8"),
    "---\nname: web-search\n---\nUse curl.\n",
  );
  assert.equal(fs.existsSync(path.join(agentHome, ".agents", "skills", ".env")), false);
});

test("global wrapper copies bundled skills before user skill overrides", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-sandbox-global-"));
  const packageRoot = path.join(root, "package");
  const bundledSkill = path.join(packageRoot, "skills", "subagent-delegation");
  const hostHome = path.join(root, "host-home");
  const userSkill = path.join(hostHome, ".agents", "skills", "subagent-delegation");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledSkill, { recursive: true });
  fs.mkdirSync(userSkill, { recursive: true });
  fs.writeFileSync(path.join(bundledSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nBundled policy\n");
  fs.writeFileSync(path.join(userSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nUser policy\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(agentHome, ".agents", "skills", "subagent-delegation", "SKILL.md"), "utf8"),
    "---\nname: subagent-delegation\n---\nUser policy\n",
  );
});

test("global wrapper copies bundled skills without host user skills", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv231-sandbox-global-"));
  const packageRoot = path.join(root, "package");
  const bundledSkill = path.join(packageRoot, "skills", "subagent-delegation");
  const hostHome = path.join(root, "host-home");
  const agentHome = path.join(root, "agent-home");
  fs.mkdirSync(bundledSkill, { recursive: true });
  fs.mkdirSync(hostHome, { recursive: true });
  fs.writeFileSync(path.join(bundledSkill, "SKILL.md"), "---\nname: subagent-delegation\n---\nBundled policy\n");

  prepareSandboxImports(
    { agentHome, agentsFiles: [], skillsPaths: [], importUserSkills: true },
    { homeDir: hostHome, packageRoot },
  );

  assert.equal(
    fs.readFileSync(path.join(agentHome, ".agents", "skills", "subagent-delegation", "SKILL.md"), "utf8"),
    "---\nname: subagent-delegation\n---\nBundled policy\n",
  );
});
