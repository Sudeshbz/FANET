# SDN Kontrollü FANET Projesi

Bu proje, SDN tabanlı UAV-UGV haberleşmesini simüle etmek amacıyla geliştirilmiştir.

## Özellikler

- Mininet-WiFi ile mobil ağ simülasyonu
- Ryu controller ile SDN kontrolü
- Dinamik cluster head seçimi
- Multipath routing mantığı (primary + backup)

## Topoloji

- 2 UAV (uav1, uav2)
- 2 UGV (ugv1, ugv2)
- 1 Access Point
- 1 SDN Controller

## Çalıştırma

```bash
ryu-manager controller/tez_controller.py
sudo python3 topoloji/mobility_topoloji.py
