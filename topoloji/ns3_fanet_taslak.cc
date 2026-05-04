/*
 * FANET ns-3 Simülasyon Taslağı (AODV Baseline)
 * ---------------------------------------------
 * Bu dosya, TÜBİTAK Projesinde (1 Nisan - 1 Temmuz 2026 İş Paketi) 
 * istenen "ns-3 Simülasyon Ortamının Kurulumu" maddesi için
 * Mininet-WiFi SDN ortamına kıyaslama yapmak üzere hazırlanmış
 * geleneksel dağıtık (AODV kullanan) bir C++ ağ simülasyonu taslağıdır.
 * 
 * Topoloji: 2 UAV, 2 UGV
 * Yönlendirme: AODV (Ad hoc On-Demand Distance Vector)
 * Trafik: UDP / CBR
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/wifi-module.h"
#include "ns3/aodv-module.h"
#include "ns3/applications-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE ("FANETAodvSimulation");

int main (int argc, char *argv[])
{
  uint32_t uavCount = 2;
  uint32_t ugvCount = 2;
  double simulationTime = 20.0; // saniye

  CommandLine cmd;
  cmd.Parse (argc, argv);

  NodeContainer uavNodes;
  uavNodes.Create (uavCount);

  NodeContainer ugvNodes;
  ugvNodes.Create (ugvCount);

  NodeContainer allNodes;
  allNodes.Add (uavNodes);
  allNodes.Add (ugvNodes);

  // Fiziksel Katman (WiFi 802.11b - Adhoc Mod)
  YansWifiPhyHelper wifiPhy = YansWifiPhyHelper::Default ();
  YansWifiChannelHelper wifiChannel = YansWifiChannelHelper::Default ();
  wifiPhy.SetChannel (wifiChannel.Create ());

  WifiHelper wifi;
  wifi.SetStandard (WIFI_PHY_STANDARD_80211b);

  WifiMacHelper wifiMac;
  wifiMac.SetType ("ns3::AdhocWifiMac");

  NetDeviceContainer devices = wifi.Install (wifiPhy, wifiMac, allNodes);

  // Hareket (Mobility) Modeli - ConstantPositionMobilityModel
  // Sabit koordinatlar atanıyor (İstenirse GaussMarkov gibi dinamik eklenebilir)
  MobilityHelper mobility;
  Ptr<ListPositionAllocator> positionAlloc = CreateObject<ListPositionAllocator> ();
  
  // UAV Koordinatları
  positionAlloc->Add (Vector (20.0, 25.0, 10.0)); // uav1
  positionAlloc->Add (Vector (25.0, 35.0, 10.0)); // uav2
  // UGV Koordinatları
  positionAlloc->Add (Vector (35.0, 25.0, 0.0));  // ugv1
  positionAlloc->Add (Vector (40.0, 35.0, 0.0));  // ugv2

  mobility.SetPositionAllocator (positionAlloc);
  mobility.SetMobilityModel ("ns3::ConstantPositionMobilityModel");
  mobility.Install (allNodes);

  // İnternet ve AODV Yönlendirme Kurulumu
  AodvHelper aodv;
  InternetStackHelper internet;
  internet.SetRoutingHelper (aodv);
  internet.Install (allNodes);

  Ipv4AddressHelper ipv4;
  NS_LOG_INFO ("IP Adresleri Atanıyor...");
  ipv4.SetBase ("10.0.0.0", "255.0.0.0");
  Ipv4InterfaceContainer interfaces = ipv4.Assign (devices);

  // Uygulama (UDP Trafik) - UGV1'den UGV2'ye UAV'ler üzerinden
  uint16_t port = 9;
  UdpEchoServerHelper server (port);
  ApplicationContainer apps = server.Install (ugvNodes.Get (1)); // ugv2 sunucu
  apps.Start (Seconds (1.0));
  apps.Stop (Seconds (simulationTime));

  UdpEchoClientHelper client (interfaces.GetAddress (3), port); // ugv2'ye gönder
  client.SetAttribute ("MaxPackets", UintegerValue (100));
  client.SetAttribute ("Interval", TimeValue (Seconds (0.1)));
  client.SetAttribute ("PacketSize", UintegerValue (1024));

  apps = client.Install (ugvNodes.Get (0)); // ugv1 istemci
  apps.Start (Seconds (2.0));
  apps.Stop (Seconds (simulationTime));

  // Animasyon için NetAnim xml dökümü (isteğe bağlı)
  // AnimationInterface anim ("fanet_aodv_animation.xml");

  NS_LOG_INFO ("Simülasyon Başlıyor...");
  Simulator::Stop (Seconds (simulationTime));
  Simulator::Run ();
  Simulator::Destroy ();
  
  NS_LOG_INFO ("Simülasyon Tamamlandı.");

  return 0;
}
