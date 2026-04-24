from flask import Flask, request, jsonify
import heapq
import json

app = Flask(__name__)

# --- HARTA CLĂDIRII (Graf bidirecțional) ---
cladire_graf = {
    'A': {'C': 5, 'F': 10},
    'B': {'D': 4, 'E': 6, 'F': 8, 'G': 5},
    'C': {'A': 5, 'E': 4, 'D': 7},
    'D': {'C': 7, 'B': 4},
    'E': {'C': 4, 'F': 5, 'B': 6},
    'F': {'A': 10, 'E': 5, 'B': 8, 'G': 4},
    'G': {'F': 4, 'B': 5}
}

iesiri_sigure = ['F']
stare_camere = {nod: {'gaz_adc': 1200, 'temp': 22.0} for nod in cladire_graf}


def verifica_bidirectional():
    erori = []
    total = 0
    for nod, vecini in cladire_graf.items():
        for vecin, dist in vecini.items():
            total += 1
            if vecin not in cladire_graf or nod not in cladire_graf[vecin]:
                erori.append(f"  ✗ {nod}→{vecin}: lipsește invers")
                continue
            if cladire_graf[vecin][nod] != dist:
                erori.append(f"  ✗ {nod}→{vecin}={dist} ≠ {vecin}→{nod}={cladire_graf[vecin][nod]}")
    print("\n--- Verificare bidirecționalitate ---")
    if erori:
        for e in erori:
            print(e)
    else:
        print(f"  ✓ {total} conexiuni consistente ({total // 2} coridoare unice)")
    print("-----------------------------------\n")


def muchii_unice():
    """Returnează lista de muchii unice cu cheie sortată."""
    vazute = set()
    rezultat = []
    for nod, vecini in cladire_graf.items():
        for vecin in vecini:
            cheie = tuple(sorted([nod, vecin]))
            if cheie in vazute:
                continue
            vazute.add(cheie)
            rezultat.append(cheie)
    return sorted(rezultat)


def dijkstra(start, exit_node, ponderi):
    if start not in cladire_graf or exit_node not in cladire_graf:
        return [], float('inf')
    distante = {nod: float('inf') for nod in cladire_graf}
    predecesori = {nod: None for nod in cladire_graf}
    distante[start] = 0
    coada = [(0, start)]
    while coada:
        cost_curent, nod_curent = heapq.heappop(coada)
        if nod_curent == exit_node:
            break
        if cost_curent > distante[nod_curent]:
            continue
        for vecin in cladire_graf[nod_curent]:
            cheie = tuple(sorted([nod_curent, vecin]))
            pondere = ponderi.get(cheie, 1)
            noua_dist = cost_curent + pondere
            if noua_dist < distante[vecin]:
                distante[vecin] = noua_dist
                predecesori[vecin] = nod_curent
                heapq.heappush(coada, (noua_dist, vecin))
    if distante[exit_node] == float('inf'):
        return [], float('inf')
    ruta = []
    nod = exit_node
    while nod is not None:
        ruta.insert(0, nod)
        nod = predecesori[nod]
    return ruta, distante[exit_node]


# --- API DEMO (client-side weights trimise prin query) ---
@app.route('/api/demo')
def api_demo():
    start = request.args.get('start', 'A')
    exit_node = request.args.get('exit', 'F')
    weights_json = request.args.get('weights', '{}')

    try:
        weights_user = json.loads(weights_json)
    except Exception:
        weights_user = {}

    # Construim dict de ponderi pentru Dijkstra (default 1, peste scriem ce a trimis user-ul)
    ponderi = {}
    for cheie in muchii_unice():
        key_str = f"{cheie[0]}-{cheie[1]}"
        valoare = weights_user.get(key_str, 1)
        try:
            valoare = max(1, int(valoare))
        except (ValueError, TypeError):
            valoare = 1
        ponderi[cheie] = valoare

    ruta, cost = dijkstra(start, exit_node, ponderi)

    muchii = []
    for cheie in muchii_unice():
        muchii.append({
            'de_la': cheie[0],
            'la': cheie[1],
            'id': f"{cheie[0]}-{cheie[1]}",
            'pondere': ponderi[cheie]
        })

    return jsonify({
        'start': start,
        'exit': exit_node,
        'muchii': muchii,
        'ruta': ruta,
        'cost_total': cost if cost != float('inf') else None,
        'ruta_posibila': cost != float('inf')
    })


# --- ENDPOINTS ESP32 (păstrate pentru plăcuțele fizice) ---
@app.route('/update_camera', methods=['POST'])
def update_camera():
    date = request.json
    camera = date.get('camera')
    if camera in stare_camere:
        stare_camere[camera]['gaz_adc'] = date.get('gaz', 1200)
        stare_camere[camera]['temp'] = date.get('temp', 22.0)
        status_foc = date.get('foc')
        msg = f"ALERTA (ADC:{stare_camere[camera]['gaz_adc']} Temp:{stare_camere[camera]['temp']})" if status_foc else "AER CURAT"
        print(f" Camera {camera}: {msg}")
    return jsonify({"status": "success"}), 200


@app.route('/stare_usa/<camera_start>/<camera_urmatoare>', methods=['GET'])
def stare_usa(camera_start, camera_urmatoare):
    if camera_start not in cladire_graf or camera_urmatoare not in cladire_graf:
        return jsonify({"comanda": "ROSU", "motiv": "Cameră necunoscută"}), 400
    # Pentru ESP32: folosim ponderi default 1, sau 100 la camerele în alertă
    ponderi = {}
    for cheie in muchii_unice():
        ponderi[cheie] = 1
    for cam, st in stare_camere.items():
        if st['gaz_adc'] >= 1600 or st['temp'] >= 45:
            for vecin in cladire_graf[cam]:
                cheie = tuple(sorted([cam, vecin]))
                ponderi[cheie] = 100
    iesire = min(iesiri_sigure, key=lambda x: dijkstra(camera_start, x, ponderi)[1])
    ruta, cost = dijkstra(camera_start, iesire, ponderi)
    if not ruta or cost == float('inf'):
        return jsonify({"comanda": "ROSU", "motiv": "FĂRĂ SCĂPARE"})
    if len(ruta) == 1:
        return jsonify({"comanda": "VERDE", "motiv": "Ești la ieșire"})
    pasul = ruta[1]
    if pasul == camera_urmatoare:
        return jsonify({"comanda": "VERDE", "motiv": f"Ruta: {' → '.join(ruta)}"})
    return jsonify({"comanda": "ROSU", "motiv": f"Mergi spre {pasul}"})


@app.route('/')
def dashboard():
    return HTML_DASHBOARD


HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="ro">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Demo Dijkstra - Ponderi Manuale</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 20px;
    }
    .container { max-width: 1400px; margin: 0 auto; }
    header { margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid #334155; }
    header h1 { font-size: 26px; color: #f8fafc; margin-bottom: 6px; }
    header .subtitle { color: #94a3b8; font-size: 13px; }

    main { display: grid; grid-template-columns: 1fr 360px; gap: 20px; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; } }

    .map-container, .panel {
      background: #1e293b; border-radius: 12px; padding: 18px;
      border: 1px solid #334155;
    }
    .map-container svg { width: 100%; height: auto; max-height: 640px; display: block; }
    .sidebar { display: flex; flex-direction: column; gap: 14px; }

    .panel h3 {
      font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;
      color: #94a3b8; margin-bottom: 12px;
    }

    .config-row { margin-bottom: 10px; }
    .config-row label {
      display: block; font-size: 12px; color: #94a3b8;
      margin-bottom: 4px; font-weight: 600;
    }
    .config-row select {
      width: 100%; padding: 8px 10px; background: #0f172a;
      color: #f8fafc; border: 1px solid #334155; border-radius: 6px;
      font-size: 14px; cursor: pointer;
    }
    .config-row select:focus { outline: none; border-color: #60a5fa; }

    /* Edge weight editor rows */
    .edge-grid {
      display: flex; flex-direction: column; gap: 6px;
    }
    .edge-row {
      display: flex; align-items: center; gap: 8px;
      padding: 6px 10px; background: #0f172a;
      border: 1px solid #334155; border-radius: 6px;
      transition: all 0.2s;
    }
    .edge-row.on-path {
      border-color: #10b981; background: rgba(16,185,129,0.08);
    }
    .edge-row.heavy {
      border-color: #ef4444; background: rgba(239,68,68,0.08);
    }
    .edge-label {
      flex: 1; font-size: 14px; font-weight: 700;
      color: #cbd5e1; letter-spacing: 0.5px;
    }
    .edge-row.on-path .edge-label { color: #6ee7b7; }
    .edge-row.heavy .edge-label { color: #fca5a5; }

    .weight-control { display: flex; align-items: center; gap: 0; }
    .weight-btn {
      width: 28px; height: 28px; padding: 0;
      background: #334155; color: #e2e8f0;
      border: 1px solid #475569; cursor: pointer;
      font-size: 16px; font-weight: 700;
      display: flex; align-items: center; justify-content: center;
      transition: all 0.15s; user-select: none;
    }
    .weight-btn:first-child { border-radius: 4px 0 0 4px; }
    .weight-btn:last-child { border-radius: 0 4px 4px 0; }
    .weight-btn:hover { background: #475569; }
    .weight-btn:active { background: #60a5fa; transform: scale(0.95); }
    .weight-btn:disabled { opacity: 0.3; cursor: not-allowed; }
    .edge-input {
      width: 48px; height: 28px; padding: 0 4px;
      background: #1e293b; color: #f8fafc;
      border: 1px solid #475569; border-left: none; border-right: none;
      font-size: 14px; font-weight: 700; text-align: center;
      font-family: inherit;
      -moz-appearance: textfield;
    }
    .edge-input::-webkit-outer-spin-button,
    .edge-input::-webkit-inner-spin-button {
      -webkit-appearance: none; margin: 0;
    }
    .edge-input:focus { outline: none; border-color: #60a5fa; background: #0f172a; }

    .btn {
      width: 100%; padding: 10px; border: none; border-radius: 6px;
      font-size: 13px; font-weight: 600; cursor: pointer;
      transition: all 0.2s; margin-top: 8px;
    }
    .btn-primary { background: #10b981; color: white; }
    .btn-primary:hover { background: #059669; }
    .btn-secondary { background: #334155; color: #e2e8f0; }
    .btn-secondary:hover { background: #475569; }
    .btn-row { display: flex; gap: 6px; }
    .btn-row .btn { margin-top: 0; }

    .status-box {
      padding: 12px; border-radius: 6px; font-size: 13px;
      border-left: 3px solid;
    }
    .status-box.ok { background: rgba(16,185,129,0.1); border-color: #10b981; }
    .status-box.fail { background: rgba(239,68,68,0.1); border-color: #ef4444; }
    .status-box strong { display: block; margin-bottom: 4px; color: #f8fafc; }
    .path-display {
      font-weight: 700; font-size: 18px; color: #6ee7b7;
      margin-top: 6px; letter-spacing: 1px;
    }
    .cost-display { font-size: 12px; color: #94a3b8; margin-top: 4px; }

    /* Node visuals */
    .node-bg { fill: #334155; }
    .ring-start { stroke: #60a5fa; stroke-width: 4; fill: none; }
    .ring-exit { stroke: #fbbf24; stroke-width: 4; fill: none; }

    .edge { stroke: #475569; stroke-width: 2; transition: all 0.3s; }
    .edge-medium { stroke: #f59e0b; stroke-width: 3; }
    .edge-heavy { stroke: #ef4444; stroke-width: 3; stroke-dasharray: 5 3; }
    .edge-path { stroke: #10b981; stroke-width: 5; filter: drop-shadow(0 0 6px #10b981); }

    .node-circle { stroke: rgba(255,255,255,0.3); stroke-width: 2; }
    .node-label {
      fill: white; font-weight: 700; font-size: 22px;
      text-anchor: middle; dominant-baseline: central; pointer-events: none;
    }
    .role-label {
      font-size: 10px; font-weight: 700; text-anchor: middle;
      dominant-baseline: central;
    }
    .role-label.start { fill: #60a5fa; }
    .role-label.exit { fill: #fbbf24; }

    .edge-weight-bg { fill: #0f172a; stroke: #334155; stroke-width: 1; }
    .edge-weight-bg.medium { fill: #451a03; stroke: #f59e0b; }
    .edge-weight-bg.heavy { fill: #450a0a; stroke: #ef4444; }
    .edge-weight-bg.path { fill: #022c22; stroke: #10b981; }
    .edge-weight { font-size: 11px; font-weight: 700; text-anchor: middle; dominant-baseline: central; }
    .edge-weight.normal { fill: #cbd5e1; }
    .edge-weight.medium { fill: #fbbf24; }
    .edge-weight.heavy { fill: #fca5a5; }
    .edge-weight.path { fill: #6ee7b7; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🎓 Demo Dijkstra - Controlează Ponderile</h1>
      <div class="subtitle">Modifică ponderea fiecărei muchii și vezi în timp real cum algoritmul recalculează traseul optim.</div>
    </header>

    <main>
      <div class="map-container">
        <svg viewBox="0 0 800 600" id="mapSvg">
          <g id="edges"></g>
          <g id="edgeLabels"></g>
          <g id="nodes"></g>
        </svg>
      </div>

      <aside class="sidebar">
        <div class="panel">
          <h3>⚙️ Configurare traseu</h3>
          <div class="config-row">
            <label>📍 START (unde mă aflu)</label>
            <select id="selStart"></select>
          </div>
          <div class="config-row">
            <label>🚪 IEȘIRE (destinație)</label>
            <select id="selExit"></select>
          </div>
        </div>

        <div class="panel">
          <h3>🔧 Ponderi muchii (editează!)</h3>
          <div class="edge-grid" id="edgeEditor"></div>
          <div class="btn-row" style="margin-top:10px">
            <button class="btn btn-secondary" onclick="resetWeights()">↻ Reset la 1</button>
            <button class="btn btn-secondary" onclick="randomize()">🎲 Aleatoriu</button>
          </div>
        </div>

        <div class="panel">
          <h3>📊 Rezultat Dijkstra</h3>
          <div id="statusArea"></div>
        </div>
      </aside>
    </main>
  </div>

  <script>
    const NODE_POSITIONS = {
      'A': [180, 120], 'B': [550, 420], 'C': [180, 300], 'D': [280, 510],
      'E': [430, 260], 'F': [680, 120], 'G': [700, 500]
    };
    const NODES = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];
    const EDGES = [
      ['A','C'], ['A','F'], ['B','D'], ['B','E'], ['B','F'],
      ['B','G'], ['C','D'], ['C','E'], ['E','F'], ['F','G']
    ];
    const SVG_NS = 'http://www.w3.org/2000/svg';

    // State: weights stored client-side
    let weights = {};
    EDGES.forEach(([a, b]) => { weights[a + '-' + b] = 1; });

    // Selectoare Start/Exit
    const selStart = document.getElementById('selStart');
    const selExit = document.getElementById('selExit');
    NODES.forEach(n => {
      selStart.innerHTML += `<option value="${n}">Camera ${n}</option>`;
      selExit.innerHTML += `<option value="${n}">Camera ${n}</option>`;
    });
    selStart.value = 'D';
    selExit.value = 'F';
    selStart.onchange = update;
    selExit.onchange = update;

    // Editor de ponderi: fiecare muchie are input + butoane − și +
    const editor = document.getElementById('edgeEditor');
    EDGES.forEach(([a, b]) => {
      const id = a + '-' + b;
      const row = document.createElement('div');
      row.className = 'edge-row';
      row.id = 'row-' + id;
      row.innerHTML =
        `<span class="edge-label">${a} ↔ ${b}</span>
         <div class="weight-control">
           <button class="weight-btn" onclick="changeWeight('${id}', -1)">−</button>
           <input type="number" class="edge-input" id="w-${id}"
                  min="1" max="999" value="1">
           <button class="weight-btn" onclick="changeWeight('${id}', +1)">+</button>
         </div>`;
      editor.appendChild(row);

      const input = document.getElementById('w-' + id);
      // Ascult orice modificare a valorii (typing, paste, spinner, schimbare programatică)
      const handler = () => {
        let v = parseInt(input.value);
        if (isNaN(v) || v < 1) v = 1;
        if (v > 999) v = 999;
        weights[id] = v;
        update();
      };
      input.addEventListener('input', handler);
      input.addEventListener('change', handler);
    });

    function changeWeight(id, delta) {
      const input = document.getElementById('w-' + id);
      let v = parseInt(input.value) || 1;
      v += delta;
      if (v < 1) v = 1;
      if (v > 999) v = 999;
      input.value = v;
      weights[id] = v;
      update();
    }

    function resetWeights() {
      EDGES.forEach(([a, b]) => {
        const id = a + '-' + b;
        weights[id] = 1;
        document.getElementById('w-' + id).value = 1;
      });
      update();
    }

    function randomize() {
      EDGES.forEach(([a, b]) => {
        const id = a + '-' + b;
        const v = Math.floor(Math.random() * 20) + 1;
        weights[id] = v;
        document.getElementById('w-' + id).value = v;
      });
      update();
    }

    function el(tag, attrs, text) {
      const e = document.createElementNS(SVG_NS, tag);
      for (const k in attrs) e.setAttribute(k, attrs[k]);
      if (text !== undefined) e.textContent = text;
      return e;
    }

    function isEdgeOnPath(a, b, path) {
      if (!path || path.length < 2) return false;
      for (let i = 0; i < path.length - 1; i++) {
        if ((path[i] === a && path[i+1] === b) || (path[i] === b && path[i+1] === a)) return true;
      }
      return false;
    }

    function weightCategory(w) {
      if (w <= 1) return 'normal';
      if (w <= 20) return 'medium';
      return 'heavy';
    }

    function render(data) {
      const gEdges = document.getElementById('edges');
      const gLabels = document.getElementById('edgeLabels');
      const gNodes = document.getElementById('nodes');
      gEdges.innerHTML = ''; gLabels.innerHTML = ''; gNodes.innerHTML = '';

      const path = data.ruta || [];

      // Edges + weight labels
      data.muchii.forEach(e => {
        const a = NODE_POSITIONS[e.de_la];
        const b = NODE_POSITIONS[e.la];
        const onPath = isEdgeOnPath(e.de_la, e.la, path);
        const cat = weightCategory(e.pondere);

        let cls = 'edge';
        if (cat === 'medium') cls = 'edge edge-medium';
        if (cat === 'heavy') cls = 'edge edge-heavy';
        if (onPath) cls = 'edge edge-path';

        gEdges.appendChild(el('line', {
          x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: cls
        }));

        const mx = (a[0] + b[0]) / 2;
        const my = (a[1] + b[1]) / 2;
        const bgCls = onPath ? 'edge-weight-bg path' :
                      (cat === 'heavy' ? 'edge-weight-bg heavy' :
                      (cat === 'medium' ? 'edge-weight-bg medium' : 'edge-weight-bg'));
        const txtCls = onPath ? 'edge-weight path' :
                       (cat === 'heavy' ? 'edge-weight heavy' :
                       (cat === 'medium' ? 'edge-weight medium' : 'edge-weight normal'));
        gLabels.appendChild(el('rect', {
          x: mx - 20, y: my - 10, width: 40, height: 20, rx: 4, class: bgCls
        }));
        gLabels.appendChild(el('text', { x: mx, y: my + 1, class: txtCls }, e.pondere));

        // Update sidebar row styling
        const row = document.getElementById('row-' + e.id);
        if (row) {
          row.classList.remove('on-path', 'heavy');
          if (onPath) row.classList.add('on-path');
          else if (cat === 'heavy') row.classList.add('heavy');
        }
      });

      // Nodes
      NODES.forEach(n => {
        const [x, y] = NODE_POSITIONS[n];
        const g = el('g', {});
        const isStart = data.start === n;
        const isExit = data.exit === n;

        g.appendChild(el('circle', { cx: x, cy: y, r: 38, class: 'node-circle node-bg' }));
        if (isStart && isExit) {
          g.appendChild(el('circle', { cx: x, cy: y, r: 44, class: 'ring-start' }));
          g.appendChild(el('circle', { cx: x, cy: y, r: 50, class: 'ring-exit' }));
        } else {
          if (isStart) g.appendChild(el('circle', { cx: x, cy: y, r: 44, class: 'ring-start' }));
          if (isExit) g.appendChild(el('circle', { cx: x, cy: y, r: 44, class: 'ring-exit' }));
        }

        g.appendChild(el('text', { x: x, y: y, class: 'node-label' }, n));

        let yOffset = -55;
        if (isStart) {
          g.appendChild(el('text', { x: x, y: y + yOffset, class: 'role-label start' }, '📍 START'));
          yOffset -= 14;
        }
        if (isExit) {
          g.appendChild(el('text', { x: x, y: y + yOffset, class: 'role-label exit' }, '🚪 IEȘIRE'));
        }

        gNodes.appendChild(g);
      });
    }

    function renderStatus(data) {
      const area = document.getElementById('statusArea');
      if (!data.ruta_posibila) {
        area.innerHTML = '<div class="status-box fail"><strong>⚠ Nicio rută</strong>Nu există drum de la ' + data.start + ' la ' + data.exit + '.</div>';
      } else if (data.ruta.length === 1) {
        area.innerHTML = '<div class="status-box ok"><strong>✓ Ești deja la ieșire</strong>START = IEȘIRE.</div>';
      } else {
        area.innerHTML = '<div class="status-box ok">' +
                         '<strong>✓ Rută optimă (' + (data.ruta.length - 1) + ' pași)</strong>' +
                         '<div class="path-display">' + data.ruta.join(' → ') + '</div>' +
                         '<div class="cost-display">Cost total (suma ponderilor): <strong>' + data.cost_total + '</strong></div>' +
                         '</div>';
      }
    }

    async function update() {
      const params = new URLSearchParams({
        start: selStart.value,
        exit: selExit.value,
        weights: JSON.stringify(weights)
      });
      try {
        const res = await fetch('/api/demo?' + params);
        const data = await res.json();
        render(data);
        renderStatus(data);
      } catch (e) { console.error(e); }
    }

    update();
  </script>
</body>
</html>
"""


if __name__ == '__main__':
    print("========================================")
    print("DEMO DIJKSTRA - PONDERI MANUALE")
    print("========================================")
    verifica_bidirectional()
    print(" Dashboard:  http://localhost:5000/")
    print("========================================")
    app.run(host='0.0.0.0', port=5000, debug=False)