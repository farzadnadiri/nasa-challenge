import { useEffect, useMemo, useRef, useState } from 'react'
import type { Grid } from '../api'
import { computeLimits, meanFrame, renderToImageData, type StretchOpts } from '../render'

// A static, deeper image of the same field for comparison, like WiseView's lower panel.
// External surveys come from the Legacy Survey viewer's cutout service (north up, east left,
// TAN projection), requested at exactly the blink view's field and centre so they line up.
export type RefSource = 'unwise' | 'ls' | 'spherex'

export const REF_SOURCES: { id: RefSource; label: string; note: string }[] = [
  { id: 'unwise', label: 'unWISE (infrared)', note: 'unWISE W1+W2, 3.4 and 4.6 µm, averaged over 2010–2020' },
  { id: 'ls', label: 'Legacy Survey (optical)', note: 'DESI Legacy Surveys DR10, g r z colour; not every part of the sky' },
  { id: 'spherex', label: 'SPHEREx stack', note: 'All shown SPHEREx epochs averaged: deeper, but movers smear' },
]
const LAYER: Record<Exclude<RefSource, 'spherex'>, string> = { unwise: 'unwise-neo7', ls: 'ls-dr10' }
const PX = 512 // requested image size; the browser scales it to the panel

interface Props {
  grid: Grid
  source: RefSource
  onSource: (s: RefSource) => void
  frames: (Float32Array | null)[]
  stretch: StretchOpts
}

export function Reference({ grid, source, onSource, frames, stretch }: Props) {
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading')
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const url = useMemo(() => {
    if (source === 'spherex') return null
    const fovArcsec = grid.n * grid.pixel_scale_arcsec
    const q = new URLSearchParams({
      ra: grid.ra.toFixed(6), dec: grid.dec.toFixed(6), layer: LAYER[source],
      pixscale: (fovArcsec / PX).toFixed(4), size: String(PX),
    })
    return `https://www.legacysurvey.org/viewer/jpeg-cutout?${q}`
  }, [grid, source])

  useEffect(() => { setState('loading') }, [url])

  // SPHEREx stack: mean of the epochs, drawn with the blink view's own stretch (never differenced).
  useEffect(() => {
    if (source !== 'spherex' || !canvasRef.current || !grid.n) return
    const mean = meanFrame(frames)
    const opts: StretchOpts = { ...stretch, display: 'frames' }
    const c = canvasRef.current
    c.width = c.height = grid.n
    c.getContext('2d')!.putImageData(renderToImageData(mean, grid.n, computeLimits([mean], opts), opts), 0, 0)
    setState('ok')
  }, [source, frames, grid.n, stretch])

  const note = REF_SOURCES.find((s) => s.id === source)!.note
  return (
    <>
      <div className="viewer-wrap ref-wrap">
        {source === 'spherex'
          ? <canvas ref={canvasRef} className="ref-img pixelated" aria-label="SPHEREx stack of all epochs" />
          : <img key={url} src={url!} className="ref-img" alt={`${note} at the same field`}
              onLoad={() => setState('ok')} onError={() => setState('error')} />}
        <svg className="ref-overlay" viewBox="0 0 100 100" aria-hidden="true">
          <path d="M50 44.5V41M50 55.5V59M44.5 50H41M55.5 50H59" stroke="rgba(255,196,0,0.9)" strokeWidth="0.45" />
        </svg>
        {state === 'loading' && <div className="ref-status"><span className="spinner" /> Loading reference…</div>}
        {state === 'error' && <div className="ref-status">Reference image unavailable for this field.</div>}
      </div>
      <div className="ref-controls">
        <label className="sr-only" htmlFor="ref-source">Reference image</label>
        <select id="ref-source" value={source} onChange={(e) => onSource(e.target.value as RefSource)}>
          {REF_SOURCES.map((s) => <option key={s.id} value={s.id}>Reference: {s.label}</option>)}
        </select>
        <span className="muted small">{note}{source !== 'spherex' && '. Fast movers appear at their older position'}.</span>
      </div>
    </>
  )
}
