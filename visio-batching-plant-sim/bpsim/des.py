"""Tiny discrete-event simulation kernel (generator based, no dependencies)."""
from __future__ import annotations

import heapq
import itertools
from collections import deque


class Event:
    __slots__ = ("env", "done", "cbs")

    def __init__(self, env: "Env") -> None:
        self.env, self.done, self.cbs = env, False, []

    def succeed(self) -> None:
        if self.done:
            return
        self.done = True
        for cb in self.cbs:
            self.env.schedule(0.0, cb)
        self.cbs = []

    def then(self, cb) -> None:
        if self.done:
            self.env.schedule(0.0, cb)
        else:
            self.cbs.append(cb)


class Process:
    def __init__(self, env: "Env", gen) -> None:
        self.env, self.gen, self.finished = env, gen, Event(env)
        env.schedule(0.0, self._step)

    def _step(self) -> None:
        try:
            ev = next(self.gen)
        except StopIteration:
            self.finished.succeed()
            return
        ev.then(self._step)


class Env:
    def __init__(self) -> None:
        self.now = 0.0
        self._q: list = []
        self._n = itertools.count()
        self.last_event = 0.0

    def schedule(self, delay: float, fn) -> None:
        heapq.heappush(self._q, (self.now + delay, next(self._n), fn))

    def timeout(self, delay: float) -> Event:
        ev = Event(self)
        self.schedule(max(delay, 0.0), ev.succeed)
        return ev

    def event(self) -> Event:
        return Event(self)

    def process(self, gen) -> Process:
        return Process(self, gen)

    def run(self, until: float) -> None:
        while self._q and self._q[0][0] <= until:
            t, _, fn = heapq.heappop(self._q)
            self.now = self.last_event = t
            fn()
        self.now = until


class Resource:
    """Multi-unit resource that grants strictly in pre-registered token order.

    Registering the order in which requests will be made (batch by batch) guarantees a
    deadlock-free hold-and-wait behaviour for a plant whose flow graph is acyclic.
    """

    def __init__(self, env: Env, name: str, units: int) -> None:
        self.env, self.name, self.units = env, name, max(int(units), 1)
        self.in_use = 0
        self.expected: deque = deque()
        self.waiting: dict = {}
        self.started: dict = {}
        self.reset(0.0)

    def reset(self, now: float) -> None:
        self.active = 0.0        # time doing useful processing (flow / weigh / dump / mix)
        self.own = 0.0           # time needed at this equipment's own rate
        self.held = 0.0          # time seized (active + blocked)
        self.count = 0
        for k in self.started:
            self.started[k] = now

    def register(self, token) -> None:
        self.expected.append(token)

    def request(self, token) -> Event:
        ev = self.env.event()
        self.waiting[token] = ev
        self._grant()
        return ev

    def release(self, token) -> None:
        self.held += self.env.now - self.started.pop(token)
        self.in_use -= 1
        self._grant()

    def _grant(self) -> None:
        while self.in_use < self.units and self.expected and self.expected[0] in self.waiting:
            tok = self.expected.popleft()
            ev = self.waiting.pop(tok)
            self.in_use += 1
            self.started[tok] = self.env.now
            self.count += 1
            ev.succeed()

    def close(self, now: float) -> None:
        for k, t0 in self.started.items():
            self.held += now - t0
            self.started[k] = now


class Pool:
    """Plain FIFO counting semaphore (work-in-process limit)."""

    def __init__(self, env: Env, n: int) -> None:
        self.env, self.free, self.q = env, n, deque()

    def request(self) -> Event:
        ev = self.env.event()
        if self.free > 0:
            self.free -= 1
            ev.succeed()
        else:
            self.q.append(ev)
        return ev

    def release(self) -> None:
        if self.q:
            self.q.popleft().succeed()
        else:
            self.free += 1
