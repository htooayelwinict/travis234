from __future__ import annotations

import json
from pathlib import Path

_NODE_SETUPS = {
    "node-cli-dry-run", "node-package-install", "node-abort-controller",
    "javascript-module-refactor", "frontend-accessibility", "frontend-responsive-overflow",
}
_HYBRID_SETUPS = {"python-node-contract", "release-packaging"}


def build_fixture(setup: str, root: str | Path) -> Path:
    target = Path(root).resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"fixture directory is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    (target / "TASK.md").write_text(
        f"# SDLC fixture: {setup}\n\nUse the user turns as the authoritative requirements.\n",
        encoding="utf-8",
    )
    if setup in _SCENARIO_FILES:
        _write_files(target, _SCENARIO_FILES[setup])
        return target
    if setup in _NODE_SETUPS or setup in _HYBRID_SETUPS:
        _build_node_seed(target, setup)
    if setup not in _NODE_SETUPS or setup in _HYBRID_SETUPS:
        _build_python_seed(target, setup)
    return target


def _write_files(target: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


_PYPROJECT = """[project]
name = "sdlc-fixture"
version = "0.1.0"
requires-python = ">=3.11"
"""


_SCENARIO_FILES: dict[str, dict[str, str]] = {
    "python-cli-feature": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "",
        "fixture_app/cli.py": '''import argparse

def render(items, output_format="text"):
    return "\\n".join(items)

def main(argv=None):
    parser = argparse.ArgumentParser(prog="fixture")
    parser.add_argument("items", nargs="*")
    args = parser.parse_args(argv)
    print(render(args.items))
''',
        "README.md": "# Fixture CLI\n\nRun `python -m fixture_app.cli one two`.\n",
        "tests/test_cli.py": '''import json
from fixture_app.cli import main, render

def test_text_output_is_preserved():
    assert render(["one", "two"]) == "one\\ntwo"

def test_json_output_is_machine_readable():
    assert json.loads(render(["one", "two"], "json")) == ["one", "two"]

def test_main_accepts_format(capsys):
    main(["--format", "json", "one"])
    assert json.loads(capsys.readouterr().out) == ["one"]
''',
    },
    "python-async-race": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .cache import AsyncCache\n",
        "fixture_app/cache.py": '''class AsyncCache:
    def __init__(self, loader):
        self.loader = loader
        self.values = {}

    async def get(self, key):
        if key not in self.values:
            self.values[key] = await self.loader(key)
        return self.values[key]
''',
        "tests/test_cache.py": '''import asyncio
from fixture_app.cache import AsyncCache

def test_duplicate_misses_are_single_flight():
    calls = 0
    async def loader(key):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return key.upper()
    async def run():
        cache = AsyncCache(loader)
        assert await asyncio.gather(cache.get("a"), cache.get("a")) == ["A", "A"]
    asyncio.run(run())
    assert calls == 1

def test_cancelled_waiter_does_not_cancel_shared_load():
    async def run():
        gate = asyncio.Event()
        cache = AsyncCache(lambda key: gate.wait())
        first = asyncio.create_task(cache.get("a"))
        second = asyncio.create_task(cache.get("a"))
        await asyncio.sleep(0)
        first.cancel()
        gate.set()
        assert await second is True
    asyncio.run(run())
''',
    },
    "python-parser-refactor": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .parser import ParseError, parse\n",
        "fixture_app/parser.py": '''class ParseError(ValueError):
    pass

def parse(source):
    tokens = []
    for raw in source.replace("=", " = ").split():
        tokens.append(raw)
    if len(tokens) != 3 or tokens[1] != "=":
        raise ParseError("expected NAME = VALUE")
    if not tokens[0].isidentifier():
        raise ParseError("invalid name")
    return {"name": tokens[0], "value": tokens[2]}
''',
        "tests/test_parser.py": '''import importlib
import pytest
from fixture_app import ParseError, parse

def test_public_import_and_behavior_are_stable():
    assert parse("answer=42") == {"name": "answer", "value": "42"}
    with pytest.raises(ParseError, match="expected"):
        parse("answer")

def test_lexer_and_validation_are_owned_by_focused_modules():
    assert hasattr(importlib.import_module("fixture_app.lexer"), "lex")
    assert hasattr(importlib.import_module("fixture_app.validation"), "validate")
''',
    },
    "config-migration": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .config import load_config, save_config\n",
        "fixture_app/config.py": '''import json

def load_config(path):
    return json.loads(path.read_text())

def save_config(path, config):
    path.write_text(json.dumps(config))
''',
        "tests/test_config.py": '''import json
import pytest
from fixture_app.config import load_config, save_config

def test_v1_is_migrated_to_nested_v2(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"theme":"dark","timeout":5}')
    assert load_config(path) == {"version": 2, "ui": {"theme": "dark"}, "network": {"timeout": 5}}

def test_malformed_config_has_path_diagnostic(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{")
    with pytest.raises(ValueError, match="bad.json"):
        load_config(path)

def test_save_is_v2_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "config.json"
    save_config(path, {"version": 2, "ui": {}, "network": {}})
    assert json.loads(path.read_text())["version"] == 2
    assert list(tmp_path.glob("*.tmp")) == []
''',
    },
    "http-client-retry": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .client import RetryClient\n",
        "fixture_app/client.py": '''class RetryClient:
    def __init__(self, request, sleep=lambda delay: None, retries=3):
        self.request = request
        self.sleep = sleep
        self.retries = retries

    def get(self, url, cancelled=lambda: False):
        response = None
        for attempt in range(self.retries):
            response = self.request(url)
            if response.status == 200:
                return response
            self.sleep(attempt + 1)
        return response
''',
        "tests/test_client.py": '''from types import SimpleNamespace
import pytest
from fixture_app.client import RetryClient

def response(status, retry_after=None):
    return SimpleNamespace(status=status, headers={} if retry_after is None else {"Retry-After": retry_after})

def test_does_not_retry_client_errors():
    calls = []
    client = RetryClient(lambda url: calls.append(url) or response(404))
    assert client.get("/").status == 404
    assert calls == ["/"]

def test_retries_transient_status_with_retry_after():
    values = iter([response(503, "2"), response(200)])
    sleeps = []
    assert RetryClient(lambda url: next(values), sleeps.append).get("/").status == 200
    assert sleeps == [2.0]

def test_cancellation_stops_before_next_attempt():
    with pytest.raises(RuntimeError, match="cancel"):
        RetryClient(lambda url: response(503)).get("/", cancelled=lambda: True)
''',
    },
    "path-traversal-repair": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .archive import UnsafeArchiveError, extract_zip\n",
        "fixture_app/archive.py": '''import zipfile

class UnsafeArchiveError(ValueError):
    pass

def extract_zip(source, destination):
    with zipfile.ZipFile(source) as archive:
        archive.extractall(destination)
''',
        "tests/test_archive.py": '''import zipfile
import pytest
from fixture_app.archive import UnsafeArchiveError, extract_zip

def make_zip(path, name):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "owned")

def test_parent_escape_is_rejected(tmp_path):
    source = tmp_path / "bad.zip"
    make_zip(source, "../outside.txt")
    with pytest.raises(UnsafeArchiveError):
        extract_zip(source, tmp_path / "out")
    assert not (tmp_path / "outside.txt").exists()

def test_safe_member_extracts(tmp_path):
    source = tmp_path / "safe.zip"
    make_zip(source, "nested/value.txt")
    extract_zip(source, tmp_path / "out")
    assert (tmp_path / "out/nested/value.txt").read_text() == "owned"
''',
    },
    "streaming-memory-bound": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .collector import StreamCollector\n",
        "fixture_app/collector.py": '''class StreamCollector:
    def __init__(self):
        self.chunks = []

    def add(self, chunk):
        self.chunks.append(chunk)

    @property
    def tail(self):
        return b"".join(self.chunks).decode()
''',
        "tests/test_collector.py": '''import os
from fixture_app.collector import StreamCollector

def test_memory_tail_is_bounded_and_complete_output_is_spooled(tmp_path):
    collector = StreamCollector(max_tail_bytes=1024, spool_dir=tmp_path)
    for _ in range(10240):
        collector.add(b"x" * 1024)
    assert len(collector.tail.encode()) <= 1024
    assert collector.full_output_path.read_bytes() == b"x" * (10 * 1024 * 1024)
    assert os.stat(collector.full_output_path).st_mode & 0o777 == 0o600

def test_binary_data_uses_replacement_decoding(tmp_path):
    collector = StreamCollector(max_tail_bytes=10, spool_dir=tmp_path)
    collector.add(b"abc\\xff")
    assert "\\ufffd" in collector.tail
    collector.close()
''',
    },
    "jsonl-session-recovery": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .session import CorruptSessionError, SessionStore\n",
        "fixture_app/session.py": '''import json

class CorruptSessionError(ValueError):
    pass

class SessionStore:
    def __init__(self, path):
        self.path = path

    def append(self, item):
        with self.path.open("a") as handle:
            handle.write(json.dumps(item) + "\\n")

    def load(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]
''',
        "tests/test_session.py": '''import json
import pytest
from fixture_app.session import CorruptSessionError, SessionStore

def test_truncated_tail_is_quarantined_and_valid_prefix_recovers(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"id":1}\\n{"id":')
    assert SessionStore(path).load() == [{"id": 1}]
    assert (tmp_path / "session.jsonl.corrupt").read_text() == '{"id":'

def test_middle_corruption_is_a_hard_failure(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"id":1}\\nnot-json\\n{"id":2}\\n')
    with pytest.raises(CorruptSessionError, match="line 2"):
        SessionStore(path).load()

def test_append_produces_complete_json_lines(tmp_path):
    path = tmp_path / "session.jsonl"
    store = SessionStore(path)
    for value in range(20):
        store.append({"id": value})
    assert [json.loads(line)["id"] for line in path.read_text().splitlines()] == list(range(20))
''',
    },
    "node-cli-dry-run": {
        "package.json": '''{"name":"dry-run-fixture","version":"0.1.0","private":true,"scripts":{"test":"node --test"}}\n''',
        "cli.js": ''''use strict';
const fs = require('node:fs');

function plan(file, value) { return `write ${file}: ${value}`; }
function run(args, io = console) {
  const [file, value] = args;
  fs.writeFileSync(file, value);
  io.log(plan(file, value));
}
if (require.main === module) run(process.argv.slice(2));
module.exports = { plan, run };
''',
        "index.test.js": r''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { plan, run } = require('./cli');

test('dry run prints the identical plan without writing', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dry-run-'));
  const file = path.join(root, 'value.txt');
  const output = [];
  run(['--dry-run', file, 'next'], { log: line => output.push(line) });
  assert.deepEqual(output, [plan(file, 'next')]);
  assert.equal(fs.existsSync(file), false);
});
''',
    },
    "node-package-install": {
        "package.json": '''{"name":"kleur-fixture","version":"0.1.0","private":true,"scripts":{"test":"node --test"}}\n''',
        "formatter.js": ''''use strict';
function format(value, options = {}) { return String(value); }
module.exports = { format };
''',
        "index.test.js": ''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { format } = require('./formatter');

test('color mode uses ANSI and disabled mode stays plain', () => {
  assert.match(format('ok', { color: true }), /\\u001b\\[/);
  assert.equal(format('ok', { color: false }), 'ok');
});
''',
    },
    "node-abort-controller": {
        "package.json": '''{"name":"abort-fixture","version":"0.1.0","private":true,"scripts":{"test":"node --test"}}\n''',
        "client.js": ''''use strict';
async function stream(url, { fetchImpl = fetch } = {}) {
  const response = await fetchImpl(url);
  return response.body;
}
module.exports = { stream };
''',
        "index.test.js": ''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { stream } = require('./client');

test('caller signal is propagated to fetch', async () => {
  const controller = new AbortController();
  let observed;
  const fetchImpl = async (_url, options) => { observed = options.signal; return { body: [] }; };
  await stream('/', { fetchImpl, signal: controller.signal, timeoutMs: 100 });
  assert.equal(observed, controller.signal);
});

test('timeout aborts the composed request and cleans its timer', async () => {
  let observed;
  const fetchImpl = (_url, options) => new Promise((resolve, reject) => {
    observed = options.signal;
    options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
  });
  await assert.rejects(stream('/', { fetchImpl, timeoutMs: 5 }));
  assert.equal(observed.aborted, true);
});
''',
    },
    "javascript-module-refactor": {
        "package.json": '''{"name":"workflow-fixture","version":"0.1.0","private":true,"scripts":{"test":"node --test"}}\n''',
        "workflow.js": ''''use strict';
function run(tasks) {
  const state = { completed: [] };
  const report = [];
  for (const task of tasks) {
    state.completed.push(task.id);
    report.push(`completed:${task.id}`);
  }
  return { state, report };
}
module.exports = { run };
''',
        "index.test.js": ''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { run } = require('./workflow');

test('entry API preserves deterministic ordering', () => {
  assert.deepEqual(run([{id:'b'}, {id:'a'}]).report, ['completed:b', 'completed:a']);
});
test('scheduler state and reporter have focused module ownership', () => {
  for (const file of ['scheduler.js', 'state.js', 'reporter.js']) assert.equal(fs.existsSync(file), true, file);
});
''',
    },
    "frontend-accessibility": {
        "package.json": '''{"name":"a11y-fixture","version":"0.1.0","private":true,"scripts":{"test":"node --test"}}\n''',
        "index.html": '''<!doctype html><html><body><div class="add" onclick="addTask()">Add task</div><input id="title"><div id="board"></div><script src="app.js"></script></body></html>\n''',
        "styles.css": '''.add { color: #aaa; background: #fff; transition: all 1s; }\n''',
        "app.js": '''function addTask() { document.querySelector('#board').textContent = 'Added'; }\n''',
        "index.test.js": ''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const html = fs.readFileSync('index.html', 'utf8');
const css = fs.readFileSync('styles.css', 'utf8');
test('primary action is a labeled semantic button', () => {
  assert.match(html, /<button[^>]*(aria-label="Add task"|>Add task<)/i);
  assert.match(html, /<label[^>]*for="title"/i);
});
test('focus and reduced motion styles exist', () => {
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion/);
});
''',
    },
    "frontend-responsive-overflow": {
        "package.json": '''{"name":"responsive-fixture","version":"0.1.0","private":true,"scripts":{"test":"node --test"}}\n''',
        "index.html": '''<!doctype html><html><body><main><div class="toolbar"><button>Extremely long action label</button><button>Export</button></div><section class="cards"><article>Supercalifragilisticexpialidocious</article></section></main></body></html>\n''',
        "styles.css": '''main { width: 900px; } .toolbar { display:flex; white-space:nowrap; } .cards { display:grid; grid-template-columns:repeat(3, 300px); }\n''',
        "index.test.js": r''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const css = fs.readFileSync('styles.css', 'utf8');
test('layout uses shrinkable tracks and wraps narrow toolbar labels', () => {
  assert.match(css, /minmax\(0,\s*1fr\)/);
  assert.match(css, /overflow-wrap:\s*(anywhere|break-word)/);
  assert.match(css, /@media\s*\([^)]*max-width/);
});
test('page does not use a fixed desktop main width', () => assert.doesNotMatch(css, /main\s*{[^}]*width:\s*900px/s));
''',
    },
    "sqlite-migration": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .database import connect, migrate\n",
        "fixture_app/database.py": '''import sqlite3

def connect(path):
    database = sqlite3.connect(path)
    database.execute("create table if not exists users (id integer primary key, name text not null)")
    database.execute("pragma user_version=1")
    database.commit()
    return database

def migrate(database):
    database.execute("alter table users add column email text")
    database.execute("pragma user_version=2")
    database.commit()
''',
        "tests/test_database.py": '''import sqlite3
import pytest
from fixture_app.database import connect, migrate

def version(database):
    return database.execute("pragma user_version").fetchone()[0]

def test_v2_migration_is_idempotent_and_preserves_legacy_rows(tmp_path):
    database = connect(tmp_path / "app.db")
    database.execute("insert into users(name) values ('Ada')")
    database.commit()
    migrate(database)
    migrate(database)
    assert version(database) == 2
    assert database.execute("select name, email from users").fetchall() == [("Ada", None)]

def test_malformed_legacy_rows_roll_back_schema_change(tmp_path):
    database = connect(tmp_path / "bad.db")
    database.execute("insert into users(name) values ('')")
    database.commit()
    with pytest.raises(ValueError, match="legacy"):
        migrate(database)
    assert version(database) == 1
    assert "email" not in {row[1] for row in database.execute("pragma table_info(users)")}
''',
        "MIGRATION.md": "# Schema migration\n\nVersion 1 currently has no documented upgrade path.\n",
    },
    "python-node-contract": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .events import decode_event, encode_event\n",
        "fixture_app/events.py": '''import json

def encode_event(kind, payload):
    return json.dumps({"event_type": kind, "data": payload})

def decode_event(value):
    return json.loads(value)
''',
        "consumer.js": ''''use strict';
function decodeEvent(line) {
  const value = JSON.parse(line);
  if (!value.type || !value.payload) throw new Error('invalid event');
  return value;
}
function encodeEvent(type, payload) { return JSON.stringify({ type, payload }); }
module.exports = { decodeEvent, encodeEvent };
''',
        "CONTRACT.md": "# Event contract\n\nThe canonical schema is not yet defined.\n",
        "tests/test_events.py": '''import json
import pytest
from fixture_app.events import decode_event, encode_event

def test_python_uses_canonical_type_payload_contract():
    assert json.loads(encode_event("created", {"id": 1})) == {"type": "created", "payload": {"id": 1}}
    assert decode_event('{"type":"created","payload":{"id":1}}')["type"] == "created"

def test_python_rejects_malformed_events():
    with pytest.raises(ValueError, match="event"):
        decode_event('{"type":"created"}')
''',
        "index.test.js": ''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { decodeEvent, encodeEvent } = require('./consumer');
test('Node uses canonical type/payload contract', () => {
  assert.deepEqual(JSON.parse(encodeEvent('created', {id:1})), {type:'created', payload:{id:1}});
  assert.equal(decodeEvent('{"type":"created","payload":{"id":1}}').payload.id, 1);
});
test('Node rejects malformed events clearly', () => assert.throws(() => decodeEvent('{"type":"x"}'), /event/i));
''',
        "package.json": '''{"name":"contract-fixture","version":"0.1.0","private":true,"scripts":{"test":"node --test"}}\n''',
    },
    "failing-suite-diagnosis": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .diagnosis import normalize_name, parse_limit\n",
        "fixture_app/diagnosis.py": '''def normalize_name(value):
    return value.lower()

def parse_limit(value):
    return int(value or 0)
''',
        "tests/test_diagnosis.py": '''from fixture_app.diagnosis import normalize_name, parse_limit

def test_normalize_trims_and_casefolds():
    assert normalize_name("  STRASSE  ") == "strasse"

def test_limit_defaults_to_ten():
    assert parse_limit(None) == 10

def test_negative_limit_is_rejected():
    try:
        parse_limit("-1")
    except ValueError as error:
        assert "non-negative" in str(error)
    else:
        raise AssertionError("negative limit accepted")
''',
        "ROOT_CAUSE.md": "# Root causes\n\nDocument each failure without changing protected expectations.\n",
        "PROTECTED_EXPECTATIONS.sha256": "test_normalize_trims_and_casefolds\ntest_limit_defaults_to_ten\ntest_negative_limit_is_rejected\n",
    },
    "multi-file-domain-rename": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .domain import Whitelist, is_whitelisted\n",
        "fixture_app/domain.py": '''class Whitelist:
    def __init__(self, values=()):
        self.values = set(values)

def is_whitelisted(value, whitelist):
    return value in whitelist.values
''',
        "fixture_app/storage.py": '''def encode_whitelist(values):
    return {"whitelist": list(values)}
''',
        "tests/test_domain.py": '''from fixture_app import Allowlist, Whitelist, is_allowed
from fixture_app.storage import encode_allowlist

def test_new_internal_vocabulary_and_external_alias():
    current = Allowlist(["safe"])
    assert is_allowed("safe", current)
    assert Whitelist is Allowlist
    assert encode_allowlist(["safe"]) == {"allowlist": ["safe"]}
''',
        "MIGRATION.md": "# Domain migration\n\nMigration details pending.\n",
        "README.md": "The whitelist controls accepted values.\n",
    },
    "docs-code-alignment": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "",
        "fixture_app/cli.py": '''import argparse

def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--colour", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    return parser

def main(argv=None):
    args = build_parser().parse_args(argv)
    return {"color": args.colour, "limit": args.limit}
''',
        "README.md": "# Docs fixture\n\nUse `fixture --color --limit 10`. The default limit is 20.\n",
        "tests/test_docs.py": '''from pathlib import Path
from fixture_app.cli import main

def test_documented_color_flag_executes():
    assert main(["--color"])["color"] is True

def test_documented_default_is_authoritative():
    assert main([])["limit"] == 20

def test_readme_examples_match_supported_flags():
    text = Path("README.md").read_text()
    assert "--colour" not in text
    assert "--color" in text
''',
    },
    "long-context-compaction": {
        "pyproject.toml": _PYPROJECT,
        "fixture_app/__init__.py": "from .service import AccountService\n",
        "fixture_app/config.py": "DEFAULT_PAGE_SIZE = 0\n",
        "fixture_app/auth.py": "def normalize_role(value): return value\n",
        "fixture_app/validation.py": "def valid_email(value): return '@' in value\n",
        "fixture_app/storage.py": '''class Store:
    def __init__(self): self.rows = {}
    def put(self, key, value): self.rows[key] = value
    def get(self, key): return self.rows.get(key)
''',
        "fixture_app/audit.py": "def event(action, account): return {'action': action, 'account': account}\n",
        "fixture_app/api.py": "def response(data): return {'data': data}\n",
        "fixture_app/service.py": '''from .storage import Store

class AccountService:
    def __init__(self, store=None): self.store = store or Store()
    def create(self, account):
        self.store.put(account["id"], account)
        return account
    def list(self): return list(self.store.rows.values())
''',
        "REQUIREMENTS.md": '''# Twelve requirements

1. Normalize email whitespace and case. 2. Reject malformed email. 3. Normalize roles.
4. Reject unknown roles. 5. Preserve created_at on update. 6. Audit create/update.
7. Return defensive copies. 8. Sort accounts by id. 9. Default page size is 25.
10. Reject duplicate ids. 11. API responses include version 2. 12. Deletion is soft and hidden by default.
''',
        "tests/test_requirements.py": '''import pytest
from fixture_app.config import DEFAULT_PAGE_SIZE
from fixture_app.service import AccountService

def test_create_normalizes_validates_and_rejects_duplicates():
    service = AccountService()
    created = service.create({"id": "b", "email": " ADA@EXAMPLE.COM ", "role": "ADMIN"})
    assert created["email"] == "ada@example.com"
    assert created["role"] == "admin"
    with pytest.raises(ValueError, match="duplicate"):
        service.create({"id": "b", "email": "b@example.com", "role": "user"})
    with pytest.raises(ValueError, match="email"):
        service.create({"id": "c", "email": "bad", "role": "user"})

def test_listing_is_sorted_defensive_and_hides_soft_deleted():
    service = AccountService()
    service.create({"id":"b","email":"b@example.com","role":"user"})
    service.create({"id":"a","email":"a@example.com","role":"admin"})
    listed = service.list()
    assert [item["id"] for item in listed] == ["a", "b"]
    listed[0]["role"] = "changed"
    assert service.list()[0]["role"] == "admin"
    service.delete("a")
    assert [item["id"] for item in service.list()] == ["b"]

def test_cross_module_defaults_audit_and_api_contract():
    service = AccountService()
    service.create({"id":"a","email":"a@example.com","role":"user"})
    assert DEFAULT_PAGE_SIZE == 25
    assert service.events[-1]["action"] == "create"
    assert service.api_list()["version"] == 2
''',
    },
    "release-packaging": {
        "pyproject.toml": '''[project]\nname="release-fixture"\nversion="2.3.0"\nrequires-python=">=3.11"\n''',
        "fixture_app/__init__.py": "__version__ = '2.3.0'\n",
        "package.json": '''{"name":"release-fixture","version":"2.3.1","private":true,"main":"launcher.js","scripts":{"test":"node --test"}}\n''',
        "launcher.js": ''''use strict';
const VERSION = '2.3.2';
module.exports = { VERSION };
''',
        "Dockerfile": "FROM python:3.13-slim\nWORKDIR /app\nCOPY . .\nUSER root\n",
        "index.test.js": ''''use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const pkg = require('./package.json');
const { VERSION } = require('./launcher');
test('launcher and package versions match', () => assert.equal(VERSION, pkg.version));
test('npm metadata is publishable', () => assert.equal(pkg.private, false));
''',
        "tests/test_release.py": '''import re
from pathlib import Path
from fixture_app import __version__

def test_all_versions_are_2_3_4():
    package = Path("package.json").read_text()
    assert __version__ == "2.3.4"
    assert '"version":"2.3.4"' in package.replace(" ", "")

def test_image_is_non_root_and_installs_node_and_npm():
    dockerfile = Path("Dockerfile").read_text()
    assert re.search(r"apt-get install[^\\n]*nodejs[^\\n]*npm", dockerfile)
    assert re.search(r"USER\\s+(?!root)\\w+", dockerfile)
''',
        "README.md": "# Release fixture\n\nCurrent release: 2.3.0.\n",
    },
}


def _build_python_seed(target: Path, setup: str) -> None:
    package = target / "fixture_app"
    tests = target / "tests"
    package.mkdir(exist_ok=True)
    tests.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("from .core import process\n", encoding="utf-8")
    (package / "core.py").write_text(
        '"""Intentionally incomplete SDLC fixture."""\n\n'
        "def process(value):\n"
        "    return value\n",
        encoding="utf-8",
    )
    (tests / "test_core.py").write_text(
        "from fixture_app import process\n\n"
        "def test_baseline_contract():\n"
        f"    assert process({setup!r}) == {setup!r}\n",
        encoding="utf-8",
    )
    (target / "pyproject.toml").write_text(
        "[project]\nname = \"sdlc-fixture\"\nversion = \"0.1.0\"\nrequires-python = \">=3.11\"\n",
        encoding="utf-8",
    )


def _build_node_seed(target: Path, setup: str) -> None:
    (target / "index.js").write_text(
        "'use strict';\n\nfunction processValue(value) { return value; }\n"
        "module.exports = { processValue };\n",
        encoding="utf-8",
    )
    (target / "index.test.js").write_text(
        "'use strict';\nconst test = require('node:test');\nconst assert = require('node:assert/strict');\n"
        "const { processValue } = require('./index');\n"
        f"test('baseline contract', () => assert.equal(processValue({json.dumps(setup)}), {json.dumps(setup)}));\n",
        encoding="utf-8",
    )
    (target / "package.json").write_text(
        json.dumps(
            {
                "name": f"travis-eval-{setup}",
                "version": "0.1.0",
                "private": True,
                "scripts": {"test": "node --test"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = ["build_fixture"]
