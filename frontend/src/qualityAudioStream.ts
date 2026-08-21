export interface QualityPcmMetadata {
  job_id: string;
  sample_rate: number;
  channels: number;
  sample_width: number;
  format: "pcm_s16le";
  rolling_gain_db?: number;
}

interface StreamCallbacks {
  onMetadata: (metadata: QualityPcmMetadata) => void;
  onAudio: (metadata: QualityPcmMetadata, audio: Uint8Array) => void;
}

const FRAME_METADATA = 1;
const FRAME_AUDIO = 2;
const FRAME_ERROR = 3;
const FRAME_HEADER_BYTES = 5;

function appendBytes(left: Uint8Array, right: Uint8Array): Uint8Array {
  if (!left.length) return right.slice();
  const merged = new Uint8Array(left.length + right.length);
  merged.set(left);
  merged.set(right, left.length);
  return merged;
}

function decodeJson<T>(payload: Uint8Array): T {
  return JSON.parse(new TextDecoder().decode(payload)) as T;
}

export async function consumeQualityPcmStream(
  response: Response,
  signal: AbortSignal,
  callbacks: StreamCallbacks,
): Promise<QualityPcmMetadata> {
  if (!response.body) throw new Error("浏览器未提供流式响应读取能力");
  const reader = response.body.getReader();
  let pending: Uint8Array<ArrayBufferLike> = new Uint8Array();
  let metadata: QualityPcmMetadata | null = null;
  try {
    while (true) {
      if (signal.aborted) throw new DOMException("流式播放已停止", "AbortError");
      const { value, done } = await reader.read();
      if (done) break;
      if (value) pending = appendBytes(pending, value);
      let offset = 0;
      while (pending.length - offset >= FRAME_HEADER_BYTES) {
        const view = new DataView(pending.buffer, pending.byteOffset + offset, FRAME_HEADER_BYTES);
        const frameType = view.getUint8(0);
        const payloadSize = view.getUint32(1, false);
        if (pending.length - offset - FRAME_HEADER_BYTES < payloadSize) break;
        const payloadStart = offset + FRAME_HEADER_BYTES;
        const payload = pending.slice(payloadStart, payloadStart + payloadSize);
        offset = payloadStart + payloadSize;
        if (frameType === FRAME_METADATA) {
          metadata = decodeJson<QualityPcmMetadata>(payload);
          if (
            metadata.format !== "pcm_s16le"
            || metadata.sample_width !== 2
            || metadata.sample_rate < 8_000
            || ![1, 2].includes(metadata.channels)
          ) {
            throw new Error("服务端返回了浏览器不支持的流式音频格式");
          }
          callbacks.onMetadata(metadata);
        } else if (frameType === FRAME_AUDIO) {
          if (!metadata) throw new Error("流式音频缺少格式元数据");
          callbacks.onAudio(metadata, payload);
        } else if (frameType === FRAME_ERROR) {
          const error = decodeJson<{ message?: string }>(payload);
          throw new Error(error.message || "GPT-SoVITS 流式生成失败");
        } else {
          throw new Error(`未知流式音频帧：${frameType}`);
        }
      }
      pending = pending.slice(offset);
    }
  } finally {
    reader.releaseLock();
  }
  if (pending.length) throw new Error("流式音频响应不完整");
  if (!metadata) throw new Error("流式音频没有返回格式元数据");
  return metadata;
}

export class QualityPcmPlayer {
  private context: AudioContext | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private generation = 0;
  private nextStartTime = 0;
  private streamFinished = false;
  private playbackStarted = false;
  private active = false;
  private resolvePlayback: (() => void) | null = null;
  private playbackPromise: Promise<void> = Promise.resolve();

  get isActive(): boolean {
    return this.active;
  }

  get isPaused(): boolean {
    return this.context?.state === "suspended" && this.active;
  }

  async prepare(): Promise<void> {
    this.context ??= new AudioContext();
    if (this.context.state === "suspended") await this.context.resume();
  }

  async begin(): Promise<void> {
    this.stop();
    await this.prepare();
    this.generation += 1;
    this.nextStartTime = 0;
    this.streamFinished = false;
    this.playbackStarted = false;
    this.active = true;
    this.playbackPromise = new Promise<void>((resolve) => {
      this.resolvePlayback = resolve;
    });
  }

  append(metadata: QualityPcmMetadata, payload: Uint8Array): number | null {
    const context = this.context;
    if (!context || !this.active || !payload.length) return null;
    const bytesPerFrame = metadata.channels * metadata.sample_width;
    if (payload.length % bytesPerFrame !== 0) throw new Error("PCM 分段没有按采样帧对齐");
    const frameCount = payload.length / bytesPerFrame;
    const audioBuffer = context.createBuffer(metadata.channels, frameCount, metadata.sample_rate);
    const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
    for (let channel = 0; channel < metadata.channels; channel += 1) {
      const output = audioBuffer.getChannelData(channel);
      for (let frame = 0; frame < frameCount; frame += 1) {
        const sample = view.getInt16((frame * metadata.channels + channel) * 2, true);
        output[frame] = sample < 0 ? sample / 0x8000 : sample / 0x7fff;
      }
    }
    return this.scheduleBuffer(audioBuffer, metadata.rolling_gain_db ?? 0);
  }

  async appendWav(payload: ArrayBuffer): Promise<number | null> {
    const context = this.context;
    if (!context || !this.active || !payload.byteLength) return null;
    const audioBuffer = await context.decodeAudioData(payload.slice(0));
    return this.scheduleBuffer(audioBuffer);
  }

  get queuedEndTime(): number {
    return this.nextStartTime;
  }

  scheduleBoundary(time: number, callback: () => void): void {
    const context = this.context;
    if (!context || !this.active) return;
    const marker = context.createBufferSource();
    const generation = this.generation;
    marker.buffer = context.createBuffer(1, 1, context.sampleRate);
    marker.connect(context.destination);
    this.sources.add(marker);
    marker.onended = () => {
      this.sources.delete(marker);
      if (generation === this.generation) callback();
      if (generation === this.generation) this.resolveIfFinished();
    };
    marker.start(Math.max(time, context.currentTime + 0.005));
  }

  private scheduleBuffer(audioBuffer: AudioBuffer, gainDb = 0): number | null {
    const context = this.context;
    if (!context || !this.active) return null;
    const source = context.createBufferSource();
    const generation = this.generation;
    source.buffer = audioBuffer;
    const gain = context.createGain();
    const startTime = Math.max(this.nextStartTime, context.currentTime + (this.playbackStarted ? 0.02 : 0.08));
    const targetGain = Math.pow(10, gainDb / 20);
    gain.gain.setValueAtTime(0.0001, startTime);
    gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, targetGain), startTime + 0.015);
    source.connect(gain);
    gain.connect(context.destination);
    this.nextStartTime = startTime + audioBuffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      if (generation === this.generation) this.resolveIfFinished();
    };
    source.start(startTime);
    this.playbackStarted = true;
    return startTime;
  }

  finishStream(): void {
    this.streamFinished = true;
    this.resolveIfFinished();
  }

  waitForPlayback(): Promise<void> {
    return this.playbackPromise;
  }

  async pause(): Promise<void> {
    if (this.context?.state === "running") await this.context.suspend();
  }

  async resume(): Promise<void> {
    if (this.context?.state === "suspended") await this.context.resume();
  }

  stop(): void {
    this.generation += 1;
    this.sources.forEach((source) => {
      try {
        source.stop();
      } catch {}
    });
    this.sources.clear();
    this.active = false;
    this.streamFinished = true;
    this.resolvePlayback?.();
    this.resolvePlayback = null;
  }

  async dispose(): Promise<void> {
    this.stop();
    if (this.context && this.context.state !== "closed") await this.context.close();
    this.context = null;
  }

  private resolveIfFinished(): void {
    if (!this.active || !this.streamFinished || this.sources.size > 0) return;
    this.active = false;
    this.resolvePlayback?.();
    this.resolvePlayback = null;
  }
}
