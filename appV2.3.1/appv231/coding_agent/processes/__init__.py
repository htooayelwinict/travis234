"""Managed process sessions for the coding-agent profile."""

from appv231.coding_agent.processes.completions import ProcessCompletionStore
from appv231.coding_agent.processes.local import create_local_process_transport
from appv231.coding_agent.processes.output import SanitizedOutputSpool
from appv231.coding_agent.processes.service import ProcessSessionService, ProcessTransportFactory
from appv231.coding_agent.processes.transport import ProcessTransport, SignalName
from appv231.coding_agent.processes.types import (
    InvalidCursorError,
    OutputSlice,
    ProcessClosedError,
    ProcessCompletionRecord,
    ProcessEvent,
    ProcessInputLimitError,
    ProcessLaunchRequest,
    ProcessLimitError,
    ProcessNotFoundError,
    ProcessOwner,
    ProcessSessionError,
    ProcessSnapshot,
    ProcessState,
    ProcessStateError,
    ProcessWaitCancelledError,
    StopCause,
)

__all__ = [
    "InvalidCursorError",
    "OutputSlice",
    "ProcessClosedError",
    "ProcessCompletionRecord",
    "ProcessCompletionStore",
    "ProcessEvent",
    "ProcessInputLimitError",
    "ProcessLaunchRequest",
    "ProcessLimitError",
    "ProcessNotFoundError",
    "ProcessOwner",
    "ProcessSessionError",
    "ProcessSessionService",
    "ProcessSnapshot",
    "ProcessState",
    "ProcessStateError",
    "ProcessWaitCancelledError",
    "ProcessTransport",
    "ProcessTransportFactory",
    "SanitizedOutputSpool",
    "SignalName",
    "StopCause",
    "create_local_process_transport",
]
