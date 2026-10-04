"""Unit tests for throway/storage — the shared mechanics kit (1.52.0).

The kit is the seam under all three namespaces (files/dirs/pics); these
pin its two contracts: atomic writes (readers never see partial files)
and locked mutations (parallel RMW never loses updates). The behavior
surface stays with the HTTP suite — these are the proof for the race
fixes that a request-level test can't make deterministic.
"""
import json
import os
import threading

from throway import storage


def test_atomic_json_writes_valid_and_leaves_no_part(tmp_path):
    p = str(tmp_path / "m.json")
    storage.atomic_json(p, {"a": 1, "b": ["x"]})
    assert json.load(open(p)) == {"a": 1, "b": ["x"]}
    assert not os.path.exists(p + ".part")


def test_atomic_json_overwrites_in_place(tmp_path):
    p = str(tmp_path / "m.json")
    storage.atomic_json(p, {"v": 1})
    storage.atomic_json(p, {"v": 2})
    assert json.load(open(p)) == {"v": 2}


def test_locked_no_lost_updates(tmp_path):
    """N Threads x M RMW auf geteiltem Zaehler muessen exakt N*M ergeben.
    Ohne Lock verliert dict += Updates (GIL macht read-modify-write nicht
    atomar) — genau die like/comment/gallery-Rennen, die 1.52.0 fixt."""
    N, M = 8, 200
    counter = {"n": 0}

    @storage.locked("t-test")
    def bump():
        counter["n"] += 1

    def worker():
        for _ in range(M):
            bump()

    ts = [threading.Thread(target=worker) for _ in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert counter["n"] == N * M


def test_locked_rmw_on_file(tmp_path):
    """Die echte Form: load -> mutate -> save unter Lock."""
    p = str(tmp_path / "likes.json")
    storage.atomic_json(p, {"n": 0})

    @storage.locked("t-file")
    def like():
        d = json.load(open(p))
        d["n"] += 1
        storage.atomic_json(p, d)

    ts = [threading.Thread(target=like) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert json.load(open(p))["n"] == 6


def test_lock_for_identity_per_namespace():
    a1 = storage.lock_for("t-ns")
    a2 = storage.lock_for("t-ns")
    b = storage.lock_for("t-other")
    assert a1 is a2            # stabile Identitaet (dirs._LOCK verlaesst sich drauf)
    assert a1 is not b         # Namespaces isoliert (Q4)
    a1.acquire()                # benutzbar als Lock
    a1.release()


def test_locks_are_reentrant():
    """@locked-Funktionen schachteln sich (dirs-Mutatoren rufen einander,
    moderate ruft remove_pic-Follow-ups) — RLock, kein Deadlock."""
    got = []

    @storage.locked("t-re")
    def outer():
        got.append("outer")
        inner()

    @storage.locked("t-re")
    def inner():
        got.append("inner")

    outer()
    assert got == ["outer", "inner"]
