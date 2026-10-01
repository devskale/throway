"""pics — multi-gallery behavior tests (1.20.0 model: anyone creates a
gallery, becomes its admin via a per-gallery token; env token = superadmin)."""
import io
import json
import os
import time

import pytest

from conftest import Server, multipart, urlencode

AGENT = {"User-Agent": "curl/8.0"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/120.0"}
SUPER = "testtoken123"          # env THROWAWAY_PICS_ADMIN_TOKEN in conftest


def jpeg(w=1200, h=800, color=(120, 40, 200)):
    from PIL import Image
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "pics")
    yield s
    s.stop()


@pytest.fixture
def pool_srv(tmp_path):
    s = Server(tmp_path / "pool", env_extra={"THROWAWAY_PICS_POOL_BYTES": "2000"})
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


def create(srv, qs="create=1", browser=False):
    st, _, body = srv.post(f"/pics?{qs}", data=b"",
                           headers=BROWSER if browser else AGENT)
    return st, json.loads(body) if st == 200 and body[:1] == b"{" else body


def up(srv, gid, data=None, name="photo.jpg"):
    st, _, body = srv.post(f"/pics/g/{gid}?name={name}",
                           data=data or jpeg(),
                           headers={"Content-Type": "application/octet-stream"})
    assert st == 200, body
    return json.loads(body)


def admin(srv, gid, token, pid, action, page="1"):
    st, hd, _ = srv.post(f"/pics/g/{gid}/{token}",
                         data=urlencode({"id": pid, "action": action, "p": page}),
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  **BROWSER})
    return st, hd


# --- creation (create-or-get like dirs) -----------------------------------

def test_create_gallery_returns_token_once(srv):
    st, m = create(srv)
    assert st == 200
    assert m["id"] and m["token"] and m["admin_url"].endswith("/pics/g/" + m["id"] + "/" + m["token"])
    assert m["url"].endswith("/pics/g/" + m["id"])
    assert "exactly once" in m["note"]


def test_create_browser_form_shows_admin_link(srv):
    st, _, body = srv.post("/pics?create=1", data=urlencode({"name": "Party Freitag", "listed": "1"}),
                           headers={"Content-Type": "application/x-www-form-urlencoded", **BROWSER})
    assert st == 200
    html = body.decode()
    assert "Admin-Link" in html and "/pics/g/" in html


def test_named_gallery_create_or_get(srv):
    st, m1 = create(srv, "create=1&name=hochzeit-2026")
    assert st == 200 and m1["id"] == "hochzeit-2026"
    assert m1["url"].endswith("/pics/g/hochzeit-2026")
    # second create-or-get: same gallery, but NEVER the token again
    st, m2 = create(srv, "create=1&name=hochzeit-2026")
    assert st == 200
    assert m2.get("existed") is True
    assert "token" not in m2 and "admin_url" not in m2
    # display names (not key-shaped) get a fresh hex id
    st, m3 = create(srv, "create=1&name=Hochzeit%20M%C3%BCnchen")
    assert st == 200 and m3["id"] != "hochzeit-2026"
    assert len(m3["id"]) == 16 and m3["name"].startswith("Hochzeit")


def test_named_gallery_rejects_invalid_keys(srv):
    st, m = create(srv, "create=1&name=ab")            # too short -> display name
    assert st == 200 and m["id"] != "ab"
    st, m = create(srv, "create=1&name=api")           # reserved -> display name
    assert st == 200 and m["id"] != "api"


def test_index_lists_only_listed(srv):
    _, pub = create(srv, "create=1&name=pub-gal&listed=1")
    _, hid = create(srv, "create=1&name=secret-gal")
    st, _, body = srv.get("/pics", headers=AGENT)
    idx = json.loads(body)
    ids = [g["id"] for g in idx["galleries"]]
    assert "pub-gal" in ids and "secret-gal" not in ids
    # unlisted is still directly reachable
    st, _, _ = srv.get("/pics/g/secret-gal", headers=AGENT)
    assert st == 200


# --- upload + view ---------------------------------------------------------

def test_upload_to_gallery_and_view(srv):
    _, g = create(srv)
    orig = jpeg(4000, 3000)
    m = up(srv, g["id"], orig, "big.jpg")
    assert m["content_type"] == "image/webp" and m["gid"] == g["id"]
    assert m["size"] < len(orig) / 5
    st, hd, body = srv.get("/pics/i/" + m["id"])
    assert st == 200 and hd["Content-Type"].startswith("image/webp")
    from PIL import Image
    assert max(Image.open(io.BytesIO(body)).size) <= 2048
    # gallery listing scoped to this gallery
    st, _, body = srv.get("/pics/g/" + g["id"], headers=AGENT)
    listing = json.loads(body)
    assert [i["id"] for i in listing["images"]] == [m["id"]]
    st, _, html = srv.get("/pics/g/" + g["id"], headers=BROWSER)
    assert f"/pics/i/{m['id']}?thumb=1" in html.decode()


def test_upload_to_unknown_gallery_404(srv):
    st, _, _ = srv.post("/pics/g/deadbeefdeadbeef?name=x.jpg", data=jpeg())
    assert st == 404


def test_upload_name_url_decoded(srv):
    """1.22.1: ?name= values are percent-decoded (uniinfer-tu%40x.png,
    PXL%20foto.jpg arrive as @ and space, not %40/%20)."""
    _, g = create(srv)
    st, m = srv.jpost(f"/pics/g/{g['id']}?name=uniinfer-tu%40x.png",
                      data=jpeg(), headers={"Content-Type": "application/octet-stream"})
    assert st == 200, m
    assert m["name"] == "uniinfer-tu@x.png"
    st, m2 = srv.jpost(f"/pics/g/{g['id']}?name=PXL%20foto.jpg",
                       data=jpeg(500, 400, (7, 7, 7)),   # anderes bild — dedupe!
                       headers={"Content-Type": "application/octet-stream"})
    assert m2["name"] == "PXL foto.jpg"


def test_galleries_are_isolated(srv):
    _, a = create(srv, "create=1&name=gal-a")
    _, b = create(srv, "create=1&name=gal-b")
    ma = up(srv, a["id"])
    mb = up(srv, b["id"])
    la = json.loads(srv.get("/pics/g/gal-a", headers=AGENT)[2])
    lb = json.loads(srv.get("/pics/g/gal-b", headers=AGENT)[2])
    assert [i["id"] for i in la["images"]] == [ma["id"]]
    assert [i["id"] for i in lb["images"]] == [mb["id"]]


def test_multipart_batch_into_gallery(srv):
    _, g = create(srv)
    files = [("a.jpg", jpeg(800, 600), "image/jpeg"),
             ("b.png", jpeg(700, 500), "image/png")]
    body, ctype = multipart(files)
    st, _, out = srv.post(f"/pics/g/{g['id']}", data=body,
                          headers={"Content-Type": ctype, **AGENT})
    assert st == 200
    assert len(json.loads(out)["added"]) == 2
    st, hd, _ = srv.post(f"/pics/g/{g['id']}", data=body,
                         headers={"Content-Type": ctype, **BROWSER})
    assert st == 303 and hd["Location"].endswith("/pics/g/" + g["id"])


def test_non_image_rejected(srv):
    _, g = create(srv)
    st, _, body = srv.post(f"/pics/g/{g['id']}?name=x.txt", data=b"text",
                           headers={"Content-Type": "text/plain"})
    assert st == 400


def test_too_large_rejected_and_drained(srv):
    _, g = create(srv)
    st, _, body = srv.post(f"/pics/g/{g['id']}?name=h.jpg",
                           data=b"\xff\xd8\xff" + b"0" * (31 * 1024 * 1024))
    assert st == 413
    # connection still usable afterwards
    st, _, _ = srv.get("/api")
    assert st == 200


# --- admin (per-gallery token + superadmin) --------------------------------

def test_gallery_admin_hide_delete_reorder(srv):
    _, g = create(srv)
    tok = g["token"]
    a = up(srv, g["id"], jpeg(64, 64, (255, 0, 0)), "a.jpg")["id"]
    b = up(srv, g["id"], jpeg(64, 64, (0, 255, 0)), "b.jpg")["id"]
    c = up(srv, g["id"], jpeg(64, 64, (0, 0, 255)), "c.jpg")["id"]
    ids = lambda: [i["id"] for i in json.loads(
        srv.get(f"/pics/g/{g['id']}", headers=AGENT)[2])["images"]]
    assert ids() == [c, b, a]                     # newest first
    st, hd = admin(srv, g["id"], tok, b, "up")
    assert st == 303 and f"/pics/g/{g['id']}/{tok}" in hd["Location"]
    assert ids() == [b, c, a]
    # hide: public 404, listing clean, admin still sees
    admin(srv, g["id"], tok, b, "hide")
    assert srv.get("/pics/i/" + b)[0] == 404
    assert b not in ids()
    st, _, body = srv.get(f"/pics/g/{g['id']}/{tok}/json", headers=AGENT)
    hidden = json.loads(body)["hidden"]
    assert [i["id"] for i in hidden] == [b]
    assert srv.get(f"/pics/g/{g['id']}/{tok}/i/" + b)[0] == 200
    admin(srv, g["id"], tok, b, "unhide")
    assert srv.get("/pics/i/" + b)[0] == 200
    # delete removes bytes for good
    admin(srv, g["id"], tok, c, "delete")
    assert srv.get("/pics/i/" + c)[0] == 404
    d = os.path.join(srv.root, "pics")
    assert [f for f in os.listdir(d) if f.startswith(c)] == []


def test_wrong_token_404_everywhere(srv):
    _, g = create(srv)
    pid = up(srv, g["id"])["id"]
    for path in (f"/pics/g/{g['id']}/wrongtoken",
                 f"/pics/g/{g['id']}/wrongtoken/json",
                 f"/pics/g/{g['id']}/wrongtoken/i/{pid}"):
        st, _, _ = srv.post(path) if False else srv.get(path)
        assert st == 404, path
    st, _, _ = srv.post(f"/pics/g/{g['id']}/wrongtoken",
                        data=urlencode({"id": pid, "action": "hide"}),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert st == 404
    assert srv.get("/pics/i/" + pid)[0] == 200       # nothing happened


def test_superadmin_can_curate_any_gallery(srv):
    _, g = create(srv, "create=1&name=super-t")
    pid = up(srv, g["id"])["id"]
    st, _ = admin(srv, g["id"], SUPER, pid, "hide")
    assert st == 303
    assert srv.get("/pics/i/" + pid)[0] == 404
    st, _, body = srv.get(f"/pics/g/{g['id']}/{SUPER}/json", headers=AGENT)
    assert [i["id"] for i in json.loads(body)["hidden"]] == [pid]
    admin(srv, g["id"], SUPER, pid, "unhide")
    assert srv.get("/pics/i/" + pid)[0] == 200


def test_superadmin_disabled_without_env_token(noadmin_srv):
    _, g = create(noadmin_srv)
    pid = up(noadmin_srv, g["id"])["id"]
    st, _, _ = noadmin_srv.get(f"/pics/g/{g['id']}/{SUPER}/json", headers=AGENT)
    assert st == 404                      # env token unset -> superadmin off
    # own token still works
    st, _, _ = noadmin_srv.get(f"/pics/g/{g['id']}/{g['token']}/json", headers=AGENT)
    assert st == 200


def test_admin_cannot_touch_foreign_gallery_images(srv):
    _, a = create(srv, "create=1&name=iso-a")
    _, b = create(srv, "create=1&name=iso-b")
    pa = up(srv, a["id"])["id"]
    # b's admin token on b's admin route targeting a's image: no-op
    st, _ = admin(srv, b["id"], b["token"], pa, "hide")
    assert st == 303
    assert srv.get("/pics/i/" + pa)[0] == 200        # still public
    # admin view of a's image via b's admin route: 404
    st, _, _ = srv.get(f"/pics/g/{b['id']}/{b['token']}/i/{pa}")
    assert st == 404


# --- lifetime + pool --------------------------------------------------------

def test_gallery_expiry_sweeps_images_and_gallery(expiry_srv):
    _, g = create(expiry_srv, "create=1&name=short-lived")
    pid = up(expiry_srv, g["id"])["id"]
    time.sleep(1.3)
    assert expiry_srv.get("/pics/i/" + pid)[0] == 404
    st, _, _ = expiry_srv.get("/pics/g/short-lived", headers=AGENT)
    assert st == 404
    st, _, body = expiry_srv.get("/pics", headers=AGENT)
    assert "short-lived" not in [x["id"] for x in json.loads(body)["galleries"]]


def test_upload_slides_gallery_expiry(srv):
    _, g = create(srv, "create=1&name=sliding-g")
    meta_path = os.path.join(srv.root, "pics", "g", "sliding-g.json")
    e1 = json.load(open(meta_path))["expires"]
    time.sleep(0.3)
    up(srv, g["id"])
    e2 = json.load(open(meta_path))["expires"]
    assert e2 > e1


def test_pool_full_rejects_never_evicts(pool_srv):
    _, g = create(pool_srv)
    m1 = up(pool_srv, g["id"], jpeg(64, 64), "small.jpg")
    st, _, _ = pool_srv.post(f"/pics/g/{g['id']}?name=two.jpg",
                             data=jpeg(1200, 900),
                             headers={"Content-Type": "application/octet-stream"})
    assert st == 507
    assert pool_srv.get("/pics/i/" + m1["id"])[0] == 200


# --- self-service + store isolation -----------------------------------------

def test_gallery_lightbox(srv):
    """1.23.0: click-through viewer — ‹ › buttons, arrow keys, esc, swipe."""
    _, g = create(srv)
    a = up(srv, g["id"], jpeg(64, 64, (1, 2, 3)), "a.jpg")["id"]
    b = up(srv, g["id"], jpeg(64, 64, (9, 9, 9)), "b.jpg")["id"]
    st, _, html = srv.get(f"/pics/g/{g['id']}", headers=BROWSER)
    assert st == 200
    page = html.decode()
    assert "id=lb" in page and "lbprev" in page and "lbnext" in page
    assert "ArrowRight" in page and "Escape" in page   # keyboard nav
    # both images baked into the viewer list, newest first
    assert page.index(f"/pics/i/{b}") < page.index(f"/pics/i/{a}")
    # admin lightbox uses secret-scoped urls and includes hidden images
    admin(srv, g["id"], g["token"], a, "hide")
    st, _, html = srv.get(f"/pics/g/{g['id']}/{g['token']}", headers=BROWSER)
    page = html.decode()
    assert f"/pics/g/{g['id']}/{g['token']}/i/{a}" in page


def test_embed_lightbox_grows_with_infinite_scroll(srv):
    """1.38.4: thumbs appended by infinite scroll must (a) open the lightbox
    (delegated clicks — before, clicking thumb #25 navigated the iframe to
    the raw image and killed the embed) and (b) appear in the caption
    ("N / 24" bug: the LB list never grew past the first embed page)."""
    _, g = create(srv)
    for i in range(6):
        up(srv, g["id"])
    st, _, page = srv.get(f"/pics/g/{g['id']}?embed=1", headers=BROWSER)
    assert st == 200
    p = page.decode()
    # (a) delegated click handling, live index lookup
    assert "closest('.grid a')" in p
    assert "forEach.call(document.querySelectorAll('.grid a')" not in p
    # (b) LB list extends from #lbdata payloads of fetched pages
    assert "id=lbdata" in p and "window.__tyLbAdd" in p
    assert "JSON.parse(ld.textContent)" in p
    # (c) box position follows the host viewport offset
    assert "e.data.offset" in p and "lb.style.top" in p
    # snippet posts the offset and dedupes on height AND offset
    st, _, page = srv.get(f"/pics/g/{g['id']}", headers=BROWSER)
    p2 = page.decode()
    assert "offset:o" in p2 and "lastO=-1" in p2
    assert "getBoundingClientRect().top" in p2


def test_embed_lazy_load_is_viewport_aware(srv):
    """1.39.0: smart lazy-load — the infinite scroll must follow the visible
    window (handshake), not the whole auto-height iframe (which loaded
    every page at once). Plus lightbox next() loads the next page at the
    list boundary instead of wrapping."""
    _, g = create(srv)
    up(srv, g["id"])
    st, _, page = srv.get(f"/pics/g/{g['id']}?embed=1", headers=BROWSER)
    assert st == 200
    p = page.decode()
    # viewport-bewusstes Nachladen
    assert "visTop=0,visH=0" in p and "visTop+visH+900" in p
    # Fallback ohne Host-Nachricht: erst nach Grace-Periode
    assert "Date.now()-t0<1500" in p
    assert "innerHeight+500" in p                     # Fallback bleibt
    # Hooks fuer die Lightbox-Grenze
    assert "window.__tyMore=more" in p
    assert "window.__tyHasMore" in p
    # Lightbox: next() an der Grenze laedt nach statt zu wrappen
    assert "if(t>=LB.length&&window.__tyHasMore" in p
    assert "LB.length>t){clearInterval(w);open(t);" in p
    # Thumbs weiterhin native-lazy (fixed-height-frames / standalone)
    assert "loading=lazy" in p and "decoding=async" in p


def test_lightbox_visible_viewport(srv):
    """1.38.2: lightbox sized to the VISIBLE area, not the auto-height
    iframe. Host posts throway:pics:viewport (acked via pics:ready);
    without one the box caps at screen.height. Rodney flow (390x844
    viewport, 2717px auto-height iframe): legacy snippet -> #lb 844px,
    img 300x200, prev/next/cap/like/close all inside [0,844]."""
    _, g = create(srv)
    up(srv, g["id"])
    st, _, page = srv.get(f"/pics/g/{g['id']}?embed=1", headers=BROWSER)
    assert st == 200
    p = page.decode()
    assert "inset:0" not in p                        # no more full-frame box
    assert "height:100vh" in p and "max-height:80%" in p
    assert "screen.height" in p                      # self-contained fallback
    assert "throway:pics:viewport" in p              # host viewport listener
    assert "throway:pics:ready" in p                 # ... and its ack
    # gallery page: copy-paste snippet posts the viewport + honors the ack
    st, _, page = srv.get(f"/pics/g/{g['id']}", headers=BROWSER)
    p2 = page.decode()
    assert "throway:pics:viewport" in p2 and "throway:pics:ready" in p2
    # admin view keeps the (now viewport-safe) lightbox too
    st, _, page = srv.get(f"/pics/g/{g['id']}/{g['token']}", headers=BROWSER)
    assert "throway:pics:viewport" in page.decode()


def test_hq_size_cap_and_quality_floor(srv):
    """1.25.0: HQ first — noisy photo > 1 MB gets stepped down until it fits
    ~1 MB, stays 2048px."""
    import random
    from PIL import Image
    im = Image.new("RGB", (3000, 2000))
    px = im.load()
    random.seed(42)
    for y in range(0, 2000, 3):
        for x in range(0, 3000, 3):
            px[x, y] = (random.randrange(256), random.randrange(256), random.randrange(256))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    orig = buf.getvalue()
    assert len(orig) > 1024 * 1024
    _, g = create(srv)
    m = up(srv, g["id"], orig, "noisy.jpg")
    assert m["size"] <= 1024 * 1024          # PICS_TARGET
    assert m["width"] <= 2048 and m["height"] <= 2048


def test_alpha_preserved(srv):
    """1.25.0: transparent PNGs keep their alpha (no white flattening)."""
    from PIL import Image
    im = Image.new("RGBA", (800, 600), (10, 200, 100, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    _, g = create(srv)
    m = up(srv, g["id"], buf.getvalue(), "transparent.png")
    st, _, body = srv.get("/pics/i/" + m["id"])
    assert st == 200
    assert Image.open(io.BytesIO(body)).mode == "RGBA"


def test_exif_gps_stripped_lossless(srv):
    """1.26.0: small JPEGs keep their pixels byte-identically, but EXIF
    (incl. GPS) is removed via lossless container surgery."""
    from PIL import Image
    im = Image.new("RGB", (600, 400), (5, 5, 5))
    exif = Image.Exif()
    exif[271] = "TestCam"                      # Make
    exif[272] = "Model X"                      # Model
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    _, g = create(srv)
    orig = buf.getvalue()
    m = up(srv, g["id"], orig, "with-exif.jpg")
    assert m["content_type"] == "image/jpeg"
    st, _, body = srv.get("/pics/i/" + m["id"])
    assert st == 200
    served = Image.open(io.BytesIO(body))
    assert not served.getexif()                # metadata gone
    assert served.size == im.size              # pixels untouched
    assert len(body) <= len(orig)              # at least not bigger


def test_small_images_kept_byte_identical(srv):
    """1.26.0: <=2048px JPEG/PNG are stored byte-for-byte — zero quality
    loss, zero re-encode."""
    from PIL import Image
    _, g = create(srv)
    # JPEG <=2048px
    im = Image.new("RGB", (1200, 800), (200, 30, 90))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88, subsampling=0)
    orig = buf.getvalue()
    m = up(srv, g["id"], orig, "small.jpg")
    assert m["content_type"] == "image/jpeg"
    st, _, body = srv.get("/pics/i/" + m["id"])
    assert body == orig                        # bit-identical!
    # PNG <=2048px (with alpha)
    im = Image.new("RGBA", (500, 400), (10, 200, 100, 128))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    orig_png = buf.getvalue()
    m2 = up(srv, g["id"], orig_png, "small.png")
    assert m2["content_type"] == "image/png"
    st, _, body2 = srv.get("/pics/i/" + m2["id"])
    assert body2 == orig_png                   # bit-identical, alpha intact


def test_api_lists_pics_endpoints(srv):
    st, _, body = srv.get("/api", headers=AGENT)
    spec = json.loads(body)
    for ep in ("pics_create", "pics_index", "pics_gallery", "pics_upload",
               "pics_image", "pics_admin"):
        assert ep in spec["endpoints"]


def test_help_topic_pics_served(srv):
    st, _, body = srv.get("/help/pics", headers=AGENT)
    assert st == 200 and "galler" in body.decode().lower()


def test_post_pics_without_create_hints(srv):
    st, _, body = srv.post("/pics", data=b"")
    assert st == 400 and "create" in body.decode()


def test_homepage_integrates_gallery_creation(srv):
    """1.22.0: one multi-type uploader — tabs for files/text/link/gallery."""
    st, _, html = srv.get("/", headers=BROWSER)
    assert st == 200
    page = html.decode()
    # segmented control with all four modes + stable frame
    assert "role='tablist'" in page
    for mode in ("files", "text", "link", "gallery"):
        assert f"data-mode={mode}" in page, mode
    assert "galName" in page and "linkUrl" in page      # gallery + link inputs
    assert "/pics" in page                              # link to the gallery index
    # gallery creation still works end-to-end (the JS flow uses this endpoint)
    st, _, body = srv.post("/pics?create=1",
                           data=urlencode({"name": "vom-homepage", "listed": "1"}),
                           headers={"Content-Type": "application/x-www-form-urlencoded",
                                    **BROWSER})
    assert st == 200 and "Admin-Link" in body.decode()
    st, _, body = srv.get("/pics", headers=AGENT)
    assert "vom-homepage" in [g["id"] for g in json.loads(body)["galleries"]]


def test_store_surface_unaffected(srv):
    st, meta = srv.upload_raw(b"still works", name="ok.txt")
    assert st == 200
    st, _, body = srv.get("/" + meta["id"])
    assert st == 200 and body == b"still works"
    # gallery images live in the pics namespace only
    _, g = create(srv)
    pid = up(srv, g["id"])["id"]
    assert os.path.isfile(os.path.join(srv.root, "pics", pid))
    assert not os.path.isfile(os.path.join(srv.root, pid))


def test_thumbs_dont_consume_rate_limit(tmp_path):
    """1.30.0: gallery pages fire 60 thumbs — thumbs stay outside the rate
    counter (two full pages/minute would 429 from request #101)."""
    s = Server(tmp_path / "rl", env_extra={"THROWAWAY_RATE_LIMIT": "5"})
    try:
        _, g = create(s)
        m = up(s, g["id"])
        # 10 thumb requests: none may hit the limit
        for _ in range(10):
            st, hd, _ = s.get(f"/pics/i/{m['id']}?thumb=1")
            assert st == 200, "thumb wurde gelimitet"
            assert hd.get("Cache-Control") == "public, max-age=3600"
        # non-thumb requests still count (setup already used ~3 of the 5;
        # the 10 thumbs above must NOT have consumed any)
        codes = [s.get(f"/pics/i/{m['id']}")[0] for _ in range(6)]
        assert 429 in codes, "limit gilt weiterhin fuer non-thumb-requests"
    finally:
        s.stop()


def test_pics_image_cache_header_and_stats(srv):
    """1.30.0: pics images carry immutable cache hints; uploads count into
    the shared stats (homepage counters see gallery activity)."""
    _, g = create(srv)
    stats_before = json.load(open(os.path.join(
        os.path.dirname(stats_path(srv)), "stats.json"))) if os.path.isfile(
        os.path.join(os.path.dirname(stats_path(srv)), "stats.json")) else {"files": 0, "bytes": 0}
    m = up(srv, g["id"])
    st, hd, _ = srv.get("/pics/i/" + m["id"])
    assert st == 200 and hd.get("Cache-Control") == "public, max-age=86400"
    # stats counted the upload
    stats_after = json.load(open(stats_path(srv)))
    assert stats_after["files"] == stats_before.get("files", 0) + 1
    # throway single files stay uncached (they are PUT-editable)
    st2, meta = srv.upload_raw(b"mutables", name="t.txt")
    st2, hd2, _ = srv.get("/" + meta["id"])
    assert hd2.get("Cache-Control") is None


def stats_path(srv):
    # stats.json liegt neben der serverkopie im tmpdir
    return os.path.join(os.path.dirname(srv.root), "app", "stats.json")


def test_gallery_embed_view(srv):
    """1.33.0: ?embed=1 = minimal chrome-less view for iframes."""
    _, g = create(srv)
    up(srv, g["id"])
    up(srv, g["id"])
    st, hd, page = srv.get(f"/pics/g/{g['id']}?embed=1", headers=BROWSER)
    assert st == 200 and hd["Content-Type"].startswith("text/html")
    p = page.decode()
    # was drin sein muss: grid, lightbox, pagination, transparent bg
    assert "class=grid" in p and "id=lb" in p and "background:transparent" in p
    assert "?embed=1&p=" in p or "1 / 1" in p
    # 1.36.0: infinite scroll + auto-hoehe
    assert "id=sent" in p and "setInterval" in p   # polling-basiertes nachladen
    assert "throway:pics:height" in p            # postMessage an host
    # was NICHT drin sein darf: uploader, header, limits
    assert "upDrop" not in p and "galDrop" not in p and "galName" not in p
    assert "<h1>" not in p and "alle Galerien" not in p
    # normale seite: weiterhin mit uploader + embed-snippet
    st, _, page = srv.get(f"/pics/g/{g['id']}", headers=BROWSER)
    p2 = page.decode()
    assert "upDrop" in p2
    assert "embedbox" in p2 and "?embed=1" in p2 and "&lt;iframe" in p2
    # agents bekommen auch mit embed=1 das JSON (embed ist ein browser-rendering)
    st, hd, body = srv.get(f"/pics/g/{g['id']}?embed=1", headers=AGENT)
    assert hd["Content-Type"].startswith("application/json")


def test_url_import_into_gallery(tmp_path):
    """1.34.0 stage-1 google-photos plan: POST /pics/g/<gid>?url= imports a
    remote image through the normal pics pipeline."""
    s = Server(tmp_path / "imp", env_extra={"THROWAWAY_ALLOW_PRIVATE_FETCH": "1"})
    try:
        # quelle: ein throway-file das als bild-url dient (loopback erlaubt)
        st, _, body = s.post("/?name=remote.jpg", data=jpeg(900, 600),
                             headers={"User-Agent": "curl/8.0",
                                      "Content-Type": "image/jpeg"})
        src_url = f"{s.base}/{json.loads(body)['id']}"
        _, g = create(s)
        st, m = s.jpost(f"/pics/g/{g['id']}?url=" + src_url.replace(":", "%3A").replace("/", "%2F"),
                        data=b"", headers={"User-Agent": "curl/8.0"})
        assert st == 200, m
        assert m["size"] > 0 and m["content_type"].startswith("image/")
        st, _, listing = s.get(f"/pics/g/{g['id']}", headers=AGENT)
        assert len(json.loads(listing)["images"]) == 1
        # nicht-bild-url -> 400
        st, _, body = s.post("/?name=nope.txt", data=b"text",
                             headers={"User-Agent": "curl/8.0",
                                      "Content-Type": "text/plain"})
        txt_url = f"{s.base}/{json.loads(body)['id']}"
        st, err = s.jpost(f"/pics/g/{g['id']}?url=" + txt_url.replace(":", "%3A").replace("/", "%2F"),
                          data=b"", headers={"User-Agent": "curl/8.0"})
        assert st == 400 and "not an image" in err["error"]
    finally:
        s.stop()


def test_url_import_blocks_private_hosts_by_default(srv):
    """SSRF guard stays on without the env escape (production default)."""
    _, g = create(srv)
    st, err = srv.jpost(f"/pics/g/{g['id']}?url=http%3A%2F%2F127.0.0.1%2Fx.jpg",
                        data=b"", headers=AGENT)
    assert st == 400 and "blocked" in err["error"]


def test_lh3_scrape_and_hq_helpers():
    """1.34.0: Google Photos share-page scraping (pure functions)."""
    from throway import pics as P
    html = """
    ["https://lh3.googleusercontent.com/pw/AB1=a1b2=w1600-h900-k-no",
     "https://lh3.googleusercontent.com/pw/AB1=a1b2",
     "https://lh3.googleusercontent.com/pw/AB1=a1b2=w96-h72-no",
     "https://lh3.googleusercontent.com/ogw/default-user=s83",
     "https://lh3.googleusercontent.com/a/ACg8ico=s40-p-no",
     "https://lh3.googleusercontent.com/gg/XYZ=s1200",
     "https://example.com/not-google.jpg"]
    """
    urls = P._scrape_lh3(html)
    # pro foto-basis genau EINE (die groesste) variante, keine avatare/icons
    assert urls == ["https://lh3.googleusercontent.com/pw/AB1=a1b2",
                    "https://lh3.googleusercontent.com/gg/XYZ=s1200"]
    hq = P._lh3_hq(urls[0])
    assert hq.endswith("=w2048-h2048-k-no")
    assert "=a1b2=" in hq                       # auth-tail bleibt
    assert P._lh3_hq("https://lh3.googleusercontent.com/pw/AB") == \
        "https://lh3.googleusercontent.com/pw/AB=w2048-h2048-k-no"
    assert P._scrape_lh3("leer") == []


def test_duplicate_detection(srv):
    """1.37.0: same image twice -> second is idempotent (same id, duplicate
    flag), gallery count stays 1. Different images both store."""
    _, g = create(srv)
    img = jpeg(500, 400, (9, 99, 199))
    m1 = up(srv, g["id"], img, "a.jpg")
    st, m2 = srv.jpost(f"/pics/g/{g['id']}?name=a-kopie.jpg", data=img,
                       headers={"Content-Type": "application/octet-stream",
                                **AGENT})
    assert st == 200
    assert m2["id"] == m1["id"]                 # same picture back
    assert m2.get("duplicate") is True
    st, _, body = srv.get(f"/pics/g/{g['id']}", headers=AGENT)
    assert json.loads(body)["gallery"]["images"] == 1
    # different image stores normally
    m3 = up(srv, g["id"], jpeg(500, 400, (1, 2, 3)), "b.jpg")
    st, _, body = srv.get(f"/pics/g/{g['id']}", headers=AGENT)
    assert json.loads(body)["gallery"]["images"] == 2
    # multipart batch with a dup inside
    from conftest import multipart
    body_mp, ctype = multipart([("x.jpg", img, "image/jpeg"),
                                ("y.jpg", img, "image/jpeg")])
    st, _, out = srv.post(f"/pics/g/{g['id']}", data=body_mp,
                          headers={"Content-Type": ctype, **AGENT})
    res = json.loads(out)
    assert res["added"] == [] and res["duplicates"] == 2


# --- 1.38.0: likes & comments ------------------------------------------------

def _mk_gal(srv, name):
    st, _, b = srv.post("/pics?create=1&name=" + name, headers=AGENT)
    assert st == 200, b
    return json.loads(b)


def _up(srv, gid, fn="a.jpg", color=(120, 40, 200)):
    st, _, b = srv.post(f"/pics/g/{gid}?name={fn}", data=jpeg(color=color),
                        headers=AGENT)
    assert st == 200, b
    return json.loads(b)


def _cform(name, text):
    return urlencode({"name": name, "text": text})


def _browser_form(extra=None):
    h = {**BROWSER, "Content-Type": "application/x-www-form-urlencoded"}
    h.update(extra or {})
    return h


def test_image_like_toggle_and_fingerprint(srv):
    g = _mk_gal(srv, "likes1")
    pid = _up(srv, g["id"])["id"]
    st, _, b = srv.post(f"/pics/i/{pid}?like=1", headers=BROWSER)
    d = json.loads(b)
    assert st == 200 and d == {"id": pid, "likes": 1, "liked": True}
    # same visitor toggles OFF
    st, _, b = srv.post(f"/pics/i/{pid}?like=1", headers=BROWSER)
    d = json.loads(b)
    assert d["likes"] == 0 and d["liked"] is False
    # a second visitor (different X-Forwarded-For) adds a like
    v2 = {**BROWSER, "X-Forwarded-For": "203.0.113.7"}
    srv.post(f"/pics/i/{pid}?like=1", headers=BROWSER)
    st, _, b = srv.post(f"/pics/i/{pid}?like=1", headers=v2)
    assert json.loads(b)["likes"] == 2
    st, _, b = srv.post(f"/pics/i/{pid}?like=1", headers=BROWSER)
    assert json.loads(b)["likes"] == 1


def test_likes_in_json_poll_and_sort(srv):
    g = _mk_gal(srv, "likes2")
    a = _up(srv, g["id"], "a.jpg", (120, 40, 200))["id"]
    bb = _up(srv, g["id"], "b.jpg", (10, 200, 30))["id"]
    for ip in ("198.51.100.1", "198.51.100.2"):
        srv.post(f"/pics/i/{a}?like=1",
                 headers={**BROWSER, "X-Forwarded-For": ip})
    srv.post(f"/pics/i/{bb}?like=1",
             headers={**BROWSER, "X-Forwarded-For": "198.51.100.3"})
    st, _, body = srv.get(f"/pics/g/{g['id']}", headers=AGENT)
    d = json.loads(body)
    lm = {i["id"]: i["likes"] for i in d["images"]}
    assert lm == {a: 2, bb: 1}
    assert d["images"][0]["id"] == bb            # default: newest first
    st, _, body = srv.get(f"/pics/g/{g['id']}?sort=likes", headers=AGENT)
    d2 = json.loads(body)
    assert d2["images"][0]["id"] == a            # like ranking wins
    st, _, body = srv.get(f"/pics/g/{g['id']}?likes=1", headers=BROWSER)
    p = json.loads(body)
    assert p["likes"] == {a: 2, bb: 1}
    assert p["cl"] == {} and p["comments"] == 0
    # social how-to is advertised to agents
    assert "like_image" in d["social"] and "comment" in d["social"]


def test_comment_browser_form_and_redirect(srv):
    g = _mk_gal(srv, "cmt1")
    st, hd, _ = srv.post(f"/pics/g/{g['id']}?comment=1",
                         data=_cform("Anna", "War schön!"),
                         headers=_browser_form())
    assert st == 303 and hd.get("Location", "").endswith("#comments")
    st, _, body = srv.get(f"/pics/g/{g['id']}", headers=AGENT)
    d = json.loads(body)
    c = d["comments"][0]
    assert c["name"] == "Anna" and c["text"] == "War schön!" and c["likes"] == 0


def test_comment_json_body_and_defaults(srv):
    g = _mk_gal(srv, "cmt2")
    st, _, b = srv.post(f"/pics/g/{g['id']}?comment=1",
                        data=json.dumps({"text": "hi"}).encode(),
                        headers={**AGENT, "Content-Type": "application/json"})
    assert st == 200
    c = json.loads(b)
    assert c["name"] == "Gast" and c["text"] == "hi"
    # empty text rejected
    st, _, b = srv.post(f"/pics/g/{g['id']}?comment=1",
                        data=json.dumps({"name": "x", "text": "  "}).encode(),
                        headers={**AGENT, "Content-Type": "application/json"})
    assert st == 400


def test_comment_html_escaped(srv):
    g = _mk_gal(srv, "cmtx")
    srv.post(f"/pics/g/{g['id']}?comment=1",
             data=_cform("<b>X", "hi <script>alert(1)</script>\nsecond"),
             headers=_browser_form())
    for view in (f"/pics/g/{g['id']}", f"/pics/g/{g['id']}?embed=1"):
        st, _, html = srv.get(view, headers=BROWSER)
        h = html.decode()
        assert st == 200
        assert "&lt;b&gt;X" in h, view
        assert "<script>alert" not in h, view
        assert "&lt;script&gt;" in h, view


def test_comment_like_and_admin_delete(srv):
    g = _mk_gal(srv, "cl")
    srv.post(f"/pics/g/{g['id']}?comment=1", data=_cform("N", "t"),
             headers=_browser_form())
    st, _, body = srv.get(f"/pics/g/{g['id']}", headers=AGENT)
    cid = json.loads(body)["comments"][0]["id"]
    st, _, b = srv.post(f"/pics/g/{g['id']}?clike={cid}", headers=BROWSER)
    assert json.loads(b) == {"id": cid, "likes": 1, "liked": True}
    st, _, b = srv.post(f"/pics/g/{g['id']}?clike={cid}",
                        headers={**BROWSER, "X-Forwarded-For": "203.0.113.9"})
    assert json.loads(b)["likes"] == 2
    st, _, b = srv.post(f"/pics/g/{g['id']}?clike=deadbeef", headers=BROWSER)
    assert st == 404
    # admin moderation: cdel removes the comment
    st, _, _ = srv.post(f"/pics/g/{g['id']}/{SUPER}",
                        data=urlencode({"id": cid, "action": "cdel", "p": "1"}),
                        headers=_browser_form())
    assert st == 303
    st, _, body = srv.get(f"/pics/g/{g['id']}", headers=AGENT)
    assert json.loads(body)["comments"] == []


def test_comment_cooldown(srv):
    g = _mk_gal(srv, "cool")
    st, _, _ = srv.post(f"/pics/g/{g['id']}?comment=1",
                        data=_cform("a", "eins"), headers=_browser_form())
    assert st == 303
    st, _, b = srv.post(f"/pics/g/{g['id']}?comment=1",
                        data=_cform("a", "zwei"), headers=_browser_form())
    assert st == 429
    # a different visitor is not throttled
    st, _, _ = srv.post(f"/pics/g/{g['id']}?comment=1",
                        data=_cform("b", "drei"),
                        headers=_browser_form({"X-Forwarded-For": "203.0.113.4"}))
    assert st == 303


@pytest.fixture
def cmt_srv(tmp_path):
    s = Server(tmp_path / "cmt",
               env_extra={"THROWAWAY_PICS_MAX_COMMENTS": "2",
                          "THROWAWAY_PICS_COMMENT_COOLDOWN": "0"})
    yield s
    s.stop()


def test_comment_caps_and_length_trims(cmt_srv):
    g = _mk_gal(cmt_srv, "caps")
    long_text = "x" * 600
    st, _, _ = cmt_srv.post(f"/pics/g/{g['id']}?comment=1",
                            data=_cform("n" * 60, long_text),
                            headers=_browser_form())
    assert st == 303
    st, _, b = cmt_srv.post(f"/pics/g/{g['id']}?comment=1",
                            data=_cform("zwei", "ok"),
                            headers=_browser_form())
    assert st == 303
    st, _, b = cmt_srv.post(f"/pics/g/{g['id']}?comment=1",
                            data=_cform("drei", "nope"),
                            headers=_browser_form())
    assert st == 400 and "limit" in json.loads(b)["error"]
    st, _, body = cmt_srv.get(f"/pics/g/{g['id']}", headers=AGENT)
    cmts = json.loads(body)["comments"]
    assert len(cmts) == 2
    assert cmts[0]["name"] == "zwei"              # newest first
    assert len(cmts[1]["name"]) == 40             # trimmed to the cap
    assert len(cmts[1]["text"]) == 500


def test_like_hidden_and_missing_404(srv):
    g = _mk_gal(srv, "hid")
    pid = _up(srv, g["id"])["id"]
    st, _, _ = srv.post(f"/pics/g/{g['id']}/{SUPER}",
                        data=urlencode({"id": pid, "action": "hide", "p": "1"}),
                        headers=_browser_form())
    assert st == 303
    st, _, _ = srv.post(f"/pics/i/{pid}?like=1", headers=BROWSER)
    assert st == 404
    st, _, _ = srv.post("/pics/i/deadbeef12?like=1", headers=BROWSER)
    assert st == 404


def test_embed_and_page_carry_social_ui(srv):
    g = _mk_gal(srv, "ui")
    _up(srv, g["id"])
    st, _, html = srv.get(f"/pics/g/{g['id']}?embed=1&sort=likes",
                          headers=BROWSER)
    h = html.decode()
    assert st == 200
    for frag in ("class=lk", "id=comments", "id=cform", "?comment=1",
                 "sort=likes", "aria-pressed"):
        assert frag in h, frag
    st, _, html = srv.get(f"/pics/g/{g['id']}", headers=BROWSER)
    h2 = html.decode()
    assert "class=lk" in h2 and "id=cform" in h2 and "lblk" in h2
    st, _, body = srv.get("/api", headers=AGENT)
    eps = json.loads(body)["endpoints"]
    for k in ("pics_like", "pics_likes_json", "pics_comment",
              "pics_comment_like"):
        assert k in eps, k


def test_admin_json_and_page_show_comments(srv):
    g = _mk_gal(srv, "adm")
    srv.post(f"/pics/g/{g['id']}?comment=1", data=_cform("Q", "text"),
             headers=_browser_form())
    adm = f"/pics/g/{g['id']}/{SUPER}"
    st, _, body = srv.get(adm + "/json", headers=AGENT)
    d = json.loads(body)
    assert d["comments"][0]["text"] == "text"
    st, _, html = srv.get(adm, headers=BROWSER)
    assert "cdel" in html.decode()
