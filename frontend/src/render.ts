// Turns float images into pixels. One stretch is computed across ALL frames so a star that
// looks brighter in one frame really is brighter (per-frame autoscaling would hide variability).

export type Display = 'frames' | 'difference'
export type Transfer = 'asinh' | 'linear'

export interface StretchOpts {
  display: Display
  transfer: Transfer
  lowPct: number   // e.g. 1
  highPct: number  // e.g. 99.5
  invert: boolean
}

export function meanFrame(frames: (Float32Array | null)[]): Float32Array | null {
  const valid = frames.filter((f): f is Float32Array => !!f)
  if (!valid.length) return null
  const n = valid[0].length
  const sum = new Float32Array(n)
  const cnt = new Uint16Array(n)
  for (const f of valid) {
    for (let i = 0; i < n; i++) {
      const v = f[i]
      if (Number.isFinite(v)) { sum[i] += v; cnt[i]++ }
    }
  }
  for (let i = 0; i < n; i++) sum[i] = cnt[i] ? sum[i] / cnt[i] : NaN
  return sum
}

/** Frames to display, after optional difference against the mean of all frames. */
export function displayFrames(frames: (Float32Array | null)[], display: Display): (Float32Array | null)[] {
  if (display === 'frames') return frames
  const ref = meanFrame(frames)
  if (!ref) return frames
  return frames.map((f) => {
    if (!f) return null
    const out = new Float32Array(f.length)
    for (let i = 0; i < f.length; i++) out[i] = f[i] - ref[i]
    return out
  })
}

function percentile(sorted: Float32Array, p: number): number {
  if (!sorted.length) return 0
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round((p / 100) * (sorted.length - 1))))
  return sorted[i]
}

export function computeLimits(frames: (Float32Array | null)[], o: StretchOpts): [number, number] {
  const vals: number[] = []
  for (const f of frames) {
    if (!f) continue
    const step = Math.max(1, Math.floor(f.length / 20000)) // subsample big frames
    for (let i = 0; i < f.length; i += step) if (Number.isFinite(f[i])) vals.push(o.display === 'difference' ? Math.abs(f[i]) : f[i])
  }
  if (!vals.length) return [0, 1]
  const s = Float32Array.from(vals).sort()
  if (o.display === 'difference') {
    const m = percentile(s, o.highPct) || 1
    return [-m, m]
  }
  const lo = percentile(s, o.lowPct)
  let hi = percentile(s, o.highPct)
  if (hi <= lo) hi = lo + 1
  return [lo, hi]
}

const NAN_RGB = [40, 48, 66]

/** Saturated star cores are masked (NaN). Grow "saturated" from pixels at/above the clip level
 *  into neighbouring NaNs so cores render as bright stars, not as no-data holes. */
function fillSaturated(f: Float32Array, n: number, hi: number): Float32Array {
  const out = f.slice()
  for (let iter = 0; iter < 6; iter++) {
    let changed = false
    const src = out.slice()
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      const i = y * n + x
      if (Number.isFinite(src[i])) continue
      let bright = 0
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const xx = x + dx, yy = y + dy
        if ((dx || dy) && xx >= 0 && yy >= 0 && xx < n && yy < n && src[yy * n + xx] >= hi) bright++
      }
      if (bright >= 2) { out[i] = hi; changed = true }
    }
    if (!changed) break
  }
  return out
}

export function renderToImageData(f: Float32Array | null, n: number, lim: [number, number], o: StretchOpts): ImageData {
  if (f && o.display === 'frames') f = fillSaturated(f, n, lim[1])
  const img = new ImageData(n, n)
  const px = img.data
  const [lo, hi] = lim
  const span = hi - lo
  const soft = 0.1
  const norm = o.transfer === 'asinh' ? Math.asinh(1 / soft) : 1
  for (let y = 0; y < n; y++) {
    // FITS rows go south -> north; canvas rows go top -> bottom.
    const src = (n - 1 - y) * n
    for (let x = 0; x < n; x++) {
      const v = f ? f[src + x] : NaN
      const o4 = (y * n + x) * 4
      if (!Number.isFinite(v)) {
        px[o4] = NAN_RGB[0]; px[o4 + 1] = NAN_RGB[1]; px[o4 + 2] = NAN_RGB[2]; px[o4 + 3] = 255
        continue
      }
      let t = Math.min(1, Math.max(0, (v - lo) / span))
      if (o.transfer === 'asinh') t = Math.asinh(t / soft) / norm
      if (o.invert) t = 1 - t
      const g = Math.round(t * 255)
      px[o4] = g; px[o4 + 1] = g; px[o4 + 2] = g; px[o4 + 3] = 255
    }
  }
  return img
}
