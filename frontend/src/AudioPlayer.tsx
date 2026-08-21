import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

interface AudioPlayerProps {
  src: string;
  label: string;
  className?: string;
  onPlay?: () => void;
}

function formatTime(value: number) {
  if (!Number.isFinite(value) || value < 0) return "0:00";
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60);
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function AudioPlayer({ src, label, className = "", onPlay }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    audio.load();
    setDuration(0);
    setCurrentTime(0);
    setPlaying(false);
  }, [src]);

  async function togglePlayback() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      await audio.play().catch(() => setPlaying(false));
    } else {
      audio.pause();
    }
  }

  function seek(nextTime: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = nextTime;
    setCurrentTime(nextTime);
  }

  function toggleMuted() {
    const audio = audioRef.current;
    if (!audio) return;
    audio.muted = !audio.muted;
    setMuted(audio.muted);
  }

  const progress = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;
  const progressStyle = { "--audio-progress": `${progress}%` } as CSSProperties;

  return (
    <div className={`audio-player ${className}`.trim()} aria-label={label} style={progressStyle}>
      <audio
        ref={audioRef}
        className="audio-player__native"
        preload="metadata"
        src={src}
        onLoadedMetadata={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
        onDurationChange={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onPlay={() => { setPlaying(true); onPlay?.(); }}
        onPause={() => setPlaying(false)}
        onEnded={() => { setPlaying(false); setCurrentTime(0); }}
      />
      <button className="audio-player__button" type="button" title={playing ? "暂停" : "播放"} aria-label={playing ? "暂停" : "播放"} onClick={() => void togglePlayback()}>
        {playing ? <Pause size={15} fill="currentColor" /> : <Play size={15} fill="currentColor" />}
      </button>
      <span className="audio-player__time">{formatTime(currentTime)} / {formatTime(duration)}</span>
      <input
        className="audio-player__timeline"
        type="range"
        min="0"
        max={Math.max(duration, 0)}
        step="0.01"
        value={Math.min(currentTime, duration || 0)}
        aria-label={`${label}播放进度`}
        onChange={(event) => seek(Number(event.target.value))}
      />
      <button className="audio-player__button" type="button" title={muted ? "恢复声音" : "静音"} aria-label={muted ? "恢复声音" : "静音"} onClick={toggleMuted}>
        {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
      </button>
    </div>
  );
}
