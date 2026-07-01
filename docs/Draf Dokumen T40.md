



Lembar Sampul Dokumen
	
Judul Dokumen
TUGAS AKHIR TEKNIK TELEKOMUNIKASI:
Judul tugas akhir: Pengembangan dan Implementasi xApp Deteksi Anomali Trafik (O-RAN) pada Jaringan Testbed 5G-SA




Jenis Dokumen
PENGEMBANGAN DETIL DESAIN DAN IMPLEMENTASI 


Catatan: Dokumen ini dikendalikan penyebarannya oleh Prodi Teknik Telekomunikasi ITB
Nomor Dokumen
T40-01-TA2025.03.06




Nomor Revisi
01




Nama File
T40




Tanggal Penerbitan
03 April 2026 




Unit Penerbit
Prodi Teknik Telekomunikasi – ITB




Jumlah Halaman
                        
(termasuk lembar sampul ini)


Data Pengusul
Pengusul
Nama
Rizqi Radityatama
Jabatan 
Anggota Kelompok


NIM
18122046
Tanda Tangan





Nama
Wan M. Nabiel Arwindra
Jabatan
Anggota Kelompok


NIM
18122041
Tanda Tangan



Pembimbing 1
Nama
M. Ridwan Effendi
Tanda Tangan






NIP
196312011990011002




Pembimbing 2
Nama
Eueung Mulyana
Tanda Tangan






NIP
197412182008011009




Lembaga






Program Studi Teknik Telekomunikasi Sekolah Teknik Telekomunikasi dan Informatika
Institut Teknologi Bandung
Alamat






Labtek 8, Lantai 2, Jalan Ganesha No. 10, Bandung
Telepon : +62 22 2502260
Faks : +62 22 250 0962
Email:stei@stei.itb.ac.id 




 DAFTAR ISI
DAFTAR ISI	2
1	Pengantar	3
1.1	Ringkasan Isi Dokumen	3
1.2	Tujuan Penulisan dan Aplikasi/Kegunaan Dokumen	3
1.3	Daftar Singkatan	3
2	Pemodelan Desain 	4
2.1	Pemodelan Fungsional Sistem	4
2.1.1	Subsistem Integrasi Sistem (RAN - RIC)	4
2.1.2	Subsistem Deteksi Anomali Trafik (Near-RT RIC)	5
2.2	Pemodelan Tingkah Laku Sistem	6
2.2.1	Diagram Konteks (Level 0)	6
2.2.2	Perilaku Sistem Keseluruhan (Level 1)	6
3	Implementasi Desain	8
3.1	Subsistem Integrasi Sistem (RAN - RIC)	8
3.1.1	Akuisisi Data srsRAN dan Streamer	8
3.1.2	Transmisi UDP Asinkron	9
3.2	Subsistem Deteksi Anomali Trafik (Near-RT RIC)	10
3.2.1	Integrasi RealDataBridge pada FlexRIC	10
3.2.2	Inferensi LSTM-Autoencoder dan Thresholding	11
4	Analisis Pengerjaan Implementasi	12
Referensi	13
Lampiran	14






















1 Pengantar
1.1 Ringkasan Isi Dokumen
Isi dokumen T40 ini berisi pemodelan dan implementasi desain dari tugas akhir dengan judul "Pengembangan dan Implementasi xApp Deteksi Anomali Trafik (O-RAN) pada Jaringan Testbed 5G-SA". Akan dijelaskan lebih rinci mengenai desain arsitektur fungsional subsistem, tingkah laku transmisi telemetri, serta realisasi kode implementasi dari *Security xApp* yang diintegrasikan secara *real-time* ke dalam *Near-RT RIC* FlexRIC.

1.2 Tujuan Penulisan dan Aplikasi/Kegunaan Dokumen
Tujuan penulisan dokumen ini adalah sebagai berikut:
1. Sebagai dokumentasi teknis mendalam penjabaran fungsionalitas dan pemodelan operasional sistem *Security xApp* yang diintegrasikan dalam jaringan *testbed* O-RAN fisik.
2. Untuk memvalidasi kesesuaian antara rencana awal pada dokumen T20/T30 dengan status pengerjaan implementasi nyata menggunakan *Software-Defined Radio* (SDR) dan *Real User Equipment* (UE).
3. Sebagai catatan historis dari proses pengerjaan, algoritma inferensi, dan penyelesaian masalah terkait telemetri jaringan berkecepatan tinggi.
Dokumen ini ditujukan kepada dosen pembimbing tugas akhir dan tim tugas akhir Program Studi Teknik Telekomunikasi ITB sebagai bahan penilaian komprehensif terkait komponen teknis tugas akhir.

1.3 Daftar Singkatan
Singkatan	Arti
5G-SA		5G Standalone
AI			Artificial Intelligence
CQI			Channel Quality Indicator
E2AP		E2 Application Protocol
gNB			gNodeB (5G Base Station)
LSTM		Long Short-Term Memory
MSE			Mean Squared Error
O-RAN		Open Radio Access Network
PRB			Physical Resource Block
RNTI		Radio Network Temporary Identifier
RIC			RAN Intelligent Controller
SDR			Software-Defined Radio
SNR			Signal-to-Noise Ratio
UDP			User Datagram Protocol
UE			User Equipment






















2 Pemodelan Desain 
Pada tahap ini penulis merealisasikan alternatif desain arsitektur yang telah dipilih pada dokumen T30, yaitu penerapan testbed tiga node fisik menggunakan *srsRAN* sebagai gNB, *FlexRIC* dari Mosaic 5G sebagai pangkalan komputasi *Near-RT RIC*, serta *Open5GS* sebagai bagian dari *5G Core*. Model kecerdasan buatan berbasis *LSTM-Autoencoder* dipilih untuk pendeteksian pola intrusi tak kasat alat karena rekam jejaknya pada deteksi *time-series*. Semua spesifikasi teknis tersebut direkam dengan presisi di dalam dokumentasi operasional ini.

2.1 Pemodelan Fungsional Sistem
Pemodelan fungsional ini dipecah menjadi dua subsistem krusial yang bahu-membahu dalam mengamankan alur data jaringan 5G-SA.

2.1.1 Subsistem Integrasi Sistem (RAN - RIC)
Subsistem pertama merupakan tulang punggung penghantar telemetri berlatensi rendah untuk meneruskan informasi pemantauan dari perangkat *Radio Access Network* ke *RIC*. Subsistem ini berfungsi sebagai generator dan pengekstraksi metrik penting. Data metrik tingkat radio, termasuk indeks stabilitas tautan dan alokasi *PRB*, dipancarkan oleh *srsRAN*. Skrip pendamping bernama *ultimate_streamer.py* yang beroperasi pada lingkungan Node RAN (`10.91.2.1`) menangkap struktur data E2AP tersebut lewat protokol WebSocket ringan.
Untuk secara ketat memenuhi Constraint T20 terkait efisiensi stabilisasi gNB, subsistem ini menggunakan fungsionalitas kirim-tanggalkan (*fire-and-forget*) dari protokol transfer datagram *User Datagram Protocol* (UDP). 

2.1.2 Subsistem Deteksi Anomali Trafik (Near-RT RIC)
Subsistem Deteksi merupakan unit pemrosesan logis yang tertanam bersama kontroler FlexRIC pada Node RIC (`10.91.2.2`). Pada level fungsional ini, sistem menerima deretan data terstruktur setiap satuan waktu dari jaringan UDP. Fungsionalitas inti dari mesin cerdas *Backend-AI* mencakup perakitan metrik ke dalam suatu *sequence deque* (antrean sekuensial) bervolume $N=10$, lalu mengevaluasinya secara paralel dengan agen inferensi berlapis algoritma *LSTM-Autoencoder*.
Model ini berfungsi untuk menginspeksi adanya pencilan metrik—seperti kegagalan SNR seketika atau ledakan permintaan (*Signaling Storms/Jamming*)—berdasarkan perbandingan simpangan selisih rekonstrusi berbasis fungsi pengukuran deviasi *Mean Squared Error* (MSE). Intervensi pendeteksian dan pelaporan anomali ditangani otomatis untuk tidak melewati batas toleransi maksimal komputasi Node RIC (85% rasio utilisasi CPU).

2.2 Pemodelan Tingkah Laku Sistem
Pemodelan tingkah laku menggambarkan cara aliran telemetri dibentuk, dihantar, dievaluasi, serta cara sistem mengeluarkan umpan balik keputusan. Level diagram fungsional menjabarkan tingkah operasional secara hierarkis.

2.2.1 Diagram Konteks (Level 0)
Sistem ini dipicu oleh aktivitas transmisi lalu-lintas 5G dari entitas *Real User Equipment* (Oppo Reno 8) ke satelit radio *Software-Defined Radio* USRP B205 mini. Diagram level konteks menekankan batas sistem pendeteksi di mana *Security xApp* menerima metrik L1/L2 MAC dari gNB, mendeteksi penyimpangan normalitas, lalu memicu penyimpanan *Database Logging* atau mekanisme *Dashboard Alerting* (melalui Grafana atau modul observasi serupa) yang ditransimisikan ke pihak Administrator.

2.2.2 Perilaku Sistem Keseluruhan (Level 1)
Pada dekomposisi tingkat perilaku sistem keseluruhan (Level 1), koordinasi siklus terikat secara asinkron antarnode. Alur kerja operasional mengikuti tata tertib penyalaan komponen 5G bertahap: (1) Core Open5GS mengudara untuk negosiasi IP, (2) Near-RT RIC FlexRIC diaktifkan untuk menunggu soket `E2AP` (:36421), (3) proses srsGNB di-inisiasi, dilanjutkan (4) penjalanan daemon streamer xApp.
Aliran metrik, setelah ditangkap dan dirampingkan (ekstraksi kolom esensial: RNTI, SNR, DL/UL Throughput, CQI), dikemas sebagai sediaan sekuensial biner. *RealDataBridge* di sisi RIC mengorkestrasikan kumpulan *socket payload* menggunakan konsep *sliding window* dengan konjungsi model inferensi berbasis PyTorch. Saat *reconstruction loss* terindikasi melebihi nilai *threshold deterministik* 0.045, perhentian alur bergeser memanggil *Alert Manager* yang menjadwalkan notifikasi peringatan. Evaluasi asinkron ini memastikan rantai siklus E2AP yang responsif tanpa interupsi *blocking*.






















3 Implementasi Desain
Uraikan pekerjaan implementasi semua bagian sistem yang telah dirancang, misalnya pembuatan *prototype* infrastruktur uji jaringan, pemrograman antarmuka, dan sinkronisasi mesin inferensi. Pekerjaan yang didokumentasikan di sini adalah implementasi fungsional terkini hasil modifikasi dari fase simulasi menuju fase pembuktian perangkat *testbed* berskala fisik (akuisisi data langsung). Kaitkan metodologi kode sumber dengan regulasi desain pada dokumen T20.

3.1 Subsistem Integrasi Sistem (RAN - RIC)
Implementasi fungsional jaringan pengumpul data ini dibangun untuk menghindari pembebanan siklus waktu pemrosesan (*parsing overhead*) yang signifikan ketika interaksi E2AP gNB mengekstrak variabel internal telemetri. 

3.1.1 Akuisisi Data srsRAN dan Streamer
Sistem *streamer* telemetri diwujudkan dalam skrip `ultimate_streamer.py` yang dijalankan berdampingan bersama sesi gNB pada Node `10.91.2.1`. Penerapan perangkat keras pengalihan komputasi seperti *Real User Equipment* (Oppo Reno 8) menyediakan sampel trafik yang tidak artifisial. 
Data yang didapatkan mencakup metrik `dl_throughput`, `ul_throughput`, `snr`, `cqi`, dan `rnti`. Pengambilan data ini beroperasi menambat pada format WebSocket internal srsRAN sehingga sangat ringan.

3.1.2 Transmisi UDP Asinkron
Skema penyaluran metrik dibuat bersifat transfer mentah tanpa jaminan resi balik (*fire-and-forget* menggunakan soket UDP) agar tidak merusak ketahanan *state* gNB saat terjadi gangguan trafik siber. Berikut pseudocode transmisi metrik dari log srsRAN:

```python
# Potongan instruksi parsing stream dari ultimate_streamer.py
TARGET_IP = "10.91.2.2"
udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Pengekstraksian fitur esensial dari Radio Metrics srsRAN via Regex (ue_row_re)
m = ue_row_re.search(line)
if m:
    rnti = int(m.group(2), 16) if any(c in 'abcdefABCDEF' for c in m.group(2)) else int(m.group(2))
    dl_thp = parse_brate(m.group(4))  # Konversi ke kbps
    snr = float(m.group(5))
    ul_thp = parse_brate(m.group(6))  # Konversi ke kbps
    cqi = int(m.group(3))

    # Proses transfer asinkron berbasis datagram UDP (fire-and-forget)
    payload = f"TYPE=UP,RNTI={rnti},DL_THP={dl_thp:.1f},UL_THP={ul_thp:.1f},SNR={snr:.1f},CQI={cqi}"
    udp_sock.sendto(payload.encode('utf-8'), (TARGET_IP, 5555))
```

Selain itu, keberhasilan transmisi asinkron UDP dapat dibuktikan dengan pencatatan telemetri di sisi RIC. Tabel 1 di bawah ini menunjukkan cuplikan sampel (*screenshot/log*) rekam data yang ditangkap secara aktual (`comprehensive_record_20260315_135550.csv`) pada iterasi pengujian sistem:

| timestamp | rnti | mac_dl_tbs | mac_ul_tbs | mac_pusch_snr | mac_wb_cqi | kpm_prb_tot_dl | kpm_prb_tot_ul |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2026-03-15 13:56:03 | 0 | 0 | 0 | 0.0 | 0 | 0.0 | 1.0 |
| 2026-03-15 13:56:04 | 4602 | 10000.0 | 1460.0 | 31.0 | 15 | 1.0 | 0.0 |
| 2026-03-15 13:56:05 | 4602 | 10000.0 | 1460.0 | 31.0 | 15 | 7.0 | 2.0 |
| 2026-03-15 13:56:06 | 4602 | 10000.0 | 1460.0 | 31.0 | 15 | 41.0 | 2.0 |
| 2026-03-15 13:56:07 | 4602 | 10000.0 | 1460.0 | 31.0 | 15 | 99.0 | 2.0 |

Data historis ini memvalidasi bahwa skrip *streamer* di RAN efektif beroperasi dan responsif mendiseminasi ID spesifik perangkat (seperti `RNTI: 4602`) beserta *throughput*-nya ke modul internal Near-RT RIC tanpa indikasi hilang paket berlebihan.


3.2 Subsistem Deteksi Anomali Trafik (Near-RT RIC)
Dalam Subsistem Taktis Pendeteksi yang bertempat pada Node RIC, implementasi disempurnakan sebagai *Python Wrapper Middleware* agar sanggup berjalan mandiri menangkap siaran `E42` yang ditertibkan aplikasi induk *FlexRIC C/C++*.

3.2.1 Integrasi RealDataBridge pada FlexRIC
Komponen *RealDataBridge* (`src/integration/real_data_bridge.py`) disiapkan untuk menyinkronkan interaksi metrik dengan siklus deteksi model kecerdasan buatan. Sinkronisasi *timestamp* diterapkan demi merekatkan presisi sekuensial, memungkinkan konversi sekumpulan 10 paket berurutan (*sliding window* $N=10$) menjadi tabung *tensor batch 3D*. Keputusan mempertahankan ukuran bufer mikro diimplementasi murni demi meraih batasan objektif ketiga terkait efisiensi pemanfaatan rata-rata *resource* CPU <85%.

3.2.2 Inferensi LSTM-Autoencoder dan Thresholding
Algoritma AI disuntikkan parameter timbang terlatih berbasis kerangka kerja *PyTorch* (`lstm_autoencoder.pt`) yang dipasangkan eksklusif dengan objek normalisasi variabel (`scaler.pkl`), bersumber dari rekaman *benign traffic* (kondisi tanpa polusi anomali). Pemrosesan validasi berbasis rekonstruksi metrik menghasilkan skor ralat, di mana peringatan deteksi dideklarasikan apabila selisih komputasi *Mean Squared Error* (MSE) dari operasi arsitektur bersangkutan melompati takaran *threshold hybrid* numerik `0.045`. 

```python
# Potongan arsitektur middleware dari src/integration/real_data_bridge.py
def push(self, ue_data: dict, cp_data: dict, kpm_data: dict):
    # Ekstraksi paket UDP dan restrukturisasi metrik 
    snapshot = RealDataSnapshot.from_dicts(ue_data=ue_flat, cp_data=cp_data, kpm_data=kpm_data, rnti=rnti)
    kpi = snapshot.to_kpi_metrics()
    
    # Injeksi parameter metrik untuk dievaluasi oleh LSTM [dl_tbs, ul_tbs, snr, cqi, prb_dl]
    kpi.raw_lstm_features = np.array([
        snapshot.mac_dl_tbs,
        snapshot.mac_ul_tbs,
        snapshot.mac_pusch_snr,
        snapshot.mac_wb_cqi,
        snapshot.kpm_prb_tot_dl
    ], dtype=np.float32)

    # Sinkronisasi window N=10 dan inferensi Model AI 
    with self._lock:
        result = self._detector.detect(kpi)

    # Pemrosesan anomali ke AlertManager jika melampaui MSE threshold konstan (0.045)
    if result.is_anomaly:
        self.total_anomalies += 1
        alert = self._alert_manager.process_anomaly(result)
        if alert:
            self.total_alerts += 1
        return result

    return None
```






















4 Analisis Pengerjaan Implementasi

Bagian ini berisi evaluasi antara rentang waktu tenggat (*timeline*) perencanaan yang diajukan di dokumen T20 dengan realitas pekerjaan lapangan. Terdapat pelacakan progres perancangan sistem, terutama dengan adanya transisi krusial dari *User Equipment* (UE) yang sebelumnya dibentuk melalui agen tersimulasi, diubah menjadi lingkungan interaksi keras aktual (SDR USRP dan perangkat Oppo Reno).

Pengerjaan modul *xApp deteksi anomali pada arsitektur O-RAN* mengalami eskalasi implementasi pada minggu ke-8 hingga ke-12 karena tim berhasil merealisasikan integrasi *SDK API FlexRIC* serta penyusunan alur arsitektur *LSTM-Autoencoder* yang stabil melampaui tahapan yang terencana. Walau terdapat tantangan keterbatasan dependensi E2SM-KPM pada versi pustaka perangkat lunak terdahulu, restrukturisasi komponen di sisi RAN berhasil meminimalisasi hantaman isu performa, menghasilkan konektivitas RIC dan RAN yang solid dan rendah latensi. Pada sisa minggu pengerjaan menuju penerbitan akhir laporan teknis tugas akhir, progres tim difokuskan untuk perekaman pengujian keandalan respon deteksi, uji keamanan menggunakan vektor serangan tiruan/serangan radio fisik riil di laboratorium, serta penulisan kelengkapan bab akhir pada laporan Tugas Akhir.

Referensi
[1] O-RAN Alliance, "O-RAN Near-Real-Time RAN Intelligent Controller Architecture & E2 General Aspects and Principles", 2022.
[2] P. Lewis et al., "Deep Learning Analytics for Network Traffic Anomalities in 5G-SA Architectures", Journal of Network Security, vol 15, no 3, 2024.
[3] Mosaic5G FlexRIC, "FlexRIC Documentation and E42 SDK Implementation Guide", 2024. [Online]. Available: https://gitlab.eurecom.fr/o-ran-sc/ric-plt-flexric
[4] srsRAN Project, "srsRAN 4G/5G Wireless Implementation Architecture", [Online]. Available: https://github.com/srsran/srsRAN_Project.

Lampiran
Dokumen teknis pelengkap seperti hasil luaran metrik MSE (log deteksi per kejadian), petunjuk arsitektur instalasi Open5GS terbaru, sertifikasi parameter kalibrasi B205 mini, serta konfigurasi profil srsRAN 5G-SA.
