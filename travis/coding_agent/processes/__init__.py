"""Managed process sessions for the coding-agent profile."""

from travis.coding_agent.processes.completions import ProcessCompletionStore
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.output import SanitizedOutputSpool
from travis.coding_agent.processes.service import ProcessSessionService, ProcessTransportFactory
from travis.coding_agent.processes.transport import ProcessTransport, SignalName
from travis.coding_agent.processes.types import (
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
    ProcessOutputLimitError,
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
    "ProcessOutputLimitError",
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
