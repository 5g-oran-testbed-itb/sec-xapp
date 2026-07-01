



Lembar Sampul Dokumen
	
Judul Dokumen
TUGAS AKHIR TEKNIK TELEKOMUNIKASI:
Pengembangan Prototipe Agentic AI dengan Local-LLM Engine untuk Analisis Jaringan & Konfigurasi




Jenis Dokumen
PENGEMBANGAN DETIL DESAIN DAN IMPLEMENTASI 




Catatan: Dokumen ini dikendalikan penyebarannya oleh Prodi Teknik Telekomunikasi ITB
Nomor Dokumen
T40-01-TA2026.YY.ZZ




Nomor Revisi
01




Nama File
T40




Tanggal Penerbitan
11 Maret 2026




Unit Penerbit
Prodi Teknik Telekomunikasi – ITB




Jumlah Halaman
                         
(termasuk lembar sampul ini)


Data Pengusul
Pengusul
Nama
Yeremias Mangu
Jabatan 
Anggota Kelompok


NIM
18122010
Tanda Tangan





Nama
Achmad Shafwan Zuhair
Jabatan
Anggota Kelompok


NIM
18122017
Tanda Tangan



Pembimbing
Nama
Dr.-Ing. Eueung Mulyana, S.T,
M.Sc


Tanda Tangan



NIP
197412182008011000




Lembaga






Program Studi Teknik Telekomunikasi Sekolah Teknik Telekomunikasi dan Informatika
Institut Teknologi Bandung
Alamat






Labtek 8, Lantai 4, Jalan Ganesha no. 10, Bandung
Telepon : +62 22 250 0962,
+62 22 250 1661
Faks :+62 22 250 0962
Email:stei@stei.itb.ac.id




1 Pengantar	3
1.1 Ringkasan Isi Dokumen	3
1.2 Tujuan Penulisan dan Aplikasi/Kegunaan Dokumen	3
1.3 Daftar Singkatan	3
2 Pemodelan Desain	4
2.1 Pemodelan Fungsional Sistem	4
2.1.1 Subsistem Interaksi (Frontend)	4
2.1.2 Subsistem Analisis dan Inferensi (Backend-AI)	4
2.1.3 Subsistem Konfigurasi dan Validasi (Backend-NetOps)	5
2.2 Pemodelan Tingkah Laku Sistem	6
2.2.1 Diagram Konteks (Level 0)	6
2.2.2 Perilaku Sistem Keseluruhan (Level 1)	7
2.2.3 Tingkah Laku Spesifik (Level 2)	7
3 Implementasi Desain	10
3.1 Sub-Sistem Analisis dan Inferensi (Backend-AI)	10
3.1.1  LLM Engineer (Ollama)	10
3.1.2  Analisis Traffic	11
3.2 Sub-Sistem Konfigurasi dan Validasi (Backend-NetOps)	13
4 Analisis Pengerjaan Implementasi	14
Referensi	15










Pengantar
Ringkasan Isi Dokumen
Isi dokumen T40 ini berisi pemodelan dan implementasi desain dari tugas akhir dengan judul “Pengembangan Prototipe Agentic AI dengan Local-LLM Engine untuk Analisis Jaringan & Konfigurasi”. Akan dijelaskan lebih rinci mengenai fungsional sistem, tingkah laku sistem, serta bagaimana implementasi desain yang telah penulis rancang sampai dengan dokumen ini ditulis.
Tujuan Penulisan dan Aplikasi/Kegunaan Dokumen
Bagian ini berisi tujuan/maksud penulisan dokumen ini, dan ditujukan kepada siapa. Contoh:
Tujuan penulisan dokumen ini adalah sebagai berikut:
Sebagai gambaran umum dari proyek yang akan dikerjakan dari segi teknis dan non teknis
Untuk memastikan bahwa tugas akhir ini adalah sesuatu yang layak untuk dikerjakan
Sebagai catatan dari proses pengerjaan dan catatan revisi yang dilakukan.
Dokumen ini ditujukan kepada dosen pembimbing tugas akhir dan tim tugas akhir Program Studi Teknik Telekomunikasi ITB sebagai bahan penilaian tugas akhir.

Daftar Singkatan
Singkatan
Arti






















Pemodelan Desain 
Pada tahap ini penulis menggunakan alternatif desain yang telah dirancang pada dokumen sebelumnya, yaitu dengan menggunakan Ollama sebagai platform runtime LLM dan mengintegrasikannya dengan MCP sebagai middleware. Untuk tampilan antarmuka akan menggunakan webapp berbasis React.js dan GNS-3 sebagai simulator jaringan. Semua kegiatan yang dilakukan dalam proses desain harus tercatat di dalam dokumen ini.
Pemodelan Fungsional Sistem
2.1.1 Subsistem Interaksi (Frontend)
Subsistem interaksi (Frontend) merupakan subsistem yang menangani antarmuka interaktif bagi pengguna untuk berinteraksi dengan sistem. Subsistem ini berfungsi sebagai gerbang utama komunikasi antara pengguna dengan sistem untuk kebutuhan pemantauan dan konfigurasi jaringan. Antarmuka pengguna pada sistem ini dikembangkan menggunakan kerangka kerja React.js, sebuah pustaka JavaScript yang dirancang untuk membangun antarmuka pengguna yang responsif melalui pendekatan berbasis komponen. Penggunaan React memungkinkan integrasi data JSON dari API secara dinamis untuk menyajikan visualisasi statistik dan percakapan asisten secara real-time [1].

Gambar 1. Diagram alir subsistem Frontend.
Subsistem ini menerima masukan berupa prompt dan unggahan file dari pengguna serta data yang dikirimkan oleh subsistem lainnya. Masukan tersebut akan diolah oleh subsistem untuk dapat menampilkan antarmuka yang interaktif berisi visualisasi performa jaringan, tampilan percakapan, dan inventaris perangkat. Subsistem ini juga menangani fungsi meneruskan chat request ke subsistem Backend-AI melalui API request. Selain itu, subsistem ini juga menangani contextual switching antara engineer agent dan analyst agent yang ada pada subsistem Backend-AI.
2.1.2 Subsistem Analisis dan Inferensi (Backend-AI)
Subsistem analisis dan inferensi (Backend-AI) merupakan subsistem yang menangani proses penalaran dan inferensi oleh AI sesuai kebutuhan. Dalam subsistem ini terdapat dua ai agent yang memiliki peran berbeda. Agent pertama adalah engineer agent yang memiliki peran untuk menangani permintaan pengguna melalui percakapan interaktif untuk berkonsultasi mengenai jaringan maupun menangani perintah pengguna untuk melakukan analisis dan konfigurasi pada jaringan tertentu. Agent kedua adalah analyst agent yang memiliki peran untuk menganalisis performa jaringan melalui network telemetry dan memberikan peringatan serta saran perbaikan apabila ditemukan adanya anomali.


Gambar 2. Diagram alir subsistem Backend-AI engineer agent.
Engineer agent akan menerima masukan berupa permintaan percakapan (chat request) dari subsistem Frontend serta data pengetahuan teknis berupa file dan riwayat percakapan (RAG) untuk memperkaya konteks. Mekanisme Retrieval-Augmented Generation (RAG) mengambil konteks dari dokumen eksternal secara akurat sesuai dengan kebutuhan pengguna. Arsitektur ini mengadopsi konsep Agentic AI yang memungkinkan asisten cerdas melakukan penalaran berulang dan tindakan mandiri [2]. Permintaan ini kemudian akan diproses NLU dan dilakukan reasoning untuk mendapatkan langkah aksi atau jawaban. Jika dari hasil reasoning didapati bahwa permintaan pengguna memerlukan tools untuk melakukan aksi, maka agent ini akan meminta subsistem Backend-NetOps melakukan tool call. Setelah output didapat, subsistem Backend-AI akan mengolah jawaban serta menampilkannya kembali ke subsistem Frontend.

Gambar 3. Diagram alir subsistem Backend-AI analyst agent.
Analyst agent akan menerima masukan berupa data inventaris perangkat serta data performa jaringan melalui network telemetry dari perangkat jaringan langsung. Agent ini akan mengolah data-data tersebut dan menganalisis apakah ada anomali yang terdeteksi. Jika ada anomali terdeteksi, agent akan melakukan reasoning untuk mencari jawaban atau langkah aksi yang diperlukan dan mengirimkannya ke subsistem Frontend.
2.1.3 Subsistem Konfigurasi dan Validasi (Backend-NetOps)
Subsistem konfigurasi dan validasi (Backend-NetOps) merupakan subsistem yang berfungsi sebagai eksekutor teknis yang menghubungkan antara logika AI dengan infrastruktur jaringan nyata melalui perintah CLI. Subsistem ini utamanya didukung oleh Model Context Protocol (MCP) yang menyediakan abstraksi baku agar LLM dapat memanggil fungsi teknis secara terstruktur. Dalam melakukan konfigurasi perangkat jaringan melalui jalur SSH, subsistem ini menggunakan pustaka Netmiko yang mendukung pengelolaan multi-vendor untuk berbagai perangkat [3].
 

Gambar 4. Diagram alir subsistem Backend-NetOps.
Subsistem ini menerima masukan berupa permintaan perintah CLI dari subsistem Backend-AI serta data inventaris perangkat dan memprosesnya untuk memilih tools apa yang dibutuhkan. Setelah menentukan, subsistem ini akan membuka jalur komunikasi dengan perangkat jaringan untuk eksekusi tools. Setelah berhasil, subsistem ini akan mengambil dan membersihkan data output dari perangkat, log eksekusi, dan status perangkat dan mengirimkannya kembali ke subsistem Backend-AI untuk dianalisis kembali.
Pemodelan Tingkah Laku Sistem
Pemodelan tingkah laku menggambarkan bagaimana data mengalir secara dinamis dan bagaimana sistem merespons berbagai kondisi operasional. Dalam hal ini, digunakan Data Flow Diagram (DFD) dengan tingkatan level dari nol sampai dengan dua. 
2.2.1 Diagram Konteks (Level 0)

Gambar 5. Diagram konteks DFD level 0.
Tingkah laku sistem secara keseluruhan dimulai dari tingkat paling umum pada diagram konteks (DFD level 0), yang menetapkan batasan interaksi antara entitas eksternal dengan sistem asisten AI. Pada diagram konteks, aliran data bersifat makro. Pengguna memberikan input berupa prompt dan dokumen referensi, sementara perangkat jaringan memberikan umpan balik berupa data telemetri dan hasil keluaran perintah. Sistem kemudian merespons dengan menyajikan visualisasi dashboard dan balasan konsultasi yang telah diolah. 
2.2.2 Perilaku Sistem Keseluruhan (Level 1)

Gambar 6. Diagram perilaku sistem DFD level 1.
Pemodelan tingkah laku sistem pada DFD Level 1 menggambarkan koordinasi dinamis antar-subsistem dalam memproses instruksi pengguna hingga tahap eksekusi pada infrastruktur jaringan. Alur kerja dimulai saat pengguna memberikan masukan berupa prompt dan file kontekstual melalui subsistem Frontend, yang secara simultan menyajikan tampilan antarmuka interaktif kembali kepada pengguna. Masukan tersebut kemudian dipaketkan oleh subsistem Frontend menjadi sebuah chat request yang berisi informasi prompt, mode agen yang dipilih (Analyst atau Engineer), serta konteks site jaringan untuk dikirimkan ke subsistem Backend-AI.
Di dalam subsistem Backend-AI, sistem melakukan proses penalaran dengan memanfaatkan data pendukung dari data history chat untuk menjaga kesinambungan percakapan serta RAG Files untuk mendapatkan referensi dokumen teknis yang relevan. Apabila hasil penalaran menunjukkan kebutuhan akan aksi teknis, Backend-AI akan mengirimkan tool request kepada subsistem Backend-NetOps. Selanjutnya, Backend-NetOps akan mengakses data kredensial dari database inventaris perangkat untuk mengeksekusi CLI command secara langsung pada perangkat jaringan fisik maupun virtual.
Siklus ini ditutup dengan pengiriman umpan balik berupa raw output dan network telemetry dari perangkat kembali ke subsistem Backend-AI untuk diinterpretasikan menjadi jawaban final. Hasil analisis yang telah diolah tersebut kemudian diteruskan ke subsistem Frontend agar dapat dibaca oleh pengguna. Selain alur percakapan tersebut, sistem juga mengelola fungsi administratif di mana subsistem Frontend dapat secara langsung menampilkan dan mengelola data inventaris perangkat pada pusat penyimpanan data terkait untuk kebutuhan operasional harian.
2.2.3 Tingkah Laku Spesifik (Level 2)
Pemodelan tingkah laku pada DFD Level 2 memberikan gambaran yang lebih mendalam dan spesifik mengenai prosedur operasional internal pada fungsi-fungsi kritis sistem. Jika Level 1 berfokus pada interaksi antar-subsistem secara umum, Level 2 ini menguraikan logika pengambilan keputusan, aliran data pada database tertentu, serta mekanisme keamanan yang diterapkan untuk memastikan sistem bekerja sesuai dengan batasan yang telah ditetapkan dalam persyaratan desain.

Gambar 7. Tingkah laku penanganan administratif perangkat.
Tingkah laku ini menjelaskan bagaimana sistem menangani data administratif perangkat jaringan yang disimpan dalam file inventaris. Proses dimulai ketika user mengisi formulir penambahan atau penghapusan perangkat melalui subsistem Frontend. Data tersebut dikirimkan sebagai tool request ke subsistem Backend-NetOps.
Pada proses ini Backend-NetOps melakukan tindakan langsung berupa menulis atau menghapus entri pada repositori Inventaris perangkat (file TOML). Jika proses tersebut merupakan inisialisasi perangkat baru, sistem juga akan mengirimkan initial configuration langsung ke perangkat jaringan. Setelah perubahan pada file inventaris selesai dilakukan, data terbaru akan diumpan balikkan ke subsistem Frontend untuk melakukan penyegaran (refresh) tampilan inventaris bagi pengguna.

Gambar 8. Tingkah laku penanganan file untuk RAG.
Proses ini menggambarkan transformasi file mentah menjadi pengetahuan terstruktur yang dapat dipahami oleh AI. Tingkah laku ini dipicu ketika pengguna melakukan unggahan file kontekstual (seperti dokumentasi vendor atau topologi) ke subsistem Frontend. Data file tersebut kemudian diteruskan ke subsistem Backend-AI untuk menjalani proses integrasi pengetahuan.
Di dalam Backend-AI, file melalui tahapan "Chunking dan Embedding", proses ketika teks dipecah menjadi potongan kecil dan dikonversi menjadi vektor numerik agar mudah dicari [2]. Hasil dari proses ini kemudian disimpan ke dalam repositori RAG Files menggunakan ChromaDB sebagai basis data vektor pendukung [4]. Sebagai bentuk konfirmasi, sistem akan mengirimkan informasi data terbaru kembali ke Frontend untuk memperbarui daftar file yang tersimpan dan dapat diakses oleh pengguna.

Gambar 9. Tingkah laku penanganan mode eksekusi.
Tingkah laku ini merupakan mekanisme pertahanan utama sistem dalam membedakan antara instruksi aman dan instruksi yang berpotensi mengubah konfigurasi jaringan. Alur dimulai saat pengguna memberikan prompt yang diproses oleh Backend-AI dengan merujuk pada History chat dan RAG Files.
Sistem kemudian melakukan evaluasi kritis melalui dua tahapan logika:
Pengecekan Sifat Perintah: Sistem memeriksa apakah permintaan tersebut bersifat read-only (hanya melihat data). Jika Ya, sistem langsung mengirimkan tool request ke Backend-NetOps untuk dieksekusi.
Validasi Mode Eksekusi: Jika perintah bersifat read-write (perubahan konfigurasi), sistem akan memeriksa status Execution Mode yang aktif pada antarmuka. Apabila mode eksekusi aktif (Ya), perintah diteruskan ke Backend-NetOps. Namun, jika mode tersebut tidak aktif (Tidak) yang berarti sistem dalam Consult Mode, sistem akan menunjukkan perilaku protektif dengan menyatakan bahwa "Permintaan ditolak" guna menjaga keamanan infrastruktur.
Hasil akhir dari setiap perintah yang berhasil lolos validasi (berupa raw output) akan dikembalikan ke Backend-AI untuk diolah kembali menjadi jawaban yang mudah dipahami sebelum ditampilkan kepada pengguna di Frontend.
Implementasi Desain
Uraikan pekerjaan implementasi semua bagian sistem yang telah dirancang, misalnya  membuat prototype, simulasi, pengukuran, dll. Pekerjaan yang didokumentasikan adalah pekerjaan terkini dari setiap sub-sistem. Jika ada perbaikan atau pengulangan implementasi, dituliskan di dokumen versi selanjutnya. 
Kaitkan pekerjaan implementasi yang harus diambil dengan persyaratan desain (objektif, constraint, fungsi) yang sudah dibuat sebelumnya pada dokumen T20. Penjelasan implementasi desain dilakukan untuk tiap subsistem. 
Sub-Sistem Analisis dan Inferensi (Backend-AI) 
3.1.1 	LLM Engineer (Ollama)
Pada subsistem Backend-AI, komponen utama yang digunakan untuk melakukan analisis dan inferensi adalah LLM engine yang diimplementasikan menggunakan Ollama. Tujuan menggunakan Ollama adalah agar sistem yang dikembangkan mampu menjalankan LLM baik secara lokal maupun berbasis cloud, sehingga dapat mendukung kebutuhan sistem yang berbiaya rendah. 
	
Gambar 10. Tampilan Ollama pada Ubuntu
Pada Gambar 10, terlihat tampilan Ollama pada sistem operasi Ubuntu yang digunakan untuk menjalankan dan mengelola model LLM sescara lokal. Saat ini, beberapa model sudah terinstall dan dapat digunakan pada sistem seperti GPT-OSS dan Qwen3.5. 



Gambar 11. 
Terlihat implementasi kode pada Gambar 11, yang mengintegrasikan LLM melalui Ollama menggunakan AsyncClient. Kode tersebut menunjukkan proses inisialisasi koneksi ke layanan Ollama serta penggunaan berbagai library pendukung seperti FastAPI.

3.1.2 	Analisis Traffic
Analisis traffic merupakan salah satu fungsi yang ditempatkan pada Backend-AI karena proses ini membutuhkan kemampuan interpretasi dan pengambilan keputusan berbasis AI. Data trafik jaringan seperti log, flow, dan metrik performa tidak hanya disimpan di database, tetapi juga harus dianalisis untuk menemukan pola, anomali, serta potensi permasalahan yang terjadi pada jaringan. 

Gambar 12. File kode program untuk Analisis Agent

Terlihat implementasi kode program untuk analis agent yang  merupakan inti dari proses analisis pada subsistem Backend-AI yang berperan sebagai agent cerdas dalam mengolah data jaringan. Komponen ini mengintegrasikan berbagai sumber data seperti SNMP, CLI perangkat, syslog, serta playbook untuk membentuk konteks analisis yang komprehensif. Selanjutnya, data tersebut diproses dan dikirim ke model LLM melalui Ollama untuk menghasilkan root cause analysis, rekomendasi perbaikan, serta tingkat keparahan permasalahan. Selain itu, modul ini juga mendukung verifikasi konfigurasi perangkat secara otomatis, sehingga tidak hanya berfungsi sebagai alat analisis, tetapi juga sebagai mekanisme validasi dalam pengelolaan jaringan. Untuk mendukung proses analisis tersebut, sistem memanfaatkan berbagai sumber data jaringan seperti Telemetry, Netflow, SNMP, dan Syslogyang berperan sebagai input utama yang kemudian digunakan oleh analis agent untuk menganalisis jaringan.
 

Gambar 13. Kode Program Telemetry Collector

Kode program di Gambar 13, menunjukkan implementasi Telemetry Collector yang berfungsi untuk menerima dan memproses data telemetri jaringan secara real-time melalui protokol UDP. Sistem dikonfigurasi untuk mendengarkan pada port tertentu seperti port 57500 guna menerima data dari perangkat jaringan.

Gambar 14. Kode Program Netflow Collector

Kode program Gambar 14, menunjukkan implementasi Netflow Collector yang berfungsi untuk mengumpulkan, memproses, dan menganalisis data aliran trafik jaringan. Netflow tidak hanya mengambil data, tapi mengolahnya menjadi informasi yang lebih terstruktur seperti alamat IP sumber dan tujuan, port, protokol, jumlah paket, serta volume data yang ditransmisikan.Selain itu, sistem juga menyediakan berbagai fungsi untuk melakukan filtering, pembatasan jumlah data, serta agregasi trafik ke dalam bentuk time-series. Hal ini memungkinkan analisis dilakukan tidak hanya secara statis, tetapi juga berdasarkan perubahan trafik dari waktu ke waktu.

Gambar 15. Kode Program SNMP Collector

Kode program Gambar 15, menunjukkan implementasi SNMP Collector yang digunakan untuk mengumpulkan data jaringan melalui protokol SNMP. Protokol ini menangani dua mekanisme, yaitu menerima SNMP trap pada port UDP 1162 serta polling data secara periodik untuk memperoleh informasi seperti jumlah paket masuk dan keluar.


Gambar 16. Kode Program Syslog Server
 
Kode menunjukkan implementasi dari Syslog Server yang berfungsi untuk menerima dan memproses log dari perangkat jaringan. Syslog menyimpan data log dalam struktur buffer berdasarkan alamat IP perangkat serta mencatat statistik seperti jumlah log yang diterima, waktu kejadian, dan distribusi berdasarkan tingkat keparahan. Terdapat pemetaan severity dan facility berdasarkan standar RFC 5424, yang digunakan untuk mengklasifikasikan jenis log yang diterima,. 

3.1.3 	Knowledge Base / RAG
Implementasi Retrieval-Augmented Generation (RAG) pada backend-AI digunakan untuk meningkatkan kualitas dan akurasi hasil analisis yang dihasilkan oleh LLM. Sistem menggunakan ChromaDB sebagai knowledge base untuk menyimpan berbagai referensi teknis, seperti dokumentasi vendor, best practice konfigurasi, serta solusi terhadap permasalahan jaringan.

Gambar 17. Kode Program ChromaDB

Kode program pada Gambar 17, menunjukkan implementasi knowledge base menggunakan ChromaDB sebagai bagian dari pendekatan RAG pada Backend-AI. Sistem melakukan inisialisasi vector store dengan direktori penyimpanan lokal serta menggunakan model embedding SentenceTransfomer untuk mengubah teks menjadi representasi vektor. sistem membangun dua koleksi utama, yaitu network_docs sebagai penyimpanan dokumen teknis dan chat_memory sebagai penyimpanan histori interaksi. Kedua koleksi ini memungkinkan sistem menyimpan dan mengambil informasi secara kontekstual berdasarkan kemiripan makna.

3.1.4 	Proses Inferensi
Inferensi merupakan tahapan utama pada Backend-AI yang bertugas menghasilkan respons berdasarkan input pengguna dan konteks yang telah disiapkan sebelumnya. Pada tahap ini, model LLM memproses pesan yang diterima, memahami maksud user, memanfaatkan konteks tambahan seperti hasil RAG, lalu menyusun keluaran yang relevan dalam bentuk analisis, rekomendasi, atau jawaban teknis.

Gambar 18. Kode Program Inferensi

Pada Gambar 18, menunjukkan alur inferensi yang diawali dengan messages yang terdiri dari system prompt dan intruksi tambahan berbasis RAG, sehingga model memiliki konteks yang cukup sebelum dianalisis. Sistem melakukan pemanggilan model LLM melalui Ollama menggunakan async_ollama.chat, dengan paramater seperti model yang digunakan, daftar pesan, serta konfigurasi seperti temperature.
Sub-Sistem Konfigurasi dan Validasi (Backend-NetOps)
3.2.1 Middleware
Sistem menggunakan kerangka kerja Model Context Protocol (MCP) sebagai lapisan middleware utama untuk menjembatani antara Backend-AI dengan infrastruktur jaringan fisik maupun simulator. Penggunaan MCP memungkinkan sistem untuk mengabstraksikan berbagai protokol manajemen jaringan seperti SSH, Telnet, dan Serial ke dalam bentuk antarmuka standar yang dapat digunakan secara langsung oleh agent LLM.

Gambar 19. Kode Program Integrasi Middleware
Kode program pada Gambar 19 merupakan proses integrasi middleware dimulai dengan pendefinisian konfigurasi server. Sistem menginisialisasi tiga modul utama yang berfungsi sebagai jembatan komunikasi, yaitu modul serial untuk koneksi konsol langsung, modul netmiko untuk manajemen berbasis SSH, dan modul telnet untuk akses konsol emulator GNS3. Setiap modul dikonfigurasi dengan parameter perintah eksekusi dan path skrip Python yang spesifik, sehingga memungkinkan Backend-AI memiliki jalur akses yang lengkap ke berbagai jenis antarmuka perangkat pada simulator ataupun perangkat fisik tanpa memerlukan pengaturan manual tambahan dari sisi user.



Gambar 20. Kode Program Netmiko MCP
Kode pada Gambar 20 tersebut merepresentasikan implementasi layanan MCP Netmiko Server sebagai komponen eksekusi dalam subsistem Backend-NetOps. MCP ini memanfaatkan library Netmiko untuk membangun koneksi ke perangkat jaringan melalui protokol SSH, sehingga memungkinkan sistem menjalankan perintah konfigurasi secara langsung. Integrasi dengan framework FastMCP memungkinkan fungsi-fungsi dalam server diekspos sebagai tool yang dapat dipanggil oleh LLM melalui middleware. Selain itu, sistem dilengkapi dengan mekanisme connection pool untuk mengelola koneksi secara efisien tanpa perlu melakukan koneksi ulang berulang kali. MCP Netmiko berperan sebagai jembatan antara LLM dan perangkat jaringan yang terhubung melalui SSH.
Gambar 21. Kode Program Serial MCP
Kode program pada Gambar 21 menunjukkan implementasi dari MCP Serial yang berfungsi untuk menyediakan komunikasi langsung ke perangkat jaringan melalui koneksi serial. Modul ini memungkinkan sistem untuk berinteraksi dengan perangkat yang diakses melalui console perangkat fisik, terutama pada kondisi awal konfigurasi atau saat akses jaringan belum tersedia. Sama seperti MCP Netmiko, MCP ini dibangun menggunakan framework FastMCP, sehingga dapat diintegrasikan sebagai tool yang dapat dipanggil oleh LLM melalui middleware. Selain itu, terdapat konfigurasi parameter seperti serial port, baud rate, dan ukuran buffer yang dapat disesuaikan melalui environment variable, sehingga memberikan fleksibilitas penggunaan.
Gambar 22. Kode Program Telnet MCP
Kode pada Gambar 22 merupakan implementasi dari MCP Telnet yang digunakan untuk menyediakan akses dan eksekusi perintah ke perangkat jaringan melalui protokol Telnet. MCP ini juga dibangun menggunakan framework FastMCP, sehingga fungsi-fungsi yang tersedia dapat diintegrasikan sebagai tool yang dipanggil oleh middleware dalam subsistem Backend-NetOps. Selain itu, modul ini juga memiliki mekanisme logging untuk mencatat aktivitas komunikasi, serta fungsi pembersihan karakter ANSI dan normalisasi teks konsol. Proses ini penting agar output yang diterima dari perangkat menjadi lebih bersih, terstruktur, dan mudah diproses oleh sistem, terutama ketika hasil eksekusi akan diteruskan kembali ke LLM atau antarmuka pengguna.
Dengan implementasi middleware ini, sistem mampu beroperasi secara otonom dalam melakukan pengambilan data maupun pengiriman konfigurasi tanpa memerlukan intervensi manual dari pengguna. Hal ini menjamin bahwa setiap instruksi bahasa natural yang diproses oleh subsistem AI dapat diterjemahkan dan dieksekusi secara presisi ke node jaringan secara real-time.

3.2.2 	Validasi dan Verifikasi Konfigurasi
Validasi konfigurasi dan verifikasi sintaks merupakan tahap penting dalam subsistem Backend-NetOps yang bertujuan untuk memastikan bahwa perintah konfigurasi yang dihasilkan dan dieksekusi telah sesuai dengan standar serta tidak menimbulkan kesalahan pada perangkat jaringan. Proses ini dilakukan setelah tahap eksekusi, dengan cara memeriksa kesesuaian sintaks perintah, serta mengevaluasi apakah konfigurasi yang diterapkan telah berjalan sebagaimana mestinya. 
Gambar 23. Kode Program Logika Verifikasi Agent

Kode program pada Gambar 23 digunakan untuk mengatur perilaku LLM dalam melakukan proses konfigurasi dan validasi jaringan. Model LLM tidak hanya memberikan rekomendasi, tetapi bertindak sebagai eksekutor yang melakukan tindakan serta verifikasi secara langsung terhadap konfigurasi yang diterapkan. 

Sub-Sistem Interaksi (Frontend)
3.3.1 Arsitektur Frontend
Sub-sistem frontend dibangun menggunakan framework React.js dengan dukungan build tool Vite untuk menghasilkan performa yang optimal serta mendukung pengembangan berbasis komponen yang modular. Arsitektur yang digunakan berupa Single Page Application (SPA), sehingga seluruh interaksi pengguna dapat dilakukan dalam satu halaman tanpa perlu melakukan reload, dan berfungsi sebagai command center dalam mengoperasikan sistem.
Frontend berkomunikasi dengan Backend-AI melalui API Bridge menggunakan protokol HTTP REST untuk pengelolaan data statis, serta Server-Sent Events (SSE) untuk menerima aliran respons AI secara real-time. Sementara itu, interaksi dengan subsistem Backend-NetOps dilakukan secara tidak langsung melalui Backend-AI sebagai orkestrator, di mana setiap hasil eksekusi konfigurasi maupun analisis jaringan akan diteruskan kembali ke frontend untuk divisualisasikan dalam bentuk respons chat kepada pengguna.
.

3.3.2 User Interface
3.3.3 Interaksi Chat dengan Agent
3.3.4 Integrasi Frontend dengan Backend
3.3.5 Feedback Sistem




Tuliskan rencana implementasi sub-sistem yang telah dirancang. Sertakan gambar layout, source code, atau dokumentasi lainnya yang berhubungan dengan implementasi tersebut.



Analisis Pengerjaan Implementasi

Bagian ini berisi analisis perbandingan antara timeline yang sudah direncanakan pada dokumen T20 dengan implementasi sebenarnya. Terdapat tracker progres aktual sejak awal pengerjaan sampai dengan dokumen ini ditulis serta terdapat visualisasi pengerjaan implementasi dalam bentuk s-curve.


Pengembangan prototipe agentic AI dengan local-LLM engine untuk analisis jaringan & konfigurasi dimulai sesuai dengan timeline yang direncanakan. Progres pengerjaan di minggu 10 sampai dengan minggu ke 22 lebih cepat dibandingkan dengan timeline karena penulis segera memulai pengembangan prototipe mendahului rencana timeline awal. Namun pada minggu 23 sampai dengan minggu ke 26 sedikit terjadi keterlambatan karena terpotong libur serta hari raya. Pada akhirnya, sampai dokumen ini ditulis, pengerjaan implementasi kembali sesuai dengan rencana timeline dan prototipe selesai dibuat. Langkah selanjutnya adalah melakukan pengambilan data dan analisis serta revisi dan penyelesaian dokumen tugas akhir.

Referensi
[1]	Facebook Open Source, "React: A JavaScript library for building user interfaces," [Online]. Available: https://reactjs.org. 
[2] 	P. Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks," in Advances in Neural Information Processing Systems, vol. 33, 2020, pp. 9459-9475.
[3] 	Anthropic, "Model Context Protocol Introduction," [Online]. Available: https://modelcontextprotocol.io, 2024. 
[4]	ChromaDB Team, "Chroma: The AI-native open-source embedding database," 2023. [Online]. Available: https://www.trychroma.com.















Lampiran
Lampirkan dokumen pendukung yang terkait, misalnya dokumen standard yang terkait produk ini serta dokumen rujukan biaya.

