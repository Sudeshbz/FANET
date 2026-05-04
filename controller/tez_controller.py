from math import sqrt
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.lib import hub


class TezController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TezController, self).__init__(*args, **kwargs)

        # Learning switch tabloları
        self.mac_to_port = {}
        self.datapaths = {}

        # UGV listesi
        self.ugv_nodes = ["ugv1", "ugv2"]
        self.uav_nodes = [f"uav{i}" for i in range(1, 7)]

        # Topoloji konumları ve diğer değişkenleri dinamik ilklendir
        self.positions = {
            "ap1":  (30.0, 30.0, 0.0),
            "ugv1": (35.0, 25.0, 0.0),
            "ugv2": (40.0, 35.0, 0.0),
        }
        self.max_link_distance = {}
        self.uav_status = {}
        self.energy = {}
        self.load = {}
        self.packet_count = {}

        for uav in self.uav_nodes:
            self.positions[uav] = (999.0, 999.0, 0.0) # Uzakta başlat
            self.max_link_distance[uav] = 60.0
            self.uav_status[uav] = False
            self.energy[uav] = 1.0
            self.load[uav] = 0.0
            self.packet_count[uav] = 0

        # Route tablosu
        self.route_table = {}

        # Son seçilen primary/backup
        self.current_primary = None
        self.current_backup = None

        # Failover kayıtları
        self.failover_history = []

        # Ağ karar ağırlıkları
        self.weights = {
            "distance": 0.45,
            "energy": 0.35,
            "load": 0.20,
        }

        # Periyodik izleme thread’i
        self.monitor_thread = hub.spawn(self._monitor)

        self.logger.info("TezController basladi")
        self.update_routes(force_log=True)

    # ---------------------------------------------------
    # Temel yardımcı fonksiyonlar
    # ---------------------------------------------------

    def distance(self, p1, p2):
        x1, y1, z1 = p1
        x2, y2, z2 = p2
        return sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)

    def normalize(self, value, max_value):
        if max_value <= 0:
            return 0.0
        return min(max(value / max_value, 0.0), 1.0)

    def get_uav_distance_to_ap(self, uav_name):
        return self.distance(self.positions[uav_name], self.positions["ap1"])

    def is_uav_reachable(self, uav_name):
        d = self.get_uav_distance_to_ap(uav_name)
        return d <= self.max_link_distance[uav_name]

    def compute_uav_score(self, uav_name):
        """
        Küçük skor daha iyi.
        distance düşük olsun
        energy yüksek olsun
        load düşük olsun
        """
        d = self.get_uav_distance_to_ap(uav_name)
        d_norm = self.normalize(d, self.max_link_distance[uav_name])

        e = self.energy[uav_name]
        e_penalty = 1.0 - e

        l = min(max(self.load[uav_name], 0.0), 1.0)

        score = (
            self.weights["distance"] * d_norm +
            self.weights["energy"] * e_penalty +
            self.weights["load"] * l
        )
        return score

    # ---------------------------------------------------
    # Otomatik durum izleme
    # ---------------------------------------------------

    def _monitor(self):
        while True:
            try:
                self.auto_update_uav_status()
                self.update_energy_model()
                self.update_load_model()
                self.update_routes()
            except Exception as e:
                self.logger.error("Monitor thread hatasi: %s", e)

            hub.sleep(2)

    def auto_update_uav_status(self):
        """
        Basit otomatik durum kontrolü:
        UAV, AP menzilinin dışına çıktıysa down kabul edilir.
        """
        changed = False

        for uav in self.uav_nodes:
            reachable = self.is_uav_reachable(uav)
            if self.uav_status[uav] != reachable:
                self.uav_status[uav] = reachable
                changed = True
                self.logger.warning("Durum degisti: %s -> %s", uav, reachable)

        if changed:
            self.logger.warning("Otomatik UAV durum guncellemesi uygulandi")

    def update_energy_model(self):
        """
        Basit enerji modeli:
        primary UAV daha hızlı enerji kaybetsin,
        backup daha yavaş kaybetsin.
        """
        if not self.route_table:
            return

        primary = self.current_primary
        backup = self.current_backup

        for uav in self.uav_nodes:
            if not self.uav_status[uav]:
                continue

            drain = 0.002
            if uav == primary:
                drain = 0.008
            elif uav == backup:
                drain = 0.004

            self.energy[uav] = max(0.0, self.energy[uav] - drain)

            if self.energy[uav] <= 0.10 and self.uav_status[uav]:
                self.logger.warning("%s kritik enerji seviyesine indi: %.2f", uav, self.energy[uav])

    def update_load_model(self):
        """
        Paket sayımlarına göre kaba bir load hesapla.
        """
        max_packets = max(max(self.packet_count.values()), 1)

        for uav in self.uav_nodes:
            self.load[uav] = self.packet_count[uav] / float(max_packets)

        # Sayaçları çok büyütmemek için arada küçült
        for uav in self.uav_nodes:
            self.packet_count[uav] = int(self.packet_count[uav] * 0.7)

    # ---------------------------------------------------
    # Routing karar mantığı
    # ---------------------------------------------------

    def choose_primary_backup_uav(self):
        alive_uavs = [uav for uav in self.uav_nodes if self.uav_status.get(uav, False)]

        if not alive_uavs:
            return None, None

        scored = []
        for uav in alive_uavs:
            scored.append((self.compute_uav_score(uav), uav))

        scored.sort(key=lambda x: x[0])

        primary = scored[0][1]
        backup = scored[1][1] if len(scored) > 1 else None
        return primary, backup

    def greedy_repair(self, failed_uav=None):
        """
        Failed UAV dışındaki en iyi canlı UAV'yi döndür.
        Bu basit haliyle greedy repair gibi davranır.
        """
        candidates = [
            uav for uav in self.uav_nodes
            if self.uav_status.get(uav, False) and uav != failed_uav
        ]

        if not candidates:
            return None

        best = min(candidates, key=lambda u: self.compute_uav_score(u))
        return best

    def update_routes(self, force_log=False):
        previous_primary = self.current_primary
        previous_backup = self.current_backup

        primary, backup = self.choose_primary_backup_uav()

        # Hiç UAV ayakta değilse
        if primary is None:
            self.route_table = {
                ugv: {
                    "primary": None,
                    "backup": None,
                    "path": f"{ugv} -> BAGLANTI YOK"
                }
                for ugv in self.ugv_nodes
            }
            self.current_primary = None
            self.current_backup = None

            self.logger.warning("Tum UAV'ler devre disi. Baglanti yok.")
            self.log_route_table()
            return

        # Primary yok olduysa greedy repair
        if previous_primary is not None and previous_primary != primary:
            self.failover_history.append({
                "time": time.time(),
                "old_primary": previous_primary,
                "new_primary": primary
            })
            self.logger.warning(
                "FAILOVER: primary degisti -> eski=%s yeni=%s",
                previous_primary,
                primary
            )

        self.current_primary = primary
        self.current_backup = backup

        self.route_table = {}
        for ugv in self.ugv_nodes:
            path = f"{ugv} -> {primary} -> ap1"
            backup_name = backup

            if primary is None:
                path = f"{ugv} -> BAGLANTI YOK"

            self.route_table[ugv] = {
                "primary": primary,
                "backup": backup_name,
                "path": path
            }

        if force_log or previous_primary != self.current_primary or previous_backup != self.current_backup:
            self.log_route_table()

    def log_route_table(self):
        self.logger.info("=== ROUTE GUNCELLEME ===")
        for uav in self.uav_nodes:
            self.logger.info(
                "%s | uzaklik=%.2f | enerji=%.2f | yuk=%.2f | durum=%s | skor=%.4f",
                uav,
                self.get_uav_distance_to_ap(uav),
                self.energy[uav],
                self.load[uav],
                self.uav_status[uav],
                self.compute_uav_score(uav) if self.uav_status[uav] else 999.0
            )

        for ugv, route in self.route_table.items():
            self.logger.info(
                "%s primary=%s backup=%s path=%s",
                ugv,
                route["primary"],
                route["backup"],
                route["path"]
            )

    def set_uav_status(self, uav_name, status):
        """
        Manuel test için kullanılabilir.
        Örnek:
        self.set_uav_status("uav2", False)
        """
        if uav_name in self.uav_status:
            old = self.uav_status[uav_name]
            self.uav_status[uav_name] = status
            self.logger.warning("Manuel durum degisti: %s %s -> %s", uav_name, old, status)

            if not status and self.current_primary == uav_name:
                repaired = self.greedy_repair(failed_uav=uav_name)
                self.logger.warning("Greedy repair secimi: failed=%s repaired=%s", uav_name, repaired)

            self.update_routes(force_log=True)

    # ---------------------------------------------------
    # OpenFlow yardımcıları
    # ---------------------------------------------------

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout
        )
        datapath.send_msg(mod)

    def delete_flows(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=parser.OFPMatch()
        )
        datapath.send_msg(mod)

    # ---------------------------------------------------
    # Switch lifecycle
    # ---------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Table-miss: controller'a gönder
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)
        ]
        self.add_flow(datapath, 0, match, actions)

        self.logger.info("Switch baglandi: dpid=%s", datapath.id)
        self.update_routes(force_log=True)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if datapath is None:
            return

        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                del self.datapaths[datapath.id]

    # ---------------------------------------------------
    # Paket işleme
    # ---------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        # Broadcast / LLDP / boş paketleri filtrelemek istersen burada genişletebilirsin
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Route kararını logla
        self.log_route_decision(src, dst)

        # Paket sayımına göre aktif primary UAV yükünü artır
        if self.current_primary in self.packet_count:
            self.packet_count[self.current_primary] += 1

        # Şimdilik forwarding tabanı learning switch.
        # Böylece ağ çalışır kalır, controller ise akıllı routing kararlarını üretir.
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Geçici akış ekle
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst)
            self.add_flow(
                datapath=datapath,
                priority=1,
                match=match,
                actions=actions,
                idle_timeout=10,
                hard_timeout=30
            )

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        )
        datapath.send_msg(out)

    # ---------------------------------------------------
    # Loglama
    # ---------------------------------------------------

    def log_route_decision(self, src_mac, dst_mac):
        self.logger.info(
            "Paket karari | src=%s dst=%s | aktif_primary=%s backup=%s",
            src_mac,
            dst_mac,
            self.current_primary,
            self.current_backup
        )

        for ugv in self.ugv_nodes:
            if ugv in self.route_table:
                route = self.route_table[ugv]
                self.logger.info(
                    "Karar: %s icin aktif yol = %s | yedek = %s",
                    ugv,
                    route["path"],
                    route["backup"]
                )

    # ---------------------------------------------------
    # Test / demo yardımcıları
    # ---------------------------------------------------

    def move_node(self, node_name, new_position):
        if node_name in self.positions:
            self.positions[node_name] = new_position
            self.logger.warning("Node tasindi: %s -> %s", node_name, new_position)
            self.update_routes(force_log=True)

    def set_energy(self, uav_name, value):
        if uav_name in self.energy:
            self.energy[uav_name] = min(max(value, 0.0), 1.0)
            self.logger.warning("Enerji guncellendi: %s -> %.2f", uav_name, self.energy[uav_name])
            self.update_routes(force_log=True)