from math import sqrt

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet


class TezController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TezController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}

        # Tez mantigi icin ornek konumlar
        self.positions = {
            "ap1":  (30.0, 30.0, 0.0),
            "uav1": (20.0, 25.0, 0.0),
            "uav2": (25.0, 35.0, 0.0),
            "ugv1": (35.0, 25.0, 0.0),
            "ugv2": (40.0, 35.0, 0.0),
        }

        # UAV durumlari
        self.uav_status = {
            "uav1": True,
            "uav2": True,
        }

        # UGV -> secili yol bilgisi
        self.route_table = {}

        self.logger.info("TezController basladi")

    def distance(self, p1, p2):
        x1, y1, z1 = p1
        x2, y2, z2 = p2
        return sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)

    def choose_primary_backup_uav(self):
        ap_pos = self.positions["ap1"]
        d1 = self.distance(self.positions["uav1"], ap_pos)
        d2 = self.distance(self.positions["uav2"], ap_pos)

        if d1 <= d2:
            return "uav1", "uav2", d1, d2
        return "uav2", "uav1", d1, d2

    def update_routes(self):
        primary, backup, d1, d2 = self.choose_primary_backup_uav()

        # Primary down ise backup aktif olsun
        active_primary = primary if self.uav_status.get(primary, False) else backup

        # Backup da down ise yine primary yazalim ama alarm basalım
        if not self.uav_status.get(primary, False) and not self.uav_status.get(backup, False):
            self.logger.warning("Hem primary hem backup UAV down gorunuyor")
            active_primary = primary

        self.route_table = {
            "ugv1": {
                "primary": active_primary,
                "backup": backup if active_primary == primary else primary
            },
            "ugv2": {
                "primary": active_primary,
                "backup": backup if active_primary == primary else primary
            }
        }

        self.logger.info("=== ROUTE GUNCELLEME ===")
        self.logger.info("uav1->ap1 uzaklik: %.2f", d1)
        self.logger.info("uav2->ap1 uzaklik: %.2f", d2)
        self.logger.info("UAV durumlari: %s", self.uav_status)
        for ugv, route in self.route_table.items():
            self.logger.info(
                "%s primary=%s backup=%s",
                ugv, route["primary"], route["backup"]
            )

    def set_uav_status(self, uav_name, status):
        if uav_name in self.uav_status:
            self.uav_status[uav_name] = status
            self.logger.warning("Durum degisti: %s -> %s", uav_name, status)
            self.update_routes()

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

        self.logger.info("Switch baglandi: dpid=%s", datapath.id)
        self.update_routes()

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

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst
        )
        datapath.send_msg(mod)

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

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Basit learning switch davranisi
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        # Tez mantigi icin route loglari
        self.log_route_decision(src, dst)

        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self.add_flow(datapath, 1, match, actions)

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        )
        datapath.send_msg(out)

    def log_route_decision(self, src_mac, dst_mac):
        # Bu kisim MAC yerine kavramsal route logu veriyor
        for ugv in ["ugv1", "ugv2"]:
            if ugv in self.route_table:
                route = self.route_table[ugv]
                self.logger.info(
                    "Karar: %s icin aktif yol = %s -> %s -> ap1 | yedek = %s",
                    ugv,
                    ugv,
                    route["primary"],
                    route["backup"]
                )

    # Manuel test icin kullanabilecegin yardimci fonksiyonlar:
    # Ryu calisirken kodu degistirip tekrar baslatip bunlari __init__ icinde cagirabilirsin
    # self.set_uav_status("uav2", False)
    # self.set_uav_status("uav2", True)
