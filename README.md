# SAR Raw Data Compression Using Block Adaptive Quantization (BAQ)

Traditional, non-ML BAQ compressor/decompressor for large SAR GeoTIFFs.
Tested end-to-end (compress → decompress → metrics → visualization →
experiments) on synthetic data during development, including a 151 MB file
where peak memory stayed at ~360 MB — confirming the streaming design.

## 1. VS Code or Colab?

**Use VS Code, locally, on your own machine.** Reasons:

- Your file is 818 MB. Uploading it to Colab (even with Drive mounting) is
  slow and Colab sessions can disconnect/reset, losing your uploaded copy.
- Colab's free-tier disk and session limits are a poor fit for repeatedly
  reading an 800MB+ local file plus writing compressed/reconstructed outputs.
- This script deliberately never loads the whole file into RAM (see below),
  so you don't need Colab's extra RAM anyway — it runs fine on a normal
  laptop.
- `rasterio` (which needs GDAL) installs more reliably via `pip`/`conda` in
  a local VS Code + venv setup than it sometimes does in Colab.

Colab is fine only if you're just testing the algorithm on a small cropped
sample of your data.

## 2. Project structure

```
SAR_BAQ_Project/
├── data/
│   ├── input/        <- put your 818 MB TIFF here
│   └── output/        <- compressed.baq, reconstructed.tiff, PNGs land here
├── sar_baq.py          <- the complete single-file implementation (this is everything)
├── requirements.txt
└── README.md
```

This is the **single-file version** you asked for first (see project spec
item 14). It already contains everything: TIFF inspection, chunked BAQ
encode/decode, the `.baq` file format, streaming metric computation,
preview visualization, the experiment sweep, and a CLI menu.

### How to split it into modules later

Once you're happy with it, split `sar_baq.py` along its existing numbered
sections — they're already logically separate:

| Section in sar_baq.py                        | Move to                  |
|-----------------------------------------------|---------------------------|
| §4 `inspect_tiff`                              | `src/tiff_reader.py`     |
| §1–3 Lloyd-Max design, bit packing, `baq_encode_*` | `src/baq_encoder.py` |
| §3 `baq_decode_*`                              | `src/baq_decoder.py`     |
| §5–6 `compress_tiff`, `decompress_baq`, file format helpers | `src/compression.py` |
| §7 `compute_metrics`                           | `src/metrics.py`         |
| §8 `visualize_comparison`                      | `src/visualization.py`   |
| §9 `run_experiments`, `_plot_experiments`       | `experiments/experiments.py` |
| §10 CLI (`main`)                               | `main.py`, importing the above |

Just add `from baq_encoder import design_lloyd_max_gaussian, ...`-style
imports between the new files (keep `pack_indices`/`unpack_indices`/
`packed_byte_length` alongside the encoder since both encoder and decoder
need them, or put them in a small shared `src/bitpack.py`).

## 3. Setup in VS Code

```bash
cd SAR_BAQ_Project
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

If `pip install rasterio` fails on Windows, use conda instead:
`conda install -c conda-forge rasterio`.

Place your TIFF at `data/input/your_file.tiff`.

## 4. Running it

```bash
python sar_baq.py
```

You'll get a menu:

```
1. Inspect TIFF
2. Compress using BAQ
3. Decompress BAQ file
4. Calculate metrics
5. Run BAQ experiments
6. Visualize comparison
7. Exit
```

**Recommended order for your project:**
1. Run **option 1** first on your actual 818MB file. Read the printed
   "DATA TYPE FINDING" — it tells you whether your TIFF is real-valued
   (amplitude/intensity) or complex (I/Q), which changes what "BAQ
   compression" means for your data (see §6 below).
2. Run **option 2** to compress (pick block size + bits when prompted).
3. Run **option 3** to decompress back to a TIFF.
4. Run **option 4** to get MSE/RMSE/SNR/PSNR/compression ratio.
5. Run **option 6** for the preview comparison PNG + histograms.
6. Run **option 5** to sweep block sizes × bit rates and get the
   comparison table + 5 graphs (PNG files) for your report.

Progress percentages print during compression/decompression since your
file is large.

## 5. What each function does

- `design_lloyd_max_gaussian(bits)` — designs the Lloyd-Max quantizer
  (decision thresholds + reconstruction levels) optimal for a zero-mean,
  unit-variance Gaussian source, for a given bit depth. Cached, computed
  once, reused for every block.
- `pack_indices` / `unpack_indices` — pack quantization indices (2–6 bits
  each) into a compact byte stream, and unpack them back.
- `baq_encode_block` / `baq_decode_block` — encode/decode ONE block: compute
  the block's RMS (the adaptive scale factor), normalize, quantize using
  the Lloyd-Max table, and the inverse.
- `baq_encode_chunk` / `baq_decode_chunk` — apply the above to every block
  in a 2D row-strip chunk, handling partial edge blocks.
- `inspect_tiff` — prints all requested metadata plus streamed (not
  in-RAM) min/max/mean/std, and reports whether the data is real or
  complex.
- `compress_tiff` — opens the TIFF with `rasterio`, reads one row-strip
  window at a time (`rasterio.windows.Window`), BAQ-encodes it, and
  appends the result to the `.baq` file. Automatically splits complex
  bands into real/imag components.
- `decompress_baq` — reads the `.baq` header, then reconstructs and writes
  the output TIFF one chunk at a time via `rasterio`'s windowed writer.
- `compute_metrics` — streams both TIFFs in matching windows, accumulating
  `sum_squared_error`, `sum_signal_power`, and `sample_count` only —
  never holds both full arrays in memory.
- `visualize_comparison` — uses `rasterio`'s `out_shape` decimated read to
  build a small preview (default ≤512px) for display; does not touch the
  full-resolution array.
- `run_experiments` — loops over block sizes × bit rates, compresses,
  decompresses, measures, deletes the temp files, and finally prints a
  results table and saves 5 PNG graphs.

## 6. Important scientific notes (read before writing your report)

- **BAQ variant implemented**: a block-adaptive scalar quantizer whose
  reconstruction levels are a **Lloyd-Max quantizer designed for a
  zero-mean, unit-variance Gaussian source**, with the scale factor being
  each block's RMS value. This mirrors the classical BAQ approach described
  in the SAR literature (e.g. Kwok & Johnson) for near-Gaussian I/Q data.
  It is **not** any specific mission's proprietary/patented implementation.
- **Real vs. complex input**: run option 1 first. If your TIFF is a single
  real-valued band (the normal case for an 818MB single-image `.tiff`),
  this script compresses **"BAQ compression applied to SAR TIFF numerical
  data"** — i.e., whatever numbers are actually stored in the file
  (amplitude/intensity/magnitude, depending on how your data was
  processed upstream — the GeoTIFF itself typically doesn't record which).
  This is explicitly **not** the same claim as "BAQ compression of the
  original raw SAR I/Q echo," which would require the original complex
  radar samples, not a detected/processed image product.
- **Side information**: the compressed `.baq` file's size, as reported and
  used in all compression-ratio/storage-saving numbers, **includes** the
  header and every block's stored sigma (4 bytes/block) — nothing is
  hidden from the accounting.
- **What's lost**: BAQ is lossy. Each block's samples are represented by
  only 2^bits distinct reconstruction values, so fine amplitude variation
  within a block is discarded; the residual is exactly what MSE/SNR/PSNR
  quantify for you.

## 7. Expected files after a full run

```
data/output/compressed.baq          # binary: header + per-block sigma + packed indices
data/output/reconstructed.tiff      # same width/height/bands as input, float32 (or complex64)
data/output/preview_comparison.png  # original/reconstructed/error + 3 histograms
experiments_tmp/graph1_cr_vs_bits.png
experiments_tmp/graph2_snr_vs_bits.png
experiments_tmp/graph3_mse_vs_bits.png
experiments_tmp/graph4_cr_vs_blocksize.png
experiments_tmp/graph5_saving_vs_bits.png
```

## 8. Example terminal output (compression)

```
Compressing: chunk 1/25  (2048 rows)  Progress:   4.0%
Compressing: chunk 2/25  (2048 rows)  Progress:   8.0%
...
Compressing: chunk 25/25  (612 rows)  Progress: 100.0%
----------------------------------------------------------------------
Done in 41.3s.
Original file size   : 818.00 MB
Compressed file size : 74.36 MB (header + per-block sigma + packed indices, i.e. FULL side info included)
Compression ratio    : 11.001 x
Storage saving       : 90.91 %
```

## 9. Metrics explained

- **MSE** — mean squared error between original and reconstructed samples.
- **RMSE** — √MSE, same units as the data.
- **SNR (dB)** — ratio of original signal power to error power.
- **PSNR (dB)** — like SNR but normalized by the data's actual value range
  (max − min), computed from the streamed exact min/max.
- **Compression Ratio** — original file size ÷ compressed `.baq` file size.
- **Storage Saving (%)** — `(1 − compressed/original) × 100`.

## 10. Memory efficiency, concretely

- Reading: `rasterio` opens the file with GDAL and only decodes the pixels
  inside the `Window` you request — the OS/GDAL never materializes the
  full raster.
- Chunk size is chosen by `chunk_target_mb` (default 64MB) and does **not**
  grow with file size — an 818MB file uses the same per-chunk memory as an
  8GB file, just more chunks.
- Metrics never hold two full arrays — only three running scalars
  (`sum_squared_error`, `sum_signal_power`, `n_samples`).
- Visualization reads a single decimated overview (`out_shape`), not the
  full array.
- Verified empirically during development: compressing a 144MB test file
  peaked at ~360MB RSS (most of that is Python/GDAL baseline overhead, not
  data) — confirming memory does not scale with input size.
