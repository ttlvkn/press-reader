/* 外刊精读 · 前端交互（无框架，~5KB） */
(function () {
  'use strict';

  var doc = document.documentElement;
  var store = {
    get: function (k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };

  function effectiveTheme() {
    var t = store.get('pr-theme', 'auto');
    if (t !== 'auto') return t;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  /* ---------- 主题 ---------- */
  function applyTheme(t) {
    if (t === 'auto') doc.removeAttribute('data-theme');
    else doc.dataset.theme = t;
    store.set('pr-theme', t);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', effectiveTheme() === 'light' ? '#faf9f6' : '#0f1115');
  }

  /* ---------- 字号 ---------- */
  var FS = [15, 17, 18, 20, 22];
  function applyFS(v) {
    doc.style.setProperty('--fs', v + 'px');
    store.set('pr-fs', String(v));
  }
  function currentFS() {
    var v = parseInt(store.get('pr-fs', '18'), 10);
    return isNaN(v) ? 18 : v;
  }

  /* ---------- 阅读模式 ---------- */
  function applyMode(m) {
    doc.dataset.mode = m;
    store.set('pr-mode', m);
    document.querySelectorAll('.seg[data-seg="mode"] button').forEach(function (b) {
      b.classList.toggle('on', b.dataset.v === m);
    });
  }

  /* ---------- 收藏 ---------- */
  function bookmarks() {
    try { return JSON.parse(store.get('pr-bm', '[]')); } catch (e) { return []; }
  }
  function toggleBM(id, btn) {
    var list = bookmarks();
    var i = list.indexOf(id);
    if (i >= 0) { list.splice(i, 1); if (btn) btn.classList.remove('on'); }
    else { list.push(id); if (btn) btn.classList.add('on'); }
    store.set('pr-bm', JSON.stringify(list));
    if (btn) btn.textContent = list.indexOf(id) >= 0 ? '★' : '☆';
  }

  /* ---------- 顶栏按钮 ---------- */
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-act]');
    if (btn) {
      var act = btn.dataset.act;
      if (act === 'theme') {
        var cur = store.get('pr-theme', 'auto');
        var next = cur === 'auto' ? 'dark' : cur === 'dark' ? 'light' : 'auto';
        applyTheme(next);
        btn.textContent = next === 'dark' ? '☾' : next === 'light' ? '☀' : '◐';
      } else if (act === 'fs+') {
        var i = FS.indexOf(currentFS());
        applyFS(FS[Math.min(FS.length - 1, i < 0 ? 2 : i + 1)]);
      } else if (act === 'fs-') {
        var j = FS.indexOf(currentFS());
        applyFS(FS[Math.max(0, j < 0 ? 2 : j - 1)]);
      } else if (act === 'bookmark') {
        toggleBM(btn.dataset.id, btn);
      }
      return;
    }

    /* 分段控件 */
    var segBtn = ev.target.closest('.seg[data-seg="mode"] button');
    if (segBtn) { applyMode(segBtn.dataset.v); return; }

    /* 点击正文任意英文单词 → 柯林斯词条 */
    if (ev.target.closest('.para .en')) {
      openLookup(ev.clientX, ev.clientY);
    }
  });

  /* ---------- 点词查词（柯林斯英汉双解） ---------- */
  var lexEl = document.getElementById('lex');
  var LEX = { w: {}, n: {} };
  if (lexEl) {
    try { LEX = JSON.parse(lexEl.textContent); } catch (e) {}
  }
  var STAR = function (n) {
    n = Math.max(0, Math.min(5, n | 0));
    return n ? '★'.repeat(n) + '☆'.repeat(5 - n) : '';
  };

  function wordAtPoint(x, y) {
    var node = null, off = 0;
    try {
      if (document.caretRangeFromPoint) {
        var r = document.caretRangeFromPoint(x, y);
        if (r && r.startContainer && r.startContainer.nodeType === 3) {
          node = r.startContainer; off = r.startOffset;
        }
      } else if (document.caretPositionFromPoint) {
        var p = document.caretPositionFromPoint(x, y);
        if (p && p.offsetNode && p.offsetNode.nodeType === 3) {
          node = p.offsetNode; off = p.offset;
        }
      }
    } catch (e) { return null; }
    if (!node) return null;

    var text = node.nodeValue || '';
    var re = /[A-Za-z][A-Za-z'’-]*/g, m;
    while ((m = re.exec(text))) {
      var s = m.index, e = s + m[0].length;
      if (off >= s && off <= e) {
        return { word: m[0].replace(/^[-'’-]+|[-'’-]+$/g, ''), node: node, start: s, end: e };
      }
    }
    return null;
  }

  function lookupWord(w) {
    var key = w.toLowerCase();
    if (LEX.w[key]) return { key: key, entry: LEX.w[key] };
    var rules = [
      [/'s$/, ''], [/ies$/, 'y'], [/ves$/, 'f'], [/es$/, ''], [/s$/, ''],
      [/ied$/, 'y'], [/ed$/, 'e'], [/ed$/, ''], [/ing$/, 'e'], [/ing$/, ''],
      [/ly$/, ''], [/ier$/, 'y'], [/iest$/, 'y'], [/er$/, ''], [/est$/, '']
    ];
    for (var i = 0; i < rules.length; i++) {
      var cand = key.replace(rules[i][0], rules[i][1]);
      if (cand !== key && LEX.w[cand]) return { key: cand, entry: LEX.w[cand] };
    }
    return { key: key, entry: null };
  }

  function esc2(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function openLookup(x, y) {
    var sheet = document.getElementById('sheet');
    if (!sheet || !x) return;
    var hit = wordAtPoint(x, y);
    if (!hit || !hit.word) return;

    var found = lookupWord(hit.word);
    var e = found.entry;
    var note = LEX.n[found.key] || LEX.n[hit.word.toLowerCase()];

    document.getElementById('sw').textContent = (e && e.w) ? e.w : hit.word;
    document.getElementById('sp').textContent = e && e.p ? '/' + e.p + '/' : '';
    document.getElementById('ss').textContent = e && e.s ? STAR(e.s) : '';

    var html = '';
    if (note) {
      html += '<div class="dnote"><span class="tag">精读批注</span>' +
        (note.pos ? esc2(note.pos) + ' ' : '') + esc2(note.zh) +
        (note.ex_en ? '<br><span style="opacity:.85">' + esc2(note.ex_en) + '</span>' : '') +
        '</div>';
    }
    if (e && e.d && e.d.length) {
      html += '<div class="dsimp">' + e.d.map(function (d) {
        return '<span>' + esc2(d) + '</span>';
      }).join('') + '</div>';
    }
    if (e && e.e && e.e.length) {
      html += e.e.map(function (d) {
        var one = '<div class="ddef">';
        if (d.pos) one += '<span class="dpos">' + esc2(d.pos) + '</span>';
        if (d.tip) one += '<span class="dtip">' + esc2(d.tip) + '</span>';
        if (d.en) one += '<div class="en">' + esc2(d.en) + '</div>';
        if (d.zh) one += '<div class="zh">' + esc2(d.zh) + '</div>';
        if (d.ex && d.ex[0]) {
          one += '<div class="ex"><i>' + esc2(d.ex[0].en) + '</i>' + esc2(d.ex[0].zh) + '</div>';
        }
        return one + '</div>';
      }).join('');
    }
    if (!html) {
      html = '<div class="dmiss">柯林斯未收录 <b>' + esc2(hit.word) + '</b><br>' +
        '可长按选中查其他词，或换一个词试试</div>';
    }
    document.getElementById('sb').innerHTML = html;
    sheet.hidden = false;
    document.body.style.overflow = 'hidden';

    /* 高亮选中的词（支持的浏览器才有，不影响功能） */
    try {
      if (window.CSS && CSS.highlights && window.Highlight) {
        var r = document.createRange();
        r.setStart(hit.node, hit.start);
        r.setEnd(hit.node, hit.end);
        CSS.highlights.set('tapword', new Highlight(r));
      }
    } catch (err) {}
  }

  function closeLookup() {
    var sheet = document.getElementById('sheet');
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    document.body.style.overflow = '';
    try { if (window.CSS && CSS.highlights) CSS.highlights.delete('tapword'); } catch (e) {}
  }

  document.addEventListener('click', function (ev) {
    if (ev.target.closest('[data-close]')) closeLookup();
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') closeLookup();
  });

  /* ---------- 阅读进度 ---------- */
  var pb = document.getElementById('pb');
  if (pb) {
    var onScroll = function () {
      var h = document.documentElement;
      var max = h.scrollHeight - h.clientHeight;
      pb.style.width = max > 0 ? (h.scrollTop / max) * 100 + '%' : '0%';
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ---------- 返回：优先历史栈 ---------- */
  document.addEventListener('click', function (ev) {
    var back = ev.target.closest('.icon-btn.back');
    if (back && history.length > 1) { ev.preventDefault(); history.back(); }
  });

  /* ---------- 首页搜索 ---------- */
  var q = document.getElementById('q');
  if (q) {
    var box = document.getElementById('qres');
    var data = null;
    var load = function (cb) {
      if (data) return cb();
      fetch('search-index.json').then(function (r) { return r.json(); }).then(function (d) {
        data = d; cb();
      }).catch(function () {});
    };
    var render = function (kw) {
      kw = kw.trim().toLowerCase();
      if (!kw) { box.hidden = true; box.innerHTML = ''; return; }
      load(function () {
        var hits = data.filter(function (a) {
          return (a.t + ' ' + a.z).toLowerCase().indexOf(kw) >= 0;
        }).slice(0, 12);
        box.innerHTML = hits.length
          ? hits.map(function (a) {
              return '<a href="a/' + a.i + '.html">' + esc(a.t) +
                '<span class="qz">' + esc(a.z || '') + ' · ' + a.d + '</span></a>';
            }).join('')
          : '<div class="qempty">没有找到相关文章</div>';
        box.hidden = false;
      });
    };
    var timer;
    q.addEventListener('input', function () {
      clearTimeout(timer);
      var v = q.value;
      timer = setTimeout(function () { render(v); }, 160);
    });
    q.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { box.hidden = true; q.blur(); }
    });
  }

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------- 归档筛选 ---------- */
  var filters = document.getElementById('filters');
  if (filters) {
    var empty = document.getElementById('empty');
    var apply = function (src) {
      var bm = bookmarks();
      var shown = 0;
      document.querySelectorAll('.row').forEach(function (row) {
        var ok = src === 'all' ? true : src === '__bm' ? bm.indexOf(row.dataset.id) >= 0 : row.dataset.src === src;
        row.style.display = ok ? '' : 'none';
        if (ok) shown++;
      });
      document.querySelectorAll('.daygroup').forEach(function (g) {
        var any = Array.prototype.some.call(g.querySelectorAll('.row'), function (r) { return r.style.display !== 'none'; });
        g.style.display = any ? '' : 'none';
      });
      if (empty) empty.hidden = shown > 0;
    };
    filters.addEventListener('click', function (ev) {
      var b = ev.target.closest('.fchip');
      if (!b) return;
      filters.querySelectorAll('.fchip').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on');
      apply(b.dataset.src);
    });
    if (location.search.indexOf('bm=1') >= 0) {
      var bmBtn = filters.querySelector('[data-src="__bm"]');
      if (bmBtn) { bmBtn.click(); }
    }
  }

  /* ---------- 恢复状态 ---------- */
  var mode = store.get('pr-mode', 'both');
  if (document.querySelector('.seg[data-seg="mode"]')) applyMode(mode);
  else doc.dataset.mode = mode;

  var themeBtn = document.querySelector('[data-act="theme"]');
  if (themeBtn) {
    var t = store.get('pr-theme', 'auto');
    themeBtn.textContent = t === 'dark' ? '☾' : t === 'light' ? '☀' : '◐';
  }

  var bmBtn = document.querySelector('[data-act="bookmark"]');
  if (bmBtn && bookmarks().indexOf(bmBtn.dataset.id) >= 0) {
    bmBtn.classList.add('on');
    bmBtn.textContent = '★';
  }

  /* ---------- 注册 Service Worker（离线可读） ---------- */
  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    var link = document.querySelector('link[rel="manifest"]');
    var prefix = link ? link.getAttribute('href').replace('manifest.webmanifest', '') : './';
    navigator.serviceWorker.register(prefix + 'sw.js', { scope: prefix || './' }).catch(function () {});
  }
})();
