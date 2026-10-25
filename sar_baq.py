import os
import sys
import json
import struct
import time
import math
import traceback
from functools import lru_cache

import numpy as np

try:
    import rasterio
    from rasterio.windows import Window
    HAVE_RASTERIO = True
except ImportError:
    rasterio = None
    Window = None
    HAVE_RASTERIO = False

# matplotlib is only needed for visualization / experiment graphs
try:
    import matplotlib
    matplotlib.use("Agg")  # no display needed, just save PNGs
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


MAGIC = b"SARBAQ01"          # file format magic number
VALID_BITS = (2, 3, 4, 5, 6)
VALID_BLOCK_SIZES = (8, 16, 32, 64, 128)


# =====================================================================
# 1. LLOYD-MAX GAUSSIAN QUANTIZER DESIGN  (the "quantization table")
# =====================================================================

@lru_cache(maxsize=None)
def design_lloyd_max_gaussian(bits, iterations=60, grid_points=20001, x_range=6.0):
    """
    Design a Lloyd-Max scalar quantizer optimal for a zero-mean, unit-
    variance Gaussian source, with 2**bits reconstruction levels.

    Returns
    -------
    thresholds : np.ndarray, shape (2**bits - 1,)
        Decision boundaries (ascending) used to map a normalized sample
        to a quantization index via np.searchsorted.
    levels : np.ndarray, shape (2**bits,)
        Reconstruction (dequantization) values, one per index.

    This table is computed ONCE per bit-depth (cached) and is tiny
    (<= 64 levels), so it costs nothing during streaming compression.
    """
    n_levels = 2 ** bits
    x = np.linspace(-x_range, x_range, grid_points)
    pdf = np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

    # Initialize levels evenly spread across the likely range
    levels = np.linspace(-x_range * 0.7, x_range * 0.7, n_levels)

    for _ in range(iterations):
        thresholds = (levels[:-1] + levels[1:]) / 2.0
        bin_idx = np.searchsorted(thresholds, x)
        new_levels = levels.copy()
        for i in range(n_levels):
            mask = bin_idx == i
            weight = pdf[mask].sum()
            if weight > 0:
                new_levels[i] = np.sum(x[mask] * pdf[mask]) / weight
        if np.allclose(new_levels, levels, atol=1e-7):
            levels = new_levels
            break
        levels = new_levels

    levels = np.sort(levels)
    thresholds = (levels[:-1] + levels[1:]) / 2.0
    return thresholds, levels


# =====================================================================
# 2. BIT PACKING  (pack N-bit indices into a compact byte stream)
# =====================================================================

def pack_indices(indices, bits):
    """Pack an array of small unsigned ints (each < 2**bits) into bytes."""
    idx = indices.astype(np.uint8)
    bit_matrix = np.unpackbits(idx[:, None], axis=1)      # (N, 8), MSB first
    bit_matrix = bit_matrix[:, 8 - bits:]                  # keep low `bits` bits
    flat_bits = bit_matrix.reshape(-1)
    packed = np.packbits(flat_bits)
    return packed.tobytes()


def unpack_indices(packed_bytes, bits, n):
    """Inverse of pack_indices."""
    packed = np.frombuffer(packed_bytes, dtype=np.uint8)
    bit_stream = np.unpackbits(packed)[: n * bits]
    bit_matrix = bit_stream.reshape(n, bits)
    idx = np.zeros(n, dtype=np.uint16)
    for b in range(bits):
        idx = (idx << 1) | bit_matrix[:, b].astype(np.uint16)
    return idx


def packed_byte_length(n_samples, bits):
    return (n_samples * bits + 7) // 8


# =====================================================================
# 3. BAQ ENCODER / DECODER  (operate on ONE chunk array at a time)
# =====================================================================

def baq_encode_block(block, bits, thresholds, levels):
    """
    Encode a single block (2D or flattened 1D array of real samples).

    Adaptive scale factor = RMS of the block (standard choice for BAQ;
    robust for zero-mean, near-Gaussian SAR echo statistics, and well
    defined even when the block mean is nonzero, e.g. intensity data).

    Handles:
      - all-zero blocks (sigma == 0 -> store sigma=0, indices=0, decoder
        reconstructs an all-zero block, no division by zero)
      - very small values (float64 accumulation avoids underflow)
      - negative values (Gaussian design is symmetric about 0)
      - large dynamic range (each block gets its OWN scale, so a bright
        block and a dark block are each represented efficiently)
      - integer or floating point input (cast to float64 internally)

    Returns
    -------
    packed_bytes : bytes
    sigma : float32
    n_samples : int
    """
    flat = block.reshape(-1).astype(np.float64)
    n = flat.size
    sigma = float(np.sqrt(np.mean(flat * flat)))  # RMS

    if sigma < 1e-12 or n == 0:
        # Degenerate / all-zero block: nothing to transmit but the flag.
        indices = np.zeros(n, dtype=np.uint8)
        packed = pack_indices(indices, bits)
        return packed, 0.0, n

    normalized = flat / sigma
    indices = np.searchsorted(thresholds, normalized).astype(np.uint8)
    packed = pack_indices(indices, bits)
    return packed, np.float32(sigma), n


def baq_decode_block(packed_bytes, bits, sigma, n_samples, levels, out_shape):
    """Inverse of baq_encode_block. Returns a reconstructed array of out_shape."""
    if sigma == 0.0:
        return np.zeros(out_shape, dtype=np.float32)
    indices = unpack_indices(packed_bytes, bits, n_samples)
    recon = levels[indices].astype(np.float32) * np.float32(sigma)
    return recon.reshape(out_shape)


def baq_encode_chunk(chunk, block_size, bits):
    """
    Encode a full 2D chunk (rows x cols, single band) into a sequence of
    per-block (packed_bytes, sigma) pairs, iterated in row-major block
    order. Partial edge blocks (when chunk dims aren't multiples of
    block_size) are handled by simply using the smaller remaining shape.
    """
    thresholds, levels = design_lloyd_max_gaussian(bits)
    h, w = chunk.shape
    out = []  # list of (packed_bytes, sigma, n_samples, rows, cols)
    for r0 in range(0, h, block_size):
        r1 = min(r0 + block_size, h)
        for c0 in range(0, w, block_size):
            c1 = min(c0 + block_size, w)
            block = chunk[r0:r1, c0:c1]
            packed, sigma, n = baq_encode_block(block, bits, thresholds, levels)
            out.append((packed, sigma, n, r1 - r0, c1 - c0))
    return out


def baq_decode_chunk(block_records, block_size, bits, chunk_shape):
    """Inverse of baq_encode_chunk -> reconstructed 2D chunk array."""
    _, levels = design_lloyd_max_gaussian(bits)
    h, w = chunk_shape
    out = np.zeros((h, w), dtype=np.float32)
    i = 0
    for r0 in range(0, h, block_size):
        r1 = min(r0 + block_size, h)
        for c0 in range(0, w, block_size):
            c1 = min(c0 + block_size, w)
            packed, sigma, n, rows, cols = block_records[i]
            recon = baq_decode_block(packed, bits, sigma, n, levels, (rows, cols))
            out[r0:r1, c0:c1] = recon
            i += 1
    return out


# =====================================================================
# 4. TIFF INSPECTION
# =====================================================================

def inspect_tiff(path, band=1):
    """Print metadata and basic statistics WITHOUT loading the full file."""
    if not HAVE_RASTERIO:
        print("ERROR: rasterio is required. Install with: pip install rasterio")
        return None
    if not os.path.exists(path):
        print(f"ERROR: file not found: {path}")
        return None

    file_size = os.path.getsize(path)
    with rasterio.open(path) as src:
        if band < 1 or band > src.count:
            print(f"ERROR: band {band} is out of range (file has {src.count} band(s)).")
            return None
        print("=" * 70)
        print(f"TIFF INSPECTION: {path}")
        print("=" * 70)
        print(f"File size          : {file_size / (1024**3):.3f} GB "
              f"({file_size / (1024**2):.1f} MB)")
        print(f"Width x Height     : {src.width} x {src.height}")
        print(f"Number of bands    : {src.count}")
        print(f"Data type          : {src.dtypes}")
        print(f"NoData value       : {src.nodata}")
        print(f"CRS                : {src.crs}")
        print(f"Transform          : {src.transform}")
        print(f"Driver             : {src.driver}")
        print(f"Compression        : {src.profile.get('compress', 'none')}")
        print(f"Tiled              : {src.profile.get('tiled', False)}")
        if src.profile.get("tiled", False):
            print(f"Block/tile size    : {src.profile.get('blockxsize')} x "
                  f"{src.profile.get('blockysize')}")
        else:
            bs = src.block_shapes[0] if src.block_shapes else None
            print(f"Strip/block shape  : {bs}  (rows, cols) per band")

        dtype = src.dtypes[band - 1]
        is_complex = np.dtype(dtype).kind == "c"
        tiled = bool(src.profile.get("tiled", False))
        compress = src.profile.get("compress", "none") or "none"
        if tiled:
            layout = f"{src.profile.get('blockxsize')} x {src.profile.get('blockysize')} tiles"
        else:
            bs = src.block_shapes[0] if src.block_shapes else None
            layout = f"strips {bs} (rows, cols) per band"

        print("-" * 70)
        if is_complex:
            finding_kind = "complex"
            finding_title = f"Complex samples ({dtype})"
            finding_body = [
                "This looks like raw / SLC complex SAR data (I and Q combined into one complex value per pixel).",
                "BAQ is applied to the real and imaginary parts separately. This script auto-splits a complex band into I/Q.",
            ]
            print("DATA TYPE FINDING: This TIFF stores COMPLEX samples "
                  f"({dtype}).")
            print("  -> This looks like raw / SLC complex SAR data (I and Q")
            print("     combined into one complex value per pixel).")
            print("  -> RECOMMENDATION: BAQ should be applied to the REAL and")
            print("     IMAGINARY parts SEPARATELY (each is a real-valued,")
            print("     roughly Gaussian channel) using two independent BAQ")
            print("     streams. This script auto-splits a complex band into")
            print("     I/Q for you if it encounters one.")
        elif src.count >= 2:
            finding_kind = "multiband"
            finding_title = f"{src.count} real-valued bands"
            finding_body = [
                "If this is a 2-band I and Q product, BAQ should be run independently on each band.",
                "This workspace currently compresses only the selected band.",
            ]
            print(f"DATA TYPE FINDING: TIFF has {src.count} bands.")
            print("  -> If this is a 2-band real-valued I and Q product, BAQ")
            print("     will be run independently on band 1 (I) and band 2 (Q).")
            print("  -> If this is a multi-band amplitude stack, each band is")
            print("     compressed independently as real-valued SAR imagery.")
        else:
            finding_kind = "real"
            finding_title = "Single band, real-valued samples"
            finding_body = [
                "This is consistent with amplitude / intensity / magnitude SAR imagery (a processed product), not raw complex I/Q radar echo.",
                "BAQ here compresses the numbers stored in the TIFF. The GeoTIFF tags usually do not say whether those numbers are amplitude, intensity, or magnitude.",
            ]
            print("DATA TYPE FINDING: Single band, real-valued samples.")
            print("  -> This is consistent with amplitude / intensity /")
            print("     magnitude SAR imagery (a processed/detected product),")
            print("     NOT raw complex I/Q radar echo data.")
            print("  -> IMPORTANT LIMITATION: this file, as a plain single-band")
            print("     real TIFF, cannot by itself tell us whether it is")
            print("     amplitude, intensity (power) or magnitude -- that")
            print("     distinction depends on how the SAR processor produced")
            print("     it (commonly documented in the product's own metadata")
            print("     /XML, not in the GeoTIFF tags). BAQ here is applied to")
            print("     'BAQ compression of SAR TIFF numerical data', i.e. the")
            print("     numbers as stored in the TIFF -- NOT a claim that this")
            print("     is the original raw I/Q echo.")
        print("-" * 70)

        # Streaming statistics (min/max/mean/std) without loading full array
        total_min, total_max = np.inf, -np.inf
        sum_x, sum_x2, count = 0.0, 0.0, 0
        for _, window in src.block_windows(band):
            data = src.read(band, window=window)
            if np.iscomplexobj(data):
                data = np.abs(data).astype(np.float64)
            else:
                data = data.astype(np.float64)
            if src.nodata is not None:
                data = data[data != src.nodata]
            if data.size == 0:
                continue
            total_min = min(total_min, float(data.min()))
            total_max = max(total_max, float(data.max()))
            sum_x += float(data.sum())
            sum_x2 += float((data * data).sum())
            count += int(data.size)
        mean = sum_x / count if count else float("nan")
        var = (sum_x2 / count - mean ** 2) if count else float("nan")
        std = math.sqrt(max(var, 0.0))
        print(f"Band {band} statistics (streamed, exact, whole band):")
        print(f"  Number of samples : {count}")
        print(f"  Min               : {total_min}")
        print(f"  Max               : {total_max}")
        print(f"  Mean              : {mean:.6f}")
        print(f"  Std               : {std:.6f}")
        print("=" * 70)

        crs = str(src.crs) if src.crs else None
        transform = list(src.transform)[:6] if src.transform else None
        return {
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "band": band,
            "dtype": str(dtype),
            "is_complex": bool(is_complex),
            "nodata": src.nodata,
            "file_size": file_size,
            "crs": crs,
            "transform": transform,
            "driver": src.driver,
            "compression": compress,
            "tiled": tiled,
            "layout": layout,
            "n_samples": count,
            "min": None if count == 0 else total_min,
            "max": None if count == 0 else total_max,
            "mean": mean,
            "std": std,
            "finding_kind": finding_kind,
            "finding_title": finding_title,
            "finding_body": finding_body,
        }


# =====================================================================
# 5. STREAMING COMPRESSION  (TIFF -> .baq)
# =====================================================================

def choose_chunk_rows(height, block_size, target_mb=64, bytes_per_pixel=4):
    """Pick a chunk height (multiple of block_size) close to target_mb per chunk width."""
    # This is refined once we know width in compress_tiff(); placeholder here.
    return block_size


def compress_tiff(input_path, output_path, block_size=32, bits=4,
                   band=1, chunk_target_mb=64, progress_cb=None):
    """
    Stream-compress ONE band of a TIFF into a .baq file. If the TIFF has
    multiple real bands, call this once per band (see compress_all_bands).
    If the TIFF is complex, this function auto-splits I/Q and stores both.
    """
    if bits not in VALID_BITS:
        raise ValueError(f"Invalid BAQ bits {bits}; must be one of {VALID_BITS}")
    if block_size not in VALID_BLOCK_SIZES:
        raise ValueError(f"Invalid block size {block_size}; must be one of {VALID_BLOCK_SIZES}")
    if not HAVE_RASTERIO:
        raise RuntimeError("rasterio is required. Install with: pip install rasterio")
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input TIFF not found: {input_path}")

    with rasterio.open(input_path) as src:
        width, height = src.width, src.height
        dtype = src.dtypes[band - 1]
        is_complex = np.dtype(dtype).kind == "c"

        # chunk height: multiple of block_size, sized for ~chunk_target_mb per chunk
        bytes_per_px = np.dtype(dtype).itemsize
        rows_for_target = max(1, int((chunk_target_mb * 1024 * 1024) / (width * bytes_per_px)))
        chunk_rows = max(block_size, (rows_for_target // block_size) * block_size)
        n_chunks = math.ceil(height / chunk_rows)

        header = {
            "magic": MAGIC.decode(),
            "width": width,
            "height": height,
            "band": band,
            "source_dtype": str(dtype),
            "is_complex": is_complex,
            "block_size": block_size,
            "bits": bits,
            "chunk_rows": chunk_rows,
            "n_chunks": n_chunks,
            "components": ["real", "imag"] if is_complex else ["real"],
        }

        t0 = time.time()
        with open(output_path, "wb") as fout:
            fout.write(MAGIC)
            header_bytes = json.dumps(header).encode("utf-8")
            fout.write(struct.pack("<I", len(header_bytes)))
            fout.write(header_bytes)

            for ci in range(n_chunks):
                r0 = ci * chunk_rows
                r1 = min(r0 + chunk_rows, height)
                window = Window(0, r0, width, r1 - r0)
                data = src.read(band, window=window)

                comps = [data.real, data.imag] if is_complex else [data]
                for comp in comps:
                    comp = comp.astype(np.float64)
                    records = baq_encode_chunk(comp, block_size, bits)
                    for packed, sigma, n, rows, cols in records:
                        fout.write(struct.pack("<f", sigma))
                        fout.write(packed)

                pct = 100.0 * (ci + 1) / n_chunks
                print(f"Compressing: chunk {ci + 1}/{n_chunks}  "
                      f"({r1 - r0} rows)  Progress: {pct:5.1f}%")
                if progress_cb:
                    progress_cb({
                        "stage": "compress",
                        "chunk": ci + 1,
                        "n_chunks": n_chunks,
                        "rows": r1 - r0,
                        "pct": pct,
                        "message": f"Compressing chunk {ci + 1}/{n_chunks}",
                    })

        elapsed = time.time() - t0

    compressed_size = os.path.getsize(output_path)
    original_size = os.path.getsize(input_path)
    print("-" * 70)
    print(f"Done in {elapsed:.1f}s.")
    print(f"Original file size   : {original_size / (1024**2):.2f} MB")
    print(f"Compressed file size : {compressed_size / (1024**2):.2f} MB "
          f"(header + per-block sigma + packed indices, i.e. FULL side info included)")
    print(f"Compression ratio    : {original_size / compressed_size:.3f} x")
    print(f"Storage saving       : {(1 - compressed_size / original_size) * 100:.2f} %")
    header = dict(header)
    header["elapsed_s"] = elapsed
    header["original_size"] = original_size
    header["compressed_size"] = compressed_size
    header["compression_ratio"] = original_size / compressed_size if compressed_size else None
    header["storage_saving"] = (1 - compressed_size / original_size) * 100 if original_size else None
    return header


# =====================================================================
# 6. STREAMING DECOMPRESSION  (.baq -> TIFF)
# =====================================================================

def _read_header(fin):
    magic = fin.read(len(MAGIC))
    if magic != MAGIC:
        raise ValueError("Corrupted or invalid .baq file: bad magic number.")
    (hlen,) = struct.unpack("<I", fin.read(4))
    header = json.loads(fin.read(hlen).decode("utf-8"))
    return header


def decompress_baq(input_path, output_path, reference_tiff_for_geo=None, progress_cb=None):
    """
    Stream-decompress a .baq file into a reconstructed single-band GeoTIFF,
    never holding the full image in RAM (writes chunk-by-chunk via rasterio).
    """
    if not HAVE_RASTERIO:
        raise RuntimeError("rasterio is required. Install with: pip install rasterio")
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Compressed file not found: {input_path}")

    with open(input_path, "rb") as fin:
        try:
            header = _read_header(fin)
        except Exception as e:
            raise ValueError(f"Failed to parse .baq header (corrupted file?): {e}")

        width, height = header["width"], header["height"]
        block_size, bits = header["block_size"], header["bits"]
        chunk_rows, n_chunks = header["chunk_rows"], header["n_chunks"]
        is_complex = header["is_complex"]
        components = header["components"]

        # geo metadata (optional, taken from a reference tiff if given)
        profile = {
            "driver": "GTiff", "width": width, "height": height, "count": 1,
            "dtype": "complex64" if is_complex else "float32",
            "compress": "deflate",
        }
        if reference_tiff_for_geo and os.path.exists(reference_tiff_for_geo):
            with rasterio.open(reference_tiff_for_geo) as ref:
                profile["crs"] = ref.crs
                profile["transform"] = ref.transform

        with rasterio.open(output_path, "w", **profile) as dst:
            for ci in range(n_chunks):
                r0 = ci * chunk_rows
                r1 = min(r0 + chunk_rows, height)
                rows = r1 - r0
                comp_arrays = []
                for _ in components:
                    n_blocks_r = math.ceil(rows / block_size)
                    n_blocks_c = math.ceil(width / block_size)
                    block_records = []
                    for br in range(n_blocks_r):
                        rr0 = br * block_size
                        rr1 = min(rr0 + block_size, rows)
                        for bc in range(n_blocks_c):
                            cc0 = bc * block_size
                            cc1 = min(cc0 + block_size, width)
                            n_samp = (rr1 - rr0) * (cc1 - cc0)
                            (sigma,) = struct.unpack("<f", fin.read(4))
                            nbytes = packed_byte_length(n_samp, bits)
                            packed = fin.read(nbytes)
                            block_records.append((packed, sigma, n_samp, rr1 - rr0, cc1 - cc0))
                    comp_arrays.append(
                        baq_decode_chunk(block_records, block_size, bits, (rows, width))
                    )

                if is_complex:
                    chunk_out = (comp_arrays[0] + 1j * comp_arrays[1]).astype(np.complex64)
                else:
                    chunk_out = comp_arrays[0].astype(np.float32)

                window = Window(0, r0, width, rows)
                dst.write(chunk_out, 1, window=window)

                pct = 100.0 * (ci + 1) / n_chunks
                print(f"Decompressing: chunk {ci + 1}/{n_chunks}  Progress: {pct:5.1f}%")
                if progress_cb:
                    progress_cb({
                        "stage": "decompress",
                        "chunk": ci + 1,
                        "n_chunks": n_chunks,
                        "pct": pct,
                        "message": f"Decompressing chunk {ci + 1}/{n_chunks}",
                    })

    print(f"Reconstructed TIFF written to: {output_path}")
    return header


# =====================================================================
# 7. MEMORY-EFFICIENT METRICS  (chunk-by-chunk, running accumulators)
# =====================================================================

def compute_metrics(original_tiff, reconstructed_tiff, band=1, compressed_file=None):
    """
    Compute MSE, RMSE, SNR, PSNR by streaming both TIFFs together in
    matching row windows. Only running sums are kept in memory:
    sum_squared_error, sum_signal_power, sample_count.
    """
    with rasterio.open(original_tiff) as src_o, rasterio.open(reconstructed_tiff) as src_r:
        if (src_o.width, src_o.height) != (src_r.width, src_r.height):
            raise ValueError("Original and reconstructed dimensions do not match!")

        width, height = src_o.width, src_o.height
        chunk_rows = max(1, min(2048, height))

        sum_sq_err = 0.0
        sum_sig_pow = 0.0
        n_samples = 0
        data_min, data_max = np.inf, -np.inf

        n_chunks = math.ceil(height / chunk_rows)
        for ci in range(n_chunks):
            r0 = ci * chunk_rows
            r1 = min(r0 + chunk_rows, height)
            window = Window(0, r0, width, r1 - r0)
            orig = src_o.read(band, window=window)
            recon = src_r.read(band, window=window)

            if np.iscomplexobj(orig) or np.iscomplexobj(recon):
                orig_c = orig.astype(np.complex128)
                recon_c = recon.astype(np.complex128)
                err = np.abs(orig_c - recon_c)
                sig = np.abs(orig_c)
                mag = np.abs(orig_c)
                data_min = min(data_min, float(mag.min()))
                data_max = max(data_max, float(mag.max()))
            else:
                orig_f = orig.astype(np.float64)
                recon_f = recon.astype(np.float64)
                err = orig_f - recon_f
                sig = orig_f
                data_min = min(data_min, float(orig_f.min()))
                data_max = max(data_max, float(orig_f.max()))

            sum_sq_err += float(np.sum(np.asarray(err, dtype=np.float64) ** 2))
            sum_sig_pow += float(np.sum(np.asarray(sig, dtype=np.float64) ** 2))
            n_samples += orig.size

        mse = sum_sq_err / n_samples
        rmse = math.sqrt(mse)
        snr = 10 * math.log10(sum_sig_pow / sum_sq_err) if sum_sq_err > 0 else float("inf")
        data_range = data_max - data_min
        psnr = (10 * math.log10((data_range ** 2) / mse)
                if mse > 0 and data_range > 0 else float("inf"))

    original_size = os.path.getsize(original_tiff)
    if compressed_file and os.path.exists(compressed_file):
        compressed_size = os.path.getsize(compressed_file)
        cr = original_size / compressed_size
        saving = (1 - compressed_size / original_size) * 100
    else:
        compressed_size = cr = saving = None

    print("=" * 70)
    print("PERFORMANCE METRICS (streamed, whole-image, exact)")
    print("=" * 70)
    print(f"Samples compared     : {n_samples}")
    print(f"MSE                  : {mse:.6f}")
    print(f"RMSE                 : {rmse:.6f}")
    print(f"SNR                  : {snr:.3f} dB")
    print(f"PSNR                 : {psnr:.3f} dB   (data range used: {data_range:.6f})")
    if cr is not None:
        print(f"Compressed size      : {compressed_size / (1024**2):.2f} MB "
              f"(includes header + per-block scale side-info)")
        print(f"Compression ratio    : {cr:.3f} x")
        print(f"Storage saving       : {saving:.2f} %")
    print("=" * 70)

    return {
        "mse": mse, "rmse": rmse, "snr": snr, "psnr": psnr,
        "compression_ratio": cr, "storage_saving": saving,
        "n_samples": n_samples, "data_range": data_range,
        "original_size": original_size, "compressed_size": compressed_size,
    }


# =====================================================================
# 8. VISUALIZATION  (previews only -- never loads full image)
# =====================================================================

def visualize_comparison(original_tiff, reconstructed_tiff, band=1,
                          preview_size=512, out_path="preview_comparison.png"):
    """
    Build previews by reading a single downsampled overview window (via
    rasterio's out_shape resampling), NOT the full array. Saves a PNG with
    original / reconstructed / error panels plus histograms.
    """
    if not HAVE_MPL:
        print("matplotlib not installed; skipping visualization.")
        return

    with rasterio.open(original_tiff) as src_o:
        scale = min(1.0, preview_size / max(src_o.width, src_o.height))
        out_h = max(1, int(src_o.height * scale))
        out_w = max(1, int(src_o.width * scale))
        orig_prev = src_o.read(band, out_shape=(out_h, out_w),
                                resampling=rasterio.enums.Resampling.average)
    with rasterio.open(reconstructed_tiff) as src_r:
        recon_prev = src_r.read(band, out_shape=(out_h, out_w),
                                 resampling=rasterio.enums.Resampling.average)

    if np.iscomplexobj(orig_prev):
        orig_prev = np.abs(orig_prev)
    if np.iscomplexobj(recon_prev):
        recon_prev = np.abs(recon_prev)
    orig_prev = orig_prev.astype(np.float64)
    recon_prev = recon_prev.astype(np.float64)
    err_prev = orig_prev - recon_prev

    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    vmin, vmax = np.percentile(orig_prev, [2, 98])

    axes[0, 0].imshow(orig_prev, cmap="gray", vmin=vmin, vmax=vmax)
    axes[0, 0].set_title("Original SAR (preview)")
    axes[0, 1].imshow(recon_prev, cmap="gray", vmin=vmin, vmax=vmax)
    axes[0, 1].set_title("Reconstructed SAR (preview)")
    im = axes[0, 2].imshow(err_prev, cmap="RdBu")
    axes[0, 2].set_title("Error (Original - Reconstructed)")
    plt.colorbar(im, ax=axes[0, 2], fraction=0.046)

    axes[1, 0].hist(orig_prev.ravel(), bins=100, color="steelblue")
    axes[1, 0].set_title("Original histogram (preview)")
    axes[1, 1].hist(recon_prev.ravel(), bins=100, color="darkorange")
    axes[1, 1].set_title("Reconstructed histogram (preview)")
    axes[1, 2].hist(err_prev.ravel(), bins=100, color="crimson")
    axes[1, 2].set_title("Error histogram (preview)")

    for ax in axes[0, :2]:
        ax.axis("off")

    fig.suptitle("NOTE: panels use a downsampled PREVIEW for display speed.\n"
                  "Compression, decompression, and all metrics above operate on the FULL dataset.",
                  fontsize=9)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)
    print(f"Saved visualization: {out_path}")


# =====================================================================
# 9. EXPERIMENT MODULE  (sweep block size x bits)
# =====================================================================

def run_experiments(input_tiff, block_sizes, bit_list, work_dir="experiments_tmp",
                     band=1, chunk_target_mb=64):
    os.makedirs(work_dir, exist_ok=True)
    results = []
    for bs in block_sizes:
        for bits in bit_list:
            tag = f"bs{bs}_b{bits}"
            baq_path = os.path.join(work_dir, f"{tag}.baq")
            recon_path = os.path.join(work_dir, f"{tag}_recon.tif")
            print(f"\n--- Experiment: block_size={bs}, bits={bits} ---")
            try:
                compress_tiff(input_tiff, baq_path, block_size=bs, bits=bits,
                               band=band, chunk_target_mb=chunk_target_mb)
                decompress_baq(baq_path, recon_path, reference_tiff_for_geo=input_tiff)
                m = compute_metrics(input_tiff, recon_path, band=band,
                                     compressed_file=baq_path)
                results.append({
                    "block_size": bs, "bits": bits,
                    "compressed_size_MB": os.path.getsize(baq_path) / (1024 ** 2),
                    "compression_ratio": m["compression_ratio"],
                    "storage_saving": m["storage_saving"],
                    "mse": m["mse"], "rmse": m["rmse"],
                    "snr": m["snr"], "psnr": m["psnr"],
                })
            finally:
                # free disk: keep results only, remove big intermediate files
                for p in (baq_path, recon_path):
                    if os.path.exists(p):
                        os.remove(p)

    print("\n" + "=" * 100)
    header = (f"{'BlockSize':>9} | {'Bits':>4} | {'CompSize(MB)':>12} | "
              f"{'CR':>6} | {'Saving%':>8} | {'MSE':>10} | {'RMSE':>8} | "
              f"{'SNR(dB)':>8} | {'PSNR(dB)':>9}")
    print(header)
    print("-" * 100)
    for r in results:
        print(f"{r['block_size']:>9} | {r['bits']:>4} | {r['compressed_size_MB']:>12.2f} | "
              f"{r['compression_ratio']:>6.2f} | {r['storage_saving']:>8.2f} | "
              f"{r['mse']:>10.4f} | {r['rmse']:>8.4f} | {r['snr']:>8.2f} | {r['psnr']:>9.2f}")
    print("=" * 100)

    if HAVE_MPL and results:
        _plot_experiments(results, work_dir)
    return results


def _plot_experiments(results, out_dir):
    import collections
    by_bs = collections.defaultdict(list)
    for r in results:
        by_bs[r["block_size"]].append(r)

    def series(field, fixed_bs=None):
        rows = [r for r in results if fixed_bs is None or r["block_size"] == fixed_bs]
        rows.sort(key=lambda r: r["bits"])
        return [r["bits"] for r in rows], [r[field] for r in rows]

    ref_bs = sorted(by_bs.keys())[len(by_bs) // 2]  # a representative block size

    def plot_vs_bits(field, ylabel, fname):
        plt.figure(figsize=(6, 4))
        x, y = series(field, fixed_bs=ref_bs)
        plt.plot(x, y, marker="o")
        plt.xlabel("BAQ bits/sample")
        plt.ylabel(ylabel)
        plt.title(f"{ylabel} vs BAQ bits (block size={ref_bs})")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, fname), dpi=130)
        plt.close()

    plot_vs_bits("compression_ratio", "Compression Ratio", "graph1_cr_vs_bits.png")
    plot_vs_bits("snr", "SNR (dB)", "graph2_snr_vs_bits.png")
    plot_vs_bits("mse", "MSE", "graph3_mse_vs_bits.png")
    plot_vs_bits("storage_saving", "Storage Saving (%)", "graph5_saving_vs_bits.png")

    plt.figure(figsize=(6, 4))
    distinct_bits = sorted({r["bits"] for r in results})
    ref_bits = distinct_bits[len(distinct_bits) // 2] if distinct_bits else None
    rows = [r for r in results if r["bits"] == ref_bits]
    rows.sort(key=lambda r: r["block_size"])
    plt.plot([r["block_size"] for r in rows], [r["compression_ratio"] for r in rows], marker="o")
    plt.xlabel("Block size")
    plt.ylabel("Compression Ratio")
    plt.title(f"Compression Ratio vs Block Size (bits={ref_bits})")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "graph4_cr_vs_blocksize.png"), dpi=130)
    plt.close()
    print(f"Saved 5 graphs to {out_dir}/")


# =====================================================================
# 10. CONFIG  --  EDIT THESE VALUES FOR YOUR OWN RUN
# =====================================================================
#
# Only ONE thing is asked of you: the input TIFF path (either edit
# INPUT_TIFF below, or just type/paste the path when the script asks for
# it at runtime -- pressing Enter with nothing typed uses INPUT_TIFF).
#
# ALL OUTPUT FILES ARE WRITTEN AUTOMATICALLY into OUTPUT_DIR below --
# you never have to specify output paths yourself.
#
# Then just run:
#
#       python sar_baq.py
#
# It will automatically: inspect -> compress -> decompress -> compute
# metrics -> save a visual comparison PNG -> (optionally) run the
# block-size/bit-rate experiment sweep and save graphs.

# Default input TIFF path (used if you just press Enter at the prompt).
# You can also hardcode your exact file here so it never asks at all.
INPUT_TIFF = r"C:\Users\asus\Desktop\sar\data\input\sar.tiff"

# Every output file/folder is created automatically inside this directory.
OUTPUT_DIR = r"C:\Users\asus\Desktop\sar\data"

CONFIG = {
    # ---- input (can be overridden by the runtime prompt, see bottom of file) ----
    "input_tiff": INPUT_TIFF,

    # ---- outputs: all auto-derived from OUTPUT_DIR, nothing to edit here ----
    "output_baq":                os.path.join(OUTPUT_DIR, "compressed.baq"),
    "output_reconstructed_tiff": os.path.join(OUTPUT_DIR, "reconstructed.tiff"),
    "output_preview_png":        os.path.join(OUTPUT_DIR, "preview_comparison.png"),
    "experiment_dir":            os.path.join(OUTPUT_DIR, "experiments"),

    # ---- BAQ parameters ----
    "block_size": 32,      # one of 8, 16, 32, 64, 128
    "bits": 4,             # one of 2, 3, 4, 5, 6
    "band": 1,             # which band to process (1 = first band)
    "chunk_target_mb": 64, # approx. MB per streamed chunk (memory knob)

    # ---- what to run ----
    "run_experiments": False,          # set True to also sweep block sizes x bits
    "experiment_block_sizes": [16, 32, 64],
    "experiment_bits": [2, 3, 4, 5, 6],
}


# =====================================================================
# 11. AUTOMATIC END-TO-END PIPELINE  (runs once, no interaction)
# =====================================================================

def run_full_pipeline(cfg):
    print("#" * 70)
    print("# SAR BAQ COMPRESSION -- FULL AUTOMATIC PIPELINE")
    print("#" * 70)

    in_tiff = cfg["input_tiff"]
    if not os.path.exists(in_tiff):
        print(f"\nERROR: input TIFF not found at '{in_tiff}'.")
        print("Edit the CONFIG dict at the top of this file (or place your")
        print("file at that exact path) and run again.")
        sys.exit(1)

    os.makedirs(os.path.dirname(cfg["output_baq"]) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(cfg["output_reconstructed_tiff"]) or ".", exist_ok=True)

    # ---- STEP 1: inspect ----
    print("\n" + "#" * 25 + " STEP 1/5: INSPECT TIFF " + "#" * 25)
    inspect_tiff(in_tiff)

    # ---- STEP 2: compress ----
    print("\n" + "#" * 25 + " STEP 2/5: COMPRESS (BAQ) " + "#" * 23)
    compress_tiff(
        in_tiff, cfg["output_baq"],
        block_size=cfg["block_size"], bits=cfg["bits"],
        band=cfg["band"], chunk_target_mb=cfg["chunk_target_mb"],
    )

    # ---- STEP 3: decompress ----
    print("\n" + "#" * 25 + " STEP 3/5: DECOMPRESS " + "#" * 27)
    decompress_baq(
        cfg["output_baq"], cfg["output_reconstructed_tiff"],
        reference_tiff_for_geo=in_tiff,
    )

    # ---- STEP 4: metrics ----
    print("\n" + "#" * 25 + " STEP 4/5: METRICS " + "#" * 30)
    metrics = compute_metrics(
        in_tiff, cfg["output_reconstructed_tiff"],
        band=cfg["band"], compressed_file=cfg["output_baq"],
    )

    # ---- STEP 5: visualization ----
    print("\n" + "#" * 25 + " STEP 5/5: VISUALIZATION " + "#" * 24)
    visualize_comparison(
        in_tiff, cfg["output_reconstructed_tiff"],
        band=cfg["band"], out_path=cfg["output_preview_png"],
    )

    # ---- optional: experiment sweep ----
    if cfg["run_experiments"]:
        print("\n" + "#" * 20 + " BONUS: BLOCK SIZE x BITS EXPERIMENT SWEEP " + "#" * 20)
        run_experiments(
            in_tiff, cfg["experiment_block_sizes"], cfg["experiment_bits"],
            work_dir=cfg["experiment_dir"], band=cfg["band"],
            chunk_target_mb=cfg["chunk_target_mb"],
        )

    # ---- final summary ----
    print("\n" + "#" * 70)
    print("# PIPELINE COMPLETE -- SUMMARY")
    print("#" * 70)
    print(f"Compressed file        : {cfg['output_baq']}")
    print(f"Reconstructed TIFF     : {cfg['output_reconstructed_tiff']}")
    print(f"Preview PNG            : {cfg['output_preview_png']}")
    print(f"BAQ bits/sample used   : {cfg['bits']}")
    print(f"Block size used        : {cfg['block_size']}")
    print(f"Compression ratio      : {metrics['compression_ratio']:.3f} x")
    print(f"Storage saving         : {metrics['storage_saving']:.2f} %")
    print(f"MSE / RMSE             : {metrics['mse']:.4f} / {metrics['rmse']:.4f}")
    print(f"SNR / PSNR              : {metrics['snr']:.2f} dB / {metrics['psnr']:.2f} dB")
    print("#" * 70)


if __name__ == "__main__":
    if not HAVE_RASTERIO:
        print("ERROR: rasterio is required. Install with: pip install rasterio")
        sys.exit(1)
    print(f"Input TIFF path [{CONFIG['input_tiff']}]: ", end="")
    typed = input().strip().strip('"')  # strip quotes if pasted from Windows "Copy as path"
    if typed:
        CONFIG["input_tiff"] = typed

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"All outputs will be written automatically to: {OUTPUT_DIR}\n")

    run_full_pipeline(CONFIG)
