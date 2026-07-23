import type { CharacterSummary } from "./types";

const waveform = [18, 44, 32, 62, 28, 52, 70, 38, 24, 58, 42, 76, 35, 64, 48, 30, 68, 54, 22, 46, 72, 36, 56, 26, 60, 40, 66, 31, 50, 20, 74, 45];

export function Waveform({ color }: { color: CharacterSummary["color"] }) {
  return (
    <div className={`waveform waveform--${color}`} aria-label="音频波形">
      {waveform.map((height, index) => (
        <span key={index} style={{ height: `${height}%` }} />
      ))}
    </div>
  );
}
