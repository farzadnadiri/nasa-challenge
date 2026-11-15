// User-uploaded datasets ("FITS + WCS v1"): stored in R2 under uploads/<id>/, deleted after 7 days
// by the bucket's lifecycle rule. Anyone may upload, within the limits below; every dataset gets an
// unguessable 128-bit id, so a dataset is only visible to people who have its link.

// 100 MB per file is also Cloudflare's request-body cap on this plan, so files are streamed
// straight into R2 rather than held in Worker memory.
export const LIMITS = { maxFiles: 50, maxFileBytes: 100 * 1000 * 1000, maxDatasetBytes: 1000 * 1000 * 1000, keepDays: 7 }

const ID_RE = /^[0-9a-f]{32}$/
const NAME_RE = /^[A-Za-z0-9._-]{1,80}$/
const EXT_RE = /\.(fits|fit|fts)(\.gz)?$/i

interface Manifest { created: number; bytes: number; files: { name: string; bytes: number }[] }

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const fail = (status: number, error: string) => json({ error }, status)

async function readManifest(env: Env, id: string): Promise<Manifest | null> {
  const obj = await env.CACHE.get(`uploads/${id}/manifest.json`)
  return obj ? ((await obj.json()) as Manifest) : null
}

async function limited(limiter: RateLimit, request: Request): Promise<boolean> {
  const ip = request.headers.get('cf-connecting-ip') ?? 'unknown'
  return !(await limiter.limit({ key: ip })).success
}

/** POST /api/datasets: create an empty dataset. */
export async function createDataset(request: Request, env: Env): Promise<Response> {
  if (await limited(env.UPLOAD_CREATE_LIMIT, request)) return fail(429, 'Too many new datasets from your address; wait a minute.')
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  const id = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
  const manifest: Manifest = { created: Date.now(), bytes: 0, files: [] }
  await env.CACHE.put(`uploads/${id}/manifest.json`, JSON.stringify(manifest), { httpMetadata: { contentType: 'application/json' } })
  return json({ id, limits: LIMITS })
}

/** PUT /api/datasets/<id>/files/<name>: add one FITS file (raw body). */
export async function putFile(request: Request, env: Env, id: string, name: string): Promise<Response> {
  if (await limited(env.UPLOAD_FILE_LIMIT, request)) return fail(429, 'Too many uploads from your address; wait a minute.')
  if (!ID_RE.test(id)) return fail(404, 'Unknown dataset.')
  if (!NAME_RE.test(name) || !EXT_RE.test(name)) {
    return fail(400, 'File names may use letters, digits, dot, dash and underscore, and must end in .fits, .fit or .fts (optionally .gz).')
  }
  const length = Number(request.headers.get('content-length') ?? NaN)
  if (!Number.isFinite(length)) return fail(411, 'Content-Length is required.')
  if (length > LIMITS.maxFileBytes) return fail(413, `Files are limited to ${LIMITS.maxFileBytes / 1e6} MB.`)
  const manifest = await readManifest(env, id)
  if (!manifest) return fail(404, 'Unknown or expired dataset.')
  const others = manifest.files.filter((f) => f.name !== name)
  if (others.length >= LIMITS.maxFiles) return fail(413, `A dataset holds at most ${LIMITS.maxFiles} files.`)
  const used = others.reduce((s, f) => s + f.bytes, 0)
  if (used + length > LIMITS.maxDatasetBytes) return fail(413, `A dataset holds at most ${LIMITS.maxDatasetBytes / 1e9} GB.`)

  if (!request.body) return fail(400, 'Empty upload.')
  // Stream into R2 (a 100 MB file would not fit in Worker memory), then check the FITS signature.
  const key = `uploads/${id}/files/${name}`
  const stored = await env.CACHE.put(key, request.body, { httpMetadata: { contentType: 'application/fits' } })
  const first = await env.CACHE.get(key, { range: { offset: 0, length: 9 } })
  const b = first ? new Uint8Array(await first.arrayBuffer()) : new Uint8Array()
  const isFits = new TextDecoder().decode(b) === 'SIMPLE  =' || (b[0] === 0x1f && b[1] === 0x8b)
  if (!stored || !isFits || stored.size > LIMITS.maxFileBytes) {
    await env.CACHE.delete(key)
    return fail(isFits ? 413 : 415, isFits ? 'File too large.' : 'Not a FITS file (it must start with SIMPLE =).')
  }
  manifest.files = [...others, { name, bytes: stored.size }]
  manifest.bytes = used + stored.size
  await env.CACHE.put(`uploads/${id}/manifest.json`, JSON.stringify(manifest), { httpMetadata: { contentType: 'application/json' } })
  return json({ ok: true, files: manifest.files.length, bytes: manifest.bytes })
}

/** GET /api/_upload/<id>/<manifest.json | files/name>: internal, for the container only. */
export async function readUpload(request: Request, env: Env, rest: string): Promise<Response> {
  if (!env.IRSA_PROXY_KEY || request.headers.get('x-proxy-key') !== env.IRSA_PROXY_KEY) return fail(403, 'Forbidden')
  const m = rest.match(/^([0-9a-f]{32})\/(manifest\.json|files\/[A-Za-z0-9._-]{1,80})$/)
  if (!m) return fail(404, 'Not found')
  const obj = await env.CACHE.get(`uploads/${m[1]}/${m[2]}`)
  if (!obj) return fail(404, 'Not found')
  return new Response(obj.body, { headers: { 'content-type': obj.httpMetadata?.contentType ?? 'application/octet-stream' } })
}
