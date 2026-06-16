from __future__ import annotations

WORKSPACE_MANIFEST_SCHEMA = {
    "schema_id": "file_management.workspace_manifest",
    "type": "object",
    "required": ["generated_by", "moves", "held", "collisions"],
    "properties": {
        "generated_by": {"type": "string"},
        "moves": {"type": "array"},
        "held": {"type": "array"},
        "collisions": {"type": "array"},
    },
}

REPO_SNAPSHOT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed"]},
        "files": {"type": "array", "items": {"type": "string"}},
        "directories": {"type": "array", "items": {"type": "string"}},
        "text_previews": {"type": "object"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "files", "directories", "errors"],
}

READ_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "content"],
}
