"""Append-only, hash-chained audit log (JSON Lines).

Each entry stores the hash of the previous entry, so editing, reordering or
removing an entry in the middle breaks the chain. Truncating the tail is only
detectable against a head hash recorded elsewhere (see `verify(expected_head=...)`).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .plan import canonical_json, sha256_hex

GENESIS = "0" * 64


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int
    problem: Optional[str] = None


class AuditLog:
    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self.path = path
        self._clock = clock
        self._seq = 0
        self._head = GENESIS
        if os.path.exists(path):
            entries = self.entries()
            if entries:
                self._seq = entries[-1]["seq"] + 1
                self._head = entries[-1]["hash"]

    @property
    def head(self) -> str:
        return self._head

    def append(self, event: str, run_id: Optional[str], data: Dict[str, Any]) -> Dict[str, Any]:
        body = {
            "seq": self._seq,
            "ts": round(self._clock(), 3),
            "run_id": run_id,
            "event": event,
            "data": data,
            "prev_hash": self._head,
        }
        entry = dict(body, hash=sha256_hex(canonical_json(body)))
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seq += 1
        self._head = entry["hash"]
        return entry

    def _lines(self) -> List[str]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [line for line in fh if line.strip()]

    def entries(self) -> List[Dict[str, Any]]:
        return [json.loads(line) for line in self._lines()]

    def verify(self, expected_head: Optional[str] = None) -> VerifyResult:
        prev = GENESIS
        count = 0
        for line in self._lines():
            try:
                entry = json.loads(line)
                claimed = entry.pop("hash")
                seq, prev_hash = entry["seq"], entry["prev_hash"]
            except (ValueError, KeyError, AttributeError):
                return VerifyResult(False, count, "entry %d is not a valid entry" % count)
            if seq != count:
                return VerifyResult(False, count, "entry %d has seq %s (missing or reordered)" % (count, seq))
            if prev_hash != prev:
                return VerifyResult(False, count, "entry %d does not chain to its predecessor" % count)
            if sha256_hex(canonical_json(entry)) != claimed:
                return VerifyResult(False, count, "entry %d content does not match its hash" % count)
            prev = claimed
            count += 1
        if expected_head is not None and prev != expected_head:
            return VerifyResult(False, count, "head hash differs from the expected head (truncated or replaced)")
        return VerifyResult(True, count)
