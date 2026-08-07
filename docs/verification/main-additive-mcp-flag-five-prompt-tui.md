# Travis234 2.4.3 Additive MCP Flag Qualification

Date: 2026-08-08 (Asia/Yangon)

This record qualifies the bare `--mcp` CLI flag through exact locally built and installed release artifacts. GitOps and registry publication were intentionally excluded.

## Artifact and launch contract

- Travis234 wheel: `dist/travis234-2.4.3-py3-none-any.whl`
- MCP adapter wheel: `dist/mcp-adapter/travis234_mcp_adapter-0.1.1-py3-none-any.whl`
- Model: `openrouter/minimax/minimax-m3`, thinking `medium`
- Interface: one continuous attached TUI with `--mcp --approve --no-session`
- Environment: the repository `.env` was supplied through `--dotenv`; no values were copied into evidence or command output.
- Package state: an isolated temporary agent directory installed the exact adapter wheel through `travis234 install` and reported package version `0.1.1`.
- MCP configuration: existing shared configuration supplied the public Context7 and filesystem servers; the test did not rewrite configuration.

The launch used `--mcp` without `--tools`. Startup reached an idle TUI, proving the dedicated flag was accepted by the installed wheel.

## Five-prompt scenario

All five prompts completed in one continuous session.

| Prompt | Result | Independently verified evidence |
| --- | --- | --- |
| 1. Default repository read | Pass | Default `read` returned project `travis234` version `2.4.3`; response ended `ADDITIVE-MCP-1-PASS`. |
| 2. Context7 discovery | Pass | The `mcp` proxy searched for and described `resolve-library-id`, reporting required `libraryName` and `query` inputs; response ended `ADDITIVE-MCP-2-PASS`. |
| 3. Context7 live calls | Pass | `resolve-library-id` selected `/modelcontextprotocol/python-sdk`; `query-docs` returned the `stdio_client` signature and async transport purpose; response ended `ADDITIVE-MCP-3-PASS`. |
| 4. Filesystem MCP and core comparison | Pass | Filesystem MCP and default `read` read the same harmless temporary text file and returned matching content; response ended `ADDITIVE-MCP-4-PASS`. |
| 5. Same-session activation audit | Pass | Default `bash` printed `ADDITIVE-CORE-OK`, then `mcp {}` reported Context7 and filesystem connected; response ended `ADDITIVE-MCP-5-PASS`. |

The model made one malformed MCP describe call in prompt 2 (`describe: true`); the adapter returned a bounded error, and the same turn recovered through search and a valid describe call. In prompt 4, the model displayed the filesystem server's two configured temporary roots and listed temporary filenames despite being asked not to display absolute paths. It did not read or print credential contents. These were model instruction-following issues, not adapter or additive-selection failures.

## Trace audit and cleanup

- Secret-redacted event trace: `/tmp/travis234-243-additive-mcp-events.jsonl`
- Secret-redacted conversation log: `/tmp/travis234-243-additive-mcp-conversation.jsonl`
- Event trace: 41 records, including five `turn_start`, five successful `turn_end`, 20 `tool_end`, and one `shutdown`.
- Tool completions: 13 successful MCP, one bounded MCP error, four successful bash, and two successful read calls.
- Conversation log: exactly five successful records and all five unique `ADDITIVE-MCP-1-PASS` through `ADDITIVE-MCP-5-PASS` markers.
- `/exit` returned the TUI to the shell. The dedicated Orca terminal was then closed, and an independent process scan found no remaining Context7, filesystem MCP, Travis234, or isolated-adapter child process.

## Repository and artifact verification

- Focused CLI/session suites: 67 passed.
- Full root Python baseline after the behavioral change: 1911 passed in 135.97 seconds.
- Final full root Python suite after documentation, version, artifact, and TUI evidence changes: 1911 passed in 132.57 seconds.
- Full adapter suite: 54 passed in 17.81 seconds.
- npm launcher suite: 23 passed.
- Exact release wheels installed together in a clean Python 3.13 environment and reported Travis234 `2.4.3` plus adapter `0.1.1`; adapter import and installed CLI help passed.
- Root and adapter wheel/sdist builds passed; Twine accepted all four exact artifacts.
- npm pack dry run reported `@htooayelwinict/travis234@2.4.3` with the expected five files.
- A no-cache `Dockerfile.release` build produced `travis234:additive-mcp-243`; the repository container smoke exited zero, the image reported `2.4.3`, and its CLI help exposed additive `--mcp`.

No GitOps operation was performed.
