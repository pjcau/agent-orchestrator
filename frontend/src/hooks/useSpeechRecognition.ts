import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Voice input via the browser Web Speech API.
 *
 * Supported in Chrome / Edge / Safari (incl. iOS 14.5+ / macOS 14.3+ on-device).
 * Firefox does not ship `SpeechRecognition` — `isSupported` is false there and
 * the hook surfaces a `"not-supported"` error if `start()` is called.
 *
 * The hook does NOT touch the textarea state directly. The caller wires the
 * `onFinal` callback to its own state (e.g. setText((prev) => prev + chunk))
 * so dictation behaves correctly alongside manual typing.
 */

export type SpeechErrorCode =
  | "not-supported"
  | "permission-denied"
  | "no-speech"
  | "audio-capture"
  | "network"
  | "aborted"
  | "service-not-allowed"
  | "language-not-supported"
  | "unknown";

export interface SpeechRecognitionState {
  isSupported: boolean;
  isListening: boolean;
  /** Final transcript accumulated across the current/last session. */
  transcript: string;
  /** Last interim (non-final) chunk — useful for a live preview. */
  interim: string;
  error: SpeechErrorCode | null;
  start: () => void;
  stop: () => void;
  reset: () => void;
}

export interface UseSpeechRecognitionOpts {
  /** BCP-47 language tag — defaults to navigator.language with `it-IT` fallback. */
  lang?: string;
  /** When true, keeps listening until the caller calls `stop()`. Default true. */
  continuous?: boolean;
  /** Called with each final chunk as it arrives. */
  onFinal?: (chunk: string) => void;
  /** Called with the live interim transcript on every result event. */
  onInterim?: (chunk: string) => void;
  /** Called when recognition ends — whether by stop, no-speech, or error. */
  onEnd?: () => void;
}

// The Web Speech API types are not in lib.dom.d.ts yet — keep them loose.
type SpeechRecognitionInstance = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((ev: { error?: string }) => void) | null;
  onresult: ((ev: SpeechRecognitionEventLike) => void) | null;
};

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    [index: number]: { transcript: string };
    length: number;
  }>;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionInstance;

function getCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function mapError(raw: string | undefined): SpeechErrorCode {
  switch (raw) {
    case "not-allowed":
    case "service-not-allowed":
      return "permission-denied";
    case "no-speech":
      return "no-speech";
    case "audio-capture":
      return "audio-capture";
    case "network":
      return "network";
    case "aborted":
      return "aborted";
    case "language-not-supported":
      return "language-not-supported";
    default:
      return "unknown";
  }
}

function defaultLang(): string {
  if (typeof navigator === "undefined") return "it-IT";
  return navigator.language || "it-IT";
}

export function useSpeechRecognition(
  opts: UseSpeechRecognitionOpts = {}
): SpeechRecognitionState {
  const { lang, continuous = true, onFinal, onInterim, onEnd } = opts;
  const resolvedLang = lang ?? defaultLang();

  // `isSupported` is computed once — the API surface doesn't change at runtime.
  const isSupported = getCtor() !== null;

  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<SpeechErrorCode | null>(null);

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  // Keep callbacks in a ref so updating them doesn't force a new recognition
  // instance — the running session keeps the latest handlers.
  const callbacksRef = useRef({ onFinal, onInterim, onEnd });
  useEffect(() => {
    callbacksRef.current = { onFinal, onInterim, onEnd };
  }, [onFinal, onInterim, onEnd]);

  const reset = useCallback(() => {
    setTranscript("");
    setInterim("");
    setError(null);
  }, []);

  const start = useCallback(() => {
    const Ctor = getCtor();
    if (!Ctor) {
      setError("not-supported");
      return;
    }
    // If we're already running, ignore the call instead of double-starting.
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch {
        // ignore
      }
      recognitionRef.current = null;
    }

    let rec: SpeechRecognitionInstance;
    try {
      rec = new Ctor();
    } catch {
      setError("unknown");
      return;
    }

    rec.lang = resolvedLang;
    rec.continuous = continuous;
    rec.interimResults = true;

    rec.onstart = () => {
      setIsListening(true);
      setError(null);
    };

    rec.onend = () => {
      setIsListening(false);
      setInterim("");
      recognitionRef.current = null;
      callbacksRef.current.onEnd?.();
    };

    rec.onerror = (ev) => {
      setError(mapError(ev?.error));
      // onend fires after onerror for terminal errors — leave listening flag
      // alone so the UI shows the error before resetting to idle.
    };

    rec.onresult = (ev) => {
      let interimText = "";
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const result = ev.results[i];
        const chunk = result[0]?.transcript ?? "";
        if (result.isFinal) {
          finalText += chunk;
        } else {
          interimText += chunk;
        }
      }
      if (finalText) {
        setTranscript((prev) => prev + finalText);
        callbacksRef.current.onFinal?.(finalText);
      }
      setInterim(interimText);
      if (interimText) {
        callbacksRef.current.onInterim?.(interimText);
      }
    };

    recognitionRef.current = rec;
    try {
      rec.start();
    } catch {
      // `start()` throws if already running. Map to aborted so the UI clears.
      setError("aborted");
      recognitionRef.current = null;
    }
  }, [resolvedLang, continuous]);

  const stop = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    try {
      rec.stop();
    } catch {
      // ignore
    }
  }, []);

  // Cleanup on unmount: abort and drop the reference so async events from a
  // stale instance don't write into a stale React tree.
  useEffect(() => {
    return () => {
      const rec = recognitionRef.current;
      recognitionRef.current = null;
      if (rec) {
        try {
          rec.onstart = null;
          rec.onend = null;
          rec.onerror = null;
          rec.onresult = null;
          rec.abort();
        } catch {
          // ignore
        }
      }
    };
  }, []);

  return {
    isSupported,
    isListening,
    transcript,
    interim,
    error,
    start,
    stop,
    reset,
  };
}
