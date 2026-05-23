/**
 * Client-side image compression for chat-input uploads.
 *
 * Why this module exists
 * ----------------------
 *
 * The dashboard's `/api/upload` endpoint accepts a raw multipart file
 * and passes it to `core/document_converter.py`. Two practical pain
 * points led to this layer:
 *
 *   1. iPhone photos are HEIC by default since iOS 11. The backend
 *      doesn't bundle a HEIC decoder, so an unmodified upload returns
 *      400 "Unsupported file format". Re-encoding to JPEG client-side
 *      sidesteps the dep — and the canvas API on iOS Safari can read
 *      HEIC natively because the OS provides the decoder.
 *
 *   2. A 4K iPhone photo is 3-8 MB; Live Photos / 48 MP shots reach
 *      15-25 MB. Even with a generous nginx body cap, uploads over a
 *      poor mobile network often time out. A 2048-px JPEG @ 0.85
 *      quality lands at <1 MB without visible loss for the screenshots
 *      / receipts / documents users actually upload to a chat.
 *
 * The function is defensive: any failure in the canvas path (corrupt
 * file, OOM, broken codec) returns the ORIGINAL file untouched. We
 * never want compression to break an upload that would otherwise
 * have worked.
 */

const IMAGE_MIME = /^image\//i;
const HEIC_EXT = /\.(heic|heif)$/i;
const MAX_DIMENSION = 2048;
const JPEG_QUALITY = 0.85;

/**
 * Return a compressed JPEG `File` when the input is an image, or the
 * original file otherwise. Never throws; on any error the source file
 * is passed through unchanged.
 *
 * Exported (also for unit testing).
 */
export async function maybeCompressImage(file: File): Promise<File> {
  if (!isImageFile(file)) return file;
  try {
    const compressed = await compressImageToJpeg(file, MAX_DIMENSION, JPEG_QUALITY);
    // If compression somehow produced a LARGER file (already-tiny image,
    // odd dimensions), keep the original.
    if (compressed.size >= file.size) return file;
    return compressed;
  } catch (err) {
    // Browser doesn't have a decoder (e.g. HEIC on desktop Chrome),
    // image is corrupted, OOM, etc. Fall back to the raw bytes — the
    // server will return its own error if the format is unsupported.
    console.warn("[compressImage] falling back to original:", err);
    return file;
  }
}

/** Heuristic — MIME `image/*` or a known image extension. */
export function isImageFile(file: File): boolean {
  if (file.type && IMAGE_MIME.test(file.type)) return true;
  // HEIC files sometimes arrive with empty MIME on older iOS; detect
  // via extension as a last resort.
  return HEIC_EXT.test(file.name);
}

/**
 * Decode → draw at scaled-down dimensions → re-encode as JPEG.
 * Pure DOM API, no extra deps.
 */
async function compressImageToJpeg(
  file: File,
  maxDimension: number,
  quality: number,
): Promise<File> {
  const url = URL.createObjectURL(file);
  try {
    const img = await loadImage(url);
    const { width, height } = scaleToFit(img.naturalWidth, img.naturalHeight, maxDimension);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas 2D context unavailable");
    ctx.drawImage(img, 0, 0, width, height);
    const blob = await canvasToJpegBlob(canvas, quality);
    const baseName = file.name.replace(/\.[^.]+$/, "") || "image";
    return new File([blob], `${baseName}.jpg`, {
      type: "image/jpeg",
      lastModified: file.lastModified,
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("image decode failed"));
    img.src = src;
  });
}

/** Preserve aspect ratio; never upscale. Returns 1×1 for any non-positive
 * input — the canvas would refuse to render anyway, but giving it a sane
 * default keeps `drawImage` from throwing on the caller's side. */
export function scaleToFit(
  w: number,
  h: number,
  max: number,
): { width: number; height: number } {
  if (w <= 0 || h <= 0) return { width: 1, height: 1 };
  const longest = Math.max(w, h);
  if (longest <= max) return { width: w, height: h };
  const ratio = max / longest;
  return { width: Math.round(w * ratio), height: Math.round(h * ratio) };
}

function canvasToJpegBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("canvas.toBlob returned null"))),
      "image/jpeg",
      quality,
    );
  });
}
