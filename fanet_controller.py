"""
fanet_controller.py
-------------------
Ryu tabanlı SDN kontrolcüsü — FANET için gerçek zamanlı karar motoru.

Özellikler:
  - konum_yayinci.py'den gerçek zamanlı konum verisi çeker
  - Dinamik kümeleme: enerji + mesafe + yük skoruyla CH seçimi
  - Çok yollu yönlendirme: primary + backup path (LB-MCR)
  - Greedy repair: primary düşünce anlık failover
  - OpenFlow akış kurallarını gerçekten switch'e yazar
  - Her UGV için bağımsız rota kararı
  - Metrik loglama: throughput sayacı, failover geçmişi, enerji takibi

Çalıştırma:
  ryu-manager fanet_controller.py

Bağımlılıklar:
  ryu, requests
"""

import time
import threading
from math import sqrt

import requests

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib import hub


# ─── Yapılandırma ─────────────────────────────────────────────────────────────

POSITION_API      = "http://127.0.0.1:8080/fanet/positions"
STATUS_API        = "http://127.0.0.1:8080/fanet/status"
POSITION_INTERVAL = 2.0     # konum çekme periyodu (saniye)
MONITOR_INTERVAL  = 2.0     # izleme döngüsü periyodu

UAV_COUNT         = 10
UGV_COUNT         = 5
AP_NAME           = "ap1"
AP_POSITION       = (100.0, 100.0, 0.0)  # fanet_topoloji.py ile uyumlu

MAX_LINK_DISTANCE = 80.0    # UAV-AP maksimum bağlantı mesafesi
STALE_THRESHOLD   = 8.0     # bu kadar süredir konum gelmezse düğüm stale

# Skor ağırlıkları (toplamı 1.0 olmalı)
W_DISTANCE = 0.40
W_ENERGY   = 0.35
W_LOAD     = 0.25

# Enerji tüketim oranları (her monitor döngüsünde)
DRAIN_PRIMARY  = 0.007
DRAIN_BACKUP   = 0.003
DRAIN_IDLE     = 0.001

# OpenFlow akış zaman aşımları
FLOW_IDLE_TIMEOUT = 15
FLOW_HARD_TIMEOUT = 60
FLOW_PRIORITY_SDN = 10   # SDN kararı — learning switch'ten yüksek
FLOW_PRIORITY_MISS = 0   # table-miss


# ─── Kontrolcü ────────────────────────────────────────────────────────────────

class FanetController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Switch kayıtları
        self.datapaths   = {}     # {dpid: datapath}
        self.mac_to_port = {}     # {dpid: {mac: port}}

        # Düğüm konumları — konum_yayinci'dan doldurulur
        self.positions   = {}     # {name: (x, y, z)}
        self.pos_ts      = {}     # {name: timestamp}

        # UAV / UGV listeleri
        self.uav_nodes = [f"uav{i}" for i in range(1, UAV_COUNT + 1)]
        self.ugv_nodes = [f"ugv{i}" for i in range(1, UGV_COUNT + 1)]

        # UAV durumu
        self.uav_alive   = {u: False for u in self.uav_nodes}
        self.energy      = {u: 1.0   for u in self.uav_nodes}
        self.load        = {u: 0.0   for u in self.uav_nodes}
        self.pkt_count   = {u: 0     for u in self.uav_nodes}

        # AP konumu sabit
        self.positions[AP_NAME] = AP_POSITION

        # Rota tablosu: {ugv_name: {"primary": uav, "backup": uav, "path": str}}
        self.route_table = {}

        # Küme yapısı: {ch_uav: [üye_uavlar]}
        self.clusters = {}

        # Aktif primary / backup
        self.current_primary = None
        self.current_backup  = None

        # Failover geçmişi
        self.failover_history = []

        # Thread'leri başlat
        self._pos_thread = hub.spawn(self._position_fetcher)
        self._mon_thread = hub.spawn(self._monitor)

        self.logger.info("FanetController başlatıldı — %d UAV, %d UGV",
                         UAV_COUNT, UGV_COUNT)

    # ─── Geometri yardımcıları ────────────────────────────────────────────────

    def _dist(self, a, b):
        return sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    def _dist_to_ap(self, uav_name):
        pos = self.positions.get(uav_name)
        if pos is None:
            return float("inf")
        return self._dist(pos, self.positions[AP_NAME])

    def _normalize(self, val, max_val):
        if max_val <= 0:
            return 0.0
        return min(max(val / max_val, 0.0), 1.0)

    # ─── Konum çekme thread'i ─────────────────────────────────────────────────

    def _position_fetcher(self):
        """
        konum_yayinci.py'den periyodik olarak konum verisi çeker.
        UAV'ların erişilebilirlik durumunu günceller.
        """
        consecutive_fail = 0
        while True:
            try:
                resp = requests.get(POSITION_API, timeout=2.0)
                if resp.status_code == 200:
                    data = resp.json()
                    now  = time.time()
                    consecutive_fail = 0

                    for name, info in data.items():
                        self.positions[name] = (
                            float(info["x"]),
                            float(info["y"]),
                            float(info["z"]),
                        )
                        self.pos_ts[name] = now

                    # Erişilebilirlik güncelle
                    self._refresh_uav_alive()

            except requests.exceptions.ConnectionError:
                consecutive_fail += 1
                if consecutive_fail == 1:
                    self.logger.warning(
                        "Konum API'ye bağlanılamıyor — konum_yayinci.py çalışıyor mu?"
                    )
            except Exception as e:
                self.logger.error("Konum çekme hatası: %s", e)

            hub.sleep(POSITION_INTERVAL)

    def _refresh_uav_alive(self):
        """
        Konum verisine göre her UAV'ın erişilebilirliğini güncelle.
        Stale veri varsa veya menzil dışındaysa down say.
        """
        now = time.time()
        changed = False

        for uav in self.uav_nodes:
            ts  = self.pos_ts.get(uav, 0)
            old = self.uav_alive[uav]

            if (now - ts) > STALE_THRESHOLD:
                new = False  # veri gelmiyor
            else:
                new = self._dist_to_ap(uav) <= MAX_LINK_DISTANCE

            if old != new:
                self.uav_alive[uav] = new
                changed = True
                self.logger.warning("UAV durum değişti: %s -> %s",
                                    uav, "CANLI" if new else "ÖLÜDEVRE")
                self._push_status_update(uav, new)

        if changed:
            self.update_routes(force_log=True)

    def _push_status_update(self, uav_name, alive):
        """Durum değişimini konum_yayinci'ya bildir."""
        try:
            requests.post(
                STATUS_API,
                json={uav_name: {"alive": alive}},
                timeout=1.0
            )
        except Exception:
            pass  # kritik değil

    # ─── Skor hesabı ──────────────────────────────────────────────────────────

    def _score(self, uav_name):
        """
        Küçük skor = daha iyi aday.
        Mesafe düşük + enerji yüksek + yük düşük olsun.
        """
        d = self._dist_to_ap(uav_name)
        d_norm   = self._normalize(d, MAX_LINK_DISTANCE)
        e_penalty = 1.0 - self.energy[uav_name]
        l_norm   = min(max(self.load[uav_name], 0.0), 1.0)

        return (
            W_DISTANCE * d_norm +
            W_ENERGY   * e_penalty +
            W_LOAD     * l_norm
        )

    # ─── Kümeleme ─────────────────────────────────────────────────────────────

    def _build_clusters(self, alive_uavs):
        """
        Basit mesafe tabanlı kümeleme:
        En iyi skorlu UAV küme başı (CH) olur.
        CH menzilindeki diğer UAV'lar o kümenin üyesi olur.
        Kalan UAV'lar kendi başına küme oluşturur.
        """
        if not alive_uavs:
            self.clusters = {}
            return

        scored = sorted(alive_uavs, key=lambda u: self._score(u))
        assigned = set()
        clusters = {}

        for ch in scored:
            if ch in assigned:
                continue
            ch_pos  = self.positions.get(ch)
            members = []

            for other in scored:
                if other == ch or other in assigned:
                    continue
                other_pos = self.positions.get(other)
                if ch_pos and other_pos:
                    if self._dist(ch_pos, other_pos) <= MAX_LINK_DISTANCE * 0.7:
                        members.append(other)
                        assigned.add(other)

            clusters[ch] = members
            assigned.add(ch)

        self.clusters = clusters
        self.logger.info("Kümeler güncellendi: %d küme, başlar=%s",
                         len(clusters), list(clusters.keys()))

    # ─── Primary / backup seçimi ──────────────────────────────────────────────

    def _choose_primary_backup(self, alive_uavs):
        if not alive_uavs:
            return None, None

        scored = sorted(alive_uavs, key=lambda u: self._score(u))
        primary = scored[0]
        backup  = scored[1] if len(scored) > 1 else None
        return primary, backup

    def greedy_repair(self, failed_uav=None):
        """Failed UAV dışındaki en iyi canlı adayı döndür."""
        candidates = [
            u for u in self.uav_nodes
            if self.uav_alive.get(u, False) and u != failed_uav
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda u: self._score(u))

    # ─── Rota güncelleme ──────────────────────────────────────────────────────

    def update_routes(self, force_log=False):
        prev_primary = self.current_primary
        prev_backup  = self.current_backup

        alive = [u for u in self.uav_nodes if self.uav_alive.get(u, False)]
        self._build_clusters(alive)
        primary, backup = self._choose_primary_backup(alive)

        # Failover tespiti
        if (prev_primary is not None
                and primary is not None
                and prev_primary != primary):
            record = {
                "time":        time.time(),
                "old_primary": prev_primary,
                "new_primary": primary,
                "reason":      "score_change_or_failure",
            }
            self.failover_history.append(record)
            self.logger.warning(
                "FAILOVER: %s -> %s", prev_primary, primary
            )

        self.current_primary = primary
        self.current_backup  = backup

        # Her UGV için bağımsız rota
        self.route_table = {}
        for ugv in self.ugv_nodes:
            if primary is None:
                self.route_table[ugv] = {
                    "primary": None,
                    "backup":  None,
                    "path":    f"{ugv} -> BAĞLANTI YOK",
                }
            else:
                self.route_table[ugv] = {
                    "primary": primary,
                    "backup":  backup,
                    "path":    f"{ugv} -> {primary} -> {AP_NAME}",
                }

        # OpenFlow akışlarını güncelle
        self._install_routes()

        if force_log or prev_primary != primary or prev_backup != backup:
            self._log_route_table()

    # ─── OpenFlow akış kurulumu ───────────────────────────────────────────────

    def _install_routes(self):
        """
        Güncel rota kararlarını bağlı tüm switch'lere OpenFlow olarak yaz.
        Her UGV için primary path üzerinden dedicated akış kuralı ekle.
        """
        if not self.datapaths:
            return

        for dpid, datapath in self.datapaths.items():
            ofproto = datapath.ofproto
            parser  = datapath.ofproto_parser

            for ugv, route in self.route_table.items():
                if route["primary"] is None:
                    continue

                # UGV IP'si: ugv1 -> 10.0.1.1, ugv2 -> 10.0.1.2 ...
                ugv_idx = int(ugv.replace("ugv", ""))
                ugv_ip  = f"10.0.1.{ugv_idx}"

                # Primary path akışı
                primary_port = self._get_output_port(
                    dpid, route["primary"]
                )
                if primary_port:
                    match = parser.OFPMatch(
                        eth_type=ether_types.ETH_TYPE_IP,
                        ipv4_dst=ugv_ip,
                    )
                    actions = [parser.OFPActionOutput(primary_port)]
                    self._add_flow(
                        datapath, FLOW_PRIORITY_SDN,
                        match, actions,
                        idle_timeout=FLOW_IDLE_TIMEOUT,
                        hard_timeout=FLOW_HARD_TIMEOUT,
                    )

    def _get_output_port(self, dpid, node_name):
        """
        MAC tablosundan çıkış portunu bul.
        Bilinmiyorsa None döner (flooding devreye girer).
        """
        mac_table = self.mac_to_port.get(dpid, {})
        for mac, port in mac_table.items():
            if node_name in mac:   # mac içinde node ismi geçiyorsa
                return port
        return None

    def _add_flow(self, datapath, priority, match, actions,
                  idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions
        )]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        )
        datapath.send_msg(mod)

    def _delete_all_flows(self, datapath):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=parser.OFPMatch(),
        )
        datapath.send_msg(mod)

    # ─── Enerji ve yük modeli ─────────────────────────────────────────────────

    def _update_energy(self):
        for uav in self.uav_nodes:
            if not self.uav_alive.get(uav, False):
                continue
            if uav == self.current_primary:
                drain = DRAIN_PRIMARY
            elif uav == self.current_backup:
                drain = DRAIN_BACKUP
            else:
                drain = DRAIN_IDLE

            self.energy[uav] = max(0.0, self.energy[uav] - drain)

            if self.energy[uav] <= 0.10:
                self.logger.warning(
                    "%s kritik enerji seviyesi: %.2f", uav, self.energy[uav]
                )

    def _update_load(self):
        max_pkt = max(max(self.pkt_count.values(), default=1), 1)
        for uav in self.uav_nodes:
            self.load[uav] = self.pkt_count[uav] / float(max_pkt)
        # Sayaçları yavaşça küçült
        for uav in self.uav_nodes:
            self.pkt_count[uav] = int(self.pkt_count[uav] * 0.7)

    # ─── İzleme döngüsü ───────────────────────────────────────────────────────

    def _monitor(self):
        """Ana izleme döngüsü — enerji, yük, rota güncelleme."""
        hub.sleep(3)  # başlangıçta topolojinin oturması için bekle
        while True:
            try:
                self._update_energy()
                self._update_load()
                self.update_routes()
            except Exception as e:
                self.logger.error("Monitor hatası: %s", e)
            hub.sleep(MONITOR_INTERVAL)

    # ─── Loglama ──────────────────────────────────────────────────────────────

    def _log_route_table(self):
        self.logger.info("─── ROTA TABLOSU ───────────────────────────────")
        for uav in self.uav_nodes:
            if not self.uav_alive.get(uav, False):
                continue
            self.logger.info(
                "  %s | d=%.1fm | e=%.2f | l=%.2f | skor=%.4f",
                uav,
                self._dist_to_ap(uav),
                self.energy[uav],
                self.load[uav],
                self._score(uav),
            )
        self.logger.info("  Primary: %s | Backup: %s",
                         self.current_primary, self.current_backup)
        for ugv, route in self.route_table.items():
            self.logger.info("  %s -> %s", ugv, route["path"])
        self.logger.info("  Kümeler: %s",
                         {ch: len(m) for ch, m in self.clusters.items()})
        self.logger.info("────────────────────────────────────────────────")

    # ─── OpenFlow olayları ────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath

        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        # Table-miss: controller'a gönder
        match   = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER
        )]
        self._add_flow(datapath, FLOW_PRIORITY_MISS, match, actions)

        self.logger.info("Switch bağlandı: dpid=%s", datapath.id)
        self.update_routes(force_log=True)

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if datapath is None:
            return
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        in_port  = msg.match["in_port"]
        dpid     = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        src = eth.src
        dst = eth.dst

        # MAC öğren
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Aktif primary'nin paket sayacını artır
        if self.current_primary in self.pkt_count:
            self.pkt_count[self.current_primary] += 1

        # Çıkış portu belirle
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Bilinen hedef için kısa süreli akış kur
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
            self._add_flow(
                datapath, FLOW_PRIORITY_SDN - 5,
                match, actions,
                idle_timeout=FLOW_IDLE_TIMEOUT,
                hard_timeout=FLOW_HARD_TIMEOUT,
            )

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None,
        )
        datapath.send_msg(out)
