"""Terminal image helpers ported from pi/packages/tui/src/terminal-image.ts."""

from __future__ import annotations

import base64
import math
import os
import random
import subprocess
from typing import Any, Callable, Literal, TypedDict

ImageProtocol = Literal["kitty", "iterm2"] | None


class TerminalCapabilities(TypedDict):
    images: ImageProtocol
    trueColor: bool
    hyperlinks: bool


class CellDimensions(TypedDict):
    widthPx: int
    heightPx: int


class ImageDimensions(TypedDict):
    widthPx: int
    heightPx: int


class ImageCellSize(TypedDict):
    columns: int
    rows: int


_cached_capabilities: TerminalCapabilities | None = None
_cell_dimensions: CellDimensions = {"widthPx": 9, "heightPx": 18}

_KITTY_PREFIX = "\x1b_G"
_ITERM2_PREFIX = "\x1b]1337;File="


def get_cell_dimensions() -> CellDimensions:
    return _cell_dimensions


def set_cell_dimensions(dims: CellDimensions) -> None:
    global _cell_dimensions
    _cell_dimensions = {"widthPx": int(dims["widthPx"]), "heightPx": int(dims["heightPx"])}


def _probe_tmux_hyperlinks() -> bool:
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{client_termfeatures}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=0.25,
        )
    except Exception:
        return False

    if result.returncode != 0:
        return False
    return "hyperlinks" in [feature.strip() for feature in result.stdout.split(",")]


def detect_capabilities(tmux_forwards_hyperlink: Callable[[], bool] | None = None) -> TerminalCapabilities:
    tmux_forwards_hyperlink = tmux_forwards_hyperlink or _probe_tmux_hyperlinks
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    terminal_emulator = os.environ.get("TERMINAL_EMULATOR", "").lower()
    term = os.environ.get("TERM", "").lower()
    color_term = os.environ.get("COLORTERM", "").lower()
    has_true_color_hint = color_term in {"truecolor", "24bit"}

    if os.environ.get("TMUX") or term.startswith("tmux"):
        return {"images": None, "trueColor": has_true_color_hint, "hyperlinks": bool(tmux_forwards_hyperlink())}

    if term.startswith("screen"):
        return {"images": None, "trueColor": has_true_color_hint, "hyperlinks": False}

    if os.environ.get("KITTY_WINDOW_ID") or term_program == "kitty":
        return {"images": "kitty", "trueColor": True, "hyperlinks": True}

    if term_program == "ghostty" or "ghostty" in term or os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return {"images": "kitty", "trueColor": True, "hyperlinks": True}

    if os.environ.get("WEZTERM_PANE") or term_program == "wezterm":
        return {"images": "kitty", "trueColor": True, "hyperlinks": True}

    if os.environ.get("ITERM_SESSION_ID") or term_program == "iterm.app":
        return {"images": "iterm2", "trueColor": True, "hyperlinks": True}

    if os.environ.get("WT_SESSION"):
        return {"images": None, "trueColor": True, "hyperlinks": True}

    if term_program == "vscode":
        return {"images": None, "trueColor": True, "hyperlinks": True}

    if term_program == "alacritty":
        return {"images": None, "trueColor": True, "hyperlinks": True}

    if terminal_emulator == "jetbrains-jediterm":
        return {"images": None, "trueColor": True, "hyperlinks": False}

    return {"images": None, "trueColor": has_true_color_hint, "hyperlinks": False}


def get_capabilities() -> TerminalCapabilities:
    global _cached_capabilities
    if _cached_capabilities is None:
        _cached_capabilities = detect_capabilities()
    return _cached_capabilities


def reset_capabilities_cache() -> None:
    global _cached_capabilities
    _cached_capabilities = None


def set_capabilities(caps: TerminalCapabilities) -> None:
    global _cached_capabilities
    _cached_capabilities = {
        "images": caps["images"],
        "trueColor": bool(caps["trueColor"]),
        "hyperlinks": bool(caps["hyperlinks"]),
    }


def is_image_line(line: str) -> bool:
    return (
        line.startswith(_KITTY_PREFIX)
        or line.startswith(_ITERM2_PREFIX)
        or _KITTY_PREFIX in line
        or _ITERM2_PREFIX in line
    )


def allocate_image_id() -> int:
    return random.randint(1, 0xFFFFFFFF)


def encode_kitty(base64_data: str, options: dict[str, Any] | None = None) -> str:
    options = options or {}
    chunk_size = 4096
    params = ["a=T", "f=100", "q=2"]

    if options.get("moveCursor") is False or options.get("move_cursor") is False:
        params.append("C=1")
    if options.get("columns"):
        params.append(f"c={options['columns']}")
    if options.get("rows"):
        params.append(f"r={options['rows']}")
    if options.get("imageId"):
        params.append(f"i={options['imageId']}")
    elif options.get("image_id"):
        params.append(f"i={options['image_id']}")

    if len(base64_data) <= chunk_size:
        return f"\x1b_G{','.join(params)};{base64_data}\x1b\\"

    chunks: list[str] = []
    offset = 0
    is_first = True
    while offset < len(base64_data):
        chunk = base64_data[offset : offset + chunk_size]
        is_last = offset + chunk_size >= len(base64_data)
        if is_first:
            chunks.append(f"\x1b_G{','.join(params)},m=1;{chunk}\x1b\\")
            is_first = False
        elif is_last:
            chunks.append(f"\x1b_Gm=0;{chunk}\x1b\\")
        else:
            chunks.append(f"\x1b_Gm=1;{chunk}\x1b\\")
        offset += chunk_size

    return "".join(chunks)


def delete_kitty_image(image_id: int) -> str:
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


def delete_all_kitty_images() -> str:
    return "\x1b_Ga=d,d=A,q=2\x1b\\"


def encode_iterm2(base64_data: str, options: dict[str, Any] | None = None) -> str:
    options = options or {}
    inline = 1 if options.get("inline") is not False else 0
    params = [f"inline={inline}"]

    if options.get("width") is not None:
        params.append(f"width={options['width']}")
    if options.get("height") is not None:
        params.append(f"height={options['height']}")
    if options.get("name"):
        name_base64 = base64.b64encode(str(options["name"]).encode("utf-8")).decode("ascii")
        params.append(f"name={name_base64}")
    if options.get("preserveAspectRatio") is False or options.get("preserve_aspect_ratio") is False:
        params.append("preserveAspectRatio=0")

    return f"\x1b]1337;File={';'.join(params)}:{base64_data}\x07"


def calculate_image_cell_size(
    image_dimensions: ImageDimensions,
    max_width_cells: int | float,
    max_height_cells: int | float | None = None,
    cell_dimensions: CellDimensions | None = None,
) -> ImageCellSize:
    cell_dimensions = cell_dimensions or {"widthPx": 9, "heightPx": 18}
    max_width = max(1, math.floor(max_width_cells))
    max_height = None if max_height_cells is None else max(1, math.floor(max_height_cells))
    image_width = max(1, int(image_dimensions["widthPx"]))
    image_height = max(1, int(image_dimensions["heightPx"]))

    width_scale = (max_width * int(cell_dimensions["widthPx"])) / image_width
    height_scale = (
        width_scale
        if max_height is None
        else (max_height * int(cell_dimensions["heightPx"])) / image_height
    )
    scale = min(width_scale, height_scale)

    scaled_width_px = image_width * scale
    scaled_height_px = image_height * scale
    columns = math.ceil(scaled_width_px / int(cell_dimensions["widthPx"]))
    rows = math.ceil(scaled_height_px / int(cell_dimensions["heightPx"]))

    return {
        "columns": max(1, min(max_width, columns)),
        "rows": max(1, rows if max_height is None else min(max_height, rows)),
    }


def calculate_image_rows(
    image_dimensions: ImageDimensions,
    target_width_cells: int | float,
    cell_dimensions: CellDimensions | None = None,
) -> int:
    return calculate_image_cell_size(image_dimensions, target_width_cells, None, cell_dimensions)["rows"]


def _decode_base64(base64_data: str) -> bytes | None:
    try:
        return base64.b64decode(base64_data)
    except Exception:
        return None


def get_png_dimensions(base64_data: str) -> ImageDimensions | None:
    buffer = _decode_base64(base64_data)
    if buffer is None or len(buffer) < 24:
        return None
    if buffer[0:4] != b"\x89PNG":
        return None
    return {
        "widthPx": int.from_bytes(buffer[16:20], "big"),
        "heightPx": int.from_bytes(buffer[20:24], "big"),
    }


def get_jpeg_dimensions(base64_data: str) -> ImageDimensions | None:
    buffer = _decode_base64(base64_data)
    if buffer is None or len(buffer) < 2:
        return None
    if buffer[0:2] != b"\xff\xd8":
        return None

    offset = 2
    while offset < len(buffer) - 9:
        if buffer[offset] != 0xFF:
            offset += 1
            continue

        marker = buffer[offset + 1]
        if 0xC0 <= marker <= 0xC2:
            height = int.from_bytes(buffer[offset + 5 : offset + 7], "big")
            width = int.from_bytes(buffer[offset + 7 : offset + 9], "big")
            return {"widthPx": width, "heightPx": height}

        if offset + 3 >= len(buffer):
            return None
        length = int.from_bytes(buffer[offset + 2 : offset + 4], "big")
        if length < 2:
            return None
        offset += 2 + length

    return None


def get_gif_dimensions(base64_data: str) -> ImageDimensions | None:
    buffer = _decode_base64(base64_data)
    if buffer is None or len(buffer) < 10:
        return None
    signature = buffer[:6].decode("ascii", errors="ignore")
    if signature not in {"GIF87a", "GIF89a"}:
        return None
    return {
        "widthPx": int.from_bytes(buffer[6:8], "little"),
        "heightPx": int.from_bytes(buffer[8:10], "little"),
    }


def get_webp_dimensions(base64_data: str) -> ImageDimensions | None:
    buffer = _decode_base64(base64_data)
    if buffer is None or len(buffer) < 30:
        return None
    if buffer[0:4].decode("ascii", errors="ignore") != "RIFF":
        return None
    if buffer[8:12].decode("ascii", errors="ignore") != "WEBP":
        return None

    chunk = buffer[12:16].decode("ascii", errors="ignore")
    if chunk == "VP8 ":
        if len(buffer) < 30:
            return None
        width = int.from_bytes(buffer[26:28], "little") & 0x3FFF
        height = int.from_bytes(buffer[28:30], "little") & 0x3FFF
        return {"widthPx": width, "heightPx": height}
    if chunk == "VP8L":
        if len(buffer) < 25:
            return None
        bits = int.from_bytes(buffer[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return {"widthPx": width, "heightPx": height}
    if chunk == "VP8X":
        if len(buffer) < 30:
            return None
        width = (buffer[24] | (buffer[25] << 8) | (buffer[26] << 16)) + 1
        height = (buffer[27] | (buffer[28] << 8) | (buffer[29] << 16)) + 1
        return {"widthPx": width, "heightPx": height}

    return None


def get_image_dimensions(base64_data: str, mime_type: str) -> ImageDimensions | None:
    if mime_type == "image/png":
        return get_png_dimensions(base64_data)
    if mime_type == "image/jpeg":
        return get_jpeg_dimensions(base64_data)
    if mime_type == "image/gif":
        return get_gif_dimensions(base64_data)
    if mime_type == "image/webp":
        return get_webp_dimensions(base64_data)
    return None


def render_image(
    base64_data: str,
    image_dimensions: ImageDimensions,
    options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    options = options or {}
    caps = get_capabilities()
    if not caps["images"]:
        return None

    max_width = options.get("maxWidthCells", options.get("max_width_cells", 80))
    size = calculate_image_cell_size(
        image_dimensions,
        max_width,
        options.get("maxHeightCells", options.get("max_height_cells")),
        get_cell_dimensions(),
    )

    image_id = options.get("imageId", options.get("image_id"))
    if caps["images"] == "kitty":
        sequence = encode_kitty(
            base64_data,
            {
                "columns": size["columns"],
                "rows": size["rows"],
                "imageId": image_id,
                "moveCursor": options.get("moveCursor", options.get("move_cursor")),
            },
        )
        result: dict[str, Any] = {"sequence": sequence, "rows": size["rows"]}
        if image_id is not None:
            result["imageId"] = image_id
        return result

    if caps["images"] == "iterm2":
        sequence = encode_iterm2(
            base64_data,
            {
                "width": size["columns"],
                "height": "auto",
                "preserveAspectRatio": options.get(
                    "preserveAspectRatio",
                    options.get("preserve_aspect_ratio", True),
                ),
            },
        )
        return {"sequence": sequence, "rows": size["rows"]}

    return None


def hyperlink(text: str, url: str) -> str:
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def image_fallback(mime_type: str, dimensions: ImageDimensions | None = None, filename: str | None = None) -> str:
    parts: list[str] = []
    if filename:
        parts.append(filename)
    parts.append(f"[{mime_type}]")
    if dimensions:
        parts.append(f"{dimensions['widthPx']}x{dimensions['heightPx']}")
    return f"[Image: {' '.join(parts)}]"


getCellDimensions = get_cell_dimensions
setCellDimensions = set_cell_dimensions
detectCapabilities = detect_capabilities
getCapabilities = get_capabilities
resetCapabilitiesCache = reset_capabilities_cache
setCapabilities = set_capabilities
isImageLine = is_image_line
allocateImageId = allocate_image_id
encodeKitty = encode_kitty
deleteKittyImage = delete_kitty_image
deleteAllKittyImages = delete_all_kitty_images
encodeITerm2 = encode_iterm2
calculateImageCellSize = calculate_image_cell_size
calculateImageRows = calculate_image_rows
getPngDimensions = get_png_dimensions
getJpegDimensions = get_jpeg_dimensions
getGifDimensions = get_gif_dimensions
getWebpDimensions = get_webp_dimensions
getImageDimensions = get_image_dimensions
renderImage = render_image
imageFallback = image_fallback
