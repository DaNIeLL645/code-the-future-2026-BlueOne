from flask import Flask, request, jsonify
import heapq
import json
import sys
import os

app = Flask(__name__)

# ==========================================
# 1. INCARCAREA HARTII DIN JSON
# ==========================================
# Poti schimba fisierul fie prin argument in linia de comanda:
#     python server.py harta_cladire_2.json
# Fie prin variabila de mediu HARTA_JSON
# Fie va folosi implicit "harta_cladire_1.json"

def incarca_harta(cale_fisier):
    """Incarca graful cladirii din fisier JSON."""
    if not os.path.exists(cale_fisier):
        print(f"[EROARE] Fisierul {cale_fisier} nu exista!")
        sys.exit(1)
    
    with open(cale_fisier, 'r', encoding='utf-8') as f:
        harta = json.load(f)
    
    # Validari de baza
    campuri_necesare = ['graf', 'iesiri_sigure', 'pozitii_vizuale']
    for camp in campuri_necesare:
        if camp not in harta:
            print(f"[EROARE] Fisierul JSON nu contine campul '{camp}'")
            sys.exit(1)
    
    # Verificam ca toate iesirile sunt noduri valide in graf
    for iesire in harta['iesiri_sigure']:
        if iesire not in harta['graf']:
            print(f"[EROARE] Iesirea '{iesire}' nu exista in graf!")
            sys.exit(1)
    
    print(f"[OK] Harta incarcata: {harta.get('nume_cladire', 'Fara nume')}")
    print(f"     Camere: {list(harta['graf'].keys())}")
    print(f"     Iesiri sigure: {harta['iesiri_sigure']}")
    return harta


# Alegere fisier harta: argument > variabila mediu > implicit
if len(sys.argv) > 1:
    CALE_HARTA = sys.argv[1]
else:
    CALE_HARTA = os.environ.get('HARTA_JSON', 'harta_cladire_2.json')

harta_cladire = incarca_harta(CALE_HARTA)

cladire_graf = harta_cladire['graf']
iesiri_sigure = harta_cladire['iesiri_sigure']
pozitii_vizuale = harta_cladire['pozitii_vizuale']
nume_cladire = harta_cladire.get('nume_cladire', 'Cladire')

# Setam aerul curat de baza la 1500 (valoarea normala a senzorului MQ)
stare_camere = {nod: {'foc': False, 'gaz_adc': 1500, 'temp': 22.0} for nod in cladire_graf}


# ==========================================
# 2. MATEMATICA DE SUPRAVIETUIRE (FED)
# ==========================================
def evalueaza_cost_coridor(n1, n2, dist_baza):
    gaz = max(stare_camere[n1]['gaz_adc'], stare_camere[n2]['gaz_adc'])
    temp = max(stare_camere[n1]['temp'], stare_camere[n2]['temp'])
    
    if temp >= 80.0: 
        return 9999.0  # Blocaj termic letal
        
    # Penalizam coridorul doar daca gazul trece de nivelul normal de 1500
    adc_norm = max(0, gaz - 1500) 
    ppm_simulat = min(5000.0, (adc_norm / 2100.0) * 5000.0)
    
    viteza = 1.35 
    if ppm_simulat > 50:
        viteza = max(0.2, 1.35 - (1.15 * min(ppm_simulat / 1000.0, 1.0)))
        
    timp_sec = dist_baza / viteza
    timp_min = timp_sec / 60.0
    
    alpha = 0.00002857 
    beta = 0.00000002   
    doza_totala = timp_min * ((alpha * ppm_simulat) + (beta * (temp ** 3.4)))
    
    if doza_totala >= 1.0: 
        return 9999.0  # Doza letala
        
    cost_final = timp_sec + (doza_totala * 5000)
    return round(cost_final, 1)


# ==========================================
# 3. ALGORITMUL DIJKSTRA DINAMIC
# ==========================================
def calculeaza_dijkstra_dinamic():
    graf_inversat = {nod: {} for nod in cladire_graf}
    for nod, vecini in cladire_graf.items():
        for vecin, dist_baza in vecini.items():
            graf_inversat[vecin][nod] = dist_baza

    distante = {nod: float('inf') for nod in cladire_graf}
    trasee = {nod: [] for nod in cladire_graf}
    coada = []

    for iesire in iesiri_sigure:
        if not stare_camere[iesire]['foc']:
            distante[iesire] = 0
            trasee[iesire] = [iesire]
            heapq.heappush(coada, (0, iesire))

    while coada:
        dist_curenta, nod_curent = heapq.heappop(coada)
        if dist_curenta > distante[nod_curent]: 
            continue

        for vecin, dist_baza in graf_inversat[nod_curent].items():
            cost_real = evalueaza_cost_coridor(nod_curent, vecin, dist_baza)
            
            if cost_real >= 9999.0:
                continue 
                
            noua_dist = dist_curenta + cost_real
            if noua_dist < distante[vecin]:
                distante[vecin] = noua_dist
                trasee[vecin] = [vecin] + trasee[nod_curent]
                heapq.heappush(coada, (noua_dist, vecin))
                
    return trasee, distante


# ==========================================
# 4. COMUNICAREA CU ESP32
# ==========================================
@app.route('/update_camera', methods=['POST'])
def update_camera():
    date = request.json
    camera = date.get('camera')
    
    if camera in stare_camere:
        gaz_curent = date.get('gaz', stare_camere[camera]['gaz_adc'])
        temp_curenta = date.get('temp', stare_camere[camera]['temp'])
        
        # --- PRAGURI CU HISTEREZIS ---
        PRAG_ALERTA_GAZ = 1800
        PRAG_REVENIRE_GAZ = 1500
        PRAG_ALERTA_TEMP = 50.0
        PRAG_REVENIRE_TEMP = 45.0
        
        stare_actuala_foc = stare_camere[camera]['foc']
        
        if not stare_actuala_foc:
            if gaz_curent > PRAG_ALERTA_GAZ or temp_curenta > PRAG_ALERTA_TEMP:
                stare_actuala_foc = True
        else:
            if gaz_curent < PRAG_REVENIRE_GAZ and temp_curenta < PRAG_REVENIRE_TEMP:
                stare_actuala_foc = False
        
        stare_camere[camera]['foc'] = stare_actuala_foc
        stare_camere[camera]['gaz_adc'] = gaz_curent
        stare_camere[camera]['temp'] = temp_curenta

        msg = "!!! ALERTA FOC !!!" if stare_actuala_foc else "--- SIGUR ---"
        print(f"\n[SERVER] Camera {camera}: {msg} | G: {gaz_curent} | T: {temp_curenta}°")
        
    return jsonify({"status": "success"}), 200

@app.route('/stare_usa/<camera_start>/<camera_urmatoare>', methods=['GET'])
def stare_usa(camera_start, camera_urmatoare):
    trasee, distante = calculeaza_dijkstra_dinamic()
    ruta_completa = trasee.get(camera_start, [])
    
    if distante.get(camera_start) == float('inf') or len(ruta_completa) < 2:
        return jsonify({"comanda": "ROSU", "motiv": "FARA SCAPARE / BLOCAT"})
    
    pasul_urmator_optim = ruta_completa[1]
    if pasul_urmator_optim == camera_urmatoare:
        return jsonify({"comanda": "VERDE", "motiv": f"Intra! Ruta e {' -> '.join(ruta_completa)}"})
    else:
        return jsonify({"comanda": "ROSU", "motiv": f"Ocoleste! Ruta optima e prin {pasul_urmator_optim}"})


# ==========================================
# 5. DASHBOARD LIVE - TRIMITE SI HARTA
# ==========================================
@app.route('/api/stare_live')
def stare_live():
    trasee, distante = calculeaza_dijkstra_dinamic()
    
    ponderi_active = {}
    for nod, vecini in cladire_graf.items():
        ponderi_active[nod] = {}
        for vecin, dist_baza in vecini.items():
            ponderi_active[nod][vecin] = evalueaza_cost_coridor(nod, vecin, dist_baza)

    # Construim lista muchiilor (unice, nu duble) pentru desenare
    muchii = []
    vazute = set()
    for nod, vecini in cladire_graf.items():
        for vecin in vecini:
            cheie = tuple(sorted([nod, vecin]))
            if cheie not in vazute:
                vazute.add(cheie)
                muchii.append(list(cheie))

    return jsonify({
        "camere": stare_camere,
        "trasee": trasee,
        "ponderi_active": ponderi_active,
        "pozitii": pozitii_vizuale,
        "muchii": muchii,
        "nume_cladire": nume_cladire,
        "iesiri": iesiri_sigure
    })

@app.route('/')
def index():
    # Generam optiunile de select dinamic din graf (excludem iesirile)
    optiuni_camere = [n for n in cladire_graf.keys() if n not in iesiri_sigure]
    optiuni_html = ""
    for i, cam in enumerate(optiuni_camere):
        selected = " selected" if i == 0 else ""
        optiuni_html += f'<option value="{cam}"{selected}>{cam}</option>'
    
    return HTML_PAGE.replace("{{OPTIUNI_CAMERE}}", optiuni_html).replace("{{NUME_CLADIRE}}", nume_cladire)


HTML_PAGE = """
<!DOCTYPE html>
<html lang="ro">
<head>
    <meta charset="UTF-8">
    <title>Live Dashboard Smart Exit</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #121212; color: #ffffff; text-align: center; margin: 0; padding: 20px; }
        .harta-container { background: #1e1e1e; border: 2px solid #333; border-radius: 10px; width: 100%; max-width: 900px; margin: 20px auto; padding: 20px; box-sizing: border-box; }
        svg { width: 100%; height: auto; display: block; }
        
        .node { fill: #2c3e50; stroke: #34495e; stroke-width: 3; transition: all 0.3s; }
        .node.fire { fill: #c0392b; stroke: #e74c3c; animation: pulse 0.8s infinite alternate; }
        .node.exit { fill: #16a085; stroke: #1abc9c; }
        .node-text { fill: white; font-size: 26px; font-weight: bold; text-anchor: middle; dominant-baseline: central; pointer-events: none; }
        .sensor-text { fill: #bdc3c7; font-size: 11px; font-weight: bold; text-anchor: middle; dominant-baseline: central; pointer-events: none; }
        .sensor-text.alert { fill: #f1c40f; }
        
        .edge { stroke: #555; stroke-width: 4; }
        .edge.blocked { stroke: #331111; stroke-dasharray: 5; opacity: 0.5; }
        .path { stroke: #2ecc71; stroke-width: 8; stroke-dasharray: 10; animation: march 1s linear infinite; }
        
        .weight-bg { fill: #111; stroke: #555; stroke-width: 1.5; }
        .weight-bg.blocked { fill: #300; stroke: #e74c3c; }
        .weight-text { fill: #3498db; font-size: 13px; font-weight: bold; text-anchor: middle; dominant-baseline: central; pointer-events: none; }
        .weight-text.blocked { fill: #e74c3c; }
        
        @keyframes pulse { from { filter: drop-shadow(0 0 5px #e74c3c); } to { filter: drop-shadow(0 0 20px #f1c40f); } }
        @keyframes march { to { stroke-dashoffset: -20; } }
        
        select { padding: 10px; font-size: 16px; border-radius: 5px; background: #333; color: white; border: 1px solid #555; }
        .status { margin-top: 15px; padding: 15px; border-radius: 5px; font-size: 16px; font-weight: bold; background: #222; }
        .nume-cladire { color: #3498db; font-size: 14px; margin-bottom: 10px; }
    </style>
</head>
<body>
    <h2>🔥 Evacuare Live (Costuri Matematic FED)</h2>
    <div class="nume-cladire">📍 {{NUME_CLADIRE}}</div>
    <label>Simulare evadare din camera: </label>
    <select id="camera_select">{{OPTIUNI_CAMERE}}</select>

    <div class="harta-container">
        <svg viewBox="0 0 800 600" id="harta"></svg>
        <div id="status_box" class="status">Se incarca datele...</div>
    </div>

    <script>
        // Pozitiile si muchiile vin de la server (dinamic din JSON)
        let noduri_pos = {};
        let muchii = [];
        let iesiri = [];

        async function updateDashboard() {
            try {
                let response = await fetch('/api/stare_live');
                let data = await response.json();
                
                noduri_pos = data.pozitii;
                muchii = data.muchii;
                iesiri = data.iesiri;
                
                let camera_selectata = document.getElementById('camera_select').value;
                let svg_html = '';

                // 1. Desenam muchiile
                muchii.forEach(m => {
                    let n1 = m[0], n2 = m[1];
                    let x1 = noduri_pos[n1][0], y1 = noduri_pos[n1][1];
                    let x2 = noduri_pos[n2][0], y2 = noduri_pos[n2][1];
                    
                    let p1 = data.ponderi_active[n1] ? data.ponderi_active[n1][n2] : 1;
                    let clasa_edge = (p1 >= 9999) ? "edge blocked" : "edge";
                    
                    svg_html += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="${clasa_edge}"/>`;
                });

                // 2. Desenam traseul
                let ruta = data.trasee[camera_selectata] || [];
                for(let i = 0; i < ruta.length - 1; i++) {
                    let n1 = ruta[i], n2 = ruta[i+1];
                    svg_html += `<line x1="${noduri_pos[n1][0]}" y1="${noduri_pos[n1][1]}" x2="${noduri_pos[n2][0]}" y2="${noduri_pos[n2][1]}" class="path"/>`;
                }
                
                // 3. Desenam Ponderile
                muchii.forEach(m => {
                    let n1 = m[0], n2 = m[1];
                    let mx = (noduri_pos[n1][0] + noduri_pos[n2][0]) / 2;
                    let my = (noduri_pos[n1][1] + noduri_pos[n2][1]) / 2;
                    
                    let p1 = data.ponderi_active[n1] ? data.ponderi_active[n1][n2] : 1;
                    let p2 = data.ponderi_active[n2] ? data.ponderi_active[n2][n1] : 1;
                    
                    let este_blocat = (p1 >= 9999 || p2 >= 9999);
                    let text_pondere = este_blocat ? "⛔" : (p1 === p2 ? p1 : `${p1} | ${p2}`);
                    
                    let clasa_bg = este_blocat ? "weight-bg blocked" : "weight-bg";
                    let clasa_txt = este_blocat ? "weight-text blocked" : "weight-text";
                    let rx = text_pondere.toString().length > 3 ? 24 : 16; 
                    
                    svg_html += `<rect x="${mx - rx}" y="${my - 12}" width="${rx*2}" height="24" rx="12" class="${clasa_bg}"/>`;
                    svg_html += `<text x="${mx}" y="${my}" class="${clasa_txt}">${text_pondere}</text>`;
                });

                // 4. Desenam Camerele
                for(let n in noduri_pos) {
                    let stare = data.camere[n];
                    let clasa_nod = '';
                    if (stare.foc) clasa_nod = 'fire';
                    else if (iesiri.includes(n)) clasa_nod = 'exit';
                    
                    let x = noduri_pos[n][0];
                    let y = noduri_pos[n][1];
                    
                    svg_html += `<circle cx="${x}" cy="${y}" r="35" class="node ${clasa_nod}"/>`;
                    svg_html += `<text x="${x}" y="${y - 4}" class="node-text">${n}</text>`;
                    
                    let txt_alert = stare.foc ? 'alert' : '';
                    svg_html += `<text x="${x}" y="${y + 18}" class="sensor-text ${txt_alert}">G:${stare.gaz_adc}</text>`;
                    svg_html += `<text x="${x}" y="${y + 28}" class="sensor-text ${txt_alert}">T:${stare.temp}°</text>`;
                }

                document.getElementById('harta').innerHTML = svg_html;

                // 5. Status text
                let status_box = document.getElementById('status_box');
                if (data.camere[camera_selectata] && data.camere[camera_selectata].foc) {
                    status_box.innerHTML = `🔴 PERICOL IN CAMERA ${camera_selectata}! EVACUATI IMEDIAT!`;
                    status_box.style.color = '#e74c3c';
                } else if (ruta.length > 0) {
                    status_box.innerHTML = `🟢 Ruta optima gasita: ${ruta.join(' -> ')}`;
                    status_box.style.color = '#2ecc71';
                } else {
                    status_box.innerHTML = `❌ BLOCAJ TOTAL! Nu exista scapare din camera ${camera_selectata}.`;
                    status_box.style.color = '#f39c12';
                }
            } catch (error) {
                console.log("Eroare comunicare cu serverul.", error);
            }
        }

        setInterval(updateDashboard, 500);
        document.getElementById('camera_select').addEventListener('change', updateDashboard);
        updateDashboard();
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    print("========================================")
    print(f"SERVER SMART EXIT - {nume_cladire}")
    print(f"Harta incarcata din: {CALE_HARTA}")
    print("Accesati: http://localhost:5000")
    print("========================================")
    app.run(host='0.0.0.0', port=5000, debug=False)
