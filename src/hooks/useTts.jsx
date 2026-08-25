import React, { createContext, useContext, useRef, useState, useCallback, useEffect } from "react";

const TTS_SETTINGS_KEY = "abcyesno:tts";

const DEFAULT_TTS_SETTINGS = {
  autoRead: false,
  voice: "zh-CN-XiaoxiaoNeural",
  rate: 1.0,
};

// 静态中文神经语音清单（微软云端 edge-tts，不依赖本机语音包）
const TTS_VOICE_OPTIONS = [
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓（女·默认）" },
  { value: "zh-CN-YunxiNeural", label: "云希（男）" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊（女·俏皮）" },
  { value: "zh-CN-YunyangNeural", label: "云扬（男·新闻）" },
  { value: "zh-CN-XiaochenNeural", label: "晓辰（女）" },
  { value: "zh-CN-YunjianNeural", label: "云健（男）" },
];

export { DEFAULT_TTS_SETTINGS, TTS_VOICE_OPTIONS };

function loadTtsSettings() {
  try {
    const raw = localStorage.getItem(TTS_SETTINGS_KEY);
    if (!raw) return { ...DEFAULT_TTS_SETTINGS };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_TTS_SETTINGS, ...parsed };
  } catch {
    return { ...DEFAULT_TTS_SETTINGS };
  }
}

const TtsContext = createContext(null);

/**
 * TtsProvider — 全局 TTS 朗读控制器。
 *
 * 用 edge-tts 云端语音（经 window.hermes.synthesizeSpeech → 后端 /api/tts 生成
 * mp3），前端用单一 <audio> 实例播放，保证同一时刻只播一条、全局状态同步。
 *
 * 暴露：
 *   ttsSettings      { autoRead, voice, rate }（持久化到 localStorage）
 *   updateTtsSettings(partial)
 *   mute / setMuted  全局静音（仅影响自动朗读 + 静音时停当前播放）
 *   isPlaying        是否正在播放
 *   currentMsgId     当前正在朗读的消息 id（用于按钮高亮同步）
 *   speak(text, id)  朗读；返回 Promise<boolean>
 *   stop()           停止当前播放
 */
export function TtsProvider({ children }) {
  const [ttsSettings, setTtsSettings] = useState(loadTtsSettings);
  const [mute, setMute] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentMsgId, setCurrentMsgId] = useState(null);
  const audioRef = useRef(null);
  if (!audioRef.current) audioRef.current = new Audio();

  const updateTtsSettings = useCallback((partial) => {
    setTtsSettings((prev) => {
      const next = { ...prev, ...partial };
      try {
        localStorage.setItem(TTS_SETTINGS_KEY, JSON.stringify(next));
      } catch {}
      return next;
    });
  }, []);

  const stop = useCallback(() => {
    const a = audioRef.current;
    if (a) {
      a.pause();
      try { a.currentTime = 0; } catch {}
    }
    setIsPlaying(false);
    setCurrentMsgId(null);
  }, []);

  const speak = useCallback(async (text, msgId) => {
    if (!text || !text.trim()) return false;
    // 切换前先停掉当前（避免串音）
    const a = audioRef.current;
    if (a) {
      a.pause();
      try { a.currentTime = 0; } catch {}
    }
    setIsPlaying(false);
    setCurrentMsgId(null);

    if (!window.hermes || !window.hermes.synthesizeSpeech) {
      console.warn("synthesizeSpeech 不可用（非 Electron 环境？）");
      return false;
    }
    try {
      const res = await window.hermes.synthesizeSpeech(
        text,
        ttsSettings.voice,
        ttsSettings.rate
      );
      if (!res || res.error) {
        console.warn("TTS 生成失败:", res && res.error);
        return false;
      }
      const mime = res.mime || "audio/mpeg";
      a.src = `data:${mime};base64,${res.audio}`;
      a.onended = () => {
        setIsPlaying(false);
        setCurrentMsgId(null);
      };
      a.onerror = () => {
        setIsPlaying(false);
        setCurrentMsgId(null);
      };
      await a.play();
      setIsPlaying(true);
      setCurrentMsgId(msgId);
      return true;
    } catch (err) {
      console.error("speak failed", err);
      return false;
    }
  }, [ttsSettings.voice, ttsSettings.rate]);

  const setMuted = useCallback((b) => {
    setMute(b);
    if (b) {
      const a = audioRef.current;
      if (a) a.pause();
      setIsPlaying(false);
      setCurrentMsgId(null);
    }
  }, []);

  // 卸载时停止，避免残留播放
  useEffect(() => {
    return () => {
      const a = audioRef.current;
      if (a) {
        a.pause();
        try { a.src = ""; } catch {}
      }
    };
  }, []);

  const value = {
    ttsSettings,
    updateTtsSettings,
    mute,
    setMuted,
    isPlaying,
    currentMsgId,
    speak,
    stop,
    voiceOptions: TTS_VOICE_OPTIONS,
  };

  return <TtsContext.Provider value={value}>{children}</TtsContext.Provider>;
}

export function useTts() {
  const ctx = useContext(TtsContext);
  if (!ctx) {
    throw new Error("useTts must be used within TtsProvider");
  }
  return ctx;
}
