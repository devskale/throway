"""pics — event gallery behavior tests (RFQ.md sections 3, 8)."""
import io
import json
import os
import time

import pytest

from conftest import Server, multipart, urlencode

AGENT = {"User-Agent": "curl/8.0"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/120.0"}
SECRET = "testtoken123"
ADMIN = f"/pics/{SECRET}"


def jpeg(w=3000, h=2000, color=(120, 40, 200)):
    from PIL import Image
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "pics")
    yield s
    s.stop()


@pytest.fixture
def pool_srv(tmp_path):
    s = Server(tmp_path / "pool", env_extra={
        "THROWAWAY_PICS_POOL_BYTES": "2000",
    })
    yield s
    s.stop()


@pytest.fixture
def expiry_srv(tmp_path):
    s = Server(tmp_path / "exp", env_extra={"THROWAWAY_PICS_TTL": "1"})
    yield s
    s.stop()


@pytest.fixture
def noadmin_srv(tmp_path):
    s = Server(tmp_path / "noadmin", env_extra={"THROWAWAY_PICS_ADMIN_TOKEN": ""})
    yield s
    s.stop()


def up(srv, data=None, name="photo.jpg"):
    st, meta = srv.jpost(f"/pics?name={name}", data=data or jpeg(),
                         headers={"Content-Type": "application/octet-stream"})
    assert st == 200, meta
    return meta


def admin_action(srv, pid, action, page="1"):
    return srv.post(ADMIN, data=urlencode({"id": pid, "action": action, "p": page}),
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             **BROWSER})


# --- upload + view (FR-1..FR-10, AK-1/2) ---------------------------------

def test_upload_recompresses_and_serves(srv):
    orig = jpeg(4000, 3000)
    m = up(srv, orig, "big.jpg")
    assert m["content_type"] == "image/webp"
    assert m["size"] < len(orig) / 5          # heavy shrink
    st, hd, body = srv.get(f"/pics/i/{m['id']}")
    assert st == 200
    assert hd["Content-Type"].startswith("image/webp")
    from PIL import Image
    im = Image.open(io.BytesIO(body))
    assert max(im.size) <= 2048               # PICS_EDGE


def test_gallery_lists_visible_for_agents_and_browsers(srv):
    m = up(srv)
    st, _, body = srv.get("/pics", headers=AGENT)
    assert st == 200
    listing = json.loads(body)
    assert listing["gallery"] is True
    assert any(i["id"] == m["id"] for i in listing["images"])
    assert "pool" in listing and "limits" in listing
    st, _, html = srv.get("/pics", headers=BROWSER)
    assert st == 200 and f"/pics/i/{m['id']}?thumb=1" in html.decode()


def test_upload_multipart_batch(srv):
    files = [("a.jpg", jpeg(800, 600), "image/jpeg"),
             ("b.png", jpeg(700, 500), "image/png"),
             ("c.jpg", jpeg(900, 700), "image/jpeg")]
    body, ctype = multipart(files)
    st, _, out = srv.post("/pics", data=body,
                          headers={"Content-Type": ctype, **AGENT})
    assert st == 200
    res = json.loads(out)
    assert len(res["added"]) == 3 and not res["errors"]
    # browser form flow -> redirect to the gallery
    st, hd, _ = srv.post("/pics", data=body,
                         headers={"Content-Type": ctype, **BROWSER})
    assert st == 303 and hd["Location"].endswith("/pics")


def test_non_image_rejected(srv):
    st, _, body = srv.post("/pics?name=x.txt", data=b"just text",
                           headers={"Content-Type": "text/plain"})
    assert st == 400
    assert "image" in json.loads(body)["error"]


def test_heic_without_lib_clear_error(srv):
    # ftyp box with heic brand but garbage payload
    data = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    st, _, body = srv.post("/pics?name=x.heic", data=data)
    assert st == 400
    err = json.loads(body)["error"].lower()
    assert "heic" in err or "decode" in err or "image" in err


def test_upload_too_large_rejected(srv):
    st, _, body = srv.post("/pics?name=huge.jpg", data=b"0" * (30 * 1024 * 1024 + 1))
    assert st == 413


def test_too_large_before_processing(srv):
    # oversized but valid-image-looking header must not hit the decoder
    st, _, _ = srv.post("/pics?name=h.jpg", data=b"\xff\xd8\xff" + b"0" * (31 * 1024 * 1024))
    assert st == 413


# --- lifetime + pool (FR-15..FR-17, AK-3/4) ------------------------------

def test_expiry_removes_image(expiry_srv):
    m = up(expiry_srv)
    time.sleep(1.3)
    st, _, _ = expiry_srv.get(f"/pics/i/{m['id']}")
    assert st == 404
    st, _, body = expiry_srv.get("/pics", headers=AGENT)
    assert json.loads(body)["count"] == 0


def test_pool_full_rejects_never_evicts(pool_srv):
    m1 = up(pool_srv, jpeg(64, 64), "small.jpg")   # tiny webp fits in 2000B budget
    # now fill the rest: another upload must be rejected with 507 …
    st, _, body = pool_srv.post("/pics?name=two.jpg", data=jpeg(1200, 900),
                                headers={"Content-Type": "application/octet-stream"})
    assert st == 507
    # … and the first image must still be there, untouched
    st, _, _ = pool_srv.get(f"/pics/i/{m1['id']}")
    assert st == 200


def test_pics_live_in_own_namespace_not_throway_pool(srv):
    m = up(srv)
    assert os.path.isfile(os.path.join(srv.root, "pics", m["id"]))
    assert not os.path.isfile(os.path.join(srv.root, m["id"]))
    # throway uploads still work and don't see gallery bytes
    st, meta = srv.upload_raw(b"normal file", name="n.txt")
    assert st == 200


# --- admin + moderation (FR-11..FR-14, AK-5..AK-8) -----------------------

def test_admin_secret_wrong_and_right(srv):
    st, _, _ = srv.get("/pics/wrongsecret", headers=BROWSER)
    assert st == 404
    st, _, body = srv.get(ADMIN, headers=BROWSER)
    assert st == 200 and "admin" in body.decode().lower()


def test_admin_disabled_without_token(noadmin_srv):
    st, _, _ = noadmin_srv.get(f"/pics/{SECRET}")
    assert st == 404
    st, _, _ = noadmin_srv.get(f"/pics/{SECRET}/json", headers=AGENT)
    assert st == 404


def test_hide_user_404_admin_sees_unhide_restores(srv):
    m = up(srv)
    st, _, _ = admin_action(srv, m["id"], "hide")
    assert st == 303
    # public: gone everywhere
    st, _, _ = srv.get(f"/pics/i/{m['id']}")
    assert st == 404
    st, _, body = srv.get("/pics", headers=AGENT)
    assert all(i["id"] != m["id"] for i in json.loads(body)["images"])
    # admin: still visible (page + direct + json)
    st, _, html = srv.get(ADMIN, headers=BROWSER)
    assert st == 200 and m["id"] in html.decode()
    st, _, _ = srv.get(f"{ADMIN}/i/{m['id']}")
    assert st == 200
    st, _, body = srv.get(f"{ADMIN}/json", headers=AGENT)
    hidden = json.loads(body)["hidden"]
    assert any(i["id"] == m["id"] for i in hidden)
    # bytes + meta survive the hide (RFQ FR-13)
    assert os.path.isfile(os.path.join(srv.root, "pics", m["id"]))
    # unhide restores public view
    admin_action(srv, m["id"], "unhide")
    st, _, _ = srv.get(f"/pics/i/{m['id']}")
    assert st == 200


def test_admin_delete_removes_bytes(srv):
    m = up(srv)
    st, _, _ = admin_action(srv, m["id"], "delete")
    assert st == 303
    st, _, _ = srv.get(f"/pics/i/{m['id']}")
    assert st == 404
    d = os.path.join(srv.root, "pics")
    leftovers = [f for f in os.listdir(d) if f.startswith(m["id"])]
    assert leftovers == []                    # file, .meta, .thumb all gone


def test_reorder_basic_moves(srv):
    a = up(srv, jpeg(200, 200, (255, 0, 0)), "a.jpg")["id"]
    b = up(srv, jpeg(200, 200, (0, 255, 0)), "b.jpg")["id"]
    c = up(srv, jpeg(200, 200, (0, 0, 255)), "c.jpg")["id"]
    ids = lambda: [i["id"] for i in json.loads(srv.get("/pics", headers=AGENT)[2])["images"]]
    assert ids() == [c, b, a]                 # newest first (FR-8)
    admin_action(srv, b, "up")                # b one slot towards the front
    assert ids() == [b, c, a]


def test_reorder_is_stable_and_exact(srv):
    a = up(srv, jpeg(64, 64, (255, 0, 0)), "a.jpg")["id"]
    b = up(srv, jpeg(64, 64, (0, 255, 0)), "b.jpg")["id"]
    c = up(srv, jpeg(64, 64, (0, 0, 255)), "c.jpg")["id"]
    d = up(srv, jpeg(64, 64, (9, 9, 9)), "d.jpg")["id"]
    ids = lambda: [i["id"] for i in json.loads(srv.get("/pics", headers=AGENT)[2])["images"]]
    assert ids() == [d, c, b, a]
    admin_action(srv, c, "up")                # d c b a -> c d b a
    assert ids() == [c, d, b, a]
    admin_action(srv, c, "up")                # already first: no-op
    assert ids() == [c, d, b, a]
    admin_action(srv, a, "down")              # already last: no-op
    assert ids() == [c, d, b, a]
    admin_action(srv, d, "down")              # c d b a -> c b d a
    assert ids() == [c, b, d, a]


# --- self-service (TR-6, AK-10) ------------------------------------------

def test_api_lists_pics_endpoints(srv):
    st, _, body = srv.get("/api", headers=AGENT)
    spec = json.loads(body)
    for ep in ("pics_upload", "pics_gallery", "pics_image", "pics_admin"):
        assert ep in spec["endpoints"]


def test_help_topic_pics_served(srv):
    st, _, body = srv.get("/help/pics", headers=AGENT)
    assert st == 200
    assert "gallery" in body.decode().lower()


def test_throway_surface_unaffected(srv):
    """The whole point of the namespace split: throway keeps behaving."""
    st, meta = srv.upload_raw(b"still works", name="ok.txt")
    assert st == 200
    st, _, body = srv.get("/" + meta["id"])
    assert st == 200 and body == b"still works"
