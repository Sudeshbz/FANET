"""
fanet_topoloji.py
-----------------
10 UAV + 5 UGV ile Mininet-WiFi tabanlı FANET topolojisi.

Özellikler:
  - Ölçeklenebilir düğüm sayısı (UAV_COUNT / UGV_COUNT sabitleri)
  - Sürekli ve rastgele mobilite (GaussMarkov benzeri waypoint modeli)
  - Her hareket sonrası konumları REST API ile kontrolcüye bildiren thread
  - LogDistance propagasyon modeli (exp=3)
  - VXLAN tünelleme (AP <-> backbone switch)
  - wmediumd interference modu (gerçekçi kablosuz kanal)
  - Otomatik metrik toplama (iperf3 tabanlı)

Çalıştırma:
  sudo python3 fanet_topoloji.py

Bağımlılıklar:
  pip install requests
  mn-wifi kurulu olmalı
"""

import os
import sys
import time
import random
import threading
import subprocess
import json

try:
    import requests
except ImportError:
    print("[HATA] 'requests' paketi bulunamadı. Kurmak için: pip install requests")
    sys.exit(1)

from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info, warning, error
from mn_wifi.net import Mininet_wifi
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference


# ─── Yapılandırma ────────────────────────────────────────────────────────────

UAV_COUNT        = 10
UGV_COUNT        = 5
CONTROLLER_IP    = "127.0.0.1"
CONTROLLER_PORT  = 6653
POSITION_API_URL = "http://127.0.0.1:8080/fanet/positions"  # konum_yayinci.py

AREA_X           = 200   # simülasyon alanı genişliği (metre)
AREA_Y           = 200   # simülasyon alanı yüksekliği
UAV_HEIGHT       = 30    # UAV'ların sabit uçuş irtifası
UAV_RANGE        = 80    # UAV kablosuz menzili (metre)
UGV_RANGE        = 50    # UGV kablosuz menzili (metre)
AP_RANGE         = 100   # AP menzili

MOBILITY_INTERVAL   = 3.0   # saniyede bir konum güncelleme
WAYPOINT_STEP_MAX   = 20.0  # tek adımda maksimum hareket (metre)
POSITION_SEND_INTERVAL = 2.0  # kontrolcüye konum gönderme periyodu

SIMULATION_DURATION = 120  # toplam simülasyon süresi (saniye), 0 = CLI açık kal

VXLAN_VNI        = 100
AP_UNDERLAY_IP   = "192.168.10.1/24"
SW_UNDERLAY_IP   = "192.168.10.2/24"

# ─── Küresel durum ───────────────────────────────────────────────────────────

positions   = {}   # {isim: (x, y, z)}
net_ref     = None
stop_event  = threading.Event()


# ─── Yardımcı fonksiyonlar ───────────────────────────────────────────────────

def random_position(z=0.0):
    """Alan içinde rastgele konum üret."""
    x = round(random.uniform(10, AREA_X - 10), 2)
    y = round(random.uniform(10, AREA_Y - 10), 2)
    return (x, y, z)


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


def next_waypoint(current, z=0.0):
    """Mevcut konumdan küçük bir adım at (random walk)."""
    cx, cy, _ = current
    dx = random.uniform(-WAYPOINT_STEP_MAX, WAYPOINT_STEP_MAX)
    dy = random.uniform(-WAYPOINT_STEP_MAX, WAYPOINT_STEP_MAX)
    nx = clamp(cx + dx, 5, AREA_X - 5)
    ny = clamp(cy + dy, 5, AREA_Y - 5)
    return (round(nx, 2), round(ny, 2), z)


def pos_string(pos):
    """Mininet-WiFi'nin beklediği 'x,y,z' formatına çevir."""
    return f"{pos[0]},{pos[1]},{pos[2]}"


# ─── Mobilite thread'i ───────────────────────────────────────────────────────

def set_node_position(node, pos):
    """
    Mininet-WiFi versiyonuna göre konum güncelleme.
    Farklı versiyonlar farklı metod kullanıyor.
    """
    pos_str = pos_string(pos)
    # Yöntem 1: setPosition string ile
    if hasattr(node, 'setPosition'):
        try:
            node.setPosition(pos_str)
            return
        except Exception:
            pass
    # Yöntem 2: params dict üzerinden
    try:
        node.params['position'] = pos_str
        if hasattr(node, 'updatePosition'):
            node.updatePosition()
        return
    except Exception:
        pass
    # Yöntem 3: x,y,z attribute'larını direkt güncelle
    try:
        node.params['x'] = str(pos[0])
        node.params['y'] = str(pos[1])
        node.params['z'] = str(pos[2])
    except Exception:
        pass


def mobility_loop():
    """
    Tüm UAV'leri periyodik olarak rastgele hareket ettirir.
    UGV'ler yavaş hareket eder (adım boyutu UAV'nin 1/4'ü).
    """
    info("[Mobilite] Thread başladı\n")
    while not stop_event.is_set():
        if net_ref is None:
            time.sleep(0.5)
            continue

        for uav_name, pos in list(positions.items()):
            if not uav_name.startswith("uav"):
                continue
            try:
                node = net_ref.get(uav_name)
                new_pos = next_waypoint(pos, z=UAV_HEIGHT)
                positions[uav_name] = new_pos
                set_node_position(node, new_pos)
            except Exception as e:
                warning(f"[Mobilite] {uav_name} hareket hatası: {e}\n")

        for ugv_name, pos in list(positions.items()):
            if not ugv_name.startswith("ugv"):
                continue
            try:
                node = net_ref.get(ugv_name)
                cx, cy, cz = pos
                dx = random.uniform(-WAYPOINT_STEP_MAX / 4, WAYPOINT_STEP_MAX / 4)
                dy = random.uniform(-WAYPOINT_STEP_MAX / 4, WAYPOINT_STEP_MAX / 4)
                new_pos = (
                    round(clamp(cx + dx, 5, AREA_X - 5), 2),
                    round(clamp(cy + dy, 5, AREA_Y - 5), 2),
                    0.0
                )
                positions[ugv_name] = new_pos
                set_node_position(node, new_pos)
            except Exception as e:
                warning(f"[Mobilite] {ugv_name} hareket hatası: {e}\n")

        stop_event.wait(MOBILITY_INTERVAL)

    info("[Mobilite] Thread durdu\n")


# ─── Konum yayın thread'i ────────────────────────────────────────────────────

def position_sender_loop():
    """
    Güncel konumları REST API üzerinden kontrolcüye iletir.
    Kontrolcü bu verileri gerçek zamanlı karar almak için kullanır.
    """
    info("[KonumYayıcı] Thread başladı\n")
    consecutive_failures = 0

    while not stop_event.is_set():
        payload = {
            name: {"x": pos[0], "y": pos[1], "z": pos[2]}
            for name, pos in positions.items()
        }
        try:
            resp = requests.post(
                POSITION_API_URL,
                json=payload,
                timeout=1.5
            )
            if resp.status_code == 200:
                consecutive_failures = 0
            else:
                warning(f"[KonumYayıcı] API yanıt kodu: {resp.status_code}\n")
        except requests.exceptions.ConnectionError:
            consecutive_failures += 1
            if consecutive_failures == 1:
                warning("[KonumYayıcı] Kontrolcü API'ye bağlanılamıyor — "
                        "konum_yayinci.py çalışıyor mu?\n")
        except Exception as e:
            warning(f"[KonumYayıcı] Hata: {e}\n")

        stop_event.wait(POSITION_SEND_INTERVAL)

    info("[KonumYayıcı] Thread durdu\n")


# ─── VXLAN kurulumu ──────────────────────────────────────────────────────────

def setup_vxlan(net):
    """AP ile backbone switch arasına VXLAN tüneli kur."""
    info("[VXLAN] Tünel kuruluyor...\n")
    ap1 = net.get("ap1")
    s1  = net.get("s1")

    ap1.cmd(f"ip addr add {AP_UNDERLAY_IP} dev ap1-eth1 2>/dev/null || true")
    s1.cmd(f"ip addr add {SW_UNDERLAY_IP} dev s1-eth1 2>/dev/null || true")

    sw_ip = SW_UNDERLAY_IP.split("/")[0]
    ap_ip = AP_UNDERLAY_IP.split("/")[0]

    ap1.cmd(
        f"ovs-vsctl add-port ap1 vxlan0 -- "
        f"set interface vxlan0 type=vxlan "
        f"options:remote_ip={sw_ip} options:key={VXLAN_VNI} 2>/dev/null || true"
    )
    s1.cmd(
        f"ovs-vsctl add-port s1 vxlan0 -- "
        f"set interface vxlan0 type=vxlan "
        f"options:remote_ip={ap_ip} options:key={VXLAN_VNI} 2>/dev/null || true"
    )
    info("[VXLAN] Tünel hazır\n")


# ─── Metrik toplama ──────────────────────────────────────────────────────────

def collect_metrics(net, duration=10):
    info("[Metrik] Ölçüm başlıyor...\n")
    ap1 = net.get("ap1")
    results = {}

    # AP'de iperf3 sunucusu başlat
    ap1.cmd("iperf3 -s -D")
    time.sleep(1)
    ap_ip = ap1.IP()

    for i in range(1, UGV_COUNT + 1):
        ugv_name = f"ugv{i}"
        try:
            ugv = net.get(ugv_name)

            # Ping testi (paket kaybı ve gecikme)
            ping_out = ugv.cmd(f"ping -c 20 -i 0.2 {ap_ip} 2>/dev/null")
            loss_pct = 100.0
            avg_rtt  = 0.0
            import re
            loss_m = re.search(r'(\d+)% packet loss', ping_out)
            rtt_m  = re.search(r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/', ping_out)
            if loss_m:
                loss_pct = float(loss_m.group(1))
            if rtt_m:
                avg_rtt = float(rtt_m.group(1))

            # iperf3 testi (throughput)
            iperf_out = ugv.cmd(
                f"iperf3 -c {ap_ip} -t {duration} -J 2>/dev/null"
            )
            throughput_mbps = 0.0
            try:
                iperf_data = json.loads(iperf_out)
                bits = iperf_data["end"]["sum_received"]["bits_per_second"]
                throughput_mbps = round(bits / 1e6, 3)
            except Exception:
                pass

            results[ugv_name] = {
                "throughput_mbps": throughput_mbps,
                "packet_loss_pct": loss_pct,
                "jitter_ms":       avg_rtt,
                "energy_joule":    0.0,  # Mininet-WiFi enerji modeli eklendikten sonra
            }
        except Exception as e:
            results[ugv_name] = {"error": str(e)}

    ap1.cmd("killall iperf3 2>/dev/null")

    output_path = "fanet_mininet_metrics.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    info(f"[Metrik] {output_path} kaydedildi\n")
    return results


# ─── Ana topoloji fonksiyonu ─────────────────────────────────────────────────

def topology():
    global net_ref

    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
        switch=OVSKernelSwitch,
        autoAssociation=True
    )

    # Kontrolcü
    info("*** Kontrolcü ekleniyor\n")
    c0 = net.addController(
        "c0",
        controller=RemoteController,
        ip=CONTROLLER_IP,
        port=CONTROLLER_PORT
    )

    # AP
    info("*** AP ekleniyor\n")
    ap1 = net.addAccessPoint(
        "ap1",
        ssid="fanet-ag",
        mode="g",
        channel="6",
        position=f"{AREA_X//2},{AREA_Y//2},0",
        range=AP_RANGE
    )

    # UAV'lar
    info(f"*** {UAV_COUNT} UAV ekleniyor\n")
    for i in range(1, UAV_COUNT + 1):
        pos = random_position(z=float(UAV_HEIGHT))
        positions[f"uav{i}"] = pos
        net.addStation(
            f"uav{i}",
            ip=f"10.0.0.{i}/8",
            position=pos_string(pos),
            range=UAV_RANGE
        )

    # UGV'ler
    info(f"*** {UGV_COUNT} UGV ekleniyor\n")
    for i in range(1, UGV_COUNT + 1):
        pos = random_position(z=0.0)
        positions[f"ugv{i}"] = pos
        net.addStation(
            f"ugv{i}",
            ip=f"10.0.1.{i}/8",
            position=pos_string(pos),
            range=UGV_RANGE
        )

    # AP konumu da position dict'e ekle
    positions["ap1"] = (float(AREA_X // 2), float(AREA_Y // 2), 0.0)

    # Backbone switch
    info("*** Backbone switch ekleniyor\n")
    s1 = net.addSwitch("s1")
    net.addLink(ap1, s1)

    # Propagasyon modeli
    net.setPropagationModel(model="logDistance", exp=3)

    # WiFi düğüm yapılandırması
    info("*** WiFi düğümleri yapılandırılıyor\n")
    net.configureWifiNodes()

    # Görselleştirme (opsiyonel — X sunucu gerektirir)
    try:
        net.plotGraph(max_x=AREA_X + 20, max_y=AREA_Y + 20)
    except Exception:
        pass

    # Ağı başlat
    info("*** Ağ başlatılıyor\n")
    net.build()
    c0.start()
    ap1.start([c0])
    s1.start([c0])

    # VXLAN
    setup_vxlan(net)

    # Global referansı ayarla (thread'ler kullanacak)
    net_ref = net

    # Mobilite thread'ini başlat
    mob_thread = threading.Thread(target=mobility_loop, daemon=True, name="Mobilite")
    mob_thread.start()

    # Konum yayın thread'ini başlat
    pos_thread = threading.Thread(target=position_sender_loop, daemon=True, name="KonumYayici")
    pos_thread.start()

    # Simülasyon modu
    if SIMULATION_DURATION > 0:
        info(f"*** Simülasyon {SIMULATION_DURATION} saniye çalışacak\n")
        time.sleep(30)
        info("*** Ağ otururdu, metrik toplama başlıyor\n")
        collect_metrics(net, duration=15)
        time.sleep(max(0, SIMULATION_DURATION - 45))
        stop_event.set()
    else:
        info("*** CLI başlatılıyor (Ctrl+D ile çıkış)\n")
        CLI(net)
        stop_event.set()

    # Temizlik
    info("*** Ağ kapatılıyor\n")
    net.stop()


# ─── Giriş noktası ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("[HATA] Bu script root (sudo) ile çalıştırılmalıdır.")
        sys.exit(1)

    setLogLevel("info")
    os.system("mn -c > /dev/null 2>&1")
    topology()