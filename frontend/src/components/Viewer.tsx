import { useEffect, useMemo, useRef, useState } from 'react'
import type { CatwiseSource, Frame, GaiaSource, Grid, Mover } from '../api'
import { computeLimits, displayFrames, renderToImageData, type StretchOpts } from '../render'
import { propagate, skyToPixel, type GridGeom } from '../sky'

interface Props {
  grid: Grid
  frames: Frame[]
  index: number
  stretch: StretchOpts
  smooth: boolean
  gaia: GaiaSource[] | null
  gaiaEpoch: number
  catwise?: CatwiseSource[] | null
  catwiseEpoch?: number
  showContam?: boolean
  movers?: Mover[] | null
  pick: [number, number] | null
  onPick: (x: number, y: number) => void
  onHover: (xy: [number, number] | null) => void
}

const GAIA_MAX_MARKERS = 80 // brightest Gaia stars shown; SPHEREx sees few fainter ones, more is clutter

// Common SIMBAD object types, in words (anything else is shown as SIMBAD's own code).
const OTYPES: Record<string, string> = {
  '*': 'star', '**': 'double or multiple star', 'PM*': 'high proper-motion star', 'BD*': 'brown dwarf',
  'BY*': 'BY Draconis variable', 'Mi*': 'Mira variable', 'TT*': 'T Tauri star', 'V*': 'variable star',
  'LP*': 'long-period variable', 'SB*': 'spectroscopic binary', 'EB*': 'eclipsing binary', 'RR*': 'RR Lyrae variable',
  'WD*': 'white dwarf', 'RG*': 'red giant', 'LM*': 'low-mass star', 'Ro*': 'rotating variable', 'G': 'galaxy', 'QSO': 'quasar',
}

function WiseTooltip({ src, x, y, size }: { src: CatwiseSource; x: number; y: number; size: number }) {
  const pm = Math.hypot(src.pmra ?? 0, src.pmdec ?? 0)
  const red = (src.w1_w2 ?? 0) >= 1.0
  return (
    <div className="star-tip" role="tooltip"
      style={{ left: `${(x / size) * 100}%`, top: `${(y / size) * 100}%`,
        transform: `translate(${x > size * 0.7 ? '-100%' : x < size * 0.3 ? '0' : '-50%'}, ${y < 120 ? '16px' : 'calc(-100% - 14px)'})` }}>
      <div className="star-tip-name">CatWISE {src.name}</div>
      <div className="star-tip-type">WISE infrared source{red ? ': very red, brown-dwarf-like colour' : ''}</div>
      <dl>
        {src.w1 != null && <><dt>W1 (3.4 µm)</dt><dd>{src.w1.toFixed(2)} mag</dd></>}
        {src.w2 != null && <><dt>W2 (4.6 µm)</dt><dd>{src.w2.toFixed(2)} mag</dd></>}
        {src.w1_w2 != null && <><dt>Colour</dt><dd>W1 − W2 = {src.w1_w2.toFixed(2)}{red ? ' (red)' : ''}</dd></>}
        <dt>Motion</dt><dd>{(pm / 1000).toFixed(pm > 1000 ? 2 : 3)}″ per year{pm > 200 ? ' (fast)' : ''}</dd>
      </dl>
    </div>
  )
}

function StarTooltip({ star, x, y, size }: { star: GaiaSource; x: number; y: number; size: number }) {
  const pm = Math.hypot(star.pmra ?? 0, star.pmdec ?? 0)
  const plx = star.parallax ?? 0
  const named = star.name && !star.name.startsWith('Gaia DR3')
  const below = y < 120 // near the top: show under the marker
  return (
    <div className="star-tip" role="tooltip"
      style={{ left: `${(x / size) * 100}%`, top: `${(y / size) * 100}%`,
        transform: `translate(${x > size * 0.7 ? '-100%' : x < size * 0.3 ? '0' : '-50%'}, ${below ? '16px' : 'calc(-100% - 14px)'})` }}>
      <div className="star-tip-name">{named ? star.name : `Gaia DR3 ${star.source_id}`}</div>
      {star.otype && <div className="star-tip-type">{OTYPES[star.otype] ?? star.otype}</div>}
      {named && <div className="muted">Gaia DR3 {star.source_id}</div>}
      <dl>
        {star.phot_g_mean_mag != null && <><dt>Brightness</dt><dd>G = {star.phot_g_mean_mag.toFixed(2)} mag</dd></>}
        {star.bp_rp != null && <><dt>Colour</dt><dd>BP − RP = {star.bp_rp.toFixed(2)} {star.bp_rp > 2 ? '(red)' : star.bp_rp < 0.5 ? '(blue)' : ''}</dd></>}
        {plx > 0.2 && <><dt>Distance</dt><dd>≈ {(1000 / plx).toFixed(plx > 10 ? 1 : 0)} pc ({((1000 / plx) * 3.2616).toFixed(plx > 10 ? 1 : 0)} light years)</dd></>}
        <dt>Motion</dt><dd>{(pm / 1000).toFixed(pm > 1000 ? 2 : 3)}″ per year{pm > 200 ? ' (fast)' : ''}</dd>
      </dl>
    </div>
  )
}

export function Viewer({ grid, frames, index, stretch, smooth, gaia, gaiaEpoch, catwise, catwiseEpoch = 2015.4, showContam, movers, pick, onPick, onHover }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  // Gaia markers as drawn in the current frame (canvas CSS px), for hover lookups.
  type Marker = { kind: 'gaia'; s: GaiaSource; cx: number; cy: number } | { kind: 'wise'; s: CatwiseSource; cx: number; cy: number }
  const markersRef = useRef<Marker[]>([])
  const [hoverStar, setHoverStar] = useState<Marker | null>(null)
  // Draw at the canvas's real on-screen size so text and lines stay crisp at any size.
  const [SIZE, setSize] = useState(640)
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setSize(Math.max(240, Math.round(e.contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const geom: GridGeom = useMemo(
    () => ({ n: grid.n, ra: grid.ra, dec: grid.dec, scaleDeg: grid.pixel_scale_arcsec / 3600 }),
    [grid],
  )

  // Pre-render every frame once per stretch change; blinking then only swaps bitmaps.
  const contamBitmaps = useMemo(() => frames.map((f) => {
    if (!f.contam || !grid.n || f.contam.length !== grid.n * grid.n) return null
    const img = new ImageData(grid.n, grid.n)
    let any = false
    for (let y = 0; y < grid.n; y++) {
      const src = (grid.n - 1 - y) * grid.n // FITS rows go south -> north
      for (let x = 0; x < grid.n; x++) {
        const v = f.contam[src + x]
        if (!v) continue
        any = true
        const o = (y * grid.n + x) * 4
        img.data[o] = 255; img.data[o + 1] = 140; img.data[o + 2] = 0
        img.data[o + 3] = 70 + Math.round((v / 255) * 110)
      }
    }
    if (!any) return null
    const c = document.createElement('canvas')
    c.width = c.height = grid.n
    c.getContext('2d')!.putImageData(img, 0, 0)
    return c
  }), [frames, grid.n])

  const bitmaps = useMemo(() => {
    if (!grid.n) return []
    const shown = displayFrames(frames.map((f) => f.data), stretch.display)
    const lim = computeLimits(shown, stretch)
    return shown.map((f) => {
      const c = document.createElement('canvas')
      c.width = c.height = grid.n
      c.getContext('2d')!.putImageData(renderToImageData(f, grid.n, lim, stretch), 0, 0)
      return c
    })
  }, [frames, grid.n, stretch])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !grid.n) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = SIZE * dpr
    canvas.height = SIZE * dpr
    const ctx = canvas.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, SIZE, SIZE)
    const k = SIZE / grid.n // CSS px per grid pixel
    const bmp = bitmaps[index]
    if (bmp) {
      ctx.imageSmoothingEnabled = smooth
      ctx.drawImage(bmp, 0, 0, SIZE, SIZE)
    }
    const cbmp = showContam ? contamBitmaps[index] : null
    if (cbmp) {
      ctx.imageSmoothingEnabled = false
      ctx.drawImage(cbmp, 0, 0, SIZE, SIZE)
    }
    // grid pixel (FITS, y north) -> canvas coords (pixel centres)
    const toCanvas = (x: number, y: number): [number, number] => [(x + 0.5) * k, (grid.n - 0.5 - y) * k]

    // Gaia DR3, carried from 2016.0 to this frame's date.
    const frame = frames[index]
    markersRef.current = []
    if (gaia && frame) {
      const dt = frame.decimalYear - gaiaEpoch
      ctx.lineWidth = 1.25
      let shown = 0
      // Sources arrive sorted brightest first; fast movers are always drawn.
      for (const s of gaia) {
        const fastMover = Math.hypot(s.pmra ?? 0, s.pmdec ?? 0) > 200
        if (shown >= GAIA_MAX_MARKERS && !fastMover) continue
        const pmra = s.pmra ?? 0, pmdec = s.pmdec ?? 0
        const [ra, dec] = propagate(s.ra, s.dec, pmra, pmdec, dt)
        const p = skyToPixel(geom, ra, dec)
        if (!p) continue
        const [cx, cy] = toCanvas(p[0], p[1])
        if (cx < -10 || cy < -10 || cx > SIZE + 10 || cy > SIZE + 10) continue
        shown++
        markersRef.current.push({ kind: 'gaia', s, cx, cy })
        const fast = Math.hypot(pmra, pmdec) > 200
        ctx.strokeStyle = fast ? 'rgba(255, 92, 92, 0.95)' : 'rgba(80, 200, 255, 0.7)'
        ctx.beginPath()
        ctx.arc(cx, cy, fast ? 9 : 6, 0, Math.PI * 2)
        ctx.stroke()
      }
    }

    // CatWISE2020 (WISE mission) sources: orange squares, moved from 2015.4 to this frame's date.
    // A very red W1 - W2 colour (brown-dwarf-like) gets a thicker square.
    if (catwise && frame) {
      const dt = frame.decimalYear - catwiseEpoch
      let shown = 0
      for (const s of catwise) {
        const fastMover = Math.hypot(s.pmra ?? 0, s.pmdec ?? 0) > 200
        if (shown >= GAIA_MAX_MARKERS && !fastMover) continue
        const [ra, dec] = propagate(s.ra, s.dec, s.pmra ?? 0, s.pmdec ?? 0, dt)
        const p = skyToPixel(geom, ra, dec)
        if (!p) continue
        const [cx, cy] = toCanvas(p[0], p[1])
        if (cx < -10 || cy < -10 || cx > SIZE + 10 || cy > SIZE + 10) continue
        shown++
        markersRef.current.push({ kind: 'wise', s, cx, cy })
        const red = (s.w1_w2 ?? 0) >= 1.0
        ctx.strokeStyle = 'rgba(255, 169, 77, 0.9)'
        ctx.lineWidth = red ? 2.4 : 1.2
        ctx.strokeRect(cx - 5, cy - 5, 10, 10)
      }
    }

    // Moving sources: the measured track, and a diamond at this frame's date
    // (magenta solid = new candidate with no Gaia match, dashed = matched to a Gaia star).
    if (movers && frame) {
      const dec0 = grid.dec * (Math.PI / 180)
      for (const m of movers) {
        const track = m.positions.map((p) => skyToPixel(geom, p.ra, p.dec)).filter((p): p is [number, number] => !!p)
        ctx.strokeStyle = 'rgba(255, 79, 216, 0.35)'
        ctx.lineWidth = 1.5
        ctx.setLineDash([])
        ctx.beginPath()
        track.forEach(([x, y], i) => { const [cx, cy] = toCanvas(x, y); if (i) ctx.lineTo(cx, cy); else ctx.moveTo(cx, cy) })
        ctx.stroke()
        const dt = frame.decimalYear - m.year_mid
        const ra = m.ra + (m.pmra_mas_yr * dt) / 3.6e6 / Math.max(Math.cos(dec0), 1e-6)
        const dec = m.dec + (m.pmdec_mas_yr * dt) / 3.6e6
        const p = skyToPixel(geom, ra, dec)
        if (!p) continue
        const [cx, cy] = toCanvas(p[0], p[1])
        const r = m.status === 'candidate' ? 12 : 9
        ctx.strokeStyle = 'rgba(255, 79, 216, 0.95)'
        ctx.lineWidth = m.status === 'candidate' ? 2.2 : 1.6
        ctx.setLineDash(m.status === 'candidate' ? [] : [3, 3])
        ctx.beginPath()
        ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath()
        ctx.stroke()
        ctx.setLineDash([])
      }
    }

    // Target crosshair (gap in the middle so the source stays visible).
    const c = toCanvas((grid.n - 1) / 2, (grid.n - 1) / 2)
    ctx.strokeStyle = 'rgba(255, 196, 0, 0.9)'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      ctx.moveTo(c[0] + dx * 10, c[1] + dy * 10)
      ctx.lineTo(c[0] + dx * 22, c[1] + dy * 22)
    }
    ctx.stroke()

    if (pick) {
      const [px, py] = toCanvas(pick[0], pick[1])
      ctx.strokeStyle = 'rgba(0, 255, 140, 0.95)'
      ctx.beginPath()
      ctx.arc(px, py, 2 * k + 2, 0, Math.PI * 2)
      ctx.stroke()
    }

    // First frame of the loop gets a dashed border (as in WiseView) so a mover's direction is unambiguous.
    if (index === 0 && frames.length > 1) {
      ctx.setLineDash([8, 6])
      ctx.strokeStyle = 'rgba(255, 196, 0, 0.9)'
      ctx.lineWidth = 3
      ctx.strokeRect(1.5, 1.5, SIZE - 3, SIZE - 3)
      ctx.setLineDash([])
    }

    // Compass (N up, E left) and a 1' scale bar, drawn with a dark halo so they read on
    // both normal and inverted images.
    const arcminPx = (60 / grid.pixel_scale_arcsec) * k
    ctx.font = 'bold 12px system-ui, sans-serif'
    ctx.lineJoin = 'round'
    for (const [style, width] of [['rgba(0,0,0,0.75)', 4], ['rgba(255,255,255,0.95)', 1.5]] as const) {
      ctx.strokeStyle = style
      ctx.lineWidth = width
      ctx.beginPath()
      ctx.moveTo(SIZE - 20, SIZE - 20); ctx.lineTo(SIZE - 20, SIZE - 50)
      ctx.moveTo(SIZE - 20, SIZE - 20); ctx.lineTo(SIZE - 50, SIZE - 20)
      ctx.moveTo(14, SIZE - 20); ctx.lineTo(14 + arcminPx, SIZE - 20)
      ctx.stroke()
    }
    for (const [text, x, y] of [['N', SIZE - 24, SIZE - 54], ['E', SIZE - 64, SIZE - 16], ['1′', 14 + arcminPx / 2 - 4, SIZE - 26]] as const) {
      ctx.lineWidth = 3
      ctx.strokeStyle = 'rgba(0,0,0,0.75)'
      ctx.strokeText(text, x, y)
      ctx.fillStyle = 'rgba(255,255,255,0.95)'
      ctx.fillText(text, x, y)
    }
  }, [bitmaps, contamBitmaps, showContam, index, smooth, gaia, gaiaEpoch, catwise, catwiseEpoch, movers, frames, geom, grid, pick, SIZE])

  const toGrid = (e: React.MouseEvent<HTMLCanvasElement>): [number, number] => {
    const r = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * grid.n - 0.5
    const y = grid.n - 0.5 - ((e.clientY - r.top) / r.height) * grid.n
    return [x, y]
  }

  return (
    <div ref={wrapRef} className="viewer-wrap">
      <canvas
        ref={canvasRef}
        className="viewer"
        style={{ width: '100%', height: 'auto', aspectRatio: '1 / 1' }}
        onClick={(e) => onPick(...toGrid(e))}
        onMouseMove={(e) => {
          onHover(toGrid(e))
          // Nearest drawn Gaia marker within 10 px of the pointer.
          const r = e.currentTarget.getBoundingClientRect()
          const x = (e.clientX - r.left) * (SIZE / r.width), y = (e.clientY - r.top) * (SIZE / r.height)
          let best: Marker | null = null, bd = 10
          for (const m of markersRef.current) {
            const d = Math.hypot(m.cx - x, m.cy - y)
            if (d < bd) { bd = d; best = m }
          }
          setHoverStar(best)
        }}
        onMouseLeave={() => { onHover(null); setHoverStar(null) }}
      />
      {hoverStar?.kind === 'gaia' && <StarTooltip star={hoverStar.s} x={hoverStar.cx} y={hoverStar.cy} size={SIZE} />}
      {hoverStar?.kind === 'wise' && <WiseTooltip src={hoverStar.s} x={hoverStar.cx} y={hoverStar.cy} size={SIZE} />}
    </div>
  )
}
