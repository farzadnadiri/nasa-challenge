import { useMemo, useState } from 'react'
import type { PhotPoint } from '../api'

// Categorical slots 1-3 (dark steps) pass all-pairs CVD checks for scatter plots.
// Epochs beyond three keep a distinct marker shape and fall back to a muted ink.
const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)']
const SHAPES = ['circle', 'square', 'triangle', 'diamond', 'circle', 'square'] as const
const colorFor = (i: number) => SERIES[i] ?? 'var(--text-muted)'

interface Pt { x: number; y: number; err?: number; series: number; tip: string }

function Marker({ shape, x, y, fill }: { shape: (typeof SHAPES)[number]; x: number; y: number; fill: string }) {
  const r = 4.5
  const common = { fill, stroke: 'var(--surface-1)', strokeWidth: 2 }
  if (shape === 'square') return <rect x={x - r} y={y - r} width={2 * r} height={2 * r} rx={1} {...common} />
  if (shape === 'triangle') return <path d={`M${x},${y - r - 1} L${x + r + 1},${y + r} L${x - r - 1},${y + r} Z`} {...common} />
  if (shape === 'diamond') return <path d={`M${x},${y - r - 1} L${x + r + 1},${y} L${x},${y + r + 1} L${x - r - 1},${y} Z`} {...common} />
  return <circle cx={x} cy={y} r={r} {...common} />
}

function ticks(lo: number, hi: number, count = 5): number[] {
  const span = hi - lo || 1
  const step0 = span / count
  const mag = 10 ** Math.floor(Math.log10(step0))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? step0
  const out = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toPrecision(6)))
  return out
}

interface ScatterProps {
  title: string
  xLabel: string
  yLabel: string
  points: Pt[]
  seriesNames: string[]
  xFormat?: (v: number) => string
}

function Scatter({ title, xLabel, yLabel, points, seriesNames, xFormat = (v) => String(v) }: ScatterProps) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 520, H = 240, m = { l: 56, r: 12, t: 10, b: 38 }
  const { sx, sy, xt, yt } = useMemo(() => {
    const xs = points.map((p) => p.x)
    const ys = points.flatMap((p) => [p.y - (p.err ?? 0), p.y + (p.err ?? 0)])
    let x0 = Math.min(...xs), x1 = Math.max(...xs)
    let y0 = Math.min(0, ...ys), y1 = Math.max(...ys)
    const px = (x1 - x0) * 0.04 || 0.5, py = (y1 - y0) * 0.06 || 1
    x0 -= px; x1 += px; y1 += py; if (y0 < 0) y0 -= py
    return {
      sx: (v: number) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r),
      sy: (v: number) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b),
      xt: ticks(x0, x1), yt: ticks(y0, y1),
    }
  }, [points, m.l, m.r, m.t, m.b])

  if (!points.length) return null
  const hp = hover != null ? points[hover] : null
  const used = [...new Set(points.map((p) => p.series))].sort((a, b) => a - b)

  return (
    <figure className="chart">
      <figcaption>
        <span className="chart-title">{title}</span>
        {used.length > 1 && (
          <span className="legend">
            {used.map((s) => (
              <span key={s} className="legend-item">
                <svg width="12" height="12"><Marker shape={SHAPES[s % SHAPES.length]} x={6} y={6} fill={colorFor(s)} /></svg>
                {seriesNames[s]}
              </span>
            ))}
          </span>
        )}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
        {yt.map((v) => (
          <g key={`y${v}`}>
            <line x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} className="gridline" />
            <text x={m.l - 6} y={sy(v) + 4} textAnchor="end" className="tick">{v}</text>
          </g>
        ))}
        {xt.map((v) => (
          <text key={`x${v}`} x={sx(v)} y={H - m.b + 16} textAnchor="middle" className="tick">{xFormat(v)}</text>
        ))}
        <line x1={m.l} x2={W - m.r} y1={H - m.b} y2={H - m.b} className="axis" />
        <text x={(m.l + W - m.r) / 2} y={H - 4} textAnchor="middle" className="axis-label">{xLabel}</text>
        <text x={12} y={(m.t + H - m.b) / 2} textAnchor="middle" className="axis-label"
          transform={`rotate(-90 12 ${(m.t + H - m.b) / 2})`}>{yLabel}</text>
        {points.map((p, i) => p.err ? (
          <line key={`e${i}`} x1={sx(p.x)} x2={sx(p.x)} y1={sy(p.y - p.err)} y2={sy(p.y + p.err)}
            stroke={colorFor(p.series)} strokeWidth={1} opacity={0.6} />
        ) : null)}
        {points.map((p, i) => (
          <g key={i} onMouseEnter={() => setHover(i)}>
            <circle cx={sx(p.x)} cy={sy(p.y)} r={9} fill="transparent" />
            <Marker shape={SHAPES[p.series % SHAPES.length]} x={sx(p.x)} y={sy(p.y)} fill={colorFor(p.series)} />
          </g>
        ))}
      </svg>
      {hp && (
        <div className="tooltip" style={{ left: `${(sx(hp.x) / W) * 100}%`, top: `${(sy(hp.y) / H) * 100}%` }}>
          {hp.tip}
        </div>
      )}
    </figure>
  )
}

function downloadCSV(points: PhotPoint[], epochDates: string[]) {
  const rows = ['mjd,epoch,epoch_date,detector,wavelength_um,flux_mjy,err_mjy,contaminated',
    ...points.map((p) => [p.mjd.toFixed(5), p.epoch + 1, epochDates[p.epoch] ?? '', p.detector,
      p.wavelength_um?.toFixed(4) ?? '', p.flux_mjy.toFixed(4), p.err_mjy.toFixed(4), p.contaminated].join(','))]
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([rows.join('\n')], { type: 'text/csv' }))
  a.download = 'spherex_photometry.csv'
  a.click()
}

export function PhotometryCharts({ points, epochDates, mode }: { points: PhotPoint[]; epochDates: string[]; mode: 'aperture' | 'wing' }) {
  const wing = mode === 'wing'
  const unit = wing ? 'wing flux, mJy' : 'mJy'
  const clean = points.filter((p) => !p.contaminated && p.wavelength_um != null)
  const names = epochDates.map((d, i) => `Epoch ${i + 1} (${d})`)

  const spectrum: Pt[] = clean.map((p) => ({
    x: p.wavelength_um!, y: p.flux_mjy, err: p.err_mjy, series: p.epoch,
    tip: `${p.wavelength_um!.toFixed(3)} µm · ${p.flux_mjy.toFixed(2)} ± ${p.err_mjy.toFixed(2)} mJy · epoch ${p.epoch + 1}`,
  }))

  // Brightness per epoch, compared at matched wavelengths: each measurement is divided by the
  // average spectrum at its wavelength, then each epoch takes the median ratio. Comparing raw
  // medians would mistake different wavelength sampling for variability (e.g. brown dwarfs,
  // whose flux changes steeply with wavelength).
  const median = (v: number[]) => { const s = [...v].sort((a, b) => a - b); return s[Math.floor(s.length / 2)] }
  const BIN = 0.04 // µm, about one SPHEREx spectral channel
  const refBins = new Map<number, number[]>()
  for (const p of clean) {
    const b = Math.round(p.wavelength_um! / BIN)
    refBins.set(b, [...(refBins.get(b) ?? []), p.flux_mjy])
  }
  const ref = new Map([...refBins].map(([b, v]) => [b, median(v)]))
  const lc: Pt[] = []
  const byEpoch = new Map<number, { ratio: number; mjd: number }[]>()
  for (const p of clean) {
    const r = ref.get(Math.round(p.wavelength_um! / BIN))!
    if (!(r > 0)) continue
    byEpoch.set(p.epoch, [...(byEpoch.get(p.epoch) ?? []), { ratio: p.flux_mjy / r, mjd: p.mjd }])
  }
  for (const [ep, ps] of [...byEpoch].sort((a, b) => a[0] - b[0])) {
    const r = ps.map((p) => p.ratio)
    const med = median(r)
    const mean = r.reduce((a, b) => a + b, 0) / r.length
    const sd = Math.sqrt(r.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, r.length - 1))
    const mjd = ps.reduce((a, p) => a + p.mjd, 0) / ps.length
    const year = 2000 + (mjd - 51544.5) / 365.25
    lc.push({ x: year, y: med, err: sd / Math.sqrt(r.length), series: ep,
      tip: `${epochDates[ep] ?? ''} · ${med.toFixed(2)}× average · ${ps.length} exposures` })
  }

  if (!clean.length) return <p className="muted">No clean photometry at this position.</p>
  return (
    <div className="charts">
      <Scatter title={wing ? "Spectrum by epoch (saturated: wing flux)" : "Spectrum by epoch"} xLabel="Wavelength (µm)" yLabel={`Flux (${unit})`} points={spectrum} seriesNames={names} />
      <Scatter title="Brightness per epoch (1 = average)" xLabel="Year" yLabel="Relative brightness" points={lc} seriesNames={names}
        xFormat={(v) => v.toFixed(1)} />
      <p className="muted small">
        {wing
          ? 'Saturated star: the core is masked, so flux is measured in a fixed ring around it. It tracks relative brightness, not total flux. '
          : 'Quick-look aperture photometry on reprojected frames. '}
        Contaminated exposures excluded.
        For science-grade spectra use IRSA's SPHEREx spectrophotometry tool.{' '}
        <button className="link" onClick={() => downloadCSV(points, epochDates)}>Download CSV</button>
      </p>
    </div>
  )
}
