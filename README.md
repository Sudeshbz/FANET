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

## 1. Standart Simülasyonu Çalıştırma

Projenin temel ağ topolojisini ayağa kaldırmak için iki ayrı terminale ihtiyacınız vardır.

**Terminal 1 (Ryu SDN Kontrolcüsü):**
```bash
cd ~/Desktop/FANET
source ~/ryu-env/bin/activate
ryu-manager controller/tez_controller.py
```

**Terminal 2 (Mininet-WiFi Topolojisi):**
```bash
cd ~/Desktop/FANET
sudo python3 topoloji/mobility_topoloji.py
```

## 2. Otomatik Performans Testleri (2, 4, 6 UAV)

Sistemin ölçeklenebilirliğini test etmek ve Gecikme, Paket Kaybı, Bant Genişliği verilerini (CSV olarak) çıkarmak için aşağıdaki testi çalıştırın (bu script arka planda SDN kontrolcüsünü kendi başına yönetir).

```bash
cd ~/Desktop/FANET
source ~/ryu-env/bin/activate
sudo -E python3 tests/performance_test.py
```
*(İşlem sonucunda klasörde `performance_results.csv` dosyası oluşacaktır.)*

## 3. Karşılaştırma Grafiklerini Oluşturma

Performans testleri tamamlandıktan sonra, çıkan sonuçları rapor formatında görselleştirmek için:

```bash
# Eğer kurulu değilse sadece bir defaya mahsus kütüphaneyi kurun:
# pip install matplotlib

cd ~/Desktop/FANET
source ~/ryu-env/bin/activate
python3 tests/generate_graphs.py
```
*(Grafikler `tests/` klasörünün içerisine kaydedilecektir.)*
