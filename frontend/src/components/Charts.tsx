import { useEffect, useMemo, useState } from 'react'
import type { PhotPoint } from '../api'

// Line charts: one line per epoch (spectrum) or one line through the epochs (brightness).
// Colours are the 8 categorical slots in fixed order (lines only need neighbouring series to
// differ); each epoch also keeps its own marker shape so colour is never the only cue.
const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`)
const SHAPES = ['circle', 'square', 'triangle', 'diamond'] as const
const colorFor = (i: number) => SERIES[i % SERIES.length]
const shapeFor = (i: number) => SHAPES[i % SHAPES.length]

interface Pt { x: number; y: number; err?: number; series: number; tip: string }

function Marker({ shape, x, y, fill }: { shape: (typeof SHAPES)[number]; x: number; y: number; fill: string }) {
  const r = 4
  const common = { fill, stroke: 'var(--surface-1)', strokeWidth: 2 }
  if (shape === 'square') return <rect x={x - r} y={y - r} width={2 * r} height={2 * r} rx={1} {...common} />
  if (shape === 'triangle') return <path d={`M${x},${y - r - 1} L${x + r + 1},${y + r} L${x - r - 1},${y + r} Z`} {...common} />
  if (shape === 'diamond') return <path d={`M${x},${y - r - 1} L${x + r + 1},${y} L${x},${y + r + 1} L${x - r - 1},${y} Z`} {...common} />
  return <circle cx={x} cy={y} r={r} {...common} />
}

/** Push line-end labels apart vertically so they never overlap (min 14 px between baselines). */
function spreadLabels<T extends { y: number }>(labels: T[]): T[] {
  const out = [...labels].sort((a, b) => a.y - b.y).map((l) => ({ ...l }))
  for (let i = 1; i < out.length; i++) if (out[i].y - out[i - 1].y < 14) out[i].y = out[i - 1].y + 14
  return out
}

function ticks(lo: number, hi: number, count = 6): number[] {
  const span = hi - lo || 1
  const step0 = span / count
  const mag = 10 ** Math.floor(Math.log10(step0))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? step0
  const out = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toPrecision(6)))
  return out
}

interface LineChartProps {
  title: string
  xLabel: string
  yLabel: string
  points: Pt[]
  seriesNames: string[]
  /** 'series': one line per series. 'all': a single line through every point (one series). */
  connect?: 'series' | 'all'
  xFormat?: (v: number) => string
  height?: number
}

function LineChart({ title, xLabel, yLabel, points, seriesNames, connect = 'series', xFormat = (v) => String(v), height = 340 }: LineChartProps) {
  // Drawn at its real on-screen width so text stays a constant, readable size. A callback ref:
  // the figure may only appear after the first render, and must be measured whenever it does.
  const [el, setEl] = useState<HTMLElement | null>(null)
  const [W, setW] = useState(900)
  useEffect(() => {
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, Math.round(e.contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  const [hover, setHover] = useState<number | null>(null)
  const H = height
  const m = { l: 64, r: 56, t: 14, b: 44 }

  const { sx, sy, xt, yt, lines } = useMemo(() => {
    const xs = points.map((p) => p.x)
    const ys = points.flatMap((p) => [p.y - (p.err ?? 0), p.y + (p.err ?? 0)])
    let x0 = Math.min(...xs), x1 = Math.max(...xs)
    let y0 = Math.min(0, ...ys), y1 = Math.max(...ys)
    const px = (x1 - x0) * 0.03 || 0.5, py = (y1 - y0) * 0.08 || 1
    x0 -= px; x1 += px; y1 += py; if (y0 < 0) y0 -= py
    const sx = (v: number) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r)
    const sy = (v: number) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b)
    const groups = new Map<number, Pt[]>()
    for (const p of points) {
      const k = connect === 'all' ? 0 : p.series
      groups.set(k, [...(groups.get(k) ?? []), p])
    }
    const lines = [...groups].sort((a, b) => a[0] - b[0]).map(([series, ps]) => {
      const sorted = [...ps].sort((a, b) => a.x - b.x)
      const d = sorted.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('')
      return { series, last: sorted[sorted.length - 1], d }
    })
    return { sx, sy, xt: ticks(x0, x1, Math.max(4, Math.floor(W / 110))), yt: ticks(y0, y1, 6), lines }
  }, [points, W, H, connect, m.l, m.r, m.t, m.b])

  if (!points.length) return null
  const hp = hover != null ? points[hover] : null
  const multi = connect === 'series' && lines.length > 1
  const colorOf = (p: Pt) => (connect === 'all' ? colorFor(0) : colorFor(p.series))

  // Crosshair hover: the point nearest the pointer.
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - r.left, y = e.clientY - r.top
    let best = -1, bd = Infinity
    points.forEach((p, i) => {
      const d = Math.hypot(sx(p.x) - x, (sy(p.y) - y) * 0.6)
      if (d < bd) { bd = d; best = i }
    })
    setHover(bd < 60 ? best : null)
  }

  return (
    <figure className="chart" ref={setEl}>
      <figcaption>
        <span className="chart-title">{title}</span>
        {multi && (
          <span className="legend">
            {lines.map(({ series }) => (
              <span key={series} className="legend-item">
                <svg width="22" height="12" aria-hidden="true">
                  <line x1="1" x2="21" y1="6" y2="6" stroke={colorFor(series)} strokeWidth="2" />
                  <Marker shape={shapeFor(series)} x={11} y={6} fill={colorFor(series)} />
                </svg>
                {seriesNames[series]}
              </span>
            ))}
          </span>
        )}
      </figcaption>
      <svg width={W} height={H} role="img" aria-label={title} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {yt.map((v) => (
          <g key={`y${v}`}>
            <line x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} className="gridline" />
            <text x={m.l - 8} y={sy(v) + 4} textAnchor="end" className="tick">{v}</text>
          </g>
        ))}
        {xt.map((v) => (
          <text key={`x${v}`} x={sx(v)} y={H - m.b + 18} textAnchor="middle" className="tick">{xFormat(v)}</text>
        ))}
        <line x1={m.l} x2={W - m.r} y1={H - m.b} y2={H - m.b} className="axis" />
        <text x={(m.l + W - m.r) / 2} y={H - 6} textAnchor="middle" className="axis-label">{xLabel}</text>
        <text x={14} y={(m.t + H - m.b) / 2} textAnchor="middle" className="axis-label"
          transform={`rotate(-90 14 ${(m.t + H - m.b) / 2})`}>{yLabel}</text>
        {hp && <line x1={sx(hp.x)} x2={sx(hp.x)} y1={m.t} y2={H - m.b} className="crosshair" />}
        {lines.map(({ series, d }) => (
          <path key={`l${series}`} d={d} fill="none" stroke={connect === 'all' ? colorFor(0) : colorFor(series)}
            strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
            opacity={hp && multi && hp.series !== series ? 0.35 : 1} />
        ))}
        {points.map((p, i) => p.err ? (
          <line key={`e${i}`} x1={sx(p.x)} x2={sx(p.x)} y1={sy(p.y - p.err)} y2={sy(p.y + p.err)}
            stroke={colorOf(p)} strokeWidth={1} opacity={0.5} />
        ) : null)}
        {points.map((p, i) => (
          <Marker key={i} shape={connect === 'all' ? 'circle' : shapeFor(p.series)} x={sx(p.x)} y={sy(p.y)} fill={colorOf(p)} />
        ))}
        {/* Direct labels at line ends for up to four lines; the legend covers more. */}
        {multi && lines.length <= 4 && spreadLabels(lines.map(({ series, last }) => ({ series, x: sx(last.x) + 8, y: sy(last.y) + 4 })))
          .map(({ series, x, y }) => <text key={`d${series}`} x={x} y={y} className="direct-label">E{series + 1}</text>)}
      </svg>
      {hp && <div className="tooltip" style={{ left: sx(hp.x) + 14, top: sy(hp.y) + 40 }}>{hp.tip}</div>}
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

export function PhotometryCharts({ points, epochDates, mode, hiddenEpochs = [], unit: dataUnit = 'mJy' }: {
  points: PhotPoint[]; epochDates: string[]; mode: 'aperture' | 'wing'; hiddenEpochs?: number[]; unit?: string
}) {
  const wing = mode === 'wing'
  // SPHEREx: mJy; uploaded data: its own units, summed over the aperture.
  const unit = wing ? `wing flux, ${dataUnit}` : dataUnit === 'mJy' ? 'mJy' : `${dataUnit}, summed`
  // Same epochs as the viewer: shallow ones are hidden there by default, so here too.
  // Exposures flagged as contaminated at this spot are left out unless asked for.
  const [withFlagged, setWithFlagged] = useState(false)
  const usable = points.filter((p) => p.wavelength_um != null && !hiddenEpochs.includes(p.epoch))
  const nFlagged = usable.filter((p) => p.contaminated).length
  const clean = usable.filter((p) => withFlagged || !p.contaminated)
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

  if (!usable.length) {
    return <p className="muted">Nothing to measure here: the aperture falls on masked or uncovered pixels in every exposure (for example the edge of the field or a saturated core).</p>
  }
  if (!clean.length) {
    return (
      <p className="muted">
        All {nFlagged} measurements here are flagged as contaminated (light from a ghost, halo, streak or crosstalk at this spot).{' '}
        <button className="link-btn" onClick={() => setWithFlagged(true)}>Show them anyway</button>
      </p>
    )
  }
  return (
    <div className="charts">
      <div className="charts-head">
        <span className="muted small">
          {clean.length} measurements from {new Set(clean.map((p) => p.epoch)).size} epochs
        </span>
        {nFlagged > 0 && (
          <label className="check small">
            <input type="checkbox" checked={withFlagged} onChange={(e) => setWithFlagged(e.target.checked)} />
            Include {nFlagged} flagged as contaminated
          </label>
        )}
        <button className="download-btn" onClick={() => downloadCSV(points, epochDates)}>
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1v9M4 6.5 8 10.5 12 6.5M2 13.5h12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
          Download CSV
        </button>
      </div>
      <LineChart title={wing ? "Spectrum by epoch (saturated: wing flux)" : "Spectrum by epoch"} xLabel="Wavelength (µm)" yLabel={`Flux (${unit})`} points={spectrum} seriesNames={names} />
      <LineChart title="Brightness per epoch (1 = average)" xLabel="Year" yLabel="Relative brightness" points={lc} seriesNames={names}
        connect="all"
        xFormat={(v) => v.toFixed(1)} />
      <p className="muted small">
        {wing
          ? 'Saturated star: the core is masked, so flux is measured in a fixed ring around it. It tracks relative brightness, not total flux. '
          : 'Quick-look aperture photometry on reprojected frames. '}
        Contaminated exposures excluded{hiddenEpochs.length ? `; shallow epochs (E${hiddenEpochs.map((e) => e + 1).join(', E')}) hidden as in the viewer` : ''}.
        For science-grade spectra use IRSA's SPHEREx spectrophotometry tool.
      </p>
    </div>
  )
}
