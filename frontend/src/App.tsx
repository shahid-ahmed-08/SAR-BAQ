import React, { useEffect, useMemo, useRef, useState } from "react"
import { api, watchJob, type Health, type InspectInfo, type Job, type LibraryFile } from "./api"

type View = "input" | "pipeline" | "compare" | "metrics" | "experiments"

interface NavItem {
  id: View
  stepNum: string
  label: string
  hint: string
}

const navItems: NavItem[] = [
  { id: "input", stepNum: "01", label: "Input & Inspect", hint: "Choose & inspect GeoTIFF" },
  { id: "pipeline", stepNum: "02", label: "Pipeline", hint: "Compress & reconstruct" },
  { id: "compare", stepNum: "03", label: "Comparison", hint: "Visual residual & preview" },
  { id: "metrics", stepNum: "04", label: "Metrics", hint: "Full-image quality & ratio" },
  { id: "experiments", stepNum: "05", label: "Experiments", hint: "Parameter sweep analysis" },
]

function formatBytes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—"
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(2)} MB`
  return `${(n / 1024 ** 3).toFixed(3)} GB`
}

function formatNum(n: number | null | undefined, digits = 3): string {
  if (n == null || Number.isNaN(n)) return "—"
  if (!Number.isFinite(n)) return "∞"
  return n.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    file: <><path d="M6 2h7l5 5v15H6z" /><path d="M13 2v6h5M9 13h6M9 17h6" /></>,
    play: <path d="m8 5 11 7-11 7z" />,
    check: <path d="m4 12 5 5L20 6" />,
    grid: <><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /></>,
    chart: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,
    compare: <><rect x="3" y="4" width="7" height="16" /><rect x="14" y="4" width="7" height="16" /></>,
    flask: <><path d="M9 3h6M10 3v6l-6 9a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-6-9V3" /><path d="M7 16h10" /></>,
    folder: <><path d="M3 6h7l2 2h9v11H3z" /></>,
    alert: <><path d="M12 3 2 21h20z" /><path d="M12 9v5M12 18h.01" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
    upload: <><path d="M12 21V9m-5 5 5-5 5 5" /><path d="M4 4h16" /></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5" /><path d="M4 20h16" /></>,
    copy: <><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>,
    zoom: <><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /><path d="M11 8v6M8 11h6" /></>,
    refresh: <><path d="M21.5 2v6h-6M2.5 22v-6h6" /><path d="M20.5 13A9 9 0 0 1 5.7 18.3L2.5 16m19-8-3.2 2.3A9 9 0 0 1 3.5 11" /></>,
    book: <><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z" /><path d="M6 6h10M6 10h10" /></>,
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name] || paths.info}
    </svg>
  )
}

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  )
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  )
}

function EmptyState({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return (
    <div className="empty" role="status">
      <Icon name="file" size={32} />
      <strong>{title}</strong>
      <span>{body}</span>
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  )
}

export default function App() {
  const [view, setView] = useState<View>("input")
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState("")
  const [library, setLibrary] = useState<LibraryFile[]>([])
  const [path, setPath] = useState("")
  const [block, setBlock] = useState(32)
  const [bits, setBits] = useState(4)
  const [band, setBand] = useState(1)
  const [chunkMb, setChunkMb] = useState(64)
  const [experiments, setExperiments] = useState(false)
  const [inspecting, setInspecting] = useState(false)
  const [info, setInfo] = useState<InspectInfo | null>(null)
  const [notice, setNotice] = useState("")
  const [noticeTone, setNoticeTone] = useState<"danger" | "info" | "warning">("danger")
  const [job, setJob] = useState<Job | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [theoryOpen, setTheoryOpen] = useState(false)
  const [zoomImage, setZoomImage] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [sortField, setSortField] = useState<string>("bits")
  const [sortAsc, setSortAsc] = useState<boolean>(true)

  const fileRef = useRef<HTMLInputElement>(null)
  const logRef = useRef<HTMLPreElement>(null)

  const running = job?.status === "running" || job?.status === "queued"
  const done = job?.status === "done"
  const result = (job?.result || null) as Record<string, any> | null
  const metrics = result?.metrics as Record<string, number> | undefined
  const header = result?.header as Record<string, any> | undefined
  const graphs: string[] = result?.graphs || []

  const settings = useMemo(() => ({
    path,
    block_size: block,
    bits,
    band,
    chunk_target_mb: chunkMb,
    run_experiments: experiments,
  }), [path, block, bits, band, chunkMb, experiments])

  const refreshLibrary = async () => {
    try {
      const data = await api.files()
      setLibrary(data.files)
      // If path is empty and library has a file, default to the first TIFF
      if (!path && data.files.length > 0) {
        const firstTiff = data.files.find(f => f.kind === "tiff") || data.files[0]
        setPath(firstTiff.path)
      }
    } catch {
      /* ignore */
    }
  }

  const checkHealth = () => {
    api.health()
      .then((h) => {
        setHealth(h)
        setHealthError("")
      })
      .catch((err: Error) => {
        setHealthError(err.message || "Cannot reach the SAR BAQ API on 127.0.0.1:8000. Start it with 'python api.py'")
      })
  }

  useEffect(() => {
    checkHealth()
    refreshLibrary()
    const timer = setInterval(checkHealth, 5000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!job?.id) return
    return watchJob(job.id, (updated) => {
      setJob(updated)
    })
  }, [job?.id])

  useEffect(() => {
    if (job?.status === "done" && job.kind === "pipeline") {
      setView("metrics")
    }
    if (job?.status === "error") {
      setNotice(job.error || job.message || "Job failed")
      setNoticeTone("danger")
    }
  }, [job?.status, job?.kind, job?.error, job?.message])

  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [job?.logs, autoScroll])

  const showNotice = (text: string, tone: "danger" | "info" | "warning" = "danger") => {
    setNotice(text)
    setNoticeTone(tone)
  }

  const copyToClipboard = (text: string, key: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedKey(key)
      setTimeout(() => setCopiedKey(null), 2000)
    })
  }

  const inspect = async (targetPath = path) => {
    if (!targetPath) {
      showNotice("Select or enter a GeoTIFF path first.")
      return
    }
    setInspecting(true)
    setNotice("")
    try {
      const data = await api.inspect(targetPath, band)
      setInfo(data)
      setPath(data.path)
      showNotice(`Successfully inspected ${data.name} (${data.width} × ${data.height}) in streamed mode.`, "info")
    } catch (err) {
      setInfo(null)
      showNotice(err instanceof Error ? err.message : "Inspection failed")
    } finally {
      setInspecting(false)
    }
  }

  const onUpload = async (file: File) => {
    try {
      showNotice(`Uploading ${file.name} to data/input…`, "info")
      const uploaded = await api.upload(file)
      await refreshLibrary()
      setPath(uploaded.path)
      if (uploaded.kind === "tiff") {
        await inspect(uploaded.path)
      } else {
        showNotice("Uploaded a .baq file. You can decompress it on the Pipeline page.", "info")
      }
    } catch (err) {
      showNotice(err instanceof Error ? err.message : "Upload failed")
    }
  }

  const startJob = async (kind: "pipeline" | "compress") => {
    if (!path) {
      showNotice("Select a GeoTIFF before starting.")
      setView("input")
      return
    }
    setNotice("")
    try {
      const created = kind === "pipeline" ? await api.pipeline(settings) : await api.compress(settings)
      setJob(created)
      setView("pipeline")
    } catch (err) {
      showNotice(err instanceof Error ? err.message : "Failed to start execution")
    }
  }

  const startDecompress = async () => {
    const baq = library.find((f) => f.kind === "baq")
    const baqPath = path.toLowerCase().endsWith(".baq") ? path : baq?.path
    if (!baqPath) {
      showNotice("Provide a .baq file path, or upload one to data/input.")
      return
    }
    try {
      const created = await api.decompress(baqPath, info?.path)
      setJob(created)
      setView("pipeline")
    } catch (err) {
      showNotice(err instanceof Error ? err.message : "Failed to start decompression")
    }
  }

  const askRun = () => {
    if (!health?.rasterio) {
      showNotice("rasterio is required to run the pipeline.")
      return
    }
    if (!path) {
      showNotice("Select or enter a GeoTIFF path before running.")
      setView("input")
      return
    }
    setConfirmOpen(true)
  }

  const exportMetricsJSON = () => {
    if (!metrics) return
    const exportData = {
      job_id: job?.id,
      input_file: info?.name || path,
      parameters: {
        block_size: result?.block_size || block,
        bits: result?.bits || bits,
        band: result?.band || band,
      },
      metrics,
      header,
      generated_at: new Date().toISOString(),
    }
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `sar_baq_metrics_${job?.id || "run"}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const exportExperimentsCSV = () => {
    if (!Array.isArray(result?.experiments)) return
    const headers = ["Block", "Bits", "Compressed_MB", "Compression_Ratio", "Storage_Saving_Pct", "MSE", "RMSE", "SNR_dB", "PSNR_dB"]
    const rows = result.experiments.map((r: Record<string, number>) => [
      r.block_size,
      r.bits,
      r.compressed_size_MB?.toFixed(2),
      r.compression_ratio?.toFixed(2),
      r.storage_saving?.toFixed(2),
      r.mse?.toFixed(4),
      r.rmse?.toFixed(4),
      r.snr?.toFixed(2),
      r.psnr?.toFixed(2),
    ])
    const csvContent = [headers.join(","), ...rows.map(row => row.join(","))].join("\n")
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `sar_baq_experiments_${job?.id || "sweep"}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const sortedExperiments = useMemo(() => {
    if (!Array.isArray(result?.experiments)) return []
    const copy = [...result.experiments]
    copy.sort((a, b) => {
      const valA = a[sortField] ?? 0
      const valB = b[sortField] ?? 0
      return sortAsc ? valA - valB : valB - valA
    })
    return copy
  }, [result?.experiments, sortField, sortAsc])

  const toggleSort = (field: string) => {
    if (sortField === field) {
      setSortAsc(!sortAsc)
    } else {
      setSortField(field)
      setSortAsc(true)
    }
  }

  const stages = ["inspect", "compress", "decompress", "metrics", "preview", "experiments", "done"]
  const currentStageIndex = stages.indexOf(job?.stage || "")
  const stepLabels = ["Inspect", "Compress", "Decompress", "Metrics", "Preview"]

  return (
    <div className="app-shell">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div>
            <strong>SAR BAQ</strong>
            <small>Block Adaptive Quantization</small>
          </div>
        </div>

        <p className="side-label">Workflow</p>
        <nav aria-label="Workspace navigation">
          {navItems.map((item) => {
            const isActive = view === item.id
            const isCompleted = (item.id === "input" && !!info) ||
              (item.id === "pipeline" && done) ||
              (item.id === "compare" && !!result?.preview_available) ||
              (item.id === "metrics" && !!metrics) ||
              (item.id === "experiments" && graphs.length > 0)

            return (
              <button
                key={item.id}
                className={`nav-item ${isActive ? "active" : ""}`}
                onClick={() => setView(item.id)}
                title={item.hint}
              >
                <Icon
                  name={
                    item.id === "input" ? "file" :
                    item.id === "pipeline" ? "grid" :
                    item.id === "compare" ? "compare" :
                    item.id === "metrics" ? "chart" : "flask"
                  }
                  size={18}
                />
                <span>
                  <b>{item.label}</b>
                  <small>{item.hint}</small>
                </span>
                {isCompleted && <i className="dot done" title="Step completed" />}
              </button>
            )
          })}
        </nav>

        <div className="sidebar-bottom">
          <button className="about-toggle" onClick={() => setTheoryOpen(true)}>
            <Icon name="book" size={16} />
            <span>How SAR BAQ Works</span>
          </button>

          <div className="lossy-note">
            <strong>Gaussian Lloyd–Max</strong>
            <span>Adapts to local radar reflectivity variance (&sigma;) and packs non-uniform decision levels.</span>
          </div>

          <div className="version">
            <span>Engine Core</span>
            <Badge tone={health?.rasterio ? "success" : healthError ? "danger" : "warning"}>
              {health?.rasterio ? "Online" : healthError ? "Offline" : "Missing Libs"}
            </Badge>
          </div>
        </div>
      </aside>

      {/* Main Workspace */}
      <main id="main">
        {/* Sticky Topbar */}
        <header className="topbar">
          <div className="job-title">
            <span className={`status-dot ${job?.status || "idle"}`} />
            <div>
              <small>Active Input</small>
              <strong>{info?.name || (path ? path.split(/[\\/]/).pop() : "No file selected")}</strong>
            </div>
            <Badge tone={job?.status === "done" ? "success" : running ? "running" : job?.status === "error" ? "danger" : "neutral"}>
              {running ? "Streaming" : job?.status === "done" ? "Complete" : job?.status === "error" ? "Failed" : "Ready"}
            </Badge>
          </div>

          <div className="output-destination">
            <Icon name="folder" size={16} />
            <div>
              <small>Output Destination</small>
              <span>{result?.paths?.baq || health?.output_dir || "data/output/<job_id>"}</span>
            </div>
          </div>

          <button className="primary top-run" onClick={askRun} disabled={running || inspecting}>
            <Icon name="play" size={16} />
            {running ? "Processing…" : "Run Full Pipeline"}
          </button>
        </header>

        {/* Live Progress Banner */}
        {running && (
          <div className="progress-banner" role="status" aria-live="polite">
            <div>
              <span className="spinner" />
              <div>
                <strong>{job?.message || "Streaming data…"}</strong>
                <span>
                  {job?.chunk && job?.n_chunks ? `Chunk ${job.chunk} of ${job.n_chunks}` : "Streaming chunks through windowed memory buffer"}
                </span>
              </div>
            </div>
            <b>{Math.round(job?.pct || 0)}%</b>
            <div className="progress-track">
              <i style={{ width: `${Math.max(2, job?.pct || 0)}%` }} />
            </div>
          </div>
        )}

        {/* Body Content */}
        <section className="content">
          {/* Health Alert */}
          {(healthError || (health && !health.rasterio)) && (
            <div className="alert danger">
              <Icon name="alert" size={20} />
              <div>
                <strong>{healthError ? "Backend API Unreachable" : "Rasterio Dependency Missing"}</strong>
                <span>
                  {healthError
                    ? "Start the backend API with 'python api.py' in the terminal. The UI will automatically connect."
                    : "Install rasterio with 'pip install rasterio' or 'conda install rasterio', then restart python api.py."}
                </span>
              </div>
              <button onClick={checkHealth}>Retry Connection</button>
            </div>
          )}

          {/* User Notice */}
          {notice && (
            <div className={`alert ${noticeTone}`}>
              <Icon name={noticeTone === "info" ? "info" : "alert"} size={18} />
              <div>
                <strong>{noticeTone === "info" ? "Notice" : noticeTone === "warning" ? "Caution" : "Attention"}</strong>
                <span>{notice}</span>
              </div>
              <button onClick={() => setNotice("")}>Dismiss</button>
            </div>
          )}

          {/* VIEW 1: INPUT & INSPECT */}
          {view === "input" && (
            <>
              <div className="page-heading">
                <div>
                  <p>01 / INPUT & INSPECTION</p>
                  <h1>Select & Inspect GeoTIFF</h1>
                  <span>Local streaming inspection reads metadata and calculates exact sample statistics without exhausting RAM.</span>
                </div>
                {info && <Badge tone="success"><Icon name="check" size={14} /> Inspected</Badge>}
              </div>

              <div className="two-col wide-left">
                <div className="stack">
                  {/* File Selection Card */}
                  <div
                    className={`card dropzone ${dragOver ? "over" : ""}`}
                    onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={(e) => {
                      e.preventDefault()
                      setDragOver(false)
                      const f = e.dataTransfer.files[0]
                      if (f) onUpload(f)
                    }}
                  >
                    <div className="card-head">
                      <div>
                        <h2>GeoTIFF Source</h2>
                        <p>Select an existing file from the local library, enter a disk path, or upload a scene.</p>
                      </div>
                      <button
                        className="secondary"
                        onClick={refreshLibrary}
                        title="Refresh file list"
                        style={{ height: 32, padding: "0 10px" }}
                      >
                        <Icon name="refresh" size={14} />
                      </button>
                    </div>

                    <Field label="File Path" hint="Absolute path or relative path inside data/input">
                      <div className="input-row">
                        <input
                          value={path}
                          onChange={(e) => setPath(e.target.value)}
                          placeholder="e.g. data/input/sample_sentinel1_vv.tif"
                        />
                        <button
                          className="secondary"
                          type="button"
                          onClick={() => inspect()}
                          disabled={inspecting || !path}
                        >
                          {inspecting ? "Inspecting…" : "Inspect"}
                        </button>
                      </div>
                    </Field>

                    <div className="upload-row">
                      <input
                        ref={fileRef}
                        type="file"
                        accept=".tif,.tiff,.geotiff,.baq"
                        hidden
                        onChange={(e) => {
                          const f = e.target.files?.[0]
                          if (f) onUpload(f)
                          e.target.value = ""
                        }}
                      />
                      <button className="secondary" type="button" onClick={() => fileRef.current?.click()}>
                        <Icon name="upload" size={16} /> Choose File to Upload
                      </button>
                      <small>Drag & drop .tif / .baq files directly into this card</small>
                    </div>

                    {/* Local File Library */}
                    {library.length > 0 ? (
                      <div style={{ marginTop: 14 }}>
                        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-secondary)" }}>
                          Local Files ({library.length})
                        </span>
                        <ul className="file-list">
                          {library.map((f) => (
                            <li key={f.path}>
                              <button
                                className={`file-row ${path === f.path ? "selected" : ""}`}
                                onClick={() => {
                                  setPath(f.path)
                                  if (f.kind === "tiff") inspect(f.path)
                                }}
                              >
                                <div className="file-icon">
                                  <Icon name={f.kind === "baq" ? "grid" : "file"} size={18} />
                                </div>
                                <div>
                                  <strong>{f.name}</strong>
                                  <span>{f.kind.toUpperCase()} Scene · {formatBytes(f.size)}</span>
                                </div>
                                {path === f.path && <Badge tone="success">Selected</Badge>}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : (
                      <EmptyState
                        title="No files in data/input"
                        body="Drop a GeoTIFF here, upload one, or paste an absolute file path on this PC."
                      />
                    )}

                    <div className="field-grid">
                      <Field label="Processing Band" hint="BAQ compresses one selected band per run">
                        <select value={band} onChange={(e) => setBand(Number(e.target.value))}>
                          {Array.from({ length: info?.count || 4 }, (_, i) => i + 1).map((n) => (
                            <option key={n} value={n}>Band {n}</option>
                          ))}
                        </select>
                      </Field>
                    </div>
                  </div>
                </div>

                {/* Right Stack: Inspection Metadata */}
                <div className="stack">
                  {info ? (
                    <>
                      {/* Raster Identity */}
                      <div className="card">
                        <div className="card-head">
                          <div>
                            <h2>Raster Identity</h2>
                            <p>Streamed header & structure from rasterio</p>
                          </div>
                          <Badge tone="running">Band {info.band}</Badge>
                        </div>

                        <div className="metadata-grid">
                          <MetricCard
                            label="Dimensions"
                            value={`${info.width.toLocaleString()} × ${info.height.toLocaleString()}`}
                            detail={`${(info.n_samples / 1e6).toFixed(2)} M samples`}
                          />
                          <MetricCard
                            label="Bands"
                            value={String(info.count)}
                            detail={`active band ${info.band}`}
                          />
                          <MetricCard
                            label="Data Type"
                            value={info.dtype}
                            detail={info.is_complex ? "Complex I/Q" : "Real-valued"}
                          />
                          <MetricCard
                            label="NoData Value"
                            value={info.nodata == null ? "None" : String(info.nodata)}
                            detail="Excluded from quantizer"
                          />
                          <MetricCard
                            label="Driver"
                            value={info.driver}
                            detail={info.compression || "uncompressed"}
                          />
                          <MetricCard
                            label="File Size"
                            value={formatBytes(info.file_size)}
                            detail={`${info.file_size.toLocaleString()} bytes`}
                          />
                        </div>

                        <div className="meta-lines">
                          <div>
                            <span>CRS</span>
                            <code>{info.crs || "Unprojected / Not georeferenced"}</code>
                          </div>
                          <div>
                            <span>Transform</span>
                            <code>
                              {info.transform ? `[${info.transform.map((n) => formatNum(n, 2)).join(", ")}]` : "—"}
                            </code>
                          </div>
                          <div>
                            <span>Storage</span>
                            <code>{info.layout}</code>
                          </div>
                        </div>
                      </div>

                      {/* Streamed Stats */}
                      <div className="card">
                        <div className="card-head">
                          <div>
                            <h2>Full Raster Statistics</h2>
                            <p>Exact streamed values across all {info.n_samples.toLocaleString()} samples</p>
                          </div>
                        </div>
                        <div className="stats-list">
                          <div><span>Minimum Amplitude</span><b>{formatNum(info.min, 4)}</b></div>
                          <div><span>Maximum Amplitude</span><b>{formatNum(info.max, 4)}</b></div>
                          <div><span>Mean (&mu;)</span><b>{formatNum(info.mean, 4)}</b></div>
                          <div><span>Standard Deviation (&sigma;)</span><b>{formatNum(info.std, 4)}</b></div>
                        </div>
                      </div>

                      {/* Scientific Finding */}
                      <div className="card finding">
                        <div className="finding-title">
                          <Icon name="info" size={18} />
                          <h2>{info.finding_title}</h2>
                        </div>
                        {info.finding_body.map((p, i) => (
                          <p key={i}>{p}</p>
                        ))}
                        <div className="plain-note">
                          BAQ relies on the local Gaussian assumption. For real SAR intensity products, Lloyd–Max quantization optimizes MSE against local block RMS.
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="card">
                      <EmptyState
                        title="Inspection Required"
                        body="Select a file and click Inspect to examine metadata, real vs complex type, and streamed statistics before configuring the compressor."
                        action={
                          <button className="primary" onClick={() => inspect()} disabled={!path || inspecting}>
                            <Icon name="play" size={14} /> Inspect File
                          </button>
                        }
                      />
                    </div>
                  )}
                </div>
              </div>
            </>
          )}

          {/* VIEW 2: PIPELINE */}
          {view === "pipeline" && (
            <>
              <div className="page-heading">
                <div>
                  <p>02 / PIPELINE SETUP</p>
                  <h1>Configure Compression Pipeline</h1>
                  <span>Fine-tune Block Adaptive Quantization parameters and run streaming jobs.</span>
                </div>
              </div>

              <div className="two-col">
                {/* Settings Card */}
                <div className="card">
                  <div className="card-head">
                    <div>
                      <h2>Quantizer Parameters</h2>
                      <p>Hardware-supported block sizes and Lloyd-Max bit depths</p>
                    </div>
                    <Badge tone="running">Band {band}</Badge>
                  </div>

                  <div className="field-grid">
                    <Field label="Block Size (Samples)" hint="Local RMS scaling window (8 to 128)">
                      <select value={block} onChange={(e) => setBlock(Number(e.target.value))}>
                        {(health?.valid_block_sizes || [8, 16, 32, 64, 128]).map((n) => (
                          <option key={n} value={n}>{n} samples / block</option>
                        ))}
                      </select>
                    </Field>

                    <Field label="Bit Depth (Bits/Sample)" hint="Quantization bits (2 to 6 bits)">
                      <select value={bits} onChange={(e) => setBits(Number(e.target.value))}>
                        {(health?.valid_bits || [2, 3, 4, 5, 6]).map((n) => (
                          <option key={n} value={n}>
                            {n} bits ({n === 2 ? "~93% saving" : n === 4 ? "Balanced (32 dB)" : n === 6 ? "High fidelity (>38 dB)" : `${n} bits`})
                          </option>
                        ))}
                      </select>
                    </Field>

                    <Field label="Chunk Memory Budget" hint="Row-strip memory target per iteration">
                      <div className="with-unit">
                        <input
                          type="number"
                          min={8}
                          max={512}
                          value={chunkMb}
                          onChange={(e) => setChunkMb(Number(e.target.value) || 64)}
                        />
                        <span>MB</span>
                      </div>
                    </Field>

                    <Field label="Selected Band" hint="Active band to compress">
                      <select value={band} onChange={(e) => setBand(Number(e.target.value))}>
                        {Array.from({ length: info?.count || 4 }, (_, i) => i + 1).map((n) => (
                          <option key={n} value={n}>Band {n}</option>
                        ))}
                      </select>
                    </Field>
                  </div>

                  <label className="toggle-row">
                    <div>
                      <strong>Run Parameter Sweep (15 Configurations)</strong>
                      <span>Sweeps block sizes (16, 32, 64) × bit depths (2–6) and generates report plots.</span>
                    </div>
                    <input
                      type="checkbox"
                      checked={experiments}
                      onChange={(e) => setExperiments(e.target.checked)}
                    />
                  </label>

                  <button
                    className="primary full"
                    onClick={askRun}
                    disabled={running}
                    style={{ height: 42, fontSize: 13 }}
                  >
                    <Icon name="play" size={18} />
                    {running ? "Pipeline Running…" : "Start Full Pipeline (Compress → Decompress → Metrics)"}
                  </button>
                </div>

                {/* Right Stack: Pipeline Stages & Live Log */}
                <div className="stack">
                  {/* Step Sequence */}
                  <div className="card">
                    <div className="card-head">
                      <div>
                        <h2>Pipeline Stages</h2>
                        <p>Streamed memory architecture ensures low RAM usage</p>
                      </div>
                    </div>
                    <div className="step-list">
                      {stepLabels.map((s, i) => (
                        <div
                          key={s}
                          className={done || i < currentStageIndex ? "complete" : i === currentStageIndex && running ? "current" : ""}
                        >
                          <i>
                            {done || i < currentStageIndex ? <Icon name="check" size={14} /> : i + 1}
                          </i>
                          <span>
                            <strong>{s}</strong>
                            <small>
                              {[
                                "Read metadata and calculate streamed statistics",
                                "Compute block RMS and pack Lloyd–Max bit indices into .baq",
                                "Unpack bits and reconstruct georeferenced GeoTIFF",
                                "Compute full-raster MSE, RMSE, SNR, PSNR & ratio",
                                "Generate comparison image and histogram figure",
                              ][i]}
                            </small>
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Standalone Actions */}
                  <div className="card">
                    <div className="card-head">
                      <div>
                        <h2>Standalone Jobs</h2>
                        <p>Execute individual pipeline stages independently</p>
                      </div>
                    </div>
                    <div className="job-actions">
                      <div>
                        <Icon name="file" size={18} />
                        <span>
                          <strong>Compress Only</strong>
                          <small>TIFF → .baq (stores header, per-block sigma & indices)</small>
                        </span>
                        <button className="secondary" onClick={() => startJob("compress")} disabled={running}>
                          Run
                        </button>
                      </div>
                      <div>
                        <Icon name="grid" size={18} />
                        <span>
                          <strong>Decompress Only</strong>
                          <small>.baq → reconstructed GeoTIFF with original georeference</small>
                        </span>
                        <button className="secondary" onClick={startDecompress} disabled={running}>
                          Run
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Process Terminal */}
                  <div className="card terminal-card">
                    <div className="card-head">
                      <div>
                        <h2>Process Terminal</h2>
                        <p>Live stream from the compressor backend</p>
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <button
                          className="secondary"
                          style={{ height: 26, fontSize: 10, padding: "0 8px" }}
                          onClick={() => setAutoScroll(!autoScroll)}
                        >
                          Auto-scroll: {autoScroll ? "ON" : "OFF"}
                        </button>
                        <button
                          className="secondary"
                          style={{ height: 26, fontSize: 10, padding: "0 8px" }}
                          onClick={() => copyToClipboard(job?.logs?.join("\n") || "", "logs")}
                        >
                          {copiedKey === "logs" ? "Copied" : "Copy Logs"}
                        </button>
                      </div>
                    </div>
                    <pre className="log" ref={logRef}>
                      {job?.logs?.length ? job.logs.join("\n") : "Waiting for job to start…"}
                    </pre>
                  </div>
                </div>
              </div>
            </>
          )}

          {/* VIEW 3: COMPARISON */}
          {view === "compare" && (
            <>
              <div className="page-heading">
                <div>
                  <p>03 / VISUAL COMPARISON</p>
                  <h1>Original vs. Reconstructed Residuals</h1>
                  <span>Inspect quantization effects, speckle preservation, and error distribution.</span>
                </div>
                {result?.paths?.preview && (
                  <a
                    className="secondary"
                    href={`/api/jobs/${job?.id}/preview`}
                    download="sar_baq_preview.png"
                  >
                    <Icon name="download" size={14} /> Download High-Res Preview
                  </a>
                )}
              </div>

              {job?.id && result?.preview_available ? (
                <>
                  <div className="preview-note">
                    <Icon name="info" size={18} />
                    <span>
                      <strong>Display Preview:</strong> This image contains the downsampled comparison panels, error map, and distribution histograms generated by matplotlib. Comprehensive metrics below are calculated across the entire full-resolution raster.
                    </span>
                  </div>

                  <div className="card preview-card" style={{ position: "relative" }}>
                    <img
                      src={`/api/jobs/${job.id}/preview`}
                      alt="Original vs Reconstructed Comparison"
                      style={{ cursor: "zoom-in", width: "100%", maxHeight: "750px" }}
                      onClick={() => setZoomImage(`/api/jobs/${job.id}/preview`)}
                    />
                    <button
                      className="secondary"
                      style={{
                        position: "absolute",
                        top: 14,
                        right: 14,
                        background: "rgba(9, 19, 25, 0.85)",
                        color: "#fff",
                        borderColor: "#1e3845",
                      }}
                      onClick={() => setZoomImage(`/api/jobs/${job.id}/preview`)}
                    >
                      <Icon name="zoom" size={14} /> Fullscreen Zoom
                    </button>
                  </div>
                </>
              ) : (
                <EmptyState
                  title="No Comparison Preview Available"
                  body={
                    health?.matplotlib === false
                      ? "matplotlib is not installed on the system, so preview PNG generation was skipped."
                      : "Run the full pipeline to generate the multi-panel residual comparison and histogram figure."
                  }
                  action={
                    <button className="primary" onClick={() => setView("pipeline")}>
                      <Icon name="play" size={14} /> Go to Pipeline
                    </button>
                  }
                />
              )}
            </>
          )}

          {/* VIEW 4: METRICS */}
          {view === "metrics" && (
            <>
              <div className="page-heading">
                <div>
                  <p>04 / QUALITY METRICS</p>
                  <h1>Full-Image Quality & Compression Performance</h1>
                  <span>Streaming metrics evaluated on 100% of samples in the raster.</span>
                </div>
                {metrics && (
                  <button className="secondary" onClick={exportMetricsJSON}>
                    <Icon name="download" size={14} /> Export Metrics JSON
                  </button>
                )}
              </div>

              {!metrics ? (
                <EmptyState
                  title="No Metrics Computed Yet"
                  body="Execute the full pipeline to run the streaming error comparison and compute full-scene metrics."
                  action={
                    <button className="primary" onClick={() => setView("pipeline")}>
                      <Icon name="play" size={14} /> Run Pipeline
                    </button>
                  }
                />
              ) : (
                <>
                  <div className="success-strip">
                    <Icon name="check" size={20} />
                    <div>
                      <strong>Pipeline Complete</strong>
                      <span>{formatNum(metrics.n_samples, 0)} samples evaluated across full scene</span>
                    </div>
                    <Badge tone="success">Artifacts Written</Badge>
                  </div>

                  {/* Big KPI Cards */}
                  <div className="metric-cards">
                    <div className="metric">
                      <span>Mean Squared Error</span>
                      <strong>{formatNum(metrics.mse, 4)}</strong>
                      <small>Amplitude squared error</small>
                    </div>
                    <div className="metric">
                      <span>Root Mean Squared Error</span>
                      <strong>{formatNum(metrics.rmse, 4)}</strong>
                      <small>Linear amplitude residual</small>
                    </div>
                    <div className="metric">
                      <span>Signal-to-Noise Ratio</span>
                      <strong>{formatNum(metrics.snr, 2)} dB</strong>
                      <small>{metrics.snr > 30 ? "High Fidelity" : "Standard BAQ"}</small>
                    </div>
                    <div className="metric">
                      <span>Peak SNR</span>
                      <strong>{formatNum(metrics.psnr, 2)} dB</strong>
                      <small>Range: {formatNum(metrics.data_range, 2)}</small>
                    </div>
                  </div>

                  <div className="two-col">
                    {/* Summary Card */}
                    <div className="card">
                      <div className="card-head">
                        <div>
                          <h2>Compression Summary</h2>
                          <p>
                            Block {result?.block_size || block} · {result?.bits || bits} bits/sample · Band {result?.band || band}
                          </p>
                        </div>
                      </div>
                      <div className="summary-table">
                        <div><span>Original Raster Size</span><b>{formatBytes(metrics.original_size)}</b></div>
                        <div><span>Compressed .baq Size</span><b>{formatBytes(metrics.compressed_size)}</b></div>
                        <div><span>Compression Ratio</span><b className="accent">{formatNum(metrics.compression_ratio, 2)} : 1</b></div>
                        <div><span>Storage Saving</span><b className="accent">{formatNum(metrics.storage_saving, 2)}%</b></div>
                        <div><span>Contents</span><b>Header + Per-block RMS (&sigma;) + Packed Indices</b></div>
                        {header?.elapsed_s != null && (
                          <div><span>Compress Time</span><b>{formatNum(header.elapsed_s, 2)} s</b></div>
                        )}
                      </div>
                    </div>

                    {/* Artifacts Card */}
                    <div className="card">
                      <div className="card-head">
                        <div>
                          <h2>Output Artifacts</h2>
                          <p>Files written to disk under data/output/</p>
                        </div>
                      </div>
                      <div className="artifact-list">
                        {result?.paths?.baq && (
                          <div>
                            <Icon name="file" size={18} />
                            <span>
                              <strong>Compressed BAQ File</strong>
                              <code>{result.paths.baq}</code>
                            </span>
                            <button
                              className="secondary"
                              style={{ height: 28, fontSize: 10, padding: "0 8px" }}
                              onClick={() => copyToClipboard(result.paths.baq, "baq_path")}
                            >
                              {copiedKey === "baq_path" ? "Copied" : "Copy Path"}
                            </button>
                          </div>
                        )}
                        {result?.paths?.reconstructed && (
                          <div>
                            <Icon name="grid" size={18} />
                            <span>
                              <strong>Reconstructed GeoTIFF</strong>
                              <code>{result.paths.reconstructed}</code>
                            </span>
                            <button
                              className="secondary"
                              style={{ height: 28, fontSize: 10, padding: "0 8px" }}
                              onClick={() => copyToClipboard(result.paths.reconstructed, "recon_path")}
                            >
                              {copiedKey === "recon_path" ? "Copied" : "Copy Path"}
                            </button>
                          </div>
                        )}
                        {result?.paths?.preview && (
                          <div>
                            <Icon name="compare" size={18} />
                            <span>
                              <strong>Comparison Preview Image</strong>
                              <code>{result.paths.preview}</code>
                            </span>
                            <button
                              className="secondary"
                              style={{ height: 28, fontSize: 10, padding: "0 8px" }}
                              onClick={() => setView("compare")}
                            >
                              View
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </>
              )}
            </>
          )}

          {/* VIEW 5: EXPERIMENTS */}
          {view === "experiments" && (
            <>
              <div className="page-heading">
                <div>
                  <p>05 / PARAMETER EXPERIMENTS</p>
                  <h1>Parameter Sweep & Rate-Distortion Curves</h1>
                  <span>Comprehensive analysis across block sizes (16, 32, 64) and bit depths (2–6).</span>
                </div>
                {sortedExperiments.length > 0 && (
                  <button className="secondary" onClick={exportExperimentsCSV}>
                    <Icon name="download" size={14} /> Export CSV Table
                  </button>
                )}
              </div>

              {graphs.length > 0 && job?.id ? (
                <>
                  <div className="experiment-config">
                    <div><span>Block Sizes</span><b>16, 32, 64 samples</b></div>
                    <div><span>Bit Depths</span><b>2, 3, 4, 5, 6 bits</b></div>
                    <div><span>Evaluated Configurations</span><b>{sortedExperiments.length || 15} runs</b></div>
                    <Badge tone="success">Sweep Complete</Badge>
                  </div>

                  {/* Generated Plot Images */}
                  <div className="charts">
                    {graphs.map((name) => (
                      <figure
                        key={name}
                        className="mini-chart"
                        style={{ cursor: "pointer" }}
                        onClick={() => setZoomImage(`/api/jobs/${job.id}/graphs/${name}`)}
                      >
                        <span>{name.replace(/\.png$/i, "").replace(/_/g, " ")}</span>
                        <img src={`/api/jobs/${job.id}/graphs/${name}`} alt={name} />
                      </figure>
                    ))}
                  </div>

                  {/* Sortable Table */}
                  {sortedExperiments.length > 0 && (
                    <div className="card table-card">
                      <div className="card-head">
                        <div>
                          <h2>Experimental Sweep Data</h2>
                          <p>Click column headers to sort results</p>
                        </div>
                      </div>
                      <table>
                        <thead>
                          <tr>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("block_size")}>
                              Block {sortField === "block_size" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("bits")}>
                              Bits {sortField === "bits" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("compressed_size_MB")}>
                              Size (MB) {sortField === "compressed_size_MB" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("compression_ratio")}>
                              Ratio {sortField === "compression_ratio" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("storage_saving")}>
                              Saving % {sortField === "storage_saving" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("mse")}>
                              MSE {sortField === "mse" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("rmse")}>
                              RMSE {sortField === "rmse" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("snr")}>
                              SNR (dB) {sortField === "snr" && (sortAsc ? "▲" : "▼")}
                            </th>
                            <th style={{ cursor: "pointer" }} onClick={() => toggleSort("psnr")}>
                              PSNR (dB) {sortField === "psnr" && (sortAsc ? "▲" : "▼")}
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {sortedExperiments.map((row: Record<string, number>, idx: number) => {
                            const isHighestSNR = row.snr === Math.max(...sortedExperiments.map(r => r.snr || 0))
                            const isCurrentChoice = row.block_size === block && row.bits === bits

                            return (
                              <tr key={idx} style={isCurrentChoice ? { background: "#f0fdfa" } : undefined}>
                                <td>
                                  <b>{row.block_size}</b>
                                  {isCurrentChoice && <Badge tone="running">active</Badge>}
                                </td>
                                <td>{row.bits}</td>
                                <td>{formatNum(row.compressed_size_MB, 2)}</td>
                                <td><b>{formatNum(row.compression_ratio, 2)} : 1</b></td>
                                <td><span className="accent">{formatNum(row.storage_saving, 2)}%</span></td>
                                <td>{formatNum(row.mse, 4)}</td>
                                <td>{formatNum(row.rmse, 4)}</td>
                                <td>
                                  <b>{formatNum(row.snr, 2)} dB</b>
                                  {isHighestSNR && <Badge tone="success">best</Badge>}
                                </td>
                                <td>{formatNum(row.psnr, 2)} dB</td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              ) : (
                <EmptyState
                  title="No Parameter Sweep Data Yet"
                  body="Enable 'Run Parameter Sweep' on the Pipeline page and execute the full pipeline to generate the 5 scientific trade-off graphs and CSV metrics."
                  action={
                    <button
                      className="primary"
                      onClick={() => {
                        setExperiments(true)
                        setView("pipeline")
                      }}
                    >
                      <Icon name="play" size={14} /> Enable Sweep in Pipeline
                    </button>
                  }
                />
              )}
            </>
          )}
        </section>
      </main>

      {/* Confirmation Modal */}
      {confirmOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
          <div className="modal">
            <h2 id="confirm-title">Start Full BAQ Pipeline?</h2>
            <p>
              This will execute a complete streaming cycle: Inspect &rarr; Compress &rarr; Decompress &rarr; Full-Scene Metrics &rarr; Comparison Figure
              {experiments ? " and sweep 15 parameter configurations" : ""}.
            </p>
            <ul>
              <li><strong>Target Scene:</strong> {info?.name || path}</li>
              <li><strong>Configuration:</strong> Block Size {block} · {bits} Bits/Sample · Band {band}</li>
              <li><strong>Memory Budget:</strong> {chunkMb} MB streaming chunk windows</li>
            </ul>
            <div className="modal-actions">
              <button className="secondary" onClick={() => setConfirmOpen(false)}>Cancel</button>
              <button
                className="primary"
                onClick={() => {
                  setConfirmOpen(false)
                  startJob("pipeline")
                }}
              >
                Confirm & Run
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Theory / Documentation Modal */}
      {theoryOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={() => setTheoryOpen(false)}>
          <div className="modal" style={{ maxWidth: 640 }} onClick={(e) => e.stopPropagation()}>
            <h2>Block Adaptive Quantization (BAQ) Principles</h2>
            <p style={{ marginTop: 8 }}>
              BAQ is the foundational data compression standard developed for Synthetic Aperture Radar missions (Magellan, Cassini, Sentinel-1) to reduce massive downlink data rates.
            </p>

            <div style={{ margin: "16px 0", display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ background: "#f8fafc", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
                <strong style={{ display: "block", color: "var(--teal)", fontSize: 12 }}>1. Gaussian Source Assumption</strong>
                <span style={{ fontSize: 11.5, color: "#475569" }}>
                  SAR raw echoes are linear superpositions of many random scatterers, following a zero-mean Gaussian distribution with spatially varying variance according to radar cross-section.
                </span>
              </div>

              <div style={{ background: "#f8fafc", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
                <strong style={{ display: "block", color: "var(--teal)", fontSize: 12 }}>2. Lloyd–Max Optimal Quantizer</strong>
                <span style={{ fontSize: 11.5, color: "#475569" }}>
                  Unlike uniform quantizers, Lloyd–Max places decision thresholds closer together near zero where sample density is highest, minimizing Mean Squared Error (MSE).
                </span>
              </div>

              <div style={{ background: "#f8fafc", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
                <strong style={{ display: "block", color: "var(--teal)", fontSize: 12 }}>3. Adaptive Scale Factor (&sigma;)</strong>
                <span style={{ fontSize: 11.5, color: "#475569" }}>
                  The image is partitioned into small blocks (e.g., 32 samples). The compressor estimates the block RMS (&sigma;), normalizes the samples, and stores &sigma; as side information in the .baq file header.
                </span>
              </div>
            </div>

            <div className="modal-actions">
              <button className="primary" onClick={() => setTheoryOpen(false)}>Close Guide</button>
            </div>
          </div>
        </div>
      )}

      {/* Image Zoom Modal */}
      {zoomImage && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={() => setZoomImage(null)}>
          <div
            style={{
              position: "relative",
              maxWidth: "92vw",
              maxHeight: "92vh",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="secondary"
              style={{
                alignSelf: "flex-end",
                marginBottom: 8,
                background: "#0f172a",
                color: "#fff",
                borderColor: "#334155",
              }}
              onClick={() => setZoomImage(null)}
            >
              Close [ESC]
            </button>
            <img
              src={zoomImage}
              alt="Zoomed figure"
              style={{
                maxWidth: "100%",
                maxHeight: "85vh",
                borderRadius: 8,
                boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
                background: "#fff",
              }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
