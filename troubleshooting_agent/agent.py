from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import webbrowser

# Ensure imports resolve within this package directory
sys.path.insert(0, os.path.dirname(__file__))

from config import AGENT_PORT, EMBEDDINGS_PATH
from ontology_loader import get_index
from routes import app


def _free_port(port: int) -> None:
    """Kill any process currently listening on *port*."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True,
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid and pid != str(os.getpid()):
                os.kill(int(pid), signal.SIGTERM)
                print(f"Killed stale process {pid} on port {port}")
    except Exception:
        pass


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_browser(port: int) -> None:
    def _open() -> None:
        webbrowser.open(f"http://127.0.0.1:{port}/", new=2)

    threading.Timer(1.2, _open).start()


def main() -> None:
    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda *_: (print("\nShutting down…"), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (print("\nShutting down…"), sys.exit(0)))

    # Free the port if a stale process is holding it
    if _port_in_use(AGENT_PORT):
        print(f"Port {AGENT_PORT} is in use – freeing it…")
        _free_port(AGENT_PORT)
        import time
        time.sleep(0.5)

    # Fail fast: ontology must load cleanly
    index = get_index()
    print(f"Ontology loaded: {len(index.symptoms)} symptoms, "
          f"{sum(len(v) for v in index.may_indicate.values())} MAY_INDICATE relations")

    if not EMBEDDINGS_PATH.exists():
        print(
            "\n[WARNING] symptom_embeddings.json not found.\n"
            "Run first:  python embeddings.py\n"
        )
        sys.exit(1)

    # Check embedding consistency: every symptom in ontology must have an embedding
    import json as _json
    with EMBEDDINGS_PATH.open("r", encoding="utf-8") as _f:
        _stored_ids = set(_json.load(_f).keys())
    _ontology_ids = {s["symptom_id"] for s in index.symptoms if "symptom_id" in s}
    _missing = _ontology_ids - _stored_ids
    _extra = _stored_ids - _ontology_ids
    if _missing:
        print(
            f"\n[ERROR] {len(_missing)} symptom(s) in ontology have NO embedding:\n"
            + "\n".join(f"  - {sid}" for sid in sorted(_missing))
            + "\nRun:  python embeddings.py\n"
        )
        sys.exit(1)
    if _extra:
        print(
            f"[WARNING] {len(_extra)} embedding(s) have no matching symptom in ontology (stale):\n"
            + "\n".join(f"  - {sid}" for sid in sorted(_extra))
        )
    print(f"Embeddings: {EMBEDDINGS_PATH} ({len(_ontology_ids)} symptoms, all covered)")
    print(f"Server:     http://127.0.0.1:{AGENT_PORT}/")
    print("Press Ctrl+C to stop the server.")
    open_browser(AGENT_PORT)
    app.run(host="127.0.0.1", port=AGENT_PORT, debug=False)


if __name__ == "__main__":
    main()
