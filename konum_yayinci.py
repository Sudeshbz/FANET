"""
konum_yayinci.py
----------------
Topoloji (fanet_topoloji.py) ile SDN kontrolcüsü (fanet_controller.py)
arasındaki köprü REST API servisi.

Görevleri:
  1. Topolojiden gelen konum verilerini alır ve saklar  (POST /fanet/positions)
  2. Kontrolcünün konum sorgulamasına yanıt verir       (GET  /fanet/positions)
  3. Tek bir düğümün konumunu döndürür                  (GET  /fanet/positions/<name>)
  4. Düğüm durumu güncellemelerini kabul eder           (POST /fanet/status)
  5. Sağlık kontrolü                                    (GET  /fanet/health)

Çalıştırma:
  python3 konum_yayinci.py

Bağımlılıklar:
  pip install flask
"""

import time
import threading
import logging
from flask import Flask, request, jsonify

# ─── Yapılandırma ─────────────────────────────────────────────────────────────

API_HOST    = "0.0.0.0"
API_PORT    = 8080
LOG_LEVEL   = logging.INFO

# Bir düğümden bu kadar süredir veri gelmezse "stale" say (saniye)
STALE_THRESHOLD = 10.0

# ─── Uygulama kurulumu ───────────────────────────────────────────────────────

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("konum_yayinci")

app = Flask(__name__)
app.logger.setLevel(logging.WARNING)  # Flask'ın kendi loglarını sustur

# ─── Paylaşılan durum ────────────────────────────────────────────────────────

_lock = threading.Lock()

# {node_name: {"x": float, "y": float, "z": float, "ts": float}}
_positions: dict = {}

# {node_name: {"alive": bool, "ts": float}}
_status: dict = {}

_start_time = time.time()
_update_count = 0


# ─── Yardımcılar ─────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _is_stale(ts: float) -> bool:
    return (_now() - ts) > STALE_THRESHOLD


def _enrich(name: str, pos: dict) -> dict:
    """Konum kaydına meta veri ekle."""
    return {
        "x":     pos["x"],
        "y":     pos["y"],
        "z":     pos["z"],
        "ts":    pos["ts"],
        "age_s": round(_now() - pos["ts"], 2),
        "stale": _is_stale(pos["ts"]),
    }


# ─── Endpoint'ler ─────────────────────────────────────────────────────────────

@app.route("/fanet/health", methods=["GET"])
def health():
    """Servis sağlık kontrolü."""
    with _lock:
        node_count = len(_positions)
        stale_count = sum(1 for p in _positions.values() if _is_stale(p["ts"]))

    return jsonify({
        "status":       "ok",
        "uptime_s":     round(_now() - _start_time, 1),
        "node_count":   node_count,
        "stale_count":  stale_count,
        "update_count": _update_count,
    }), 200


@app.route("/fanet/positions", methods=["POST"])
def receive_positions():
    """
    Topoloji tarafından gönderilen toplu konum güncellemesi.

    Beklenen JSON:
    {
        "uav1": {"x": 45.2, "y": 80.1, "z": 30.0},
        "ugv3": {"x": 12.0, "y": 55.0, "z": 0.0},
        ...
    }
    """
    global _update_count

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Geçersiz JSON gövdesi"}), 400

    ts = _now()
    updated = []
    errors  = []

    with _lock:
        for name, pos in data.items():
            try:
                x = float(pos["x"])
                y = float(pos["y"])
                z = float(pos.get("z", 0.0))
                _positions[name] = {"x": x, "y": y, "z": z, "ts": ts}
                updated.append(name)
            except (KeyError, TypeError, ValueError) as e:
                errors.append({"node": name, "error": str(e)})

        _update_count += 1

    if errors:
        log.warning("Konum güncellemesinde hatalar: %s", errors)
    else:
        log.debug("Konum güncellendi: %d düğüm", len(updated))

    return jsonify({
        "updated": updated,
        "errors":  errors,
        "ts":      ts,
    }), 200


@app.route("/fanet/positions", methods=["GET"])
def get_all_positions():
    """
    Tüm düğümlerin güncel konumlarını döndür.
    Kontrolcü bu endpoint'i periyodik olarak sorgular.

    Yanıt:
    {
        "uav1": {"x": 45.2, "y": 80.1, "z": 30.0, "ts": ..., "age_s": ..., "stale": false},
        ...
    }
    """
    node_type = request.args.get("type")  # ?type=uav veya ?type=ugv filtresi

    with _lock:
        if node_type:
            result = {
                name: _enrich(name, pos)
                for name, pos in _positions.items()
                if name.startswith(node_type)
            }
        else:
            result = {
                name: _enrich(name, pos)
                for name, pos in _positions.items()
            }

    return jsonify(result), 200


@app.route("/fanet/positions/<string:node_name>", methods=["GET"])
def get_position(node_name: str):
    """
    Tek bir düğümün konumunu döndür.
    Düğüm bulunamazsa 404 döner.
    """
    with _lock:
        pos = _positions.get(node_name)

    if pos is None:
        return jsonify({"error": f"Düğüm bulunamadı: {node_name}"}), 404

    return jsonify(_enrich(node_name, pos)), 200


@app.route("/fanet/status", methods=["POST"])
def receive_status():
    """
    Düğüm canlılık durumu güncellemesi.
    Kontrolcü veya topoloji tarafından kullanılabilir.

    Beklenen JSON:
    {
        "uav3": {"alive": false},
        "uav7": {"alive": true}
    }
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Geçersiz JSON gövdesi"}), 400

    ts = _now()
    with _lock:
        for name, info in data.items():
            _status[name] = {
                "alive": bool(info.get("alive", True)),
                "ts":    ts,
            }

    log.info("Durum güncellendi: %s", list(data.keys()))
    return jsonify({"ok": True, "ts": ts}), 200


@app.route("/fanet/status", methods=["GET"])
def get_all_status():
    """Tüm düğümlerin durumunu döndür."""
    with _lock:
        result = {
            name: {
                "alive":  s["alive"],
                "ts":     s["ts"],
                "age_s":  round(_now() - s["ts"], 2),
            }
            for name, s in _status.items()
        }
    return jsonify(result), 200


@app.route("/fanet/summary", methods=["GET"])
def summary():
    """
    Ağın anlık özet durumu — debug ve izleme için.
    """
    with _lock:
        pos_snap    = dict(_positions)
        status_snap = dict(_status)

    uavs = {k: v for k, v in pos_snap.items() if k.startswith("uav")}
    ugvs = {k: v for k, v in pos_snap.items() if k.startswith("ugv")}

    alive_uavs = [
        n for n, s in status_snap.items()
        if n.startswith("uav") and s.get("alive", True)
    ]

    return jsonify({
        "uav_count":      len(uavs),
        "ugv_count":      len(ugvs),
        "alive_uavs":     alive_uavs,
        "alive_uav_count": len(alive_uavs),
        "stale_nodes":    [
            n for n, p in pos_snap.items() if _is_stale(p["ts"])
        ],
        "update_count":   _update_count,
        "uptime_s":       round(_now() - _start_time, 1),
    }), 200


# ─── Giriş noktası ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Konum yayıncısı başlatılıyor — http://%s:%d", API_HOST, API_PORT)
    log.info("Endpoint'ler:")
    log.info("  GET  /fanet/health")
    log.info("  POST /fanet/positions   <- topoloji buraya yazar")
    log.info("  GET  /fanet/positions   <- kontrolcü buradan okur")
    log.info("  GET  /fanet/positions/<name>")
    log.info("  POST /fanet/status")
    log.info("  GET  /fanet/summary")

    app.run(
        host=API_HOST,
        port=API_PORT,
        debug=False,
        threaded=True,
    )
