from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import travis.cli as cli
import travis.coding_agent.input_expansion as input_expansion
from tests._provider_runtime import register_api_provider, reset_api_providers, reset_models
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import ImageContent, TextContent, UserMessage
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.config import ENV_AGENT_DIR
from travis.coding_agent.input_expansion import InputExpansionError, expand_user_input


def setup_function() -> None:
    reset_api_providers()
    reset_models()


def _user_text(message: UserMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(block.text for block in message.content if isinstance(block, TextContent))


def test_expands_unquoted_and_quoted_files_but_preserves_escaped_at(
    tmp_path: Path,
) -> None:
    readme = tmp_path / "README.md"
    spaced = tmp_path / "path with spaces.txt"
    readme.write_text("alpha", encoding="utf-8")
    spaced.write_text("beta", encoding="utf-8")

    expanded = expand_user_input(
        r'Check @README.md then @"path with spaces.txt" and \@literal plus name@example.com',
        cwd=str(tmp_path),
        images=[],
    )

    assert f'<file name="{readme.resolve()}">\nalpha\n</file>' in expanded.text
    assert f'<file name="{spaced.resolve()}">\nbeta\n</file>' in expanded.text
    assert "@literal" in expanded.text
    assert "\\@literal" not in expanded.text
    assert "name@example.com" in expanded.text
    assert expanded.referenced_paths == (str(readme.resolve()), str(spaced.resolve()))
    assert expanded.content == (TextContent(text=expanded.text),)


def test_file_contents_are_not_scanned_recursively(tmp_path: Path) -> None:
    outer = tmp_path / "outer.txt"
    secret = tmp_path / "secret.txt"
    outer.write_text("literal @secret.txt", encoding="utf-8")
    secret.write_text("must not be included", encoding="utf-8")

    expanded = expand_user_input("Read @outer.txt", cwd=str(tmp_path), images=[])

    assert "literal @secret.txt" in expanded.text
    assert "must not be included" not in expanded.text
    assert expanded.referenced_paths == (str(outer.resolve()),)


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("@missing.txt", "does not exist"),
        ("@folder", "directory"),
        ("@binary.dat", "binary"),
        ("@../outside.txt", "outside the working directory"),
    ],
)
def test_rejects_invalid_relative_file_references(
    tmp_path: Path,
    reference: str,
    message: str,
) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "binary.dat").write_bytes(b"\x00\xff\x00")
    (tmp_path.parent / "outside.txt").write_text("outside", encoding="utf-8")

    with pytest.raises(InputExpansionError, match=message):
        expand_user_input(reference, cwd=str(tmp_path), images=[])


def test_operator_supplied_absolute_path_may_reference_outside_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content", encoding="utf-8")

    expanded = expand_user_input(f"Read @{outside}", cwd=str(project), images=[])

    assert "outside content" in expanded.text
    assert expanded.referenced_paths == (str(outside.resolve()),)


def test_text_inclusion_uses_read_tool_limits_and_reports_truncation(tmp_path: Path) -> None:
    large = tmp_path / "large.txt"
    large.write_text("\n".join(f"line {index}" for index in range(2_100)), encoding="utf-8")

    expanded = expand_user_input("@large.txt", cwd=str(tmp_path), images=[])

    assert "line 0" in expanded.text
    assert "line 2099" not in expanded.text
    assert "[Truncated:" in expanded.text
    assert "2000 of 2100 lines" in expanded.text


def test_inline_and_explicit_images_become_content_blocks(tmp_path: Path) -> None:
    inline = tmp_path / "inline.png"
    explicit = tmp_path / "explicit.gif"
    inline.write_bytes(b"\x89PNG\r\n\x1a\ninline")
    explicit.write_bytes(b"GIF89aexplicit")

    expanded = expand_user_input(
        "Inspect @inline.png",
        cwd=str(tmp_path),
        images=["explicit.gif"],
    )

    assert expanded.referenced_paths == (str(inline.resolve()), str(explicit.resolve()))
    assert isinstance(expanded.content[0], TextContent)
    image_blocks = [block for block in expanded.content if isinstance(block, ImageContent)]
    assert [(block.mime_type, base64.b64decode(block.data)) for block in image_blocks] == [
        ("image/png", inline.read_bytes()),
        ("image/gif", explicit.read_bytes()),
    ]
    assert f'<file name="{inline.resolve()}"></file>' in expanded.text
    assert f'<file name="{explicit.resolve()}"></file>' in expanded.text


def test_explicit_image_rejects_unsupported_and_oversized_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "image.bmp"
    oversized = tmp_path / "large.png"
    unsupported.write_bytes(b"BMpayload")
    oversized.write_bytes(b"\x89PNG\r\n\x1a\nlarge-payload")

    with pytest.raises(InputExpansionError, match="unsupported image"):
        expand_user_input("", cwd=str(tmp_path), images=[str(unsupported)])

    monkeypatch.setattr(input_expansion, "MAX_INLINE_IMAGE_BASE64_BYTES", 8)
    with pytest.raises(InputExpansionError, match="inline image size limit"):
        expand_user_input("", cwd=str(tmp_path), images=[str(oversized)])


def test_session_expands_file_and_image_before_provider_submission(tmp_path: Path) -> None:
    text_file = tmp_path / "notes.txt"
    image_file = tmp_path / "pixel.webp"
    text_file.write_text("operator notes", encoding="utf-8")
    image_file.write_bytes(b"RIFF\x04\x00\x00\x00WEBPdata")
    submitted: list[UserMessage] = []

    def provider(model, context):
        submitted.append(next(message for message in reversed(context.messages) if isinstance(message, UserMessage)))
        return text_response_events(model, "ok")

    model = replace(faux_model(), input=["text", "image"])
    register_api_provider(create_faux_provider(provider, models=[model]))
    session = AgentSession(cwd=str(tmp_path), model=model)
    try:
        session.prompt("Use @notes.txt and @pixel.webp")
    finally:
        session.shutdown()

    assert len(submitted) == 1
    assert "operator notes" in _user_text(submitted[0])
    assert any(isinstance(block, ImageContent) for block in submitted[0].content)


def test_session_rejects_image_when_model_lacks_image_capability_before_provider(
    tmp_path: Path,
) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\npixel")
    provider_calls = 0

    def provider(model, context):
        nonlocal provider_calls
        provider_calls += 1
        return text_response_events(model, "unexpected")

    model = faux_model()
    register_api_provider(create_faux_provider(provider))
    session = AgentSession(cwd=str(tmp_path), model=model)
    try:
        with pytest.raises(InputExpansionError, match="does not support image input"):
            session.prompt("Inspect @pixel.png")
    finally:
        session.shutdown()

    assert provider_calls == 0


def test_cli_image_argument_reaches_print_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\npixel")
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **_kwargs):
            self.session = SimpleNamespace(get_known_tool_names=lambda: [])

        def close(self) -> None:
            pass

    def fake_print(app, prompt, output, *, image_paths=()):
        captured.update(prompt=prompt, image_paths=list(image_paths))
        return 31

    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent"))
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "run_print_mode", fake_print)

    exit_code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--no-session",
            "--mode",
            "print",
            "--image",
            str(image),
            "inspect",
        ]
    )

    assert exit_code == 31
    assert captured == {"prompt": "inspect", "image_paths": [str(image.resolve())]}
