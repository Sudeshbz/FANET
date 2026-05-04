import csv
import matplotlib.pyplot as plt
import os

def generate_graphs():
    csv_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'performance_results.csv')
    if not os.path.exists(csv_file):
        # Eger calistirilan dizindeyse diye de bakalim
        if os.path.exists('performance_results.csv'):
            csv_file = 'performance_results.csv'
        else:
            print(f"Hata: performance_results.csv bulunamadi. Lutfen once performance_test.py scriptini calistirin.")
            return

    scenarios = []
    rtt_values = []
    loss_values = []
    bw_values = []

    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            scenarios.append(row['Scenario'])
            try:
                rtt_values.append(float(row['Avg_RTT_ms']))
            except:
                rtt_values.append(0.0)
            try:
                loss_values.append(float(row['Packet_Loss_%']))
            except:
                loss_values.append(100.0)
            try:
                bw_values.append(float(row['Bandwidth_Mbps']))
            except:
                bw_values.append(0.0)

    output_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. Gecikme
    plt.figure(figsize=(10, 6))
    plt.bar(scenarios, rtt_values, color='skyblue', edgecolor='black')
    plt.title('Senaryolara Gore Ortalama Gecikme (RTT)')
    plt.ylabel('Gecikme (ms)')
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    for i, v in enumerate(rtt_values):
        plt.text(i, v + 0.5, f"{v:.2f}", ha='center')
    plt.savefig(os.path.join(output_dir, 'latency_chart.png'))
    plt.close()

    # 2. Bant Genisligi
    plt.figure(figsize=(10, 6))
    plt.bar(scenarios, bw_values, color='lightgreen', edgecolor='black')
    plt.title('Senaryolara Gore Bant Genisligi')
    plt.ylabel('Bant Genisligi (Mbps)')
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    for i, v in enumerate(bw_values):
        plt.text(i, v + 0.1, f"{v:.2f}", ha='center')
    plt.savefig(os.path.join(output_dir, 'bandwidth_chart.png'))
    plt.close()

    # 3. Paket Kaybi
    plt.figure(figsize=(10, 6))
    plt.bar(scenarios, loss_values, color='salmon', edgecolor='black')
    plt.title('Senaryolara Gore Paket Kaybi')
    plt.ylabel('Paket Kaybi (%)')
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    for i, v in enumerate(loss_values):
        plt.text(i, v + 0.5, f"{v:.1f}%", ha='center')
    plt.savefig(os.path.join(output_dir, 'packet_loss_chart.png'))
    plt.close()

    print(f"Grafikler '{output_dir}' dizininde basariyla uretildi:")
    print("- latency_chart.png")
    print("- bandwidth_chart.png")
    print("- packet_loss_chart.png")

if __name__ == '__main__':
    generate_graphs()
