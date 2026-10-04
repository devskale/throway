"""Shared storage mechanics — atomic meta writes, locked mutations.

Architecture review candidate 4 (1.52.0). Three namespaces (files, dirs,
pics) sit on ONE mechanics kit; pool accounting and eviction policy stay
per namespace (files evict LRU on the throwaway pool, pics reject 507 on
its own pool — never mixed). What is shared is only the MECHANICS:
atomar schreiben, read-modify-write serialisieren.

Interface (klein & tief, Q1a):
    atomic_json(path, obj)   write JSON via tmp + os.replace — concurrent
                             readers never see a partial file (1.45.2 retro)
    locked(ns)               decorator serializing a namespace's mutations:
                             load -> mutate -> save holds the namespace lock
                             for the whole body, so parallel requests can't
                             lose updates (pics had NO lock at all before)
    lock_for(ns)             the namespace's RLock, for modules needing
                             synchronized visibility (dirs._LOCK delegates)

Locks are PER NAMESPACE ("files", "dirs", "pics"; Q4): a like-storm on
pics never blocks a dir mutation. They are RLocks because locked
functions nest (dirs mutators call each other; moderate calls remove_pic).
Lock order where nesting occurs is pics -> files (store_pic's stats bump);
no other cross-namespace nesting exists — keep it that way.

Deliberately NOT locked: pics.store_pic (image processing is too heavy to
serialize; its binary write is atomic, dedupe races only cost a rare
duplicate image) and the sweeps (long pool scans; they lock per removal
via remove_gallery instead).
"""
import json
import os
import threading

_LOCKS = {}


def lock_for(ns):
    """The namespace's reentrant mutation lock (lazily created, stable
    identity per name — dirs and store see the same lock)."""
    lock = _LOCKS.get(ns)
    if lock is None:
        lock = _LOCKS.setdefault(ns, threading.RLock())
    return lock


def locked(ns):
    """Serialize mutations of one namespace. Retro context: likes,
    comments, gallery sliding and create-or-get were unserialized
    load->mutate->save chains — parallel requests lost updates."""
    def deco(fn):
        def wrapper(*a, **kw):
            with lock_for(ns):
                return fn(*a, **kw)
        return wrapper
    return deco


def atomic_json(path, obj):
    """Write JSON atomically (tmp + os.replace) — concurrent readers never
    see a partial file. Retro 1.45.2 / 2026-10-02 hard-validate:
    non-atomic meta writes lost files under parallel writes."""
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)
