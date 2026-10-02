"""Operator answer queues with prompt-generation matching (gateway scan driver).

The driver shows each operator prompt under a monotonically increasing
generation and waits for an answer tagged with that generation, so a late
answer for a timed-out prompt can never satisfy the next wait. All
cross-module coordination goes through the public surface of
:class:`OperatorAnswerQueue` — no caller outside this module touches queue
internals (``_items``/``_generation``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


@dataclass
class OperatorAnswerQueue(Generic[_T]):  # noqa: UP046 -- mypy requires Generic binding
    """Operator answer queue tagged with monotonic prompt generations.

    The driver calls :meth:`begin_prompt` before showing each operator
    prompt, then waits with :meth:`next_answer`. Answers queued under an
    older generation are ignored so a late approval for a timed-out prompt
    cannot satisfy the next one, while answers for the current prompt are
    preserved even if they arrive before the wait begins.
    """

    _items: asyncio.Queue[tuple[int, _T]] = field(default_factory=asyncio.Queue)
    _generation: int = 0

    async def put(self, value: _T, *, for_generation: int | None = None) -> None:
        """Queue an answer for a specific prompt generation.

        Args:
            value: Operator answer payload.
            for_generation: Prompt generation this answer belongs to. Defaults
                to the queue's current generation when omitted.
        """
        generation = self._generation if for_generation is None else for_generation
        await self._items.put((generation, value))

    def put_nowait(self, value: _T, *, for_generation: int | None = None) -> None:
        """Queue an answer without blocking.

        Args:
            value: Operator answer payload.
            for_generation: Prompt generation this answer belongs to. Defaults
                to the queue's current generation when omitted.
        """
        generation = self._generation if for_generation is None else for_generation
        self._items.put_nowait((generation, value))

    @property
    def current_generation(self) -> int:
        """Return the active prompt generation (0 before any prompt)."""
        return self._generation

    def begin_prompt(self) -> int:
        """Claim the next prompt generation.

        Returns:
            The new active prompt generation number.
        """
        self._generation += 1
        return self._generation

    def drain_through(self, max_generation: int) -> int:
        """Discard queued answers through *max_generation* (inclusive).

        Args:
            max_generation: Drop answers tagged at or below this generation.

        Returns:
            Number of discarded queue items.
        """
        drained = 0
        retained: list[tuple[int, _T]] = []
        while True:
            try:
                item = self._items.get_nowait()
            except asyncio.QueueEmpty:
                break
            generation, _value = item
            if generation <= max_generation:
                self._items.task_done()
                drained += 1
            else:
                retained.append(item)
        for item in retained:
            self._items.task_done()
            self._items.put_nowait(item)
        return drained

    def empty(self) -> bool:
        """Return whether no answers are queued.

        Returns:
            True when the queue holds no answers.
        """
        return self._items.empty()

    async def next_answer(
        self,
        timeout_s: float,
        wait_name: str,
        timeout_note: str,
        *,
        expected_generation: int | None = None,
    ) -> _T | None:
        """Wait for one operator answer with generation matching and timeout fallback.

        Accepts only answers tagged for *expected_generation* (or the queue's
        current generation when omitted), and on timeout discards answers for
        the expired prompt so a late arrival cannot satisfy the next wait.

        Args:
            timeout_s: Seconds to wait before falling back.
            wait_name: Short prompt label for the timeout warning.
            timeout_note: Fallback description for the timeout warning
                (e.g. "auto-beginning").
            expected_generation: Generation claimed by :meth:`begin_prompt`
                before the prompt was shown. When set, must match the
                generation stored on the approval gate and used by
                ``for_generation`` enqueue.

        Returns:
            The operator's answer, or None on timeout (caller maps None to
            its no-queue default: auto-begin / allow-all / decline-all).
        """
        prompt_generation = expected_generation if expected_generation is not None else self._generation
        deadline = time.monotonic() + timeout_s
        # Newer-generation answers are stashed locally and requeued once on
        # exit so the wait blocks instead of busy-spinning on a requeued head.
        stashed: list[tuple[int, _T]] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                generation, value = await asyncio.wait_for(self._items.get(), timeout=remaining)
            except TimeoutError:
                break
            if generation == prompt_generation:
                self._items.task_done()
                for item in stashed:
                    self._items.put_nowait(item)
                return value
            if generation < prompt_generation:
                self._items.task_done()
                continue
            # Newer-generation answer: preserve for the next wait (see drain_through).
            self._items.task_done()
            stashed.append((generation, value))

        for item in stashed:
            self._items.put_nowait(item)
        logger.warning(
            "%s timed out after %ss; %s",
            wait_name,
            timeout_s,
            timeout_note,
        )
        self.drain_through(prompt_generation)
        return None


def _drain_queue[T](queue: asyncio.Queue[T]) -> int:
    """Discard all items from a plain asyncio queue.

    Args:
        queue: Queue to drain.

    Returns:
        Number of items discarded.
    """
    drained = 0
    while True:
        try:
            queue.get_nowait()
            queue.task_done()
            drained += 1
        except asyncio.QueueEmpty:
            return drained
