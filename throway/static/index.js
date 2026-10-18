(function () {
  'use strict';

  /* Works under any mount point: '' at root, '/throway' behind the proxy. */
  var PREFIX = location.pathname.replace(/\/+$/, '');
  var MAX_MB = +(document.querySelector('main') && document.querySelector('main').dataset.maxMb) || 5;

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

  /* ── Konto (1.56.1): Header-Pill/Avatar-Chip + Popover ─────────── */
  function applyAuth(user) {
    var pill = $('hauthAnon'), chip = $('hauthUser');
    if (user) {
      pill.hidden = true; chip.hidden = false;
      $('hWho').textContent = user;
      $('hAvatar').textContent = (user[0] || '?').toUpperCase();
      $('mWho').textContent = user;
      $('mAvatar').textContent = (user[0] || '?').toUpperCase();
    } else {
      pill.hidden = false; chip.hidden = true;
    }
    var longTtls = ['24h', '7d', '14d'];
    ['ttlSel', 'createTtl'].forEach(function (id) {
      var sel = $(id); if (!sel) return;
      for (var i = 0; i < sel.options.length; i++) {
        var o = sel.options[i];
        if (longTtls.indexOf(o.value) !== -1) o.disabled = !user;
      }
      if (!user && longTtls.indexOf(sel.value) !== -1) sel.value = '';
    });
    if (!user) closePop();
  }
  function authMsg(t, ok) {
    var m = $('acctMsg'); m.textContent = t;
    m.style.color = ok === false ? '#dc2626' : 'var(--muted)';
  }
  function api(path, body) {
    return fetch(PREFIX + '/api/auth/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); });
  }
  var popMode = 'login';
  function openPop(mode) {
    popMode = mode;
    var menu = mode === 'menu';
    $('chipMenu').hidden = !menu;
    $('popForm').hidden = menu;
    if (menu) {
      $('popTitle').textContent = 'Account';
    } else {
      $('popTitle').textContent = mode === 'reg' ? 'Register — free, unlocks 14d' : 'Log in';
      $('popGo').textContent = mode === 'reg' ? 'Send verify mail' : 'Log in';
      $('popSwitch').textContent = mode === 'reg' ? 'have an account? log in' : 'no account? register';
      authMsg(mode === 'reg' ? 'We mail you a verify link (30 min). Lifetimes up to 14d.' : '');
    }
    var p = $('authPop'); p.hidden = false;
    if (!menu) $('acctEmail').focus();
  }
  function closePop() { var p = $('authPop'); if (p) p.hidden = true; }
  function initAuth() {
    fetch(PREFIX + '/api/auth/whoami')
      .then(function (r) { return r.json(); })
      .then(function (d) { applyAuth(d.user); })
      .catch(function () { applyAuth(null); });
    $('hLogin').onclick = function () { openPop('login'); };
    $('hReg').onclick = function () { openPop('reg'); };
    $('popSwitch').onclick = function () { openPop(popMode === 'reg' ? 'login' : 'reg'); };
    $('popGo').onclick = function () {
      api(popMode === 'reg' ? 'register' : 'login',
          { email: $('acctEmail').value.trim(), passphrase: $('acctPass').value })
        .then(function (r) {
          if (r.ok && popMode === 'login') { applyAuth(r.j.user); authMsg(''); }
          else if (r.ok) authMsg(r.j.message || 'Check your mail — the verify link is valid 30 min.');
          else authMsg(r.j.error || 'failed', false);
        });
    };
    $('popClose').onclick = closePop;
    $('acctPass').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') $('popGo').click();
    });
    $('acctEmail').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') $('acctPass').focus();
    });
    $('hauthUser').onclick = function (e) {
      e.stopPropagation();
      var p = $('authPop');
      if (p.hidden) openPop('menu'); else closePop();
    };
    $('hLogout').onclick = function () {
      api('logout', {}).then(function () { applyAuth(null); });
    };
    document.addEventListener('click', function (e) {
      var p = $('authPop');
      if (p.hidden) return;
      if (!p.contains(e.target) && e.target.id !== 'hLogin' && e.target.id !== 'hReg') closePop();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closePop();
    });
  }
  initAuth();
})();
