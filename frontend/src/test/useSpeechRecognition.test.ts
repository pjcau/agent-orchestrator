import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  useSpeechRecognition,
  type SpeechErrorCode,
} from "@/hooks/useSpeechRecognition";

/**
 * Mock SpeechRecognition implementation that lets each test drive the lifecycle
 * (onstart / onresult / onerror / onend) manually. Mirrors the subset of the
 * Web Speech API the hook depends on.
 */
class MockRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((ev: { error?: string }) => void) | null = null;
  onresult:
    | ((ev: { resultIndex: number; results: unknown[] }) => void)
    | null = null;

  start = vi.fn(() => {
    // Mirror the real API which fires onstart asynchronously, but tests can
    // also call this.fireStart() if they need precise control.
    this.onstart?.();
  });
  stop = vi.fn(() => {
    this.onend?.();
  });
  abort = vi.fn(() => {
    this.onend?.();
  });

  // Helpers for tests
  fireResult(
    chunks: Array<{ transcript: string; isFinal: boolean }>,
    resultIndex = 0
  ) {
    const results = chunks.map((c) => ({
      isFinal: c.isFinal,
      0: { transcript: c.transcript },
      length: 1,
    }));
    this.onresult?.({ resultIndex, results });
  }
  fireError(code: string) {
    this.onerror?.({ error: code });
  }
}

let lastInstance: MockRecognition | null = null;

function installMock() {
  // Constructor returns a fresh mock and records it for the test to inspect.
  const Ctor = vi.fn(() => {
    lastInstance = new MockRecognition();
    return lastInstance;
  });
  (window as unknown as { SpeechRecognition: unknown }).SpeechRecognition =
    Ctor;
  return Ctor;
}

function uninstallMock() {
  lastInstance = null;
  delete (window as unknown as { SpeechRecognition?: unknown })
    .SpeechRecognition;
  delete (window as unknown as { webkitSpeechRecognition?: unknown })
    .webkitSpeechRecognition;
}

describe("useSpeechRecognition", () => {
  beforeEach(() => {
    uninstallMock();
  });
  afterEach(() => {
    uninstallMock();
  });

  it("reports isSupported=false when no constructor is on window", () => {
    const { result } = renderHook(() => useSpeechRecognition());
    expect(result.current.isSupported).toBe(false);
    expect(result.current.isListening).toBe(false);
  });

  it("start() with no support sets error='not-supported'", () => {
    const { result } = renderHook(() => useSpeechRecognition());
    act(() => result.current.start());
    expect(result.current.error).toBe("not-supported");
    expect(result.current.isListening).toBe(false);
  });

  it("falls back to webkitSpeechRecognition if SpeechRecognition is missing", () => {
    const Ctor = vi.fn(() => {
      lastInstance = new MockRecognition();
      return lastInstance;
    });
    (window as unknown as { webkitSpeechRecognition: unknown })
      .webkitSpeechRecognition = Ctor;
    const { result } = renderHook(() => useSpeechRecognition());
    expect(result.current.isSupported).toBe(true);
  });

  it("start() opens a session, applies lang/continuous, and sets isListening", () => {
    installMock();
    const { result } = renderHook(() =>
      useSpeechRecognition({ lang: "en-US", continuous: false })
    );
    act(() => result.current.start());
    expect(lastInstance).not.toBeNull();
    expect(lastInstance!.lang).toBe("en-US");
    expect(lastInstance!.continuous).toBe(false);
    expect(lastInstance!.interimResults).toBe(true);
    expect(result.current.isListening).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("appends final chunks to transcript and notifies onFinal", () => {
    installMock();
    const onFinal = vi.fn();
    const { result } = renderHook(() => useSpeechRecognition({ onFinal }));
    act(() => result.current.start());
    act(() =>
      lastInstance!.fireResult([
        { transcript: "ciao ", isFinal: true },
        { transcript: "mondo", isFinal: false },
      ])
    );
    expect(result.current.transcript).toBe("ciao ");
    expect(result.current.interim).toBe("mondo");
    expect(onFinal).toHaveBeenCalledWith("ciao ");
  });

  it("accumulates multiple final chunks across events", () => {
    installMock();
    const { result } = renderHook(() => useSpeechRecognition());
    act(() => result.current.start());
    // Event 1: a single fresh result at index 0
    act(() =>
      lastInstance!.fireResult([{ transcript: "hello ", isFinal: true }])
    );
    // Event 2: the spec emits the FULL cumulative results array on every
    // event, with `resultIndex` pointing at the first NEW one. Mirror that.
    act(() =>
      lastInstance!.fireResult(
        [
          { transcript: "hello ", isFinal: true },
          { transcript: "world", isFinal: true },
        ],
        1
      )
    );
    expect(result.current.transcript).toBe("hello world");
  });

  const errorCases: Array<[string, SpeechErrorCode]> = [
    ["not-allowed", "permission-denied"],
    ["service-not-allowed", "permission-denied"],
    ["no-speech", "no-speech"],
    ["audio-capture", "audio-capture"],
    ["network", "network"],
    ["aborted", "aborted"],
    ["language-not-supported", "language-not-supported"],
    ["weird-thing", "unknown"],
  ];

  for (const [raw, mapped] of errorCases) {
    it(`maps raw '${raw}' error code to '${mapped}'`, () => {
      installMock();
      const { result } = renderHook(() => useSpeechRecognition());
      act(() => result.current.start());
      act(() => lastInstance!.fireError(raw));
      expect(result.current.error).toBe(mapped);
    });
  }

  it("stop() calls recognition.stop and clears isListening via onend", () => {
    installMock();
    const onEnd = vi.fn();
    const { result } = renderHook(() => useSpeechRecognition({ onEnd }));
    act(() => result.current.start());
    expect(result.current.isListening).toBe(true);
    act(() => result.current.stop());
    expect(lastInstance!.stop).toHaveBeenCalled();
    expect(result.current.isListening).toBe(false);
    expect(onEnd).toHaveBeenCalled();
  });

  it("reset() wipes transcript, interim and error", () => {
    installMock();
    const { result } = renderHook(() => useSpeechRecognition());
    act(() => result.current.start());
    act(() =>
      lastInstance!.fireResult([
        { transcript: "hello", isFinal: true },
        { transcript: "world", isFinal: false },
      ])
    );
    act(() => lastInstance!.fireError("network"));
    expect(result.current.transcript).toBe("hello");
    expect(result.current.error).toBe("network");
    act(() => result.current.reset());
    expect(result.current.transcript).toBe("");
    expect(result.current.interim).toBe("");
    expect(result.current.error).toBeNull();
  });

  it("calling start() twice aborts the previous instance before starting fresh", () => {
    installMock();
    const { result } = renderHook(() => useSpeechRecognition());
    act(() => result.current.start());
    const first = lastInstance!;
    act(() => result.current.start());
    const second = lastInstance!;
    expect(first).not.toBe(second);
    expect(first.abort).toHaveBeenCalled();
  });

  it("maps a thrown rec.start() into 'aborted'", () => {
    // Custom constructor returns mocks whose start() always throws — simulates
    // "InvalidStateError: recognition has already started" or a denied audio
    // device that fails synchronously.
    const Ctor = vi.fn(() => {
      const inst = new MockRecognition();
      inst.start = vi.fn(() => {
        throw new Error("InvalidStateError");
      });
      lastInstance = inst;
      return inst;
    });
    (window as unknown as { SpeechRecognition: unknown }).SpeechRecognition =
      Ctor;
    const { result } = renderHook(() => useSpeechRecognition());
    act(() => result.current.start());
    expect(result.current.error).toBe("aborted");
    expect(result.current.isListening).toBe(false);
  });

  it("unmount aborts the live recognition session", () => {
    installMock();
    const { result, unmount } = renderHook(() => useSpeechRecognition());
    act(() => result.current.start());
    const rec = lastInstance!;
    unmount();
    expect(rec.abort).toHaveBeenCalled();
  });
});
