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
  psf_fwhm_arcsec: number
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
}

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

export interface ViewParams {
  ra: number
  dec: number
  fov: number
  dets: number[]
  mode: 'epoch' | 'exposure'
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
  return new URLSearchParams({
    ra: p.ra.toFixed(6),
    dec: p.dec.toFixed(6),
    fov: p.fov.toFixed(3),
    dets: p.dets.join(','),
  }).toString()
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type RawFrame = any

export async function fetchFrames(p: ViewParams, signal?: AbortSignal): Promise<FramesResult> {
  const raw = await getJSON<RawFrame>(`/api/frames?${qs(p)}&mode=${p.mode}`, signal)
  const frames: Frame[] = (raw.frames as RawFrame[]).map((f) => {
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

export const fetchPhotometry = (p: ViewParams, x: number, y: number) =>
  getJSON<{ status: string; mode: 'aperture' | 'wing'; points: PhotPoint[] }>(
    `/api/photometry?${qs(p)}&x=${x.toFixed(2)}&y=${y.toFixed(2)}`,
  )
