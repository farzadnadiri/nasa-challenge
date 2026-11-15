import { useEffect, useMemo, useRef, useState } from 'react'
import type { GaiaSource, Grid } from '../api'
import { ErrorBoundary } from './ErrorBoundary'
import { ALADIN_LAYERS, AladinPanel, type AladinLayer } from './AladinPanel'
import { computeLimits, meanFrame, renderToImageData, type StretchOpts } from '../render'

// A static, deeper image of the same field for comparison, like WiseView's lower panel.
// External surveys come from the Legacy Survey viewer's cutout service (north up, east left,
// TAN projection), requested at exactly the blink view's field and centre so they line up.
export type RefSource = 'unwise' | 'ls' | 'spherex' | 'aladin'

export const REF_SOURCES: { id: RefSource; label: string; note: string }[] = [
  { id: 'unwise', label: 'unWISE (infrared)', note: 'unWISE W1+W2, 3.4 and 4.6 µm, averaged over 2010–2020' },
  { id: 'ls', label: 'Legacy Survey (optical)', note: 'DESI Legacy Surveys DR10, g r z colour; not every part of the sky' },
  { id: 'spherex', label: 'SPHEREx stack', note: 'All shown SPHEREx epochs averaged: deeper, but movers smear' },
  { id: 'aladin', label: 'Aladin Lite (interactive)', note: 'CDS Aladin Lite: pan, zoom, switch surveys and catalogues' },
]
const LAYER: Record<Exclude<RefSource, 'spherex' | 'aladin'>, string> = { unwise: 'unwise-neo7', ls: 'ls-dr10' }
const PX = 512 // requested image size; the browser scales it to the panel

interface Props {
  grid: Grid
  source: RefSource
  onSource: (s: RefSource) => void
  frames: (Float32Array | null)[]
  stretch: StretchOpts
  gaia: GaiaSource[] | null
}

export function Reference({ grid, source, onSource, frames, stretch, gaia }: Props) {
  // Which image URL finished (or failed). Keyed by URL so a cached image that loads before
  // any effect runs can never be overwritten by a late 'loading' reset.
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const [aladinLayer, setAladinLayer] = useState<AladinLayer>('allwise')
  const [aladinGaia, setAladinGaia] = useState(true)
  const [aladinCatwise, setAladinCatwise] = useState(false)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const url = useMemo(() => {
    if (source === 'spherex' || source === 'aladin') return null
    const fovArcsec = grid.n * grid.pixel_scale_arcsec
    const q = new URLSearchParams({
      ra: grid.ra.toFixed(6), dec: grid.dec.toFixed(6), layer: LAYER[source],
      pixscale: (fovArcsec / PX).toFixed(4), size: String(PX),
    })
    return `https://www.legacysurvey.org/viewer/jpeg-cutout?${q}`
  }, [grid, source])

  // SPHEREx stack: mean of the epochs, drawn with the blink view's own stretch (never differenced).
  useEffect(() => {
    if (source !== 'spherex' || !canvasRef.current || !grid.n) return
    const mean = meanFrame(frames)
    const opts: StretchOpts = { ...stretch, display: 'frames' }
    const c = canvasRef.current
    c.width = c.height = grid.n
    c.getContext('2d')!.putImageData(renderToImageData(mean, grid.n, computeLimits([mean], opts), opts), 0, 0)
  }, [source, frames, grid.n, stretch])

  const note = REF_SOURCES.find((s) => s.id === source)!.note
  return (
    <>
      {source === 'aladin' ? (
        <ErrorBoundary key={`${grid.ra},${grid.dec}`} fallback={
          <div className="viewer-wrap ref-wrap"><div className="ref-status">Aladin Lite stopped working here. Choose another reference image.</div></div>}>
          <AladinPanel grid={grid} layer={aladinLayer} gaia={gaia} showGaia={aladinGaia} showCatwise={aladinCatwise} />
        </ErrorBoundary>
      ) : (
      <div className="viewer-wrap ref-wrap">
        {source === 'spherex'
          ? <canvas ref={canvasRef} className="ref-img pixelated" aria-label="SPHEREx stack of all epochs" />
          : <img key={url} src={url!} className="ref-img" alt={`${note} at the same field`}
              ref={(el) => { if (el?.complete && el.naturalWidth > 0) setLoadedUrl(el.src) }}
              onLoad={(e) => setLoadedUrl(e.currentTarget.src)} onError={(e) => setFailedUrl(e.currentTarget.src)} />}
        <svg className="ref-overlay" viewBox="0 0 100 100" aria-hidden="true">
          <path d="M50 44.5V41M50 55.5V59M44.5 50H41M55.5 50H59" stroke="rgba(255,196,0,0.9)" strokeWidth="0.45" />
        </svg>
        {source !== 'spherex' && url && loadedUrl !== url && failedUrl !== url && (
          <div className="ref-status"><p><span className="spinner" aria-hidden="true" />Loading reference…</p></div>)}
        {source !== 'spherex' && url && failedUrl === url && <div className="ref-status">Reference image unavailable for this field.</div>}
      </div>
      )}
      <div className="ref-controls">
        <label className="sr-only" htmlFor="ref-source">Reference image</label>
        <select id="ref-source" value={source} onChange={(e) => onSource(e.target.value as RefSource)}>
          {REF_SOURCES.map((s) => <option key={s.id} value={s.id}>Reference: {s.label}</option>)}
        </select>
        {source === 'aladin' && (
          <div className="aladin-controls">
            <label className="sr-only" htmlFor="aladin-layer">Aladin image layer</label>
            <select id="aladin-layer" value={aladinLayer} onChange={(e) => setAladinLayer(e.target.value as AladinLayer)}>
              {ALADIN_LAYERS.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <label className="check"><input type="checkbox" checked={aladinGaia} onChange={(e) => setAladinGaia(e.target.checked)} /> Gaia DR3</label>
            <label className="check"><input type="checkbox" checked={aladinCatwise} onChange={(e) => setAladinCatwise(e.target.checked)} /> CatWISE2020</label>
          </div>
        )}
        <span className="muted small">
          {source === 'aladin'
            ? `${ALADIN_LAYERS.find((l) => l.id === aladinLayer)!.name} from IRSA. Drag to pan, scroll to zoom; markers show source details.`
            : `${note}${source !== 'spherex' ? '. Fast movers appear at their older position' : ''}.`}
        </span>
      </div>
    </>
  )
}
