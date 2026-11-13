(function () {
  var box = document.getElementById('q'), out = document.getElementById('results'), latest = document.getElementById('latest');
  if (!box) return;
  var root = box.getAttribute('data-root') || '', ms = null, loading = null, timer = null;
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function load() {
    if (loading) return loading;
    loading = fetch(root + 'search-index.json').then(function (r) { return r.json(); }).then(function (d) {
      ms = new MiniSearch({ fields: ['n'], storeFields: ['n', 'u', 'k', 'c', 'd'], searchOptions: { prefix: true, fuzzy: 0.2, boost: { n: 2 } } });
      var docs = [], i = 0;
      d.e.forEach(function (e) { docs.push({ id: 'e' + (i++), n: e[1] + ' ' + e[2], u: 'e/' + e[0] + '.html', k: e[3], c: e[4], d: e[1] }); });
      d.o.forEach(function (o) { docs.push({ id: 'o' + (i++), n: o[1] + ' ' + o[2], u: 'o/' + o[0] + '.html', k: 'order', c: 0, d: o[3] + (o[4] ? ' · ' + o[4] : ''), t: o[1] }); });
      ms.addAll(docs);
    });
    return loading;
  }
  function render(hits) {
    if (!hits.length) { out.innerHTML = '<p class="meta">No matches. Try fewer letters — search is fuzzy but not magic.</p>'; return; }
    out.innerHTML = hits.slice(0, 30).map(function (h) {
      var isE = h.k !== 'order';
      var title = isE ? h.d : (h.t || h.n);
      var sub = isE ? (h.k + ' · ' + h.c + ' order' + (h.c === 1 ? '' : 's')) : h.d;
      return '<a class="card" href="' + root + h.u + '"><strong>' + esc(title) + '</strong><div class="meta">' + esc(sub) + '</div></a>';
    }).join('');
  }
  function run() {
    var q = box.value.trim();
    if (q.length < 2) { out.innerHTML = ''; if (latest) latest.classList.remove('hide'); return; }
    if (latest) latest.classList.add('hide');
    load().then(function () { render(ms.search(q)); });
  }
  box.addEventListener('focus', load);
  box.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(run, 120); });
  var pre = new URLSearchParams(location.search).get('q'); if (pre) { box.value = pre; run(); }
})();
