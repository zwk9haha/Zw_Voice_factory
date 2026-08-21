import { useEffect, useRef, useState } from "react";
import type { CharacterSummary } from "./types";

interface WaveformProps {
  src: string | null | undefined;
  color: CharacterSummary["color"];
  barCount?: number;
  className?: string;
}

const peakCache = new Map<string, Promise<number[]>>();
let decoder: OfflineAudioContext | null = null;

function audioDecoder(): OfflineAudioContext {
  decoder ??= new OfflineAudioContext(1, 1, 44_100);
  return decoder;
}

function extractPeaks(buffer: AudioBuffer, barCount: number): number[] {
  const peaks = Array.from({ length: barCount }, () => 0);
  const bucketSize = Math.max(1, Math.floor(buffer.length / barCount));
  for (let barIndex = 0; barIndex < barCount; barIndex += 1) {
    const start = barIndex * bucketSize;
    const end = barIndex === barCount - 1 ? buffer.length : Math.min(buffer.length, start + bucketSize);
    const stride = Math.max(1, Math.floor((end - start) / 256));
    let peak = 0;
    for (let channelIndex = 0; channelIndex < buffer.numberOfChannels; channelIndex += 1) {
      const channel = buffer.getChannelData(channelIndex);
      for (let sampleIndex = start; sampleIndex < end; sampleIndex += stride) {
        peak = Math.max(peak, Math.abs(channel[sampleIndex] ?? 0));
      }
    }
    peaks[barIndex] = peak;
  }
  const maximum = Math.max(...peaks);
  if (maximum <= 0.0001) return peaks.map(() => 0);
  return peaks.map((peak) => Math.pow(peak / maximum, 0.72));
}

function loadPeaks(src: string, barCount: number): Promise<number[]> {
  const cacheKey = `${barCount}:${src}`;
  const cached = peakCache.get(cacheKey);
  if (cached) return cached;
  const pending = fetch(src, { cache: "force-cache" })
    .then((response) => {
      if (!response.ok) throw new Error(`音频读取失败：${response.status}`);
      return response.arrayBuffer();
    })
    .then((content) => audioDecoder().decodeAudioData(content))
    .then((buffer) => extractPeaks(buffer, barCount))
    .catch((error) => {
      peakCache.delete(cacheKey);
      throw error;
    });
  peakCache.set(cacheKey, pending);
  return pending;
}

export function Waveform({ src, color, barCount = 72, className = "" }: WaveformProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "failed">("idle");

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "160px" });
    observer.observe(root);
    return () => observer.disconnect();
  }, [src]);

  useEffect(() => {
    let active = true;
    setPeaks([]);
    if (!src || !visible) {
      setStatus("idle");
      return () => { active = false; };
    }
    setStatus("loading");
    loadPeaks(src, barCount).then((nextPeaks) => {
      if (!active) return;
      setPeaks(nextPeaks);
      setStatus("ready");
    }).catch(() => {
      if (active) setStatus("failed");
    });
    return () => { active = false; };
  }, [barCount, src, visible]);

  return (
    <div
      ref={rootRef}
      className={`waveform waveform--${color} waveform--${status} ${className}`.trim()}
      role="img"
      aria-label={status === "failed" ? "音频波形读取失败" : "音频真实波形"}
      aria-busy={status === "loading"}
      data-waveform-source={src ?? undefined}
    >
      {peaks.map((peak, index) => <span key={index} style={{ height: `${Math.max(0.04, peak) * 100}%` }} />)}
    </div>
  );
}
