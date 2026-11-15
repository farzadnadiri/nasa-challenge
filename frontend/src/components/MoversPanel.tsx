import type { Mover, MoversResult } from '../api'
import { formatDec, formatRA } from '../sky'

// Moving sources found across the epochs. "Candidate" = moves, but no Gaia star moves with it:
// the signature of a discovery (e.g. a brown dwarf too red for Gaia).
interface Props {
  result: MoversResult | null
  loading: boolean
  onSelect: (m: Mover) => void
}

const TAGS: Record<Mover['status'], { cls: string; label: string; tip: string }> = {
  candidate: { cls: 'tag tag-cand', label: 'candidate', tip: 'Moves, and no Gaia DR3 star is on its track' },
  unchecked: { cls: 'tag tag-cand', label: 'unchecked', tip: 'Moves; Gaia could not be reached to check it' },
  known: { cls: 'tag', label: 'known', tip: 'A Gaia DR3 star moves the same way' },
  doubtful: { cls: 'tag tag-dim', label: 'doubtful', tip: 'The measured motion is probably an artifact' },
}

const REASONS: Record<string, string> = {
  gaia_motion: 'A Gaia DR3 star sits on this track but moves differently, so the measured motion is probably an artifact (blend or edge)',
  saturated: 'The source is saturated: its centre can wander between epochs and look like motion',
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
  const count = (s: Mover['status']) => movers.filter((m) => m.status === s).length
  const candidates = count('candidate'), unchecked = count('unchecked'), known = count('known'), odd = count('doubtful')
  const parts = [
    candidates && `${candidates} new candidate${candidates === 1 ? '' : 's'} (no Gaia match)`,
    unchecked && `${unchecked} not checked against Gaia`,
    known && `${known} known`,
    odd && `${odd} doubtful`,
  ].filter(Boolean)
  return (
    <section className="movers-section" aria-label="Moving sources">
      <div className="movers-head">
        <h2>Moving sources</h2>
        <span className="muted small">
          {loading ? 'Searching the epochs for moving sources…'
            : !result ? ''
            : result.note ? `Not searched: ${result.note}.`
            : movers.length === 0 ? `None found across ${result.n_epochs} epochs.`
            : `${movers.length} found across ${result.n_epochs} epochs: ${parts.join(', ')}.`}
        </span>
      </div>
      <p className="muted small movers-how">
        Each source is located in every deep epoch, linked across epochs and fitted with a straight-line motion.
        Position errors are calibrated on the still stars of this field. A source is listed when the motion is at least 5σ
        and 0.3″ per year over 3 or more epochs. Candidates have no Gaia DR3 star on their track; very red objects such as
        brown dwarfs are often missing from Gaia. A motion is marked doubtful when a Gaia star sits on the track but moves
        differently, or when the source is saturated.
        {result && result.gaia_checked === false && ' Gaia could not be reached, so movers are listed as unchecked.'}
        {result?.gaia_truncated && ' This field is very crowded and only the brightest Gaia stars were compared, so some candidates may be faint Gaia stars.'}
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
                  <td><span className={TAGS[m.status]?.cls ?? 'tag'} title={(m.reason && REASONS[m.reason]) || TAGS[m.status]?.tip}>{TAGS[m.status]?.label ?? m.status}</span></td>
                  <td className="mono">{formatRA(m.ra)} {formatDec(m.dec)}</td>
                  <td className="num">{m.pm_arcsec_yr.toFixed(2)}{m.pm_err_arcsec_yr != null ? ` ± ${m.pm_err_arcsec_yr.toFixed(2)}` : ''}″/yr</td>
                  <td>{direction(m)}</td>
                  <td className="num">{m.significance.toFixed(1)}σ</td>
                  <td className="num" title={m.n_forced ? `${m.n_forced} measured at the predicted position` : undefined}>{m.n_epochs}{m.n_forced ? ` (${m.n_forced} forced)` : ''}</td>
                  <td className="num">{m.snr >= 1000 ? '>1000' : m.snr.toFixed(0)}{m.saturated ? ' (sat.)' : ''}</td>
                  <td>{m.gaia ? <>{m.gaia.name ?? `Gaia DR3 ${m.gaia.source_id}`}{m.reason === 'gaia_motion' && <span className="muted"> (moves {m.gaia.pm_arcsec_yr.toFixed(2)}″/yr)</span>}</> : <span className="muted">none</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
