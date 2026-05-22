# FANET - SDN Kontrollü Çok Yollu Kümeleme Tabanlı FANET Sistemi

> TÜBİTAK 2209-A Üniversite Öğrencileri Araştırma Projesi  
> Yalova Üniversitesi — Bilgisayar Mühendisliği

## Proje Hakkında

Bu proje, SDN (Software Defined Networking) mimarisi kullanarak UAV (İnsansız Hava Aracı) ve UGV (İnsansız Kara Aracı) düğümlerinden oluşan FANET (Flying Ad-hoc Network) ağlarında güvenilir ve enerji verimli iletişim sağlamayı hedeflemektedir.

**10 UAV + 5 UGV** ile çalışan sistem; dinamik kümeleme, çok yollu yönlendirme (LB-MCR) ve otomatik failover mekanizmaları içermektedir.

---

## Sistem Mimarisi

```
[fanet_topoloji.py]  →  [konum_yayinci.py]  →  [fanet_controller.py]
  Mininet-WiFi            REST API Köprüsü        Ryu SDN Kontrolcüsü
  10 UAV + 5 UGV          Flask / HTTP             OpenFlow 1.3
  VXLAN Tünelleme         Konum Senkronizasyonu    Kümeleme + Failover
```

Karşılaştırma için ns-3 ile AODV ve OLSR baseline simülasyonları da mevcuttur.

---

## Gereksinimler

```bash
# Python paketleri
pip install flask requests pandas matplotlib numpy

# Sistem paketleri
sudo apt install iperf3 -y

# Ryu SDN framework (Python 3.10 ile eventlet düzeltmesi gerekir)
pip install eventlet==0.30.2
pip install ryu

# ns-3.41 (AODV/OLSR baseline için)
# https://www.nsnam.org/releases/ns-3-41/
```

---

## Çalıştırma — SDN Sistemi

> **Sıra önemlidir!** Üç terminal açılmalı ve sırasıyla başlatılmalıdır.

### Terminal 1 — Konum Yayıncısı (önce başlat)

```bash
cd ~/fanet_proje
source ~/fanet-env/bin/activate
python3 konum_yayinci.py
```

Beklenen çıktı:
```
* Running on http://127.0.0.1:8080
```

### Terminal 2 — SDN Kontrolcüsü (ikinci başlat)

```bash
cd ~/fanet_proje
source ~/fanet-env/bin/activate
ryu-manager fanet_controller.py
```

Beklenen çıktı:
```
FanetController başlatıldı — 10 UAV, 5 UGV
Switch bağlandı: dpid=...
UAV durum değişti: uav1 -> CANLI
```

### Terminal 3 — Topoloji (en son başlat, sudo gerektirir)

```bash
cd ~/fanet_proje
sudo python3 fanet_topoloji.py
```

Beklenen çıktı:
```
*** 10 UAV ekleniyor
*** 5 UGV ekleniyor
[VXLAN] Tünel hazır
[Mobilite] Thread başladı
```

---

## Çalıştırma — ns-3 Baseline Simülasyonları

```bash
# Dosyaları kopyala
cp ns3_aodv.cc ~/ns-3/scratch/
cp ns3_olsr.cc ~/ns-3/scratch/

cd ~/ns-3

# AODV baseline
./ns3 run scratch/ns3_aodv

# OLSR baseline
./ns3 run scratch/ns3_olsr

# Sonuçları proje klasörüne taşı
cp ~/ns-3/fanet_aodv_results.csv ~/fanet_proje/
cp ~/ns-3/fanet_olsr_results.csv ~/fanet_proje/
```

---

## Karşılaştırmalı Analiz

```bash
cd ~/fanet_proje
source ~/fanet-env/bin/activate
python3 metrik_toplayici.py
```

Çıktılar:
- `fanet_karsilastirma.csv` — birleşik metrik tablosu
- `fanet_rapor.png` — karşılaştırma grafiği
- `fanet_rapor.html` — interaktif HTML raporu

---

## Dosya Yapısı

```
fanet_proje/
├── fanet_controller.py     # Ryu SDN kontrolcüsü
├── fanet_topoloji.py       # Mininet-WiFi topolojisi (10 UAV + 5 UGV)
├── konum_yayinci.py        # REST API konum köprüsü
├── metrik_toplayici.py     # Karşılaştırmalı analiz
├── ns3_aodv.cc             # ns-3 AODV baseline
└── ns3_olsr.cc             # ns-3 OLSR baseline
```

---

## Bilinen Sorunlar ve Çözüm Süreci

### rx=0 Sorunu (ns-3 AODV/OLSR)

ns-3 simülasyonlarında UGV'lerden AP'ye gönderilen paketlerin alıcıda `rx=0` olarak görünmesi sorunu yaşanmaktadır.

**Denenen çözümler:**
- TX gücü 20 dBm → 40 dBm artırıldı
- `UdpServerHelper` → `UdpEchoServerHelper` ile değiştirildi
- Rastgele mobilite kaldırılarak sabit çember topolojisi denendi (UAV'lar AP'ye 60m, UGV'ler 110m uzaklıkta)
- `RandomWaypoint` mobility modeli yorum satırına alındı
- Propagasyon modeli yumuşatıldı (exp=3.0 → 2.5, refLoss=46.6 → 40.0)
- 2 düğümlü minimal test ile AODV'nin çalıştığı doğrulandı
- FlowMonitor yerine direkt socket sayacı denendi

**Mevcut durum:** Sorun araştırılmaya devam etmektedir. SDN sistemi (Terminal 1-2-3) tam çalışır durumda olup gerçek metrikler alınmaktadır.

---

## Teknik Detaylar

| Bileşen | Teknoloji |
|---|---|
| SDN Kontrolcüsü | Ryu 4.34 + OpenFlow 1.3 |
| Ağ Emülatörü | Mininet-WiFi 2.7 |
| Sanal Switch | OVS (Open vSwitch) 8.3 |
| Tünel | VXLAN (VNI=100) |
| Konum API | Flask REST |
| Baseline Simülatör | ns-3.41 |
| İşletim Sistemi | Ubuntu 22.04 (Python 3.10) |

---
