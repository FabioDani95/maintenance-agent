# Maintenance Agent

Repository per un sistema di troubleshooting knowledge-grounded dedicato alla **Bambu Lab P1P**. Il progetto contiene due applicazioni Flask:

- `troubleshooting_agent/`: assistant web che interpreta il problema, recupera i path dal knowledge graph e mostra evidenze, manuali e telemetria collegata
- `modify/`: editor grafico per visualizzare e modificare `ontology.json`

Il comportamento dell'assistant e dell'editor dipende da:

- `ontology.json`: istanza corrente del knowledge graph
- `ontology_schema.JSON`: schema e vincoli del modello
- `sources.csv`: sorgenti documentali usate per costruire il grafo

## Struttura

```text
maintenance-agent/
├── ontology.json
├── ontology_schema.JSON
├── sources.csv
├── oldVersion/
├── troubleshooting_agent/
├── modify/
├── scripts/
├── requirements.txt
└── README.md
```

## Troubleshooting Agent

L'app in `troubleshooting_agent/` espone una UI web con chat, knowledge graph e overlay PDF. Il flusso reale implementato è:

1. classificazione del messaggio come `relevant`, `unclear` o `not_relevant`
2. embedding della query utente
3. retrieval dei sintomi con similarità coseno su embedding precomputati
4. traversal del grafo `Symptom -> FailureMode -> CorrectiveAction`
5. eventuale chiarimento se emergono più failure mode
6. generazione della risposta grounded via LLM
7. highlight del path sul grafo
8. recupero opzionale di segnali telemetry collegati al failure mode

### File principali

| File | Responsabilità |
|---|---|
| `troubleshooting_agent/agent.py` | Entry point, cleanup porta, validazioni iniziali, apertura browser |
| `troubleshooting_agent/app_factory.py` | Composizione dell'app Flask |
| `troubleshooting_agent/frontend_routes.py` | Route `/` della UI |
| `troubleshooting_agent/api_routes.py` | Endpoint JSON, status, graph data, manuali e telemetria raw |
| `troubleshooting_agent/orchestrator.py` | Logica conversazionale multi-turno e trace del grafo |
| `troubleshooting_agent/ontology_loader.py` | Caricamento ontologia e metadata prodotto |
| `troubleshooting_agent/graph_traversal.py` | Traversal e deduplica dei path di troubleshooting |
| `troubleshooting_agent/embeddings.py` | Generazione/caricamento embedding |
| `troubleshooting_agent/similarity.py` | Similarità coseno e ranking sintomi |
| `troubleshooting_agent/domain_check.py` | Classificazione di dominio via LLM |
| `troubleshooting_agent/response_builder.py` | Formattazione risposte grounded e clarification |
| `troubleshooting_agent/telemetry_loader.py` | Caricamento CSV telemetry e statistiche |
| `troubleshooting_agent/templates/index.html` | Shell HTML della UI |
| `troubleshooting_agent/static/css/app.css` | Stili frontend |
| `troubleshooting_agent/static/js/app.js` | Logica client-side |

### Configurazione attuale

I parametri principali sono in `troubleshooting_agent/config.py`:

- `OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"`
- `OPENAI_CHAT_MODEL = "gpt-5-mini"`
- `SIMILARITY_THRESHOLD = 0.45`
- `HIGH_CONFIDENCE_THRESHOLD = 0.75`
- `TOP_K_SYMPTOMS = 3`
- `AGENT_PORT = 5001` di default
- telemetria letta da `troubleshooting_agent/telemetry/telemetry_p1p.csv`

### Avvio

Prerequisiti:

- `OPENAI_API_KEY` disponibile nell'ambiente o in `.env`
- dipendenze installate da `requirements.txt`
- `troubleshooting_agent/symptom_embeddings.json` coerente con i nodi `Symptom`

Avvio:

```bash
python3 troubleshooting_agent/agent.py
```

Porta custom:

```bash
AGENT_PORT=5002 python3 troubleshooting_agent/agent.py
```

Il server gira su `http://127.0.0.1:5001/` salvo override.

### Endpoint principali

| Endpoint | Metodo | Descrizione |
|---|---|---|
| `/` | GET | UI chat + knowledge graph |
| `/chat` | POST | Elabora un messaggio utente e restituisce risposta, trace e telemetria opzionale |
| `/next_issue` | POST | Mostra il prossimo failure mode nella stessa sessione |
| `/reset` | POST | Reset dello stato conversazionale |
| `/graph_data` | GET | Grafo completo in formato vis-network |
| `/product_info` | GET | Metadata prodotto e sintomi suggeriti |
| `/status` | GET | Versione ontologia e conteggi principali |
| `/manuals/<filename>` | GET | Serve i PDF locali dei manuali |
| `/telemetry/<signal_name>` | GET | Serie temporale grezza e statistiche per un segnale |

### Convenzioni dati importanti

Per funzionare correttamente, `ontology.json` deve rispettare queste assunzioni:

- top-level con `metadata`, `nodes`, `relationships`
- tipi di nodo usati dal flow: `Printer`, `Component`, `Symptom`, `FailureMode`, `CorrectiveAction`
- relazioni operative: `MAY_INDICATE`, `RESOLVED_BY`, `AFFECTS`
- ID attesi nei nodi: `printer_id`, `component_id`, `symptom_id`, `failure_mode_id`, `action_id`
- nei nodi `CorrectiveAction` servono almeno `name`, `instruction_text`, `source_title`, `source_reference`

Per i manuali PDF locali:

- se `source_reference` e' numerico, il frontend tratta la sorgente come pagina di manuale
- `source_title` deve corrispondere al nome file PDF in `troubleshooting_agent/manuals/` senza estensione
- esempio: `source_title = "Bambu Lab P1 series manual"` apre `troubleshooting_agent/manuals/Bambu Lab P1 series manual.pdf`

Per la telemetria:

- un `FailureMode` puo' includere `related_measurements`
- questi nomi devono corrispondere alle colonne del CSV telemetry
- se il CSV manca o le colonne non esistono, l'app degrada senza interrompersi

### Embeddings

Se modifichi i nodi `Symptom`, rigenera gli embeddings:

```bash
python3 troubleshooting_agent/embeddings.py
```

`agent.py` blocca l'avvio se mancano embedding per sintomi presenti nell'ontologia.

### Re-istanziare `ontology.json` per un altro prodotto

Se vuoi riusare il progetto con un prodotto diverso, per esempio una injection molding machine, non basta sostituire i contenuti di `ontology.json`. Per mantenere il comportamento attuale devi verificare tutta la catena dati-codice.

Procedura consigliata:

1. Mantieni la stessa struttura top-level del file:
   `metadata`, `nodes`, `relationships`.
2. Mantieni i tipi e gli ID che il codice usa direttamente:
   `Printer`, `Component`, `Symptom`, `FailureMode`, `CorrectiveAction` e i campi `printer_id`, `component_id`, `symptom_id`, `failure_mode_id`, `action_id`.
3. Mantieni le relazioni operative minime:
   `MAY_INDICATE`, `RESOLVED_BY`, `AFFECTS`.
4. Aggiorna i metadata di prodotto in `ontology.json`:
   `product_name`, `product_short_name`, `product_type`, `domain_topics`, `version`, `total_nodes`, `total_relationships`.
   `domain_topics` e' importante perche' il classifier di dominio usa proprio questi valori nel prompt.
5. Aggiorna i nodi `Symptom`, `FailureMode` e `CorrectiveAction` con contenuti coerenti col nuovo prodotto.
6. Se cambi o aggiungi sintomi, rigenera `troubleshooting_agent/symptom_embeddings.json`:

```bash
python3 troubleshooting_agent/embeddings.py
```

7. Verifica che ogni `CorrectiveAction` abbia `instruction_text`, `source_title` e `source_reference`.
8. Se usi manuali PDF locali, metti i file corretti in `troubleshooting_agent/manuals/` e fai combaciare `source_title` con il nome file senza estensione.
9. Se vuoi la sezione telemetria, aggiorna `related_measurements` nei `FailureMode` e assicurati che esistano colonne omonime in `troubleshooting_agent/telemetry/telemetry_p1p.csv`.
10. Se il nuovo dominio richiede tipi di nodo o relazioni nuove, aggiorna anche `ontology_schema.JSON` e verifica che l'editor in `modify/` continui a riflettere lo schema corretto.
11. Avvia l'agent e controlla i fallimenti iniziali:
    `python3 troubleshooting_agent/agent.py`
    se gli embeddings non sono allineati, `agent.py` si ferma subito.

In pratica, per cambiare prodotto senza rompere l'app, devi aggiornare almeno:

- `ontology.json`
- `troubleshooting_agent/symptom_embeddings.json`
- eventuali PDF in `troubleshooting_agent/manuals/`
- eventuale CSV in `troubleshooting_agent/telemetry/`
- `ontology_schema.JSON` se il modello concettuale cambia

Se invece il nuovo prodotto non puo' essere rappresentato con la catena `Symptom -> FailureMode -> CorrectiveAction`, allora servono modifiche anche al codice, soprattutto in `ontology_loader.py`, `graph_traversal.py`, `api_routes.py` e nella UI del grafo.

## Ontologia

Lo stato attuale di `ontology.json` e' coerente con i metadata presenti nel file:

- versione: `2.0-aligned`
- extraction date: `2026-03-10`
- lingua: `en`
- prodotto: `Bambu Lab P1P`
- nodi totali: `73`
- relazioni totali: `98`

Tipi di nodo presenti:

- `Printer`: 1
- `Component`: 16
- `Symptom`: 16
- `FailureMode`: 18
- `CorrectiveAction`: 22

Lo schema in `ontology_schema.JSON` definisce anche `ErrorCode`, `GENERATES_ERROR` e `INDICATES`, ma al momento non risultano popolati nell'istanza corrente.

## Ontology Editor

L'app in `modify/` permette di visualizzare e modificare il knowledge graph senza database né build step.

Avvio:

```bash
python3 modify/modify_ontology.py
```

Porta custom:

```bash
ONTOLOGY_GRAPH_PORT=8080 python3 modify/modify_ontology.py
```

### Moduli principali

| File | Responsabilità |
|---|---|
| `modify/modify_ontology.py` | Entry point dell'editor |
| `modify/config.py` | Path e configurazione base |
| `modify/state.py` | Stato in memoria, dirty flag, save/load |
| `modify/schema.py` | Caricamento schema e merge con i tipi live |
| `modify/graph.py` | Costruzione del payload grafo |
| `modify/routes.py` | Route Flask e API dell'editor |
| `modify/template.py` | HTML/CSS/JS embedded dell'editor |

## Note operative
- `troubleshooting_agent/spec.txt` descrive la specifica funzionale aggiornata dell'assistant.
