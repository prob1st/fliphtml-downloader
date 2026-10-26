#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fliphtml-downloader
====================

Download public FlipHTML5 flipbooks and save them as a single PDF
(and/or a folder of page images, and/or a CBZ archive).

How it works
------------
1. The book URL / ID is normalized to its ``online.fliphtml5.com`` address.
2. ``javascript/config.js`` is fetched. It contains the page manifest
   (``fliphtml5_pages``) and the book title.
3. Newer books encrypt that manifest. When that happens the official
   ``deString.js`` decoder shipped by FlipHTML5 itself is downloaded once,
   its embedded WebAssembly module is extracted and executed locally with
   ``wasmtime`` to recover the plain page list. No Node.js, no browser.
4. Page images are downloaded in parallel from ``files/large/`` (with
   ``medium`` / ``thumb`` and webp<->jpg fallbacks), verified, and merged
   into a PDF with ``img2pdf``.

If no usable config can be found (very old books), the tool falls back to
probing sequential page URLs (``files/large/1.jpg``, ``2.jpg``, ...).

Only download books you own or have the right to download.

Author : prob1st
license: MIT
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

__version__ = "1.0.0"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DESTRING_URL = "https://static.fliphtml5.com/resourceFiles/html5_templates/js/deString.js"
DESTRING_WASM_RE = re.compile(r"data:application/octet-stream;base64,([A-Za-z0-9+/=]+)")
SIZES = ("large", "medium", "thumb")

# ---------------------------------------------------------------------------
# i18n (Turkish default, English available with --lang en)
# ---------------------------------------------------------------------------

STRINGS = {
    "tr": {
        "prompt_url": "Kitap bağlantısını yapıştırın (örn. https://fliphtml5.com/xxxxx/yyyy): ",
        "book": "Kitap",
        "fetch_config": "Kitap bilgileri alınıyor...",
        "encrypted": "Şifreli sayfa listesi algılandı, çözülüyor (wasmtime)...",
        "decoder_download": "Çözücü ilk kez indiriliyor (deString.js)...",
        "probing": "Config bulunamadı, sayfalar tek tek aranıyor (eski format)...",
        "pages_found": "sayfa bulundu",
        "downloading": "Sayfalar indiriliyor",
        "building_pdf": "PDF oluşturuluyor",
        "building_cbz": "CBZ oluşturuluyor",
        "done": "Tamamlandı",
        "saved_pdf": "PDF kaydedildi",
        "saved_cbz": "CBZ kaydedildi",
        "saved_images": "Sayfa görselleri",
        "skipped_pages": "indirilemeyen sayfa atlandı",
        "error_no_pages": "HATA: Sayfa listesi bulunamadı veya çözülemedi. Kitap gizli/kaldırılmış olabilir.",
        "error_bad_input": "HATA: Geçerli bir kitap adresi anlaşılamadı",
        "error_nothing": "HATA: Hiçbir sayfa indirilemedi, PDF oluşturulmadı.",
    },
    "en": {
        "prompt_url": "Paste the book link (e.g. https://fliphtml5.com/xxxxx/yyyy): ",
        "book": "Book",
        "fetch_config": "Fetching book information...",
        "encrypted": "Encrypted page list detected, decoding (wasmtime)...",
        "decoder_download": "Downloading the decoder once (deString.js)...",
        "probing": "No config found, probing pages one by one (legacy format)...",
        "pages_found": "pages found",
        "downloading": "Downloading pages",
        "building_pdf": "Building PDF",
        "building_cbz": "Building CBZ",
        "done": "Done",
        "saved_pdf": "PDF saved",
        "saved_cbz": "CBZ saved",
        "saved_images": "Page images",
        "skipped_pages": "pages could not be downloaded and were skipped",
        "error_no_pages": "ERROR: Page list not found or could not be decoded. The book may be private/removed.",
        "error_bad_input": "ERROR: Could not understand a valid book address",
        "error_nothing": "ERROR: No pages could be downloaded, PDF not created.",
    },
}
LANG = "tr"


def t(key: str) -> str:
    return STRINGS.get(LANG, STRINGS["en"])[key]


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


@dataclass
class BookRef:
    base_url: str  # always ends with "/"
    book_id: str | None  # "xxxxx/yyyy" for fliphtml5.com books, None otherwise


def parse_book_input(raw: str) -> BookRef:
    """Accept a book ID (xxxxx/yyyy), a fliphtml5.com / online.fliphtml5.com
    URL, or a custom-domain URL hosting a FlipHTML5 book."""
    raw = raw.strip().strip("'\"")
    if not raw:
        raise ValueError("empty input")

    # Plain "xxxxx/yyyy" (optionally with #p=.. fragment or query)
    m = re.fullmatch(r"([\w\-]+)/([\w\-]+)/?(?:[?#].*)?", raw)
    if m and "://" not in raw and "." not in m.group(1):
        book_id = f"{m.group(1)}/{m.group(2)}"
        return BookRef(f"https://online.fliphtml5.com/{book_id}/", book_id)

    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    segments = [s for s in parsed.path.split("/") if s]
    if segments and segments[-1].lower() in ("index.html", "index.htm"):
        segments = segments[:-1]

    host = (parsed.hostname or "").lower()
    if host.endswith("fliphtml5.com"):
        if len(segments) < 2:
            raise ValueError(f"book id missing in URL: {raw}")
        book_id = f"{segments[0]}/{segments[1]}"
        return BookRef(f"https://online.fliphtml5.com/{book_id}/", book_id)

    # Custom domain: the whole path (minus index.html) is the book root.
    if not segments:
        raise ValueError(f"book path missing in URL: {raw}")
    base = f"{parsed.scheme}://{parsed.netloc}/" + "/".join(segments) + "/"
    return BookRef(base, None)


def sanitize_title(title: str, fallback: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title or "").strip(" .")
    title = re.sub(r"\s+", " ", title)
    return title[:150] or fallback


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def looks_like_image(data: bytes) -> bool:
    if len(data) < 100:
        return False
    if data[:3] == b"\xff\xd8\xff":  # JPEG
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":  # PNG
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WebP
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:2] == b"BM":  # BMP
        return True
    return False


# ---------------------------------------------------------------------------
# deString WASM decoder (for encrypted page manifests)
# ---------------------------------------------------------------------------


def _decoder_cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path.home() / "AppData" / "Local" / "fliphtml-downloader"
    else:
        base = Path.home() / ".cache" / "fliphtml-downloader"
    base.mkdir(parents=True, exist_ok=True)
    return base


class DeStringDecoder:
    """Runs FlipHTML5's own deString WebAssembly module via wasmtime.

    Only the handful of emscripten/WASI imports the module needs are
    implemented; anything else gets a harmless no-op stub.
    """

    PAGE = 65536

    def __init__(self, session: requests.Session):
        try:
            import wasmtime  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "This book uses an encrypted page list. Install the decoder "
                "dependency first:  pip install wasmtime"
            ) from e
        from wasmtime import Linker, Module, Store

        wasm = self._load_wasm(session)
        self._store = Store()
        module = Module(self._store.engine, wasm)
        linker = Linker(self._store.engine)
        self._define_imports(linker, module)
        instance = linker.instantiate(self._store, module)
        exports = instance.exports(self._store)
        self._memory = exports["memory"]
        self._malloc = exports["malloc"]
        self._free = exports["free"]
        self._decode_fn = exports.get("DeString") or exports["_DeString"]
        for init in ("emscripten_stack_init", "__wasm_call_ctors"):
            if init in exports:
                exports[init](self._store)

    # -- wasm loading -------------------------------------------------------
    @staticmethod
    def _load_wasm(session: requests.Session) -> bytes:
        js_path = _decoder_cache_dir() / "deString.js"
        if not (js_path.exists() and js_path.stat().st_size > 0):
            log(t("decoder_download"))
            r = session.get(DESTRING_URL, timeout=60)
            r.raise_for_status()
            js_path.write_bytes(r.content)
        code = js_path.read_text(encoding="utf-8", errors="replace")
        m = DESTRING_WASM_RE.search(code)
        if not m:
            raise RuntimeError("Embedded WASM not found inside deString.js")
        return base64.b64decode(m.group(1))

    # -- imports ------------------------------------------------------------
    def _define_imports(self, linker, module) -> None:
        handlers = {
            "emscripten_run_script": lambda caller, *_a: None,
            "emscripten_memcpy_big": self._memcpy_big,
            "fd_write": self._fd_write,
            "emscripten_resize_heap": self._resize_heap,
        }
        for imp in module.imports:
            func = handlers.get(imp.name)
            if func is None:
                func = self._make_noop(imp.type)
            linker.define_func(imp.module, imp.name, imp.type, func, access_caller=True)

    @staticmethod
    def _make_noop(func_type):
        def _noop(caller, *_args):
            return 0 if func_type.results else None

        return _noop

    @staticmethod
    def _mem_of(caller):
        mem = caller.get("memory")
        if mem is None:
            raise RuntimeError("wasm memory unavailable")
        return mem

    def _memcpy_big(self, caller, dest, src, size):
        if size > 0:
            mem = self._mem_of(caller)
            mem.write(caller, bytes(mem.read(caller, src, src + size)), dest)

    def _fd_write(self, caller, _fd, iovs, iovs_len, nwritten):
        mem = self._mem_of(caller)
        total = 0
        for i in range(iovs_len):
            base = iovs + i * 8
            total += int.from_bytes(mem.read(caller, base + 4, base + 8), "little")
        mem.write(caller, total.to_bytes(4, "little"), nwritten)
        return 0

    def _resize_heap(self, caller, requested):
        from wasmtime import WasmtimeError

        mem = self._mem_of(caller)
        current = mem.data_len(caller)
        if requested <= current:
            return 1
        try:
            mem.grow(caller, (requested - current + self.PAGE - 1) // self.PAGE)
            return 1
        except WasmtimeError:
            return 0

    # -- public API ---------------------------------------------------------
    def decode(self, value: str) -> str:
        data = value.encode("utf-8") + b"\x00"
        ptr = self._malloc(self._store, len(data))
        try:
            self._memory.write(self._store, data, ptr)
            out_ptr = self._decode_fn(self._store, ptr)
            return self._read_c_string(out_ptr)
        finally:
            self._free(self._store, ptr)

    def _read_c_string(self, ptr: int) -> str:
        if not ptr or ptr >= self._memory.data_len(self._store):
            return ""
        raw = bytes(self._memory.read(self._store, ptr, self._memory.data_len(self._store)))
        nul = raw.find(b"\x00")
        if nul >= 0:
            raw = raw[:nul]
        return raw.decode("utf-8", errors="replace")


_decoder: DeStringDecoder | None = None


def destring(session: requests.Session, value: str) -> str:
    global _decoder
    if _decoder is None:
        _decoder = DeStringDecoder(session)
    text = _decoder.decode(value)
    # Decoded buffers can carry trailing junk after the JSON payload.
    end = max(text.rfind("]"), text.rfind("}"))
    return text[: end + 1] if end != -1 else text


# ---------------------------------------------------------------------------
# Config & page manifest
# ---------------------------------------------------------------------------


def parse_config_text(text: str) -> dict:
    text = text.strip().lstrip("\ufeff")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("config does not look like JSON")
    return json.loads(text[start : end + 1])


def fetch_config(session: requests.Session, base_url: str) -> dict | None:
    for candidate in ("javascript/config.js", "mobile/javascript/config.js"):
        try:
            r = session.get(urljoin(base_url, candidate), timeout=30)
            if r.status_code == 200 and "fliphtml5_pages" in r.text:
                return parse_config_text(r.text)
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            continue
    return None


def _parse_pages_json(text: str) -> list | None:
    text = text.strip()
    candidates = [text]
    s, e = text.find("["), text.rfind("]")
    if s != -1 and e > s:
        candidates.append(text[s : e + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
    return None


def decode_pages_value(session: requests.Session, value) -> list | None:
    """`fliphtml5_pages` can be a plain list, a JSON string, or an encrypted
    string produced by FlipHTML5's deString obfuscation."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            return _parse_pages_json(stripped)
        log(t("encrypted"))
        return _parse_pages_json(destring(session, stripped))
    return None


def get_title(config: dict, fallback: str) -> str:
    meta = config.get("meta")
    if isinstance(meta, dict) and meta.get("title"):
        return str(meta["title"])
    if config.get("title"):
        return str(config["title"])
    bc = config.get("bookConfig")
    if isinstance(bc, dict) and bc.get("bookTitle"):
        return str(bc["bookTitle"])
    return fallback


def extract_pages(session: requests.Session, config: dict) -> list | None:
    pages = decode_pages_value(session, config.get("fliphtml5_pages"))
    if pages:
        return pages
    # Some books tuck everything inside an (encrypted) bookConfig blob.
    bc = config.get("bookConfig")
    if isinstance(bc, str):
        try:
            decoded = destring(session, bc)
            pages = _parse_pages_json(decoded)
            if pages:
                return pages
            inner = json.loads(decoded)
            if isinstance(inner, dict):
                return decode_pages_value(session, inner.get("fliphtml5_pages"))
        except (RuntimeError, ValueError, json.JSONDecodeError):
            pass
    if isinstance(bc, dict):
        return decode_pages_value(session, bc.get("fliphtml5_pages"))
    return None


def page_filename(entry) -> str | None:
    """One manifest entry -> the file name (or URL) of that page image."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        n = entry.get("n")
        if isinstance(n, list) and n:
            return str(n[0])
        if isinstance(n, str) and n:
            return n
        for key in ("src", "url", "l"):
            if entry.get(key):
                return str(entry[key])
    return None


def candidate_urls(base_url: str, filename: str, quality: str) -> list[str]:
    """Ordered, de-duplicated list of URLs to try for one page."""
    if filename.startswith(("http://", "https://")):
        return [filename]
    name = filename.lstrip("./")
    variants = [name]
    lower = name.lower()
    if lower.endswith(".webp"):
        variants.append(name[:-5] + ".jpg")
    elif lower.endswith((".jpg", ".jpeg")):
        variants.append(name.rsplit(".", 1)[0] + ".webp")

    sizes = [quality] + [s for s in SIZES if s != quality]
    urls: list[str] = []
    for var in variants:
        if var.startswith("files/"):
            urls.append(urljoin(base_url, var))
        else:
            for size in sizes:
                urls.append(urljoin(base_url, f"files/{size}/{var}"))
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# Legacy fallback: probe files/large/1.jpg, 2.jpg, ...
# ---------------------------------------------------------------------------


def probe_pages(session: requests.Session, base_url: str, max_misses: int = 10,
                hard_limit: int = 10000) -> list[str]:
    log(t("probing"))
    found: list[str] = []
    misses = 0
    for n in range(1, hard_limit + 1):
        hit = None
        for ext in ("jpg", "webp", "png", "jpeg"):
            url = urljoin(base_url, f"files/large/{n}.{ext}")
            try:
                with session.get(url, timeout=20, stream=True) as r:
                    ctype = r.headers.get("Content-Type", "")
                    if r.status_code == 200 and (
                        ctype.startswith("image/")
                        or int(r.headers.get("Content-Length") or 0) > 1000
                    ):
                        hit = f"{n}.{ext}"
                        break
            except requests.RequestException:
                continue
        if hit:
            found.append(hit)
            misses = 0
        else:
            misses += 1
            if misses >= max_misses:
                break
    return found


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------


@dataclass
class Result:
    page_files: list[Path] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


def _valid_existing(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 100:
        return False
    with open(path, "rb") as f:
        return looks_like_image(f.read(64))


def download_one(session: requests.Session, urls: list[str], dest: Path,
                 overwrite: bool) -> Path | None:
    if not overwrite:
        for ext in (".jpg", ".webp", ".png"):
            existing = dest.with_suffix(ext)
            if _valid_existing(existing):
                return existing
    for url in urls:
        for attempt in range(2):
            try:
                r = session.get(url, timeout=45)
            except requests.RequestException:
                time.sleep(0.4 * (attempt + 1))
                continue
            if r.status_code == 200 and looks_like_image(r.content):
                suffix = Path(urlparse(url).path).suffix.lower() or ".jpg"
                target = dest.with_suffix(suffix if suffix in (".jpg", ".jpeg", ".webp", ".png") else ".jpg")
                target.write_bytes(r.content)
                return target
            if r.status_code == 404:
                break  # next candidate URL, retrying is pointless
            time.sleep(0.4 * (attempt + 1))
    return None


def download_pages(session: requests.Session, base_url: str, entries: list,
                   start: int, end: int | None, quality: str, workers: int,
                   out_dir: Path, overwrite: bool) -> Result:
    total_all = len(entries)
    start = max(1, start)
    end = min(total_all, end) if end else total_all
    selected = list(range(start, end + 1))
    width = len(str(total_all))
    result = Result(page_files=[None] * len(selected))  # type: ignore[list-item]
    counter = {"done": 0}
    lock = threading.Lock()
    total = len(selected)

    def job(slot: int, page_no: int):
        name = page_filename(entries[page_no - 1])
        if not name:
            return slot, page_no, None
        urls = candidate_urls(base_url, name, quality)
        dest = out_dir / f"{page_no:0{width}d}"
        return slot, page_no, download_one(session, urls, dest, overwrite)

    def progress():
        done = counter["done"]
        pct = done * 100 // max(total, 1)
        bar = "#" * (pct // 4) + "-" * (25 - pct // 4)
        msg = f"\r{t('downloading')}: [{bar}] {done}/{total} ({pct}%)"
        if sys.stdout.isatty():
            sys.stdout.write(msg)
            sys.stdout.flush()
        elif done % 10 == 0 or done == total:
            log(msg.strip())

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(job, i, p): p for i, p in enumerate(selected)}
        for fut in as_completed(futures):
            slot, page_no, path = fut.result()
            if path is None:
                result.failed.append(page_no)
            else:
                result.page_files[slot] = path
            with lock:
                counter["done"] += 1
                progress()
    if sys.stdout.isatty():
        sys.stdout.write("\n")
    result.page_files = [p for p in result.page_files if p is not None]
    result.failed.sort()
    return result


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------


def _jpeg_copy(src: Path, dst: Path, quality: int) -> Path:
    from PIL import Image

    with Image.open(src) as im:
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            mask = im.split()[-1] if im.mode in ("RGBA", "LA") else None
            bg.paste(im, mask=mask)
            im = bg
        else:
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=quality)
    return dst


def build_pdf(page_files: list[Path], pdf_path: Path, jpeg_quality: int,
              title: str) -> None:
    import img2pdf

    log(f"{t('building_pdf')}...")
    with tempfile.TemporaryDirectory(prefix="fliphtml-") as tmp:
        tmp_dir = Path(tmp)
        inputs: list[str] = []
        for i, p in enumerate(page_files):
            if p.suffix.lower() in (".jpg", ".jpeg"):
                inputs.append(str(p))
            else:
                inputs.append(str(_jpeg_copy(p, tmp_dir / f"{i:05d}.jpg", jpeg_quality)))
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pdf_path, "wb") as f:
            try:
                f.write(img2pdf.convert(inputs, title=title))
            except TypeError:  # older img2pdf without the title kwarg
                f.write(img2pdf.convert(inputs))


def build_cbz(page_files: list[Path], cbz_path: Path) -> None:
    log(f"{t('building_cbz')}...")
    cbz_path.parent.mkdir(parents=True, exist_ok=True)
    width = len(str(len(page_files)))
    with zipfile.ZipFile(cbz_path, "w", zipfile.ZIP_STORED) as z:
        for i, p in enumerate(page_files, 1):
            z.write(p, f"{i:0{width}d}{p.suffix.lower()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fliphtml-downloader",
        description="Download public FlipHTML5 flipbooks as PDF / images / CBZ.",
    )
    p.add_argument("url", nargs="?",
                   help="Book URL (https://fliphtml5.com/xxxxx/yyyy) or just the ID (xxxxx/yyyy)")
    p.add_argument("-o", "--out", default="downloads",
                   help="Output directory (default: downloads)")
    p.add_argument("--pdf", metavar="FILE",
                   help="PDF file name/path (default: <book title>.pdf inside the output dir)")
    p.add_argument("--no-pdf", action="store_true", help="Do not build a PDF")
    p.add_argument("--cbz", action="store_true", help="Also build a CBZ archive")
    p.add_argument("--start", type=int, default=1, help="First page to download (default: 1)")
    p.add_argument("--end", type=int, default=None, help="Last page to download (default: last page)")
    p.add_argument("--quality", choices=SIZES, default="large",
                   help="Preferred image size (default: large)")
    p.add_argument("--workers", type=int, default=8, help="Parallel download threads (default: 8)")
    p.add_argument("--jpeg-quality", type=int, default=92,
                   help="JPEG quality used while building the PDF, 1-100 (default: 92)")
    p.add_argument("--overwrite", action="store_true",
                   help="Re-download pages that already exist (otherwise resumes)")
    p.add_argument("--no-keep-images", action="store_true",
                   help="Delete the page images after building the PDF/CBZ")
    p.add_argument("--config", metavar="FILE",
                   help="Use a local config.js instead of fetching it (debugging)")
    p.add_argument("--lang", choices=("tr", "en"), default="tr", help="Interface language (default: tr)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    global LANG
    LANG = args.lang

    raw = args.url or input(t("prompt_url")).strip()
    try:
        ref = parse_book_input(raw)
    except ValueError as e:
        log(f"{t('error_bad_input')}: {raw!r} ({e})")
        return 2
    log(f"{t('book')}: {ref.base_url}")

    session = make_session()
    log(t("fetch_config"))
    config = None
    if args.config:
        config = parse_config_text(Path(args.config).read_text(encoding="utf-8", errors="replace"))
    else:
        config = fetch_config(session, ref.base_url)

    entries = extract_pages(session, config) if config else None
    title_fallback = (ref.book_id or "fliphtml5-book").replace("/", "_")
    title = sanitize_title(get_title(config, title_fallback) if config else title_fallback,
                           title_fallback)
    if not entries:
        entries = probe_pages(session, ref.base_url)
    if not entries:
        log(t("error_no_pages"))
        return 2
    log(f"{title}: {len(entries)} {t('pages_found')}.")

    out_dir = Path(args.out)
    images_dir = out_dir / title
    images_dir.mkdir(parents=True, exist_ok=True)

    result = download_pages(
        session, ref.base_url, entries, args.start, args.end,
        args.quality, args.workers, images_dir, args.overwrite,
    )
    if not result.page_files:
        log(t("error_nothing"))
        return 1
    if result.failed:
        log(f"{len(result.failed)} {t('skipped_pages')}: "
            + ", ".join(map(str, result.failed[:50]))
            + (" ..." if len(result.failed) > 50 else ""))

    if not args.no_pdf:
        pdf_path = Path(args.pdf) if args.pdf else out_dir / f"{title}.pdf"
        build_pdf(result.page_files, pdf_path, args.jpeg_quality, title)
        log(f"{t('saved_pdf')}: {pdf_path}")
    if args.cbz:
        cbz_path = out_dir / f"{title}.cbz"
        build_cbz(result.page_files, cbz_path)
        log(f"{t('saved_cbz')}: {cbz_path}")
    if args.no_keep_images and (not args.no_pdf or args.cbz):
        for p in result.page_files:
            p.unlink(missing_ok=True)
        try:
            images_dir.rmdir()
        except OSError:
            pass
    else:
        log(f"{t('saved_images')}: {images_dir}")
    log(f"{t('done')} ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
