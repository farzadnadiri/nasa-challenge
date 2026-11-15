import { useState } from 'react'
import { createDataset, uploadFile } from '../api'
import { parseTargetInput } from '../sky'

// Upload your own FITS images ("FITS + WCS v1"); they are aligned and blinked like SPHEREx data.
interface Props {
  onDone: (id: string, target: [number, number] | null) => void
  onClose: () => void
}

type FileState = { name: string; size: number; status: 'waiting' | 'uploading' | 'done' | 'error'; error?: string }

const MB = 1000 * 1000
const MAX_FILE = 100 * MB // per file; Cloudflare's request cap on this plan
const OK_NAME = /\.(fits|fit|fts)(\.gz)?$/i

export function UploadPanel({ onDone, onClose }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [states, setStates] = useState<FileState[]>([])
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pick = (list: FileList | null) => {
    const chosen = [...(list ?? [])]
    setFiles(chosen)
    setStates(chosen.map((f) => ({
      name: f.name, size: f.size,
      status: !OK_NAME.test(f.name) ? 'error' : f.size > MAX_FILE ? 'error' : 'waiting',
      error: !OK_NAME.test(f.name) ? 'name must end in .fits, .fit or .fts' : f.size > MAX_FILE ? 'larger than 100 MB' : undefined,
    })))
    setError(null)
  }

  const start = async () => {
    const t = target.trim() ? parseTargetInput(target) : null
    if (target.trim() && !t) { setError('Target must be decimal degrees ("150.1 2.2") or sexagesimal ("10:00:24 +02:12:00").'); return }
    const good = files.filter((f) => OK_NAME.test(f.name) && f.size <= MAX_FILE)
    if (!good.length) { setError('Choose at least one FITS file.'); return }
    if (good.length > 50) { setError('A dataset holds at most 50 files.'); return }
    setBusy(true)
    setError(null)
    try {
      const { id } = await createDataset()
      let uploaded = 0
      for (const f of good) {
        setStates((s) => s.map((x) => (x.name === f.name ? { ...x, status: 'uploading' } : x)))
        try {
          await uploadFile(id, f)
          uploaded++
          setStates((s) => s.map((x) => (x.name === f.name ? { ...x, status: 'done' } : x)))
        } catch (e) {
          setStates((s) => s.map((x) => (x.name === f.name ? { ...x, status: 'error', error: (e as Error).message } : x)))
        }
      }
      if (!uploaded) throw new Error('No file could be uploaded.')
      onDone(id, t)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="upload-panel" aria-label="Upload your own images">
      <div className="upload-head">
        <h2>Explore your own images</h2>
        <button className="link" onClick={onClose}>Close</button>
      </div>
      <details className="upload-standard">
        <summary>File standard: FITS + WCS v1 (one file per exposure)</summary>
        <ul>
          <li><strong>Required:</strong> a 2-D image (first image HDU, or an extension named <code>IMAGE</code>/<code>SCI</code>),
            a celestial WCS (RA/Dec), and an observation time: <code>MJD-OBS</code>, <code>MJD-AVG</code> or <code>DATE-OBS</code> (UTC).</li>
          <li><strong>Optional:</strong> noise as <code>VARIANCE</code>, <code>ERR</code> (1σ) or <code>WEIGHT</code> (inverse variance);
            bad pixels as <code>MASK</code>, <code>FLAGS</code> or <code>DQ</code> (non-zero = bad); <code>WAVELEN</code> (µm) or <code>FILTER</code>; <code>BUNIT</code>.</li>
          <li><strong>Limits:</strong> up to 50 files, 100 MB each, 1 GB per dataset, images up to 6000 × 6000.
            All images should cover the same patch of sky.</li>
          <li><strong>Privacy:</strong> your dataset gets a private link and is deleted automatically after 7 days.</li>
        </ul>
      </details>
      <div className="upload-row">
        <label className="upload-files">FITS files
          <input type="file" multiple accept=".fits,.fit,.fts,.gz" disabled={busy} onChange={(e) => pick(e.target.files)} />
        </label>
        <label className="upload-target">Target (optional)
          <input value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}
            placeholder="RA Dec; default: centre of your images" />
        </label>
        <button className="upload-go" onClick={start} disabled={busy || !files.length}>
          {busy ? 'Uploading…' : `Upload ${files.length || ''} file${files.length === 1 ? '' : 's'}`}
        </button>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      {states.length > 0 && (
        <ul className="upload-list">
          {states.map((f) => (
            <li key={f.name} className={`up-${f.status}`}>
              <span className="mono">{f.name}</span>
              <span className="muted small">{(f.size / MB).toFixed(1)} MB</span>
              <span className="up-status">{f.status === 'error' ? f.error : f.status}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
