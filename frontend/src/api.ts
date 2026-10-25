export type Health = {
  ok: boolean
  rasterio: boolean
  matplotlib: boolean
  input_dir: string
  output_dir: string
  valid_bits: number[]
  valid_block_sizes: number[]
}

export type LibraryFile = {
  name: string
  path: string
  size: number
  kind: "tiff" | "baq"
}

export type InspectInfo = {
  path: string
  name: string
  width: number
  height: number
  count: number
  band: number
  dtype: string
  is_complex: boolean
  nodata: number | null
  file_size: number
  crs: string | null
  transform: number[] | null
  driver: string
  compression: string
  tiled: boolean
  layout: string
  n_samples: number
  min: number | null
  max: number | null
  mean: number
  std: number
  finding_kind: "complex" | "multiband" | "real"
  finding_title: string
  finding_body: string[]
}

export type Job = {
  id: string
  kind: "pipeline" | "compress" | "decompress"
  status: "queued" | "running" | "done" | "error"
  pct: number
  stage: string
  message: string
  chunk: number | null
  n_chunks: number | null
  logs: string[]
  error: string | null
  result: Record<string, any> | null
}

export type RunSettings = {
  path: string
  block_size: number
  bits: number
  band: number
  chunk_target_mb: number
  run_experiments: boolean
  experiment_block_sizes?: number[]
  experiment_bits?: number[]
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data?.detail === "string") return data.detail
    if (Array.isArray(data?.detail)) {
      return data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join(" ")
    }
  } catch {
    /* ignore */
  }
  return res.statusText || "Request failed"
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(await parseError(res))
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<Health>("/api/health"),
  files: () => request<{ input_dir: string; files: LibraryFile[] }>("/api/files"),
  inspect: (path: string, band: number) =>
    request<InspectInfo>("/api/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, band }),
    }),
  upload: async (file: File) => {
    const body = new FormData()
    body.append("file", file)
    return request<LibraryFile>("/api/upload", { method: "POST", body })
  },
  pipeline: (settings: RunSettings) =>
    request<Job>("/api/jobs/pipeline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  compress: (settings: RunSettings) =>
    request<Job>("/api/jobs/compress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  decompress: (path: string, reference_tiff?: string) =>
    request<Job>("/api/jobs/decompress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, reference_tiff }),
    }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
}

export function watchJob(id: string, onUpdate: (job: Job) => void): () => void {
  let isClosed = false
  let pollInterval: any = null
  let source: EventSource | null = null

  const startPolling = () => {
    if (pollInterval || isClosed) return
    pollInterval = setInterval(async () => {
      try {
        const j = await api.job(id)
        if (isClosed) return
        onUpdate(j)
        if (j.status === "done" || j.status === "error") {
          clearInterval(pollInterval)
          pollInterval = null
        }
      } catch {
        /* ignore polling error */
      }
    }, 600)
  }

  try {
    source = new EventSource(`/api/jobs/${id}/events`)
    source.onmessage = (event) => {
      if (isClosed) return
      try {
        const parsed = JSON.parse(event.data) as Job
        onUpdate(parsed)
        if (parsed.status === "done" || parsed.status === "error") {
          source?.close()
        }
      } catch {
        /* ignore parse error */
      }
    }
    source.onerror = () => {
      if (isClosed) return
      source?.close()
      source = null
      startPolling()
    }
  } catch {
    startPolling()
  }

  return () => {
    isClosed = true
    if (source) source.close()
    if (pollInterval) clearInterval(pollInterval)
  }
}

