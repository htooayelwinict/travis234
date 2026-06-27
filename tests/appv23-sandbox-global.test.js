const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  buildDockerCommand,
  buildPullCommand,
  parseArgs,
  prepareSandboxImports,
} = require("../bin/appv23-sandbox.js");

test("global wrapper defaults to production GHCR image and pull", () => {
  const config = parseArgs([]);

  assert.equal(config.image, "ghcr.io/htooayelwinict/appv23:production");
  assert.equal(config.pull, true);
  assert.deepEqual(buildPullCommand(config), ["docker", "pull", "ghcr.io/htooayelwinict/appv23:production"]);
});

test("global wrapper builds hardened docker command without host-home mounts", () => {
  const workspace = path.join(os.tmpdir(), "appv23-workspace");
  const agentHome = path.join(os.tmpdir(), "appv23-agent-home");
  const config = parseArgs([
    "--cwd",
    workspace,
    "--agent-home",
    agentHome,
    "--image",
    "appv23:test",
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
  assert.ok(command.includes("PI_CODING_AGENT_DIR=/agent-home/agent"));
  assert.ok(command.includes("appv23:test"));
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
  const config = parseArgs(["--image", "appv23:local", "--no-pull"]);

  assert.equal(config.image, "appv23:local");
  assert.equal(config.pull, false);
  assert.deepEqual(buildPullCommand(config), []);
});

test("global wrapper copies user agents skills into sandbox home", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "appv23-sandbox-global-"));
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
