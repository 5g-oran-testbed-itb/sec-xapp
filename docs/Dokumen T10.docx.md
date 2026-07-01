

Lembar Sampul Dokumen  
	

| Judul Dokumen | TUGAS AKHIR TEKNIK TELEKOMUNIKASI: *5G-Testbed@ITB: Pengembangan dan Implementasi Security-Related xApp (O-RAN) pada Jaringan Testbed 5G-SA* |  |
| :---- | :---- | :---- |
|  |  |  |
| Jenis Dokumen | **PROPOSAL IDENTIFIKASI PERMASALAHAN**   |  |
|  | Catatan: Dokumen ini dikendalikan penyebarannya oleh Prodi Teknik Telekomunikasi ITB |  |
| Nomor Dokumen | **T10-01-TA2022.03.01** |  |
|  |  |  |
| Nomor Revisi | **01** |  |
|  |  |  |
| Nama File | **T10** |  |
|  |  |  |
| Tanggal Penerbitan | **27 September 2025** |  |
|  |  |  |
| Unit Penerbit | **Prodi Teknik Telekomunikasi – ITB** |  |
|  |  |  |
| Jumlah Halaman | **9**        | (termasuk lembar sampul ini) |

| Data Pengusul |  |  |  |  |  |  |  |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| Pengusul | Nama | Rizqi Radityatama |  |  | Jabatan  |  | Anggota Kelompok |
|  | NIM | 18122046 |  |  | Tanda Tangan |  |   |
|  | Nama | Wan M. Nabiel Arwindra |  |  | Jabatan |  | Anggota Kelompok |
|  | NIM | 18122041 |  |  | Tanda Tangan |  |   |
| Pembimbing 1 | Nama | M. Ridwan Effendi |  |  | Tanda Tangan |  |   |
|  | NIP | 196312011990011002 |  |  |  |  |  |
| Pembimbing 2 | Nama | Eueung Mulyana |  |  | Tanda Tangan |  |   |
|  | NIP | 197412182008011009 |  |  |  |  |  |
| Lembaga |  |  |  |  |  |  |  |
| Program Studi Teknik Telekomunikasi Sekolah Teknik Telekomunikasi dan Informatika Institut Teknologi Bandung |  |  |  |  |  |  |  |
| Alamat |  |  |  |  |  |  |  |
| Labtek 8, Lantai 2, Jalan Ganesha No. 10, Bandung |  |  |  |  |  |  |  |
| Telepon : \+62 22 2502260 |  |  |  | Faks : \+62 22 250 0962 |  | Email:stei@stei.itb.ac.id |  |

# **DAFTAR ISI**

# 

# 

# DAFTAR ISI

# 

# 1\.	PENGANTAR

# 1.1	Ringkasan Isi Dokumen

# 1.2	Tujuan Penulisan dan Aplikasi/Kegunaan Dokumen

# 1.3	Daftar Singkatan

# 

# 2\.	LATAR BELAKANG MASALAH

# 2.1	Identifikasi Permasalahan Pelanggan

# 2.2.   	Identifikasi Potensi Pelanggan

# 2.3.   	Identifikasi Persyaratan Pelanggan

3\.    FORMULASI MASALAH

3.1.   	Rumusan Masalah  
3.2.   	Tujuan   
3.3.	Manfaat Desain

# 4\.     ANALISIS UMUM

REFERENSI

# 

LAMPIRAN 1\. Formulir Pendaftaran Tugas Akhir I & Seminar

LAMPIRAN 2\. Curriculum Vitae

1. # **PENGANTAR**

   1. ## **RINGKASAN ISI DOKUMEN**

Topik ini bertujuan untuk pengembangan dan implementasi security-related xApp berbasis open-source pada testbed 5G  *open radio access network* (O-RAN), dengan fokus pada integrasi di *ear* *real-time* RAN *intelligent controller* (*Near*\-RT RIC) untuk mendeteksi ancaman keamanan serta menguji fungsionalitas dan kinerjanya.

Permasalahan yang menjadi dasar penelitian ini adalah meningkatnya kerentanan keamanan pada arsitektur O-RAN dikarenakan sifat terbuka dan terdisagregasinya. Rumusan masalah difokuskan kepada bagaimana merancang xApp keamanan berbasis *open-source*, mengintegrasikan pada *Near-*RT RIC, serta menguji fungsionalitas dan kinerjanya dalam meningkatkan aspek keamanan jaringan.

Penelitian diharapkan memberikan beberapa manfaat desain tersebut dari segi sosial budaya, politik, pendidikan, dan teknologi. Menurut aspek sosial budaya, pengembangan berbasis *open-source* mendorong kolaborasi dan kemandirian teknologi. Dari aspek politik, tugas akhir ini dapat menjadi saran untuk regulator agar menyusun kebijakan keamanan 5G nasional dengan baik. Diharapkan *testbed* O-RAN pada penelitian ini dapat bertindak sebagai media pembelajaran untuk mahasiswa untuk memahami keamanan jaringan terbaru pada aspek pendidikan. Terakhir, dari aspek teknologi, penelitian ini dapat mendorong transformasi digital dengan infrastruktur terbuka yang lebih fleksibel dan berkelanjutan.

2. ## **TUJUAN PENULISAN DAN APLIKASI/KEGUNAAN DOKUMEN**

Tujuan penulisan dokumen ini adalah sebagai berikut:

* Sebagai gambaran umum dari proyek yang akan dikerjakan dari segi teknis dan non teknis  
* Untuk memastikan bahwa tugas akhir ini adalah sesuatu yang layak untuk dikerjakan  
* Sebagai catatan dari proses pengerjaan dan catatan revisi yang dilakukan.

Dokumen ini ditujukan kepada dosen pembimbing tugas akhir dan tim tugas akhir Program Studi Teknik Telekomunikasi ITB sebagai bahan penilaian tugas akhir.

3. ## **DAFTAR SINGKATAN**

| Singkatan | Arti |
| ----- | ----- |
| O-RAN | *Open Radio Access Network* |
| *Near*\-RT RIC | *Near-Real Time RAN Intelligent Controller* |
| RAN | *Radio Access Network* |
| SA | *Stand Alone* |
| UE | *User Equipment* |
| STEI | Sekolah Teknik Elektro dan Teknik Informatika |
| OSS | *Open Source Software* |

2. # **LATAR BELAKANG MASALAH**

   1. ## **IDENTIFIKASI PERMASALAHAN PELANGGAN**

**TAMBAHIN RAN, CORE, O-RAN DEFINISI SEMUANYA** 

Paradigma baru dalam implementasi jaringan 5G adalah *open radio access network* (O-RAN), yang menekankan arsitektur terbuka, modular, dan terdisagregasi. Konsep disagregasi dengan antarmuka terbuka yang distandarisasi memungkinkan O-RAN untuk berinteraksi dengan vendor lain dan mendukung fungsi intelligent controller seperti *Near*\-RT RIC \[1\]. Keunggulan ini meningkatkan fleksibilitas dan inovasi dalam industri telekomunikasi. Namun, jika dibandingkan dengan arsitektur RAN tradisional yang lebih tertutup, sifat keterbukaan tersebut meningkatkan risiko keamanan dan memperluas titik rentan keamanan seperti pada E2 interface \[2\].

Kerentanan O-RAN terutama terkait dengan bagian *near* *real-time* RAN *intelligent controller* (*Near*\-RT RIC) dan antarmuka terbuka seperti E2. Serangan, seperti *malicious* xApp yang menyalahgunakan kontrol jaringan atau manipulasi data yang mengganggu stabilitas sistem \[2\]. Masalah keamanan jaringan masih menjadi fokus utama dalam implementasi O-RAN walaupun memiliki potensi besar dalam hal fleksibilitas dan minimnya ketergantungan terhadap perangkat lain.

Penggunaan perangkat lunak berbasis open-source dalam O-RAN juga menimbulkan risiko tambahan. Hal-hal seperti *backdoor*, *man-in-the-middle attack* pada *fronthaul*, dan serangan berbasis *data poisoning* dapat dimanfaatkan oleh pihak berbahaya jika tidak diantisipasi dengan baik \[3\]. Industri juga menekankan bahwa untuk mencegah masalah baru, pengembangan O-RAN harus memperhatikan privasi, integritas data, dan keandalan jaringan \[4\], \[5\].

Maka dari itu, diperlukan solusi teknologi yang mampu menjawab permasalahan-permasalahan tersebut. Hal ini dapat dicapai dengan menyediakan mekanisme perlindungan yang terintegrasi, kompatibel dengan arsitektur O-RAN, dan dapat diuji secara representatif dalam testbed. Dengan begitu, diharapkan dapat mengurangi kesenjangan antara kebutuhan nyata operator untuk memastikan keamanan jaringan 5G dan penelitian yang masih terbatas pada simulasi.

**2.2. IDENTIFIKASI POTENSI PELANGGAN** 

Sekolah Tinggi Elektro dan Informatika Institut Teknologi Bandung (STEI ITB) sudah mempunyai *testbed* 5G O-RAN SA. *Testbed* tersebut menggunakan perangkat lunak srsRAN untuk jaringan RAN dan Open5GS untuk jaringan *core*. Perangkat keras yang digunakan adalah USRP B205mini-i sebagai gNodeB dan Oppo Reno 8 5G sebagai *user equipment* (UE) \[6\], \[7\].

**2.3. IDENTIFIKASI PERSYARATAN PELANGGAN**

Pelanggan mempunyai kebutuhan menambahkan spek keamanan pada infrastruktur *testbed* 5G O-RAN. Dari sisi objektif, pelanggan menginginkan solusi keamanan tambahan melalui *Security* xApp yang mampu meningkatkan perlindungan jaringan tanpa mengurangi performansi *testbed*. 

Dari sisi *constraint*, solusi harus kompatibel dengan perangkat lunak yang digunakan pada *testbed*, yaitu srsRAN untuk jaringan RAN dan Open5GS pada jaringan *core*. Selain itu, solusi juga harus kompatibel pada perangkat keras yaitu USRP B205mini-i sebagai gNodeB dan UE. Aplikasi harus dijalankan pada RT-RIC.

Fungsi utama yang harus dipenuhi meliputi mendeteksi anomali atau serangan dalam jaringan, memberikan notifikasi serta log keamanan secara *real-time*, dan menyediakan antarmuka *monitoring* yang terintegrasi dengan *testbed*. Sistem harus mendukung konfigurasi ulang agar dapat digunakan pada berbagai skenario eksperimen.

3. # **FORMULASI MASALAH**

   1. ## **RUMUSAN MASALAH**

Bagaimana merancang *Security-Related* xApp berbasis *open-source* yang dapat bekerja pada arsitektur O-RAN, bagaimana cara mengintegrasikannya pada *Near* RT-RIC pada jaringan *testbed* O-RAN serta bagaimana cara menguji fungsionalitas dan kinerjanya dalam meningkatkan keamanan jaringan? 

2. ## **TUJUAN**

Tujuan dari tugas akhir ini adalah merancang dan mengimplementasikan *security-related* xApp berbasis *open-source* yang dapat berjalan di atas arsitektur O-RAN, mengintegrasikannya pada *Near RT-RIC* dalam jaringan testbed O-RAN serta menguji fungsionalitas dan kinerjanya demi meningkatkan aspek keamanan jaringan.

3. ## **MANFAAT DESAIN** 

Manfaat pengembangan proyek tugas akhir ini dapat dikaitkan dengan beberapa aspek, yaitu: sosial budaya, politik/regulasi, pendidikan, dan lingkungan teknologi.

1\. Sosial Budaya

Pengembangan *security-related* xApp berbasis *open-source* memiliki nilai sosial budaya penting, terutama dalam membangun ekosistem kolaboratif di bidang telekomunikasi. *Open-source* dapat mencerminkan nilai seperti keterbukaan, kolaborasi, partisipasi, meritokrasi dan transparansi \[8\]. Dengan prinsip tersebut pengembangan teknologi O-RAN di Indonesia dapat dilakukan secara inklusif dengan melibatkan akademisi, praktisi dan pihak lain untuk mempercepat proses adopsi dan inovasi tanpa bergantung sepenuhnya pada vendor komersial.

Nilai utama *open-source* menekankan aksesibilitas, kreativitas, kebebasan milih solusi, serta skalabilitas melalui kolaborasi, karena pihak-pihak dengan keterbatasan sumber daya tetap dapat berkontribusi, karena kode dapat diakses, dimodifikasi dan digunakan tanpa hambatan *lock-in* kepada suatu vendor \[9\]. Kolaborasi *open-source* juga mendorong budaya berbagi pengetahuan dan inovasi terbuka, yang dapat mempercepat kemandirian teknologi di Indonesia.

2\. Politik

Keamanan merupakan aspek strategis dalam pengembangan infrastruktur digital Indonesia. Melalui Peraturan Presiden Nomor 53 tahun 2017 (Perpres 133/137), pemerintah Indonesia telah membentuk Badan Siber dan Sandi Negara (BSSN) sebagai lembaga utama dalam implementasi keamanan siber nasional. Strategi Keamanan Siber Indonesia menegaskan lima prinsip utama yang menjadi landasan dalam menjaga kedaulatan digital dan meningkatkan daya saing nasional, yaitu Kedaulatan, Kemandirian, Keamanan, Kebersamaan dan Adaptif \[10\].

Pengembangan xApp keamanan pada *testbed* O-RAN dapat berperan sebagai masukan praktis bagi badan pemerintah seperti BSSN dalam merumuskan kebijakan keamanan 5G. Kemudian, xApp keamanan *open-source* dapat membangun ruang siber yang aman, andal dan kompetitif serta mengurangi ketergantungan kepada vendor asing.

3\. Pendidikan

Pemanfaatan *open-source software* (OSS) di institusi pendidikan membuka banyak peluang untuk meningkatkan inovasi, meningkatkan kolaborasi, dan mengubah metode pembelajaran dan pengajaran.  Keunggulan OSS seperti kolaborasi komunitas, fleksibilitas, dan hemat biaya sesuai dengan prinsip pendidikan modern.  Lembaga pendidikan dapat membuat lingkungan belajar yang inklusif dan dinamis dengan memasukkan OSS ke dalam kurikulum dan laboratorium. Ini akan memberi siswa kesempatan untuk berkembang melalui pengalaman nyata.  Strategi perencanaan harus dilakukan untuk memaksimalkan manfaat OSS dengan mengantisipasi masalah seperti dukungan teknis, kompatibilitas, dan kebutuhan pelatihan tetap \[11\].

Dalam konteks proyek ini, pengembangan xApp berbasis *open-source* yang berkaitan dengan keamanan di testbed 5G O-RAN ITB dapat berfungsi sebagai media pembelajaran langsung bagi siswa.  Mahasiswa dapat mempelajari teori dan praktik keamanan jaringan generasi baru melalui keterlibatan dalam perancangan, integrasi, dan pengujian xApp pada *Near*\-RT RIC.  Selain itu, evaluasi internal, seperti survei pengguna atau hasil eksperimen siswa, dapat meningkatkan pemahaman tentang ekspektasi kinerja dan dukungan infrastruktur.  Oleh karena itu, testbed ini tidak hanya mendukung penelitian akademik, tetapi juga mendukung kurikulum 5G, keamanan siber, dan software open-source di universitas.

4\. Teknologi

Implementasi testbed O-RAN dengan xApp terkait keamanan sejalan dengan lintasan transformasi digital Indonesia yang fokus pada modernisasi infrastruktur dan penggunaan teknologi terbuka. Pengembangan xApp dengan menggunakan open source memungkinkan kolaborasi yang lebih luas, biaya implementasi yang lebih murah, dan fleksibilitas dalam pengujian dan validasi keamanan. Hal ini sejalan dengan rekomendasi forum nasional untuk modernisasi aplikasi, seperti OpenGov Breakfast Insight 2024, yang menekankan betapa pentingnya beralih dari sistem lama ke infrastruktur yang lebih efisien, adaptif, dan aman \[12\].

4. # **ANALISIS UMUM**

Gambar. 1\. Analisis Umum

## 

## 

## **REFERENSI**

\[1\] M. Polese, L. Bonati, S. D’Oro, S. Basagni and T. Melodia, "Understanding O-RAN: Architecture, Interfaces, Algorithms, Security, and Research Challenges," IEEE Communications Surveys & Tutorials, vol. 25, no. 2, pp. 1376-1411, Secondquarter 2023, doi: 10.1109/COMST.2023.3239220.

\[2\] C. \-F. Hung, Y. \-R. Chen, C. \-H. Tseng and S. \-M. Cheng, "Security Threats to xApps Access Control and E2 Interface in O-RAN," IEEE Open Journal of the Communications Society, vol. 5, pp. 1197-1203, 2024, doi: 10.1109/OJCOMS.2024.3364840.

\[3\] M. Liyanage, et al., "Open RAN Security: Challenges and Opportunities," Journal of Network and Computer Applications, vol. 210, 103621, 2023, doi: 10.1016/j.jnca.2023.103621.

\[4\] Ericsson, "Evolving Open RAN Security," Ericsson Reports and Papers, 2023\. \[Online\]. Available: [https://www.ericsson.com/en/reports-and-papers/further-insights/evolving-open-ran-security](https://www.ericsson.com/en/reports-and-papers/further-insights/evolving-open-ran-security).

\[5\] Cybersecurity and Infrastructure Security Agency (CISA), "Open Radio Access Network Security Considerations," 2022\. \[Online\]. Available: [https://www.cisa.gov/sites/default/files/publications/open-radio-access-network-security-considerations\_508.pdf](https://www.cisa.gov/sites/default/files/publications/open-radio-access-network-security-considerations_508.pdf)

\[6\] Z. G. M. K. Khalfani, *Pengembangan dan Implementasi Jaringan Testbed 5G Standalone Berbasis srsRAN dan Open5GS*, Tugas Akhir, S1 Teknik Telekomunikasi, Institut Teknologi Bandung, Bandung, Indonesia, 2025\.

\[7\] B. I. Ahmad, Analisis dan Implementasi Core Network serta User Equipment pada Testbed Jaringan 5G Standalone, Tugas Akhir, S1 Teknik Telekomunikasi, Institut Teknologi Bandung, Bandung, Indonesia, 2025\.

\[8\] Opensource.com, “What is open source?,” *Opensource.com*, accessed Oct. 1, 2025\. \[Online\]. Available: [https://opensource.com/resources/what-open-source](https://opensource.com/resources/what-open-source) 

\[9\] S. Martinez, “Why Open Source Collaboration Is More Important Than Ever,” SUSE Communities, Feb. 3, 2025\. \[Online\]. Available: [https://www.suse.com/c/why-open-source-collaboration-is-more-important-than-ever/](https://www.suse.com/c/why-open-source-collaboration-is-more-important-than-ever/) 

\[10\] “Indonesian Cyber Security Strategy,” BSSN, \[Online\]. Available: [https://bssn.go.id/indonesian-cyber-security-strategy/](https://bssn.go.id/indonesian-cyber-security-strategy/?utm_source=chatgpt.com). 

\[11\] M. Somaraj, “Unveiling the Potential of Open-Source Software Integration in Education: Advantages, Challenges, and Effective Strategies,” *International Research Journal on Advanced Engineering and Management (IRJAEM)*, vol. 2, pp. 1309–1314, 2024, doi: 10.47392/IRJAEM.2024.0178.

\[12\] OpenGov Asia, “Exclusive: Indonesia’s digital revolution paves the way for open source and modernisation growth,” *OpenGov Asia*, Sep. 24, 2024\. \[Online\]. Available: [https://archive.opengovasia.com/2024/09/24/exclusive-indonesias-digital-revolution-paves-the-way-for-open-source-and-modernisation-growth-2/](https://archive.opengovasia.com/2024/09/24/exclusive-indonesias-digital-revolution-paves-the-way-for-open-source-and-modernisation-growth-2/)

## **LAMPIRAN 1\. FORMULIR PENDAFTARAN TUGAS AKHIR I & SEMINAR**

## **LAMPIRAN 2\. CURRICULUM VITAE**