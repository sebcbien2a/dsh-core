"""P22 - Local web interface for the APD four-stage cockpit.

Run ``python scripts/apd_pipeline_web.py`` and open ``http://127.0.0.1:8765``.
The page exposes the same structured commands as the Hermès Telegram bridge:
status, pending questions, stage execution and one-click decisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.apd_four_stage_pipeline import DEFAULT_TENANT, DEFAULT_YEAR
from services.apd_control_panel import dashboard_html
from services.hermes_accounting_bridge import HermesAccountingBridge


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _bridge(args) -> HermesAccountingBridge:
    return HermesAccountingBridge(
        args.tenant,
        root=args.root,
        db_path=args.db,
        export_dir=args.export_dir,
        year=args.year,
    )


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>APD - Cockpit P22</title>
<style>
body{font-family:system-ui,Segoe UI,Arial;margin:24px;background:#f7f9fc;color:#172033}
h1{font-size:22px}button{padding:8px 14px;margin:4px;border:0;border-radius:6px;background:#1f4e78;color:#fff;cursor:pointer}
button.secondary{background:#5b6b7f}pre{background:#fff;border:1px solid #d8e0ea;padding:14px;border-radius:8px;overflow:auto;max-height:60vh}
.row{display:flex;gap:18px;align-items:flex-start}.col{flex:1}
</style>
</head>
<body>
<h1>APD - Pipeline comptable P22</h1>
<div class="row">
<div class="col">
<h3>Actions</h3>
<button onclick="call('/api/status')">Statut</button>
<button onclick="call('/api/pending')">Questions en attente</button>
<button onclick="stage(1)">Étape 1 - Vision</button>
<button onclick="stage(2)">Étape 2 - Banque</button>
<button onclick="stage(3)">Étape 3 - Ventilation</button>
<button onclick="stage(4)">Étape 4 - Simulations</button>
<button class="secondary" onclick="call('/api/run-all')">Tout exécuter</button>
</div>
<div class="col">
<h3>Réponse</h3>
<pre id="out">Prêt.</pre>
</div>
</div>
<script>
async function call(url){
  const r=await fetch(url,{method:'POST'}); const j=await r.json();
  document.getElementById('out').textContent=JSON.stringify(j,null,2);
}
async function stage(n){call('/api/stage/'+n);}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    bridge: HermesAccountingBridge | None = None

    def log_message(self, format, *args):  # noqa: A003 - stdlib signature
        return

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode(
            "utf-8"
        )
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, exc: Exception, status: int = 400) -> None:
        self._json({"error": type(exc).__name__, "message": str(exc)}, status=status)

    def do_GET(self):  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send(
                200,
                dashboard_html().encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/status":
            try:
                self._json(self.bridge.status_summary())
            except Exception as exc:  # noqa: BLE001
                self._error(exc, 500)
            return
        if path == "/api/pending":
            try:
                self._json(self.bridge.list_pending_questions())
            except Exception as exc:  # noqa: BLE001
                self._error(exc, 500)
            return
        self._json({"error": "NOT_FOUND"}, status=404)

    def do_POST(self):  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        try:
            if path == "/api/status":
                self._json(self.bridge.status_summary())
                return
            if path == "/api/pending":
                self._json(self.bridge.list_pending_questions())
                return
            if path == "/api/run-all":
                self._json(self.bridge.run_all())
                return
            if path.startswith("/api/stage/"):
                stage = int(path.rsplit("/", 1)[-1])
                self._json(self.bridge.run_stage(stage))
                return
            if path == "/api/decide":
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                arbitration = {
                    "category": payload.get("category"),
                    "fiscal_year_target": payload.get(
                        "fiscal_year_target"
                    ),
                    "payment_source": payload.get("payment_source"),
                    "counterparty_account": payload.get(
                        "counterparty_account"
                    ),
                    "month": payload.get("month"),
                    "report": payload.get("report"),
                    "expense_subcategory": payload.get(
                        "expense_subcategory"
                    ),
                    "tier_alias_target": payload.get(
                        "tier_alias_target"
                    ),
                }
                self._json(
                    self.bridge.decide_document(
                        payload.get("doc_id", ""),
                        payload.get("decision", ""),
                        arbitration=arbitration,
                    )
                )
                return
            self._json({"error": "NOT_FOUND"}, status=404)
        except Exception as exc:  # noqa: BLE001
            self._error(exc, 500)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--root", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--export-dir", default=None)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    Handler.bridge = _bridge(args)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Interface APD P25 : http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
