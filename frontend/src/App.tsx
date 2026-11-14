import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  fetchFrames, fetchGaia, fetchPhotometry, resolveTarget,
  type FramesResult, type GaiaSource, type PhotPoint, type Resolved, type ViewParams,
} from './api'
import { PhotometryCharts } from './components/Charts'
import { Reference, type RefSource } from './components/Reference'
import { Viewer } from './components/Viewer'
import type { StretchOpts } from './render'
import { formatDec, formatRA, pixelToSky } from './sky'

const TARGETS = [
  { label: "Barnard's Star", q: "Barnard's Star", hint: 'high proper motion', fov: 0.1 },
  { label: 'Epsilon Indi B', q: 'eps Ind B', hint: 'brown-dwarf pair', fov: 0.1 },
  // By name so it is carried to 2026 with its proper motion; the challenge's coordinates
  // (08h55m10.831s -07°14′42.53″) are from ~2010 and are now about 1.4′ off.
  { label: 'WISE 0855', q: 'WISE J085510.83-071442.5', hint: 'fast brown dwarf', fov: 0.12 },
  { label: 'RW Aurigae', q: 'RW Aur', hint: 'variable star', fov: 0.08 },
  { label: 'R Draconis', q: 'R Dra', hint: 'variable star', fov: 0.08 },
]

const DETECTORS = [
  { d: 1, range: '0.75–1.09 µm' }, { d: 2, range: '1.10–1.62 µm' }, { d: 3, range: '1.63–2.41 µm' },
  { d: 4, range: '2.42–3.82 µm' }, { d: 5, range: '3.83–4.41 µm' }, { d: 6, range: '4.42–5.00 µm' },
]

function readURL(): { params: ViewParams | null; q: string | null; speed: number } {
  const u = new URLSearchParams(location.search)
  const ra = parseFloat(u.get('ra') ?? ''), dec = parseFloat(u.get('dec') ?? '')
  const dets = (u.get('dets') ?? '5,6').split(',').map(Number).filter((d) => d >= 1 && d <= 6)
  const speed = parseInt(u.get('speed') ?? '500', 10) || 500
  const mode = u.get('mode') === 'exposure' ? 'exposure' : 'epoch'
  const fov = Math.min(0.25, Math.max(0.02, parseFloat(u.get('fov') ?? '0.1') || 0.1))
  if (Number.isFinite(ra) && Number.isFinite(dec)) return { params: { ra, dec, fov, dets, mode }, q: u.get('q'), speed }
  return { params: null, q: u.get('q'), speed }
}

export default function App() {
  const init = useMemo(readURL, [])
  const [query, setQuery] = useState(init.q ?? '')
  const [resolved, setResolved] = useState<Resolved | null>(null)
  const [params, setParams] = useState<ViewParams | null>(init.params)
  const [fov, setFov] = useState(init.params?.fov ?? 0.1)
  // The slider moves a draft; the view updates once it rests, not on every step of a drag.
  const [fovDraft, setFovDraft] = useState(fov)
  useEffect(() => {
    const t = setTimeout(() => setFov(fovDraft), 350)
    return () => clearTimeout(t)
  }, [fovDraft])
  const [refSource, setRefSource] = useState<RefSource>('unwise')
  const [dets, setDets] = useState<number[]>(init.params?.dets ?? [5, 6])
  const [mode, setMode] = useState<'epoch' | 'exposure'>(init.params?.mode ?? 'epoch')
  const [result, setResult] = useState<FramesResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(init.speed)
  const [stretch, setStretch] = useState<StretchOpts>({ display: 'frames', transfer: 'asinh', lowPct: 1, highPct: 99.5, invert: true })
  const [smooth, setSmooth] = useState(false)
  const [showGaia, setShowGaia] = useState(true)
  const [skipShallow, setSkipShallow] = useState(true)
  const [photMode, setPhotMode] = useState<'aperture' | 'wing'>('aperture')
  const [gaia, setGaia] = useState<GaiaSource[] | null>(null)
  const [pick, setPick] = useState<[number, number] | null>(null)
  const [phot, setPhot] = useState<PhotPoint[] | null>(null)
  const [hover, setHover] = useState<[number, number] | null>(null)

  // ---- search
  const go = useCallback(async (q: string, fovOverride?: number) => {
    setError(null)
    setBusy(true)
    try {
      const r = await resolveTarget(q)
      setResolved(r)
      setQuery(q)
      const f = fovOverride ?? fov
      setFov(f)
      setFovDraft(f)
      setParams({ ra: r.ra, dec: r.dec, fov: f, dets, mode })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }, [fov, dets, mode])

  useEffect(() => {
    if (!init.params && init.q) go(init.q)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Settings changes re-run the view without re-resolving.
  useEffect(() => {
    setParams((p) => (p ? { ...p, fov, dets, mode } : p))
  }, [fov, dets, mode])

  // ---- URL sync (shareable links)
  useEffect(() => {
    if (!params) return
    const u = new URLSearchParams({
      ra: params.ra.toFixed(6), dec: params.dec.toFixed(6), fov: params.fov.toFixed(3),
      dets: params.dets.join(','), mode: params.mode, speed: String(speed),
    })
    if (query) u.set('q', query)
    history.replaceState(null, '', `?${u}`)
  }, [params, speed, query])

  // ---- frames, polled until the backend job finishes
  const pollRef = useRef(0)
  const prevParams = useRef<ViewParams | null>(null)
  useEffect(() => {
    if (!params) return
    const ctrl = new AbortController()
    const myPoll = ++pollRef.current
    // Same target and settings with only a new field of view: keep showing the current frames
    // until the re-cropped ones arrive (the backend serves them from the same job).
    const prev = prevParams.current
    const sameView = !!prev && prev.ra === params.ra && prev.dec === params.dec && prev.mode === params.mode
      && prev.dets.join() === params.dets.join()
    prevParams.current = params
    if (!sameView) { setResult(null); setIndex(0) }
    setPick(null); setPhot(null)
    let failures = 0
    const tick = async () => {
      try {
        const r = await fetchFrames(params, ctrl.signal)
        if (myPoll !== pollRef.current) return
        failures = 0
        setResult(r)
        if (r.status === 'error') { setError(r.error); return }
        if (r.status !== 'done') setTimeout(tick, r.frames.length ? 2000 : 1000)
      } catch (e) {
        if ((e as Error).name === 'AbortError' || myPoll !== pollRef.current) return
        // A container restart or a brief network error should not end the session.
        if (++failures <= 5) setTimeout(tick, 1500 * failures)
        else setError((e as Error).message)
      }
    }
    setError(null)
    tick()
    return () => ctrl.abort()
  }, [params])

  // ---- Gaia overlay
  useEffect(() => {
    if (!params || !showGaia) return
    let live = true
    fetchGaia(params.ra, params.dec, params.fov * 0.75)
      .then((g) => live && setGaia(g.sources))
      .catch(() => live && setGaia(null))
    return () => { live = false }
  }, [params, showGaia])

  // ---- frames actually shown (shallow epochs are noisy and patchy)
  const frames = useMemo(
    () => (result?.frames ?? []).filter((f) => !(skipShallow && f.shallow) && f.data),
    [result, skipShallow],
  )
  useEffect(() => { setIndex((i) => (frames.length ? i % frames.length : 0)) }, [frames.length])

  // ---- blink loop
  const nFrames = frames.length
  useEffect(() => {
    if (!playing || nFrames < 2) return
    const t = setInterval(() => setIndex((i) => (i + 1) % nFrames), speed)
    return () => clearInterval(t)
  }, [playing, speed, nFrames])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (['INPUT', 'SELECT', 'BUTTON', 'TEXTAREA'].includes((e.target as HTMLElement).tagName)) return
      if (e.key === ' ') { e.preventDefault(); setPlaying((p) => !p) }
      if (e.key === 'ArrowRight') { setPlaying(false); setIndex((i) => (i + 1) % Math.max(1, nFrames)) }
      if (e.key === 'ArrowLeft') { setPlaying(false); setIndex((i) => (i - 1 + nFrames) % Math.max(1, nFrames)) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nFrames])

  // ---- photometry at a clicked pixel
  const onPick = async (x: number, y: number) => {
    if (!params || !result?.grid.n) return
    setPick([x, y])
    setPhot(null)
    try {
      const r = await fetchPhotometry(params, x, y)
      setPhot(r.points)
      setPhotMode(r.mode)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const grid = result?.grid
  const geom = grid?.n ? { n: grid.n, ra: grid.ra, dec: grid.dec, scaleDeg: grid.pixel_scale_arcsec / 3600 } : null
  const hoverSky = geom && hover ? pixelToSky(geom, hover[0], hover[1]) : null
  const frame = frames[index]
  const progress = result?.progress
  // A view is set but its first response hasn't arrived yet also counts as loading.
  const running = !!params && !error && (!result || (result.status !== 'done' && result.status !== 'error'))

  return (
    <div className="app">
      <header>
        <div className="title-row">
          <h1>SPHEREx Sky Motion Explorer</h1>
          <span className="team">by Team Braobax</span>
        </div>
        <p className="tagline">Blink NASA SPHEREx infrared images of any patch of sky to spot things that move or change.</p>
      </header>

      <label className="search-label" htmlFor="target-input">
        Find a target: <span className="muted">object name or sky coordinates (RA Dec)</span>
      </label>
      <form className="search" onSubmit={(e) => { e.preventDefault(); if (query.trim()) go(query.trim()) }}>
        <input id="target-input" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. Barnard's Star · 08:55:10.8 -07:14:42 · 133.795 -7.245" autoComplete="off" />
        <button type="submit" disabled={busy}>{busy ? 'Resolving…' : 'Show'}</button>
      </form>
      <div className="presets">
        {TARGETS.map((t) => (
          <button key={t.label} className="chip" onClick={() => go(t.q, t.fov)} title={t.hint}>{t.label}</button>
        ))}
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      <main className="layout">
        <section className="stage">
          {grid?.n ? (
            <div className="panes">
              <div className="pane">
              <Viewer grid={grid} frames={frames} index={index} stretch={stretch} smooth={smooth}
                gaia={showGaia ? gaia : null} gaiaEpoch={2016.0} pick={pick} onPick={onPick} onHover={setHover} />
              <div className="frame-info">
                <strong>{frame?.label}</strong> · {frame?.date}
                {frame?.wavelength && ` · ${frame.wavelength[0].toFixed(2)}${frame.wavelength[1] !== frame.wavelength[0] ? `–${frame.wavelength[1].toFixed(2)}` : ''} µm`}
                {mode === 'epoch' && frame && ` · ${frame.nUsed} exposures`}
                {frame && frame.nContaminated > 0 && <span className="warn"> · {frame.nContaminated} flagged</span>}
                {frame?.meta?.target_flags.length ? <span className="muted"> · flags: {frame.meta.target_flags.join(', ')}</span> : null}
              </div>
              <div className="cursor muted">
                {hoverSky ? `${formatRA(hoverSky[0])}  ${formatDec(hoverSky[1])}  (${hoverSky[0].toFixed(5)}, ${hoverSky[1].toFixed(5)})` : 'Hover for coordinates · click a source for its spectrum'}
              </div>
          {running && progress && progress.total > 0 && (
                <div className="progress" aria-live="polite">
                  <div className="bar"><div style={{ width: `${(100 * progress.done) / progress.total}%` }} /></div>
                  <span>Loading exposures {progress.done}/{progress.total}{progress.failed ? ` (${progress.failed} failed)` : ''}. Frames sharpen as data arrives.</span>
                </div>
              )}

              {frames.length > 0 && (
                <div className="strip">
                  {frames.map((f, i) => (
                    <button key={i} className={`thumb ${i === index ? 'active' : ''}`} onClick={() => { setPlaying(false); setIndex(i) }}>
                      <span>{mode === 'epoch' ? f.label.replace('Epoch ', 'E') : i + 1}</span>
                      <small>{f.date.slice(0, 10)}</small>
                    </button>
                  ))}
                </div>
              )}
              </div>
              <div className="pane">
                <Reference grid={grid} source={refSource} onSource={setRefSource}
                  frames={frames.map((f) => f.data)} stretch={stretch} />
              </div>
            </div>
          ) : (
            <div className="placeholder">
              {running || busy ? (
                <p>
                  <span className="spinner" aria-hidden="true" />{' '}
                  {busy ? 'Looking up the target…'
                    : result?.status === 'queued' ? 'Waiting for another search to finish…'
                    : result?.status === 'querying' ? 'Searching the SPHEREx archive…'
                    : result?.status === 'fetching' ? 'Loading the first exposures…'
                    : 'Starting…'}
                  <br /><small className="muted">A new position takes about 10–30 seconds; after that it is cached.</small>
                </p>
              ) : (
                <p>Pick a target above or enter coordinates.</p>
              )}
            </div>
          )}
        </section>

        <aside className="controls">
          {resolved && params && (
            <div className="target">
              <div className="target-name">{resolved.name ?? 'Coordinates'}{resolved.otype ? <span className="muted"> · {resolved.otype}</span> : null}</div>
              <div className="mono">{formatRA(params.ra)} {formatDec(params.dec)}</div>
              <div className="muted small">{resolved.note}</div>
            </div>
          )}

          <fieldset>
            <legend>View</legend>
            <div className="seg" role="group" aria-label="Frames to animate">
              <button aria-pressed={mode === 'epoch'} className={mode === 'epoch' ? 'on' : ''} onClick={() => setMode('epoch')}>Epochs</button>
              <button aria-pressed={mode === 'exposure'} className={mode === 'exposure' ? 'on' : ''} onClick={() => setMode('exposure')}>Exposures</button>
            </div>
            <p className="muted small seg-hint">
              {mode === 'epoch'
                ? 'One stacked image per survey visit: best for stars and brown dwarfs.'
                : 'Every exposure in time order: best for asteroids and comets.'}
            </p>
            <div className="seg" role="group" aria-label="Image type">
              <button aria-pressed={stretch.display === 'frames'} className={stretch.display === 'frames' ? 'on' : ''}
                onClick={() => setStretch({ ...stretch, display: 'frames' })}>Images</button>
              <button aria-pressed={stretch.display === 'difference'} className={stretch.display === 'difference' ? 'on' : ''}
                onClick={() => setStretch({ ...stretch, display: 'difference' })}>Difference</button>
            </div>
            <p className="muted small seg-hint">
              {stretch.display === 'frames'
                ? 'The images as observed.'
                : 'Each frame minus the average: still stars vanish, movers show as dark/light pairs.'}
            </p>
          </fieldset>

          <fieldset>
            <legend>Playback</legend>
            <div className="row">
              <button onClick={() => setPlaying((p) => !p)}>{playing ? 'Pause' : 'Play'}</button>
              <button onClick={() => { setPlaying(false); setIndex((i) => (i - 1 + nFrames) % Math.max(1, nFrames)) }} aria-label="Previous frame">◀</button>
              <button onClick={() => { setPlaying(false); setIndex((i) => (i + 1) % Math.max(1, nFrames)) }} aria-label="Next frame">▶</button>
            </div>
            <label>Blink interval {speed} ms
              <input type="range" min={100} max={2000} step={50} value={speed} onChange={(e) => setSpeed(+e.target.value)} />
            </label>
          </fieldset>

          <fieldset>
            <legend>Wavelength (detectors)</legend>
            <div className="dets">
              {DETECTORS.map(({ d, range }) => (
                <label key={d} className="check" title={range}>
                  <input type="checkbox" checked={dets.includes(d)}
                    onChange={(e) => setDets((cur) => {
                      const next = e.target.checked ? [...cur, d] : cur.filter((x) => x !== d)
                      return next.length ? next.sort() : cur
                    })} />
                  D{d} <small className="muted">{range}</small>
                </label>
              ))}
            </div>
            <p className="muted small">Brown dwarfs are brightest in D5–D6. Fewer detectors load faster.</p>
          </fieldset>

          <fieldset>
            <legend>Field of view</legend>
            <label>{(fovDraft * 60).toFixed(1)}′
              <input type="range" min={0.02} max={0.25} step={0.01} value={fovDraft} onChange={(e) => setFovDraft(+e.target.value)} />
            </label>
          </fieldset>

          <fieldset>
            <legend>Display</legend>
            <label>Brightness clip {stretch.highPct}%
              <input type="range" min={95} max={99.95} step={0.05} value={stretch.highPct} onChange={(e) => setStretch({ ...stretch, highPct: +e.target.value })} />
            </label>
            <label className="check"><input type="checkbox" checked={stretch.transfer === 'asinh'} onChange={(e) => setStretch({ ...stretch, transfer: e.target.checked ? 'asinh' : 'linear' })} /> Asinh stretch</label>
            <label className="check"><input type="checkbox" checked={stretch.invert} onChange={(e) => setStretch({ ...stretch, invert: e.target.checked })} /> Invert (dark stars)</label>
            <label className="check"><input type="checkbox" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} /> Smooth pixels</label>
            <label className="check"><input type="checkbox" checked={skipShallow} onChange={(e) => setSkipShallow(e.target.checked)} /> Skip shallow epochs <small className="muted">(few exposures)</small></label>
            <label className="check"><input type="checkbox" checked={showGaia} onChange={(e) => setShowGaia(e.target.checked)} /> Gaia DR3 overlay <small className="muted">(moved to frame date)</small></label>
          </fieldset>
        </aside>
      </main>

      {pick && (
        <section className="photometry">
          <h2>Source at {geom ? (() => { const s = pixelToSky(geom, pick[0], pick[1]); return `${formatRA(s[0])} ${formatDec(s[1])}` })() : ''}</h2>
          {phot ? <PhotometryCharts points={phot} mode={photMode} epochDates={result?.epochs.map((e) => e.date_mid) ?? []} /> : <p className="muted">Measuring…</p>}
        </section>
      )}

      {result?.status === 'done' && result.frames.length === 0 && (
        <p className="muted">No SPHEREx images cover this position yet in the selected detectors.</p>
      )}

      <footer className="muted small">
        Data: NASA SPHEREx via NASA/IPAC Infrared Science Archive (IRSA); ESA Gaia DR3; CDS Sesame.
        Keys: Space play/pause, ← → step frames.
      </footer>
    </div>
  )
}
