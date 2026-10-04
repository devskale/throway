"""index — the throway homepage (upload UI under /).

Pure presentation module (mdrender precedent, 1.51.0): page(stats)
assembles the whole homepage HTML from a flat stats dict — no I/O, no
handler, no kit. store.py gathers the engine state (pool fill, session
counters, agent description, config) and sends the result.

stats contract (all keys required):
    files_now, bytes_now     current single files in the throwaway pool
    pool_size                THROW_POOL_SIZE (bytes) — pool meter denominator
    since_files, since_bytes session counters (since server start)
    version, ttl_hours       live config values (badge + ttl defaults)
    prefix                   route prefix (PUBLIC_BASE-relative links)
    max_mb                   MAX_FILE in MB — substituted into the JS
    agent_description        plain text (the HELP-assembled agent copy);
                             page() HTML-escapes it

Everything below the interface is implementation; unit tests drive
page() directly (tests/test_index.py), behavior tests stay on HTTP.
"""
import html as _html

DROPZONE_VERSION = "5.9.3"
DROPZONE_CSS = f"https://cdn.jsdelivr.net/npm/dropzone@{DROPZONE_VERSION}/dist/min/dropzone.min.css"
DROPZONE_JS = f"https://cdn.jsdelivr.net/npm/dropzone@{DROPZONE_VERSION}/dist/min/dropzone.min.js"

# same mobile/tint metas as store._META_MOBILE (shared presentation
# constant; joins the shared-mechanics theme when candidate 4 lands)
_META_MOBILE = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<meta name=theme-color content='#2563eb'>")

_INDEX_JS = r"""(function () {
  'use strict';

  /* Works under any mount point: '' at root, '/throway' behind the proxy. */
  var PREFIX = location.pathname.replace(/\/+$/, '');
  var MAX_MB = __MAX_MB__;

  function $(id) { return document.getElementById(id); }
  var statusEl = $('status'), resultEl = $('result'), upBtn = $('up'), dirMode = $('dirMode'),
      ttlSel = $('ttlSel'), createBtn = $('create'), createText = $('createText'),
      createName = $('createName'), createTtl = $('createTtl'), createShare = $('createShare'),
      shareSel = $('shareSel'), onceSel = $('onceSel'), createOnce = $('createOnce');

  /* --- upload type tabs (segmented control) ---
     SOTA rules applied: unmistakable active state (pill + weight), instant
     switch with a stable frame, per-tab state preserved (panels only hide),
     roving-focus keyboard nav, active tab reflected in the URL hash. */
  var tabBtns = [].slice.call(document.querySelectorAll('.tabs [role=tab]'));
  var currentTab = 'files';
  function selectTab(name, focus) {
    currentTab = name;
    tabBtns.forEach(function (t) {
      var on = t.dataset.mode === name;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      var p = document.getElementById(t.getAttribute('aria-controls'));
      if (p) p.hidden = !on;
      if (on && focus) t.focus();
    });
    try { history.replaceState(null, '', '#' + name); } catch (e) { /* noop */ }
  }
  tabBtns.forEach(function (t) {
    t.addEventListener('click', function () { selectTab(t.dataset.mode); });
    t.addEventListener('keydown', function (e) {
      var i = tabBtns.indexOf(t), n = tabBtns.length, j = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % n;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + n) % n;
      if (e.key === 'Home') j = 0;
      if (e.key === 'End') j = n - 1;
      if (j !== null) { e.preventDefault(); selectTab(tabBtns[j].dataset.mode, true); }
    });
  });
  selectTab(/^#(files|text|link|gallery)$/.test(location.hash) ? location.hash.slice(1) : 'files');

  /* --- link tab: server-side URL import (POST /?url=…) --- */
  var linkUrl = $('linkUrl'), linkName = $('linkName'), linkGo = $('linkGo');
  linkGo.addEventListener('click', function () {
    var u = linkUrl.value.trim();
    if (!/^https?:\/\//i.test(u)) { setStatus('Enter a http(s) URL', true); return; }
    var q = { url: u };
    if (linkName.value.trim()) q.name = linkName.value.trim();
    resultEl.style.display = 'none';
    setStatus('Fetching…');
    linkGo.disabled = true;
    fetch(PREFIX + '/' + qs(q), { method: 'POST' })
      .then(function (r) { return r.json().catch(function () { return { error: 'HTTP ' + r.status }; }); })
      .then(function (d) {
        linkGo.disabled = false;
        if (d && d.url) { setStatus(''); showResult(d); linkUrl.value = ''; linkName.value = ''; }
        else setStatus('Error: ' + ((d && d.error) || 'import failed'), true);
      })
      .catch(function (e) { linkGo.disabled = false; setStatus('Error: ' + e, true); });
  });
  linkUrl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); linkGo.click(); }
  });

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function setStatus(msg, isErr) {
    statusEl.textContent = msg || '';
    statusEl.className = isErr ? 'err' : '';
  }
  function asObj(r) {
    if (r && typeof r === 'object') return r;
    try { return JSON.parse(r); } catch (e) { return { error: String(r) }; }
  }

  /* --- result box --- */
  function row(label, html) {
    return '<div class=row><span class=lbl>' + esc(label) + '</span>' + html + '</div>';
  }
  function filesBlock(files) {
    return '<div class=files>' + files.map(function (f) {
      return '<div>\u2022 <a href="' + esc(f.url) + '" target=_blank>' + esc(f.name) + '</a> (' + f.size + ' B)</div>';
    }).join('') + '</div>';
  }
  function showResult(d) {
    var html = '<h3>Done \u2713</h3>' + row('URL',
      '<div class=urlbox><input readonly value="' + esc(d.url) + '"><button class=btn data-copy>copy</button></div>');
    if (d.dir) {
      html += row('Dir', d.files.length + ' files \u00b7 ' + (d.expires_at ? 'expires ' + esc(d.expires_at) : 'retained \u221e')) + filesBlock(d.files);
    } else if (d.bundle) {
      html += row('Bundle', d.files.length + ' files \u00b7 ' + (d.expires_at ? 'expires ' + esc(d.expires_at) : 'retained \u221e')) + filesBlock(d.files);
    } else {
      html += row('Name', esc(d.name)) + row('Size', d.size + ' B') +
              row('Type', esc(d.content_type)) +
              row('Expires', d.expires_at ? esc(d.expires_at) : 'retained \u221e');
    }
    /* native share sheet on smartphones (WhatsApp, mail, …) when available */
    if (navigator.share) {
      html += '<div class=sharerow><button class=btn data-share>\u21d7 share link</button></div>';
    }
    resultEl.innerHTML = html;
    resultEl.style.display = 'block';
    resultEl.querySelector('[data-copy]').addEventListener('click', function () {
      navigator.clipboard.writeText(this.previousElementSibling.value);
    });
    var sh = resultEl.querySelector('[data-share]');
    if (sh) sh.addEventListener('click', function () {
      navigator.share({ url: d.url, title: (d.name || 'throway link') }).catch(function () {});
    });
  }

  /* --- dropzone --- */
  Dropzone.autoDiscover = false;
  var dz = new Dropzone('#drop', {
    url: PREFIX + '/',
    autoProcessQueue: false,
    uploadMultiple: true,      /* one POST for the whole queue -> file | bundle | dir */
    parallelUploads: 100,
    paramName: 'f',
    maxFilesize: MAX_MB,
    createImageThumbnails: false,
    clickable: true,
    previewTemplate: [
      '<div class="dz-preview dz-file-preview">',
      '  <span class="dz-filename" data-dz-name></span>',
      '  <span class="dz-size" data-dz-size></span>',
      '  <span class="dz-progress"><i data-dz-uploadprogress></i></span>',
      '  <span class="dz-error-msg" data-dz-errormessage></span>',
      '  <a class="dz-remove" href="javascript:undefined" data-dz-remove>remove</a>',
      '</div>'
    ].join('')
  });

  function qs(obj) {
    var p = [];
    for (var k in obj) if (obj[k]) p.push(encodeURIComponent(k) + '=' + encodeURIComponent(obj[k]));
    return p.length ? '?' + p.join('&') : '';
  }

  upBtn.addEventListener('click', function () {
    if (!dz.files.length) { setStatus('Choose at least one file', true); return; }
    var q = {};
    if (dirMode.checked) q.dir = 1;
    if (shareSel.value.trim()) q.share = shareSel.value.trim();
    if (onceSel.checked) q.once = 1;
    if (ttlSel.value) q.ttl = ttlSel.value;
    dz.options.url = PREFIX + '/' + qs(q);
    resultEl.style.display = 'none';
    dz.processQueue();
  });

  createBtn.addEventListener('click', function () {
    var text = createText.value;
    if (!text.trim()) { setStatus('Enter some text first', true); return; }
    var q = {};
    if (createName.value.trim()) q.name = createName.value.trim();
    if (createShare.value.trim()) q.share = createShare.value.trim();
    if (createOnce.checked) q.once = 1;
    if (createTtl.value) q.ttl = createTtl.value;
    resultEl.style.display = 'none';
    setStatus('Creating…');
    createBtn.disabled = true;
    fetch(PREFIX + '/' + qs(q), { method: 'POST', body: text })
      .then(function (r) { return r.json().catch(function () { return { error: 'HTTP ' + r.status }; }); })
      .then(function (d) {
        createBtn.disabled = false;
        if (d && d.url) { setStatus(''); showResult(d); createText.value = ''; createName.value = ''; }
        else setStatus('Error: ' + ((d && d.error) || 'create failed'), true);
      })
      .catch(function (e) { createBtn.disabled = false; setStatus('Error: ' + e, true); });
  });

  /* --- gallery tab: create-or-get + client-side downscale + per-file rows --- */
  var galName = $('galName'), galListed = $('galListed'), galFile = $('galFile'),
      galDrop = $('galDrop'), galRows = $('galRows'), galResult = $('galResult');
  var gal = { gid: null, queue: [], busy: false };

  galDrop.addEventListener('click', function () { galFile.click(); });
  ['dragover', 'dragenter'].forEach(function (ev) {
    galDrop.addEventListener(ev, function (e) { e.preventDefault(); galDrop.classList.add('hover'); });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    galDrop.addEventListener(ev, function (e) { e.preventDefault(); galDrop.classList.remove('hover'); });
  });
  galDrop.addEventListener('drop', function (e) {
    e.preventDefault(); galDrop.classList.remove('hover');
    var fl = e.dataTransfer.files, n = 0;
    for (var i = 0; i < fl.length; i++) {
      if (fl[i].type && fl[i].type.indexOf('image/') !== 0) continue;
      gal.queue.push(fl[i]); galRow(fl[i].name, 'queued'); n++;
    }
    if (n) { pumpGallery(); return; }
    var u = (e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain') || '').trim();
    if (/^https?:\/\//i.test(u)) {
      var r0 = galRow(u.slice(0, 60), 'lade von url\u2026');
      if (!gal.gid) {
        /* gallery not created yet: create empty first, then import */
        var q0 = 'create=1';
        if (galName.value.trim()) q0 += '&name=' + encodeURIComponent(galName.value.trim());
        if (galListed.checked) q0 += '&listed=1';
        fetch(PREFIX + '/pics?' + q0, { method: 'POST', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d && d.id && d.token) { gal.gid = d.id; showGalCreated(d); importUrl(r0, u); }
            else { galSt(r0, 'fehler: galerie anlegen', 'err'); }
          });
      } else importUrl(r0, u);
    }
  });
  function importUrl(row, u) {
    fetch(PREFIX + '/pics/g/' + gal.gid + '?url=' + encodeURIComponent(u),
          { method: 'POST', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (x) {
        if (x.ok && x.d.id) galSt(row, 'done \u2713', 'ok');
        else galSt(row, 'fehler: ' + ((x.d && x.d.error) || 'unbekannt'), 'err');
      })
      .catch(function (e) { galSt(row, 'fehler: ' + e, 'err'); });
  }
  galFile.addEventListener('change', function () { queueImages(this.files); this.value = ''; });

  /* Create an empty gallery now — no images needed yet (name reserves the
     key create-or-get; admin link is shown exactly once). */
  $('galCreate').addEventListener('click', function () {
    var q = 'create=1';
    if (galName.value.trim()) q += '&name=' + encodeURIComponent(galName.value.trim());
    if (galListed.checked) q += '&listed=1';
    this.disabled = true;
    var btn = this;
    fetch(PREFIX + '/pics?' + q, { method: 'POST', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) {
        btn.disabled = false;
        if (d && d.id && d.token) {                 /* freshly created */
          gal.gid = d.id;
          showGalCreated(d);
        } else if (d && d.id && d.existed) {        /* create-or-get hit */
          galResult.style.display = 'block';
          galResult.innerHTML = '<h3>Gallery exists</h3>'
            + row('Gallery', '<div class=urlbox><input readonly value="' + esc(d.url) + '"><button class=btn data-copy>copy</button></div>')
            + '<div class=hint>This name is taken — the admin link was shown only at creation.</div>';
          [].forEach.call(galResult.querySelectorAll('[data-copy]'), function (b) {
            b.addEventListener('click', function () {
              navigator.clipboard.writeText(this.previousElementSibling.value);
              this.textContent = 'copied \u2713';
            });
          });
        } else {
          setStatus('Error: ' + ((d && d.error) || 'create failed'), true);
        }
      })
      .catch(function (e) { btn.disabled = false; setStatus('Error: ' + e, true); });
  });

  function galRow(name, st, cls) {
    var d = document.createElement('div');
    d.className = 'grow';
    d.innerHTML = '<span class=n>' + esc(name) + '</span><span class="st ' + (cls || '') + '">' + esc(st) + '</span>';
    galRows.appendChild(d);
    return d;
  }
  function galSt(row, st, cls) {
    var s = row.querySelector('.st');
    s.textContent = st;
    s.className = 'st ' + (cls || '');
  }
  function galRetry(row, f) {
    var b = document.createElement('button');
    b.type = 'button'; b.textContent = 'retry';
    b.addEventListener('click', function () {
      b.remove();
      gal.queue.push(f);
      if (!gal.busy) pumpGallery();
    });
    row.appendChild(b);
  }

  function queueImages(list) {
    var added = 0;
    for (var i = 0; i < list.length; i++) {
      var f = list[i];
      if (f.type && f.type.indexOf('image/') !== 0) {
        galRow(f.name, 'skipped — not an image', 'err');
        continue;
      }
      gal.queue.push(f);
      galRow(f.name, 'queued');
      added++;
    }
    if (added) pumpGallery();
  }

  /* Downscale in the browser BEFORE upload (saves phone-photo bandwidth
     ~10-20x). EXIF orientation kept via createImageBitmap with an <img>
     fallback. GIFs pass through untouched (animation survives). */
  function shrink(f) {
    return new Promise(function (res) {
      if (!f.type || f.type === 'image/gif' || f.type.indexOf('image/') !== 0) return res(f);
      var done = function (bmp) {
        try {
          var M = 2048, TARGET = 1048576;   /* HQ: 2048px, q0.90, cap ~1MB */
          var w = bmp.width, h = bmp.height;
          if (Math.max(w, h) <= M && f.size <= TARGET) return res(f);
          var s = Math.min(1, M / Math.max(w, h));
          w = Math.round(w * s); h = Math.round(h * s);
          var c = document.createElement('canvas');
          c.width = w; c.height = h;
          c.getContext('2d').drawImage(bmp, 0, 0, w, h);
          var q = 0.90;
          var finish = function (b) {
            if (!b) return res(f);
            var out = new File([b], f.name.replace(/\.[^.]+$/, '') + '.webp',
                               { type: b.type || 'image/webp', lastModified: Date.now() });
            res(out.size < f.size ? out : f);
          };
          var attempt = function (b) {
            if (b && b.size <= TARGET) return finish(b);
            if (q <= 0.70) return finish(b);
            q -= 0.05;
            c.toBlob(attempt, 'image/webp', q);
          };
          c.toBlob(attempt, 'image/webp', q);
        } catch (e) { res(f); }
      };
      if (window.createImageBitmap) {
        createImageBitmap(f, { imageOrientation: 'from-image' }).then(done,
          function () {
            var img = new Image();
            var url = URL.createObjectURL(f);
            img.onload = function () { URL.revokeObjectURL(url); done(img); };
            img.onerror = function () { URL.revokeObjectURL(url); res(f); };
            img.src = url;
          });
      } else {
        var img = new Image();
        var url = URL.createObjectURL(f);
        img.onload = function () { URL.revokeObjectURL(url); done(img); };
        img.onerror = function () { URL.revokeObjectURL(url); res(f); };
        img.src = url;
      }
    });
  }

  function showGalCreated(d) {
    galResult.style.display = 'block';
    galResult.innerHTML = '<h3>Gallery created \u2713</h3>'
      + row('Gallery', '<div class=urlbox><input readonly value="' + esc(d.url) + '"><button class=btn data-copy>copy</button></div>')
      + row('Admin link', '<div class=urlbox><input readonly value="' + esc(d.admin_url) + '"><button class=btn data-copy>copy</button></div>')
      + '<div class=hint>Copy the admin link now — it is shown only once (hide / delete / reorder live there).</div>'
      + '<div class=sharerow><a class=btn href="' + esc(d.url) + '" target=_blank>open gallery</a></div>';
    [].forEach.call(galResult.querySelectorAll('[data-copy]'), function (b) {
      b.addEventListener('click', function () {
        navigator.clipboard.writeText(this.previousElementSibling.value);
        this.textContent = 'copied ✓';
      });
    });
  }

  function pumpGallery() {
    if (gal.busy) return;
    gal.busy = true;
    var dups = 0;
    (async function () {
      while (gal.queue.length) {
        var f = gal.queue.shift();
        var r0 = galRow(f.name, 'resizing…');
        try {
          var out = await shrink(f);
          if (!gal.gid) {
            galSt(r0, 'creating gallery…');
            var q = 'create=1';
            if (galName.value.trim()) q += '&name=' + encodeURIComponent(galName.value.trim());
            if (galListed.checked) q += '&listed=1';
            var r = await fetch(PREFIX + '/pics?' + q, { method: 'POST', headers: { 'Accept': 'application/json' } });
            var d = await r.json().catch(function () { return {}; });
            if (!d.id) throw new Error(d.error || 'create failed');
            gal.gid = d.id;
            showGalCreated(d);
          }
          galSt(r0, 'uploading…');
          var ok = false;
          for (var a = 0; a < 3; a++) {
            var r2 = await fetch(PREFIX + '/pics/g/' + gal.gid + '?name=' + encodeURIComponent(out.name),
                                 { method: 'POST', body: out });
            if (r2.ok) {
              var dj = await r2.json().catch(function () { return {}; });
              if (dj.duplicate) { dups++; galSt(r0, 'duplikat \u2014 \u00fcbersprungen', ''); }
              ok = true; break;
            }
            if (r2.status === 429) { galSt(r0, 'rate-limited, waiting…'); await sleep(61000); continue; }
            await sleep(1500);
          }
          if (ok && !dups) galSt(r0, 'done ✓', 'ok');
          else { galSt(r0, 'failed', 'err'); galRetry(r0, f); }
        } catch (e) {
          galSt(r0, 'failed — ' + (e && e.message ? e.message : 'error'), 'err');
          galRetry(r0, f);
        }
      }
      gal.busy = false;
    })();
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  dz.on('sendingmultiple', function () {
    upBtn.disabled = true;
    setStatus('Uploading\u2026');
  });
  dz.on('successmultiple', function (files, resp) {
    upBtn.disabled = false;
    var d = asObj(resp);
    if (d && d.url) { setStatus(''); showResult(d); dz.removeAllFiles(true); }
    else { setStatus('Error: ' + ((d && d.error) || 'upload failed'), true); }
  });
  dz.on('errormultiple', function (files, resp) {
    upBtn.disabled = false;
    var d = asObj(resp);
    setStatus('Error: ' + ((d && d.error) || 'upload failed'), true);
  });
  dz.on('error', function (file, msg) {
    upBtn.disabled = false;
    setStatus('Error: ' + (file.status === Dropzone.CANCELED ? 'canceled' : msg), true);
  });

  /* --- paste-to-upload (Ctrl+V) --- */
  /* Listens on the whole page. Images go to the gallery queue when the
     gallery tab is active, otherwise into the files dropzone. Pastes
     inside inputs/textareas are never hijacked. */
  function handlePaste(e) {
    var items = (e.clipboardData || window.clipboardData);
    if (!items || !items.items) return;
    var t = e.target;
    var inField = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA');
    var added = 0;
    for (var i = 0; i < items.items.length; i++) {
      var it = items.items[i];
      if (it.kind !== 'file') continue;
      var f = it.getAsFile();
      if (!f) continue;
      if (f.type && f.type.indexOf('image/') !== 0) continue;  /* only images */
      var base = (f.name || 'pasted').replace(/\.[^.]+$/, '');
      var ext = (f.type || 'image/png').split('/')[1] || 'png';
      var name = base + '-' + Date.now() + '.' + ext;
      if (currentTab === 'gallery') {
        gal.queue.push(f);
        galRow(name, 'queued');
        added++;
        continue;
      }
      if (inField) continue;   /* never steal pastes inside form fields */
      var blob = new Blob([f], { type: f.type });
      blob.name = name;
      blob.lastModified = Date.now();
      /* Dropzone expects a File; wrap the blob with a name so it's accepted. */
      try {
        blob = new File([f], name, { type: f.type, lastModified: Date.now() });
      } catch (err) { /* older browsers: keep the named blob */ }
      dz.addFile(blob);
      added++;
    }
    if (added) {
      e.preventDefault();
      if (currentTab === 'gallery') pumpGallery();
      else setStatus('Pasted ' + added + ' image' + (added > 1 ? 's' : '') + ' — click Upload');
      return;
    }
    /* pasted a URL as text while the gallery tab is active: import it */
    if (currentTab === 'gallery' && !inField) {
      var txt = (e.clipboardData || window.clipboardData || {}).getData ?
        (e.clipboardData || window.clipboardData).getData('text/plain') : '';
      txt = (txt || '').trim();
      if (/^https?:\/\//i.test(txt)) {
        e.preventDefault();
        var row = galRow(txt.slice(0, 60), 'lade von url…');
        var go = function () {
          fetch(PREFIX + '/pics/g/' + gal.gid + '?url=' + encodeURIComponent(txt),
                { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (x) {
              if (x.ok) galSt(row, 'done ✓ (' + ((x.d.imported != null) ? x.d.imported + ' bilder' : '1 bild') + ')', 'ok');
              else galSt(row, 'fehler: ' + ((x.d && x.d.error) || 'unbekannt'), 'err');
            })
            .catch(function (er) { galSt(row, 'fehler: ' + er, 'err'); });
        };
        if (gal.gid) { go(); }
        else {
          var q0 = 'create=1';
          if (galName.value.trim()) q0 += '&name=' + encodeURIComponent(galName.value.trim());
          if (galListed.checked) q0 += '&listed=1';
          fetch(PREFIX + '/pics?' + q0, { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d && d.id && d.token) { gal.gid = d.id; showGalCreated(d); go(); }
              else galSt(row, 'fehler: galerie anlegen', 'err');
            });
        }
      }
    }
  }
  document.addEventListener('paste', handlePaste);
})();
"""

_INDEX_CSS = (
         ":root{--bg:#ffffff;--card:#fafafa;--card2:#f4f4f5;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}"
         "*{box-sizing:border-box}"
         "body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--ink);line-height:1.55;min-height:100vh}"
         "main{max-width:840px;margin:0 auto;padding:3rem 1.5rem 5rem}"
         "header{display:flex;align-items:baseline;gap:.75rem;margin-bottom:.25rem}"
         "h1{font-size:2rem;margin:0;letter-spacing:-.02em}"
         "h1 .dot{color:var(--accent)}"
         ".version{font-size:.8rem;color:var(--muted);background:var(--card2);border:1px solid var(--line);padding:.15rem .5rem;border-radius:999px}"
         "p.lede{color:var(--muted);max-width:60ch;margin:.5rem 0 1.5rem}"
         "ul.feats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.6rem;list-style:none;padding:0;margin:0 0 1.5rem}"
         "ul.feats li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7rem .9rem;font-size:.9rem}"
         "ul.feats li b{color:var(--accent)}"
         "ul.feats li small{display:block;color:var(--muted);margin-top:.15rem}"
                  "#drop{border:2px dashed #d1d5db;border-radius:14px;padding:2rem 1.5rem;text-align:center;cursor:pointer;transition:border-color .15s,background .15s;background:var(--card);margin-bottom:.8rem}"
         "#drop:hover,#drop.dz-drag-hover{border-color:var(--accent);background:#eff6ff}"
         "#drop .big{font-size:1.05rem;font-weight:600}"
         "#drop .sub{color:var(--muted);font-size:.85rem;margin-top:.2rem}"
         ".dz-preview{display:flex;align-items:center;gap:.6rem;text-align:left;background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:.35rem .6rem;margin:.35rem .2rem 0;font-size:.85rem}"
         ".dz-preview .dz-filename{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}"
         ".dz-preview .dz-size{color:var(--muted);font-size:.75rem;white-space:nowrap}"
         ".dz-preview .dz-progress{position:relative;height:3px;width:90px;background:#e5e7eb;border-radius:999px;overflow:hidden}"
         ".dz-preview .dz-progress i{display:block;height:100%;width:0;background:var(--accent);border-radius:999px}"
         ".dz-preview .dz-error-msg{color:#dc2626;font-size:.75rem;display:none}"
         ".dz-preview.dz-error{border-color:#fecaca;background:#fef2f2}"
         ".dz-preview.dz-error .dz-error-msg{display:block}"
         ".dz-preview .dz-remove{color:var(--muted);font-size:.75rem;text-decoration:none;white-space:nowrap}"
         ".dz-preview .dz-remove:hover{color:#dc2626}"
         ".controls{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}"
         "label.mode{display:flex;align-items:center;gap:.4rem;font-size:.85rem;color:var(--muted);cursor:pointer}"
         "label.mode input{margin:0;width:17px;height:17px}"
         "label.mode select{background:var(--card2);border:1px solid var(--line);border-radius:6px;padding:.2rem .4rem;font-size:.85rem;color:var(--ink)}"
         ".shareline{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-top:.6rem}"
         ".shareline input{flex:1;min-width:160px;background:#fff;border:1px solid var(--line);border-radius:8px;padding:.5rem .6rem;font-size:.85rem;color:var(--ink)}"
         ".shareline .hint{font-size:.75rem;color:var(--muted);flex-basis:100%;margin-top:-.15rem}"
         "button.plus{width:42px;height:42px;padding:0;font-size:1.4rem;line-height:1;border-radius:8px;background:var(--card2);color:var(--accent);border:1px solid var(--line);font-weight:600}"
         "button.plus:hover{background:var(--accent);color:#fff;border-color:var(--accent)}"
         "#createBox{margin-top:.8rem;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.8rem .9rem}"
         "#createText{width:100%;padding:.9rem 1rem;border:1px solid var(--line);border-radius:10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.95rem;line-height:1.6;resize:vertical;min-height:220px;color:var(--ink);background:#fff;transition:border-color .15s,box-shadow .15s}"
         "#createText:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.15)}"
         "#createText::placeholder{color:var(--muted)}"
         ".createbar{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-top:.6rem}"
         ".createbar input{background:#fff;border:1px solid var(--line);border-radius:8px;padding:.5rem .6rem;font-size:.85rem;color:var(--ink)}"
         "button,.btn{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:.6rem 1.2rem;font-size:.95rem;font-weight:600;cursor:pointer;transition:background .15s;touch-action:manipulation}"
         "button:hover,.btn:hover{background:#1d4ed8}"
         "button:active{transform:translateY(1px)}"
         "button:disabled{opacity:.5;cursor:not-allowed}"
         "#status{margin:.8rem 0;font-size:.9rem}"
         "#status.err{color:#dc2626}"
         "#result{margin:1rem 0;padding:1rem 1.2rem;border-radius:12px;background:#eff6ff;border:1px solid #bfdbfe;display:none}"
         "#result h3{margin:.2rem 0 .6rem;font-size:1.05rem}"
         "#result .row{padding:.25rem 0;font-size:.9rem}"
         "#result .lbl{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;margin-right:.5rem}"
         "#result a{color:var(--accent);word-break:break-all}"
         "#result .urlbox{display:flex;gap:.4rem;align-items:center;background:#fff;border:1px solid var(--line);border-radius:8px;padding:.4rem .6rem;margin:.3rem 0}"
         "#result .urlbox input{flex:1;background:none;border:0;color:var(--ink);font-size:.85rem;font-family:ui-monospace,monospace;outline:none}"
         "#result .urlbox .btn{flex:none;min-height:38px;padding:.35rem .8rem;font-size:.85rem}"
         ".sharerow{margin-top:.4rem}"
         "#result .files{font-size:.85rem;color:var(--muted)}"
         "#result .files div{padding:.15rem 0}"
         "#result .files a{color:var(--ink)}"
         "#result .files a:hover{color:var(--accent)}"
         ".stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin:1.4rem 0}"
         ".stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7rem .9rem}"
         ".stat b{font-size:1.15rem;display:block}"
         ".stat span{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}"
         ".stat .sub{font-size:.9rem;color:var(--ink);margin:.15rem 0}"
         ".stat .when{font-size:.7rem;color:var(--accent);margin-top:.2rem;text-transform:uppercase;letter-spacing:.04em}"
         ".meter{height:6px;background:#e5e7eb;border-radius:999px;overflow:hidden;margin-top:.4rem}"
         ".meter i{display:block;height:100%;background:var(--accent);border-radius:999px}"
         "nav.links{display:flex;gap:1.2rem;margin-top:2rem;font-size:.88rem;flex-wrap:wrap}"
         ".uploader{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:.9rem;margin-bottom:.8rem}"
         ".tabs{display:flex;gap:.25rem;background:var(--card2);border-radius:10px;padding:.25rem;width:max-content;max-width:100%;overflow-x:auto}"
         ".tabs [role=tab]{border:0;background:transparent;color:var(--muted);font-weight:600;font-size:.9rem;padding:.45rem .95rem;border-radius:8px;cursor:pointer;min-height:40px;white-space:nowrap}"
         ".tabs [role=tab]:hover{color:var(--ink)}"
         ".tabs [role=tab][aria-selected=true]{background:#fff;color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.08)}"
         ".tabs [role=tab]:focus-visible{outline:2px solid var(--accent);outline-offset:1px}"
         ".panel{padding-top:.7rem}"
         ".panel[hidden]{display:none}"
         ".limits{color:var(--muted);font-size:.78rem;margin:.5rem 0 0}"
         ".linkrow{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}"
         ".linkrow input{flex:1;min-width:200px;min-height:44px;border:1px solid var(--line);border-radius:8px;padding:.4rem .8rem;font-size:1rem}"
         ".linkrow button,.galbar button{min-height:44px;background:var(--accent);color:#fff;border:0;border-radius:8px;font-weight:600;padding:.5rem 1.1rem;cursor:pointer}"
         ".galbar{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.55rem}"
         ".galbar input[type=text]{flex:1;min-width:180px;min-height:44px;border:1px solid var(--line);border-radius:8px;padding:.4rem .8rem;font-size:1rem}"
         ".galbar label{color:var(--muted);font-size:.9rem;display:flex;gap:.35rem;align-items:center}"
         ".minidrop{border:2px dashed #d1d5db;border-radius:10px;padding:1.4rem 1rem;text-align:center;cursor:pointer;color:var(--muted);background:#fff;font-size:.95rem;transition:border-color .15s,background .15s;margin-bottom:.6rem}"
         ".minidrop:hover,.minidrop.hover{border-color:var(--accent);background:#eff6ff;color:var(--accent)}"
         ".minidrop:focus-within{outline:2px solid var(--accent);outline-offset:2px}"
         ".minidrop .big{font-weight:600;font-size:1rem;color:var(--ink)}"
         ".minidrop .sub{font-size:.83rem;margin-top:.15rem}"
         ".srinput{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;clip:rect(0 0 0 0)}"
         ".grow{display:flex;justify-content:space-between;gap:.6rem;align-items:center;background:#fff;border:1px solid var(--line);border-radius:8px;padding:.35rem .6rem;margin-top:.35rem;font-size:.85rem}"
         ".grow .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
         ".grow .st{color:var(--muted);white-space:nowrap}"
         ".grow .st.ok{color:#15803d}"
         ".grow .st.err{color:#dc2626}"
         ".grow button{min-height:32px;font-size:.78rem;padding:.15rem .6rem;border:1px solid var(--line);border-radius:6px;background:#fff;cursor:pointer}"
         "nav.links a{color:var(--muted);text-decoration:none;display:inline-flex;align-items:center;min-height:44px}"
         "nav.links a:hover{color:var(--accent)}"
         "details.agents{margin-top:1.5rem;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7rem 1rem}"
         "details.agents summary{cursor:pointer;font-weight:600;color:var(--accent)}"
         "details.agents pre{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12px;line-height:1.5;color:var(--muted);margin:.6rem 0 0;padding-top:.6rem;border-top:1px solid var(--line)}"
         "@media(max-width:560px){main{padding:1.6rem 1rem 4rem}"
         "h1{font-size:1.6rem}"
         "#drop{padding:1.4rem 1rem}"
         ".controls{flex-direction:column;align-items:stretch}"
         ".controls .sp{display:none}"
         "button#up{width:100%;min-height:46px;font-size:1rem}"
         "label.mode{justify-content:center;padding:.45rem 0;font-size:.95rem}"
         ".createbar{flex-direction:column;align-items:stretch}"
         ".createbar input{width:100%}"
         ".createbar button{width:100%;min-height:46px;font-size:1rem}"
         ".tabs{width:100%}"
         ".tabs [role=tab]{flex:1;padding:.45rem .3rem;font-size:.85rem}"
         ".linkrow input,.galbar input[type=text]{width:100%}"
         ".linkrow button,.galbar button{width:100%;min-height:46px;font-size:1rem}"
         "#result{padding:.8rem .9rem}"
         "#result .urlbox input{font-size:16px}"
         ".sharerow .btn{width:100%;min-height:46px;font-size:1rem}}"
)

def _fmt_size(n):
    """Format bytes with a sensible unit: B, kB, MB, GB (own copy, like
    pics._fmt — presentation formatter per module)."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} kB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def page(stats):
    """Render the homepage as one HTML string (pure — see module docstring)."""
    files_now = stats["files_now"]
    bytes_now = stats["bytes_now"]
    pool_size = stats["pool_size"]
    since_files = stats["since_files"]
    since_bytes = stats["since_bytes"]
    version = stats["version"]
    ttl_hours = stats["ttl_hours"]
    prefix = stats["prefix"]
    pct = 100.0 * bytes_now / pool_size
    h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
         f"{_META_MOBILE}"
         "<title>throway — share files, gone in 4 hours | skale.dev</title>"
         "<meta name=description content='Disposable file sharing: upload a file or bundle, "
         "get a short URL, everything auto-expires after 4 hours. No signup, no tracking. "
         "With a plain API for agents.'>"
         "<link rel=canonical href='https://skale.dev/throway/'>"
         "<meta property=og:type content=website>"
         "<meta property=og:site_name content='skale.dev Apps'>"
         "<meta property=og:title content='throway — share files, gone in 4 hours'>"
         "<meta property=og:description content='No signup, no tracking — files and bundles "
         "auto-expire after 4 hours. Plain API for agents.'>"
         "<meta property=og:url content='https://skale.dev/throway/'>"
         "<meta property=og:image content='https://skale.dev/og-throway.png'>"
         "<meta name=twitter:card content=summary_large_image>"
         f"<link rel=stylesheet href='{DROPZONE_CSS}'>"
         f"<style>{_INDEX_CSS}</style></head><body><main>"
         f"<header><h1>throway<span class='dot'>.</span></h1>"
         f"<span class='version'>v{version}</span></header>"
         "<p class='lede'>A disposable file store for agents and humans. Upload a file, a"
         " bundle, or a dir — share a short-lived URL. No accounts, no setup,"
         " nothing permanent.</p>"
         "<ul class='feats'>"
         "<li><b>Files</b> — one URL per upload<small>inline for images &amp; text, download otherwise</small></li>"
         "<li><b>Bundles</b> — a whole mini-website<small>index.html renders inline; zip for agents</small></li>"
         "<li><b>Dirs</b> — keep adding files over days<small>sliding lifetime (ttl= up to 14d, default 7d); edit history</small></li>"
         "<li><b>Pics</b> — galleries for events<small>free upload, images live 90 days, per-gallery admin link · <a href='" + prefix + "/pics'>browse</a></small></li>"
         "</ul>"
         "<div class='uploader'>"
         "<div class='tabs' role='tablist' aria-label='Upload type'>"
         "<button type=button role=tab id=tab-files data-mode=files aria-controls=panel-files aria-selected=true tabindex=0>Files</button>"
         "<button type=button role=tab id=tab-text data-mode=text aria-controls=panel-text aria-selected=false tabindex=-1>Text</button>"
         "<button type=button role=tab id=tab-link data-mode=link aria-controls=panel-link aria-selected=false tabindex=-1>Link</button>"
         "<button type=button role=tab id=tab-gallery data-mode=gallery aria-controls=panel-gallery aria-selected=false tabindex=-1>Gallery</button>"
         "</div>"
         "<div class='panel' id='panel-files' role='tabpanel' aria-labelledby='tab-files'>"
         "<div id='drop' class='dropzone'>"
         "<div class='dz-message'>"
         "<div class='big'>Drop files here, or click to choose</div>"
         "<div class='sub'>Select one or many files — or just paste an image (⌘/Ctrl+V)</div>"
         "</div></div>"
         "<div class='controls'>"
         "<button id='up'>Upload</button>"
         "<label class='mode'><input type='checkbox' id='dirMode'>create a <b>dir</b></label>"
         "<label class='mode'><input type='checkbox' id='onceSel'>download <b>once</b></label>"
         "<label class='mode'>live <select id='ttlSel'>"
         "<option value=''>" + str(ttl_hours) + "h (default)</option>"
         "<option value='24h'>24h</option>"
         "<option value='7d'>7d</option>"
         "<option value='14d'>14d (max)</option>"
         "</select></label>"
         "</div>"
         "<div class='shareline'><label class='mode'>share name</label>"
         "<input id='shareSel' placeholder='optional, e.g. my-note' maxlength=32>"
         "<span class='hint'>a chosen, memorable URL (5-32 chars: a-z, 0-9, -)</span>"
         "</div>"
         "<div class='limits'>max 5 MB per file &#183; lives 4h (ttl up to 14d) &#183; one file = share URL, many = bundle, dir checkbox = dir</div>"
         "</div>"
         "<div class='panel' id='panel-text' role='tabpanel' aria-labelledby='tab-text' hidden>"
         "<textarea id='createText' placeholder='Paste or type text to share…' rows=8></textarea>"
         "<div class='createbar'>"
         "<input id='createName' placeholder='filename (optional, e.g. note.txt)' style='flex:1;min-width:180px'>"
         "<label class='mode' title='the file auto-deletes after the first download'><input type='checkbox' id='createOnce'>download once</label>"
         "<label class='mode'>live <select id='createTtl'>"
         "<option value=''>" + str(ttl_hours) + "h (default)</option>"
         "<option value='24h'>24h</option>"
         "<option value='7d'>7d</option>"
         "<option value='14d'>14d (max)</option>"
         "</select></label>"
         "<button id='create'>Create</button>"
         "</div>"
         "<div class='shareline'><label class='mode'>share name</label>"
         "<input id='createShare' placeholder='optional, e.g. my-note' maxlength=32>"
         "<span class='hint'>a chosen, memorable URL (5-32 chars: a-z, 0-9, -)</span>"
         "</div>"
         "<div class='limits'>becomes a text file with its own URL &#183; lives 4h (ttl up to 14d)</div>"
         "</div>"
         "<div class='panel' id='panel-link' role='tabpanel' aria-labelledby='tab-link' hidden>"
         "<div class='linkrow'>"
         "<input id='linkUrl' type='url' placeholder='https://example.com/page-or-file'>"
         "<input id='linkName' placeholder='filename (optional)'>"
         "<button id='linkGo'>Import</button>"
         "</div>"
         "<div class='limits'>the server fetches the URL and hosts it here &#183; &#8804; 5 MB, public http(s) URLs only &#183; lives 4h</div>"
         "</div>"
         "<div class='panel' id='panel-gallery' role='tabpanel' aria-labelledby='tab-gallery' hidden>"
         "<div class='galbar'>"
         "<input id='galName' type='text' placeholder='gallery name, e.g. hochzeit-2026' maxlength=80>"
         "<label class='mode'><input type='checkbox' id='galListed'> listed</label>"
         "<button type=button id='galCreate' title='create the gallery now — you can add images any time'>Create gallery</button>"
         "<a class='mode' href='" + prefix + "/pics' style='color:var(--accent);text-decoration:none'>all galleries &#8594;</a>"
         "</div>"
         "<div id='galDrop' class='minidrop'>"
         "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true' style='width:32px;height:32px;display:block;margin:0 auto .35rem;color:var(--accent)'>"
         "<rect x='3' y='3' width='18' height='18' rx='2'/><circle cx='8.5' cy='8.5' r='1.5'/>"
         "<path d='M21 15l-5-5L5 21'/></svg>"
         "<div class=big>Drop images here, or click to choose</div>"
         "<div class=sub>JPG · PNG · WebP · GIF · HEIC — max 30 MB, downscaled to 2048 px · or just paste (⌘/Ctrl+V)</div>"
         "</div>"
         "<input id='galFile' type='file' accept='image/*' multiple class=srinput>"
         "<div id='galRows'></div>"
         "<div id='galResult'></div>"
         "<div class='limits'>max 30 MB per image, downscaled in your browser to &#8804; 2048 px &#183; images live 90 days &#183; you get a private admin link (hide / delete / reorder)</div>"
         "</div>"
         "</div>"
         "<div id='status'></div>"
         "<div id='result'></div>"
         "<div class='stats'>"
         f"<div class='stat'><b>{files_now}</b><span>{'file' if files_now == 1 else 'files'}</span>"
         f"<div class='sub'>{_fmt_size(bytes_now)}</div>"
         f"<div class='meter' title='pool: {_fmt_size(bytes_now)} of {_fmt_size(pool_size)}'><i style='width:{min(100, pct) if pct >= 2 else (2 if bytes_now > 0 else 0):.0f}%'></i></div>"
         f"<div class='when'>now</div></div>"
         f"<div class='stat'><b>{since_files}</b><span>{'file' if since_files == 1 else 'files'}</span>"
         f"<div class='sub'>{_fmt_size(since_bytes)}</div>"
         f"<div class='when'>since start</div></div>"
         "</div>"
         "<nav class='links'>"
         f"<a href='{prefix}/pics'>pics</a>"
         f"<a href='{prefix}/api'>API</a>"
         f"<a href='{prefix}/help'>help</a>"
         f"<a href='{prefix}/write_for_agents'>for agents</a>"
         f"<a href='{prefix}/releases'>releases</a>"
         "</nav>"
         "<details class='agents'><summary>Agent info — this page is machine-readable</summary><pre>" + _html.escape(stats["agent_description"]) + "</pre></details>"
         f"<script src='{DROPZONE_JS}'></script>"
         f"<script>{_INDEX_JS.replace('__MAX_MB__', str(stats['max_mb']))}</script>"
         "</main></body></html>")
    return h
