from __future__ import annotations
import http.client, socket, ssl
from urllib.parse import urlparse


def safe_tcp_probe(host: str, port: int, timeout: float = 5.0) -> dict:
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return {"status":"PASS", "classification":"TCP_REACHABLE", "tcp_connected":True,
                    "host":host, "port":int(port)}
    except (socket.timeout, TimeoutError):
        return {"status":"INCONCLUSIVE", "classification":"PROBE_TIMEOUT", "tcp_connected":False,
                "host":host, "port":int(port)}
    except OSError as exc:
        return {"status":"FAIL", "classification":"ENDPOINT_NOT_LISTENING", "tcp_connected":False,
                "host":host, "port":int(port), "error_type":type(exc).__name__, "error":str(exc)}
    except Exception as exc:
        return {"status":"INCONCLUSIVE", "classification":"PROBE_INTERNAL_ERROR", "tcp_connected":False,
                "host":host, "port":int(port), "error_type":type(exc).__name__, "error":str(exc)}


def safe_http_probe(url: str, timeout: float = 6.0) -> dict:
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname:
        return {"status":"INCONCLUSIVE", "classification":"UNSUPPORTED_PROTOCOL", "url":url,
                "tcp_connected":False}
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        sock = socket.create_connection((p.hostname, port), timeout=float(timeout))
        sock.close()
        tcp_connected = True
    except (socket.timeout, TimeoutError):
        return {"status":"INCONCLUSIVE", "classification":"PROBE_TIMEOUT", "url":url,
                "tcp_connected":False}
    except OSError as exc:
        return {"status":"FAIL", "classification":"ENDPOINT_NOT_LISTENING", "url":url,
                "tcp_connected":False, "error_type":type(exc).__name__, "error":str(exc)}
    except Exception as exc:
        return {"status":"INCONCLUSIVE", "classification":"PROBE_INTERNAL_ERROR", "url":url,
                "tcp_connected":False, "error_type":type(exc).__name__, "error":str(exc)}
    try:
        cls = http.client.HTTPSConnection if p.scheme == "https" else http.client.HTTPConnection
        conn = cls(p.hostname, port, timeout=float(timeout))
        conn.request("GET", p.path or "/", headers={"Accept":"text/html,*/*"})
        resp = conn.getresponse()
        ctype = resp.getheader("Content-Type", "")
        resp.read(1024)
        conn.close()
        return {"status":"PASS", "classification":"HTTP_RESPONSE_RECEIVED", "url":url,
                "tcp_connected":tcp_connected, "http_status":resp.status, "content_type":ctype}
    except (http.client.BadStatusLine, http.client.HTTPException, ValueError) as exc:
        return {"status":"INCONCLUSIVE", "classification":"HTTP_PROTOCOL_MISMATCH", "url":url,
                "tcp_connected":tcp_connected, "http_response_received":False,
                "error_type":type(exc).__name__, "error":str(exc)}
    except (ssl.SSLError,) as exc:
        return {"status":"INCONCLUSIVE", "classification":"TLS_PROTOCOL_MISMATCH", "url":url,
                "tcp_connected":tcp_connected, "http_response_received":False,
                "error_type":type(exc).__name__, "error":str(exc)}
    except (socket.timeout, TimeoutError) as exc:
        return {"status":"INCONCLUSIVE", "classification":"PROBE_TIMEOUT", "url":url,
                "tcp_connected":tcp_connected, "http_response_received":False,
                "error_type":type(exc).__name__, "error":str(exc)}
    except OSError as exc:
        return {"status":"INCONCLUSIVE", "classification":"HTTP_CONNECTION_ERROR", "url":url,
                "tcp_connected":tcp_connected, "http_response_received":False,
                "error_type":type(exc).__name__, "error":str(exc)}
    except Exception as exc:
        return {"status":"INCONCLUSIVE", "classification":"PROBE_INTERNAL_ERROR", "url":url,
                "tcp_connected":tcp_connected, "http_response_received":False,
                "error_type":type(exc).__name__, "error":str(exc)}
