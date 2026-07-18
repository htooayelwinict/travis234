from __future__ import annotations

from tests._support_coding_agent import *  # noqa: F403


def test_agent_session_export_to_html_embeds_markdown_highlight_renderer(tmp_path: Path) -> None:
    session_path = tmp_path / "html-markdown.jsonl"
    export_path = tmp_path / "exports" / "markdown.html"

    register_api_provider(
        create_faux_provider(lambda m, c: text_response_events(m, "## Result\n\n```python\nprint('ok')\n```"))
    )
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("render markdown")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "marked v15.0.4" in html
    assert "Highlight.js v11.9.0" in html
    assert "marked.use({" in html
    assert "function safeMarkedParse(text)" in html
    assert "hljs.highlight(code, { language: lang }).value" in html
    assert "safeMarkedParse(messageText(message))" in html
    assert "markdown-content" in html

def test_agent_session_export_to_html_uses_travis234_visual_edge_styles(tmp_path: Path) -> None:
    session_path = tmp_path / "html-visual-edges.jsonl"
    export_path = tmp_path / "exports" / "visual-edges.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "## Result\n\n```python\nprint('ok')\n```")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("visual styles")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert ".sidebar-controls {" in html
    assert ".help-hint {" in html
    assert "flex: 1 1 240px;" in html
    assert ".info-value {" in html
    assert "color: var(--text);\n      flex: 1;" in html
    assert ".tool-params-hint {" in html
    assert ".tool-params-hint::after {" in html
    assert "content: '[click to show parameters]';" in html
    assert ".tool-item.params-expanded .tool-params-hint::after {" in html
    assert "content: '[hide parameters]';" in html
    assert ".system-prompt.provider-prompt {" in html
    assert ".system-prompt-note {" in html
    assert ".tree-node.in-path {" in html
    assert ".tree-node:not(.in-path) {" in html
    assert ".tree-custom-message {" in html
    assert ".footer {" in html
    assert "#messages {" in html
    assert "#sidebar, #sidebar-resizer, #sidebar-toggle { display: none !important; }" in html
    assert ".markdown-content h1," in html
    assert ".markdown-content blockquote {" in html
    assert ".markdown-content table {" in html
    assert ".hljs { background: transparent; color: var(--text); }" in html
    assert ".hljs-keyword, .hljs-selector-tag { color: var(--syntaxKeyword); }" in html

def test_agent_session_export_to_html_renders_discriminated_tool_parameter_variants(tmp_path: Path) -> None:
    session_path = tmp_path / "html-tool-variants.jsonl"
    export_path = tmp_path / "exports" / "tool-variants.html"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "function getToolParameterVariants(parameters)" in html
    assert "Array.isArray(parameters.oneOf)" in html
    assert "variant.title" in html
    assert "tool-param-variant-title" in html

def test_agent_session_export_to_html_wires_tree_search_and_filters(tmp_path: Path) -> None:
    session_path = tmp_path / "html-filter.jsonl"
    export_path = tmp_path / "exports" / "filter.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("searchable user message")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "let filterMode = 'default';" in html
    assert "let searchQuery = '';" in html
    assert "function hasTextContent(content)" in html
    assert "function getSearchableText(entry, label)" in html
    assert "function filterNodes(flatNodes, currentLeafId)" in html
    assert "case 'user-only':" in html
    assert "case 'no-tools':" in html
    assert "case 'labeled-only':" in html
    assert "function forceTreeRerender()" in html
    assert "const searchInput = document.getElementById('tree-search');" in html
    assert "searchInput.addEventListener('input'" in html
    assert "document.querySelectorAll('.filter-btn').forEach(btn =>" in html
    assert "filterMode = btn.dataset.filter;" in html
    assert "forceTreeRerender();" in html
    assert "`${filtered.length} / ${rows.length} entries`" in html

def test_agent_session_export_to_html_uses_travis234_tree_display_and_navigation(tmp_path: Path) -> None:
    session_path = tmp_path / "html-rich-tree.jsonl"
    export_path = tmp_path / "exports" / "rich-tree.html"

    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        UserMessage('<skill name="planner" location="local">\nPlan details\n</skill>\n\nBuild it')
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        AssistantMessage(
            content=[
                TextContent("I will inspect it."),
                ToolCall(id="tree-bash-call", name="bash", arguments={"command": "printf ok"}),
            ],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=Usage(),
            stop_reason="toolUse",
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="tree-bash-call",
            tool_name="bash",
            content=[TextContent("ok")],
            is_error=False,
        )
    )

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "const toolCallMap = new Map();" in html
    assert "toolCallMap.set(block.id, { name: block.name, arguments: block.arguments });" in html
    assert "function findNewestLeaf(nodeId)" in html
    assert "function buildTreePrefix(flatNode)" in html
    assert "function recalculateVisualStructure(filteredNodes, allFlatNodes)" in html
    assert "function formatToolCall(name, args)" in html
    assert "function truncate(s, maxLen = 100)" in html
    assert "function parseSkillBlock(text)" in html
    assert "function getTreeNodeDisplayHtml(entry, label)" in html
    assert "const skillBlock = parseSkillBlock(rawContent);" in html
    assert "tree-role-skill" in html
    assert "tree-role-tool" in html
    assert "tree-prefix" in html
    assert "tree-marker" in html
    assert "tree-content" in html
    assert "treeRendered = false;" in html
    assert "const leafId = findNewestLeaf(entry.id);" in html
    assert "navigateTo(leafId, 'target', entry.id);" in html
    assert "node.classList.toggle('in-path', isOnPath);" in html
    assert "marker.textContent = isOnPath ? '•' : ' ';" in html

def test_agent_session_export_to_html_wires_copy_links_and_deep_links(tmp_path: Path) -> None:
    session_path = tmp_path / "html-links.jsonl"
    export_path = tmp_path / "exports" / "links.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("linkable message")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "function buildShareUrl(entryId)" in html
    assert "document.querySelector('meta[name=\"travis-share-base-url\"]')" in html
    assert "params.set('leafId', currentLeafId);" in html
    assert "params.set('targetId', entryId);" in html
    assert "async function copyToClipboard(text, button)" in html
    assert "document.execCommand('copy')" in html
    assert "function renderCopyLinkButton(entryId)" in html
    assert "copy-link-btn" in html
    assert 'id="${entryDomId}"' in html
    assert "messagesEl.querySelectorAll('.copy-link-btn').forEach(btn =>" in html
    assert "const shareUrl = buildShareUrl(entryId);" in html
    assert "targetEl.scrollIntoView({ block: 'center' });" in html
    assert "targetEl.classList.add('highlight');" in html
    assert "navigateTo(leafId, 'target', urlTargetId);" in html

def test_agent_session_export_to_html_wires_header_stats_and_jsonl_download(tmp_path: Path) -> None:
    session_path = tmp_path / "html-header.jsonl"
    export_path = tmp_path / "exports" / "header.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("header stats")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "function formatTokens(count)" in html
    assert "function computeStats(entryList)" in html
    assert "const globalStats = computeStats(entries);" in html
    assert "function downloadSessionJson()" in html
    assert "new Blob([jsonlContent], { type: 'application/x-ndjson' })" in html
    assert "a.download = `${header?.id || 'session'}.jsonl`;" in html
    assert "download-json-btn" in html
    assert "data-action=\"toggle-thinking\"" in html
    assert "data-action=\"toggle-tools\"" in html
    assert "Tool Calls:" in html
    assert "Tokens:" in html
    assert "Cost:" in html

def test_agent_session_export_to_html_renders_image_blocks_with_modal_wiring(tmp_path: Path) -> None:
    session_path = tmp_path / "html-images.jsonl"
    export_path = tmp_path / "exports" / "images.html"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        UserMessage(
            [
                TextContent("inspect this"),
                ImageContent(data="aW1hZ2U=", mime_type="image/png"),
            ]
        )
    )

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert session_data["entries"][0]["message"]["content"][1] == {
        "type": "image",
        "data": "aW1hZ2U=",
        "mimeType": "image/png",
    }
    assert "function renderMessageImages(content)" in html
    assert "function openImageModal(src)" in html
    assert "function closeImageModal()" in html
    assert "class=\"message-images\"" in html
    assert "class=\"message-image\"" in html
    assert "data:${escapeHtml(img.mimeType || img.mime_type || 'image/png')};base64,${escapeHtml(img.data || '')}" in html
    assert "messagesEl.querySelectorAll('.message-image').forEach(img =>" in html
    assert "img.addEventListener('click', () => openImageModal(img.src));" in html
    assert "imageModal.addEventListener('click', closeImageModal);" in html

def test_agent_session_export_to_html_wires_sidebar_resize_and_keyboard_shortcuts(tmp_path: Path) -> None:
    session_path = tmp_path / "html-sidebar.jsonl"
    export_path = tmp_path / "exports" / "sidebar.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("sidebar controls")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "--sidebar-width: 400px;" in html
    assert "--sidebar-min-width: 240px;" in html
    assert "--sidebar-max-width: 840px;" in html
    assert "body.sidebar-resizing" in html
    assert "const sidebarResizer = document.getElementById('sidebar-resizer');" in html
    assert "const SIDEBAR_WIDTH_STORAGE_KEY = 'travis-share:v1:sidebar-width';" in html
    assert "function isMobileLayout()" in html
    assert "function getSidebarBounds()" in html
    assert "function clampSidebarWidth(width)" in html
    assert "function applySidebarWidth(width)" in html
    assert "function loadSidebarWidth()" in html
    assert "function saveSidebarWidth(width)" in html
    assert "function setupSidebarResize()" in html
    assert "sidebarResizer.addEventListener('pointerdown'" in html
    assert "window.addEventListener('pointermove', onPointerMove);" in html
    assert "sidebarResizer.addEventListener('dblclick'" in html
    assert "setupSidebarResize();" in html
    assert "const closeSidebar = () =>" in html
    assert "overlay.addEventListener('click', closeSidebar);" in html
    assert "document.addEventListener('keydown', (event) =>" in html
    assert "if (event.key === 'Escape')" in html
    assert "const key = event.key.toLowerCase();" in html
    assert "if (key === 't')" in html
    assert "toggleToolOutputs();" in html

def test_agent_session_export_to_html_renders_tool_calls_with_cached_navigation(tmp_path: Path) -> None:
    session_path = tmp_path / "html-tool-navigation.jsonl"
    export_path = tmp_path / "exports" / "tool-navigation.html"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        AssistantMessage(
            content=[
                TextContent("I will inspect it."),
                ToolCall(id="bash-call", name="bash", arguments={"command": "printf ok"}),
            ],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=Usage(),
            stop_reason="toolUse",
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="bash-call",
            tool_name="bash",
            content=[TextContent("ok")],
            is_error=False,
        )
    )

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert [entry["message"]["role"] for entry in session_data["entries"]] == ["assistant", "toolResult"]
    assert "function findToolResult(toolCallId)" in html
    assert "function formatExpandableOutput(text, maxLines, lang)" in html
    assert "function renderToolCall(call)" in html
    assert "const toolDomId = `tool-call-${escapeHtml(call.id)}`;" in html
    assert "class=\"tool-execution ${statusClass}\" id=\"${toolDomId}\"" in html
    assert "case 'bash':" in html
    assert "if (role === 'toolResult') return '';" in html
    assert "for (const block of message.content || [])" in html
    assert "html += renderToolCall(block);" in html
    assert "const entryCache = new Map();" in html
    assert "function getScrollTargetElementId(entryId)" in html
    assert "return `tool-call-${entry.message.toolCallId}`;" in html
    assert "function renderEntryToNode(entry)" in html
    assert "entryCache.set(entry.id, node.cloneNode(true));" in html
    assert "const fragment = document.createDocumentFragment();" in html
    assert "messagesEl.appendChild(fragment);" in html
    assert "document.getElementById(getScrollTargetElementId(scrollTargetId))" in html

def test_agent_session_export_to_html_renders_edit_ls_write_and_tool_images(tmp_path: Path) -> None:
    session_path = tmp_path / "html-rich-tools.jsonl"
    export_path = tmp_path / "exports" / "rich-tools.html"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        AssistantMessage(
            content=[
                ToolCall(id="read-image", name="read", arguments={"path": "image.png"}),
                ToolCall(id="write-long", name="write", arguments={"path": "notes.txt", "content": "\n".join(f"line {i}" for i in range(12))}),
                ToolCall(id="edit-diff", name="edit", arguments={"path": "notes.txt"}),
                ToolCall(id="ls-limit", name="ls", arguments={"path": ".", "limit": 3}),
            ],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=Usage(),
            stop_reason="toolUse",
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="read-image",
            tool_name="read",
            content=[
                TextContent("Read image file [image/png]"),
                ImageContent(data="cGl4ZWw=", mime_type="image/png"),
            ],
            is_error=False,
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="write-long",
            tool_name="write",
            content=[TextContent("wrote notes.txt")],
            is_error=False,
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="edit-diff",
            tool_name="edit",
            content=[TextContent("edited notes.txt")],
            is_error=False,
            details={"diff": "-old\n+new\n context"},
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="ls-limit",
            tool_name="ls",
            content=[TextContent("notes.txt\nimage.png")],
            is_error=False,
        )
    )

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert [entry["message"]["role"] for entry in session_data["entries"]] == [
        "assistant",
        "toolResult",
        "toolResult",
        "toolResult",
        "toolResult",
    ]
    assert session_data["entries"][1]["message"]["content"][1] == {
        "type": "image",
        "data": "cGl4ZWw=",
        "mimeType": "image/png",
    }
    assert ".tool-images" in html
    assert ".tool-image" in html
    assert "const getResultImages = () =>" in html
    assert "function renderResultImages()" in html
    assert "class=\"tool-image\"" in html
    assert "case 'write':" in html
    assert "if (lines.length > 10) html += ` <span class=\"line-count\">(${lines.length} lines)</span>`;" in html
    assert "case 'edit':" in html
    assert "result?.details?.diff" in html
    assert "html += '<div class=\"tool-diff\">';" in html
    assert "case 'ls':" in html
    assert "pathHtml += ` <span class=\"line-count\">(limit ${escapeHtml(String(limit))})</span>`;" in html

def test_agent_session_export_to_html_renders_travis234_transcript_entry_blocks(tmp_path: Path) -> None:
    session_path = tmp_path / "html-transcript-blocks.jsonl"
    export_path = tmp_path / "exports" / "transcript-blocks.html"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        UserMessage('<skill name="planner" location="local">\nPlan details\n</skill>\n\nBuild it')
    )
    first_entry_id = session.session_entries[0]["id"]
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        AssistantMessage(
            content=[TextContent("Working on it")],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=Usage(),
            stop_reason="error",
            error_message="boom",
        )
    )
    session._session_store.append_model_change("faux", "replacement")
    session._session_store.append_compaction("Older work summary", first_entry_id, 12345)
    session._session_store.branch_with_summary(first_entry_id, "Branch **summary**")
    session._session_store.append_custom_message_entry("notice", "Visible **hook**", True)
    session._session_store.append_custom_message_entry("hidden", "Hidden hook", False)

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert ".skill-user-entry:hover .copy-link-btn" in html
    assert ".skill-invocation" in html
    assert ".assistant-message" in html
    assert ".assistant-text" in html
    assert ".model-change" in html
    assert ".compaction-content" in html
    assert ".hook-message" in html
    assert ".branch-summary" in html
    assert "const skillBlock = parseSkillBlock(text);" in html
    assert "class=\"skill-user-entry\" id=\"${entryDomId}\"" in html
    assert "class=\"skill-invocation-label\">[skill] ${escapeHtml(skillBlock.name)}</div>" in html
    assert "class=\"assistant-text markdown-content\"" in html
    assert "if (message.stopReason === 'aborted')" in html
    assert "Error: ${escapeHtml(message.errorMessage || 'Unknown error')}" in html
    assert "entry.type === 'model_change'" in html
    assert "Switched to model:" in html
    assert "Compacted from ${entry.tokensBefore.toLocaleString()} tokens" in html
    assert "class=\"branch-summary-header\">Branch Summary</div>" in html
    assert "entry.type === 'custom_message' && entry.display" in html
    assert "[${escapeHtml(entry.customType)}]" in html

def test_agent_session_export_to_html_prerenders_custom_tools_only(tmp_path: Path) -> None:
    session_path = tmp_path / "html-tools.jsonl"
    export_path = tmp_path / "exports" / "session-tools.html"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        AssistantMessage(
            content=[
                ToolCall(id="custom-call", name="custom_tool", arguments={"value": "<arg>"}),
                ToolCall(id="read-call", name="read", arguments={"path": "README.md"}),
            ],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=Usage(),
            stop_reason="toolUse",
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="custom-call",
            tool_name="custom_tool",
            content=[TextContent("custom result")],
            is_error=False,
            details={"rows": 2},
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="read-call",
            tool_name="read",
            content=[TextContent("read result")],
            is_error=False,
        )
    )

    class Renderer:
        def renderCall(self, tool_call_id, tool_name, args):
            return f"<div>{tool_call_id}:{tool_name}:{args['value']}</div>"

        def renderResult(self, tool_call_id, tool_name, result, details, is_error):
            return {
                "collapsed": f"<summary>{tool_call_id}:{tool_name}:{result[0]['text']}</summary>",
                "expanded": f"<section>{details['rows']}:{is_error}</section>",
            }

    returned_path = session.export_to_html({"outputPath": str(export_path), "toolRenderer": Renderer()})

    assert returned_path == str(export_path)
    html = export_path.read_text(encoding="utf-8")
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert session_data["renderedTools"] == {
        "custom-call": {
            "callHtml": "<div>custom-call:custom_tool:<arg></div>",
            "resultHtmlCollapsed": "<summary>custom-call:custom_tool:custom result</summary>",
            "resultHtmlExpanded": "<section>2:False</section>",
        }
    }

def test_agent_session_export_to_html_converts_custom_tool_ansi_components(tmp_path: Path) -> None:
    session_path = tmp_path / "html-custom-tool-ansi.jsonl"
    export_path = tmp_path / "exports" / "custom-tool-ansi.html"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        AssistantMessage(
            content=[ToolCall(id="ansi-call", name="ansi_tool", arguments={"value": "colored"})],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=Usage(),
            stop_reason="toolUse",
        )
    )
    session._session_store.append_message(  # noqa: SLF001 - builds a precise persisted transcript fixture.
        ToolResultMessage(
            tool_call_id="ansi-call",
            tool_name="ansi_tool",
            content=[TextContent("ansi result")],
            is_error=False,
        )
    )

    class Component:
        def __init__(self, lines: list[str]) -> None:
            self.lines = lines

        def render(self, width: int) -> list[str]:
            assert width == 100
            return self.lines

    class Renderer:
        def renderCall(self, tool_call_id, tool_name, args):
            return Component(["\x1b[31mcall <red>\x1b[0m", ""])

        def renderResult(self, tool_call_id, tool_name, result, details, is_error):
            return {
                "collapsed": Component(["\x1b[1;32mok\x1b[0m"]),
                "expanded": ["\x1b[4mexpanded\x1b[0m", "plain & <tag>"],
            }

    session.export_to_html({"outputPath": str(export_path), "toolRenderer": Renderer()})

    html = export_path.read_text(encoding="utf-8")
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert ".ansi-line {" in html
    assert session_data["renderedTools"] == {
        "ansi-call": {
            "callHtml": '<div class="ansi-line"><span style="color:#800000">call &lt;red&gt;</span></div><div class="ansi-line">&nbsp;</div>',
            "resultHtmlCollapsed": '<div class="ansi-line"><span style="color:#008000;font-weight:bold">ok</span></div>',
            "resultHtmlExpanded": '<div class="ansi-line"><span style="text-decoration:underline">expanded</span></div><div class="ansi-line">plain &amp; &lt;tag&gt;</div>',
        }
    }

def test_export_html_from_file_reads_arbitrary_session_jsonl_without_live_state(tmp_path: Path) -> None:
    from travis.coding_agent.export_html import export_from_file

    session_path = tmp_path / "standalone-source.jsonl"
    output_path = tmp_path / "exports" / "standalone.html"
    store = SessionStore(str(session_path), cwd=str(tmp_path))
    store.append_message(UserMessage("from file <only>"))

    returned_path = export_from_file(str(session_path), {"outputPath": str(output_path), "themeName": "dark"})

    assert returned_path == str(output_path)
    assert export_from_file(str(session_path), str(tmp_path / "exports" / "standalone-alias.html")).endswith(
        "standalone-alias.html"
    )
    html = output_path.read_text(encoding="utf-8")
    assert "from file &lt;only&gt;" not in html
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert session_data["header"]["id"] == store.header["id"]
    assert session_data["header"]["cwd"] == str(tmp_path)
    assert session_data["leafId"] == store.get_leaf_id()
    assert [entry["type"] for entry in session_data["entries"]] == ["message"]
    assert session_data["entries"][0]["message"]["content"] == "from file <only>"
    assert "systemPrompt" not in session_data
    assert "tools" not in session_data

    missing_path = tmp_path / "missing-session.jsonl"
    with pytest.raises(FileNotFoundError, match=str(missing_path)):
        export_from_file(str(missing_path), str(tmp_path / "exports" / "missing.html"))
    assert not missing_path.exists()

def test_agent_session_get_user_messages_for_forking_from_session_entries(tmp_path: Path) -> None:
    session_path = tmp_path / "fork-selector-session.jsonl"
    model = faux_model()
    responses = iter(["first reply", "second reply"])

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("first")
    session.prompt("second")

    user_entries = [
        entry
        for entry in session.session_entries
        if entry["type"] == "message" and entry["message"]["role"] == "user"
    ]

    result = session.get_user_messages_for_forking()

    assert result == [
        {"entryId": user_entries[0]["id"], "text": "first"},
        {"entryId": user_entries[1]["id"], "text": "second"},
    ]
    assert session.get_user_messages_for_forking() == result

def test_agent_session_get_last_assistant_text_skips_empty_aborted_message(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    assert session.get_last_assistant_text() is None

    session.agent.state.messages = [
        AssistantMessage(
            content=[TextContent(text=" first "), TextContent(text="reply ")],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=empty_usage(),
            stop_reason="stop",
        ),
        AssistantMessage(
            content=[],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=empty_usage(),
            stop_reason="aborted",
        ),
    ]

    assert session.get_last_assistant_text() == "first reply"

def test_agent_session_stats_and_context_usage_from_messages(tmp_path: Path) -> None:
    session_path = tmp_path / "stats-session.jsonl"
    model = faux_model()
    model.context_window = 1000
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    usage = Usage(input=100, output=20, cache_read=5, cache_write=2, total_tokens=140)
    usage.cost.total = 0.25
    session.agent.state.messages = [
        UserMessage(content="hello"),
        AssistantMessage(
            content=[TextContent(text="reply"), ToolCall(id="call_1", name="read", arguments={})],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=usage,
            stop_reason="toolUse",
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            tool_name="read",
            content=[TextContent(text="result")],
            is_error=False,
        ),
        UserMessage(content="follow-up " * 20),
    ]

    stats = session.get_session_stats()
    context_usage = session.get_context_usage()

    assert stats["sessionFile"] == str(session_path)
    assert stats["sessionId"] == session.session_id
    assert stats["userMessages"] == 2
    assert stats["assistantMessages"] == 1
    assert stats["toolCalls"] == 1
    assert stats["toolResults"] == 1
    assert stats["totalMessages"] == 4
    assert stats["tokens"] == {"input": 100, "output": 20, "cacheRead": 5, "cacheWrite": 2, "total": 127}
    assert stats["cost"] == 0.25
    assert context_usage is not None
    assert context_usage["tokens"] >= 140
    assert context_usage["contextWindow"] == 1000
    assert context_usage["percent"] == (context_usage["tokens"] / 1000) * 100
    assert context_usage["confidence"] == "estimated_trailing"
    assert stats["contextUsage"] == context_usage
    assert session.get_session_stats() == stats
    assert session.get_context_usage() == context_usage

def test_agent_session_context_usage_uses_rough_estimate_when_provider_usage_is_zero(tmp_path: Path) -> None:
    session_path = tmp_path / "stats-zero-usage-session.jsonl"
    model = faux_model()
    model.context_window = 1000
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.agent.state.messages = [
        UserMessage(content="before " * 80),
        AssistantMessage(
            content=[TextContent(text="reply " * 80)],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=Usage(input=0, output=0, cache_read=0, cache_write=0, total_tokens=0),
            stop_reason="stop",
        ),
    ]

    context_usage = session.get_context_usage()

    assert context_usage is not None
    assert context_usage["tokens"] > 0
    assert context_usage["contextWindow"] == 1000
    assert context_usage["percent"] == (context_usage["tokens"] / 1000) * 100
    assert context_usage["estimated"] is True
    assert context_usage["confidence"] == "estimated_full_request"

def test_agent_session_context_usage_estimated_after_compaction_until_post_compaction_assistant(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "stats-compaction-session.jsonl"
    model = faux_model()
    model.context_window = 1000
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.agent.state.messages = [UserMessage(content="before"), AssistantMessage(
        content=[TextContent(text="old reply")],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=Usage(input=900, output=80, cache_read=0, cache_write=0, total_tokens=980),
        stop_reason="stop",
    )]
    first_entry = session._session_store.append_message(session.agent.state.messages[0])
    session._session_store.append_message(session.agent.state.messages[1])
    session._session_store.append_compaction("summary", first_entry, 980)
    session.agent.state.messages = session._session_store.build_context(default_thinking_level="off").messages

    estimated_usage = session.get_context_usage()
    assert estimated_usage is not None
    assert estimated_usage["tokens"] > 0
    assert estimated_usage["contextWindow"] == 1000
    assert estimated_usage["percent"] == (estimated_usage["tokens"] / 1000) * 100
    assert estimated_usage["estimated"] is True
    assert estimated_usage["confidence"] == "estimated_after_compaction_full_request"
    assert estimated_usage["tokens"] == (
        estimated_usage["systemTokens"]
        + estimated_usage["toolTokens"]
        + estimated_usage["messageTokens"]
    )

    usage = Usage(input=20, output=5, cache_read=0, cache_write=0, total_tokens=25)
    post_compaction = AssistantMessage(
        content=[TextContent(text="post")],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=usage,
        stop_reason="stop",
    )
    session._session_store.append_message(post_compaction)
    session.agent.state.messages = session._session_store.build_context(default_thinking_level="off").messages

    context_usage = session.get_context_usage()

    assert context_usage is not None
    assert context_usage["tokens"] >= 20
    assert context_usage["contextWindow"] == 1000
    assert context_usage["percent"] == (context_usage["tokens"] / 1000) * 100
    assert context_usage.get("estimated") is not True
    assert context_usage["confidence"] == "provider_real"

def test_agent_session_navigate_tree_writes_extension_summary_and_label(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner, default_convert_to_llm

    session_path = tmp_path / "tree-session.jsonl"
    model = faux_model()
    responses = iter(["first reply", "second reply", "revised reply"])
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    runner = ExtensionRunner()
    before_events: list[dict] = []
    tree_events: list[dict] = []

    def before_tree(event: dict) -> dict:
        before_events.append(event)
        return {
            "summary": {
                "summary": "summary from old branch",
                "details": {"source": "extension"},
            },
            "label": "summary label",
        }

    runner.on("session_before_tree", before_tree)
    runner.on("session_tree", lambda event: tree_events.append(event))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        session_path=str(session_path),
        extension_runner=runner,
    )
    session.prompt("first")
    first_user_entry = next(
        entry
        for entry in session.session_entries
        if entry["type"] == "message"
        and entry["message"]["role"] == "user"
        and entry["message"]["content"] == _serialized_text_content("first")
    )
    session.prompt("second")
    old_leaf_id = session.session_entries[-1]["id"]

    result = session.navigate_tree(first_user_entry["id"], {"summarize": True, "label": "initial label"})

    assert result["cancelled"] is False
    assert result["editorText"] == "first"
    summary_entry = result["summaryEntry"]
    assert summary_entry["type"] == "branch_summary"
    assert summary_entry["parentId"] is None
    assert summary_entry["fromId"] == "root"
    assert summary_entry["summary"] == "summary from old branch"
    assert summary_entry["details"] == {"source": "extension"}
    assert summary_entry["fromHook"] is True
    assert before_events[0]["preparation"]["targetId"] == first_user_entry["id"]
    assert before_events[0]["preparation"]["oldLeafId"] == old_leaf_id
    assert before_events[0]["preparation"]["commonAncestorId"] == first_user_entry["id"]
    assert [entry["type"] for entry in before_events[0]["preparation"]["entriesToSummarize"]] == [
        "message",
        "message",
        "message",
    ]
    llm_messages = default_convert_to_llm(session.messages)
    assert len(llm_messages) == 1
    assert llm_messages[0].role == "user"
    assert llm_messages[0].content[0].text.startswith(
        "The following is a summary of a branch that this conversation came back from:"
    )
    assert "summary from old branch" in llm_messages[0].content[0].text

    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    label_entry = next(entry for entry in persisted if entry["type"] == "label")
    assert label_entry["targetId"] == summary_entry["id"]
    assert label_entry["label"] == "summary label"
    assert tree_events[-1]["newLeafId"] == label_entry["id"]
    assert tree_events[-1]["oldLeafId"] == old_leaf_id
    assert tree_events[-1]["fromExtension"] is True

    session.prompt("revised first")

    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    revised_user = next(
        entry for entry in persisted if entry.get("message", {}).get("content") == _serialized_text_content("revised first")
    )
    assert revised_user["parentId"] == label_entry["id"]

def test_agent_session_navigate_tree_user_message_without_summary_resets_to_parent(tmp_path: Path) -> None:
    session_path = tmp_path / "tree-edit-session.jsonl"
    model = faux_model()
    responses = iter(["first reply", "second reply", "rewritten reply"])
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("first")
    first_user_entry = next(
        entry
        for entry in session.session_entries
        if entry["type"] == "message"
        and entry["message"]["role"] == "user"
        and entry["message"]["content"] == _serialized_text_content("first")
    )
    session.prompt("second")

    result = session.navigate_tree(first_user_entry["id"])

    assert result == {"cancelled": False, "editorText": "first"}
    assert session.messages == []

    session.prompt("rewritten first")

    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    rewritten_user = next(
        entry
        for entry in persisted
        if entry.get("message", {}).get("content") == _serialized_text_content("rewritten first")
    )
    assert rewritten_user["parentId"] is None

def test_agent_session_navigate_tree_generates_default_branch_summary(tmp_path: Path) -> None:
    session_path = tmp_path / "tree-default-summary.jsonl"
    model = faux_model()
    model.context_window = 128000
    prompt_responses = iter(["first reply", "second reply"])
    summary_prompts: list[str] = []

    def provider(message, context):
        if context.system_prompt.startswith("You are a context summarization assistant."):
            prompt_text = context.messages[0].content[0].text
            summary_prompts.append(prompt_text)
            return text_response_events(message, "## Goal\nSummarized abandoned branch.")
        return text_response_events(message, next(prompt_responses))

    register_api_provider(create_faux_provider(provider))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("first")
    first_user_entry = next(
        entry
        for entry in session.session_entries
        if entry["type"] == "message"
        and entry["message"]["role"] == "user"
        and entry["message"]["content"] == _serialized_text_content("first")
    )
    session.prompt("second")

    result = session.navigate_tree(first_user_entry["id"], {"summarize": True})

    assert result["cancelled"] is False
    summary_entry = result["summaryEntry"]
    assert summary_entry["type"] == "branch_summary"
    assert summary_entry["summary"].startswith(
        "The user explored a different conversation branch before returning here."
    )
    assert "Summarized abandoned branch." in summary_entry["summary"]
    assert summary_entry["details"] == {"readFiles": [], "modifiedFiles": []}
    assert summary_entry.get("fromHook") is False
    assert summary_prompts
    assert summary_prompts[0].startswith("<conversation>")
    assert "[User]: second" in summary_prompts[0]
    assert "[Assistant]: second reply" in summary_prompts[0]
    assert "Create a structured summary of this conversation branch" in summary_prompts[0]

def test_agent_session_custom_entries_and_messages_persist_and_convert(tmp_path: Path) -> None:
    from travis.coding_agent import default_convert_to_llm

    session_path = tmp_path / "custom-session.jsonl"
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))

    custom_entry_id = session.append_custom_entry("preset-state", {"name": "plan"})
    session.send_custom_message(
        {"customType": "note", "content": "remember this", "display": True, "details": {"priority": 1}}
    )

    assert session.messages[-1].role == "custom"
    assert session.messages[-1].custom_type == "note"
    llm_messages = default_convert_to_llm(session.messages)
    assert llm_messages[-1].role == "user"
    assert llm_messages[-1].content[0].text == "remember this"

    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    custom_entry = next(entry for entry in persisted if entry["id"] == custom_entry_id)
    custom_message = next(entry for entry in persisted if entry["type"] == "custom_message")
    assert custom_entry["type"] == "custom"
    assert custom_entry["customType"] == "preset-state"
    assert custom_entry["data"] == {"name": "plan"}
    assert custom_message["parentId"] == custom_entry_id
    assert custom_message["customType"] == "note"
    assert custom_message["content"] == "remember this"
    assert custom_message["display"] is True
    assert custom_message["details"] == {"priority": 1}

    restored = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    assert restored.messages[-1].role == "custom"
    assert restored.messages[-1].custom_type == "note"
    assert restored.messages[-1].content == "remember this"

def test_agent_session_custom_message_next_turn_injects_context(tmp_path: Path) -> None:
    session_path = tmp_path / "custom-next-turn.jsonl"
    model = faux_model()
    seen_contexts: list[list[UserMessage]] = []

    def provider(message, context):
        seen_contexts.append([msg for msg in context.messages if isinstance(msg, UserMessage)])
        return text_response_events(message, "done")

    register_api_provider(create_faux_provider(provider))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.send_custom_message(
        {"customType": "carry", "content": "carry this", "display": False, "details": {}},
        {"deliverAs": "nextTurn"},
    )

    session.prompt("start")

    assert [_user_text(message) for message in seen_contexts[-1]] == ["start", "carry this"]
    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["type"] for entry in persisted[1:]] == ["message", "custom_message", "message"]

def test_agent_session_runtime_replaces_sessions_with_lifecycle_hooks(tmp_path: Path) -> None:
    from travis.coding_agent import AgentSessionRuntime, CreateAgentSessionRuntimeResult, ExtensionRunner

    model = faux_model()
    events: list[tuple[str, str, str | None]] = []
    session_counter = {"n": 0}

    def make_runner() -> ExtensionRunner:
        runner = ExtensionRunner()
        runner.on(
            "session_start",
            lambda event: events.append(("start", event["reason"], event.get("previousSessionFile"))),
        )
        runner.on(
            "session_shutdown",
            lambda event: events.append(("shutdown", event["reason"], event.get("targetSessionFile"))),
        )
        runner.on(
            "session_before_switch",
            lambda event: events.append(("before", event["reason"], event.get("targetSessionFile"))),
        )
        runner.on(
            "session_before_switch",
            lambda event: {"cancel": str(event.get("targetSessionFile") or "").endswith("cancel.jsonl")},
        )
        return runner

    def create_runtime(options: dict) -> CreateAgentSessionRuntimeResult:
        session_counter["n"] += 1
        session_path = options.get("session_path") or str(tmp_path / f"session-{session_counter['n']}.jsonl")
        session = AgentSession(
            cwd=options["cwd"],
            model=model,
            session_path=session_path,
            parent_session_path=options.get("parent_session_path"),
            extension_runner=make_runner(),
            session_start_event=options.get("session_start_event"),
            defer_session_start=bool(options.get("defer_session_start", False)),
        )
        return CreateAgentSessionRuntimeResult(
            session=session,
            services={"cwd": options["cwd"], "agentDir": str(tmp_path / ".travis234")},
            diagnostics=[],
        )

    initial = create_runtime(
        {
            "cwd": str(tmp_path),
            "session_path": str(tmp_path / "initial.jsonl"),
            "session_start_event": {"type": "session_start", "reason": "startup"},
        }
    ).session
    runtime = AgentSessionRuntime(
        initial,
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / ".travis234")},
        create_runtime,
    )
    rebinds: list[str | None] = []
    invalidations = {"n": 0}
    def rebind(session) -> None:
        rebinds.append(session.session_path)
        events.append(("rebind", "session", session.session_path))

    runtime.set_rebind_session(rebind)
    runtime.set_before_session_invalidate(lambda: invalidations.__setitem__("n", invalidations["n"] + 1))

    new_result = runtime.new_session()

    assert new_result == {"cancelled": False}
    assert runtime.session is not initial
    assert rebinds == [runtime.session.session_path]
    assert invalidations["n"] == 1
    assert ("before", "new", None) in events
    assert ("shutdown", "new", runtime.session.session_path) in events
    assert ("start", "new", str(tmp_path / "initial.jsonl")) in events
    assert events.index(("rebind", "session", runtime.session.session_path)) < events.index(
        ("start", "new", str(tmp_path / "initial.jsonl"))
    )

    active_session = runtime.session
    cancel_result = runtime.switch_session(str(tmp_path / "cancel.jsonl"))

    assert cancel_result == {"cancelled": True}
    assert runtime.session is active_session
    assert rebinds == [active_session.session_path]

    target = tmp_path / "resumed.jsonl"
    resume_result = runtime.switch_session(str(target))

    assert resume_result == {"cancelled": False}
    assert runtime.session.session_path == str(target)
    assert rebinds[-1] == str(target)
    assert ("before", "resume", str(target)) in events
    assert ("shutdown", "resume", str(target)) in events
    assert ("start", "resume", active_session.session_path) in events

    runtime.dispose()

    assert invalidations["n"] == 3
    assert ("shutdown", "quit", None) in events

def test_agent_session_runtime_fork_creates_branched_session_with_selected_text(tmp_path: Path) -> None:
    from travis.coding_agent import AgentSessionRuntime, CreateAgentSessionRuntimeResult, ExtensionRunner

    model = faux_model()
    responses = iter(["first reply", "second reply"])
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    events: list[tuple[str, str, str | None]] = []

    def make_runner() -> ExtensionRunner:
        runner = ExtensionRunner()
        runner.on(
            "session_start",
            lambda event: events.append(("start", event["reason"], event.get("previousSessionFile"))),
        )
        runner.on(
            "session_shutdown",
            lambda event: events.append(("shutdown", event["reason"], event.get("targetSessionFile"))),
        )
        runner.on(
            "session_before_fork",
            lambda event: events.append(("before_fork", event["position"], event["entryId"])),
        )
        runner.on("session_before_fork", lambda event: {"cancel": event["entryId"] == "cancel-me"})
        return runner

    def create_runtime(options: dict) -> CreateAgentSessionRuntimeResult:
        session = AgentSession(
            cwd=options["cwd"],
            model=model,
            session_path=options["session_path"],
            parent_session_path=options.get("parent_session_path"),
            extension_runner=make_runner(),
            session_start_event=options.get("session_start_event"),
            defer_session_start=bool(options.get("defer_session_start", False)),
        )
        return CreateAgentSessionRuntimeResult(
            session=session,
            services={"cwd": options["cwd"], "agentDir": str(tmp_path / ".travis234")},
            diagnostics=[],
        )

    initial_path = tmp_path / "fork-source.jsonl"
    initial = create_runtime(
        {
            "cwd": str(tmp_path),
            "session_path": str(initial_path),
            "session_start_event": {"type": "session_start", "reason": "startup"},
        }
    ).session
    initial.prompt("first")
    fork_user_entry = next(
        entry
        for entry in initial.session_entries
        if entry["type"] == "message"
        and entry["message"]["role"] == "user"
        and entry["message"]["content"] == _serialized_text_content("first")
    )
    first_assistant_entry = initial.session_entries[-1]
    initial.prompt("second")
    second_user_entry = next(
        entry
        for entry in initial.session_entries
        if entry["type"] == "message"
        and entry["message"]["role"] == "user"
        and entry["message"]["content"] == _serialized_text_content("second")
    )
    runtime = AgentSessionRuntime(
        initial,
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / ".travis234")},
        create_runtime,
    )

    cancel_result = runtime.fork("cancel-me")

    assert cancel_result == {"cancelled": True}
    assert runtime.session is initial

    fork_result = runtime.fork(second_user_entry["id"], {"position": "before"})

    assert fork_result == {"cancelled": False, "selectedText": "second"}
    assert runtime.session.session_path != str(initial_path)
    assert [_user_text(message) for message in runtime.session.messages if isinstance(message, UserMessage)] == ["first"]
    forked_lines = [json.loads(line) for line in Path(runtime.session.session_path).read_text(encoding="utf-8").splitlines()]
    assert forked_lines[0]["parentSession"] == str(initial_path)
    assert [entry["id"] for entry in forked_lines[1:]] == [fork_user_entry["id"], first_assistant_entry["id"]]
    assert ("before_fork", "before", second_user_entry["id"]) in events
    assert ("shutdown", "fork", runtime.session.session_path) in events
    assert ("start", "fork", str(initial_path)) in events

def test_agent_session_runtime_import_from_jsonl_copies_and_replaces_session(tmp_path: Path) -> None:
    from travis.coding_agent import (
        AgentSessionRuntime,
        CreateAgentSessionRuntimeResult,
        ExtensionRunner,
        SessionImportFileNotFoundError,
    )

    model = faux_model()
    responses = iter(["initial reply", "imported reply", "cancel reply"])
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    session_dir = tmp_path / "sessions"
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    events: list[tuple[str, str, str | None]] = []

    def make_runner() -> ExtensionRunner:
        runner = ExtensionRunner()
        runner.on(
            "session_start",
            lambda event: events.append(("start", event["reason"], event.get("previousSessionFile"))),
        )
        runner.on(
            "session_shutdown",
            lambda event: events.append(("shutdown", event["reason"], event.get("targetSessionFile"))),
        )
        runner.on(
            "session_before_switch",
            lambda event: events.append(("before", event["reason"], event.get("targetSessionFile"))),
        )
        runner.on(
            "session_before_switch",
            lambda event: {"cancel": str(event.get("targetSessionFile") or "").endswith("cancel.jsonl")},
        )
        return runner

    def create_runtime(options: dict) -> CreateAgentSessionRuntimeResult:
        session = AgentSession(
            cwd=options["cwd"],
            model=model,
            session_path=options["session_path"],
            extension_runner=make_runner(),
            session_start_event=options.get("session_start_event"),
            defer_session_start=bool(options.get("defer_session_start", False)),
        )
        return CreateAgentSessionRuntimeResult(
            session=session,
            services={"cwd": options["cwd"], "agentDir": str(tmp_path / ".travis234")},
            diagnostics=[],
        )

    initial_path = session_dir / "initial.jsonl"
    initial = create_runtime(
        {
            "cwd": str(tmp_path),
            "session_path": str(initial_path),
            "session_start_event": {"type": "session_start", "reason": "startup"},
        }
    ).session
    initial.prompt("initial")
    imported_path = external_dir / "imported.jsonl"
    imported = AgentSession(cwd=str(tmp_path), model=model, session_path=str(imported_path))
    imported.prompt("imported")
    cancel_path = external_dir / "cancel.jsonl"
    cancel_session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(cancel_path))
    cancel_session.prompt("cancel")
    runtime = AgentSessionRuntime(
        initial,
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / ".travis234")},
        create_runtime,
    )

    try:
        runtime.import_from_jsonl(str(external_dir / "missing.jsonl"))
        assert False, "expected missing import path to raise"
    except SessionImportFileNotFoundError as error:
        assert error.file_path == str((external_dir / "missing.jsonl").resolve())

    cancel_result = runtime.import_from_jsonl(str(cancel_path))

    assert cancel_result == {"cancelled": True}
    assert runtime.session is initial

    result = runtime.import_from_jsonl(str(imported_path))

    destination = session_dir / "imported.jsonl"
    assert result == {"cancelled": False}
    assert runtime.session.session_path == str(destination)
    assert destination.exists()
    assert [_user_text(message) for message in runtime.session.messages if isinstance(message, UserMessage)] == ["imported"]
    assert ("before", "resume", str(destination)) in events
    assert ("shutdown", "resume", str(destination)) in events
    assert ("start", "resume", str(initial_path)) in events

def test_coding_agent_package_exports_canonical_runtime_factory_surface(tmp_path: Path) -> None:
    from travis.coding_agent import (
        AgentSessionRuntime,
        AgentSessionRuntimeDiagnostic,
        CreateAgentSessionRuntimeResult,
        MissingSessionCwdError,
        SessionCwdIssue,
        create_agent_session_from_services,
        create_agent_session_runtime,
        create_agent_session_services,
        format_missing_session_cwd_prompt,
    )

    calls: list[dict[str, object]] = []

    def create_runtime(options: dict) -> CreateAgentSessionRuntimeResult:
        calls.append(dict(options))
        session = AgentSession(
            cwd=str(options["cwd"]),
            model=faux_model(),
            session_path=str(tmp_path / "runtime.jsonl"),
        )
        return CreateAgentSessionRuntimeResult(
            session=session,
            services={"cwd": str(options["cwd"]), "agentDir": str(options["agentDir"])},
            diagnostics=[{"type": "info", "message": "ok"}],
            model_fallback_message="fallback",
        )

    runtime = create_agent_session_runtime(
        create_runtime,
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / ".travis234"),
            "sessionManager": object(),
            "sessionStartEvent": {"type": "session_start", "reason": "startup"},
        },
    )

    assert isinstance(runtime, AgentSessionRuntime)
    assert runtime.session.cwd == str(tmp_path)
    assert runtime.services["agentDir"] == str(tmp_path / ".travis234")
    assert runtime.diagnostics == [{"type": "info", "message": "ok"}]
    assert runtime.model_fallback_message == "fallback"
    assert calls[0]["sessionManager"] is not None
    diagnostic: AgentSessionRuntimeDiagnostic = {"type": "info", "message": "ok"}
    assert diagnostic["type"] == "info"
    issue = SessionCwdIssue(
        session_cwd="/missing",
        fallback_cwd=str(tmp_path),
        session_file=str(tmp_path / "runtime.jsonl"),
    )
    assert MissingSessionCwdError(issue).issue is issue
    assert "continue in current cwd" in format_missing_session_cwd_prompt(issue)

def test_agent_session_runtime_rejects_missing_session_cwd_before_teardown(tmp_path: Path) -> None:
    from travis.coding_agent import AgentSessionRuntime, CreateAgentSessionRuntimeResult, MissingSessionCwdError

    model = faux_model()
    current_path = tmp_path / "current.jsonl"
    missing_cwd = tmp_path / "deleted"
    target_path = tmp_path / "target.jsonl"
    target_path.write_text(
        json.dumps({"type": "session", "id": "target", "cwd": str(missing_cwd)}) + "\n",
        encoding="utf-8",
    )

    def create_runtime(options: dict) -> CreateAgentSessionRuntimeResult:
        session = AgentSession(
            cwd=str(options["cwd"]),
            model=model,
            session_path=options["session_path"],
            session_start_event=options.get("session_start_event"),
            defer_session_start=bool(options.get("defer_session_start", False)),
        )
        return CreateAgentSessionRuntimeResult(
            session=session,
            services={"cwd": str(options["cwd"]), "agentDir": str(tmp_path / ".travis234")},
            diagnostics=[],
        )

    initial = AgentSession(cwd=str(tmp_path), model=model, session_path=str(current_path))
    runtime = AgentSessionRuntime(initial, {"cwd": str(tmp_path), "agentDir": str(tmp_path / ".travis234")}, create_runtime)

    try:
        runtime.switch_session(str(target_path))
        assert False, "expected missing session cwd to raise"
    except MissingSessionCwdError as error:
        assert error.issue.session_cwd == str(missing_cwd)
        assert error.issue.fallback_cwd == str(tmp_path)
        assert error.issue.session_file == str(target_path.resolve())
        assert "Stored session working directory does not exist" in str(error)

    assert runtime.session is initial

    result = runtime.switch_session(str(target_path), {"cwdOverride": str(tmp_path)})

    assert result == {"cancelled": False}
    assert runtime.session.cwd == str(tmp_path)

def test_tui_exports_travis234_parse_skill_block() -> None:
    from travis.tui import parse_skill_block


def test_coding_agent_package_exports_travis_tool_factory_surface(tmp_path: Path) -> None:
    from travis.coding_agent import (
        all_tool_names,
        create_all_tool_definitions_map,
        create_all_tools_map,
        create_bash_tool,
        create_bash_tool_definition,
        create_coding_tool_definitions,
        create_coding_tools,
        create_edit_tool,
        create_edit_tool_definition,
        create_find_tool,
        create_find_tool_definition,
        create_grep_tool,
        create_grep_tool_definition,
        create_ls_tool,
        create_ls_tool_definition,
        create_read_only_tool_definitions,
        create_read_only_tools,
        create_read_tool,
        create_read_tool_definition,
        create_tool,
        create_tool_definition,
        create_write_tool,
        create_write_tool_definition,
    )

    cwd = str(tmp_path)

    assert all_tool_names == {"read", "bash", "edit", "write", "grep", "find", "ls"}
    assert create_read_tool(cwd).name == "read"
    assert create_bash_tool(cwd).name == "bash"
    assert create_edit_tool(cwd).name == "edit"
    assert create_write_tool(cwd).name == "write"
    assert create_grep_tool(cwd).name == "grep"
    assert create_find_tool(cwd).name == "find"
    assert create_ls_tool(cwd).name == "ls"
    assert create_read_tool_definition(cwd).name == "read"
    assert create_bash_tool_definition(cwd).name == "bash"
    assert create_edit_tool_definition(cwd).name == "edit"
    assert create_write_tool_definition(cwd).name == "write"
    assert create_grep_tool_definition(cwd).name == "grep"
    assert create_find_tool_definition(cwd).name == "find"
    assert create_ls_tool_definition(cwd).name == "ls"
    assert create_tool("read", cwd).name == "read"
    assert create_tool_definition("bash", cwd).name == "bash"
    assert [tool.name for tool in create_coding_tools(cwd)] == ["read", "bash", "edit", "write"]
    assert [definition.name for definition in create_coding_tool_definitions(cwd)] == ["read", "bash", "edit", "write"]
    assert [tool.name for tool in create_read_only_tools(cwd)] == ["read", "grep", "find", "ls"]
    assert [definition.name for definition in create_read_only_tool_definitions(cwd)] == ["read", "grep", "find", "ls"]
    assert set(create_all_tools_map(cwd)) == all_tool_names
    assert set(create_all_tool_definitions_map(cwd)) == all_tool_names

def test_coding_agent_package_exports_travis_config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent_dir = tmp_path / "agent-dir"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    from travis.coding_agent import (
        APP_NAME,
        APP_TITLE,
        CONFIG_DIR_NAME,
        ENV_AGENT_DIR,
        PACKAGE_NAME,
        get_agent_dir,
        get_packaged_context_paths,
    )

    assert APP_NAME == "travis234"
    assert APP_TITLE == "Travis234"
    assert PACKAGE_NAME == "travis234"
    assert CONFIG_DIR_NAME == ".travis234"
    assert ENV_AGENT_DIR == "TRAVIS234_CODING_AGENT_DIR"
    assert get_agent_dir() == str(agent_dir)
    assert all(Path(path).exists() for path in get_packaged_context_paths())

def test_coding_agent_exports_travis234_event_bus_and_resource_loader_uses_it(tmp_path: Path) -> None:
    from travis.coding_agent import (
        DefaultResourceLoader,
        create_event_bus,
    )

    bus = create_event_bus()
    seen: list[object] = []
    unsubscribe = bus.on("resources", seen.append)

    bus.emit("resources", {"kind": "skill"})
    unsubscribe()
    bus.emit("resources", {"kind": "prompt"})

    assert seen == [{"kind": "skill"}]

    bus.clear()
    bus.emit("resources", {"kind": "theme"})
    assert seen == [{"kind": "skill"}]

    loader = DefaultResourceLoader(cwd=str(tmp_path), agent_dir=str(tmp_path / ".travis234"), event_bus=bus)

    assert loader.event_bus is bus

def test_coding_agent_exports_travis234_package_manager_and_skills_surface(tmp_path: Path) -> None:
    from travis.coding_agent import (
        DefaultPackageManager,
        ResolvedPaths,
        ResolvedResource,
        ResourceDiagnostic,
        Skill,
        format_skills_for_prompt,
        load_skills,
    )

    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: scan\n"
        "description: Inspect a codebase\n"
        "---\n"
        "# Scan\n"
        "Read files carefully.\n",
        encoding="utf-8",
    )
    manager = DefaultPackageManager(cwd=str(tmp_path), agent_dir=str(tmp_path / ".travis234"))
    resolved = manager.resolve()

    assert isinstance(resolved, ResolvedPaths)
    assert ResolvedResource(path=str(skill_dir), enabled=True, metadata={}).path == str(skill_dir)
    assert ResourceDiagnostic(type="warning", message="x", path=str(tmp_path)).type == "warning"

    loaded = load_skills([str(skill_dir)], cwd=str(tmp_path))
    assert len(loaded["skills"]) == 1
    assert isinstance(loaded["skills"][0], Skill)
    assert "scan" in format_skills_for_prompt(loaded["skills"])

def test_coding_agent_exports_travis234_low_level_tool_surface(tmp_path: Path) -> None:
    from travis.coding_agent import (
        DEFAULT_MAX_BYTES,
        DEFAULT_MAX_LINES,
        BashOperations,
        BashSpawnContext,
        TruncationResult,
        create_local_bash_operations,
        format_size,
        truncate_head,
        truncate_line,
        truncate_tail,
        with_file_mutation_queue,
    )
    from travis.coding_agent.tools import create_local_bash_operations as tools_create_local_bash_operations

    assert DEFAULT_MAX_LINES == 2000
    assert DEFAULT_MAX_BYTES == 50 * 1024
    assert tools_create_local_bash_operations is create_local_bash_operations
    assert format_size(1536) == "1.5KB"
    assert truncate_line("abcdef", 3) == ("abc... [truncated]", True)
    head = truncate_head("a\nb\nc", max_lines=2)
    tail = truncate_tail("a\nb\nc", max_lines=2)
    assert isinstance(head, TruncationResult)
    assert head.content == "a\nb"
    assert tail.content == "b\nc"
    assert BashSpawnContext(command="echo ok", cwd=str(tmp_path), env={}).command == "echo ok"
    assert isinstance(create_local_bash_operations(), BashOperations)

    calls: list[str] = []
    result = with_file_mutation_queue(str(tmp_path / "file.txt"), lambda: calls.append("ran") or "ok")

    assert result == "ok"
    assert calls == ["ran"]

def test_bash_shell_env_matches_travis234_without_runtime_python_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent.config import ENV_AGENT_DIR
    from travis.coding_agent.tools.bash import get_shell_env

    agent_dir = tmp_path / "agent"
    runtime_python_bin = str(Path(sys.executable).parent)
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PYTHONPATH", ".")

    env = get_shell_env()
    path_entries = env["PATH"].split(os.pathsep)

    assert path_entries[0] == str(agent_dir / "bin")
    assert runtime_python_bin not in path_entries
    assert "PYTHONPATH" not in env

def test_bash_shell_env_preserves_system_runtime_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent.config import ENV_AGENT_DIR
    from travis.coding_agent.tools.bash import get_shell_env

    runtime_python_bin = str(Path(sys.executable).resolve().parent)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    monkeypatch.setenv("PATH", os.pathsep.join([runtime_python_bin, "/usr/bin"]))

    env = get_shell_env()

    assert runtime_python_bin in env["PATH"].split(os.pathsep)

def test_bash_spawn_context_uses_travis234_shell_env_not_app_runtime_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent.config import ENV_AGENT_DIR
    from travis.coding_agent.tools.bash import _resolve_spawn_context

    agent_dir = tmp_path / "agent"
    runtime_python_bin = str(Path(sys.executable).parent)
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PYTHONPATH", ".")

    context = _resolve_spawn_context("python -m pytest", str(tmp_path))
    path_entries = context.env["PATH"].split(os.pathsep)

    assert path_entries[0] == str(agent_dir / "bin")
    assert runtime_python_bin not in path_entries
    assert "PYTHONPATH" not in context.env

def test_bash_shell_env_preserves_project_pythonpath_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent.config import ENV_AGENT_DIR
    from travis.coding_agent.tools.bash import get_shell_env

    project_src = tmp_path / "project" / "src"
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([".", str(project_src)]))

    env = get_shell_env()

    assert env["PYTHONPATH"] == str(project_src)

def test_bash_shell_env_provides_managed_python_shim_without_runtime_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from travis.coding_agent.config import ENV_AGENT_DIR
    from travis.coding_agent.tools.bash import get_shell_env

    agent_dir = tmp_path / "agent"
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    python3 = system_bin / "python3"
    python3.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python3.chmod(0o755)
    runtime_python_bin = str(Path(sys.executable).parent)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "runtime-venv"))
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    monkeypatch.setenv("PATH", os.pathsep.join([runtime_python_bin, str(system_bin)]))

    env = get_shell_env()
    path_entries = env["PATH"].split(os.pathsep)
    shim = agent_dir / "bin" / "python"

    assert path_entries[0] == str(agent_dir / "bin")
    assert runtime_python_bin not in path_entries
    assert shim.exists()
    assert str(python3) in shim.read_text(encoding="utf-8")

def test_default_system_prompt_does_not_force_verification_for_written_deliverables(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=["read", "write"],
            tool_snippets={"read": "Read file contents", "write": "Create or overwrite files"},
            prompt_guidelines=[
                "Use read to examine files instead of cat or sed.",
                "Use write only for new files or complete rewrites.",
            ],
        )
    )

    assert "# Finishing the job" not in prompt
    assert "backed by real tool output" not in prompt
    assert "summarize, report, review, document" not in prompt
    assert "Use write only for new files or complete rewrites." in prompt
