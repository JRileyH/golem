// golem's workflow graphs, drawn (flows.py gives what to draw). Plain JS and SVG, no libraries.
//
// A graph is blocks wired output to input, laid out down the page. Drag to pan, wheel or pinch to
// zoom; tap a block for its sheet (a job's step), to open it (a container), or for its settings.
// The editor (data-mode=edit) adds moving blocks, wiring outputs and the palette: editor.js.
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const W = 220, H = 60, PAD = 40;
  const MIN = 0.25, MAX = 1.6;

  const el = (name, attrs = {}, parent) => {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  };
  const clip = (text, n) => (text && text.length > n ? text.slice(0, n - 1) + '…' : text || '');

  // Where a block's outputs sit along its bottom edge, and where a wire comes in at its top.
  const portX = (node, i) => node.x + (W * (i + 1)) / (node.ports.length + 1);
  const portAt = (node, port) => ({ x: portX(node, Math.max(node.ports.indexOf(port), 0)), y: node.y + H });
  const inAt = (node) => ({ x: node.x + W / 2, y: node.y });

  function wirePath(a, b, past = false) {
    // Down the page to the next block: a gentle S. Past blocks in its way (`past`): round their
    // left. Back up the page (a loop): round the right of both blocks.
    if (past && b.y > a.y + 24) {
      const side = Math.min(a.x, b.x) - W * 0.7;
      return `M${a.x},${a.y} C${a.x},${a.y + 40} ${side},${a.y + 40} ${side},${(a.y + b.y) / 2} S${b.x},${b.y - 40} ${b.x},${b.y}`;
    }
    if (b.y > a.y + 24) {
      const dy = Math.max(30, (b.y - a.y) / 2);
      return `M${a.x},${a.y} C${a.x},${a.y + dy} ${b.x},${b.y - dy} ${b.x},${b.y}`;
    }
    const side = Math.max(a.x, b.x) + W * 0.75;
    const mid = (a.y + b.y) / 2;
    return `M${a.x},${a.y} C${a.x},${a.y + 60} ${side},${a.y + 60} ${side},${mid} S${b.x},${b.y - 60} ${b.x},${b.y}`;
  }

  function mount(root) {
    if (root.dataset.mounted) return;
    root.dataset.mounted = '1';
    const svg = root.querySelector('svg.flow-canvas');
    const info = root.querySelector('.flow-info');
    const crumbs = root.querySelector('.flow-crumbs');
    const note = root.querySelector('.flow-note');
    const defs = el('defs', {}, svg);
    const marker = el('marker', { id: 'flow-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
    el('path', { d: 'M0,0 L10,5 L0,10 z', class: 'flow-arrowhead' }, marker);
    const world = el('g', { class: 'flow-world' }, svg);
    const view = { k: 1, tx: PAD, ty: PAD };
    let data = null, fitted = false, timer = null;
    const flow = { root, svg, world, view, get data() { return data; }, set data(d) { data = d; }, draw: () => draw(), fit: () => fit() };
    root.flow = flow;

    const place = () => world.setAttribute('transform', `translate(${view.tx},${view.ty}) scale(${view.k})`);

    function draw() {
      world.textContent = '';
      if (!data) return;
      const byId = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
      const wires = el('g', { class: 'flow-wires' }, world);
      for (const e of data.edges) {
        const a = byId[e.from], b = byId[e.to];
        if (!a || !b) continue;
        const lit = ['done', 'failed', 'skipped'].includes(a.state) && b.state && b.state !== 'todo';
        const p = portAt(a, e.port), q = inAt(b);
        const past = data.nodes.some((n) => n !== a && n !== b && n.y > a.y + H / 2 && n.y + H < b.y + 4 && p.x > n.x - 10 && p.x < n.x + W + 10);
        el('path', { d: wirePath(p, q, past), class: 'flow-wire' + (lit ? ' lit' : ''), 'marker-end': 'url(#flow-arrow)', 'data-from': e.from, 'data-port': e.port }, wires);
      }
      const blocks = el('g', { class: 'flow-blocks' }, world);
      for (const n of data.nodes) drawNode(blocks, n);
      if (flow.afterDraw) flow.afterDraw();
    }

    function drawNode(parent, n) {
      const wrap = n.href ? el('a', { href: n.href, 'data-sheet': '' }, parent) : n.open && root.dataset.mode !== 'edit' ? el('a', { href: n.open, 'data-src': n.src || null }, parent) : parent;
      const g = el('g', { class: `flow-node t-${n.type} ${n.state || ''}${n.problems && n.problems.length ? ' bad' : ''}`, transform: `translate(${n.x},${n.y})`, 'data-id': n.id, tabindex: n.href || n.open ? null : 0 }, wrap);
      const round = ['start', 'end', 'input', 'output'].includes(n.type) ? H / 2 : ['counter', 'condition'].includes(n.type) ? 14 : 3;
      el('rect', { width: W, height: H, rx: round, class: 'flow-box' }, g);
      if (n.type === 'container') el('rect', { x: 5, y: 5, width: W - 10, height: H - 10, rx: 2, class: 'flow-inner' }, g);
      const title = el('text', { x: 12, y: 24, class: 'flow-title' }, g);
      title.textContent = clip(n.label, 24);
      const sub = el('text', { x: 12, y: 43, class: 'flow-sub' }, g);
      sub.textContent = clip(n.note, 34);
      if (n.visits > 1) {
        el('rect', { x: W - 34, y: -9, width: 30, height: 18, rx: 9, class: 'flow-visits' }, g);
        const v = el('text', { x: W - 19, y: 4, class: 'flow-visits-text', 'text-anchor': 'middle' }, g);
        v.textContent = `×${n.visits}`;
      }
      if (n.problems && n.problems.length) {
        const warn = el('text', { x: W - 18, y: 22, class: 'flow-warn', 'text-anchor': 'middle' }, g);
        warn.textContent = '!';
        el('title', {}, g).textContent = n.problems.join('\n');
      }
      n.ports.forEach((p, i) => {
        const x = (W * (i + 1)) / (n.ports.length + 1);
        el('circle', { cx: x, cy: H, r: 5, class: 'flow-port', 'data-port': p }, g);
        if (n.ports.length > 1 || !['next', 'done', 'replied'].includes(p)) {
          const t = el('text', { x: x, y: H + 17, class: 'flow-port-name', 'text-anchor': 'middle' }, g);
          t.textContent = p;
        }
      });
      if (!['start', 'input'].includes(n.type)) el('circle', { cx: W / 2, cy: 0, r: 4, class: 'flow-in' }, g);
    }

    function fit() {
      if (!data || !data.nodes.length) return;
      const xs = data.nodes.map((n) => n.x), ys = data.nodes.map((n) => n.y);
      const x0 = Math.min(...xs) - PAD, y0 = Math.min(...ys) - PAD;
      const x1 = Math.max(...xs) + W * 1.8 + PAD, y1 = Math.max(...ys) + H + PAD * 1.5;
      const box = svg.getBoundingClientRect();
      const k = Math.min(1, Math.max(MIN, Math.min(box.width / (x1 - x0), box.height / (y1 - y0))));
      view.k = k;
      view.tx = (box.width - (x1 - x0) * k) / 2 - x0 * k;
      view.ty = Math.min(PAD, (box.height - (y1 - y0) * k) / 2) - y0 * k;
      place();
    }

    // The first view: readable rather than all of it, on where the job is (or the top).
    function focus() {
      if (!data || !data.nodes.length) return;
      const box = svg.getBoundingClientRect();
      const xs = data.nodes.map((n) => n.x);
      const width = Math.max(...xs) - Math.min(...xs) + W * 1.8 + PAD * 2;
      fit();
      if (view.k >= 0.75) return;  // it all fits, readably
      view.k = Math.min(1, Math.max(0.6, box.width / width));
      const at = data.nodes.find((n) => ['now', 'waiting', 'next', 'failed'].includes(n.state)) || data.nodes.find((n) => n.type === 'start' || n.type === 'input') || data.nodes[0];
      view.tx = box.width / 2 - (at.x + W / 2) * view.k;
      view.ty = Math.min(PAD, box.height / 3 - at.y * view.k);
      place();
    }

    function zoomAt(factor, cx, cy) {
      const k = Math.min(MAX, Math.max(MIN, view.k * factor));
      view.tx = cx - ((cx - view.tx) * k) / view.k;
      view.ty = cy - ((cy - view.ty) * k) / view.k;
      view.k = k;
      place();
    }

    // --- panning and zooming: wheel, drag, pinch ----------------------------------------------
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const box = svg.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY * 0.01), e.clientX - box.left, e.clientY - box.top);
      else { view.tx -= e.deltaX; view.ty -= e.deltaY; place(); }
    }, { passive: false });
    const pointers = new Map();
    let moved = false, pinch = null;
    svg.addEventListener('pointerdown', (e) => {
      if (flow.grab && flow.grab(e)) return;  // the editor took it: a block or an output
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      moved = false;
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: view.k };
      }
    });
    svg.addEventListener('pointermove', (e) => {
      const last = pointers.get(e.pointerId);
      if (!last) return;
      const dx = e.clientX - last.x, dy = e.clientY - last.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      if (pointers.size === 2 && pinch) {
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const [a, b] = [...pointers.values()];
        const box = svg.getBoundingClientRect();
        zoomAt((pinch.k * Math.hypot(a.x - b.x, a.y - b.y)) / pinch.d / view.k, (a.x + b.x) / 2 - box.left, (a.y + b.y) / 2 - box.top);
        return;
      }
      if (moved) { svg.setPointerCapture?.(e.pointerId); view.tx += dx; view.ty += dy; place(); }
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    });
    const up = (e) => { pointers.delete(e.pointerId); if (pointers.size < 2) pinch = null; };
    svg.addEventListener('pointerup', up);
    svg.addEventListener('pointercancel', up);
    root.addEventListener('click', (e) => {  // a drag isn't a tap
      if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; return; }
      const inside = e.target.closest('a[data-src]');  // a container or a crumb: opened right here
      if (inside && !e.metaKey && !e.ctrlKey) {
        e.preventDefault(); e.stopPropagation();
        open(inside.dataset.src, inside.getAttribute('href'));
        return;
      }
      const g = e.target.closest('.flow-node');
      if (!g || e.target.closest('a') || root.dataset.mode === 'edit') return;
      showInfo(data.nodes.find((n) => n.id === g.dataset.id));
    }, true);
    root.querySelector('[data-fit]')?.addEventListener('click', () => fit());
    root.querySelectorAll('[data-zoom]').forEach((b) => b.addEventListener('click', () => {
      const box = svg.getBoundingClientRect();
      zoomAt(parseFloat(b.dataset.zoom), box.width / 2, box.height / 2);
    }));

    function showInfo(n) {
      if (!info || !n) return;
      info.textContent = '';
      const close = document.createElement('button');
      close.type = 'button'; close.className = 'quiet close'; close.textContent = '✕'; close.setAttribute('aria-label', 'Close');
      close.addEventListener('click', () => { info.hidden = true; });
      const h = document.createElement('h3'); h.textContent = n.label;
      const p = document.createElement('p'); p.className = 'muted'; p.textContent = n.note;
      const dl = document.createElement('dl');
      for (const [k, v] of n.info || []) {
        const dt = document.createElement('dt'); dt.textContent = k;
        const dd = document.createElement('dd'); dd.textContent = v;
        if (k === 'instructions') dd.className = 'flow-text';
        dl.append(dt, dd);
      }
      info.append(close, h, p, dl);
      for (const problem of n.problems || []) {
        const bad = document.createElement('p'); bad.className = 'flow-problem'; bad.textContent = problem;
        info.append(bad);
      }
      info.hidden = false;
    }
    flow.showInfo = showInfo;

    // --- what it shows: fetched, and again while a job runs --------------------------------
    async function load() {
      clearTimeout(timer);
      if (!document.hidden || !data) {
        try {
          const r = await fetch(root.dataset.src, { cache: 'no-store' });
          if (r.ok) {
            const fresh = await r.json();
            if (!flow.editing) { data = fresh; draw(); }
            settle();
            if (crumbs) {
              crumbs.textContent = '';
              (fresh.crumbs || []).forEach((c, i) => {
                if (i) crumbs.append(' › ');
                const a = document.createElement('a'); a.href = c.href; a.textContent = c.label;
                if (c.src) a.dataset.src = c.src;
                crumbs.append(a);
              });
            }
            if (note) { note.textContent = fresh.note || ''; note.hidden = !fresh.note; }
            if (fresh.poll) timer = setTimeout(load, fresh.poll * 1000);
          }
        } catch (err) { timer = setTimeout(load, 15000); }
      } else timer = setTimeout(load, 10000);
    }
    flow.load = load;
    // The first view, once it has a size: a graph that starts hidden (a phone's Graph tab) fits when shown.
    function settle() {
      if (fitted || !data || flow.editing) return;  // the editor keeps its own view
      const box = svg.getBoundingClientRect();
      if (box.width && box.height) { focus(); fitted = true; }
    }
    if (window.ResizeObserver) new ResizeObserver(settle).observe(svg);
    // Another level (a container, or back up by a crumb), in place: the page stays, the address follows.
    function open(src, href) {
      root.dataset.src = src;
      fitted = false;
      info.hidden = true;
      if (href) history.replaceState(null, '', href);
      load();
    }
    flow.open = open;
    flow.focus = () => focus();
    flow.toWorld = (clientX, clientY) => {
      const box = svg.getBoundingClientRect();
      return { x: (clientX - box.left - view.tx) / view.k, y: (clientY - box.top - view.ty) / view.k };
    };
    document.dispatchEvent(new CustomEvent('flow:mounted', { detail: flow }));
    if (root.dataset.mode === 'edit') flow.editing = true;  // editor.js loads and draws it
    else load();
  }

  window.golemFlow = { mount, W, H, el, wirePath, portAt, inAt };
  document.querySelectorAll('.flow[data-src]').forEach(mount);
})();
