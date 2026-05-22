/*
 * ns3_aodv.cc
 * -----------
 * FANET AODV Baseline Senaryosu
 *
 * Özellikler:
 *   - 10 UAV + 5 UGV, hareketli (RandomWaypoint mobility)
 *   - AODV dağıtık yönlendirme
 *   - UDP/CBR trafik: her UGV'den AP'ye
 *   - Metrik toplama: throughput, gecikme, paket kaybı, enerji
 *   - Sonuçlar CSV'ye yazılır (fanet_aodv_results.csv)
 *   - NetAnim için XML çıktısı (opsiyonel)
 *
 * Derleme:
 *   cp ns3_aodv.cc <ns3-dizini>/scratch/
 *   cd <ns3-dizini>
 *   ./ns3 run scratch/ns3_aodv
 *
 * Bağımlılıklar:
 *   ns-3.38+ (aodv, wifi, mobility, applications modülleri)
 */

/*
 * ns3_aodv.cc - Düzeltilmiş FANET AODV Baseline
 * UGV -> UAV relay -> AP topolojisi
 * ns-3.41 uyumlu
 */

#include <fstream>
#include <string>
#include <vector>
#include <iomanip>

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/wifi-module.h"
#include "ns3/aodv-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/energy-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("FanetAodvBaseline");

static const uint32_t UAV_COUNT    = 10;
static const uint32_t UGV_COUNT    = 5;
static const double   SIM_TIME     = 120.0;
static const double   AREA_X       = 200.0;
static const double   AREA_Y       = 200.0;
static const double   UAV_HEIGHT   = 30.0;
static const double   UAV_SPEED_MIN = 1.0;
static const double   UAV_SPEED_MAX = 10.0;
static const double   UGV_SPEED_MIN = 0.5;
static const double   UGV_SPEED_MAX = 3.0;
static const double   TX_POWER_DBM  = 40.0;
static const uint32_t PACKET_SIZE   = 1024;
static const double   PACKET_INTERVAL = 0.2;
static const uint16_t UDP_PORT      = 9000;
static const std::string OUTPUT_CSV = "fanet_aodv_results.csv";

struct NodeMetrics {
    std::string name;
    uint64_t txPackets   = 0;
    uint64_t rxPackets   = 0;
    double   txBytes     = 0;
    double   rxBytes     = 0;
    double   delay_sum   = 0;
    double   jitter_sum  = 0;
    double   energy_j    = 0;
    double   lostPackets = 0;
};

class EnergyMonitor {
public:
    std::map<uint32_t, double> initial_energy;
    std::map<uint32_t, double> final_energy;

    void RecordInitial(EnergySourceContainer sources) {
        for (uint32_t i = 0; i < sources.GetN(); i++) {
            Ptr<BasicEnergySource> src =
                DynamicCast<BasicEnergySource>(sources.Get(i));
            if (src) initial_energy[i] = src->GetInitialEnergy();
        }
    }
    void RecordFinal(EnergySourceContainer sources) {
        for (uint32_t i = 0; i < sources.GetN(); i++) {
            Ptr<BasicEnergySource> src =
                DynamicCast<BasicEnergySource>(sources.Get(i));
            if (src) final_energy[i] = src->GetRemainingEnergy();
        }
    }
    double GetConsumed(uint32_t idx) {
        return initial_energy.count(idx) ? initial_energy[idx] - final_energy[idx] : 0;
    }
};

void WriteCSV(const std::vector<NodeMetrics>& metrics, double sim_time)
{
    std::ofstream csv(OUTPUT_CSV);
    csv << std::fixed << std::setprecision(4);
    csv << "protocol,node,tx_packets,rx_packets,lost_packets,"
        << "packet_loss_pct,throughput_kbps,avg_delay_ms,"
        << "avg_jitter_ms,energy_joule\n";

    for (const auto& m : metrics) {
        double loss_pct    = (m.txPackets > 0) ? 100.0 * m.lostPackets / m.txPackets : 0.0;
        double tput_kbps   = (sim_time > 0) ? (m.rxBytes * 8.0) / (sim_time * 1000.0) : 0.0;
        double avg_delay   = (m.rxPackets > 0) ? (m.delay_sum / m.rxPackets) * 1000.0 : 0.0;
        double avg_jitter  = (m.rxPackets > 1) ? (m.jitter_sum / (m.rxPackets-1)) * 1000.0 : 0.0;

        csv << "AODV," << m.name << ","
            << m.txPackets << "," << m.rxPackets << "," << m.lostPackets << ","
            << loss_pct << "," << tput_kbps << "," << avg_delay << ","
            << avg_jitter << "," << m.energy_j << "\n";
    }
    csv.close();
    NS_LOG_UNCOND("Sonuçlar yazıldı: " << OUTPUT_CSV);
}

int main(int argc, char* argv[])
{
    uint32_t uavCount = UAV_COUNT;
    uint32_t ugvCount = UGV_COUNT;
    double   simTime  = SIM_TIME;

    CommandLine cmd;
    cmd.AddValue("uavCount", "UAV sayisi", uavCount);
    cmd.AddValue("ugvCount", "UGV sayisi", ugvCount);
    cmd.AddValue("simTime",  "Simulasyon suresi", simTime);
    cmd.Parse(argc, argv);

    NS_LOG_UNCOND("=== FANET AODV Baseline ===");
    NS_LOG_UNCOND("UAV: " << uavCount << " | UGV: " << ugvCount << " | Sure: " << simTime << "s");

    // Düğümler: UAV + UGV + 1 AP
    NodeContainer uavNodes, ugvNodes, apNode;
    uavNodes.Create(uavCount);
    ugvNodes.Create(ugvCount);
    apNode.Create(1);

    NodeContainer allNodes;
    allNodes.Add(uavNodes);
    allNodes.Add(ugvNodes);
    allNodes.Add(apNode);

    // WiFi — adhoc mod, tüm düğümler aynı ağda
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211g);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
        "DataMode",    StringValue("ErpOfdmRate24Mbps"),
        "ControlMode", StringValue("ErpOfdmRate6Mbps"));

    YansWifiPhyHelper phy;
    YansWifiChannelHelper channel = YansWifiChannelHelper::Default();
    channel.AddPropagationLoss("ns3::LogDistancePropagationLossModel",
        "Exponent",      DoubleValue(2.5),
        "ReferenceLoss", DoubleValue(40.0));
    phy.SetChannel(channel.Create());
    phy.Set("TxPowerStart", DoubleValue(TX_POWER_DBM));
    phy.Set("TxPowerEnd",   DoubleValue(TX_POWER_DBM));

    WifiMacHelper mac;
    mac.SetType("ns3::AdhocWifiMac");
    NetDeviceContainer devices = wifi.Install(phy, mac, allNodes);

    // Mobilite
    MobilityHelper mobility;
    /*
    // UAV — rastgele waypoint, 3D
    mobility.SetPositionAllocator(
        "ns3::RandomBoxPositionAllocator",
        "X", StringValue("ns3::UniformRandomVariable[Min=20|Max=180]"),
        "Y", StringValue("ns3::UniformRandomVariable[Min=20|Max=180]"),
        "Z", StringValue("ns3::ConstantRandomVariable[Constant=30]")
    );
    mobility.SetMobilityModel(
        "ns3::RandomWaypointMobilityModel",
        "Speed", StringValue("ns3::UniformRandomVariable[Min=1|Max=10]"),
        "Pause", StringValue("ns3::ConstantRandomVariable[Constant=1]"),
        "PositionAllocator", StringValue("ns3::RandomBoxPositionAllocator")
    );
    mobility.Install(uavNodes);

    // UGV — yavaş, zemin
    mobility.SetPositionAllocator(
        "ns3::RandomBoxPositionAllocator",
        "X", StringValue("ns3::UniformRandomVariable[Min=20|Max=180]"),
        "Y", StringValue("ns3::UniformRandomVariable[Min=20|Max=180]"),
        "Z", StringValue("ns3::ConstantRandomVariable[Constant=0]")
    );
    mobility.SetMobilityModel(
        "ns3::RandomWaypointMobilityModel",
        "Speed", StringValue("ns3::UniformRandomVariable[Min=0.5|Max=3]"),
        "Pause", StringValue("ns3::ConstantRandomVariable[Constant=2]"),
        "PositionAllocator", StringValue("ns3::RandomBoxPositionAllocator")
    );
    mobility.Install(ugvNodes);
    */
    /*
    // AP — merkez sabit
    Ptr<ListPositionAllocator> apPos = CreateObject<ListPositionAllocator>();
    apPos->Add(Vector(AREA_X/2, AREA_Y/2, 0.0));
    mobility.SetPositionAllocator(apPos);
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(apNode);
    */
    // Tüm düğümler grid'e yerleştir
    Ptr<ListPositionAllocator> posAlloc = CreateObject<ListPositionAllocator>();

    // UAV'lar önce (allNodes'ta ilk sırada)
    for (uint32_t i = 0; i < uavCount; i++) {
        double angle = i * 2 * M_PI / uavCount;
        posAlloc->Add(Vector(100 + 60*cos(angle), 100 + 60*sin(angle), 0));
    }

    // UGV'ler sonra
    for (uint32_t i = 0; i < ugvCount; i++) {
        double angle = i * 2 * M_PI / ugvCount;
        posAlloc->Add(Vector(100 + 110*cos(angle), 100 + 110*sin(angle), 0));
    }

    // AP en son (merkez)
    posAlloc->Add(Vector(100, 100, 0));

    mobility.SetPositionAllocator(posAlloc);
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(allNodes);
    // İnternet + AODV
    AodvHelper aodv;
    InternetStackHelper internet;
    internet.SetRoutingHelper(aodv);
    internet.Install(allNodes);

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.0.0.0", "255.0.0.0");
    Ipv4InterfaceContainer interfaces = ipv4.Assign(devices);

    // Enerji
    BasicEnergySourceHelper energyHelper;
    energyHelper.Set("BasicEnergySourceInitialEnergyJ", DoubleValue(1000.0));
    EnergySourceContainer sources = energyHelper.Install(allNodes);

    WifiRadioEnergyModelHelper radioHelper;
    radioHelper.Set("TxCurrentA", DoubleValue(0.0174));
    radioHelper.Set("RxCurrentA", DoubleValue(0.0197));
    radioHelper.Install(devices, sources);

    EnergyMonitor energyMonitor;
    energyMonitor.RecordInitial(sources);

    // UDP trafik: her UGV -> AP
    uint32_t apIdx   = uavCount + ugvCount;
    Ipv4Address apAddr = interfaces.GetAddress(apIdx);

    UdpEchoServerHelper server(UDP_PORT);
    ApplicationContainer serverApp = server.Install(apNode.Get(0));
    serverApp.Start(Seconds(1.0));
    serverApp.Stop(Seconds(simTime));

    double trafficStart = 5.0;  // AODV'nin rota kurması için bekle
    for (uint32_t i = 0; i < ugvCount; i++) {
        UdpEchoClientHelper client(apAddr, UDP_PORT);
        client.SetAttribute("MaxPackets",
            UintegerValue((uint32_t)((simTime - trafficStart) / PACKET_INTERVAL)));
        client.SetAttribute("Interval",   TimeValue(Seconds(PACKET_INTERVAL)));
        client.SetAttribute("PacketSize", UintegerValue(PACKET_SIZE));

        ApplicationContainer clientApp = client.Install(ugvNodes.Get(i));
        clientApp.Start(Seconds(trafficStart + i * 0.2));
        clientApp.Stop(Seconds(simTime));
    }

    // FlowMonitor
    FlowMonitorHelper flowHelper;
    Ptr<FlowMonitor> flowMon = flowHelper.InstallAll();

    NS_LOG_UNCOND("Simulasyon basliyor...");
    Simulator::Stop(Seconds(simTime + 1.0));
    Simulator::Run();

    energyMonitor.RecordFinal(sources);

    // Metrik toplama
    flowMon->CheckForLostPackets();
    Ptr<Ipv4FlowClassifier> classifier =
        DynamicCast<Ipv4FlowClassifier>(flowHelper.GetClassifier());
    auto stats = flowMon->GetFlowStats();

    std::vector<NodeMetrics> results;

    for (uint32_t i = 0; i < ugvCount; i++) {
        NodeMetrics m;
        m.name     = "ugv" + std::to_string(i+1);
        m.energy_j = energyMonitor.GetConsumed(uavCount + i);

        Ipv4Address ugvAddr = interfaces.GetAddress(uavCount + i);
        for (auto& kv : stats) {
            auto t = classifier->FindFlow(kv.first);
            if (t.sourceAddress == ugvAddr && t.destinationAddress == apAddr) {
                m.txPackets   += kv.second.txPackets;
                m.rxPackets   += kv.second.rxPackets;
                m.txBytes     += kv.second.txBytes;
                m.rxBytes     += kv.second.rxBytes;
                m.delay_sum   += kv.second.delaySum.GetSeconds();
                m.jitter_sum  += kv.second.jitterSum.GetSeconds();
                m.lostPackets += kv.second.lostPackets;
            }
        }
        results.push_back(m);
    }

    // Özet
    NS_LOG_UNCOND("\n=== AODV Sonuclari ===");
    for (const auto& m : results) {
        double loss_pct  = (m.txPackets > 0) ? 100.0 * m.lostPackets / m.txPackets : 0.0;
        double tput_kbps = (m.rxBytes * 8.0) / (simTime * 1000.0);
        double delay_ms  = (m.rxPackets > 0) ? (m.delay_sum / m.rxPackets) * 1000.0 : 0.0;
        NS_LOG_UNCOND(m.name
            << " | tx=" << m.txPackets << " rx=" << m.rxPackets
            << " loss=" << std::fixed << std::setprecision(1) << loss_pct << "%"
            << " tput=" << tput_kbps << "kbps"
            << " delay=" << delay_ms << "ms"
            << " energy=" << m.energy_j << "J");
    }

    WriteCSV(results, simTime);
    Simulator::Destroy();
    NS_LOG_UNCOND("Simulasyon tamamlandi.");
    return 0;
}