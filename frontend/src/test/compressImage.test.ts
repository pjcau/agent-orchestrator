/**
 * Tests for the client-side image compressor used by chat-input uploads.
 *
 * The canvas API behaviour is provided by jsdom in `vitest` — `toBlob` and
 * `getContext("2d")` are not natively available, so the heavy decode/encode
 * path is exercised against monkey-patched stubs that mimic the browser
 * contract. The pure helpers (`isImageFile`, `scaleToFit`) are tested
 * straight without any monkey-patching.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { isImageFile, scaleToFit, maybeCompressImage } from "@/lib/compressImage";

describe("isImageFile", () => {
  it("matches image/* MIME", () => {
    expect(isImageFile(new File([""], "x.png", { type: "image/png" }))).toBe(true);
    expect(isImageFile(new File([""], "x.jpg", { type: "image/jpeg" }))).toBe(true);
    expect(isImageFile(new File([""], "x.webp", { type: "image/webp" }))).toBe(true);
  });

  it("detects HEIC via extension when MIME is empty (older iOS)", () => {
    expect(isImageFile(new File([""], "IMG_0001.heic", { type: "" }))).toBe(true);
    expect(isImageFile(new File([""], "IMG_0001.HEIF", { type: "" }))).toBe(true);
  });

  it("rejects non-images", () => {
    expect(isImageFile(new File([""], "doc.pdf", { type: "application/pdf" }))).toBe(false);
    expect(isImageFile(new File([""], "notes.txt", { type: "text/plain" }))).toBe(false);
    expect(isImageFile(new File([""], "data.csv", { type: "" }))).toBe(false);
  });
});

describe("scaleToFit", () => {
  it("never upscales when already within max", () => {
    expect(scaleToFit(800, 600, 2048)).toEqual({ width: 800, height: 600 });
  });

  it("scales landscape down to max longest dimension", () => {
    expect(scaleToFit(4032, 3024, 2048)).toEqual({ width: 2048, height: 1536 });
  });

  it("scales portrait down to max longest dimension", () => {
    expect(scaleToFit(3024, 4032, 2048)).toEqual({ width: 1536, height: 2048 });
  });

  it("handles square", () => {
    expect(scaleToFit(4000, 4000, 2048)).toEqual({ width: 2048, height: 2048 });
  });

  it("guards against zero/negative dimensions", () => {
    expect(scaleToFit(0, 0, 2048)).toEqual({ width: 1, height: 1 });
    expect(scaleToFit(-5, -5, 2048)).toEqual({ width: 1, height: 1 });
  });
});

describe("maybeCompressImage", () => {
  // Save the originals so each test can re-install fresh stubs.
  let originalCreateObjectURL: typeof URL.createObjectURL;
  let originalRevokeObjectURL: typeof URL.revokeObjectURL;
  let originalGetContext: typeof HTMLCanvasElement.prototype.getContext;
  let originalToBlob: typeof HTMLCanvasElement.prototype.toBlob;

  beforeEach(() => {
    originalCreateObjectURL = URL.createObjectURL;
    originalRevokeObjectURL = URL.revokeObjectURL;
    originalGetContext = HTMLCanvasElement.prototype.getContext;
    originalToBlob = HTMLCanvasElement.prototype.toBlob;
    URL.createObjectURL = vi.fn(() => "blob:fake");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    HTMLCanvasElement.prototype.getContext = originalGetContext;
    HTMLCanvasElement.prototype.toBlob = originalToBlob;
  });

  it("passes through non-image files unchanged", async () => {
    const pdf = new File(["%PDF-1.4"], "doc.pdf", { type: "application/pdf" });
    const out = await maybeCompressImage(pdf);
    expect(out).toBe(pdf);
  });

  it("returns a JPEG file when the canvas pipeline succeeds", async () => {
    // Fake image decode: Image.onload fires immediately with 4000×3000.
    Object.defineProperty(window.Image.prototype, "src", {
      configurable: true,
      set(this: HTMLImageElement, _v: string) {
        Object.defineProperty(this, "naturalWidth", { value: 4000, configurable: true });
        Object.defineProperty(this, "naturalHeight", { value: 3000, configurable: true });
        setTimeout(() => this.onload?.(new Event("load")), 0);
      },
    });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      drawImage: vi.fn(),
    })) as never;
    // toBlob returns a tiny "JPEG"
    HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
      cb(new Blob(["fake-jpeg-bytes"], { type: "image/jpeg" }));
    };

    const heic = new File([new Uint8Array(8 * 1024 * 1024)], "IMG_0001.heic", { type: "" });
    const out = await maybeCompressImage(heic);

    expect(out).not.toBe(heic);                            // recompressed
    expect(out.type).toBe("image/jpeg");
    expect(out.name).toBe("IMG_0001.jpg");                 // extension rewritten
    expect(out.size).toBeLessThan(heic.size);              // strictly smaller
  });

  it("falls back to the original on decode error (HEIC on desktop)", async () => {
    Object.defineProperty(window.Image.prototype, "src", {
      configurable: true,
      set(this: HTMLImageElement, _v: string) {
        setTimeout(() => this.onerror?.(new Event("error")), 0);
      },
    });

    const heic = new File([new Uint8Array(4 * 1024 * 1024)], "IMG_0002.heic", { type: "" });
    const out = await maybeCompressImage(heic);
    expect(out).toBe(heic);
  });

  it("keeps the original when compression produces a larger file", async () => {
    Object.defineProperty(window.Image.prototype, "src", {
      configurable: true,
      set(this: HTMLImageElement, _v: string) {
        Object.defineProperty(this, "naturalWidth", { value: 100, configurable: true });
        Object.defineProperty(this, "naturalHeight", { value: 100, configurable: true });
        setTimeout(() => this.onload?.(new Event("load")), 0);
      },
    });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never;
    HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
      // Encoder bloats a 100×100 image — common for tiny inputs.
      cb(new Blob([new Uint8Array(50_000)], { type: "image/jpeg" }));
    };

    const tiny = new File([new Uint8Array(1_000)], "icon.png", { type: "image/png" });
    const out = await maybeCompressImage(tiny);
    expect(out).toBe(tiny);
  });
});
