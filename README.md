# Bambu Lab P1P Troubleshooting Agent + Ontology Editor

Repository per un sistema di troubleshooting knowledge-grounded della stampante **Bambu Lab P1P**. Il progetto include:

- un **knowledge graph** in `ontology.json`
- uno **schema ontologico** in `ontology_schema.JSON`
- un **troubleshooting agent** web in `troubleshooting_agent/`
- un **editor grafico dell'ontologia** in `modify/`

L'agente non genera soluzioni tecniche liberamente: interpreta il problema espresso in linguaggio naturale, lo collega ai sintomi noti tramite embedding, attraversa il grafo `Symptom -> FailureMode -> CorrectiveAction` e formula la risposta usando solo i dati presenti nell'ontologia.

---

## Struttura del progetto

```text
APMS_2026/
├── ontology.json
├── ontology_schema.JSON
├── sources.csv
├── oldVersion/
│   └── ontology_v*.json
├── troubleshooting_agent/
│   ├── agent.py
│   ├── app_factory.py
│   ├── api_routes.py
│   ├── frontend_routes.py
│   ├── orchestrator.py
│   ├── routes.py
│   ├── ontology_loader.py
│   ├── graph_traversal.py
│   ├── embeddings.py
│   ├── similarity.py
│   ├── domain_check.py
│   ├── response_builder.py
│   ├── config.py
│   ├── symptom_embeddings.json
│   ├── manuals/
│   ├── templates/
│   │   └── index.html
│   ├── static/
│   │   ├── css/
│   │   │   └── app.css
│   │   └── js/
│   │       └── app.js
│   └── spec.txt
├── modify/
│   ├── modify_ontology.py
│   ├── config.py
│   ├── state.py
│   ├── schema.py
│   ├── graph.py
│   ├── routes.py
│   └── template.py
├── requirements.txt
└── README.md
```

---

## Troubleshooting Agent

L'applicazione in `troubleshooting_agent/` espone una UI web con chat e visualizzazione del grafo. Il flusso implementato nel codice e nella specifica è:

1. controllo di rilevanza del messaggio rispetto al dominio P1P
2. embedding della query utente
3. matching dei sintomi via similarità coseno su embedding precomputati
4. attraversamento del knowledge graph per recuperare failure mode, componenti e corrective actions
5. generazione della risposta finale vincolata ai dati dell'ontologia
6. evidenziazione nel grafo del percorso attivo

### Architettura applicativa

Il troubleshooting agent è ancora una singola applicazione Flask, ma ora è separato per responsabilità:

- `agent.py`: entry point operativo
- `app_factory.py`: crea e compone l'app Flask
- `frontend_routes.py`: route della UI
- `api_routes.py`: endpoint JSON e serving dei manuali
- `routes.py`: wrapper di compatibilità che continua a esportare `app`
- `templates/index.html`: shell HTML della UI
- `static/css/app.css`: stili del frontend
- `static/js/app.js`: logica client-side per chat, grafo, highlight e PDF overlay

Questa separazione non cambia l'utilizzo esterno dell'applicazione, ma rende più semplice integrarla in una piattaforma più ampia o sostituire in futuro il frontend senza toccare la logica API.

### File principali

| File | Responsabilità |
|---|---|
| `troubleshooting_agent/agent.py` | Entry point del server Flask, controllo porta, verifica ontologia/embeddings, apertura browser |
| `troubleshooting_agent/app_factory.py` | Crea l'app Flask e registra i blueprint |
| `troubleshooting_agent/frontend_routes.py` | Espone la route `/` e renderizza `templates/index.html` |
| `troubleshooting_agent/api_routes.py` | Espone gli endpoint backend JSON e il serving dei manuali |
| `troubleshooting_agent/routes.py` | Punto di compatibilità per import esistenti (`app = create_app()`) |
| `troubleshooting_agent/orchestrator.py` | Gestione conversazione multi-turno, ambiguità, trace del grafo |
| `troubleshooting_agent/ontology_loader.py` | Load di `ontology.json` e costruzione indici in memoria |
| `troubleshooting_agent/graph_traversal.py` | Traversal `Symptom -> FailureMode -> CorrectiveAction` |
| `troubleshooting_agent/embeddings.py` | Generazione/caricamento embedding dei sintomi |
| `troubleshooting_agent/similarity.py` | Cosine similarity e soglie di matching |
| `troubleshooting_agent/domain_check.py` | Classificazione `relevant / unclear / not_relevant` tramite LLM |
| `troubleshooting_agent/response_builder.py` | Formattazione risposta grounded e domande di chiarimento |
| `troubleshooting_agent/templates/index.html` | Template HTML della UI |
| `troubleshooting_agent/static/css/app.css` | CSS del frontend |
| `troubleshooting_agent/static/js/app.js` | JavaScript del frontend |

### Avvio del troubleshooting agent

Prerequisiti:

- `OPENAI_API_KEY` definita in `.env` o nell'ambiente
- dipendenze installate da `requirements.txt`
- `troubleshooting_agent/symptom_embeddings.json` presente

Avvio:

```bash
python3 troubleshooting_agent/agent.py
```

Porta custom:

```bash
AGENT_PORT=5002 python3 troubleshooting_agent/agent.py
```

Di default il server gira su `http://127.0.0.1:5001/`.

### Endpoint principali dell'agent

| Endpoint | Metodo | Descrizione |
|---|---|---|
| `/` | GET | UI chat + knowledge graph |
| `/chat` | POST | Elabora un messaggio utente |
| `/next_issue` | POST | Mostra il failure mode successivo nella stessa sessione |
| `/reset` | POST | Reset dello stato conversazionale |
| `/graph_data` | GET | Grafo completo in formato vis-network |
| `/product_info` | GET | Metadata prodotto + sintomi suggeriti |
| `/manuals/<filename>` | GET | Serve i PDF locali dei manuali |
| `/status` | GET | Versione ontologia e conteggi principali |

### Frontend e backend separati

Dal punto di vista del codice:

- il frontend vive in `templates/` e `static/`
- il backend HTTP vive in `api_routes.py`
- la composizione dell'app vive in `app_factory.py`

Dal punto di vista dell'utente o di un'integrazione esistente, invece, non cambia nulla:

- stessa entrypoint `python3 troubleshooting_agent/agent.py`
- stessi path pubblici
- stessa UI
- stessa logica conversazionale

Questo rende il progetto più adatto a:

- mounting dietro reverse proxy
- sostituzione futura della UI con un frontend esterno
- test separati di backend e frontend
- riuso delle API da altri servizi interni

### Come usare correttamente l'agent

Per far funzionare davvero il troubleshooting agent non basta avviare `agent.py`: i dati in `ontology.json` devono rispettare alcune convenzioni precise, perché il backend e il frontend si aspettano campi e naming coerenti.

#### 1. L'ontologia deve avere la struttura prevista

Il file `ontology.json` deve contenere almeno queste sezioni top-level:

- `metadata`
- `nodes`
- `relationships`

Nei `nodes` il codice si aspetta almeno questi tipi:

- `Printer`
- `Component`
- `Symptom`
- `FailureMode`
- `CorrectiveAction`

Le relazioni minime realmente usate dal troubleshooting flow sono:

- `MAY_INDICATE`: da `Symptom` a `FailureMode`
- `RESOLVED_BY`: da `FailureMode` a `CorrectiveAction`
- `AFFECTS`: da `FailureMode` a `Component`

Le relazioni `HAS_COMPONENT` e `RELATED_TO` non sono indispensabili per generare la risposta finale, ma sono utili per la completezza del grafo e della visualizzazione.

#### 2. Ogni nodo deve avere il suo ID atteso

L'app identifica i nodi leggendo chiavi specifiche:

- `printer_id`
- `component_id`
- `symptom_id`
- `failure_mode_id`
- `action_id`

Se questi campi mancano, il nodo non viene indicizzato correttamente e può sparire dal traversal o dalla visualizzazione del grafo.

#### 3. Le `CorrectiveAction` devono avere tutti i campi operativi

Ogni `CorrectiveAction` deve avere almeno:

- `action_id`
- `name`
- `description`
- `instruction_text`
- `source_title`
- `source_reference`

In pratica:

- `instruction_text` è il testo operativo che il chatbot riformatta e presenta all’utente
- `source_title` è il nome umano della sorgente
- `source_reference` è o una URL oppure un numero pagina di un PDF locale

#### 4. Convenzione fondamentale per i manuali PDF locali

Se una `CorrectiveAction` usa un manuale PDF locale, il frontend lo apre costruendo il path con questa logica:

```text
/manuals/<source_title>.pdf#page=<source_reference>
```

Quindi, per funzionare correttamente:

- `source_title` deve corrispondere **esattamente** al nome file del PDF in `troubleshooting_agent/manuals/`, senza estensione
- il file deve esistere realmente nella cartella `manuals/`
- `source_reference` dovrebbe essere un **numero di pagina puro**, per esempio `4`, `6`, `15`

Esempio valido:

- `source_title`: `Bambu Lab P1 series manual`
- file presente: `troubleshooting_agent/manuals/Bambu Lab P1 series manual.pdf`
- `source_reference`: `4`

Se il nome non coincide, il bottone "Open manual" viene mostrato ma il PDF non si aprirà correttamente.

#### 5. Convenzione per le sorgenti web

Se `source_reference` inizia con `http`, la risposta viene resa come link web cliccabile e non come bottone manuale.

Esempio:

- `source_title`: `Bambu Lab P1 series printer Maintenance Recommendation`
- `source_reference`: `https://wiki.bambulab.com/en/p1/maintenance/p1p-maintenance`

#### 6. Dopo aver cambiato i sintomi, va rigenerato il file embedding

Se modifichi i nodi `Symptom` in `ontology.json` aggiungendo, rinominando o rimuovendo sintomi, devi rigenerare `troubleshooting_agent/symptom_embeddings.json`, altrimenti il matcher semantico lavorerà su dati incoerenti.

Comando:

```bash
python3 troubleshooting_agent/embeddings.py
```

#### 7. I conteggi in `metadata` dovrebbero restare coerenti

Il codice non blocca l'esecuzione se `total_nodes` e `total_relationships` sono inconsistenti, ma questi valori vengono esposti in `/status` e sono usati come riferimento documentale. Conviene aggiornarli quando si modifica l'ontologia.

#### 8. Vincoli pratici da rispettare prima del push

Checklist minima:

- `ontology.json` valido e coerente con `ontology_schema.JSON`
- tutti i `Symptom` rilevanti presenti anche in `symptom_embeddings.json`
- tutti i PDF referenziati da `source_title` realmente presenti in `troubleshooting_agent/manuals/`
- `source_reference` numerico per i PDF locali
- catena `Symptom -> FailureMode -> CorrectiveAction` effettivamente attraversabile

Se uno di questi punti manca, l'app può avviarsi ma degradare in modo silenzioso: niente match, niente manuale cliccabile, o path di troubleshooting incompleti.

---

## Ontologia e schema

### `ontology.json`

`ontology.json` contiene l'istanza corrente del knowledge graph. Nello stato attuale:

- versione metadata: `2.0-aligned`
- lingua: `en`
- nodi totali: `73`
- relazioni totali: `98`
- prodotto: `Bambu Lab P1P`

La struttura dati è organizzata in:

- `metadata`: nome ontologia, versione, lingua, conteggi e metadati di dominio
- `sources`: sorgenti documentali usate per costruire il grafo
- `nodes`: nodi raggruppati per tipo
- `relationships`: archi tipizzati `from_id -> to_id`

Tipi di nodo presenti:

- `Printer`
- `Component`
- `Symptom`
- `FailureMode`
- `CorrectiveAction`

Relazioni usate nell'istanza corrente:

- `HAS_COMPONENT`
- `RELATED_TO`
- `MAY_INDICATE`
- `AFFECTS`
- `RESOLVED_BY`

Nota: lo schema prevede anche `ErrorCode`, `GENERATES_ERROR` e `INDICATES`, ma questi elementi al momento non sono popolati in `ontology.json`.

### `ontology_schema.JSON`

`ontology_schema.JSON` definisce il modello ammesso dell'ontologia:

- nome e versione dello schema (`2.0`)
- principi di progettazione
- tipi di nodo con proprietà richieste/univoche
- relazioni valide con vincoli `domain` e `range`

Lo schema include 6 tipi di nodo:

- `Printer`
- `Component`
- `Symptom`
- `FailureMode`
- `CorrectiveAction`
- `ErrorCode`

E 7 tipi di relazione:

- `HAS_COMPONENT`
- `RELATED_TO`
- `MAY_INDICATE`
- `AFFECTS`
- `RESOLVED_BY`
- `GENERATES_ERROR`
- `INDICATES`

Lo schema è usato sia come documentazione del modello sia dall'editor in `modify/` per mostrare campi attesi e vincoli sulle relazioni.

### Esempio pratico di `CorrectiveAction`

```json
{
  "action_id": "act_clean_z_lead_screws",
  "name": "Clean Z-axis lead screws",
  "description": "Remove dust and residue from the Z-axis lead screws.",
  "instruction_text": "Power off the printer and clean the screw threads with a lint-free cloth...",
  "source_title": "Bambu Lab P1 series manual",
  "source_reference": "4"
}
```

Con questo record:

- il chatbot può descrivere l'azione usando `instruction_text`
- la UI mostrerà un bottone per aprire il manuale
- il frontend proverà ad aprire `troubleshooting_agent/manuals/Bambu Lab P1 series manual.pdf` alla pagina `4`

---

## modify/

Applicazione web (Flask + vis.js) che permette di **visualizzare** e **modificare interattivamente** il knowledge graph. Non richiede database né build step. Il codice è suddiviso in moduli tematici all'interno della cartella `modify/`; l'unico file da lanciare è `modify_ontology.py`.

### Avvio

```bash
python3 modify/modify_ontology.py
```

Il server si avvia su `http://127.0.0.1:5000/` e apre automaticamente il browser. Per usare una porta diversa:

```bash
ONTOLOGY_GRAPH_PORT=8080 python3 modify/modify_ontology.py
```

> **Nota:** su macOS la porta 5000 può essere occupata da AirPlay Receiver. In quel caso usare una porta alternativa come 8080.

---

### Come funziona internamente

#### Moduli backend

| Modulo | Responsabilità |
|---|---|
| `config.py` | Costanti di percorso (`BASE_DIR`, `ONTOLOGY_PATH`, `SCHEMA_PATH`) |
| `state.py` | Stato in-memoria (`_working_ontology`), dirty flag, load/save su disco |
| `schema.py` | Caricamento e cache di `ontology_schema.JSON`, merge con tipi live |
| `graph.py` | Dataclass `Node`/`Edge`, `build_graph()` — logica pura senza I/O |
| `routes.py` | `Flask app` e tutti i route handler |
| `template.py` | Stringa `HTML_TEMPLATE` (CSS + HTML + JS vis.js) |

All'avvio, `ontology.json` viene caricato in memoria come **copia di lavoro mutabile** (`_working_ontology` in `state.py`). Tutte le modifiche vengono applicate a questo dict in memoria — non viene scritto nulla su disco finché non si clicca "Save Version".

Lo schema (`ontology_schema.JSON`) viene caricato in cache da `schema.py` e usato per:
- mostrare nel pannello edit tutti i campi previsti per ogni tipo di nodo (anche quelli ancora vuoti nel JSON)
- popolare i dropdown dei tipi di relazione validi

#### Endpoints API

| Endpoint | Metodo | Descrizione |
|---|---|---|
| `/` | GET | Serve l'interfaccia HTML |
| `/data` | GET | Grafo completo in formato vis.js (nodi + archi) |
| `/schema` | GET | Tipi di nodo e vincoli domain/range delle relazioni |
| `/node/<id>` | GET | Attributi completi + relazioni in/out di un nodo |
| `/node/<id>/update` | POST | Aggiorna attributi di un nodo |
| `/node/<id>/delete` | POST | Elimina un nodo e tutte le sue relazioni |
| `/relationship/add` | POST | Aggiunge una relazione |
| `/relationship/<idx>/delete` | POST | Rimuove una relazione per indice |
| `/save` | POST | Salva versione su disco (bump versione + file versionato) |
| `/status` | GET | Versione corrente e flag modifiche non salvate |
| `/all_nodes` | GET | Lista piatta di tutti i nodi (per autocomplete) |

#### Frontend (vis.js)

Il grafo usa **DataSet persistenti + DataView** di vis.js: le modifiche ai dati (aggiunta/rimozione di nodi e archi) si riflettono immediatamente nel grafo senza ricaricare la pagina. I filtri per tipo di nodo/relazione agiscono tramite `DataView.refresh()`.

---

### Interfaccia utente

#### Modalità View (default, 2 colonne)

```
┌─────────────────────────────────────────────────────┐
│  Header: titolo ontologia           [Edit Mode ○]   │
├──────────────┬──────────────────────────────────────┤
│  Sidebar     │                                      │
│              │         Grafo interattivo            │
│  Node Types  │                                      │
│  (chip)      │   • Drag nodi                        │
│              │   • Scroll per zoom                  │
│  Rel Types   │   • Hover per tooltip                │
│  (chip)      │                                      │
│              │                           113 nodes  │
│  Physics ✓   │                                      │
│  Smooth  ✓   │                                      │
│  [Fit]       │                                      │
└──────────────┴──────────────────────────────────────┘
```

- **Chip Node Types**: click per mostrare/nascondere categorie di nodi
- **Chip Rel Types**: click per mostrare/nascondere tipi di relazione
- **Physics**: abilita/disabilita la simulazione fisica
- **Smooth edges**: abilita/disabilita le curve sugli archi
- **Fit to screen**: adatta il grafo alla finestra

#### Modalità Edit (3 colonne)

Attivabile con il toggle **"Edit Mode"** nell'header (diventa arancione quando attivo).

```
┌──────────────────────────────────────────────────────────────────┐
│  Header: titolo ontologia                    [Edit Mode ●]       │
├──────────────┬───────────────────────────┬──────────────────────┤
│  Sidebar     │                           │  Edit Panel          │
│              │                           │                      │
│  (filtri)    │   Grafo interattivo       │  [Component] ●       │
│              │                           │  comp_nozzle         │
│              │   ← click su un nodo      │                      │
│              │      per editarlo         │  ATTRIBUTES          │
│              │                           │  name: [Nozzle    ]  │
│              │                           │  description: [   ]  │
│              │                           │  category: [      ]  │
│              │                           │  [Apply]             │
│              │                           │                      │
│              │                           │  OUTGOING (0)        │
│              │                           │  None                │
│              │                           │                      │
│              │                           │  INCOMING (4)        │
│              │                           │  Printer ─[HAS]─ ×  │
│              │                           │  Symptom ─[REL]─ ×  │
│              │                           │                      │
│              │                           │  + Add Relationship  │
│              │                           │                      │
│              │                           │  ── DANGER ZONE ──   │
│              │                           │  [Delete this node]  │
├──────────────┴───────────────────────────┴──────────────────────┤
│  ● v1.2                                      [Save Version]     │
└──────────────────────────────────────────────────────────────────┘
```

---

### Funzionalità in Edit Mode

#### Modifica attributi di un nodo

1. Click su un nodo nel grafo → pannello edit si popola
2. Il pannello mostra **tutti i campi** definiti nello schema per quel tipo di nodo (inclusi quelli ancora vuoti nel JSON)
3. Modifica i valori nei campi di testo
4. Click **Apply** → il label del nodo nel grafo si aggiorna immediatamente

#### Gestione relazioni

**Eliminare una relazione:**
- Click sul pulsante `×` accanto a una relazione nel pannello → la relazione sparisce dal grafo istantaneamente

**Aggiungere una relazione:**
- Aprire la sezione **"+ Add Relationship"** nel pannello
- Selezionare la direzione (uscente / entrante)
- Selezionare il tipo di relazione (dropdown con tutti i tipi dello schema)
- Cercare e selezionare il nodo target (campo di ricerca con autocomplete)
- Click **Add** → il nuovo arco appare nel grafo

#### Eliminare un nodo

- In fondo al pannello, sezione **"Danger Zone"**
- Click **Delete this node** → richiede conferma
- Vengono eliminati il nodo e **tutte le sue relazioni** in automatico

#### Navigazione tra nodi collegati

Nel pannello edit, i nodi nelle liste relazioni sono **cliccabili**: click su un nodo collegato per navigare direttamente ad esso (il grafo si centra sul nodo e il pannello si aggiorna).

---

### Salvataggio versioni

Il pallino arancione `●` nella barra in basso indica **modifiche non salvate**.

Click **"Save Version"** per:
1. Incrementare la versione (es. `1.2` → `1.3`)
2. Salvare `oldVersion/ontology_v1.3.json` (archivio storico)
3. Aggiornare `ontology.json` con la nuova versione

**Puoi fare tutte le modifiche che vuoi (edit, delete, add relazioni) e salvare una sola volta alla fine.**

Se si tenta di chiudere il browser con modifiche non salvate, viene mostrato un avviso.

---

### Struttura ontologia (ontology.json)

```json
{
  "metadata": {
    "ontology_name": "...",
    "version": "1.2",
    "total_nodes": 113,
    "total_relationships": 204
  },
  "nodes": {
    "Component": [
      { "component_id": "comp_nozzle", "name": "Nozzle", ... }
    ],
    "Symptom": [ ... ],
    "FailureMode": [ ... ],
    "CorrectiveAction": [ ... ]
  },
  "relationships": [
    { "type": "HAS_COMPONENT", "from_id": "prt_p1p", "to_id": "comp_nozzle" }
  ]
}
```

I tipi di nodo e i vincoli domain/range delle relazioni sono definiti in `ontology_schema.JSON`.
