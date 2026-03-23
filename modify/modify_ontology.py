from __future__ import annotations

import os
import threading
import webbrowser

from config import ONTOLOGY_PATH
from routes import app
from state import load_ontology


def open_browser(port: int) -> None:
    def _open() -> None:
        webbrowser.open(f"http://127.0.0.1:{port}/", new=2)

    threading.Timer(1.2, _open).start()


def main() -> None:
    port_str = os.getenv("ONTOLOGY_GRAPH_PORT", "5000")
    try:
        port = int(port_str)
    except ValueError:
        port = 5000

    _ = load_ontology()  # fail fast if file missing

    print(f"Ontology: {ONTOLOGY_PATH}")
    print(f"Server:   http://127.0.0.1:{port}/")
    open_browser(port)
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
