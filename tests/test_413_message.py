"""413-Meldungen muessen aus der Konfiguration kommen (1.53.3).

Retro-Fund der Issue-#1-Verifikation: „too large (max 5MB)" stand an sieben
Stellen hartkodiert — steigt THROWAWAY_MAX_FILE_BYTES per Env, logten die
Meldungen falsch (derselbe Drift-Fehler wie die /api-Notes vor 1.53.0).

Zwei 413-Wege, zwei Testformen:
- Frühcheck (Content-Length vor dem Lesen): per Raw-Socket — der Server
  antwortet, ohne den Body zu lesen (pics drainet dort, Files nicht;
  urllib bekommt bei grossem Overshoot sonst broken pipe). Der Test
  dokumentiert dieses Verhalten gleich mit.
- Post-read (len(data)/cur+len nach dem Lesen, z.B. PATCH): normal über
  den HTTP-Client."""
import json
import socket

import pytest

from conftest import Server


@pytest.fixture
def max_srv(tmp_path):
    s = Server(tmp_path / "max", env_extra={"THROWAWAY_MAX_FILE_BYTES": str(2 * 1024 * 1024)})
    yield s
    s.stop()


@pytest.fixture
def def_srv(tmp_path):
    s = Server(tmp_path / "def")
    yield s
    s.stop()


def _early_post(srv, path, total, chunk=1024):
    """POST mit riesigem Content-Length, nur chunk Bytes gesendet —
    der Frühcheck antwortet 413, ohne den Rest zu brauchen."""
    sk = socket.create_connection(("127.0.0.1", srv.port), timeout=10)
    req = (f"POST {path} HTTP/1.1\r\nHost: t\r\n"
           f"Content-Length: {total}\r\nConnection: close\r\n\r\n").encode() + b"x" * chunk
    sk.sendall(req)
    buf = b""
    while True:
        d = sk.recv(4096)
        if not d:
            break
        buf += d
    sk.close()
    return buf


def test_413_early_check_message_follows_env_max(max_srv):
    resp = _early_post(max_srv, "/?name=big.bin", 3 * 1024 * 1024)
    assert b" 413 " in resp.split(b"\r\n")[0]
    assert b"(max 2MB)" in resp


def test_413_patch_postread_message_follows_env_max(max_srv):
    st, body = max_srv.upload_raw(b"a" * (1536 * 1024), name="t.txt")
    assert st == 200
    fid = body["id"]
    st, _, body = max_srv.patch(f"/{fid}", data=b"b" * (1024 * 1024))
    assert st == 413
    assert "(max 2MB)" in json.loads(body)["error"]


def test_413_default_message_5mb(def_srv):
    resp = _early_post(def_srv, "/?name=big.bin", 6 * 1024 * 1024)
    assert b" 413 " in resp.split(b"\r\n")[0]
    assert b"(max 5MB)" in resp
