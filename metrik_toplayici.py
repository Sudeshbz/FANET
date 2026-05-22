"""
metrik_toplayici.py
-------------------
FANET karşılaştırmalı analiz scripti.

Üç kaynaktan veri okur:
  1. fanet_aodv_results.csv     — ns-3 AODV baseline
  2. fanet_olsr_results.csv     — ns-3 OLSR baseline
  3. fanet_mininet_metrics.json — Mininet-WiFi SDN sistemi

Çıktılar:
  - Terminal: karşılaştırma tablosu + iyileşme yüzdeleri
  - fanet_karsilastirma.csv     — birleşik CSV
  - fanet_rapor.png             — 4 panel grafik
  - fanet_rapor.html            — interaktif HTML rapor

Çalıştırma:
  python3 metrik_toplayici.py

  # Sadece belirli dosyaları kullan:
  python3 metrik_toplayici.py --aodv fanet_aodv_results.csv \
                               --olsr fanet_olsr_results.csv \
                               --sdn  fanet_mininet_metrics.json

Bağımlılıklar:
  pip install pandas matplotlib numpy
"""

import os
import sys
import json
import argparse
import textwrap
from datetime import datetime

try:
    import pandas as pd
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
except ImportError as e:
    print(f"[HATA] Eksik paket: {e}")
    print("Kurmak için: pip install pandas matplotlib numpy")
    sys.exit(1)


# ─── Varsayılan dosya yolları ─────────────────────────────────────────────────

DEFAULT_AODV = "fanet_aodv_results.csv"
DEFAULT_OLSR = "fanet_olsr_results.csv"
DEFAULT_SDN  = "fanet_mininet_metrics.json"
OUTPUT_CSV   = "fanet_karsilastirma.csv"
OUTPUT_PNG   = "fanet_rapor.png"
OUTPUT_HTML  = "fanet_rapor.html"

# ─── Renk paleti ─────────────────────────────────────────────────────────────

COLOR_AODV = "#E24B4A"
COLOR_OLSR = "#BA7517"
COLOR_SDN  = "#1D9E75"


# ─── Veri yükleme ────────────────────────────────────────────────────────────

def load_ns3_csv(path: str, protocol: str) -> pd.DataFrame:
    """ns-3 CSV çıktısını yükle, UGV satırlarını filtrele."""
    if not os.path.exists(path):
        print(f"[UYARI] {path} bulunamadı — {protocol} verisi atlanıyor.")
        return pd.DataFrame()

    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()

    # Sadece UGV satırları (ap1 satırını çıkar)
    df = df[df["node"].str.startswith("ugv")].copy()
    df["protocol"] = protocol
    return df


def load_mininet_json(path: str) -> pd.DataFrame:
    """Mininet-WiFi JSON metriklerini yükle."""
    if not os.path.exists(path):
        print(f"[UYARI] {path} bulunamadı — SDN verisi atlanıyor.")
        return pd.DataFrame()

    with open(path) as f:
        data = json.load(f)

    rows = []
    for node, metrics in data.items():
        if not node.startswith("ugv"):
            continue
        if "error" in metrics:
            print(f"[UYARI] {node} metrik hatası: {metrics['error']}")
            continue
        rows.append({
            "protocol":         "SDN",
            "node":             node,
            "throughput_kbps":  metrics.get("throughput_mbps", 0) * 1000,
            "avg_delay_ms":     metrics.get("jitter_ms", 0),   # iperf jitter ~ delay proxy
            "packet_loss_pct":  metrics.get("packet_loss_pct", 0),
            "energy_joule":     metrics.get("energy_joule", 0),
            "tx_packets":       0,
            "rx_packets":       0,
            "lost_packets":     0,
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def unify_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Sütun isimlerini normalize et."""
    rename_map = {
        "avg_delay_ms":    "delay_ms",
        "packet_loss_pct": "loss_pct",
        "throughput_kbps": "tput_kbps",
        "energy_joule":    "energy_j",
    }
    return df.rename(columns=rename_map)


# ─── Özet hesaplama ───────────────────────────────────────────────────────────

def compute_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Her protokol için UGV ortalaması al."""
    if df.empty:
        return pd.DataFrame()

    metrics = ["tput_kbps", "delay_ms", "loss_pct", "energy_j"]
    available = [m for m in metrics if m in df.columns]

    summary = (
        df.groupby("protocol")[available]
        .agg(["mean", "std"])
        .round(3)
    )
    return summary


def compute_improvement(summary: pd.DataFrame, baseline: str, target: str) -> dict:
    """
    baseline'a göre target'ın iyileşme yüzdelerini hesapla.
    Pozitif = iyileşme, negatif = kötüleşme.
    """
    if summary.empty:
        return {}

    improvements = {}
    metrics = ["tput_kbps", "delay_ms", "loss_pct", "energy_j"]

    for metric in metrics:
        if (metric, "mean") not in summary.columns:
            continue
        try:
            base_val   = summary.loc[baseline, (metric, "mean")]
            target_val = summary.loc[target,   (metric, "mean")]
        except KeyError:
            continue

        if base_val == 0:
            continue

        delta = target_val - base_val

        # Throughput için artış iyidir; diğerleri için azalış iyidir
        if metric == "tput_kbps":
            pct = (delta / base_val) * 100
        else:
            pct = -(delta / base_val) * 100  # azalma = iyileşme

        improvements[metric] = round(pct, 2)

    return improvements


# ─── Terminal tablosu ─────────────────────────────────────────────────────────

def print_table(summary: pd.DataFrame, improvements_aodv: dict, improvements_olsr: dict):
    """Karşılaştırma tablosunu terminale yaz."""
    print("\n" + "═" * 72)
    print("  FANET Karşılaştırmalı Analiz Raporu")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 72)

    header = f"{'Metrik':<20} {'AODV':>12} {'OLSR':>12} {'SDN':>12}"
    print(header)
    print("─" * 72)

    metric_labels = {
        "tput_kbps": ("Throughput (kbps)", True),   # yüksek iyi
        "delay_ms":  ("Gecikme (ms)",      False),  # düşük iyi
        "loss_pct":  ("Paket Kaybı (%)",   False),
        "energy_j":  ("Enerji (J)",        False),
    }

    for metric, (label, higher_is_better) in metric_labels.items():
        if (metric, "mean") not in summary.columns:
            continue

        def fmt(proto):
            try:
                mean = summary.loc[proto, (metric, "mean")]
                std  = summary.loc[proto, (metric, "std")]
                return f"{mean:.2f}±{std:.2f}"
            except KeyError:
                return "—"

        row = f"{label:<20} {fmt('AODV'):>12} {fmt('OLSR'):>12} {fmt('SDN'):>12}"
        print(row)

    print("─" * 72)
    print("\n  SDN iyileşmesi — AODV'ye göre:")
    for metric, pct in improvements_aodv.items():
        label = metric_labels.get(metric, (metric, True))[0]
        arrow = "▲" if pct > 0 else "▼"
        print(f"    {label:<22} {arrow} {abs(pct):.1f}%")

    print("\n  SDN iyileşmesi — OLSR'ye göre:")
    for metric, pct in improvements_olsr.items():
        label = metric_labels.get(metric, (metric, True))[0]
        arrow = "▲" if pct > 0 else "▼"
        print(f"    {label:<22} {arrow} {abs(pct):.1f}%")

    print("═" * 72)

    # Hedef kontrolü (%15-20)
    all_improvements = list(improvements_aodv.values()) + list(improvements_olsr.values())
    if all_improvements:
        avg_imp = np.mean([abs(x) for x in all_improvements])
        status  = "✓ HEDEF KARŞILANDI" if avg_imp >= 15 else "✗ Hedef karşılanmadı"
        print(f"\n  Ortalama iyileşme: {avg_imp:.1f}%  →  {status}")
        print(f"  (Proje hedefi: %15-20)\n")


# ─── Grafik ───────────────────────────────────────────────────────────────────

def plot_comparison(df: pd.DataFrame, summary: pd.DataFrame):
    """4 panel karşılaştırma grafiği üret."""
    if df.empty or summary.empty:
        print("[UYARI] Grafik için yeterli veri yok.")
        return

    protocols = [p for p in ["AODV", "OLSR", "SDN"] if p in summary.index]
    colors    = {"AODV": COLOR_AODV, "OLSR": COLOR_OLSR, "SDN": COLOR_SDN}

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle(
        "FANET Karşılaştırmalı Analiz: SDN vs AODV vs OLSR",
        fontsize=14, fontweight="bold", y=0.98
    )

    metrics = [
        ("tput_kbps", "Throughput (kbps)",  True,  axes[0, 0]),
        ("delay_ms",  "Ortalama Gecikme (ms)", False, axes[0, 1]),
        ("loss_pct",  "Paket Kaybı (%)",    False, axes[1, 0]),
        ("energy_j",  "Enerji Tüketimi (J)", False, axes[1, 1]),
    ]

    for metric, label, higher_is_better, ax in metrics:
        if (metric, "mean") not in summary.columns:
            ax.text(0.5, 0.5, "Veri yok", ha="center", va="center",
                    transform=ax.transAxes)
            ax.set_title(label)
            continue

        means = [summary.loc[p, (metric, "mean")] for p in protocols]
        stds  = [summary.loc[p, (metric, "std")]  for p in protocols]
        clrs  = [colors[p] for p in protocols]

        bars = ax.bar(protocols, means, yerr=stds,
                      color=clrs, alpha=0.85,
                      capsize=5, edgecolor="white", linewidth=0.5)

        ax.set_title(label, fontsize=11, fontweight="bold")
        ax.set_ylabel(label, fontsize=9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # Değerleri barların üstüne yaz
        for bar, mean, std in zip(bars, means, stds):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + std + max(means) * 0.01,
                f"{mean:.1f}",
                ha="center", va="bottom", fontsize=9, fontweight="bold"
            )

        # SDN barını çerçevele
        if "SDN" in protocols:
            sdn_idx = protocols.index("SDN")
            bars[sdn_idx].set_edgecolor("#1D9E75")
            bars[sdn_idx].set_linewidth(2)

    # Legend
    patches = [mpatches.Patch(color=colors[p], label=p) for p in protocols]
    fig.legend(handles=patches, loc="lower center", ncol=3,
               frameon=False, fontsize=10, bbox_to_anchor=(0.5, 0.01))

    plt.tight_layout(rect=[0, 0.05, 1, 0.96])
    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[Grafik] {OUTPUT_PNG} kaydedildi.")


# ─── HTML rapor ───────────────────────────────────────────────────────────────

def write_html(df: pd.DataFrame, summary: pd.DataFrame,
               imp_aodv: dict, imp_olsr: dict):
    """Karşılaştırma sonuçlarını HTML olarak kaydet."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    metric_labels = {
        "tput_kbps": "Throughput (kbps)",
        "delay_ms":  "Gecikme (ms)",
        "loss_pct":  "Paket Kaybı (%)",
        "energy_j":  "Enerji (J)",
    }

    def row(metric):
        label = metric_labels.get(metric, metric)
        cells = ""
        for proto in ["AODV", "OLSR", "SDN"]:
            try:
                mean = summary.loc[proto, (metric, "mean")]
                std  = summary.loc[proto, (metric, "std")]
                cells += f"<td>{mean:.2f} ± {std:.2f}</td>"
            except KeyError:
                cells += "<td>—</td>"
        return f"<tr><td><b>{label}</b></td>{cells}</tr>"

    def imp_rows(imp_dict, baseline):
        html = ""
        for metric, pct in imp_dict.items():
            label = metric_labels.get(metric, metric)
            color = "#1D9E75" if pct > 0 else "#E24B4A"
            arrow = "▲" if pct > 0 else "▼"
            html += (f"<tr><td>{label}</td><td>{baseline}</td>"
                     f"<td style='color:{color};font-weight:bold'>"
                     f"{arrow} {abs(pct):.1f}%</td></tr>")
        return html

    html = textwrap.dedent(f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
      <meta charset="UTF-8">
      <title>FANET Analiz Raporu</title>
      <style>
        body {{ font-family: system-ui, sans-serif; max-width: 900px;
                margin: 2rem auto; padding: 1rem; color: #1a1a1a; }}
        h1 {{ font-size: 1.4rem; border-bottom: 2px solid #1D9E75;
              padding-bottom: .5rem; }}
        h2 {{ font-size: 1.1rem; margin-top: 2rem; color: #444; }}
        table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
        th {{ background: #f4f4f4; font-weight: 600; }}
        tr:nth-child(even) {{ background: #fafafa; }}
        .sdn {{ background: #EAF3DE !important; }}
        img {{ max-width: 100%; margin-top: 1rem; border-radius: 8px; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 99px;
                  font-size: .8rem; font-weight: 600; }}
        .good {{ background: #EAF3DE; color: #3B6D11; }}
        .bad  {{ background: #FCEBEB; color: #A32D2D; }}
        footer {{ margin-top: 3rem; font-size: .8rem; color: #888; }}
      </style>
    </head>
    <body>
    <h1>FANET Karşılaştırmalı Analiz Raporu</h1>
    <p>Oluşturulma: {ts} &nbsp;|&nbsp; SDN vs AODV vs OLSR</p>

    <h2>Ortalama Metrikler (UGV düğümleri)</h2>
    <table>
      <tr>
        <th>Metrik</th>
        <th style="color:{COLOR_AODV}">AODV</th>
        <th style="color:{COLOR_OLSR}">OLSR</th>
        <th style="color:{COLOR_SDN}">SDN (önerilen)</th>
      </tr>
      {row("tput_kbps")}
      {row("delay_ms")}
      {row("loss_pct")}
      {row("energy_j")}
    </table>

    <h2>SDN İyileşme Oranları</h2>
    <table>
      <tr><th>Metrik</th><th>Baseline</th><th>İyileşme</th></tr>
      {imp_rows(imp_aodv, "AODV")}
      {imp_rows(imp_olsr, "OLSR")}
    </table>

    <h2>Grafik</h2>
    <img src="{OUTPUT_PNG}" alt="Karşılaştırma grafiği">

    <footer>
      TÜBİTAK 2209-A — SDN Kontrollü FANET Projesi &nbsp;|&nbsp; metrik_toplayici.py
    </footer>
    </body>
    </html>
    """)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[HTML]   {OUTPUT_HTML} kaydedildi.")


# ─── Ana akış ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="FANET karşılaştırmalı analiz"
    )
    parser.add_argument("--aodv", default=DEFAULT_AODV,
                        help="AODV CSV dosyası")
    parser.add_argument("--olsr", default=DEFAULT_OLSR,
                        help="OLSR CSV dosyası")
    parser.add_argument("--sdn",  default=DEFAULT_SDN,
                        help="SDN JSON dosyası")
    parser.add_argument("--no-plot", action="store_true",
                        help="Grafik üretme")
    args = parser.parse_args()

    # Veri yükleme
    df_aodv = load_ns3_csv(args.aodv, "AODV")
    df_olsr = load_ns3_csv(args.olsr, "OLSR")
    df_sdn  = load_mininet_json(args.sdn)

    frames = [df for df in [df_aodv, df_olsr, df_sdn] if not df.empty]
    if not frames:
        print("[HATA] Hiç veri yüklenemedi. Önce simülasyonları çalıştırın.")
        sys.exit(1)

    # Birleştir ve normalize et
    df_all = pd.concat(frames, ignore_index=True)
    df_all = unify_columns(df_all)

    # Birleşik CSV kaydet
    df_all.to_csv(OUTPUT_CSV, index=False)
    print(f"[CSV]    {OUTPUT_CSV} kaydedildi.")

    # Özet
    summary = compute_summary(df_all)

    # İyileşme oranları
    imp_aodv, imp_olsr = {}, {}
    if "AODV" in summary.index and "SDN" in summary.index:
        imp_aodv = compute_improvement(summary, "AODV", "SDN")
    if "OLSR" in summary.index and "SDN" in summary.index:
        imp_olsr = compute_improvement(summary, "OLSR", "SDN")

    # Terminal çıktısı
    print_table(summary, imp_aodv, imp_olsr)

    # Grafik
    if not args.no_plot:
        plot_comparison(df_all, summary)

    # HTML rapor
    write_html(df_all, summary, imp_aodv, imp_olsr)


if __name__ == "__main__":
    main()
