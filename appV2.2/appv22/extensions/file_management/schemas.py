from __future__ import annotations

REPO_SNAPSHOT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "root": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
        "directories": {"type": "array", "items": {"type": "string"}},
        "text_previews": {"type": "object"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "files", "directories", "errors"],
}

FIND_FILES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "root": {"type": "string"},
        "matches": {"type": "array", "items": {"type": "string"}},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "matches", "errors"],
}

SEARCH_TEXT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "root": {"type": "string"},
        "matches": {"type": "array", "items": {"type": "object"}},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "matches", "errors"],
}

READ_MANY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "bytes_read": {"type": "integer"},
                    "line_count": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                },
            },
        },
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "files", "errors"],
}

TREE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "root": {"type": "string"},
        "entries": {"type": "array", "items": {"type": "string"}},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "entries", "errors"],
}

GREP_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "root": {"type": "string"},
        "matches": {"type": "array", "items": {"type": "object"}},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "matches", "errors"],
}

READ_RANGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "content": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "content", "errors"],
}

READ_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "line_count": {"type": "integer"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "content"],
}

WRITE_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "bytes_written": {"type": "integer"},
        "overwritten": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "bytes_written"],
}

EDIT_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "edits_applied": {"type": "integer"},
        "bytes_written": {"type": "integer"},
        "first_changed_line": {"type": "integer"},
        "diff": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "edits_applied", "errors"],
}

MKDIR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "created": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "created"],
}

MOVE_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "source": {"type": "string"},
        "destination": {"type": "string"},
        "overwritten": {"type": "boolean"},
        "suggested_path": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "source", "destination", "overwritten"],
}

COPY_FILE_OUTPUT_SCHEMA = MOVE_FILE_OUTPUT_SCHEMA

DELETE_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "deleted": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "deleted"],
}
