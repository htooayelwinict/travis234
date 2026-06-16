from __future__ import annotations

import json

from appv22_ui.events import events_from_result, result_summary


class JsonRenderer:
    def render(self, result: dict | None) -> str:
        payload = {
            "summary": result_summary(result),
            "assistant_message": result.get("assistant_message", "") if isinstance(result, dict) else "",
            "events": [event.to_dict() for event in events_from_result(result)],
        }
        return json.dumps(payload, indent=2, sort_keys=True, default=str)
