import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  CONTAMINATION_FLAGS, fetchCatwise, fetchFrames, fetchGaia, fetchMovers, fetchPhotometry, fetchReleases, resolveTarget,
  type CatwiseSource, type Frame, type FramesResult, type GaiaSource, type Mover, type MoversResult, type PhotPoint, type Releases, type Resolved, type ViewMode, type ViewParams,
} from './api'
import { PhotometryCharts } from './components/Charts'
import { ExposureTable } from './components/ExposureTable'
import { MoversPanel } from './components/MoversPanel'
import { REF_SOURCES, Reference, type RefSource } from './components/Reference'
import { UploadPanel } from './components/UploadPanel'
import { Viewer } from './components/Viewer'
import type { StretchOpts } from './render'
import { exportSideBySide } from './exportImage'
import { formatDec, formatRA, pixelToSky, skyToPixel } from './sky'

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
  const m = u.get('mode')
  const mode: ViewMode = m === 'exposure' || m === 'wavelength' ? m : 'epoch'
  const fov = Math.min(0.25, Math.max(0.02, parseFloat(u.get('fov') ?? '0.1') || 0.1))
  const release = /^(auto|(dr|qr)\d+)$/.test(u.get('release') ?? '') ? u.get('release')! : 'auto'
  const deep = u.get('deep') !== '0' // Deep Fields included unless a link says deep=0
  const dataset = /^[0-9a-f]{32}$/.test(u.get('dataset') ?? '') ? u.get('dataset')! : undefined
  if (dataset) return { params: { ra, dec, fov, dets, mode, release, deep, dataset }, q: null, speed }
  if (Number.isFinite(ra) && Number.isFinite(dec)) return { params: { ra, dec, fov, dets, mode, release, deep }, q: u.get('q'), speed }
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
  const [refSource, setRefSource] = useState<RefSource>(() => {
    const r = new URLSearchParams(location.search).get('ref')
    return REF_SOURCES.some((s) => s.id === r) ? (r as RefSource) : 'unwise'
  })
  const [dets, setDets] = useState<number[]>(init.params?.dets ?? [5, 6])
  // The checkboxes edit a draft; the view follows once clicking stops, so ticking three boxes
  // starts one search instead of three.
  const [detsDraft, setDetsDraft] = useState<number[]>(dets)
  useEffect(() => {
    if (detsDraft.join() === dets.join()) return
    const t = setTimeout(() => setDets(detsDraft), 800)
    return () => clearTimeout(t)
  }, [detsDraft]) // eslint-disable-line react-hooks/exhaustive-deps
  // Shown over the image while another mode or detector set loads (the current view stays up).
  const [switching, setSwitching] = useState<string | null>(null)
  const [mode, setMode] = useState<ViewMode>(init.params?.mode ?? 'epoch')
  // Which SPHEREx data: 'auto' (newest data + quick release) or one release; optionally Deep Fields.
  const [release, setRelease] = useState(() => {
    const r = new URLSearchParams(location.search).get('release') ?? 'auto'
    return /^(auto|(dr|qr)\d+)$/.test(r) ? r : 'auto'
  })
  const [deep, setDeep] = useState(() => new URLSearchParams(location.search).get('deep') !== '0')
  const [releases, setReleases] = useState<Releases | null>(null)
  useEffect(() => { fetchReleases().then(setReleases).catch(() => setReleases(null)) }, [])
  // "Check for new data": the next frames request asks the backend to search the archive again.
  const refreshNext = useRef(false)
  const refreshFrom = useRef<number | null>(null) // exposure count before the check
  const [refreshTick, setRefreshTick] = useState(0)
  const [refreshNote, setRefreshNote] = useState<string | null>(null)
  // ?embed=1: just the viewer and a minimal bar, for <iframe> embedding on other sites.
  const embed = useMemo(() => new URLSearchParams(location.search).get('embed') === '1', [])
  // Images | Difference (each frame minus the mean) | A minus B (two chosen frames).
  // Images | Difference | A minus B | A plus B (two chosen frames added: a deeper image).
  // Images | Difference (each frame minus the average) | Add (stack chosen frames: deeper image)
  // | Subtract (stack of one chosen set minus stack of another: what changed between them).
  const [imageType, setImageType] = useState<'frames' | 'difference' | 'add' | 'subtract'>('frames')
  const [addSet, setAddSet] = useState<number[] | null>(null)  // indices into the shown frames; null = all
  const [plusSet, setPlusSet] = useState<number[] | null>(null)  // null = last frame
  const [minusSet, setMinusSet] = useState<number[] | null>(null) // null = first frame
  const [showWise, setShowWise] = useState(false)  // overlays load only when ticked
  const [catwise, setCatwise] = useState<CatwiseSource[] | null>(null)
  const [showContam, setShowContam] = useState(false)
  const [showTarget, setShowTarget] = useState(true)
  const [showPick, setShowPick] = useState(true)
  const [showFirstMark, setShowFirstMark] = useState(true)
  const [showCompass, setShowCompass] = useState(true)
  const [result, setResult] = useState<FramesResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(init.speed)
  const [stretch, setStretch] = useState<StretchOpts>({ display: 'frames', transfer: 'asinh', lowPct: 1, highPct: 99.5, invert: true })
  const [smooth, setSmooth] = useState(false)
  const [showGaia, setShowGaia] = useState(false)
  const [skipShallow, setSkipShallow] = useState(true)
  const [photMode, setPhotMode] = useState<'aperture' | 'wing'>('aperture')
  const [photUnit, setPhotUnit] = useState('mJy')
  const [showUpload, setShowUpload] = useState(false)
  // Moving-source search (our discovery feature): on by default, runs once the frames are ready.
  const [findMovers, setFindMovers] = useState(false)
  const [moversRes, setMoversRes] = useState<MoversResult | null>(null)
  const [moversLoading, setMoversLoading] = useState(false)
  // The archive view to return to after looking at an uploaded dataset.
  const archiveParams = useRef<ViewParams | null>(null)
  const [gaia, setGaia] = useState<GaiaSource[] | null>(null)
  const [pick, setPick] = useState<[number, number] | null>(null)
  const pendingSrc = useRef<[number, number] | null>((() => {
    const v = (new URLSearchParams(location.search).get('src') ?? '').split(',').map(Number)
    return v.length === 2 && v.every(Number.isFinite) ? [v[0], v[1]] : null
  })())
  const [phot, setPhot] = useState<PhotPoint[] | null>(null)
  // Clicked source in sky coordinates (for the shareable ?src= link).
  const pickSky = useMemo(() => {
    const g = result?.grid
    if (!pick || !g?.n) return null
    return pixelToSky({ n: g.n, ra: g.ra, dec: g.dec, scaleDeg: g.pixel_scale_arcsec / 3600 }, pick[0], pick[1])
  }, [pick, result?.grid])
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
      setParams({ ra: r.ra, dec: r.dec, fov: f, dets, mode, release, deep, dataset: undefined })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }, [fov, dets, mode, release, deep])

  useEffect(() => {
    // No target in the link: open on Barnard's Star, the classic fast mover.
    if (!init.params) go(init.q || TARGETS[0].q, init.q ? undefined : TARGETS[0].fov)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Settings changes re-run the view without re-resolving.
  useEffect(() => {
    setParams((p) => (p ? { ...p, fov, dets, mode, release, deep } : p))
    setRefreshNote(null)
  }, [fov, dets, mode, release, deep])

  // ---- URL sync (shareable links)
  useEffect(() => {
    if (!params) return
    const u = new URLSearchParams({ fov: params.fov.toFixed(3), mode: params.mode, speed: String(speed), ref: refSource })
    if (Number.isFinite(params.ra) && Number.isFinite(params.dec)) {
      u.set('ra', params.ra.toFixed(6))
      u.set('dec', params.dec.toFixed(6))
    }
    if (params.dataset) u.set('dataset', params.dataset)
    else u.set('dets', params.dets.join(','))
    if (query && !params.dataset) u.set('q', query)
    if (params.release !== 'auto') u.set('release', params.release)
    if (!params.deep) u.set('deep', '0')
    if (embed) u.set('embed', '1')
    if (pickSky) u.set('src', `${pickSky[0].toFixed(6)},${pickSky[1].toFixed(6)}`)
    history.replaceState(null, '', `?${u}`)
  }, [params, speed, query, refSource, pickSky, embed])

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
    // Object.is: an upload without a chosen target has NaN coordinates (NaN !== NaN).
    // Another mode or detector set of the same place also keeps the current view on screen (and the
    // reference panel, with Aladin, mounted) until the new frames arrive.
    const samePlace = !!prev && Object.is(prev.ra, params.ra) && Object.is(prev.dec, params.dec) && prev.dataset === params.dataset
      && prev.release === params.release && prev.deep === params.deep
    const modeChanged = samePlace && prev!.mode !== params.mode
    const detsChanged = samePlace && prev!.dets.join() !== params.dets.join()
    const sameView = samePlace
    let refresh = refreshNext.current
    refreshNext.current = false
    prevParams.current = params
    if (!sameView) { setResult(null); setIndex(0) }
    setSwitching(modeChanged ? `Loading the ${params.mode === 'wavelength' ? 'wavelength' : params.mode === 'exposure' ? 'exposure' : 'epoch'} view…`
      : detsChanged ? 'Loading the new detector selection…' : null)
    let first = true
    setPick(null); setPhot(null)
    let failures = 0
    const tick = async () => {
      try {
        const r = await fetchFrames(params, ctrl.signal, refresh)
        if (myPoll !== pollRef.current) return
        if (refresh && r.refresh_wait_s > 0) {
          refreshFrom.current = null
          setRefreshNote(`Checked less than an hour ago. You can check again in ${Math.ceil(r.refresh_wait_s / 60)} min.`)
        }
        refresh = false
        failures = 0
        if (first) {
          first = false
          if (modeChanged) setIndex(0)
          setSwitching(null)
        }
        setResult(r)
        if (r.status === 'done' && refreshFrom.current != null) {
          const added = r.progress.total - refreshFrom.current
          setRefreshNote(added > 0 ? `Found ${added} new exposure${added === 1 ? '' : 's'}.` : `Up to date: no new exposures (${r.progress.total} in total).`)
          refreshFrom.current = null
        }
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
  }, [params, refreshTick])

  // ---- Gaia overlay
  const gRa = params?.dataset ? result?.grid.ra : params?.ra
  const gDec = params?.dataset ? result?.grid.dec : params?.dec
  useEffect(() => {
    if (!params || !showGaia || gRa == null || gDec == null || !Number.isFinite(gRa) || !result?.grid.n && params.dataset) return
    let live = true
    fetchGaia(gRa, gDec, params.fov * 0.75)
      .then((g) => live && setGaia(g.sources))
      .catch(() => live && setGaia(null))
    return () => { live = false }
  }, [params, showGaia, gRa, gDec]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- WISE (CatWISE2020) overlay
  useEffect(() => {
    if (!params || !showWise || gRa == null || gDec == null || !Number.isFinite(gRa)) return
    let live = true
    fetchCatwise(gRa, gDec, params.fov * 0.75)
      .then((c) => live && setCatwise(c.sources))
      .catch(() => live && setCatwise(null))
    return () => { live = false }
  }, [params, showWise, gRa, gDec]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- moving sources, once all exposures are in
  useEffect(() => {
    setMoversRes(null)
    if (!params || !findMovers || result?.status !== 'done') return
    let live = true
    setMoversLoading(true)
    fetchMovers(params)
      .then((r) => live && setMoversRes(r))
      .catch(() => live && setMoversRes(null))
      .finally(() => live && setMoversLoading(false))
    return () => { live = false }
  }, [params, findMovers, result?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- frames actually shown (shallow epochs are noisy and patchy)
  const frames = useMemo(
    () => (result?.frames ?? []).filter((f) => !(skipShallow && f.shallow) && f.data),
    [result, skipShallow],
  )
  // Epochs hidden by "Skip shallow epochs", named under the frame buttons so nothing disappears silently.
  const hiddenShallow = useMemo(
    () => (skipShallow && mode === 'epoch' ? (result?.frames ?? []).filter((f) => f.shallow && f.data) : []),
    [result, skipShallow, mode],
  )
  // Add / Subtract: stacks of chosen frames (pixel-wise mean, ignoring masked pixels).
  const valid = (ix: number[] | null, fallback: number[]) =>
    (ix ?? fallback).filter((i) => i >= 0 && i < frames.length && frames[i].data)
  const addIdx = valid(addSet, frames.map((_, i) => i))
  const plusIdx = valid(plusSet, frames.length ? [frames.length - 1] : [])
  const minusIdx = valid(minusSet, frames.length ? [0] : []).filter((i) => !plusIdx.includes(i))
  const combineActive = (imageType === 'add' && addIdx.length >= 2) || (imageType === 'subtract' && plusIdx.length >= 1 && minusIdx.length >= 1)
  const viewFrames: Frame[] = useMemo(() => {
    if (!combineActive) return frames
    const stack = (ix: number[]) => {
      const n = frames[ix[0]].data!.length
      const sum = new Float32Array(n), cnt = new Uint16Array(n)
      for (const i of ix) {
        const d = frames[i].data!
        for (let k = 0; k < n; k++) if (Number.isFinite(d[k])) { sum[k] += d[k]; cnt[k]++ }
      }
      for (let k = 0; k < n; k++) sum[k] = cnt[k] ? sum[k] / cnt[k] : NaN
      return sum
    }
    const names = (ix: number[]) => ix.map((i) => frames[i].label.replace('Epoch ', 'E')).join(' + ')
    const base = frames[(imageType === 'add' ? addIdx : plusIdx)[0]]
    if (imageType === 'add') {
      return [{ ...base, label: `Added: ${names(addIdx)}`, date: `${addIdx.length} frames stacked`, data: stack(addIdx), meta: undefined, contam: null }]
    }
    const pl = stack(plusIdx), mi = stack(minusIdx)
    const data = pl.map((v, k) => v - mi[k])
    return [{ ...base, label: `Subtracted: (${names(plusIdx)}) − (${names(minusIdx)})`, date: `${frames[plusIdx[0]].date} vs ${frames[minusIdx[0]].date}`, data, meta: undefined, contam: null }]
  }, [combineActive, imageType, frames, addIdx.join(), plusIdx.join(), minusIdx.join()]) // eslint-disable-line react-hooks/exhaustive-deps
  // Memoised: the viewer re-renders every frame bitmap when this object changes.
  const viewStretch: StretchOpts = useMemo(
    () => ({ ...stretch, display: combineActive && imageType === 'subtract' ? 'signed' : imageType === 'difference' ? 'difference' : 'frames' }),
    [stretch, combineActive, imageType],
  )
  useEffect(() => { setIndex((i) => (viewFrames.length ? i % viewFrames.length : 0)) }, [viewFrames.length])

  // ---- blink loop
  const nFrames = viewFrames.length
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
  // While the view is still being built the backend has nothing to measure yet: ask again every
  // few seconds instead of showing an empty result. A newer click cancels the older one.
  const pickSeq = useRef(0)
  const [photNote, setPhotNote] = useState<string | null>(null)
  const onPick = async (x: number, y: number) => {
    if (!params || !result?.grid.n) return
    setPick([x, y])
    setPhot(null)
    setPhotNote(null)
    const seq = ++pickSeq.current
    try {
      for (let attempt = 0; attempt < 200; attempt++) {
        const r = await fetchPhotometry(params, x, y)
        if (seq !== pickSeq.current) return
        if (r.status === 'done' || r.status === 'error') {
          if (r.status === 'error') setPhotNote('The images for this view could not be loaded, so nothing was measured.')
          else { setPhot(r.points); setPhotMode(r.mode); setPhotUnit(r.unit ?? 'mJy') }
          return
        }
        setPhotNote('The images are still loading. The spectrum appears as soon as every exposure is in.')
        await new Promise((res) => setTimeout(res, 3000))
        if (seq !== pickSeq.current) return
      }
    } catch (e) {
      if (seq === pickSeq.current) setError((e as Error).message)
    }
  }

  const grid = result?.grid
  const geom = grid?.n ? { n: grid.n, ra: grid.ra, dec: grid.dec, scaleDeg: grid.pixel_scale_arcsec / 3600 } : null

  // ?src=RA,Dec reopens a clicked source (its spectrum and brightness) once the frames are ready.
  useEffect(() => {
    if (!pendingSrc.current || result?.status !== 'done' || !geom) return
    const p = skyToPixel(geom, pendingSrc.current[0], pendingSrc.current[1])
    pendingSrc.current = null
    if (p && p[0] >= 0 && p[1] >= 0 && p[0] < geom.n && p[1] < geom.n) onPick(p[0], p[1])
  }, [result?.status, geom?.n]) // eslint-disable-line react-hooks/exhaustive-deps
  const hoverSky = geom && hover ? pixelToSky(geom, hover[0], hover[1]) : null
  const frame = viewFrames[index]
  const progress = result?.progress
  // Per detector, so a wait is visible: "D1 waiting · D5 ready · D6 120/300".
  const partsLine = (result?.parts?.length ?? 0) > 1 || result?.parts?.some((p) => p.status !== 'done')
    ? result!.parts!.map((p) => `D${p.detector} ${p.status === 'done' ? 'ready' : p.status === 'queued' ? 'waiting'
      : p.status === 'querying' ? 'searching' : p.status === 'error' ? 'failed' : `${p.done}/${p.total}`}`).join(' · ')
    : null
  // A view is set but its first response hasn't arrived yet also counts as loading.
  const running = !!params && !error && (!result || (result.status !== 'done' && result.status !== 'error'))

  if (embed) {
    const full = new URLSearchParams(location.search)
    full.delete('embed')
    return (
      <div className="embed">
        {grid?.n ? (
          <Viewer grid={grid} frames={viewFrames} index={index} stretch={viewStretch} smooth={smooth}
            gaia={showGaia ? gaia : null} gaiaEpoch={2016.0} pick={null} onPick={() => {}} onHover={setHover} />
        ) : (
          <div className="placeholder embed-placeholder">
            <p>{error ?? (<><span className="spinner" aria-hidden="true" />Loading SPHEREx images…</>)}</p>
          </div>
        )}
        <div className="embed-bar">
          <button onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? '❚❚' : '▶'}</button>
          <span className="embed-frame">{frame ? <><strong>{frame.label}</strong> · {frame.date}</> : 'SPHEREx'}</span>
          <a href={`${location.pathname}?${full}`} target="_blank" rel="noopener">Open in SPHEREx Sky Motion Explorer ↗</a>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <header>
        <div className="title-row">
          <h1>SPHEREx Sky Motion Explorer</h1>
          <span className="team">by Team Barobax</span>
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
              {switching && <div className="switching" role="status"><span className="spinner spinner-sm" aria-hidden="true" />{switching}</div>}
              <Viewer grid={grid} frames={viewFrames} index={index} stretch={viewStretch} smooth={smooth}
                gaia={showGaia ? gaia : null} gaiaEpoch={2016.0} movers={findMovers ? moversRes?.movers : null}
                catwise={showWise ? catwise : null} catwiseEpoch={2015.4} showContam={showContam}
                showTarget={showTarget} showPick={showPick} showFirstMark={showFirstMark} showCompass={showCompass}
                pick={pick} onPick={onPick} onHover={setHover} />
              {/* What the markers on the image mean; entries appear only when that marker is shown. */}
              <div className="viewer-legend" aria-label="Image markers">
                {showTarget && (
                  <span className="vl-item">
                    <svg width="18" height="18" aria-hidden="true"><path d="M9 1V5M9 13V17M1 9H5M13 9H17" stroke="#ffc400" strokeWidth="2" /></svg>
                    Target
                  </span>
                )}
                {showGaia && gaia && (
                  <>
                    <span className="vl-item">
                      <svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="6" fill="none" stroke="rgb(80,200,255)" strokeWidth="1.6" /></svg>
                      Gaia DR3 star, moved to this frame's date
                    </span>
                    <span className="vl-item">
                      <svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="7.5" fill="none" stroke="rgb(255,92,92)" strokeWidth="1.8" /></svg>
                      Fast-moving Gaia star (over 0.2″ per year)
                    </span>
                  </>
                )}
                {showWise && catwise && catwise.length > 0 && (
                  <span className="vl-item">
                    <svg width="18" height="18" aria-hidden="true"><rect x="4" y="4" width="10" height="10" fill="none" stroke="rgb(255,169,77)" strokeWidth="1.8" /></svg>
                    WISE source (CatWISE2020), moved to this frame's date; thick = very red
                  </span>
                )}
                {showContam && (
                  <span className="vl-item">
                    <svg width="18" height="18" aria-hidden="true"><rect x="2" y="2" width="14" height="14" fill="rgba(255,140,0,0.5)" /></svg>
                    Pixels flagged as contaminated (ghost, streak, halo, crosstalk)
                  </span>
                )}
                {findMovers && (moversRes?.movers.length ?? 0) > 0 && (
                  <>
                    <span className="vl-item">
                      <svg width="18" height="18" aria-hidden="true"><path d="M9 2 16 9 9 16 2 9Z" fill="none" stroke="rgb(255,79,216)" strokeWidth="2" /></svg>
                      New moving-source candidate (no Gaia match)
                    </span>
                    <span className="vl-item">
                      <svg width="18" height="18" aria-hidden="true"><path d="M9 3 15 9 9 15 3 9Z" fill="none" stroke="rgb(255,79,216)" strokeWidth="1.6" strokeDasharray="3 3" /></svg>
                      Moving source matched to Gaia
                    </span>
                    {moversRes?.movers.some((m) => m.status === 'doubtful') && (
                      <span className="vl-item">
                        <svg width="18" height="18" aria-hidden="true"><path d="M9 3 15 9 9 15 3 9Z" fill="none" stroke="rgba(255,79,216,0.6)" strokeWidth="1.2" strokeDasharray="1 3" /></svg>
                        Doubtful motion (saturated, or a Gaia star on the track moves differently)
                      </span>
                    )}
                  </>
                )}
                {pick && showPick && (
                  <span className="vl-item">
                    <svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="7" fill="none" stroke="rgb(0,255,140)" strokeWidth="1.8" /></svg>
                    Source you clicked (photometry aperture)
                  </span>
                )}
              </div>
              <div className="frame-info">
                <strong>{frame?.label}</strong> · {frame?.date}
                {frame?.wavelength && ` · ${frame.wavelength[0].toFixed(2)}${frame.wavelength[1] !== frame.wavelength[0] ? `–${frame.wavelength[1].toFixed(2)}` : ''} µm`}
                {mode === 'epoch' && frame && !combineActive && ` · ${frame.nUsed} exposures`}
                {mode === 'wavelength' && frame?.channel && !combineActive && ` · ${frame.nUsed} exposures from ${frame.channel.nEpochs} epochs`}
                {frame && Object.keys(frame.flagCounts ?? {}).length > 0 && (
                  <span className="warn"> · Contamination at target: {Object.entries(frame.flagCounts!).map(([f, n], i) => (
                    <span key={f} title={CONTAMINATION_FLAGS[f] ?? f} className="flag-name">{i ? ', ' : ''}{f}{mode !== 'exposure' ? ` in ${n} of ${frame.nUsed}` : ''}</span>
                  ))}</span>
                )}
                {frame?.meta?.target_flags.length ? <span className="muted"> · flags: {frame.meta.target_flags.join(', ')}</span> : null}
              </div>
              <div className="cursor muted">
                {hoverSky ? `${formatRA(hoverSky[0])}  ${formatDec(hoverSky[1])}  (${hoverSky[0].toFixed(5)}, ${hoverSky[1].toFixed(5)})` : 'Hover for coordinates · click a source for its spectrum'}
              </div>
          {running && progress && progress.total > 0 && (
                <div className="progress" aria-live="polite">
                  <div className="bar"><div style={{ width: `${(100 * progress.done) / progress.total}%` }} /></div>
                  <span>Loading exposures {progress.done}/{progress.total}{progress.failed ? ` (${progress.failed} failed)` : ''}. Frames sharpen as data arrives.</span>
                  {partsLine && <span className="muted small">{partsLine}</span>}
                </div>
              )}

              {frames.length > 0 && !combineActive && (
                <div className="strip">
                  {frames.map((f, i) => (
                    <button key={i} className={`thumb ${i === index ? 'active' : ''}`} onClick={() => { setPlaying(false); setIndex(i) }}>
                      <span>{mode === 'epoch' ? f.label.replace('Epoch ', 'E') : mode === 'wavelength' ? f.label.replace(' µm', '') : i + 1}</span>
                      <small>{mode === 'wavelength' && f.channel ? (f.channel.detector ? `D${f.channel.detector}·${f.channel.channel}` : `${f.nUsed} file${f.nUsed === 1 ? '' : 's'}`) : f.date.slice(0, 10)}</small>
                    </button>
                  ))}
                </div>
              )}
              {hiddenShallow.length > 0 && !combineActive && (
                <p className="muted small hidden-note">
                  {hiddenShallow.map((f) => f.label.replace('Epoch ', 'E')).join(', ')} hidden: too few exposures
                  ({hiddenShallow.map((f) => f.nUsed).join(' and ')}).{' '}
                  <button className="link" onClick={() => setSkipShallow(false)}>Show {hiddenShallow.length === 1 ? 'it' : 'them'}</button>
                </p>
              )}
              </div>
              <div className="pane">
                <Reference grid={grid} source={refSource} onSource={setRefSource}
                  frames={frames.map((f) => f.data)} stretch={stretch} gaia={gaia} />
              </div>
            </div>
          ) : (
            <div className="placeholder">
              {running || busy ? (
                <p>
                  <span className="spinner" aria-hidden="true" />{' '}
                  {busy ? 'Looking up the target…'
                    : result?.status === 'queued' ? 'Waiting for a free processing slot (other views are being processed)…'
                    : result?.status === 'querying' ? 'Searching the SPHEREx archive…'
                    : result?.status === 'fetching' ? 'Loading the first exposures…'
                    : 'Starting…'}
                  <br /><small className="muted">A new position takes about 10–30 seconds per detector; after that it is cached.</small>
                  {partsLine && <><br /><small className="muted">{partsLine}</small></>}
                </p>
              ) : (
                <p>Pick a target above or enter coordinates.</p>
              )}
            </div>
          )}
          {findMovers && params && result?.status === 'done' && mode === 'epoch' && (
            <MoversPanel result={moversRes} loading={moversLoading} onSelect={(m: Mover) => {
              if (!geom) return
              const f = frames[index]
              const dt = (f?.decimalYear ?? m.year_mid) - m.year_mid
              const ra = m.ra + (m.pmra_mas_yr * dt) / 3.6e6 / Math.max(Math.cos((m.dec * Math.PI) / 180), 1e-6)
              const dec = m.dec + (m.pmdec_mas_yr * dt) / 3.6e6
              const p = skyToPixel(geom, ra, dec)
              if (p) onPick(p[0], p[1])
            }} />
          )}

          {pick && (
            <section className="photometry">
              <h2>Source at {geom ? (() => { const s = pixelToSky(geom, pick[0], pick[1]); return `${formatRA(s[0])} ${formatDec(s[1])}` })() : ''}</h2>
              {phot ? <PhotometryCharts points={phot} mode={photMode} unit={photUnit}
                hiddenEpochs={skipShallow && mode === 'epoch' ? (result?.frames ?? []).flatMap((f, i) => (f.shallow ? [i] : [])) : []}
                epochDates={result?.epochs.map((e) => e.date_mid) ?? []} />
                : <p className="muted">{photNote ? <><span className="spinner spinner-sm" aria-hidden="true" /> {photNote}</> : 'Measuring…'}</p>}
            </section>
          )}

          {result?.status === 'done' && result.frames.length === 0 && (
            <p className="muted">No SPHEREx images cover this position yet in the selected detectors.</p>
          )}
        </section>

        <aside className="controls">
          {params?.dataset && (
            <div className="target">
              <div className="target-name">Your uploaded dataset</div>
              {result?.dataset?.files_used != null ? (
                <>
                  <div className="small">
                    {result.dataset.files_used} file{result.dataset.files_used === 1 ? '' : 's'} used
                    {result.dataset.pixel_scale_arcsec != null && ` · ${result.dataset.pixel_scale_arcsec.toFixed(2)}″ pixels`}
                    {result.dataset.units && ` · units: ${result.dataset.units}`}
                  </div>
                  {(result.dataset.rejected?.length ?? 0) > 0 && (
                    <details className="small"><summary className="warn">{result.dataset.rejected.length} file{result.dataset.rejected.length === 1 ? '' : 's'} skipped</summary>
                      <ul className="rejected">{result.dataset.rejected.map((r) => <li key={r.name}><span className="mono">{r.name}</span>: {r.reason}</li>)}</ul>
                    </details>
                  )}
                </>
              ) : <div className="muted small">{result?.status === 'error' ? 'Could not use these files.' : 'Checking your files…'}</div>}
              {grid?.n ? <div className="mono small">{formatRA(grid.ra)} {formatDec(grid.dec)}</div> : null}
              <div className="muted small">Private link, deleted automatically after 7 days.</div>
            </div>
          )}
          {resolved && params && !params.dataset && (
            <div className="target">
              <div className="target-name">{resolved.name ?? 'Coordinates'}{resolved.otype ? <span className="muted"> · {resolved.otype}</span> : null}</div>
              <div className="mono">{formatRA(params.ra)} {formatDec(params.dec)}</div>
              <div className="muted small">{resolved.note}</div>
            </div>
          )}

          <fieldset>
            <legend>Playback</legend>
            <div className="row">
              <button onClick={() => setPlaying((p) => !p)}>{playing ? 'Pause' : 'Play'}</button>
              <button onClick={() => { setPlaying(false); setIndex((i) => (i - 1 + nFrames) % Math.max(1, nFrames)) }} aria-label="Previous frame">◀</button>
              <button onClick={() => { setPlaying(false); setIndex((i) => (i + 1) % Math.max(1, nFrames)) }} aria-label="Next frame">▶</button>
            </div>
            <button className="download-btn export-btn" disabled={!grid?.n || !frame} onClick={async () => {
              setPlaying(false)
              const name = params?.dataset ? 'Uploaded dataset' : resolved?.name ?? 'Sky position'
              const where = grid?.n ? `${formatRA(grid.ra)} ${formatDec(grid.dec)}` : ''
              try {
                await exportSideBySide({
                  title: `${name} · ${where} · field ${(grid!.n * grid!.pixel_scale_arcsec / 60).toFixed(1)}′`,
                  left: `SPHEREx ${frame!.label} · ${frame!.date}${frame!.wavelength ? ` · ${frame!.wavelength[0].toFixed(2)}–${frame!.wavelength[1].toFixed(2)} µm` : ''}`,
                  right: `Reference: ${REF_SOURCES.find((r) => r.id === refSource)?.label ?? refSource}`,
                  detail: 'SPHEREx Sky Motion Explorer by Team Barobax · spherex.peykan.ai · Data: NASA/IPAC IRSA, ESA Gaia DR3',
                }, `spherex_${name.replace(/[^A-Za-z0-9]+/g, '_')}_${frame!.label.replace(/[^A-Za-z0-9]+/g, '_')}.png`)
              } catch (e) {
                setError(`Export failed: ${(e as Error).message}`)
              }
            }}>
              <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1v9M4 6.5 8 10.5 12 6.5M2 13.5h12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
              Export image (both panels)
            </button>
            <label>Blink interval {speed} ms
              <input type="range" min={100} max={2000} step={50} value={speed} onChange={(e) => setSpeed(+e.target.value)} />
            </label>
          </fieldset>

          <fieldset>
            <legend>View</legend>
            <div className="seg" role="group" aria-label="Frames to animate">
              <button aria-pressed={mode === 'epoch'} className={mode === 'epoch' ? 'on' : ''} onClick={() => setMode('epoch')}>Epochs</button>
              <button aria-pressed={mode === 'exposure'} className={mode === 'exposure' ? 'on' : ''} onClick={() => setMode('exposure')}>Exposures</button>
              <button aria-pressed={mode === 'wavelength'} className={mode === 'wavelength' ? 'on' : ''} onClick={() => setMode('wavelength')}>Wavelength</button>
            </div>
            <p className="muted small seg-hint">
              {params?.dataset
                ? (mode === 'wavelength' ? 'One stack per wavelength or filter found in your files.' : 'Each uploaded file is one frame, in time order.')
                : mode === 'epoch'
                ? 'Each epoch is one SPHEREx visit (about every 6 months): exposures less than 20 days apart, stacked and weighted by noise. Visits longer than 30 days are split.'
                : mode === 'exposure'
                  ? 'Every exposure in time order: best for asteroids and comets.'
                  : 'Steps through the spectrum: one stack per SPHEREx channel (17 per detector, 102 in all), all epochs combined. Sources with strong features fade in and out.'}
            </p>
            <div className="seg" role="group" aria-label="Image type">
              <button aria-pressed={imageType === 'frames'} className={imageType === 'frames' ? 'on' : ''} onClick={() => setImageType('frames')}>Images</button>
              <button aria-pressed={imageType === 'difference'} className={imageType === 'difference' ? 'on' : ''} onClick={() => setImageType('difference')}>Difference</button>
              <button aria-pressed={imageType === 'add'} className={imageType === 'add' ? 'on' : ''} onClick={() => setImageType('add')}>Add</button>
              <button aria-pressed={imageType === 'subtract'} className={imageType === 'subtract' ? 'on' : ''} onClick={() => setImageType('subtract')}>Subtract</button>
            </div>
            {imageType === 'add' && frames.length >= 2 && (
              <div className="combine">
                <span className="combine-label">Frames to add</span>
                <div className="chips">
                  {frames.map((f, i) => (
                    <button key={i} aria-pressed={addIdx.includes(i)} className={`epoch-chip ${addIdx.includes(i) ? 'on' : ''}`}
                      onClick={() => setAddSet(addIdx.includes(i) ? addIdx.filter((x) => x !== i) : [...addIdx, i].sort((x, y) => x - y))}>
                      {f.label.replace('Epoch ', 'E')}<small>{f.date.slice(0, 10)}</small>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {imageType === 'subtract' && frames.length >= 2 && (
              <div className="combine">
                <span className="combine-label">From</span>
                <div className="chips">
                  {frames.map((f, i) => (
                    <button key={i} aria-pressed={plusIdx.includes(i)} className={`epoch-chip ${plusIdx.includes(i) ? 'on' : ''}`}
                      onClick={() => {
                        setPlusSet(plusIdx.includes(i) ? plusIdx.filter((x) => x !== i) : [...plusIdx, i].sort((x, y) => x - y))
                        setMinusSet(minusIdx.filter((x) => x !== i))
                      }}>
                      {f.label.replace('Epoch ', 'E')}<small>{f.date.slice(0, 10)}</small>
                    </button>
                  ))}
                </div>
                <span className="combine-label">Subtract</span>
                <div className="chips">
                  {frames.map((f, i) => (
                    <button key={i} aria-pressed={minusIdx.includes(i)} className={`epoch-chip minus ${minusIdx.includes(i) ? 'on' : ''}`}
                      onClick={() => {
                        setMinusSet(minusIdx.includes(i) ? minusIdx.filter((x) => x !== i) : [...minusIdx, i].sort((x, y) => x - y))
                        setPlusSet(plusIdx.filter((x) => x !== i))
                      }}>
                      {f.label.replace('Epoch ', 'E')}<small>{f.date.slice(0, 10)}</small>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <p className="muted small seg-hint">
              {imageType === 'frames'
                ? 'The images as observed.'
                : imageType === 'difference'
                  ? 'Each frame minus the average: still stars vanish, movers show as dark/light pairs.'
                  : frames.length < 2
                    ? 'Needs at least two frames.'
                    : imageType === 'add'
                      ? (addIdx.length < 2 ? 'Choose at least two frames to add.' : 'The chosen frames stacked: a deeper image, so fainter sources show. A moving source appears once per frame.')
                      : !combineActive
                        ? 'Choose at least one frame on each side.'
                        : mode === 'wavelength'
                          ? 'Stack of the first set minus the second: a colour image. Sources brighter in the first set stay light.'
                          : 'Stack of the first set minus the second: what changed between those times. A mover shows as a light/dark pair.'}
            </p>
          </fieldset>

          <fieldset>
            <legend>Field of view</legend>
            <label>{(fovDraft * 60).toFixed(1)}′
              <input type="range" min={0.02} max={0.25} step={0.01} value={fovDraft} onChange={(e) => setFovDraft(+e.target.value)} />
            </label>
          </fieldset>

          {!params?.dataset && <fieldset>
            <legend>Wavelength (detectors)</legend>
            <div className="dets">
              {DETECTORS.map(({ d, range }) => (
                <label key={d} className="check" title={range}>
                  <input type="checkbox" checked={detsDraft.includes(d)}
                    onChange={(e) => setDetsDraft((cur) => {
                      const next = e.target.checked ? [...cur, d] : cur.filter((x) => x !== d)
                      return next.length ? next.sort() : cur
                    })} />
                  D{d} <small className="muted">{range}</small>
                </label>
              ))}
            </div>
            <p className="muted small">Brown dwarfs are brightest in D5–D6. Fewer detectors load faster.</p>
          </fieldset>}

          <fieldset>
            <legend>Overlays</legend>
            <label className="check"><input type="checkbox" checked={showTarget} onChange={(e) => setShowTarget(e.target.checked)} /> Target crosshair <small className="muted">(yellow)</small></label>
            <label className="check"><input type="checkbox" checked={showGaia} onChange={(e) => setShowGaia(e.target.checked)} /> Gaia DR3 stars <small className="muted">(blue circles, fast in red)</small></label>
            <label className="check"><input type="checkbox" checked={showWise} onChange={(e) => setShowWise(e.target.checked)} /> WISE sources <small className="muted">(CatWISE2020, orange squares)</small></label>
            <label className="check"><input type="checkbox" checked={findMovers} onChange={(e) => setFindMovers(e.target.checked)} /> Moving sources <small className="muted">(magenta diamonds and tracks)</small></label>
            <label className="check"><input type="checkbox" checked={showContam} onChange={(e) => setShowContam(e.target.checked)} /> Contamination map <small className="muted">(flagged pixels)</small></label>
            <label className="check"><input type="checkbox" checked={showPick} onChange={(e) => setShowPick(e.target.checked)} /> Clicked source <small className="muted">(green circle)</small></label>
            <label className="check"><input type="checkbox" checked={showFirstMark} onChange={(e) => setShowFirstMark(e.target.checked)} /> First-frame border <small className="muted">(dashed yellow)</small></label>
            <label className="check"><input type="checkbox" checked={showCompass} onChange={(e) => setShowCompass(e.target.checked)} /> Compass and scale bar</label>
          </fieldset>

          <fieldset>
            <legend>Data</legend>
            {params?.dataset ? (
              <>
                <p className="small data-note">Showing your uploaded dataset.</p>
                <button onClick={() => {
                  setParams(archiveParams.current)
                  if (archiveParams.current) { setFov(archiveParams.current.fov); setFovDraft(archiveParams.current.fov) }
                }}>{archiveParams.current ? 'Back to the SPHEREx archive' : 'Clear dataset'}</button>
              </>
            ) : (<>
            <label>Data release
              <select value={release} onChange={(e) => setRelease(e.target.value)}>
                <option value="auto">Newest{releases?.auto.length ? ` (${releases.auto.map((r) => r.toUpperCase()).join(' + ')})` : ''}</option>
                {(releases?.available ?? []).map((r) => <option key={r} value={r}>{r.toUpperCase()}</option>)}
              </select>
            </label>
            <label className="check"><input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} /> Include Deep Fields <small className="muted">(extra visits near the ecliptic poles)</small></label>
            <button disabled={!params || !result || result.status !== 'done'} onClick={() => {
              refreshNext.current = true
              refreshFrom.current = result?.progress.total ?? null
              setRefreshNote('Searching the archive for new exposures…')
              setRefreshTick((t) => t + 1)
            }}>Check for new data</button>
            <p className="muted small" aria-live="polite">
              {refreshNote ?? 'New releases appear in the list automatically. "Newest" uses the latest data and quick release.'}
            </p>
            </>)}
            <div className="upload-entry">
              {!showUpload ? (
                <button className="upload-open" onClick={() => setShowUpload(true)}>Upload your own FITS images</button>
              ) : (
                <UploadPanel onClose={() => setShowUpload(false)} onDone={(id, t) => {
                  if (!params?.dataset) archiveParams.current = params
                  setShowUpload(false)
                  setResolved(null)
                  setQuery('')
                  setFov(0.25)
                  setFovDraft(0.25)
                  setMode('epoch')
                  setParams({ ra: t ? t[0] : NaN, dec: t ? t[1] : NaN, fov: 0.25, dets, mode: 'epoch', release, deep, dataset: id })
                }} />
              )}
            </div>
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
          </fieldset>
        </aside>
      </main>

      {params && result && result.frames.length > 0 && (
        <ExposureTable params={params} ready={result.status === 'done'}
          currentKey={mode === 'exposure' && frame?.meta ? `${frame.meta.obsid}D${frame.meta.detector}` : undefined} />
      )}

      <footer className="muted small">
        Data: NASA SPHEREx via NASA/IPAC Infrared Science Archive (IRSA); ESA Gaia DR3; CDS Sesame.
        Keys: Space play/pause, ← → step frames.
      </footer>
    </div>
  )
}
