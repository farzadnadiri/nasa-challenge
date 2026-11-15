import { useEffect, useRef, useState } from 'react'
import type { GaiaSource, Grid } from '../api'
import { propagate } from '../sky'

// Interactive sky view with CDS Aladin Lite (loaded on demand: the library is ~2.5 MB).
// Imagery comes from IRSA's HiPS mirrors and the CatWISE catalogue from the CfA VizieR mirror,
// so this panel keeps working when CDS's own servers are unavailable.
// IRSA's SPHEREx all-sky cube is not offered: Aladin picks cube layers by frequency on an evenly
// spaced axis, while that cube's layers are index-based, so every request lands on a blank layer.
const IRSA_HIPS = 'https://irsa.ipac.caltech.edu/data/hips/'
export const ALADIN_LAYERS = [
  { id: 'allwise', name: 'AllWISE colour (W1 W2 W4)', url: `${IRSA_HIPS}CDS/AllWISE/RGB-W4-W2-W1` },
  { id: 'w2', name: 'AllWISE W2 (4.6 µm)', url: `${IRSA_HIPS}CDS/AllWISE/W2` },
  { id: '2mass', name: '2MASS colour (J H K)', url: `${IRSA_HIPS}CDS/2MASS/Color` },
  { id: 'dss', name: 'DSS colour (optical)', url: `${IRSA_HIPS}CDS/DSS2/color` },
] as const
export type AladinLayer = (typeof ALADIN_LAYERS)[number]['id']

const CATWISE_URL = (ra: number, dec: number, radiusArcmin: number) =>
  'https://vizier.cfa.harvard.edu/viz-bin/votable?' + new URLSearchParams({
    '-source': 'II/365/catwise', '-c': `${ra.toFixed(6)} ${dec >= 0 ? '+' : ''}${dec.toFixed(6)}`,
    '-c.rs': (radiusArcmin * 60).toFixed(0), '-out.max': '3000',
    '-out': 'Name,RA_ICRS,DE_ICRS,pmRA,pmDE,W1mproPM,W2mproPM',
  })

interface Props {
  grid: Grid
  layer: AladinLayer
  gaia: GaiaSource[] | null
  showGaia: boolean
  showCatwise: boolean
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export function AladinPanel({ grid, layer, gaia, showGaia, showCatwise }: Props) {
  const divRef = useRef<HTMLDivElement>(null)
  const aladinRef = useRef<any>(null)
  const libRef = useRef<any>(null)
  const overlays = useRef<{ target?: any; gaia?: any; catwise?: any }>({})
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fovDeg = (grid.n * grid.pixel_scale_arcsec) / 3600

  // Create the viewer once.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const A = (await import('aladin-lite')).default
        await A.init
        // Aladin sizes its canvas at creation: wait until the square has been laid out.
        while (!cancelled && divRef.current && divRef.current.clientWidth < 50) await new Promise((r) => requestAnimationFrame(r))
        if (cancelled || !divRef.current) return
        libRef.current = A
        aladinRef.current = A.aladin(divRef.current, {
          survey: ALADIN_LAYERS.find((l) => l.id === layer)!.url,
          hipsList: ALADIN_LAYERS.map((l) => ({ id: l.url, name: l.name, url: l.url })),
          target: `${grid.ra} ${grid.dec}`,
          fov: fovDeg,
          projection: 'TAN',
          cooFrame: 'ICRSd',
          showReticle: false,
          showCooGridControl: true,
          showLayersControl: true,
          showFullscreenControl: true,
          showZoomControl: true,
          showSimbadPointerControl: false, // needs CDS SIMBAD
          showGotoControl: false,
          showShareControl: false,
          showProjectionControl: false,
          showFrame: false,
          showStatusBar: false,
        })
        setReady(true)
      } catch (e) {
        if (!cancelled) setError(`Aladin Lite could not start: ${(e as Error).message}`)
      }
    })()
    return () => {
      cancelled = true
      // Aladin has no destroy(); clearing the container releases its canvases.
      if (divRef.current) divRef.current.innerHTML = ''
      aladinRef.current = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Follow the target and field of view; mark the target.
  useEffect(() => {
    const aladin = aladinRef.current, A = libRef.current
    if (!ready || !aladin) return
    aladin.gotoRaDec(grid.ra, grid.dec)
    aladin.setFoV(fovDeg)
    if (!overlays.current.target) {
      overlays.current.target = A.catalog({ name: 'Target', shape: 'cross', color: '#ffc400', sourceSize: 22 })
      aladin.addCatalog(overlays.current.target)
    }
    overlays.current.target.removeAll()
    overlays.current.target.addSources([A.source(grid.ra, grid.dec, { name: 'Target' })])
  }, [ready, grid.ra, grid.dec, fovDeg])

  // Switch survey only when it really changes: Aladin was created with the initial one, and
  // re-adding a layer while it is still loading crashes its WebAssembly core.
  const shownLayer = useRef(layer)
  useEffect(() => {
    const aladin = aladinRef.current
    if (!ready || !aladin || shownLayer.current === layer) return
    shownLayer.current = layer
    try {
      aladin.setImageSurvey(ALADIN_LAYERS.find((l) => l.id === layer)!.url)
    } catch (e) {
      setError(`Could not switch survey: ${(e as Error).message}`)
    }
  }, [ready, layer])


  // Gaia DR3 from our API, carried to 2026.0 to match the SPHEREx data.
  useEffect(() => {
    const aladin = aladinRef.current, A = libRef.current
    if (!ready || !aladin) return
    if (!overlays.current.gaia) {
      overlays.current.gaia = A.catalog({ name: 'Gaia DR3 (at 2026.0)', color: '#50c8ff', sourceSize: 9, shape: 'circle' })
      aladin.addCatalog(overlays.current.gaia)
    }
    const cat = overlays.current.gaia
    cat.removeAll()
    if (showGaia && gaia) {
      cat.addSources(gaia.map((s) => {
        const [ra, dec] = propagate(s.ra, s.dec, s.pmra ?? 0, s.pmdec ?? 0, 2026.0 - 2016.0)
        return A.source(ra, dec, {
          source_id: s.source_id, G: s.phot_g_mean_mag?.toFixed(2),
          pmra: s.pmra?.toFixed(1), pmdec: s.pmdec?.toFixed(1), parallax: s.parallax?.toFixed(2),
        })
      }))
    }
    showGaia ? cat.show() : cat.hide()
  }, [ready, gaia, showGaia])

  // CatWISE2020 (WISE catalogue with its own proper motions), straight from the VizieR mirror.
  useEffect(() => {
    const aladin = aladinRef.current, A = libRef.current
    if (!ready || !aladin) return
    if (overlays.current.catwise) {
      aladin.removeLayer?.(overlays.current.catwise)
      overlays.current.catwise = undefined
    }
    if (!showCatwise) return
    const cat = A.catalogFromURL(CATWISE_URL(grid.ra, grid.dec, (fovDeg * 60) * 0.75), {
      name: 'CatWISE2020', color: '#ff7ad9', sourceSize: 10, shape: 'square',
    })
    overlays.current.catwise = cat
    aladin.addCatalog(cat)
  }, [ready, showCatwise, grid.ra, grid.dec, fovDeg])

  return (
    <div className="viewer-wrap ref-wrap aladin-wrap">
      <div ref={divRef} className="aladin-div" />
      {!ready && !error && <div className="ref-status"><p><span className="spinner" aria-hidden="true" />Loading Aladin Lite…</p></div>}
      {error && <div className="ref-status">{error}</div>}
    </div>
  )
}
