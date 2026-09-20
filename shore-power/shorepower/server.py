"""HTTP JSON API（仅标准库实现），供船方/港方终端接入.

启动：python -m shorepower serve --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .report import render_html
from .services import ServiceError, ShorePowerService

_INDEX = """港口岸电使用核算系统 API

基础资料：
  GET  /api/vessels            船舶列表        POST /api/vessels {name, imo?, company?}
  GET  /api/voyages            航次列表        POST /api/voyages {vessel_id, voyage_no, berth?, arrived_at?, departed_at?}
  GET  /api/tariffs            电价列表        POST /api/tariffs {name, energy_price, service_price?, effective_from}

接电记录：
  GET  /api/sessions?voyage_id=              接电记录列表
  POST /api/sessions/start                   接电开始 {voyage_id, meter_no?, connect_at, meter_start, remark?}
  POST /api/sessions/{id}/stop               断电结束 {disconnect_at, meter_end}

对账单：
  GET  /api/statements?voyage_id=            对账单列表
  POST /api/statements                       生成对账单 {voyage_id}
  GET  /api/statements/{id}                  对账明细（JSON）
  GET  /api/statements/{id}/html             对账单（可打印页面）
  POST /api/statements/{id}/issue            出具
  POST /api/statements/{id}/ship-confirm     船方确认 {confirmer}
  POST /api/statements/{id}/ship-dispute     船方异议 {reason}
  POST /api/statements/{id}/port-confirm     港方确认办结 {confirmer}
  POST /api/statements/{id}/refresh          重新汇总（草稿/异议状态）
  POST /api/statements/{id}/cancel           作废（草稿/异议状态）
"""


def _make_handler(service: ShorePowerService):
    lock = threading.Lock()  # 串行化请求，保证单连接 SQLite 的并发安全

    class Handler(BaseHTTPRequestHandler):
        server_version = "ShorePower/1.0"

        # ---------------- 基础工具 ----------------

        def _send(self, code: int, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
            else:
                data = str(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ServiceError("请求体不是合法的 JSON")

        def _query(self) -> dict:
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            return {k: v[0] for k, v in qs.items()}

        def _path(self) -> str:
            from urllib.parse import urlparse
            return urlparse(self.path).path.rstrip("/") or "/"

        def _dispatch(self, routes):
            path, method = self._path(), self.command
            try:
                with lock:
                    self._route(routes, path, method)
            except ServiceError as exc:
                self._send(400, {"error": str(exc)})
            except (KeyError, TypeError, ValueError) as exc:
                self._send(400, {"error": f"参数错误：{exc}"})

        def _route(self, routes, path, method):
            for m, pattern, fn in routes:
                if m != method:
                    continue
                match = re.fullmatch(pattern, path)
                if match:
                    body = self._json_body() if method == "POST" else self._query()
                    result = fn(body, **match.groupdict())
                    if isinstance(result, tuple):  # (payload, content_type)
                        self._send(200, result[0], result[1])
                    else:
                        self._send(200, result)
                    return
            self._send(404, {"error": f"接口不存在：{method} {path}"})

        def log_message(self, fmt, *args):  # 静默访问日志
            pass

        # ---------------- 路由 ----------------

        def do_GET(self):
            svc = service
            self._dispatch([
                ("GET", r"/", lambda q: (_INDEX, "text/plain; charset=utf-8")),
                ("GET", r"/api/vessels", lambda q: svc.list_vessels()),
                ("GET", r"/api/voyages", lambda q: svc.list_voyages()),
                ("GET", r"/api/tariffs", lambda q: svc.list_tariffs()),
                ("GET", r"/api/sessions",
                 lambda q: svc.list_sessions(int(q["voyage_id"]) if "voyage_id" in q else None)),
                ("GET", r"/api/statements",
                 lambda q: svc.list_statements(int(q["voyage_id"]) if "voyage_id" in q else None)),
                ("GET", r"/api/statements/(?P<id>\d+)",
                 lambda q, id: svc.get_statement(int(id))),
                ("GET", r"/api/statements/(?P<id>\d+)/html",
                 lambda q, id: (render_html(svc.get_statement(int(id))),
                                "text/html; charset=utf-8")),
            ])

        def do_POST(self):
            svc = service
            self._dispatch([
                ("POST", r"/api/vessels",
                 lambda b: svc.add_vessel(b["name"], b.get("imo", ""), b.get("company", ""))),
                ("POST", r"/api/voyages",
                 lambda b: svc.add_voyage(int(b["vessel_id"]), b["voyage_no"],
                                          b.get("berth", ""), b.get("arrived_at"),
                                          b.get("departed_at"))),
                ("POST", r"/api/tariffs",
                 lambda b: svc.add_tariff(b["name"], float(b["energy_price"]),
                                          float(b.get("service_price", 0)),
                                          b["effective_from"])),
                ("POST", r"/api/sessions/start",
                 lambda b: svc.start_session(int(b["voyage_id"]), b.get("meter_no", ""),
                                             b["connect_at"], float(b["meter_start"]),
                                             b.get("remark", ""))),
                ("POST", r"/api/sessions/(?P<id>\d+)/stop",
                 lambda b, id: svc.stop_session(int(id), b["disconnect_at"],
                                                float(b["meter_end"]))),
                ("POST", r"/api/statements",
                 lambda b: svc.create_statement(int(b["voyage_id"]))),
                ("POST", r"/api/statements/(?P<id>\d+)/issue",
                 lambda b, id: svc.issue_statement(int(id))),
                ("POST", r"/api/statements/(?P<id>\d+)/ship-confirm",
                 lambda b, id: svc.ship_confirm(int(id), b.get("confirmer", ""))),
                ("POST", r"/api/statements/(?P<id>\d+)/ship-dispute",
                 lambda b, id: svc.ship_dispute(int(id), b.get("reason", ""))),
                ("POST", r"/api/statements/(?P<id>\d+)/port-confirm",
                 lambda b, id: svc.port_confirm(int(id), b.get("confirmer", ""))),
                ("POST", r"/api/statements/(?P<id>\d+)/refresh",
                 lambda b, id: svc.refresh_statement(int(id))),
                ("POST", r"/api/statements/(?P<id>\d+)/cancel",
                 lambda b, id: svc.cancel_statement(int(id))),
            ])

    return Handler


def run(service: ShorePowerService, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), _make_handler(service))
    print(f"岸电核算系统 API 已启动：http://{host}:{port}/（Ctrl+C 停止）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
