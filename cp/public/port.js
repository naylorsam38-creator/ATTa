// Capability Port — runs inside the application's own page.
//
// Loaded by the tag proxy.js inserts before </head>. It builds a small
// drawer UI, then for each capability:
//
//   slot = document.createElement('section')
//   slot.dataset.capability = def.id; slot.dataset.trust = def.trust
//   target.appendChild(slot)          // target resolved from def.placement
//   def.mount({ slot, ... })          // capability receives ctx.slot
//
// placement "drawer" resolves to ui.drawerBody().
(function () {
  'use strict';
  if (window.CapabilityPort) return;

  var script =
    document.currentScript || document.querySelector('script[data-capability-app]');
  var appId = (script && script.getAttribute('data-capability-app')) || 'app';
  var base = '/_cp/';
  var CAPABILITIES = ['mover'];

  var ROOT_ID = '_cp-root';
  var css =
    '#_cp-root{all:initial;font:13px/1.4 system-ui,sans-serif;color:#1d1d1f}' +
    '#_cp-root *{box-sizing:border-box;font:inherit}' +
    '#_cp-toggle{position:fixed;right:16px;bottom:16px;z-index:2147483646;width:44px;height:44px;' +
    'border-radius:22px;border:0;background:#1d1d1f;color:#fff;font-weight:600;cursor:pointer;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.3)}' +
    '#_cp-drawer{position:fixed;top:0;right:0;bottom:0;z-index:2147483647;width:min(340px,100vw);' +
    'background:#fff;border-left:1px solid #d2d2d7;box-shadow:-4px 0 16px rgba(0,0,0,.12);' +
    'display:flex;flex-direction:column;transform:translateX(100%);transition:transform .15s}' +
    '#_cp-drawer[data-open="1"]{transform:none}' +
    '#_cp-drawer header{display:flex;align-items:center;justify-content:space-between;' +
    'padding:10px 12px;border-bottom:1px solid #e5e5ea;font-weight:600}' +
    '#_cp-drawer header button{border:0;background:none;font-size:18px;cursor:pointer;color:#1d1d1f}' +
    '#_cp-drawer-body{flex:1;overflow:auto;padding:12px}' +
    '#_cp-drawer-body section{margin-bottom:16px}';

  var ui = null;

  function buildUi() {
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    var root = document.createElement('div');
    root.id = ROOT_ID;
    root.innerHTML =
      '<button id="_cp-toggle" type="button" aria-label="Open Capability Port" ' +
      'aria-controls="_cp-drawer" aria-expanded="false">CP</button>' +
      '<aside id="_cp-drawer" data-open="0" aria-label="Capability Port">' +
      '<header><span>Capability Port</span>' +
      '<button type="button" id="_cp-close" aria-label="Close">×</button></header>' +
      '<div id="_cp-drawer-body"></div></aside>';
    document.body.appendChild(root);

    var drawer = root.querySelector('#_cp-drawer');
    var toggle = root.querySelector('#_cp-toggle');
    function setOpen(open) {
      drawer.setAttribute('data-open', open ? '1' : '0');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    toggle.addEventListener('click', function () {
      setOpen(drawer.getAttribute('data-open') !== '1');
    });
    root.querySelector('#_cp-close').addEventListener('click', function () {
      setOpen(false);
    });

    return {
      root: root,
      drawerBody: function () {
        return root.querySelector('#_cp-drawer-body');
      },
      open: function () { setOpen(true); },
      close: function () { setOpen(false); },
    };
  }

  function resolvePlacement(placement) {
    switch (placement) {
      case 'drawer':
        return ui.drawerBody();
      default:
        console.warn('[CapabilityPort] unknown placement "' + placement + '", using drawer');
        return ui.drawerBody();
    }
  }

  var pending = [];
  var Port = {
    appId: appId,
    rootId: ROOT_ID,
    capabilities: {},
    status: {},
    ui: null,
    // A capability's own UI lives under #_cp-root; it must never treat that as app DOM.
    isPortNode: function (node) {
      return !!(node && node.closest && node.closest('#' + ROOT_ID));
    },
    register: function (def) {
      if (!ui) {
        pending.push(def);
        return;
      }
      mount(def);
    },
  };

  function mount(def) {
    var slot = document.createElement('section');
    slot.setAttribute('data-capability', def.id);
    slot.setAttribute('data-trust', def.trust || 'local');
    var target = resolvePlacement(def.placement);
    target.appendChild(slot);
    Port.capabilities[def.id] = { def: def, slot: slot, api: null };
    try {
      var api = def.mount({ slot: slot, appId: appId, port: Port, document: document });
      Port.capabilities[def.id].api = api || null;
      Port.status[def.id] = 'mounted';
    } catch (e) {
      Port.status[def.id] = 'error: ' + (e && e.message);
      console.error('[CapabilityPort] ' + def.id + ' failed to mount', e);
    }
  }

  function start() {
    ui = buildUi();
    Port.ui = ui;
    pending.splice(0).forEach(mount);
    CAPABILITIES.forEach(function (name) {
      var s = document.createElement('script');
      s.src = base + 'capabilities/' + name + '.js';
      s.onerror = function () { Port.status[name] = 'error: failed to load'; };
      document.head.appendChild(s);
    });
    Port.ready = true;
  }

  window.CapabilityPort = Port;
  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
