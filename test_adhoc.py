from mn_wifi.net import Mininet_wifi
from mn_wifi.link import wmediumd, adhoc
from mn_wifi.wmediumdConnector import interference
from mininet.log import setLogLevel

setLogLevel('info')
net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
uav1 = net.addStation('uav1', position='10,10,0')
print("wintfs of uav1:", uav1.wintfs)
try:
    net.addLink(uav1, cls=adhoc, intf=uav1.wintfs[0], ssid='test-adhoc', mode='g', channel=5)
    print("Success with wintfs[0]")
except Exception as e:
    print("Error:", e)
