import concurrent.futures
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from core import Router
from test_core import catalog, payload


class RouteConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "catalog.json").write_text(json.dumps(catalog()))
        (self.root / "config.json").write_text(json.dumps({"mode": "auto", "auto_policy": "baseline"}))

    def router(self, client):
        return Router(self.root / "config.json", self.root / "catalog.json",
                      self.root / "leases.json", self.root / "telemetry.jsonl", client)

    @staticmethod
    def answer(*_):
        return {"answers": {"capability": {"choice": "luna"}, "effort": {"choice": "low"}}}

    def test_slow_jev_in_one_chat_does_not_force_another_chat_to_sol(self):
        self.assert_parallel_chats(shared_router=False)

    def test_shared_router_does_not_serialize_independent_remote_calls(self):
        self.assert_parallel_chats(shared_router=True)

    def assert_parallel_chats(self, shared_router):
        entered = threading.Event()
        release = threading.Event()

        def delayed_jev(*args):
            if entered.is_set():
                return self.answer(*args)
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test coordination timeout")
            return self.answer(*args)

        first = self.router(delayed_jev)
        second = first if shared_router else self.router(self.answer)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(first.decide, payload("Change a button label"),
                                      session_id="independent-chat-a", native_selection=True)
            try:
                self.assertTrue(entered.wait(2), "first chat never reached routing boundary")
                result = second.decide(payload("Change another button label"),
                                       session_id="independent-chat-b", native_selection=True)
                self.assertEqual(result["reason"], "jev")
                self.assertEqual((result["model"], result["effort"]), ("gpt-6-luna", "low"))
                self.assertFalse(pending.done(), "test did not exercise overlapping decisions")
            finally:
                release.set()
                completed = pending.result(timeout=2)
        self.assertEqual(completed["reason"], "jev")
        self.assertEqual(len(json.loads((self.root / "leases.json").read_text())), 2)

    def test_newer_same_session_commit_is_not_overwritten_by_slow_decision(self):
        entered, release = threading.Event(), threading.Event()
        session = "legacy-same-chat"
        session_hash = hashlib.sha256(session.encode()).hexdigest()[:24]

        def delayed_jev(*args):
            entered.set()
            release.wait(5)
            return {**self.answer(*args), "usage": {"input_tokens": 17, "output_tokens": 3}}

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.router(delayed_jev).decide, payload("Change a button label"),
                                     session_id=session, native_selection=True)
            try:
                self.assertTrue(entered.wait(2))
                with (self.root / "leases.lock").open("a+") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        self.fail("global state lock is held across the remote request")
                    newer = {session_hash: {"model": "gpt-6-sol", "role": "sol", "effort": "high",
                                            "policy": "baseline", "turn_hash": "f" * 24,
                                            "turns": 2, "updated": time.time()}}
                    (self.root / "leases.json").write_text(json.dumps(newer))
            finally:
                release.set()
                result = future.result(timeout=2)
        self.assertEqual(result["reason"], "state_error")
        self.assertEqual(json.loads((self.root / "leases.json").read_text()), newer)
        self.assertEqual((result.get("jev_input_tokens"), result.get("jev_output_tokens")), (17, 3))

    def test_busy_same_session_falls_back_without_duplicate_remote_work_then_recovers(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def delayed_jev(*args):
            calls.append(1)
            entered.set()
            release.wait(5)
            return self.answer(*args)

        router = self.router(delayed_jev)
        request = payload("Change a button label")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(router.decide, request, session_id="same-chat", native_selection=True)
            try:
                self.assertTrue(entered.wait(2))
                busy = router.decide(request, session_id="same-chat", native_selection=True)
                self.assertEqual((busy["reason"], busy["model"]), ("state_error", "gpt-6-sol"))
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                initial = pending.result(timeout=2)
        following = router.decide(request, session_id="same-chat", native_selection=True)
        self.assertEqual(initial["reason"], "jev")
        self.assertEqual((following["reason"], following["model"]), ("lease", "gpt-6-luna"))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
