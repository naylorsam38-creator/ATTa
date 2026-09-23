// Button Mover capability.
//
// Its own UI mounts into ctx.slot (the Port-created drawer <section>).
// It works directly on the application's live DOM: every button / link the
// app rendered gets a stable number (data-cp-n) and can be moved before or
// after its neighbouring element within the same parent. Reset puts every
// moved element back where it was.
(function () {
  'use strict';
  var Port = window.CapabilityPort;
  if (!Port) return;

  var SELECTOR =
    'button, a[href], [role="button"], input[type="button"], input[type="submit"], input[type="reset"]';

  Port.register({
    id: 'button-mover',
    trust: 'local',
    placement: 'drawer',
    mount: function (ctx) {
      var doc = ctx.document;
      var slot = ctx.slot;
      var next = 1;
      var origin = new Map(); // el -> { parent, marker } for reset
      var moves = [];

      slot.innerHTML =
        '<h3 style="margin:0 0 6px;font-weight:600">Button Mover</h3>' +
        '<div style="display:flex;gap:6px;margin-bottom:8px">' +
        '<button type="button" data-act="scan">Rescan</button>' +
        '<button type="button" data-act="reset">Reset</button>' +
        '<span data-role="count" style="margin-left:auto;color:#6e6e73"></span></div>' +
        '<ol data-role="list" style="list-style:none;margin:0;padding:0"></ol>';
      var list = slot.querySelector('[data-role="list"]');
      var count = slot.querySelector('[data-role="count"]');

      function label(el) {
        var t = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '')
          .trim()
          .replace(/\s+/g, ' ');
        return t.slice(0, 40) || '<' + el.tagName.toLowerCase() + '>';
      }

      function targets() {
        return Array.prototype.filter.call(doc.querySelectorAll(SELECTOR), function (el) {
          return !Port.isPortNode(el);
        });
      }

      function scan() {
        var els = targets();
        els.forEach(function (el) {
          if (!el.hasAttribute('data-cp-n')) el.setAttribute('data-cp-n', String(next++));
        });
        render(els);
        return els.map(function (el) {
          return { n: Number(el.getAttribute('data-cp-n')), label: label(el) };
        });
      }

      function byNumber(n) {
        return doc.querySelector('[data-cp-n="' + n + '"]');
      }

      function remember(el) {
        if (origin.has(el)) return;
        var marker = doc.createComment('cp-origin-' + el.getAttribute('data-cp-n'));
        el.parentNode.insertBefore(marker, el);
        origin.set(el, { parent: el.parentNode, marker: marker });
      }

      // dir: 'up' (before previous element sibling) or 'down' (after next).
      function move(n, dir) {
        var el = byNumber(n);
        if (!el || !el.parentNode) return false;
        var sib = dir === 'up' ? el.previousElementSibling : el.nextElementSibling;
        if (!sib || Port.isPortNode(sib)) return false;
        remember(el);
        if (dir === 'up') el.parentNode.insertBefore(el, sib);
        else el.parentNode.insertBefore(el, sib.nextSibling);
        moves.push({ n: n, dir: dir });
        scan();
        return true;
      }

      function reset() {
        origin.forEach(function (o, el) {
          if (o.marker.parentNode) {
            o.marker.parentNode.insertBefore(el, o.marker);
            o.marker.parentNode.removeChild(o.marker);
          }
        });
        origin.clear();
        moves = [];
        scan();
      }

      function render(els) {
        count.textContent = els.length + ' found';
        list.innerHTML = '';
        els.forEach(function (el) {
          var n = el.getAttribute('data-cp-n');
          var li = doc.createElement('li');
          li.style.cssText = 'display:flex;align-items:center;gap:6px;padding:3px 0';
          li.innerHTML =
            '<b style="min-width:2.5em">#' + n + '</b>' +
            '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>' +
            '<button type="button" data-act="up" data-n="' + n + '" aria-label="Move #' + n + ' up">↑</button>' +
            '<button type="button" data-act="down" data-n="' + n + '" aria-label="Move #' + n + ' down">↓</button>';
          li.querySelector('span').textContent = label(el);
          li.addEventListener('mouseenter', function () { el.style.outline = '2px solid #0a84ff'; });
          li.addEventListener('mouseleave', function () { el.style.outline = ''; });
          list.appendChild(li);
        });
      }

      slot.addEventListener('click', function (e) {
        var b = e.target.closest('button[data-act]');
        if (!b) return;
        var act = b.getAttribute('data-act');
        if (act === 'scan') scan();
        else if (act === 'reset') reset();
        else move(Number(b.getAttribute('data-n')), act);
      });

      // Re-number when the app re-renders (SPAs), ignoring the Port's own UI.
      var timer = null;
      new MutationObserver(function (records) {
        var relevant = records.some(function (r) { return !Port.isPortNode(r.target); });
        if (!relevant) return;
        clearTimeout(timer);
        timer = setTimeout(scan, 150);
      }).observe(doc.body, { childList: true, subtree: true });

      scan();
      return { scan: scan, move: move, reset: reset, moves: function () { return moves.slice(); } };
    },
  });
})();
