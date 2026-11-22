// golem's workflow editor, on top of flow.js: add blocks from the palette, drag them about, wire an
// output to a block by dragging from its dot, and set each block's settings in the inspector. Save
// keeps the tab as it is; whatever's wrong with it is marked on its blocks (flows.py, validate.py).
// Built for a mouse or trackpad: on a phone the same page shows the workflow to look at.
(() => {
  'use strict';
  const G = window.golemFlow;
  if (!G) return;
  const { W, H, el } = G;
  const GRID = 10;
  const snap = (v) => Math.round(v / GRID) * GRID;
  const make = (tag, attrs = {}, text) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  };

  document.querySelectorAll('.flow[data-mode=edit]').forEach((root) => root.flow && attach(root.flow));

  function attach(flow) {
    const root = flow.root;
    const small = matchMedia('(max-width: 760px), (pointer: coarse)').matches;
    let ed = null, graph = null, base = '', problems = [], warnings = [], dirty = false, selected = null;
    const past = [], future = [];

    // --- the editor's own parts: a toolbar with the palette, and the inspector ---------------------
    const bar = make('div', { class: 'editor-bar' });
    const save = make('button', { type: 'button', class: 'primary', disabled: '' }, 'Save');
    const undo = make('button', { type: 'button', title: 'Undo (⌘Z)' }, 'Undo');
    const redo = make('button', { type: 'button', title: 'Redo (⇧⌘Z)' }, 'Redo');
    const remove = make('button', { type: 'button', class: 'danger', disabled: '' }, 'Delete block');
    const status = make('span', { class: 'editor-status', role: 'status' });
    const palette = make('details', { class: 'editor-palette' });
    palette.append(make('summary', {}, 'Add a block'));
    bar.append(palette, save, undo, redo, remove, status);
    const inspector = make('aside', { class: 'editor-inspector', 'aria-label': 'Block settings' });
    const list = make('ul', { class: 'editor-problems', hidden: '' });
    root.insertBefore(bar, root.querySelector('svg'));
    root.insertBefore(list, root.querySelector('svg'));
    root.append(inspector);
    if (small) {
      bar.hidden = true;
      inspector.hidden = true;
      root.dataset.mode = 'view';
      const note = root.querySelector('.flow-note');
      note.textContent = 'Editing needs a bigger screen. Here you can look, and tap a block for its settings.';
      note.hidden = false;
    }

    // --- loading, and what's drawn ------------------------------------------------------------------
    async function load() {
      const r = await fetch(root.dataset.src, { cache: 'no-store' });
      if (!r.ok) { status.textContent = "Couldn't load it."; return; }
      ed = await r.json();
      graph = ed.graph; base = ed.base; problems = ed.problems || [];
      buildPalette();
      redraw();
      flow.focus();
      showInspector();
      tell();
    }

    const byType = () => Object.fromEntries(ed.catalog.map((c) => [c.type, c]));
    const node = (id) => graph.nodes.find((n) => n.id === id);

    function portsOf(n) {
      const t = n.type;
      if (['start', 'input', 'counter'].includes(t)) return ['next'];
      if (['end', 'output'].includes(t)) return [];
      if (t === 'condition') return ['true', 'false'];
      if (t === 'choose') return ['picked', 'more'];
      if (t === 'script' || t === 'checks' || t === 'deploy') return ['pass', 'fail'];
      if (t === 'agent') {
        if (n.contract === 'design') return ['directions', 'nothing'];
        if (n.contract) return ['done'];
        return (n.outputs || []).map((o) => o.name).filter(Boolean).length ? n.outputs.map((o) => o.name) : ['done'];
      }
      if (t === 'checkin') return (n.outputs || []).length ? n.outputs.map((o) => o.name) : ['replied'];
      if (t === 'container') return (ed.subs[n.workflow] || {}).outputs || [];
      return ['next'];
    }

    function labelOf(n) {
      const t = n.type;
      if (t === 'start') return ['Start', ''];
      if (t === 'input') return ['Input', 'where a container goes in'];
      if (t === 'end') return ['End', "tells Riggy it's done"];
      if (t === 'output') return [n.name || 'Output', 'an output of the container'];
      if (t === 'agent') return [n.name || 'agent', ['agent · ' + (n.turns || '?') + ' turns', n.contract, n.optional ? 'optional' : ''].filter(Boolean).join(' · ')];
      if (t === 'script') return [n.name || 'script', '$ ' + (n.command || '')];
      if (t === 'counter') return ['Count ' + (n.counter || ''), 'adds one'];
      if (t === 'condition') return [n.name || 'condition', (n.rows || []).map((r) => `${r.name || r.of} ${r.op} ${r.value}`).join(n.join === 'any' ? ' or ' : ' and ') || 'always true'];
      if (t === 'checkin') return [n.name || 'check-in', 'asks Riggy' + (n.optional ? ' · optional' : '')];
      if (t === 'choose') return [n.name || 'pick', 'Riggy picks an option'];
      if (t === 'container') { const s = ed.subs[n.workflow]; return [n.name || (s ? s.name : 'Sub-workflow?'), 'sub-workflow · ' + (n.tab && n.tab !== 'auto' ? n.tab + ' tab' : 'tab for the mode')]; }
      const name = ed.labels[t] || t;
      return [name.charAt(0).toUpperCase() + name.slice(1), ed.notes[t] || "golem's own"];
    }

    function redraw() {
      const marks = {};
      for (const p of problems) (marks[p.node || ''] = marks[p.node || ''] || []).push(p.message);
      flow.data = {
        nodes: graph.nodes.map((n) => {
          const [label, note] = labelOf(n);
          return { id: n.id, type: n.type, label, note, x: n.x || 0, y: n.y || 0, ports: portsOf(n), problems: marks[n.id] || [], info: info(n) };
        }),
        edges: graph.edges,
      };
      flow.draw();
      list.textContent = '';
      for (const m of [...(marks[''] || []), ...warnings.filter((w) => !w.node).map((w) => w.message)]) list.append(make('li', {}, m));
      list.hidden = !list.children.length;
    }

    function info(n) {
      const kind = byType()[n.type];
      return kind ? kind.fields.filter((f) => n[f.key] !== undefined && n[f.key] !== '').map((f) => [f.label, typeof n[f.key] === 'object' ? JSON.stringify(n[f.key]) : String(n[f.key])]) : [];
    }

    flow.afterDraw = () => {
      flow.world.querySelectorAll('.flow-node').forEach((g) => g.classList.toggle('selected', g.dataset.id === selected));
    };

    // --- changes: each one undoable, the page warned before leaving unsaved -------------------------
    function change(fn, keep = false) {
      if (!keep) { past.push(JSON.stringify(graph)); if (past.length > 100) past.shift(); future.length = 0; }
      fn();
      dirty = true;
      redraw();
      tell();
    }
    function step(from, to) {
      if (!from.length) return;
      to.push(JSON.stringify(graph));
      graph = JSON.parse(from.pop());
      if (selected && !node(selected)) selected = null;
      dirty = true;
      redraw(); showInspector(); tell();
    }
    undo.addEventListener('click', () => step(past, future));
    redo.addEventListener('click', () => step(future, past));
    window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

    function tell(text) {
      save.disabled = !dirty;
      undo.disabled = !past.length;
      redo.disabled = !future.length;
      remove.disabled = !selected || ['start', 'input'].includes((node(selected) || {}).type);
      if (text) { status.textContent = text; return; }
      const n = problems.length;
      status.textContent = dirty ? 'Unsaved changes' : n ? `${n} problem${n > 1 ? 's' : ''}: new jobs keep using its last working state until they're fixed` : 'Saved';
      status.classList.toggle('bad', !dirty && n > 0);
    }

    // --- saving ---------------------------------------------------------------------------------------
    async function doSave() {
      if (!dirty) return;
      save.disabled = true;
      tell('Saving…');
      try {
        const r = await fetch(`/workflows/${ed.workflow.id}/save`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: ed.mode, graph, base }),
        });
        const answer = await r.json();
        if (!r.ok) { tell(answer.error || "Couldn't save."); save.disabled = false; return; }
        base = answer.base; problems = answer.problems || []; warnings = answer.warnings || []; dirty = false;
        redraw(); showInspector(); tell();
      } catch (err) { tell("Couldn't save: is golem there?"); save.disabled = false; }
    }
    save.addEventListener('click', doSave);

    // --- the palette ----------------------------------------------------------------------------------
    function buildPalette() {
      const groups = {};
      for (const kind of ed.catalog) {
        if (ed.start === 'sub' ? kind.type === 'end' : kind.type === 'output') continue;
        (groups[kind.group] = groups[kind.group] || []).push(kind);
      }
      const box = make('div', { class: 'editor-kinds' });
      for (const [group, kinds] of Object.entries(groups)) {
        const section = make('div', {});
        section.append(make('h4', {}, group));
        for (const kind of kinds) {
          const b = make('button', { type: 'button', title: kind.help }, kind.label);
          b.addEventListener('click', () => { add(kind); palette.open = false; });
          section.append(b);
        }
        box.append(section);
      }
      palette.append(box);
    }

    function freshId(base) {
      const stem = String(base || 'block').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'block';
      let id = stem, i = 1;
      while (node(id)) id = `${stem}-${++i}`;
      return id;
    }

    function add(kind) {
      const box = flow.svg.getBoundingClientRect();
      const at = flow.toWorld(box.left + box.width / 2 - (W * flow.view.k) / 2, box.top + box.height / 3);
      const n = { id: '', type: kind.type, x: snap(at.x), y: snap(at.y) };
      const taken = (x, y) => graph.nodes.some((o) => Math.abs(o.x - x) < W && Math.abs(o.y - y) < H + 20);
      while (taken(n.x, n.y)) n.y += 110;  // below what's there, not on top of it
      for (const f of kind.fields) if (f.default !== undefined) n[f.key] = f.default;
      if (kind.type === 'agent') { n.name = freshId('agent'); n.instructions = ''; }
      if (kind.type === 'container') { const first = Object.keys(ed.subs)[0]; if (first) n.workflow = first; n.tab = 'auto'; }
      if (kind.type === 'condition') { n.join = 'all'; n.rows = []; }
      n.id = freshId(n.name || n.counter || (kind.type === 'output' ? 'out-' + (n.name || 'done') : kind.type));
      change(() => graph.nodes.push(n));
      select(n.id);
    }

    // --- choosing, moving and wiring blocks -------------------------------------------------------------
    function select(id) { selected = id; flow.afterDraw(); showInspector(); tell(); }

    remove.addEventListener('click', () => deleteSelected());
    function deleteSelected() {
      const n = node(selected);
      if (!n || ['start', 'input'].includes(n.type)) return;
      change(() => {
        graph.nodes = graph.nodes.filter((x) => x.id !== n.id);
        graph.edges = graph.edges.filter((e) => e.from !== n.id && e.to !== n.id);
      });
      select(null);
    }

    document.addEventListener('keydown', (e) => {
      if (small || !graph) return;
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); doSave(); return; }
      if (typing) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? step(future, past) : step(past, future); }
      else if ((e.key === 'Delete' || e.key === 'Backspace') && selected) { e.preventDefault(); deleteSelected(); }
      else if (e.key === 'Escape') select(null);
    });

    let drag = null;
    flow.grab = (e) => {
      if (small || !graph || e.button > 0) return false;
      const port = e.target.closest('.flow-port');
      const g = e.target.closest('.flow-node');
      if (port && g) {  // wiring: from this output to wherever it's dropped
        const from = node(g.dataset.id);
        drag = { kind: 'wire', from: from.id, port: port.dataset.port, line: el('path', { class: 'flow-wire editor-wiring' }, flow.world) };
      } else if (g) {
        const n = node(g.dataset.id);
        const at = flow.toWorld(e.clientX, e.clientY);
        drag = { kind: 'move', id: n.id, dx: at.x - n.x, dy: at.y - n.y, moved: false, before: JSON.stringify(graph) };
        if (selected !== n.id) select(n.id);
      } else {
        if (selected) select(null);
        return false;  // the background: flow.js pans
      }
      flow.svg.setPointerCapture?.(e.pointerId);
      e.preventDefault();
      return true;
    };
    flow.svg.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const at = flow.toWorld(e.clientX, e.clientY);
      if (drag.kind === 'move') {
        const n = node(drag.id);
        const x = snap(at.x - drag.dx), y = snap(at.y - drag.dy);
        if (x !== n.x || y !== n.y) { n.x = x; n.y = y; drag.moved = true; redraw(); }
      } else {
        const from = flow.data.nodes.find((x) => x.id === drag.from);
        drag.line.setAttribute('d', G.wirePath(G.portAt(from, drag.port), at));
        drag.line.remove(); flow.world.append(drag.line);
      }
    });
    flow.svg.addEventListener('pointerup', (e) => {
      if (!drag) return;
      const d = drag; drag = null;
      if (d.kind === 'move') {
        if (d.moved) { past.push(d.before); future.length = 0; dirty = true; tell(); }
        return;
      }
      d.line.remove();
      const under = document.elementFromPoint(e.clientX, e.clientY);
      const target = under && under.closest && under.closest('.flow-node');
      const to = target && node(target.dataset.id);
      change(() => {
        graph.edges = graph.edges.filter((x) => !(x.from === d.from && x.port === d.port));
        if (to && !['start', 'input'].includes(to.type)) graph.edges.push({ from: d.from, port: d.port, to: to.id });
      });
    });
    flow.svg.addEventListener('dblclick', (e) => {
      const g = e.target.closest('.flow-node');
      const n = g && node(g.dataset.id);
      if (n && n.type === 'container' && ed.subs[n.workflow]) {
        if (!dirty || confirm('Leave without saving?')) location.href = `/workflows/${ed.subs[n.workflow].id}`;
      }
    });

    // --- the inspector: the selected block's settings, or the workflow's intro ---------------------------
    function showInspector() {
      inspector.textContent = '';
      const n = node(selected);
      if (!n) {
        inspector.append(make('h3', {}, 'The whole workflow'));
        inspector.append(make('p', { class: 'muted' }, 'Choose a block to set it up. Drag from a block’s dot to wire that output to another block; drop it on empty space to unwire it.'));
        const label = make('label', {}, 'Text every agent block gets');
        const box = make('textarea', { rows: 8 });
        box.value = graph.intro || '';
        let first = true;
        box.addEventListener('input', () => { change(() => { graph.intro = box.value; }, !first); first = false; });
        label.append(box);
        inspector.append(label);
        return;
      }
      const kind = byType()[n.type] || { label: n.type, help: '', fields: [] };
      inspector.append(make('h3', {}, kind.label));
      if (kind.help) inspector.append(make('p', { class: 'muted' }, kind.help));
      inspector.append(make('p', { class: 'muted editor-id' }, `id: ${n.id}`));
      for (const f of kind.fields) inspector.append(field(n, f));
      const mine = problems.filter((p) => p.node === n.id);
      for (const p of mine) inspector.append(make('p', { class: 'flow-problem' }, p.message));
      if (n.type === 'container' && ed.subs[n.workflow]) {
        const open = make('a', { href: `/workflows/${ed.subs[n.workflow].id}` }, `Open ${ed.subs[n.workflow].name}`);
        inspector.append(make('p', {}, ''), open);
      }
    }

    function field(n, f) {
      const wrap = make('label', { class: 'editor-field' }, f.label);
      let first = true;
      const set = (value) => { change(() => { if (value === '' || value === null || value === false) delete n[f.key]; else n[f.key] = value; }, !first); first = false; };
      const options = (values, current) => values.map(([v, label]) => { const o = make('option', { value: v }, label); if (String(current ?? '') === String(v)) o.selected = true; return o; });
      let input;
      switch (f.kind) {
        case 'textarea':
          input = make('textarea', { rows: f.key === 'instructions' ? 14 : 4 }); input.value = n[f.key] || '';
          input.addEventListener('input', () => set(input.value));
          break;
        case 'number':
          input = make('input', { type: 'number', min: f.min, max: f.max }); input.value = n[f.key] ?? '';
          input.addEventListener('input', () => set(input.value === '' ? '' : Number(input.value)));
          break;
        case 'checkbox':
          input = make('input', { type: 'checkbox' }); input.checked = !!n[f.key];
          input.addEventListener('change', () => { first = true; set(input.checked); });
          wrap.classList.add('check');
          break;
        case 'select':
          input = make('select', {}); input.append(...options(f.options.map((v) => [v, v || '(usual)']), n[f.key]));
          input.addEventListener('change', () => { first = true; set(input.value); redraw(); });
          break;
        case 'port':
          input = make('select', {}); input.append(...options([['', '(its first output)'], ...portsOf(n).map((p) => [p, p])], n[f.key]));
          input.addEventListener('change', () => { first = true; set(input.value); });
          break;
        case 'agent':
          input = make('select', {});
          input.append(...options([['', '(the next agent)'], ...graph.nodes.filter((x) => x.type === 'agent').map((x) => [x.id, x.name || x.id])], n[f.key]));
          input.addEventListener('change', () => { first = true; set(input.value); });
          break;
        case 'sub':
          input = make('select', {}); input.append(...options([['', '(choose)'], ...Object.entries(ed.subs).map(([k, s]) => [k, s.name])], n[f.key]));
          input.addEventListener('change', () => {
            change(() => { n.workflow = input.value; n.tab = 'auto'; const outs = portsOf(n); graph.edges = graph.edges.filter((e) => e.from !== n.id || outs.includes(e.port)); });
            showInspector();
          });
          break;
        case 'tab': {
          const s = ed.subs[n.workflow] || { tabs: [] };
          input = make('select', {}); input.append(...options([['auto', "the job's mode's, else default"], ['default', 'default'], ...s.tabs.map((t) => [t, t])], n[f.key] || 'auto'));
          input.addEventListener('change', () => { first = true; set(input.value); });
          break;
        }
        case 'outputs':
          return outputsField(n, f);
        case 'rows':
          return rowsField(n, f);
        default:
          input = make('input', { type: 'text' }); input.value = n[f.key] || '';
          input.addEventListener('input', () => set(input.value));
      }
      wrap.append(input);
      if (f.help) wrap.append(make('small', { class: 'muted' }, f.help));
      return wrap;
    }

    function outputsField(n, f) {
      const box = make('fieldset', { class: 'editor-field' });
      box.append(make('legend', {}, f.label));
      if (f.help) box.append(make('small', { class: 'muted' }, f.help));
      (n.outputs || []).forEach((o, i) => {
        const row = make('div', { class: 'editor-row' });
        const name = make('input', { type: 'text', placeholder: 'name', 'aria-label': 'Output name' }); name.value = o.name;
        const about = make('input', { type: 'text', placeholder: 'when to take it', 'aria-label': 'When to take it' }); about.value = o.about || '';
        const drop = make('button', { type: 'button', class: 'quiet', 'aria-label': 'Remove output' }, '✕');
        let first = true;
        name.addEventListener('input', () => { const old = o.name; change(() => { o.name = name.value.trim(); graph.edges.forEach((e) => { if (e.from === n.id && e.port === old) e.port = o.name; }); }, !first); first = false; });
        about.addEventListener('input', () => { change(() => { o.about = about.value; }, !first); first = false; });
        drop.addEventListener('click', () => { change(() => { n.outputs.splice(i, 1); graph.edges = graph.edges.filter((e) => e.from !== n.id || portsOf(n).includes(e.port)); }); showInspector(); });
        row.append(name, about, drop);
        box.append(row);
      });
      const more = make('button', { type: 'button' }, 'Add an output');
      more.addEventListener('click', () => {
        change(() => {
          n.outputs = n.outputs || [];
          if (!n.outputs.length) n.outputs.push({ name: n.type === 'agent' ? 'done' : 'replied', about: '' });
          n.outputs.push({ name: `out-${n.outputs.length + 1}`, about: '' });
        });
        showInspector();
      });
      box.append(more);
      return box;
    }

    function rowsField(n, f) {
      const box = make('fieldset', { class: 'editor-field' });
      box.append(make('legend', {}, f.label));
      (n.rows || []).forEach((r, i) => {
        const row = make('div', { class: 'editor-row' });
        const of = make('select', { 'aria-label': 'Looks at' });
        for (const [v, label] of [['counter', 'count'], ['visits', 'visits to'], ['mode', 'the mode']]) { const o = make('option', { value: v }, label); if (r.of === v) o.selected = true; of.append(o); }
        const name = make('input', { type: 'text', placeholder: r.of === 'visits' ? 'block id' : 'counter', 'aria-label': 'Which' }); name.value = r.name || '';
        const op = make('select', { 'aria-label': 'Compared' });
        for (const v of r.of === 'mode' ? ['==', '!='] : ['>', '>=', '<', '<=', '==', '!=']) { const o = make('option', { value: v }, v === '==' && r.of === 'mode' ? 'is' : v === '!=' && r.of === 'mode' ? 'is not' : v); if (r.op === v) o.selected = true; op.append(o); }
        const value = make('input', { type: 'text', 'aria-label': 'Value', size: 6 }); value.value = r.value ?? '';
        const drop = make('button', { type: 'button', class: 'quiet', 'aria-label': 'Remove row' }, '✕');
        let first = true;
        const update = () => { change(() => { r.of = of.value; r.name = r.of === 'mode' ? '' : name.value.trim(); r.op = op.value; r.value = value.value.trim(); }, !first); first = false; };
        of.addEventListener('change', () => { first = true; update(); showInspector(); });
        [name, value].forEach((x) => x.addEventListener('input', update));
        op.addEventListener('change', () => { first = true; update(); });
        drop.addEventListener('click', () => { change(() => n.rows.splice(i, 1)); showInspector(); });
        if (r.of === 'mode') name.hidden = true;
        row.append(of, name, op, value, drop);
        box.append(row);
      });
      const more = make('button', { type: 'button' }, 'Add a row');
      more.addEventListener('click', () => { change(() => { n.rows = n.rows || []; n.rows.push({ of: 'counter', name: 'tries', op: '<', value: '3' }); }); showInspector(); });
      box.append(more);
      return box;
    }

    load();
  }
})();
