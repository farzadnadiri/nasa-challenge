import { useEffect, useMemo, useRef, useState } from 'react'
import type { Frame, GaiaSource, Grid } from '../api'
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
  pick: [number, number] | null
  onPick: (x: number, y: number) => void
  onHover: (xy: [number, number] | null) => void
}

const GAIA_MAX_MARKERS = 80 // brightest Gaia stars shown; SPHEREx sees few fainter ones, more is clutter

export function Viewer({ grid, frames, index, stretch, smooth, gaia, gaiaEpoch, pick, onPick, onHover }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
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
    // grid pixel (FITS, y north) -> canvas coords (pixel centres)
    const toCanvas = (x: number, y: number): [number, number] => [(x + 0.5) * k, (grid.n - 0.5 - y) * k]

    // Gaia DR3, carried from 2016.0 to this frame's date.
    const frame = frames[index]
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
        const fast = Math.hypot(pmra, pmdec) > 200
        ctx.strokeStyle = fast ? 'rgba(255, 92, 92, 0.95)' : 'rgba(80, 200, 255, 0.7)'
        ctx.beginPath()
        ctx.arc(cx, cy, fast ? 9 : 6, 0, Math.PI * 2)
        ctx.stroke()
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
  }, [bitmaps, index, smooth, gaia, gaiaEpoch, frames, geom, grid, pick, SIZE])

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
        onMouseMove={(e) => onHover(toGrid(e))}
        onMouseLeave={() => onHover(null)}
      />
    </div>
  )
}
