import { useEffect, useState } from 'react'
import { fetchExposures, type ExposureRow, type ViewParams } from '../api'

// Per-exposure metadata (date, detector, wavelength, release, flags at the target), fetched only
// when the section is opened. The exposure currently on screen in Exposures mode is highlighted.
interface Props {
  params: ViewParams
  ready: boolean            // the job has finished
  currentKey?: string       // obsid + detector of the frame on screen
}

const key = (r: ExposureRow) => `${r.obsid}D${r.detector}`

function downloadCSV(rows: ExposureRow[]) {
  const head = 'obsid,detector,date_obs_utc,mjd,epoch,wavelength_um,bandwidth_um,release,pipeline_version,psf_fwhm_arcsec,target_flags,contaminated,saturated'
  const body = rows.map((r) => [r.obsid, r.detector, r.date_obs, r.mjd.toFixed(5), r.epoch + 1,
    r.wavelength_um?.toFixed(4) ?? '', r.bandwidth_um?.toFixed(4) ?? '', r.release, r.version,
    r.psf_fwhm_arcsec != null && Number.isFinite(r.psf_fwhm_arcsec) ? r.psf_fwhm_arcsec.toFixed(2) : '',
    `"${r.target_flags.join(' ')}"`, r.contaminated, r.saturated].join(','))
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([[head, ...body].join('\n')], { type: 'text/csv' }))
  a.download = 'spherex_exposures.csv'
  a.click()
}

export function ExposureTable({ params, ready, currentKey }: Props) {
  const [open, setOpen] = useState(true)
  const [rows, setRows] = useState<ExposureRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const viewKey = `${params.ra},${params.dec},${params.fov},${params.dets.join()}`

  useEffect(() => { setRows(null); setError(null) }, [viewKey])
  useEffect(() => {
    if (!open || !ready || rows) return
    let live = true
    fetchExposures(params).then((r) => live && setRows(r.exposures)).catch((e) => live && setError((e as Error).message))
    return () => { live = false }
  }, [open, ready, rows, params])

  const flagged = rows?.filter((r) => r.contaminated).length ?? 0
  const saturated = rows?.filter((r) => r.saturated).length ?? 0

  return (
    <details className="meta-section" open onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <span className="meta-title">Exposure metadata</span>
        <span className="muted small">
          {rows ? `${rows.length} exposures · ${flagged} contaminated · ${saturated} saturated at the target` : 'date, detector, wavelength, release and flags for every exposure'}
        </span>
      </summary>
      {!ready && <p className="muted small">Available once all exposures have loaded.</p>}
      {error && <p className="error">{error}</p>}
      {ready && !rows && !error && open && <p className="muted small" style={{ textAlign: "center" }}><span className="spinner" aria-hidden="true" />Loading…</p>}
      {rows && (
        <>
          <div className="meta-actions">
            <button className="download-btn" onClick={() => downloadCSV(rows)}>
              <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1v9M4 6.5 8 10.5 12 6.5M2 13.5h12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
              Download CSV
            </button>
          </div>
          <div className="meta-scroll">
            <table className="meta-table">
              <thead>
                <tr>
                  <th>Observation</th><th>Det.</th><th>Date (UTC)</th><th>Epoch</th>
                  <th className="num">λ (µm)</th><th className="num">PSF (″)</th><th>Release</th><th>Flags at target</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={key(r)} className={key(r) === currentKey ? 'current' : ''}>
                    <td className="mono">{r.obsid}</td>
                    <td>{r.detector ? `D${r.detector}` : '–'}</td>
                    <td className="mono">{r.date_obs.slice(0, 19).replace('T', ' ')}</td>
                    <td>E{r.epoch + 1}</td>
                    <td className="num">{r.wavelength_um?.toFixed(3) ?? ''}</td>
                    <td className="num">{r.psf_fwhm_arcsec != null && Number.isFinite(r.psf_fwhm_arcsec) ? r.psf_fwhm_arcsec.toFixed(1) : ''}</td>
                    <td>{r.release === 'upload' ? 'Upload' : r.release.toUpperCase()}{r.version ? <span className="muted"> v{r.version}</span> : null}</td>
                    <td>
                      {r.contaminated && <span className="tag tag-warn">contaminated</span>}
                      {r.saturated && <span className="tag">saturated</span>}
                      <span className="muted small">{r.target_flags.filter((f) => f !== 'SOURCE').join(', ')}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </details>
  )
}
