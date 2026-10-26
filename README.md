# 📖 fliphtml-downloader

[![tests](https://github.com/prob1st/fliphtml-downloader/actions/workflows/test.yml/badge.svg)](https://github.com/prob1st/fliphtml-downloader/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![license: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**FlipHTML5** flipbook'larını terminalden indirin: sayfa görselleri + tek **PDF** (isterseniz **CBZ**).
Tarayıcı yok, Node.js yok — saf Python.

> 🌐 **Web sürümü / Web version:** https://fliphtml-downloader-xi.vercel.app — kurulum yapmadan tarayıcıda indir!
>
> 🇬🇧 English documentation below · [Türkçe](#-türkçe) · [English](#-english)

---

## 🇹🇷 Türkçe

### Özellikler

- 🔗 Bağlantıyı yapıştırın, gerisini o halletsin: tam URL **veya** sadece `xxxxx/yyyy` kitap kimliği
- 🔓 **Yeni (şifreli) ve eski format** kitapların ikisini de destekler — şifreli sayfa listesi, FlipHTML5'in kendi `deString` WebAssembly çözücüsü `wasmtime` ile yerelde çalıştırılarak açılır
- ⚡ Paralel indirme, otomatik tekrar deneme ve **kaldığı yerden devam** (var olan sayfalar tekrar indirilmez)
- 🖼️ Önce `large`, olmazsa `medium`/`thumb` ve webp↔jpg yedeklerini dener
- 📄 Çıktılar: sayfa görselleri klasörü + **PDF**, isterseniz **CBZ** (çizgi roman arşivi)
- 🎚️ Sayfa aralığı (`--start` / `--end`), kalite ve iş parçacığı sayısı ayarlanabilir
- 🕰️ Çok eski kitaplarda config bulunamazsa sayfaları tek tek yoklayan yedek mod
- 🇹🇷🇬🇧 Türkçe/İngilizce arayüz (`--lang en`)

### Kurulum

Python 3.10+ gerekir.

```bash
git clone https://github.com/prob1st/fliphtml-downloader.git
cd fliphtml-downloader
pip install -r requirements.txt
```

ya da komut olarak kurmak için:

```bash
pip install .
```

### Kullanım

```bash
# Bağlantıyla
python fliphtml_downloader.py "https://fliphtml5.com/xxxxx/yyyy"

# Sadece kitap kimliğiyle
python fliphtml_downloader.py xxxxx/yyyy

# Argümansız çalıştırırsanız bağlantıyı sorar
python fliphtml_downloader.py
```

> Kitap kimliği, bağlantının `fliphtml5.com/` kısmından sonraki iki parçadır.
> Örn. `https://online.fliphtml5.com/hgzok/boba/` → `hgzok/boba`
> Bookcase (kitaplık) bağlantıları değil, kitabın kendi bağlantısını kullanın.

Sonuç varsayılan olarak `downloads/` altına yazılır:

```
downloads/
├── Kitap Başlığı.pdf
└── Kitap Başlığı/
    ├── 001.webp
    ├── 002.webp
    └── ...
```

### Seçenekler

| Seçenek | Açıklama | Varsayılan |
|---|---|---|
| `-o, --out DİZİN` | Çıktı klasörü | `downloads` |
| `--pdf DOSYA` | PDF dosya adı/yolu | `<kitap başlığı>.pdf` |
| `--no-pdf` | PDF oluşturma, sadece görselleri indir | kapalı |
| `--cbz` | Ayrıca CBZ arşivi de oluştur | kapalı |
| `--start N` / `--end N` | İndirilecek sayfa aralığı | tümü |
| `--quality large\|medium\|thumb` | Tercih edilen görsel boyutu | `large` |
| `--workers N` | Paralel indirme sayısı | `8` |
| `--jpeg-quality 1-100` | PDF için JPEG kalitesi | `92` |
| `--overwrite` | Var olan sayfaları da yeniden indir | kapalı |
| `--no-keep-images` | PDF/CBZ sonrası görselleri sil | kapalı |
| `--lang tr\|en` | Arayüz dili | `tr` |
| `--config DOSYA` | Yerel `config.js` kullan (hata ayıklama) | — |

Örnekler:

```bash
# Sadece 10-25 arası sayfaları, CBZ olarak da
python fliphtml_downloader.py xxxxx/yyyy --start 10 --end 25 --cbz

# Daha küçük PDF için kaliteyi düşür, görselleri saklama
python fliphtml_downloader.py xxxxx/yyyy --jpeg-quality 70 --no-keep-images
```

### Nasıl çalışıyor?

1. Adres `online.fliphtml5.com` biçimine çevrilir, kitabın `javascript/config.js` dosyası çekilir.
2. Sayfa listesi (`fliphtml5_pages`) düz metinse doğrudan okunur; şifreliyse FlipHTML5'in resmi `deString.js` dosyası bir kez önbelleğe indirilir, içindeki WASM modülü `wasmtime` ile çalıştırılarak liste çözülür.
3. Sayfalar `files/large/` altından paralel indirilir, görsel oldukları doğrulanır.
4. Görseller `img2pdf` ile tek PDF'e birleştirilir.

### Sorun giderme

- **"Sayfa listesi bulunamadı"** → Kitap gizli, kaldırılmış veya şifre korumalı olabilir. Bookcase bağlantısı yerine kitabın doğrudan bağlantısını kullandığınızdan emin olun.
- **`pip install wasmtime` hatası** → Şifreli kitaplar için bu paket şart: `pip install -r requirements.txt`
- **Bazı sayfalar atlandı** → Program `large → medium → thumb` ve webp/jpg yedeklerinin hepsini dener; hiçbiri yoksa sayfayı atlayıp sonunda raporlar. Aynı komutu tekrar çalıştırmak eksikleri tamamlamayı dener (var olanlar indirilmez).
- **Büyük kitaplar** (500+ sayfa) birkaç dakika ve ciddi disk alanı kullanabilir.

### Testler

```bash
pip install pytest
pytest -q
```

---

## 🇬🇧 English

Download public **FlipHTML5** flipbooks from the terminal: page images + a single **PDF** (optionally a **CBZ**). No browser, no Node.js — pure Python.

### Features

- 🔗 Paste a full URL **or** just the `xxxxx/yyyy` book ID
- 🔓 Supports both **legacy and new (encrypted-manifest) books** — the encrypted page list is decoded locally by running FlipHTML5's own `deString` WebAssembly module through `wasmtime`
- ⚡ Parallel downloads with retries and **resume** (existing pages are skipped)
- 🖼️ Falls back through `large → medium → thumb` and webp↔jpg variants
- 📄 Outputs: a folder of page images + **PDF**, optionally **CBZ**
- 🎚️ Page range, quality and thread count are configurable
- 🕰️ Legacy probing mode for very old books without a usable config
- 🇹🇷🇬🇧 Turkish/English UI (`--lang en`)

### Installation

Requires Python 3.10+.

```bash
git clone https://github.com/prob1st/fliphtml-downloader.git
cd fliphtml-downloader
pip install -r requirements.txt
```

or install it as a command: `pip install .` → `fliphtml-downloader <url>`

### Usage

```bash
python fliphtml_downloader.py "https://fliphtml5.com/xxxxx/yyyy"
python fliphtml_downloader.py xxxxx/yyyy        # just the book ID
python fliphtml_downloader.py                    # asks for the link
```

The book ID is the pair after `fliphtml5.com/` — e.g. `https://online.fliphtml5.com/hgzok/boba/` → `hgzok/boba`.
Use the book's own link, not a bookcase link.

Results are written to `downloads/` by default (page-images folder + `<Book Title>.pdf`).

See `--help` for all options (page range, `--cbz`, `--no-pdf`, `--quality`, `--workers`, `--overwrite`, `--no-keep-images`, …).

### How it works

1. The address is normalized to `online.fliphtml5.com` and the book's `javascript/config.js` is fetched.
2. If `fliphtml5_pages` is plain JSON it is read directly; if it is encrypted, FlipHTML5's official `deString.js` is cached once and its embedded WASM is executed with `wasmtime` to decode it.
3. Pages are downloaded in parallel from `files/large/` and verified as real images.
4. Images are merged into one PDF with `img2pdf`.

### Tests

```bash
pip install pytest
pytest -q
```

---

## ⚖️ Disclaimer

Bu araç yalnızca **kendi kitaplarınız** veya indirme hakkına sahip olduğunuz içerikler için, eğitim amaçlıdır.
Telif haklarına ve FlipHTML5 hizmet şartlarına saygı gösterin.

This tool is for educational purposes and for books you own or have the right to download.
Respect copyright and FlipHTML5's terms of service.

## License

[MIT](LICENSE)
