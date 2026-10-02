import concurrent.futures
import fcntl
import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
import unittest

from core import Router
from test_core import catalog, payload


def _answer(*_):
    return {"answers": {"capability": {"choice": "luna"}, "effort": {"choice": "low"},
                        "work_shape": {"choice": "routine"}}}


def _hold_lock(path_text, acquired, release):
    """Spawn-safe lock holder used to avoid same-process flock semantics."""
    path = Path(path_text)
    with path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        acquired.set()
        release.wait(10)


def _process_decide(root_text, session_id, entered, release, results):
    root = Path(root_text)

    def delayed_jev(*_):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("process test coordination timed out")
        return _answer()

    router = Router(root / "config.json", root / "catalog.json", root / "leases.json",
                    root / "telemetry.jsonl", delayed_jev)
    try:
        results.put(router.decide(payload("Change a button label"), session_id=session_id,
                                  native_selection=True))
    except Exception as error:
        results.put({"process_error": repr(error)})


def _session_for_prefix(prefix, *, except_identity=None):
    for index in range(100_000):
        identity = "stripe-collision-" + str(index)
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        if digest[:2] == prefix and identity != except_identity:
            return identity, digest
    raise AssertionError("could not construct a stripe collision")


class StateLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "catalog.json").write_text(json.dumps(catalog()))
        (self.root / "config.json").write_text(json.dumps({"mode": "auto", "auto_policy": "baseline"}))

    def router(self, jev=_answer):
        return Router(self.root / "config.json", self.root / "catalog.json", self.root / "leases.json",
                      self.root / "telemetry.jsonl", jev)

    def _hold_in_process(self, path):
        context = multiprocessing.get_context("spawn")
        acquired, release = context.Event(), context.Event()
        holder = context.Process(target=_hold_lock, args=(str(path), acquired, release))
        holder.start()
        if not acquired.wait(5):
            release.set()
            holder.join(2)
            if holder.is_alive():
                holder.terminate()
                holder.join(2)
            holder.close()
            self.fail("lock holder never acquired its lock")

        def cleanup():
            release.set()
            holder.join(2)
            if holder.is_alive():
                holder.terminate()
                holder.join(2)
            holder.close()

        self.addCleanup(cleanup)
        return release, holder

    def test_same_session_overlap_calls_jev_once_then_uses_lease(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def delayed_jev(*args):
            calls.append(args)
            entered.set()
            self.assertTrue(release.wait(5), "first route was never released")
            return _answer()

        session = "one-chat-overlap"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.router(delayed_jev).decide, payload("Change a button label"),
                                    session_id=session, native_selection=True)
            self.assertTrue(entered.wait(2), "first route never reached Jev")
            following = executor.submit(self.router(delayed_jev).decide, payload("Change a button label"),
                                        session_id=session, native_selection=True)
            self.assertFalse(following.done(), "same-session route did not wait for its lease")
            release.set()
            first_result = first.result(timeout=2)
            following_result = following.result(timeout=2)

        self.assertEqual(len(calls), 1)
        self.assertEqual((first_result["model"], first_result["reason"]), ("gpt-6-luna", "jev"))
        self.assertEqual((following_result["model"], following_result["reason"]), ("gpt-6-luna", "lease"))
        self.assertEqual(len(json.loads((self.root / "leases.json").read_text())), 1)

    def test_stripe_collision_fails_open_within_bound_then_recovers(self):
        first_identity, first_hash = _session_for_prefix("00")
        second_identity, _ = _session_for_prefix(first_hash[:2], except_identity=first_identity)
        stripe = self.root / ("leases.route-" + first_hash[:2] + ".lock")
        release, holder = self._hold_in_process(stripe)
        started = time.monotonic()
        blocked = self.router().decide(payload("Change a button label"), session_id=second_identity,
                                       native_selection=True)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.5)
        self.assertEqual((blocked["model"], blocked["reason"]), ("gpt-6-sol", "state_error"))

        release.set()
        holder.join(2)
        self.assertFalse(holder.is_alive())
        recovered = self.router().decide(payload("Change a button label"), session_id=second_identity,
                                         native_selection=True)
        self.assertEqual((recovered["model"], recovered["reason"]), ("gpt-6-luna", "jev"))

    def test_global_lock_fails_open_within_bound_then_recovers(self):
        release, holder = self._hold_in_process(self.root / "leases.lock")
        started = time.monotonic()
        blocked = self.router().decide(payload("Change a button label"), session_id="global-lock-chat",
                                       native_selection=True)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.5)
        self.assertEqual((blocked["model"], blocked["reason"]), ("gpt-6-sol", "state_error"))

        release.set()
        holder.join(2)
        self.assertFalse(holder.is_alive())
        recovered = self.router().decide(payload("Change a button label"), session_id="global-lock-chat",
                                         native_selection=True)
        self.assertEqual((recovered["model"], recovered["reason"]), ("gpt-6-luna", "jev"))

    def test_policy_expiry_and_current_model_synthesis_do_not_trigger_false_cas(self):
        now = time.time()
        policy_session = "policy-changed"
        expired_session = "expired-lease"
        policy_hash = hashlib.sha256(policy_session.encode()).hexdigest()[:24]
        expired_hash = hashlib.sha256(expired_session.encode()).hexdigest()[:24]
        old_lease = {"model": "gpt-6-luna", "role": "luna", "effort": "low", "policy": "baseline",
                     "turn_hash": "a" * 24, "turns": 1, "updated": now}
        expired_lease = {**old_lease, "updated": now - 90_000}
        (self.root / "leases.json").write_text(json.dumps({policy_hash: old_lease}))
        (self.root / "config.json").write_text(json.dumps({"mode": "auto", "auto_policy": "completion_v3"}))

        changed = self.router().decide(payload("Implement a bounded form change"), session_id=policy_session,
                                       native_selection=True)
        leases_after_policy_change = json.loads((self.root / "leases.json").read_text())
        leases_after_policy_change[expired_hash] = expired_lease
        (self.root / "leases.json").write_text(json.dumps(leases_after_policy_change))
        expired = self.router().decide(payload("Implement another bounded form change"), session_id=expired_session,
                                       native_selection=True)
        synthesized = self.router().decide({**payload("Implement one more bounded form change"),
                                            "current_model": "gpt-6-terra"}, session_id="native-current-model",
                                           native_selection=True)

        for result in (changed, expired, synthesized):
            self.assertNotEqual(result["reason"], "state_error")
        self.assertEqual((changed["model"], changed["reason"]), ("gpt-6-terra", "jev"))
        self.assertEqual((expired["model"], expired["reason"]), ("gpt-6-terra", "jev"))
        self.assertEqual((synthesized["model"], synthesized["reason"]), ("gpt-6-terra", "jev"))
        leases = json.loads((self.root / "leases.json").read_text())
        self.assertEqual(leases[policy_hash]["policy"], "completion_v3")
        self.assertEqual(leases[expired_hash]["policy"], "completion_v3")
        self.assertEqual(leases[hashlib.sha256(b"native-current-model").hexdigest()[:24]]["model"], "gpt-6-terra")

    def test_independent_chats_overlap_across_processes_without_losing_either_lease(self):
        context = multiprocessing.get_context("spawn")
        entered, release, results = context.Event(), context.Event(), context.Queue()
        self.addCleanup(results.join_thread)
        self.addCleanup(results.close)
        first = context.Process(target=_process_decide,
                                args=(str(self.root), "process-chat-a", entered, release, results))
        second = None
        try:
            first.start()
            self.assertTrue(entered.wait(5), "first process never reached Jev")
            second_entered = context.Event()
            immediate_release = context.Event()
            immediate_release.set()
            second = context.Process(target=_process_decide,
                                     args=(str(self.root), "process-chat-b", second_entered,
                                           immediate_release, results))
            second.start()
            second_result = results.get(timeout=5)
            self.assertTrue(first.is_alive(), "first process completed before overlap was observed")
            self.assertEqual((second_result["model"], second_result["reason"]), ("gpt-6-luna", "jev"))
        finally:
            release.set()
            first.join(2)
            if second is not None:
                second.join(2)
            for child in (first, second):
                if child is not None:
                    if child.is_alive():
                        child.terminate()
                        child.join(2)
                    child.close()
        first_result = results.get(timeout=2)
        self.assertEqual((first_result["model"], first_result["reason"]), ("gpt-6-luna", "jev"))
        self.assertEqual(len(json.loads((self.root / "leases.json").read_text())), 2)
if __name__ == "__main__":
    unittest.main()
