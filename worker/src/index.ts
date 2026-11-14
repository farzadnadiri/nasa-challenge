import { Container } from '@cloudflare/containers'

// FastAPI science backend (backend/Dockerfile). One named instance: job progress lives in
// its memory, so every poll for the same view must reach the same container.
export class SpherexApi extends Container {
  defaultPort = 8080
  sleepAfter = '30m'
  enableInternet = true // IRSA, Gaia and CDS are reached from the container
  pingEndpoint = '/api/health'
}

// Env (API, CACHE, ASSETS) is generated from wrangler.jsonc by `npm run types`.

const CACHEABLE = new Set(['/api/frames', '/api/photometry', '/api/gaia'])
const TTL_MS = 7 * 24 * 3600 * 1000 // SPHEREx ingests weekly

function cacheKey(url: URL): string {
  const params = [...url.searchParams.entries()].sort(([a], [b]) => a.localeCompare(b))
  return `v1${url.pathname}?${new URLSearchParams(params)}`
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url)
    if (!url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request)
    if (request.method !== 'GET') return new Response('Method not allowed', { status: 405 })

    const cacheable = CACHEABLE.has(url.pathname)
    const key = cacheKey(url)
    if (cacheable) {
      const hit = await env.CACHE.get(key)
      const saved = Number(hit?.customMetadata?.saved ?? 0)
      if (hit && Date.now() - saved < TTL_MS) {
        return new Response(hit.body, {
          headers: { 'content-type': 'application/json', 'cache-control': 'public, max-age=3600', 'x-cache': 'HIT' },
        })
      }
    }

    const container = env.API.getByName('api')
    await container.startAndWaitForPorts()
    const res = await container.fetch(request)

    // Store only finished results; in-progress responses change on every poll.
    if (cacheable && res.ok && res.headers.get('x-complete') === 'true') {
      const body = await res.arrayBuffer()
      ctx.waitUntil(env.CACHE.put(key, body, {
        httpMetadata: { contentType: 'application/json' },
        customMetadata: { saved: String(Date.now()) },
      }))
      return new Response(body, { status: res.status, headers: res.headers })
    }
    return res
  },
} satisfies ExportedHandler<Env>
