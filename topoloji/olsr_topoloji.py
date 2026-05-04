import os
from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

def topology():
    info("*** Ağ Başlatılıyor (Geleneksel OLSR Baseline Senaryosu)\n")
    # Controller'a ihtiyacımız yok çünkü dağıtık OLSR kullanılacak
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** UAV ve UGV Düğümleri Ekleniyor\n")
    # Ad-hoc modu varsayılan olarak dağıtık ağlarda tercih edilir
    uav1 = net.addStation('uav1', ip='10.0.0.1/8', position='20,25,0', range=45)
    uav2 = net.addStation('uav2', ip='10.0.0.2/8', position='25,35,0', range=45)
    ugv1 = net.addStation('ugv1', ip='10.0.0.3/8', position='35,25,0', range=35)
    ugv2 = net.addStation('ugv2', ip='10.0.0.4/8', position='40,35,0', range=35)

    net.setPropagationModel(model="logDistance", exp=3)
    
    info("*** WiFi Düğümleri Yapılandırılıyor...\n")
    net.configureWifiNodes()

    info("*** Ağ Kuruluyor...\n")
    net.build()

    info("*** OLSR Dağıtık Yönlendirme Protokolü Başlatılıyor...\n")
    info("!!! DİKKAT: Sisteminizde 'olsrd' paketinin kurulu olması gerekmektedir.\n")
    info("!!! Komut: sudo apt-get install olsrd\n")
    
    for node in net.stations:
        # IP yönlendirmeyi aktif et (Router gibi davranmaları için)
        node.cmd('sysctl -w net.ipv4.ip_forward=1')
        
        # Her düğümün wlan interface'i üzerinden olsrd daemon'unu arka planda başlat
        # Hata durumunu loglamak yerine şimdilik null'a yönlendiriyoruz
        node.cmd(f'olsrd -i {node.name}-wlan0 -d 0 > /dev/null 2>&1 &')

    info("*** CLI başlatılıyor. 'pingall' yaparak test edebilirsiniz.\n")
    CLI(net)

    info("*** Ağ kapatılıyor ve süreçler temizleniyor...\n")
    for node in net.stations:
        node.cmd('killall olsrd')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    os.system('mn -c > /dev/null 2>&1')
    topology()
