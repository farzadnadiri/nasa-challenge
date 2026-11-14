// Small astrometry helpers: gnomonic projection matching the backend grid, proper motion,
// and coordinate formatting.

const D2R = Math.PI / 180

export interface GridGeom {
  n: number
  ra: number
  dec: number
  scaleDeg: number
}

/** Sky -> grid pixel (0-based, FITS orientation: y increases to the north). */
export function skyToPixel(g: GridGeom, ra: number, dec: number): [number, number] | null {
  const a = ra * D2R, d = dec * D2R, a0 = g.ra * D2R, d0 = g.dec * D2R
  const cosc = Math.sin(d) * Math.sin(d0) + Math.cos(d) * Math.cos(d0) * Math.cos(a - a0)
  if (cosc <= 0) return null
  const xi = (Math.cos(d) * Math.sin(a - a0)) / cosc / D2R
  const eta = (Math.sin(d) * Math.cos(d0) - Math.cos(d) * Math.sin(d0) * Math.cos(a - a0)) / cosc / D2R
  const c = (g.n - 1) / 2
  return [c - xi / g.scaleDeg, c + eta / g.scaleDeg]
}

/** Grid pixel -> sky (inverse gnomonic). */
export function pixelToSky(g: GridGeom, x: number, y: number): [number, number] {
  const c = (g.n - 1) / 2
  const xi = -(x - c) * g.scaleDeg * D2R
  const eta = (y - c) * g.scaleDeg * D2R
  const d0 = g.dec * D2R
  const rho = Math.hypot(xi, eta)
  if (rho === 0) return [g.ra, g.dec]
  const cc = Math.atan(rho)
  const dec = Math.asin(Math.cos(cc) * Math.sin(d0) + (eta * Math.sin(cc) * Math.cos(d0)) / rho)
  const ra = g.ra * D2R + Math.atan2(xi * Math.sin(cc), rho * Math.cos(d0) * Math.cos(cc) - eta * Math.sin(d0) * Math.sin(cc))
  return [((ra / D2R) % 360 + 360) % 360, dec / D2R]
}

/** Linear proper-motion propagation; pmra includes cos(dec). mas/yr in, degrees out. */
export function propagate(ra: number, dec: number, pmra: number, pmdec: number, years: number): [number, number] {
  return [ra + (pmra * years) / 3.6e6 / Math.max(Math.cos(dec * D2R), 1e-6), dec + (pmdec * years) / 3.6e6]
}

function sexa(v: number, sign: boolean, digits: number): string {
  const s = v < 0 ? '-' : sign ? '+' : ''
  v = Math.abs(v)
  let h = Math.floor(v)
  let m = Math.floor((v - h) * 60)
  let sec = ((v - h) * 60 - m) * 60
  if (Number(sec.toFixed(digits)) >= 60) { sec = 0; m += 1 }
  if (m >= 60) { m = 0; h += 1 }
  return `${s}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${sec.toFixed(digits).padStart(3 + digits, '0')}`
}

export const formatRA = (ra: number) => sexa(ra / 15, false, 2)
export const formatDec = (dec: number) => sexa(dec, true, 1)
