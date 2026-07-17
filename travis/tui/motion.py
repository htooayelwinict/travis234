"""Single-signal motion state for the interactive TUI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Protocol


class Cancellable(Protocol):
    def cancel(self) -> None: ...


class MotionState(str, Enum):
    IDLE = "idle"
    EXTENSION = "extension"
    WORKING = "working"
    TOOL = "tool"
    MAINTENANCE = "maintenance"
    RETRY = "retry"
    TERMINATING = "terminating"
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True)
class MotionProfile:
    frames: tuple[str, ...]
    interval: float
    repeat: bool = True
    static_frame: str = "·"


@dataclass(frozen=True)
class MotionSnapshot:
    state: MotionState
    indicator: str
    countdown: int | None
    generation: int


@dataclass(frozen=True)
class _SignalClaim:
    state: MotionState
    countdown: int | None
    sequence: int


_DIM = "\x1b[2m"
_NORMAL_INTENSITY = "\x1b[22m"
_THINKING_FRAMES = (
    f".{_DIM}..{_NORMAL_INTENSITY}",
    f"{_DIM}.{_NORMAL_INTENSITY}.{_DIM}.{_NORMAL_INTENSITY}",
    f"{_DIM}..{_NORMAL_INTENSITY}.",
)
_WORKING_PROFILE = MotionProfile(_THINKING_FRAMES, 0.25, static_frame="...")
_PROFILES = {
    MotionState.EXTENSION: _WORKING_PROFILE,
    MotionState.WORKING: _WORKING_PROFILE,
    MotionState.TOOL: MotionProfile((" ⠋", " ⠙", " ⠹", " ⠸"), 0.25, static_frame=" ◇"),
    MotionState.MAINTENANCE: MotionProfile((" ◇", " ◈", " ◆", " ◈"), 0.25, static_frame=" ◇"),
    MotionState.RETRY: MotionProfile((" !",), 1.0, static_frame=" !"),
    MotionState.TERMINATING: _WORKING_PROFILE,
    MotionState.SUCCESS: MotionProfile((" ·", " ✓"), 0.25, repeat=False, static_frame=" ✓"),
    MotionState.ERROR: MotionProfile((" ·", " !"), 0.25, repeat=False, static_frame=" !"),
}
_PRIORITIES = {
    MotionState.IDLE: 0,
    MotionState.EXTENSION: 10,
    MotionState.WORKING: 20,
    MotionState.TOOL: 30,
    MotionState.MAINTENANCE: 40,
    MotionState.SUCCESS: 50,
    MotionState.RETRY: 60,
    MotionState.TERMINATING: 70,
    MotionState.ERROR: 80,
}


class MotionController:
    """Resolve semantic activity into one bounded animated indicator."""

    def __init__(
        self,
        *,
        schedule: Callable[[float, Callable[[], None]], Cancellable],
        on_change: Callable[[MotionSnapshot], None],
        request_render: Callable[[], object],
        enabled: bool = True,
        static: bool = False,
    ) -> None:
        self._schedule = schedule
        self._on_change = on_change
        self._request_render = request_render
        self._enabled = bool(enabled)
        self._static = bool(static)
        self._signals: dict[str, _SignalClaim] = {}
        self._signal_sequence = 0
        self._active_source: str | None = None
        self._active_claim: _SignalClaim | None = None
        self._frame_index = 0
        self._cycle_generation = 0
        self._scheduled: Cancellable | None = None
        self._stopped = False
        self._snapshot = MotionSnapshot(MotionState.IDLE, "", None, 0)
        self._notify_change(request_render=False)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def state(self) -> MotionState:
        return self._snapshot.state

    @property
    def snapshot(self) -> MotionSnapshot:
        return self._snapshot

    def set_signal(
        self,
        source: str,
        state: MotionState,
        *,
        countdown: int | None = None,
    ) -> None:
        if self._stopped:
            return
        key = str(source).strip()
        if not key:
            raise ValueError("motion signal source must not be empty")
        resolved_state = MotionState(state)
        if resolved_state is MotionState.IDLE:
            self.clear_signal(key)
            return
        resolved_countdown = (
            max(0, int(countdown))
            if resolved_state is MotionState.RETRY and countdown is not None
            else None
        )
        existing = self._signals.get(key)
        if (
            existing is not None
            and existing.state is resolved_state
            and existing.countdown == resolved_countdown
        ):
            return
        self._signal_sequence += 1
        self._signals[key] = _SignalClaim(
            resolved_state,
            resolved_countdown,
            self._signal_sequence,
        )
        self._select_active_signal()

    def clear_signal(self, source: str) -> None:
        if self._stopped:
            return
        key = str(source).strip()
        if key not in self._signals:
            return
        del self._signals[key]
        self._select_active_signal()

    def set_enabled(self, enabled: bool) -> None:
        resolved = bool(enabled)
        if self._stopped or self._enabled == resolved:
            return
        self._enabled = resolved
        self._restart_active_signal()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._signals.clear()
        self._active_source = None
        self._active_claim = None
        self._cancel_scheduled()
        self._cycle_generation += 1
        self._emit(MotionState.IDLE, "", None)

    def _select_active_signal(self) -> None:
        winner = max(
            self._signals.items(),
            key=lambda item: (_PRIORITIES[item[1].state], item[1].sequence),
            default=None,
        )
        next_source, next_claim = winner if winner is not None else (None, None)
        if next_source == self._active_source and next_claim == self._active_claim:
            return
        self._active_source = next_source
        self._active_claim = next_claim
        self._restart_active_signal()

    def _restart_active_signal(self) -> None:
        self._cancel_scheduled()
        self._cycle_generation += 1
        self._frame_index = 0
        if self._active_claim is None:
            self._emit(MotionState.IDLE, "", None)
            return
        self._emit_active_frame()
        self._schedule_next_frame()

    def _emit_active_frame(self) -> None:
        claim = self._active_claim
        if claim is None:
            self._emit(MotionState.IDLE, "", None)
            return
        profile = _PROFILES[claim.state]
        if claim.state is MotionState.RETRY and claim.countdown is not None:
            indicator = f" {claim.countdown}s"
        elif not self._enabled or self._static:
            indicator = profile.static_frame
        else:
            indicator = profile.frames[self._frame_index]
        self._emit(claim.state, indicator, claim.countdown)

    def _emit(
        self,
        state: MotionState,
        indicator: str,
        countdown: int | None,
    ) -> None:
        candidate = MotionSnapshot(
            state,
            indicator,
            countdown,
            self._snapshot.generation + 1,
        )
        if (
            candidate.state is self._snapshot.state
            and candidate.indicator == self._snapshot.indicator
            and candidate.countdown == self._snapshot.countdown
        ):
            return
        self._snapshot = candidate
        self._notify_change(request_render=True)

    def _notify_change(self, *, request_render: bool) -> None:
        try:
            self._on_change(self._snapshot)
        except Exception:
            pass
        if not request_render:
            return
        try:
            self._request_render()
        except Exception:
            pass

    def _schedule_next_frame(self) -> None:
        claim = self._active_claim
        if claim is None or self._stopped or not self._enabled or self._static:
            return
        profile = _PROFILES[claim.state]
        if claim.state is MotionState.RETRY and claim.countdown is not None:
            if claim.countdown <= 0:
                return
        elif not profile.repeat and self._frame_index >= len(profile.frames) - 1:
            return
        cycle_generation = self._cycle_generation
        self._scheduled = self._schedule(
            profile.interval,
            lambda: self._advance_frame(cycle_generation),
        )

    def _advance_frame(self, cycle_generation: int) -> None:
        self._scheduled = None
        if self._stopped or cycle_generation != self._cycle_generation:
            return
        claim = self._active_claim
        if claim is None:
            return
        profile = _PROFILES[claim.state]
        if claim.state is MotionState.RETRY and claim.countdown is not None:
            claim = replace(claim, countdown=max(0, claim.countdown - 1))
            self._active_claim = claim
            if self._active_source is not None:
                self._signals[self._active_source] = claim
        elif profile.repeat:
            self._frame_index = (self._frame_index + 1) % len(profile.frames)
        else:
            self._frame_index = min(self._frame_index + 1, len(profile.frames) - 1)
        self._emit_active_frame()
        self._schedule_next_frame()

    def _cancel_scheduled(self) -> None:
        if self._scheduled is None:
            return
        self._scheduled.cancel()
        self._scheduled = None


__all__ = [
    "MotionController",
    "MotionProfile",
    "MotionSnapshot",
    "MotionState",
]
