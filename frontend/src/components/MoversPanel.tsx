import type { Mover, MoversResult } from '../api'
import { formatDec, formatRA } from '../sky'

// Moving sources found across the epochs. "Candidate" = moves, but no Gaia star moves with it:
// the signature of a discovery (e.g. a brown dwarf too red for Gaia).
interface Props {
  result: MoversResult | null
  loading: boolean
  onSelect: (m: Mover) => void
}

function direction(m: Mover): string {
  // position angle, east of north
  const pa = (Math.atan2(m.pmra_mas_yr, m.pmdec_mas_yr) * 180) / Math.PI
  const deg = (pa + 360) % 360
  const names = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
  return `${names[Math.round(deg / 45) % 8]} (${deg.toFixed(0)}°)`
}

export function MoversPanel({ result, loading, onSelect }: Props) {
  const movers = result?.movers ?? []
  const candidates = movers.filter((m) => m.status === 'candidate')
  return (
    <section className="movers-section" aria-label="Moving sources">
      <div className="movers-head">
        <h2>Moving sources</h2>
        <span className="muted small">
          {loading ? 'Searching the epochs for moving sources…'
            : !result ? ''
            : result.note ? `Not searched: ${result.note}.`
            : `${movers.length} found across ${result.n_epochs} epochs: ${candidates.length} new candidate${candidates.length === 1 ? '' : 's'} (no Gaia match), ${movers.length - candidates.length} known.`}
        </span>
      </div>
      <p className="muted small movers-how">
        Each source is located in every deep epoch, linked across epochs and fitted with a straight-line motion.
        It is listed when the motion is at least 5σ and 0.3″ per year over 3 or more epochs. Candidates have no Gaia DR3 star
        moving with them; very red objects such as brown dwarfs are often missing from Gaia.
        {result && !result.gaia_checked && ' Gaia could not be reached, so every mover is listed as a candidate.'}
      </p>
      {movers.length > 0 && (
        <div className="meta-scroll">
          <table className="meta-table movers-table">
            <thead>
              <tr>
                <th>Status</th><th>Position (RA Dec)</th><th className="num">Motion</th><th>Direction</th>
                <th className="num">Significance</th><th className="num">Epochs</th><th className="num">S/N</th><th>Gaia DR3</th>
              </tr>
            </thead>
            <tbody>
              {movers.map((m, i) => (
                <tr key={i} className="clickable" onClick={() => onSelect(m)} title="Show this source's spectrum and brightness">
                  <td>{m.status === 'candidate' ? <span className="tag tag-cand">candidate</span> : <span className="tag">known</span>}</td>
                  <td className="mono">{formatRA(m.ra)} {formatDec(m.dec)}</td>
                  <td className="num">{m.pm_arcsec_yr.toFixed(2)}″/yr</td>
                  <td>{direction(m)}</td>
                  <td className="num">{m.significance.toFixed(1)}σ</td>
                  <td className="num">{m.n_epochs}</td>
                  <td className="num">{m.snr >= 1000 ? '>1000' : m.snr.toFixed(0)}{m.saturated ? ' (sat.)' : ''}</td>
                  <td>{m.gaia ? (m.gaia.name ?? `Gaia DR3 ${m.gaia.source_id}`) : <span className="muted">none</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
