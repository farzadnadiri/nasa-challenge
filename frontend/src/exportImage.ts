// "Export image": the blink frame (with its overlays) and the reference panel side by side in one
// PNG, with a caption. Works for every reference: survey cutouts (CORS-enabled), the SPHEREx stack
// canvas, and Aladin (via its own view export).

let aladinSnapshot: (() => Promise<string>) | null = null
/** Set by the Aladin panel while it is shown. */
export function setAladinSnapshot(fn: (() => Promise<string>) | null) { aladinSnapshot = fn }

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('reference image could not be loaded'))
    img.src = src
  })
}

async function referenceSource(): Promise<CanvasImageSource | null> {
  const wrap = document.querySelector('.ref-wrap')
  if (!wrap) return null
  if (wrap.classList.contains('aladin-wrap')) return aladinSnapshot ? loadImage(await aladinSnapshot()) : null
  const canvas = wrap.querySelector('canvas.ref-img') as HTMLCanvasElement | null
  if (canvas) return canvas
  const img = wrap.querySelector('img.ref-img') as HTMLImageElement | null
  return img ? loadImage(img.src) : null
}

export async function exportSideBySide(caption: { title: string; left: string; right: string; detail: string }, filename: string) {
  const blink = document.querySelector('canvas.viewer') as HTMLCanvasElement | null
  if (!blink) throw new Error('nothing to export yet')
  const S = 1024, gap = 16, pad = 24, capH = 96
  const out = document.createElement('canvas')
  out.width = S * 2 + gap + pad * 2
  out.height = S + capH + pad * 2
  const ctx = out.getContext('2d')!
  ctx.fillStyle = '#0d0f14'
  ctx.fillRect(0, 0, out.width, out.height)
  ctx.imageSmoothingEnabled = false
  ctx.drawImage(blink, pad, pad, S, S)
  ctx.imageSmoothingEnabled = true
  const ref = await referenceSource().catch(() => null)
  if (ref) {
    ctx.drawImage(ref, pad + S + gap, pad, S, S)
  } else {
    ctx.fillStyle = '#171a21'
    ctx.fillRect(pad + S + gap, pad, S, S)
    ctx.fillStyle = '#8a909c'
    ctx.font = '28px system-ui, sans-serif'
    ctx.fillText('Reference image unavailable', pad + S + gap + 40, pad + S / 2)
  }
  const y = pad + S + 34
  ctx.fillStyle = '#eef0f3'
  ctx.font = 'bold 26px system-ui, sans-serif'
  ctx.fillText(caption.title, pad, y)
  ctx.font = '22px system-ui, sans-serif'
  ctx.fillStyle = '#c2c7d0'
  ctx.fillText(caption.left, pad, y + 34)
  ctx.fillText(caption.right, pad + S + gap, y + 34)
  ctx.fillStyle = '#8a909c'
  ctx.font = '18px system-ui, sans-serif'
  ctx.fillText(caption.detail, pad, y + 62)
  const blob: Blob = await new Promise((resolve, reject) => out.toBlob((b) => (b ? resolve(b) : reject(new Error('export failed'))), 'image/png'))
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 10000)
}
