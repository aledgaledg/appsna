import os
import time
import tempfile
import re
import csv

from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename

import fitz  # PyMuPDF
from dotenv import load_dotenv
from mistralai import Mistral

# Carica la chiave API da .env
load_dotenv()
api_key = os.getenv("MISTRAL_API_KEY")
if not api_key:
    raise ValueError("MISTRAL_API_KEY non trovata. Impostala in .env")

# Inizializza il client Mistral
client = Mistral(api_key=api_key)

# Configura Flask
app = Flask(
    __name__,
    template_folder="frontend",     # contiene index.html
    static_folder="frontend",       # contiene css/, js/
    static_url_path="/static"       # serve risorse su /static/...
)
CORS(app)

# Cartelle per upload e CSV
UPLOAD_FOLDER = "uploads_temp_pdf"
CSV_FOLDER    = "generated_csvs_download"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CSV_FOLDER,   exist_ok=True)

# Domanda standard per Mistral
DOMANDA_STANDARD = """
Analizza il testo fornito ed estrai tutte le informazioni latenti riguardanti:
- individui (persone fisiche), anche se menzionati solo indirettamente o in ruoli secondari,
- aziende/organizzazioni (società, enti, clan, associazioni, ecc.),
- luoghi (città, quartieri, regioni, paesi, sedi, ecc.).

Per ciascun soggetto (persona, azienda/organizzazione o luogo), restituisci un oggetto JSON con i seguenti campi (usa array vuoti o stringa vuota se non ci sono dati):

[
  {
    "tipo": "persona|azienda|luogo",
    "nome": "",
    "ruolo": "",
    "organizzazione": "",
    "relazioni": [
      {"tipo": "", "con_chi": "", "contesto_relazione": ""}
    ],
    "localita_principali": [""],
    "attivita_criminali_note": [""],
    "scambi_economici_sospetti": [""],
    "accuse_formali": [""],
    "coinvolgimento_omicidi": [""],
    "altro_rilevante": ""
  }
]

Rispondi SOLO con un array JSON valido, senza testo aggiuntivo o commenti.

Testo da analizzare:
"""

import json

def parse_mistral_output(text):
    """Analizza l'output JSON di Mistral per estrarre persone e relazioni."""
    print("\nDEBUG: Inizio parse_mistral_output (versione JSON).")
    print(f"DEBUG: Testo ricevuto da Mistral per il parsing (lunghezza: {len(text)} caratteri):")
    print("------------------------- INIZIO TESTO MISTRAL -------------------------")
    print(text)
    print("-------------------------- FINE TESTO MISTRAL --------------------------")

    persone = []
    relazioni = []

    # Prova a isolare solo la parte JSON
    try:
        start = text.index('[')
        end = text.rindex(']') + 1
        json_text = text[start:end]
    except Exception as e:
        print("DEBUG: Impossibile isolare la parte JSON:", e)
        json_text = text

    try:
        data = json.loads(json_text)
        for persona in data:
            nome = persona.get("nome", "").strip()
            if not nome:
                continue
            persone.append({
                "nome": nome,
                "stato": persona.get("ruolo", "").strip(),
                "fonte": ""
            })
            for rel in persona.get("relazioni", []):
                con_chi = rel.get("con_chi", "").strip()
                tipo = rel.get("tipo", "").strip()
                contesto = rel.get("contesto_relazione", "").strip()
                if con_chi and tipo:
                    relazioni.append({
                        "persona_a": nome,
                        "persona_b": con_chi,
                        "tipo": tipo,
                        "peso": "",  # Puoi aggiungere logica per il peso se serve
                        "contesto": contesto,
                        "fonte": ""
                    })
    except Exception as e:
        print(f"Errore CRITICO durante il parsing JSON dell'output di Mistral: {e}")
        import traceback
        traceback.print_exc()

    if not persone and not relazioni:
        print("DEBUG: parse_mistral_output (versione JSON) non ha estratto né persone né relazioni.")
    else:
        print(f"DEBUG: parse_mistral_output (versione JSON) ha estratto {len(persone)} persone e {len(relazioni)} relazioni.")

    return persone, relazioni

def estrai_testo_con_fitz(path):
    """Estrae tutto il testo da un PDF con PyMuPDF."""
    try:
        doc = fitz.open(path)
        testo_completo = ""
        print(f"DEBUG: Apertura PDF {path} con PyMuPDF. Numero pagine: {len(doc)}")
        for page_num, page in enumerate(doc):
            testo_pagina = page.get_text("text")
            if testo_pagina:
                # print(f"DEBUG: Testo estratto da pagina {page_num + 1} (primi 100 char): {testo_pagina[:100]}") # Opzionale per debug dettagliato pagina per pagina
                testo_completo += testo_pagina + "\n"
            else:
                print(f"DEBUG: Nessun testo estratto da pagina {page_num + 1} di {path}")
        doc.close()

        if testo_completo.strip():
            print(f"DEBUG: Testo estratto con successo da {path}.")
            print(f"DEBUG: Lunghezza totale del testo estratto: {len(testo_completo)} caratteri.")
            print(f"DEBUG: Anteprima testo estratto (primi 500 caratteri):\n{testo_completo[:500]}")
            return testo_completo
        else:
            print(f"DEBUG: Nessun testo significativo estratto da {path} (testo risultante vuoto o solo spazi).")
            return None
    except Exception as e:
        print(f"Errore CRITICO durante l'estrazione del testo da {path} con PyMuPDF: {e}")
        return None

def fai_domanda_sul_pdf(path, domanda):
    """Estrae il testo e lo invia a Mistral, ritorna la risposta testuale."""
    print(f"DEBUG: Inizio elaborazione per fai_domanda_sul_pdf con path: {path}")
    txt = estrai_testo_con_fitz(path)
    if not txt:
        print(f"DEBUG: estrai_testo_con_fitz non ha restituito testo per {path}. Impossibile procedere con la chiamata a Mistral.")
        return None
    
    print(f"DEBUG: Testo estratto (lunghezza {len(txt)}) pronto per essere inviato a Mistral per il file {path}.")
    # print(f"DEBUG: Testo che sarà inviato a Mistral (primi 200 char per {path}):\n{txt[:200]}") # Decommenta se necessario, può essere molto verboso

    try:
        print(f"DEBUG: Invio richiesta a Mistral per il file {path}...")
        resp = client.chat.complete(
            model="mistral-large-latest", 
            messages=[{"role":"user","content": f"{domanda}\n\n{txt}"}]
        )
        risposta_contenuto = resp.choices[0].message.content
        print(f"DEBUG: Risposta ricevuta da Mistral per {path} (lunghezza: {len(risposta_contenuto)}).")
        # print(f"DEBUG: Anteprima risposta Mistral (primi 200 char per {path}):\n{risposta_contenuto[:200]}") # Decommenta se necessario
        return risposta_contenuto
    except Exception as e:
        print(f"Errore CRITICO durante la chiamata API a Mistral per il file {path}: {e}")
        return None

# Funzione per scrivere il report CSV completo
def scrivi_csv(persone, relazioni, filename):
    """Scrive un CSV con persone isolate e relazioni."""
    fieldnames = [
        "ID","Tipo","Nome_A","Stato_A","FontePDF_A",
        "TipoRel","Peso","Contesto","FontePDF_Rel",
        "Nome_B","Stato_B","FontePDF_B"
    ]
    rows = []
    idx = 1
    pm = {p["nome"]: p for p in persone}

    # Relazioni
    for r in relazioni:
        pa = pm.get(r["persona_a"], {})
        pb = pm.get(r["persona_b"], {})
        rows.append({
            "ID": idx,
            "Tipo": "Relazione",
            "Nome_A": r["persona_a"],
            "Stato_A": pa.get("stato",""),
            "FontePDF_A": pa.get("fonte",""),
            "TipoRel": r["tipo"],
            "Peso": r["peso"],
            "Contesto": r["contesto"],
            "FontePDF_Rel": r.get("fonte",""),
            "Nome_B": r["persona_b"],
            "Stato_B": pb.get("stato",""),
            "FontePDF_B": pb.get("fonte","")
        })
        idx += 1

    # Persone isolate
    coinvolte = {r["persona_a"] for r in relazioni} | {r["persona_b"] for r in relazioni}
    for p in persone:
        if p["nome"] not in coinvolte:
            rows.append({
                "ID": idx,
                "Tipo": "Persona Isolata",
                "Nome_A": p["nome"],
                "Stato_A": p.get("stato",""),
                "FontePDF_A": p.get("fonte",""),
                "TipoRel": "","Peso":"","Contesto":"","FontePDF_Rel":"",
                "Nome_B":"","Stato_B":"","FontePDF_B":""
            })
            idx += 1

    # Scrittura CSV
    path = os.path.join(CSV_FOLDER, filename)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return filename

@app.route("/")
def index():
    return render_template("index.html")

@app.route('/process_files', methods=['POST'])
def process_files_route():
    app.logger.info("!!!!!!!! DEBUG: /process_files route RAGGIUNTA !!!!!!!!!!") # Nuovo logger
    print("!!!!!!!! DEBUG: /process_files route RAGGIUNTA (con print) !!!!!!!!!!") # Nuovo print

    start_time_route = time.time()
    temp_file_paths = [] 
    files_processed_names = []
    processed_files_count = 0
    warnings_list = []
    all_persone_aggregated = []
    all_relazioni_aggregated = []
    errors_occurred = []

    app.logger.info(f"DEBUG: Oggetto request.files: {request.files}") # Nuovo logger
    print(f"DEBUG: Oggetto request.files (con print): {request.files}") # Nuovo print

    if 'pdf_files' not in request.files:
        app.logger.warning("DEBUG: 'pdf_files' NON TROVATO nella richiesta.") # Nuovo logger
        print("DEBUG: 'pdf_files' NON TROVATO nella richiesta (con print).") # Nuovo print
        return jsonify({"error": "Nessun campo 'pdf_files' nella richiesta"}), 400

    files = request.files.getlist('pdf_files') 
    app.logger.info(f"DEBUG: Lista dei file ottenuti da getlist: {files}") # Nuovo logger
    print(f"DEBUG: Lista dei file ottenuti da getlist (con print): {files}") # Nuovo print
    
    if not files or all(f.filename == '' for f in files):
        app.logger.warning("DEBUG: Nessun file selezionato per il caricamento.") # Nuovo logger
        print("DEBUG: Nessun file selezionato per il caricamento (con print).") # Nuovo print
        return jsonify({"error": "Nessun file selezionato per il caricamento"}), 400

    # Rimuovi la riga duplicata di inizializzazione se presente
    files = request.files.getlist("pdf_files")
    if not files:
        return jsonify(error="Nessun file inviato"), 400

    agg_p, agg_r = [], []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            continue
        orig = secure_filename(f.filename)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=UPLOAD_FOLDER)
        f.save(tmp.name)
        tmp.close()

        # Chiamata Mistral
        risposta = fai_domanda_sul_pdf(tmp.name, DOMANDA_STANDARD)
        os.remove(tmp.name)
        if not risposta:
            continue

        # Parsing
        persone, relazioni = parse_mistral_output(risposta)
        for p in persone:   p["fonte"] = orig
        for r in relazioni: r["fonte"] = orig

        agg_p += persone
        agg_r += relazioni

    # Debug: stato dopo parsing e aggregazione
    print("DEBUG: Persone aggregate dopo parsing:", agg_p)
    print("DEBUG: Relazioni aggregate dopo parsing:", agg_r)

    def normalizza_nome(nome):
        # Rimuove spazi, mette tutto minuscolo e ordina le parole
        parole = nome.lower().split()
        parole.sort()
        return ''.join(parole)
    
    # Dedup avanzato
    persone_deduplicate_dict = {}
    for p_corrente in agg_p:
        nome_chiave = normalizza_nome(p_corrente["nome"])
        if nome_chiave not in persone_deduplicate_dict:
            persone_deduplicate_dict[nome_chiave] = p_corrente
    
    persone = list(persone_deduplicate_dict.values())
    
    relazioni = list({(
        normalizza_nome(r["persona_a"]),
        normalizza_nome(r["persona_b"]),
        r["tipo"].lower(),
        r["peso"],
        r["contesto"].lower()
    ): r for r in agg_r}.values())

    # Debug: stato dopo deduplicazione
    print("DEBUG: Persone finali dopo dedup:", persone)
    print("DEBUG: Relazioni finali dopo dedup:", relazioni)

    if not persone and not relazioni:
        return jsonify(warnings=["Nessuna persona o relazione estratta"]), 200

    report_name = f"report_{int(time.time())}.csv"
    scrivi_csv(persone, relazioni, report_name)

    return jsonify(
        message=f"{len(files)} file processati",
        report_filename=report_name,
        num_relazioni_estratte=len(relazioni)
    ), 200

@app.route("/download_csv/<filename>")
def download_csv(filename):
    safe = secure_filename(filename)
    return send_from_directory(CSV_FOLDER, safe, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)


def scrivi_csv_supercompleto(persone, relazioni, path_csv):
    """Scrive un CSV supercompleto con tutte le informazioni estratte."""
    fieldnames = [
        "nome", "ruolo", "organizzazione", "localita_principali", "attivita_criminali_note",
        "scambi_economici_sospetti", "accuse_formali", "coinvolgimento_omicidi", "altro_rilevante",
        "relazioni"
    ]
    with open(path_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for persona in persone:
            # Trova tutte le relazioni dove questa persona è coinvolta
            rels = [rel for rel in relazioni if rel["persona_a"].lower() == persona["nome"].lower()]
            rels_str = "; ".join([
                f'{rel["tipo"]} con {rel["persona_b"]} ({rel["contesto"]})'
                for rel in rels
            ]) if rels else ""
            writer.writerow({
                "nome": persona.get("nome", ""),
                "ruolo": persona.get("stato", ""),
                "organizzazione": persona.get("organizzazione", ""),
                "localita_principali": ", ".join(persona.get("localita_principali", [])) if isinstance(persona.get("localita_principali", []), list) else persona.get("localita_principali", ""),
                "attivita_criminali_note": ", ".join(persona.get("attivita_criminali_note", [])) if isinstance(persona.get("attivita_criminali_note", []), list) else persona.get("attivita_criminali_note", ""),
                "scambi_economici_sospetti": ", ".join(persona.get("scambi_economici_sospetti", [])) if isinstance(persona.get("scambi_economici_sospetti", []), list) else persona.get("scambi_economici_sospetti", ""),
                "accuse_formali": ", ".join(persona.get("accuse_formali", [])) if isinstance(persona.get("accuse_formali", []), list) else persona.get("accuse_formali", ""),
                "coinvolgimento_omicidi": ", ".join(persona.get("coinvolgimento_omicidi", [])) if isinstance(persona.get("coinvolgimento_omicidi", []), list) else persona.get("coinvolgimento_omicidi", ""),
                "altro_rilevante": persona.get("altro_rilevante", ""),
                "relazioni": rels_str
            })
    writer.writerows(rows)
    return filename

@app.route("/download_csv/<filename>")
def download_csv(filename):
    safe = secure_filename(filename)
    return send_from_directory(CSV_FOLDER, safe, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)
