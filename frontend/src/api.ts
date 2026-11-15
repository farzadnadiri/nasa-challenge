// Typed client for the FastAPI backend. In dev, Vite proxies /api to localhost:8000;
// in production, the Cloudflare Worker routes /api to the container.

export interface Epoch {
  index: number
  mjd_start: number
  mjd_end: number
  mjd_mid: number
  date_mid: string
  decimal_year: number
  n_exposures: number
}

export interface ExposureMeta {
  obsid: string
  detector: number
  epoch: number
  mjd: number
  date_obs: string
  wavelength_um: number | null
  bandwidth_um: number | null
  psf_fwhm_arcsec: number | null
  version: string
  release: string
  target_on_image: boolean
  target_flags: string[]
  contaminated: boolean
}

export interface Frame {
  label: string
  date: string
  decimalYear: number
  data: Float32Array | null
  nUsed: number
  shallow: boolean
  wavelength: [number, number] | null
  nContaminated: number
  meta?: ExposureMeta
  channel?: { detector: number; channel: number; nEpochs: number }
}

export interface Grid {
  n: number
  ra: number
  dec: number
  fov: number
  pixel_scale_arcsec: number
}

export interface FramesResult {
  key: string
  status: 'queued' | 'querying' | 'fetching' | 'done' | 'error'
  error: string | null
  progress: { done: number; total: number; failed: number }
  grid: Grid
  epochs: Epoch[]
  frames: Frame[]
  collections: string[]
  refresh_wait_s: number
  dataset: DatasetInfo | null
}

export interface DatasetInfo {
  files_used: number
  rejected: { name: string; reason: string }[]
  pixel_scale_arcsec: number
  units: string
}

// ---- uploads ("FITS + WCS v1")
export interface UploadLimits { maxFiles: number; maxFileBytes: number; maxDatasetBytes: number; keepDays: number }

export async function createDataset(): Promise<{ id: string; limits: UploadLimits }> {
  const r = await fetch('/api/datasets', { method: 'POST' })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.error ?? `${r.status} ${r.statusText}`)
  return body
}

export async function uploadFile(id: string, file: File): Promise<void> {
  const r = await fetch(`/api/datasets/${id}/files/${encodeURIComponent(file.name)}`, { method: 'PUT', body: file })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.error ?? `${r.status} ${r.statusText}`)
  }
}

export interface Releases {
  available: string[]   // newest first, e.g. ['dr1', 'qr3', 'qr2']
  auto: string[]        // what 'Newest' currently means, e.g. ['dr1', 'qr3']
}
export const fetchReleases = () => getJSON<Releases>('/api/releases')

export interface Resolved {
  query: string
  name: string | null
  otype?: string | null
  ra: number
  dec: number
  pmra: number | null
  pmdec: number | null
  note: string
}

export interface GaiaSource {
  source_id: string
  ra: number
  dec: number
  pmra: number | null
  pmdec: number | null
  parallax: number | null
  phot_g_mean_mag: number | null
  bp_rp: number | null
  name?: string   // SIMBAD main identifier, when SIMBAD knows the star
  otype?: string  // SIMBAD object type code
}

export interface PhotPoint {
  mjd: number
  epoch: number
  detector: number
  wavelength_um: number | null
  flux_mjy: number
  err_mjy: number
  contaminated: boolean
}

export type ViewMode = 'epoch' | 'exposure' | 'wavelength'

export interface ViewParams {
  ra: number
  dec: number
  fov: number
  dets: number[]
  mode: ViewMode
  release: string   // 'auto' (newest data + quick release) or a release name such as 'dr1'
  deep: boolean     // also search the Deep Field collections
  dataset?: string  // uploaded dataset id; ra/dec may then be NaN (the dataset's own centre)
}

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const body = await r.json()
      if (body?.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* not JSON */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

function decode(b64: string | null): Float32Array | null {
  if (!b64) return null
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new Float32Array(bytes.buffer)
}

function qs(p: ViewParams): string {
  const q = new URLSearchParams({ fov: p.fov.toFixed(3) })
  if (Number.isFinite(p.ra) && Number.isFinite(p.dec)) {
    q.set('ra', p.ra.toFixed(6))
    q.set('dec', p.dec.toFixed(6))
  }
  if (p.dataset) {
    q.set('dataset', p.dataset)
  } else {
    q.set('dets', p.dets.join(','))
    q.set('release', p.release)
    q.set('deep', p.deep ? '1' : '0')
  }
  return q.toString()
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type RawFrame = any

export async function fetchFrames(p: ViewParams, signal?: AbortSignal, refresh = false): Promise<FramesResult> {
  const raw = await getJSON<RawFrame>(`/api/frames?${qs(p)}&mode=${p.mode}${refresh ? '&refresh=1' : ''}`, signal)
  const frames: Frame[] = (raw.frames as RawFrame[]).map((f) => {
    if (p.mode === 'wavelength') {
      // One stack per SPHEREx channel across all epochs; Gaia markers use the middle of the data.
      return {
        label: f.wavelength_um != null ? `${f.wavelength_um.toFixed(3)} µm` : (f.filter || 'No wavelength'),
        date: f.detector ? `D${f.detector} channel ${f.channel}` : (f.filter ? `filter ${f.filter}` : 'uploaded files'),
        decimalYear: 2026.0,
        data: decode(f.image),
        nUsed: f.n_used,
        shallow: false,
        wavelength: f.wavelength_min_um != null ? [f.wavelength_min_um, f.wavelength_max_um] : null,
        nContaminated: f.n_contaminated ?? 0,
        channel: { detector: f.detector, channel: f.channel, nEpochs: f.n_epochs },
      }
    }
    if (p.mode === 'epoch') {
      const ep: Epoch = f.epoch
      return {
        label: `Epoch ${ep.index + 1}`,
        date: ep.date_mid,
        decimalYear: ep.decimal_year,
        data: decode(f.image),
        nUsed: f.n_used,
        shallow: !!f.shallow,
        wavelength: f.wavelength_min_um != null ? [f.wavelength_min_um, f.wavelength_max_um] : null,
        nContaminated: f.n_contaminated ?? 0,
      }
    }
    const m: ExposureMeta = f.meta
    const year = 2000 + (m.mjd - 51544.5) / 365.25
    return {
      label: `${m.obsid} D${m.detector}`,
      date: m.date_obs.slice(0, 16).replace('T', ' '),
      decimalYear: year,
      data: decode(f.image),
      nUsed: 1,
      shallow: false,
      wavelength: m.wavelength_um != null ? [m.wavelength_um, m.wavelength_um] : null,
      nContaminated: m.contaminated ? 1 : 0,
      meta: m,
    }
  })
  return { ...raw, frames }
}

export const resolveTarget = (q: string) => getJSON<Resolved>(`/api/resolve?q=${encodeURIComponent(q)}`)

export const fetchGaia = (ra: number, dec: number, radius: number) =>
  getJSON<{ ref_epoch: number; sources: GaiaSource[] }>(
    `/api/gaia?ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&radius=${radius.toFixed(4)}`,
  )

export interface ExposureRow extends ExposureMeta {
  saturated: boolean
  source?: string
}

export const fetchExposures = (p: ViewParams) =>
  getJSON<{ status: string; exposures: ExposureRow[] }>(`/api/exposures?${qs(p)}`)

export const fetchPhotometry = (p: ViewParams, x: number, y: number) =>
  getJSON<{ status: string; mode: 'aperture' | 'wing'; unit: string; points: PhotPoint[] }>(
    `/api/photometry?${qs(p)}&x=${x.toFixed(2)}&y=${y.toFixed(2)}`,
  )
