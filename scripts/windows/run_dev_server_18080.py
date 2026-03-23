from http.server import ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import services.api.dev_server as dev


if __name__ == "__main__":
    dev.init_db()
    server = ThreadingHTTPServer(("127.0.0.1", 18080), dev.Handler)
    print("dev server on http://127.0.0.1:18080", flush=True)
    server.serve_forever()
