"""Stops a model that is stuck repeating itself. Core sets no max_tokens, so this is the only brake.

A request runs until the model stops or the context is full. A degenerate loop (the same block over and
over, common in long reasoning) would otherwise hold a slot for an hour or more. The guard watches the
streamed text and trips when the last `span` characters are one block repeated at least `repeats` times.
Paraphrased loops don't trip it; cancel those.
"""
from __future__ import annotations

SPAN = 4000        # chars of exact repetition at the end of the output before we stop
REPEATS = 4        # the repeated block must occur at least this many times in that span
CHECK_EVERY = 500  # chars between checks


class LoopGuard:
    def __init__(self, span: int = SPAN, repeats: int = REPEATS, check_every: int = CHECK_EVERY):
        self.span, self.repeats, self.check_every = span, repeats, check_every
        self.tail = ""
        self.since = 0
        self.block = 0   # the repeating block's length once tripped

    def feed(self, text: str) -> bool:
        """Add streamed text; True once the output is looping."""
        if not text or self.block:
            return bool(self.block)
        self.tail = (self.tail + text)[-self.span * 2:]
        self.since += len(text)
        if self.since < self.check_every or len(self.tail) < self.span:
            return False
        self.since = 0
        s = self.tail[-self.span:]
        for p in range(1, self.span // self.repeats + 1):
            if s[:-p] == s[p:]:      # s has period p: one block of p chars, repeated
                self.block = p
                return True
        return False

    def reason(self) -> str:
        return (f"stopped: the model was repeating itself (a {self.block}-character block, "
                f"{self.span // self.block}+ times in a row)")
