import { Check, ChevronLeft, ChevronRight, CircleStop, Mic, Trash2, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AudioPlayer } from "./AudioPlayer";
import { Waveform } from "./Waveform";
import { normalizeAudioToWav } from "./audioFile";
import type { CharacterSummary, ReferenceAudioVersion, ReferencePlanItem } from "./types";

interface ReferenceAudioPanelProps {
  reference: ReferencePlanItem;
  disabled: boolean;
  compact?: boolean;
  hideEmpty?: boolean;
  waveformColor?: CharacterSummary["color"];
  onUpload: (referenceId: string, file: File, source: "uploaded" | "recorded") => Promise<void>;
  onActivate: (versionId: string) => Promise<void>;
  onReview: (versionId: string, decision: "accepted" | "rejected") => Promise<void>;
  onDelete: (versionId: string) => Promise<void>;
  onClear: () => Promise<void>;
  onError: (message: string) => void;
}

const sourceLabel: Record<ReferenceAudioVersion["source"], string> = {
  generated: "模型生成",
  uploaded: "用户上传",
  recorded: "用户录音",
  reused: "历史复用",
};

const decisionLabel: Record<ReferenceAudioVersion["decision"], string> = {
  provisional: "待确认",
  accepted: "标准参考",
  rejected: "已拒绝",
  superseded: "已替代",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "参考音频处理失败";
}

export function ReferenceAudioPanel({
  reference,
  disabled,
  compact = false,
  hideEmpty = false,
  waveformColor = "teal",
  onUpload,
  onActivate,
  onReview,
  onDelete,
  onClear,
  onError,
}: ReferenceAudioPanelProps) {
  const [recording, setRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [processing, setProcessing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);
  const recordingTimerRef = useRef<number | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);

  const versions = reference.audio_versions;
  const activeIndex = Math.max(0, versions.findIndex((version) => version.version_id === reference.active_audio_version_id));
  const activeVersion = versions[activeIndex] ?? null;
  const unavailable = disabled || processing;

  useEffect(() => () => {
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    if (recordingTimerRef.current !== null) window.clearInterval(recordingTimerRef.current);
  }, []);

  useEffect(() => {
    if (recordingSeconds >= 120 && recorderRef.current?.state === "recording") recorderRef.current.stop();
  }, [recordingSeconds]);

  async function submitAudio(source: Blob, fileName: string, origin: "uploaded" | "recorded", referenceId: string) {
    setProcessing(true);
    try {
      const wav = await normalizeAudioToWav(source, fileName);
      await onUpload(referenceId, wav, origin);
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setProcessing(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onError("当前浏览器不支持录音");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const referenceId = reference.reference_id;
      recordingStreamRef.current = stream;
      recorderRef.current = recorder;
      recordingChunksRef.current = [];
      recorder.ondataavailable = (event) => { if (event.data.size) recordingChunksRef.current.push(event.data); };
      recorder.onstop = () => {
        const blob = new Blob(recordingChunksRef.current, { type: recorder.mimeType || "audio/webm" });
        stream.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
        recorderRef.current = null;
        setRecording(false);
        if (recordingTimerRef.current !== null) window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
        void submitAudio(blob, `${referenceId}-recording.webm`, "recorded", referenceId);
      };
      recorder.start(250);
      setRecording(true);
      setRecordingSeconds(0);
      recordingTimerRef.current = window.setInterval(() => setRecordingSeconds((current) => current + 1), 1_000);
    } catch (error) {
      onError(error instanceof DOMException && error.name === "NotAllowedError" ? "麦克风权限未授权" : errorMessage(error));
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }

  async function moveVersion(offset: number) {
    const next = versions[activeIndex + offset];
    if (next) await onActivate(next.version_id);
  }

  return (
    <section className={`reference-audio-panel ${compact ? "reference-audio-panel--compact" : ""}`}>
      <div className="reference-audio-heading">
        <div><span>参考音频</span><strong>{versions.length ? `${versions.length} 个缓存版本` : "等待创建"}</strong></div>
        <div className="reference-capture-actions">
          <input ref={fileInputRef} className="hidden-file-input" type="file" accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm" aria-hidden="true" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; if (file) void submitAudio(file, file.name, "uploaded", reference.reference_id); }} />
          <button className="secondary-button" disabled={unavailable || recording} onClick={() => fileInputRef.current?.click()}><Upload size={14} />上传音频</button>
          <button className={`secondary-button record-button ${recording ? "recording" : ""}`} disabled={unavailable} onClick={() => recording ? stopRecording() : void startRecording()}>{recording ? <CircleStop size={14} /> : <Mic size={14} />}{recording ? `停止 ${Math.floor(recordingSeconds / 60)}:${String(recordingSeconds % 60).padStart(2, "0")}` : "录制参考"}</button>
          {versions.length > 0 && <button className="icon-button reference-cache-clear" title="清空该角色的全部参考音频缓存" disabled={unavailable || recording} onClick={() => void onClear()}><Trash2 size={14} /></button>}
        </div>
      </div>
      {activeVersion && (
        <div className="reference-version-nav" aria-label="参考音频版本切换">
          <button title="上一个参考音频" disabled={unavailable || activeIndex === 0} onClick={() => void moveVersion(-1)}><ChevronLeft size={15} /></button>
          <div><strong>{activeIndex + 1} / {versions.length}</strong><span>{sourceLabel[activeVersion.source]} · {decisionLabel[activeVersion.decision]} · {new Date(activeVersion.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span></div>
          <button title="下一个参考音频" disabled={unavailable || activeIndex >= versions.length - 1} onClick={() => void moveVersion(1)}><ChevronRight size={15} /></button>
          <button className="reference-version-accept" title="接受为当前标准参考" disabled={unavailable || activeVersion.decision === "accepted"} onClick={() => void onReview(activeVersion.version_id, "accepted")}><Check size={14} /></button>
          <button className="reference-version-reject" title="拒绝当前参考候选" disabled={unavailable || activeVersion.decision === "rejected"} onClick={() => void onReview(activeVersion.version_id, "rejected")}><X size={14} /></button>
          <button className="reference-version-delete" title="删除当前缓存音频" disabled={unavailable} onClick={() => void onDelete(activeVersion.version_id)}><Trash2 size={14} /></button>
        </div>
      )}
      {reference.audio_url ? <><Waveform src={reference.audio_url} color={waveformColor} /><AudioPlayer key={reference.active_audio_version_id ?? reference.audio_url} src={reference.audio_url} label={`${reference.display_name}参考音频试听`} /></> : !hideEmpty && <p className="reference-empty-audio">请生成、上传或录制一段参考音频。</p>}
    </section>
  );
}
