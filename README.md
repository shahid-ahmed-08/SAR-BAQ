# SAR BAQ — Synthetic Aperture Radar Compression Workspace

A full-stack, streaming **Block Adaptive Quantization (BAQ)** studio for Synthetic Aperture Radar (SAR) GeoTIFFs. Features an end-to-end compression pipeline, real-time telemetry, visual residual inspection, full-scene quality metrics (MSE, RMSE, SNR, PSNR), parameter sweep experiments, and a modern web interface.

Tested on large SAR GeoTIFF scenes (including 800 MB+ rasters) where peak memory is strictly bounded by streamed row-strip windows without exhausting RAM.

---

## ⚡ Quick Start (Running Locally)

### Prerequisites
* **Python 3.10+** (with `pip` or `conda`)
* **Node.js 18+** & `npm`

---

### Option A: 1-Click Launch (Windows)
Double-click **`start_workspace.bat`** in the project folder.

It will automatically:
1. Start the FastAPI backend on `http://127.0.0.1:8000`
2. Start the Vite React frontend on `http://127.0.0.1:5173`
3. Launch your default browser directly to the application.

---

### Option B: Manual Terminal Launch

#### 1. Setup Python Dependencies
```bash
# Clone the repository
git clone https://github.com/shahid-ahmed-08/SAR-BAQ.git
cd SAR-BAQ

# Optional: Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install Python requirements
pip install -r requirements.txt
pip install fastapi uvicorn[standard] python-multipart
```

> **Note for Windows users:** If `pip install rasterio` fails, install via conda:
> `conda install -c conda-forge rasterio`

#### 2. Start the Backend API
```bash
uvicorn api:app --host 127.0.0.1 --port 8000
```
*(Backend API is now live at `http://127.0.0.1:8000/api/health`)*

#### 3. Setup & Start the Frontend
In a new terminal:
```bash
cd frontend
npm install
npm run dev
```
Open **`http://127.0.0.1:5173/`** in your browser.

---

### Option C: CLI Mode (Terminal Only)
If you prefer running via the command-line menu without the web interface:
```bash
python sar_baq.py
```
Menu options:
1. Inspect TIFF
2. Compress using BAQ
3. Decompress BAQ file
4. Calculate metrics (MSE, RMSE, SNR, PSNR, compression ratio)
5. Run BAQ experiments (parameter sweep across block sizes & bit depths)
6. Visualize comparison & histograms
7. Exit

---

## 📂 Project Structure

```text
SAR-BAQ/
├── api.py                     # FastAPI REST & Server-Sent Events (SSE) server
├── sar_baq.py                 # Core streaming BAQ compression & metrics engine
├── start_workspace.bat        # Windows 1-click startup launcher
├── requirements.txt           # Core Python dependencies (rasterio, numpy, matplotlib)
├── README.md                  # Project documentation
├── data/
│   ├── input/                 # Put your GeoTIFF or .baq files here
│   │   └── sample_sentinel1_vv.tif   # Bundled sample SAR scene for instant testing
│   └── output/                # Generated .baq, reconstructed .tiff, metrics, and plots
└── frontend/                  # Modern Web Application
    ├── src/
    │   ├── App.tsx            # Main application UI and workflow controllers
    │   ├── api.ts             # API client with resilient polling & SSE
    │   ├── index.css          # Modern dark/light scientific styling system
    │   └── main.tsx           # React DOM entrypoint
    ├── package.json
    ├── tsconfig.json
    └── vite.config.ts         # Vite proxy configuration to backend
```

---

## 🛰️ Web Application Features

| Stage | Feature | Description |
| :--- | :--- | :--- |
| **01 / Input & Inspect** | **Dropzone & Library** | Drag & drop `.tif` / `.baq` files, 1-click file selector from `data/input/`, or direct path input. |
| | **Raster Telemetry** | Streamed inspection reads dimensions, sample count, data type, NoData value, driver, compression, CRS, and geotransform matrix. |
| | **Exact Statistics** | Streams min, max, mean ($\mu$), and standard deviation ($\sigma$) without full array loading. |
| | **Scientific Finding** | Explains whether the scene is real-valued amplitude or raw complex I/Q echoes, and how BAQ operates on it. |
| **02 / Pipeline** | **Quantizer Settings** | Discrete block sizes (8, 16, 32, 64, 128) and bit depths (2, 3, 4, 5, 6 bits) with real-time trade-off hints. |
| | **Streaming Memory** | User-configurable chunk memory budget (8 to 512 MB). |
| | **Live Terminal** | Monospace streaming log console with auto-scroll, color-coded stages, and 1-click log copying. |
| | **Standalone Tools** | Dedicated "Compress Only" (TIFF &rarr; `.baq`) and "Decompress Only" (`.baq` &rarr; GeoTIFF) actions. |
| **03 / Comparison** | **Visual Residuals** | High-resolution multi-panel comparison (Original vs Reconstructed vs Absolute Error residual map). |
| | **Fullscreen Zoom** | Click-to-enlarge modal with download button for report figures. |
| **04 / Metrics** | **Scientific KPIs** | High-contrast KPI cards: MSE, RMSE, SNR (dB), and PSNR (dB). |
| | **Storage Savings** | Visual savings ratio bar (e.g. 87.4% reduction, 7.93 : 1 compression ratio). |
| | **Data Export** | Export formatted JSON quality report with one click. |
| **05 / Experiments** | **Parameter Sweep** | Evaluates 15 configurations (block sizes 16, 32, 64 &times; bit depths 2–6). |
| | **Trade-Off Curves** | Displays matplotlib-generated rate-distortion graphs (`snr_vs_bits.png`, `compression_ratio_vs_bits.png`, etc.). |
| | **Sortable Data Table** | Interactive results table with sortable columns and **Export CSV** download. |

---

## 🔬 Scientific & Algorithmic Background

### 1. Classical Lloyd–Max Quantizer
SAR echoes backscattered from distributed targets are modeled as zero-mean Gaussian random variables. The Lloyd–Max quantizer minimizes mean squared error (MSE) by placing decision boundaries and reconstruction levels symmetrically with higher density around zero.

* Optimal decision thresholds and reconstruction levels are precomputed once per bit depth (2–6 bits).
* Cached lookup tables ensure fast encoding without iterative overhead during compression.

### 2. Block Adaptive Quantization (BAQ)
* The 2D image is partitioned into independent spatial blocks (e.g. $32 \times 32$ or 32-sample strips).
* For each block, the engine computes the sample Root Mean Square ($\sigma$):
  $$\sigma = \sqrt{\frac{1}{N} \sum_{i=1}^{N} x_i^2}$$
* Samples are normalized by $\sigma$, mapped to Lloyd–Max quantized integer indices, and packed into fractional byte bitstreams.
* The block scale factor $\sigma$ is preserved as side information in the `.baq` header (4 bytes per block), guaranteeing exact amplitude reconstruction.

### 3. Streamed Window Architecture
* Uses `rasterio.windows.Window` to process row-strip chunks directly from disk.
* Metrics (MSE, RMSE, SNR, PSNR) are accumulated continuously using streaming running sums:
  $$\text{MSE} = \frac{1}{N} \sum (x_i - \hat{x}_i)^2$$
  $$\text{SNR (dB)} = 10 \log_{10}\left( \frac{\sum x_i^2}{\sum (x_i - \hat{x}_i)^2} \right)$$
* Memory usage remains bounded (typically $< 360$ MB) regardless of whether the raster is 100 MB or 10 GB.

---

## 🧪 Benchmark Example

Tested on a Sentinel-1 single-band Float32 SAR scene:

| Metric | Original | BAQ Compressed | Performance |
| :--- | :--- | :--- | :--- |
| **File Size** | 1.00 MB | 129.2 KB | **87.4% reduction** |
| **Compression Ratio** | — | — | **7.93 : 1** |
| **Signal Quality (SNR)**| — | — | **20.52 dB** |
| **Peak SNR (PSNR)** | — | — | **39.72 dB** |
| **Elapsed Time** | — | — | **0.25 s** |

---

## 📄 License
This project is open-source and intended for academic, research, and engineering analysis of Synthetic Aperture Radar data compression.
