from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

def topology():
    net = Mininet_wifi(controller=RemoteController,
                       link=wmediumd,
                       wmediumd_mode=interference,
                       switch=OVSKernelSwitch)

    info("*** Controller ekleniyor\n")
    c0 = net.addController('c0', controller=RemoteController,
                           ip='127.0.0.1', port=6653)

    info("*** UAV ve UGV düğümleri ekleniyor\n")

    # UAV (Drone) düğümler
    uav1 = net.addStation('uav1', ip='10.0.0.1/8', position='10,20,10')
    uav2 = net.addStation('uav2', ip='10.0.0.2/8', position='20,40,10')

    # UGV (Yer aracı) düğümler
    ugv1 = net.addStation('ugv1', ip='10.0.0.3/8', position='40,20,0')
    ugv2 = net.addStation('ugv2', ip='10.0.0.4/8', position='50,35,0')

    # Access Point
    ap1 = net.addAccessPoint('ap1', ssid='tez-ag', mode='g',
                             channel='1', position='30,30,0')

    info("*** WiFi ayarları\n")
    net.setPropagationModel(model="logDistance", exp=4)
    net.configureWifiNodes()

    info("*** Ağ başlatılıyor\n")
    net.build()
    c0.start()
    ap1.start([c0])

    info("*** CLI başlatılıyor\n")
    CLI(net)

    info("*** Ağ kapatılıyor\n")
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    topology()
