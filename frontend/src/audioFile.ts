function writeAscii(view: DataView, offset: number, value: string) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

function encodeMonoWav(audio: AudioBuffer): ArrayBuffer {
  const frames = audio.length;
  const output = new ArrayBuffer(44 + frames * 2);
  const view = new DataView(output);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + frames * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, audio.sampleRate, true);
  view.setUint32(28, audio.sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, frames * 2, true);

  const channels = Array.from({ length: audio.numberOfChannels }, (_, index) => audio.getChannelData(index));
  let offset = 44;
  for (let frame = 0; frame < frames; frame += 1) {
    let sample = 0;
    channels.forEach((channel) => { sample += channel[frame] ?? 0; });
    sample = Math.max(-1, Math.min(1, sample / channels.length));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return output;
}

export async function normalizeAudioToWav(source: Blob, name: string): Promise<File> {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await source.arrayBuffer());
    if (decoded.duration < 0.5) throw new Error("参考音频至少需要 0.5 秒");
    if (decoded.duration > 120) throw new Error("参考音频不能超过 120 秒");
    const wav = encodeMonoWav(decoded);
    const baseName = name.replace(/\.[^.]+$/, "").replace(/[^\w\u4e00-\u9fff-]+/g, "-") || "reference";
    return new File([wav], `${baseName}.wav`, { type: "audio/wav" });
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("参考音频")) throw error;
    throw new Error("无法读取该音频格式，请改用 WAV、MP3、M4A、FLAC 或 OGG");
  } finally {
    await context.close();
  }
}
