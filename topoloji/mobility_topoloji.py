from math import sqrt

from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference


def parse_position_value(pos):
    if pos is None:
        return None

    if isinstance(pos, str):
        x, y, z = pos.split(',')
        return float(x), float(y), float(z)

    if isinstance(pos, (list, tuple)) and len(pos) >= 3:
        return float(pos[0]), float(pos[1]), float(pos[2])

    return None


def get_node_position(node):
    pos = node.params.get('position')
    parsed = parse_position_value(pos)
    if parsed is not None:
        return parsed

    pos = node.params.get('pos')
    parsed = parse_position_value(pos)
    if parsed is not None:
        return parsed

    if hasattr(node, 'position'):
        parsed = parse_position_value(node.position)
        if parsed is not None:
            return parsed

    x = node.params.get('x')
    y = node.params.get('y')
    z = node.params.get('z', 0)
    if x is not None and y is not None:
        return float(x), float(y), float(z)

    raise ValueError(f"{node.name} icin konum bilgisi bulunamadi")


def distance(pos1, pos2):
    x1, y1, z1 = pos1
    x2, y2, z2 = pos2
    return sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)


def choose_cluster_head(net):
    ap1 = net.get('ap1')
    uav1 = net.get('uav1')
    uav2 = net.get('uav2')

    ap_pos = get_node_position(ap1)
    uav1_pos = get_node_position(uav1)
    uav2_pos = get_node_position(uav2)

    d1 = distance(uav1_pos, ap_pos)
    d2 = distance(uav2_pos, ap_pos)

    if d1 <= d2:
        return 'uav1', 'uav2', d1, d2
    return 'uav2', 'uav1', d1, d2


def print_clusters(net):
    print("\n=== DINAMIK CLUSTER BILGISI ===")

    cluster_head, backup_uav, d1, d2 = choose_cluster_head(net)

    print("AP'ye uzakliklar:")
    print(f"uav1 -> ap1: {d1:.2f}")
    print(f"uav2 -> ap1: {d2:.2f}")
    print(f"\nSecilen Cluster Head: {cluster_head}")
    print(f"Yedek UAV: {backup_uav}")

    for ugv in ['ugv1', 'ugv2']:
        print(f"{ugv} -> {cluster_head}")


def print_multipath_info(net):
    print("\n=== MULTIPATH BILGISI ===")

    cluster_head, backup_uav, d1, d2 = choose_cluster_head(net)

    for ugv in ['ugv1', 'ugv2']:
        primary_path = f"{ugv} -> {cluster_head} -> ap1"
        backup_path = f"{ugv} -> {backup_uav} -> ap1"

        print(f"\n{ugv} icin:")
        print(f"Primary Path : {primary_path}")
        print(f"Backup Path  : {backup_path}")


def topology():
    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
        switch=OVSKernelSwitch,
        autoAssociation=True
    )

    info("*** Controller ekleniyor\n")
    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6653
    )

    info("*** UAV ve UGV dugumleri ekleniyor\n")
    net.addStation('uav1', ip='10.0.0.1/8', position='20,25,0', range=45)
    net.addStation('uav2', ip='10.0.0.2/8', position='25,35,0', range=45)
    net.addStation('ugv1', ip='10.0.0.3/8', position='35,25,0', range=35)
    net.addStation('ugv2', ip='10.0.0.4/8', position='40,35,0', range=35)

    net.addAccessPoint(
        'ap1',
        ssid='tez-ag',
        mode='g',
        channel='1',
        position='30,30,0',
        range=60
    )

    info("*** Propagation model ayarlaniyor\n")
    net.setPropagationModel(model="logDistance", exp=3)

    info("*** WiFi dugumleri yapilandiriliyor\n")
    net.configureWifiNodes()

    info("*** Ag baslatiliyor\n")
    net.build()
    c0.start()
    net.get('ap1').start([c0])

    uav1 = net.get('uav1')
    uav2 = net.get('uav2')
    ugv1 = net.get('ugv1')
    ugv2 = net.get('ugv2')

    info("*** Mobilite baslatiliyor\n")
    net.startMobility(time=0)

    net.mobility(uav1, 'start', time=1, position='20,25,0')
    net.mobility(uav1, 'stop',  time=20, position='35,40,0')

    net.mobility(uav2, 'start', time=1, position='25,35,0')
    net.mobility(uav2, 'stop',  time=20, position='40,25,0')

    net.mobility(ugv1, 'start', time=2, position='35,25,0')
    net.mobility(ugv1, 'stop',  time=20, position='25,40,0')

    net.mobility(ugv2, 'start', time=2, position='40,35,0')
    net.mobility(ugv2, 'stop',  time=20, position='45,30,0')

    net.stopMobility(time=21)

    info("*** Dinamik cluster bilgisi yazdiriliyor\n")
    print_clusters(net)

    info("*** Multipath bilgisi yazdiriliyor\n")
    print_multipath_info(net)

    info("*** CLI baslatiliyor\n")
    CLI(net)

    info("*** Ag kapatiliyor\n")
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    topology()
