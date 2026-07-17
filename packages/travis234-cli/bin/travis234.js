#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DEFAULT_IMAGE =
  process.env.TRAVIS234_IMAGE ||
  process.env.TRAVIS234_SANDBOX_IMAGE ||
  "ghcr.io/htooayelwinict/travis234:production";
const PUBLIC_IMAGE_PREFIX = "ghcr.io/htooayelwinict/travis234:";
const DEFAULT_PULL_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const CONTAINER_WORKSPACE = "/workspace";
const CONTAINER_HOME = "/travis-home";
const APP_CONFIG_DIR = ".travis234";
const APP_AGENT_DIR = "agent";
const IMPORTED_AGENTS_MARKER = "<!-- travis234-sandbox-imported-agents -->";
const SKIP_IMPORT_NAMES = new Set([
  ".DS_Store",
  ".git",
  ".hg",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  ".svn",
  ".venv",
  "__pycache__",
  "auth.json",
  "node_modules",
  "venv",
]);

function parseArgs(argv, runtime = {}) {
  const env = runtime.env || process.env;
  const homeDir = runtime.homeDir || os.homedir();
  const config = {
    cwd: runtime.cwd || process.cwd(),
    image: env.TRAVIS234_IMAGE || env.TRAVIS234_SANDBOX_IMAGE || DEFAULT_IMAGE,
    agentHome: env.TRAVIS234_SANDBOX_HOME || path.join(homeDir, ".travis234", "sandbox-home"),
    network: true,
    dryRun: false,
    pull: "auto",
    agentsFiles: [],
    skillsPaths: [],
    importUserSkills: true,
    appArgs: [],
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--") {
      config.appArgs.push(...argv.slice(index + 1));
      break;
    }
    if (arg === "--cwd") {
      config.cwd = requireValue(argv, ++index, arg);
      continue;
    }
    if (arg.startsWith("--cwd=")) {
      config.cwd = arg.slice("--cwd=".length);
      continue;
    }
    if (arg === "--image") {
      config.image = requireValue(argv, ++index, arg);
      continue;
    }
    if (arg.startsWith("--image=")) {
      config.image = arg.slice("--image=".length);
      continue;
    }
    if (arg === "--agent-home") {
      config.agentHome = requireValue(argv, ++index, arg);
      continue;
    }
    if (arg.startsWith("--agent-home=")) {
      config.agentHome = arg.slice("--agent-home=".length);
      continue;
    }
    if (arg === "--agents-file") {
      config.agentsFiles.push(requireValue(argv, ++index, arg));
      continue;
    }
    if (arg.startsWith("--agents-file=")) {
      config.agentsFiles.push(arg.slice("--agents-file=".length));
      continue;
    }
    if (arg === "--with-skills") {
      config.skillsPaths.push(requireValue(argv, ++index, arg));
      continue;
    }
    if (arg.startsWith("--with-skills=")) {
      config.skillsPaths.push(arg.slice("--with-skills=".length));
      continue;
    }
    if (arg === "--no-user-skills") {
      config.importUserSkills = false;
      continue;
    }
    if (arg === "--no-network") {
      config.network = false;
      continue;
    }
    if (arg === "--pull") {
      config.pull = "always";
      continue;
    }
    if (arg === "--no-pull") {
      config.pull = "never";
      continue;
    }
    if (arg === "--dry-run") {
      config.dryRun = true;
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      config.help = true;
      continue;
    }
    config.appArgs.push(arg);
  }

  return {
    ...config,
    cwd: resolvePath(config.cwd, homeDir),
    agentHome: resolvePath(config.agentHome, homeDir),
    agentsFiles: config.agentsFiles.map((value) => resolvePath(value, homeDir)),
    skillsPaths: config.skillsPaths.map((value) => resolvePath(value, homeDir)),
    appArgs: sanitizeAppArgs(config.appArgs),
  };
}

function requireValue(argv, index, flag) {
  const value = argv[index];
  if (!value) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

function resolvePath(value, homeDir = os.homedir()) {
  const expanded = value.startsWith("~/") ? path.join(homeDir, value.slice(2)) : value;
  return path.resolve(expanded);
}

function sanitizeAppArgs(args) {
  const stripped = [];
  let skipNext = false;
  for (const arg of args) {
    if (skipNext) {
      skipNext = false;
      continue;
    }
    if (arg === "--cwd" || arg === "--dotenv") {
      skipNext = true;
      continue;
    }
    if (arg.startsWith("--cwd=") || arg.startsWith("--dotenv=")) {
      continue;
    }
    stripped.push(arg);
  }
  return stripped;
}

function buildDockerCommand(config, runtime = {}) {
  const pid = runtime.pid ?? process.pid;
  const uid = runtime.uid ?? (typeof process.getuid === "function" ? process.getuid() : 1000);
  const gid = runtime.gid ?? (typeof process.getgid === "function" ? process.getgid() : 1000);
  const command = [
    "docker",
    "run",
    "--rm",
    "-it",
    "--name",
    `travis234-sandbox-${pid}`,
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--user",
    `${uid}:${gid}`,
    "--workdir",
    CONTAINER_WORKSPACE,
    "--pids-limit",
    "512",
    "-v",
    `${config.cwd}:${CONTAINER_WORKSPACE}:rw`,
    "-v",
    `${config.agentHome}:${CONTAINER_HOME}:rw`,
    "-e",
    `HOME=${CONTAINER_HOME}`,
    "-e",
    `TRAVIS234_CODING_AGENT_DIR=${CONTAINER_HOME}/agent`,
    "-e",
    "TRAVIS234_SANDBOX=1",
    "-e",
    `TRAVIS234_WORKSPACE_ROOT=${CONTAINER_WORKSPACE}`,
    "-e",
    `TRAVIS234_AGENT_HOME=${CONTAINER_HOME}`,
    "-e",
    "TRAVIS234_NO_VENV_REEXEC=1",
    "-e",
    "PYTHONUNBUFFERED=1",
    "-e",
    "DEBIAN_FRONTEND=noninteractive",
  ];
  if (!config.network) {
    command.push("--network=none");
  }
  command.push(config.image, "--cwd", CONTAINER_WORKSPACE, ...config.appArgs);
  return command;
}

function buildPullCommand(config, runtime = {}) {
  if (config.pull === "never" || config.pull === false) {
    return [];
  }
  if (config.pull === "always" || config.pull === true) {
    return ["docker", "pull", config.image];
  }
  return shouldAutoPull(config, runtime) ? ["docker", "pull", config.image] : [];
}

function isPublicTravis234Image(image) {
  return image.startsWith(PUBLIC_IMAGE_PREFIX);
}

function shouldUseIsolatedDockerConfig(config, env = process.env) {
  return (
    config.pull !== "never" &&
    config.pull !== false &&
    isPublicTravis234Image(config.image) &&
    !env.DOCKER_CONFIG &&
    !env.TRAVIS234_DOCKER_CONFIG
  );
}

function buildPullEnv(config, dockerConfig, env = process.env) {
  if (env.TRAVIS234_DOCKER_CONFIG) {
    return { ...env, DOCKER_CONFIG: env.TRAVIS234_DOCKER_CONFIG };
  }
  if (dockerConfig) {
    return { ...env, DOCKER_CONFIG: dockerConfig };
  }
  return env;
}

function shouldAutoPull(config, runtime = {}) {
  const pulledAtMs = readPullCache(config)[config.image];
  if (typeof pulledAtMs !== "number") {
    return true;
  }
  const nowMs = runtime.nowMs ?? Date.now();
  return nowMs < pulledAtMs || nowMs - pulledAtMs > DEFAULT_PULL_CACHE_TTL_MS;
}

function recordPullSuccess(config, runtime = {}) {
  const cache = readPullCache(config);
  cache[config.image] = runtime.nowMs ?? Date.now();
  fs.mkdirSync(config.agentHome, { recursive: true, mode: 0o700 });
  fs.writeFileSync(pullCachePath(config), `${JSON.stringify(cache, null, 2)}\n`, { mode: 0o600 });
}

function readPullCache(config) {
  try {
    const parsed = JSON.parse(fs.readFileSync(pullCachePath(config), "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function pullCachePath(config) {
  return path.join(config.agentHome, ".pull-cache.json");
}

function prepareSandboxImports(config, runtime = {}) {
  const homeDir = runtime.homeDir || os.homedir();
  const packageRoot = runtime.packageRoot || path.resolve(__dirname, "..");
  fs.mkdirSync(config.agentHome, { recursive: true, mode: 0o700 });
  seedHostDefaults(homeDir, packageRoot);
  prepareAgentsFiles(config, homeDir);
  prepareSkills(config, homeDir, packageRoot);
}

function seedHostDefaults(homeDir, packageRoot) {
  seedHostSkills(homeDir, packageRoot);
}

function seedHostSkills(homeDir, packageRoot) {
  const sourceRoot = path.join(packageRoot, "skills");
  const targetRoot = path.join(hostAgentDir(homeDir), "skills");
  if (!fs.existsSync(sourceRoot)) {
    return;
  }
  for (const child of fs.readdirSync(sourceRoot).sort()) {
    const source = path.join(sourceRoot, child);
    if (shouldSkipImport(source)) {
      continue;
    }
    const stat = fs.statSync(source);
    if (stat.isDirectory()) {
      const target = path.join(targetRoot, child);
      if (!fs.existsSync(target)) {
        copyTreeSafe(source, target);
      }
    } else if (stat.isFile() && path.extname(source) === ".md") {
      const target = path.join(targetRoot, child);
      if (!fs.existsSync(target)) {
        copyFileSafe(source, target);
      }
    }
  }
}

function prepareAgentsFiles(config, homeDir = os.homedir()) {
  const sources = collectAgentsFiles(config, homeDir);
  const target = path.join(config.agentHome, "agent", "AGENTS.md");
  if (!sources.length) {
    removeImportedAgentsFile(target);
    return;
  }
  const parts = [
    IMPORTED_AGENTS_MARKER,
    "# Imported travis234 sandbox instructions",
    "",
    "These instructions were copied into the sandbox from host ~/.travis234/agent/AGENTS.md and explicit --agents-file arguments.",
    "",
  ];
  for (const source of sources) {
    const stat = fs.statSync(source);
    if (!stat.isFile()) {
      throw new Error(`agents file is not a file: ${source}`);
    }
    parts.push(`## Source: ${source}`, "", fs.readFileSync(source, "utf8"), "");
  }
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  fs.writeFileSync(target, parts.join("\n"), { mode: 0o600 });
}

function collectAgentsFiles(config, homeDir) {
  const sources = [];
  const userAgentsFile = path.join(hostAgentDir(homeDir), "AGENTS.md");
  if (fs.existsSync(userAgentsFile)) {
    sources.push(userAgentsFile);
  }
  sources.push(...config.agentsFiles);
  const deduped = [];
  const seen = new Set();
  for (const source of sources) {
    const key = path.resolve(source);
    if (seen.has(key)) {
      continue;
    }
    deduped.push(key);
    seen.add(key);
  }
  return deduped;
}

function removeImportedAgentsFile(target) {
  if (!fs.existsSync(target)) {
    return;
  }
  const text = fs.readFileSync(target, "utf8");
  if (text.startsWith(IMPORTED_AGENTS_MARKER)) {
    fs.unlinkSync(target);
  }
}

function prepareSkills(config, homeDir, packageRoot) {
  const sources = [];
  const bundledSkills = path.join(packageRoot, "skills");
  if (fs.existsSync(bundledSkills)) {
    sources.push(bundledSkills);
  }
  const userSkills = path.join(hostAgentDir(homeDir), "skills");
  if (config.importUserSkills && fs.existsSync(userSkills)) {
    sources.push(userSkills);
  }
  sources.push(...config.skillsPaths);
  if (!sources.length) {
    return;
  }
  const targetRoot = path.join(config.agentHome, APP_AGENT_DIR, "skills");
  fs.rmSync(targetRoot, { recursive: true, force: true });
  fs.mkdirSync(targetRoot, { recursive: true, mode: 0o700 });
  for (const source of sources) {
    copySkillSource(source, targetRoot);
  }
}

function copySkillSource(source, targetRoot) {
  const stat = fs.statSync(source);
  if (stat.isFile()) {
    if (path.extname(source) !== ".md") {
      throw new Error(`skills file must be markdown: ${source}`);
    }
    copyFileSafe(source, path.join(targetRoot, path.basename(source)));
    return;
  }
  if (!stat.isDirectory()) {
    throw new Error(`skills path is not a file or directory: ${source}`);
  }
  if (fs.existsSync(path.join(source, "SKILL.md"))) {
    copyTreeSafe(source, path.join(targetRoot, path.basename(source)));
    return;
  }
  for (const child of fs.readdirSync(source).sort()) {
    const childPath = path.join(source, child);
    if (shouldSkipImport(childPath)) {
      continue;
    }
    const childStat = fs.statSync(childPath);
    if (childStat.isDirectory()) {
      copyTreeSafe(childPath, path.join(targetRoot, child));
    } else if (childStat.isFile() && path.extname(childPath) === ".md") {
      copyFileSafe(childPath, path.join(targetRoot, child));
    }
  }
}

function copyTreeSafe(source, target) {
  if (shouldSkipImport(source) || fs.lstatSync(source).isSymbolicLink()) {
    return;
  }
  const stat = fs.statSync(source);
  if (stat.isFile()) {
    copyFileSafe(source, target);
    return;
  }
  fs.mkdirSync(target, { recursive: true });
  for (const child of fs.readdirSync(source).sort()) {
    const childPath = path.join(source, child);
    if (shouldSkipImport(childPath)) {
      continue;
    }
    const childTarget = path.join(target, child);
    const childStat = fs.statSync(childPath);
    if (childStat.isDirectory()) {
      copyTreeSafe(childPath, childTarget);
    } else if (childStat.isFile()) {
      copyFileSafe(childPath, childTarget);
    }
  }
}

function copyFileSafe(source, target) {
  if (shouldSkipImport(source) || fs.lstatSync(source).isSymbolicLink()) {
    return;
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(source, target);
}

function shouldSkipImport(filePath) {
  const name = path.basename(filePath);
  return SKIP_IMPORT_NAMES.has(name) || name.startsWith(".env");
}

function hostAgentDir(homeDir) {
  return path.join(homeDir, APP_CONFIG_DIR, APP_AGENT_DIR);
}

function printHelp() {
  process.stdout.write(`Travis234

Run the prebuilt Travis234 Docker image from any directory.

Usage:
  travis234 [options] [-- application args]

Options:
  --cwd <path>          Host workspace to mount as /workspace. Defaults to current directory.
  --image <name>        Docker image. Defaults to TRAVIS234_IMAGE, TRAVIS234_SANDBOX_IMAGE, or ghcr.io/htooayelwinict/travis234:production.
  --agent-home <path>   Sandbox state directory. Defaults to ~/.travis234/sandbox-home.
  --agents-file <path>  Copy an explicit AGENTS.md-style file into sandbox context.
  --with-skills <path>  Copy an extra skill file or directory into the sandbox agent/skills directory.
  --no-user-skills      Do not copy host ~/.travis234/agent/skills.
  --no-network          Run container with --network=none.
  --pull                Pull image before running. Default.
  --no-pull             Do not pull image before running.
  --dry-run             Print docker command instead of running it.
  -h, --help            Show help.
`);
}

function main(argv = process.argv.slice(2)) {
  let config;
  try {
    config = parseArgs(argv);
    if (config.help) {
      printHelp();
      return 0;
    }
    if (!fs.existsSync(config.cwd) || !fs.statSync(config.cwd).isDirectory()) {
      throw new Error(`workspace does not exist or is not a directory: ${config.cwd}`);
    }
    prepareSandboxImports(config);
    const pullCommand = buildPullCommand(config);
    const command = buildDockerCommand(config);
    if (config.dryRun) {
      if (pullCommand.length) {
        process.stdout.write(`${pullCommand.map(shellQuote).join(" ")}\n`);
      }
      process.stdout.write(`${command.map(shellQuote).join(" ")}\n`);
      return 0;
    }
    if (pullCommand.length) {
      let dockerConfig;
      try {
        if (shouldUseIsolatedDockerConfig(config)) {
          dockerConfig = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-docker-config-"));
        }
        const pull = spawnSync(pullCommand[0], pullCommand.slice(1), {
          stdio: "inherit",
          env: buildPullEnv(config, dockerConfig),
        });
        if ((pull.status ?? 1) !== 0) {
          return pull.status ?? 1;
        }
        recordPullSuccess(config);
      } finally {
        if (dockerConfig) {
          fs.rmSync(dockerConfig, { recursive: true, force: true });
        }
      }
    }
    const result = spawnSync(command[0], command.slice(1), { stdio: "inherit" });
    return result.status ?? 1;
  } catch (error) {
    process.stderr.write(`Error: ${error.message}\n`);
    return 1;
  }
}

function shellQuote(value) {
  if (/^[A-Za-z0-9_./:=@+-]+$/.test(value)) {
    return value;
  }
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export {
  buildDockerCommand,
  buildPullEnv,
  buildPullCommand,
  main,
  parseArgs,
  prepareSandboxImports,
  recordPullSuccess,
  sanitizeAppArgs,
  shouldAutoPull,
  shouldUseIsolatedDockerConfig,
};

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fs.realpathSync(__filename)) {
  process.exit(main());
}
