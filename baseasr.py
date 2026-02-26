###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import os
import time
import numpy as np

import queue
from queue import Queue
import torch.multiprocessing as mp

from basereal import BaseReal
from logger import logger


class BaseASR:
    def __init__(self, opt, parent:BaseReal = None):
        self.opt = opt
        self.parent = parent

        self.fps = opt.fps # 20 ms per frame
        self.sample_rate = 16000
        self.chunk = self.sample_rate // self.fps # 320 samples per chunk (20ms * 16000 / 1000)

        model_name = str(getattr(opt, "model", "") or "").lower()
        overflow_mode = os.getenv("LT_AUDIO_OVERFLOW_MODE", "auto").strip().lower()
        if overflow_mode not in {"auto", "drop", "block"}:
            overflow_mode = "auto"
        musetalk_prefer_reliable = (
            os.getenv("LT_MUSETALK_PREFER_RELIABLE_SPEECH", "1").strip().lower() in {"1", "true", "yes"}
        )
        # Legacy toggle still accepted for backward compatibility.
        if os.getenv("LT_ALLOW_BLOCK_FOR_MUSETALK", "").strip():
            musetalk_prefer_reliable = (
                os.getenv("LT_ALLOW_BLOCK_FOR_MUSETALK", "0").strip().lower() in {"1", "true", "yes"}
            )
        if overflow_mode == "auto":
            if model_name == "musetalk":
                # Reliable mode preserves speech integrity when musetalk cannot sustain real-time FPS.
                overflow_mode = "block" if musetalk_prefer_reliable else "drop"
            else:
                overflow_mode = "block"
        self.audio_overflow_mode = overflow_mode
        if model_name == "musetalk":
            if self.audio_overflow_mode == "block":
                logger.warning(
                    "musetalk reliable speech mode enabled (audio queue=block). Lip-sync may lag but speech won't be dropped."
                )
            else:
                logger.warning(
                    "musetalk realtime mode enabled (audio queue=drop). This may cut speech when inference is slower than real-time."
                )

        default_queue_seconds = "1.0" if self.audio_overflow_mode == "drop" else "8"
        max_audio_queue_seconds = os.getenv("LT_AUDIO_QUEUE_SECONDS", default_queue_seconds).strip()
        if model_name == "musetalk":
            musetalk_queue_override = os.getenv("LT_MUSETALK_AUDIO_QUEUE_SECONDS", "").strip()
            if musetalk_queue_override:
                max_audio_queue_seconds = musetalk_queue_override
        try:
            max_audio_queue_seconds = float(max_audio_queue_seconds)
        except ValueError:
            max_audio_queue_seconds = float(default_queue_seconds)
        max_audio_queue_seconds = max(0.1, max_audio_queue_seconds)

        if self.audio_overflow_mode == "drop":
            # Keep a short backlog in realtime mode to avoid multi-second lip latency.
            min_chunks = max(8, self.fps // 2)
            self.max_audio_queue_chunks = max(min_chunks, int(round(max_audio_queue_seconds * self.fps)))
        else:
            self.max_audio_queue_chunks = max(self.fps, int(round(max_audio_queue_seconds * self.fps)))
        self.queue = Queue(maxsize=self.max_audio_queue_chunks)
        self.output_queue = mp.Queue()
        self._dropped_audio_chunks = 0
        self._last_drop_log_ts = 0.0
        logger.info(
            "audio queue mode=%s, max=%d chunks (~%dms)",
            self.audio_overflow_mode,
            self.max_audio_queue_chunks,
            int(self.max_audio_queue_chunks * 1000 / self.fps),
        )

        self.batch_size = opt.batch_size

        self.frames = []
        self.stride_left_size = opt.l
        self.stride_right_size = opt.r
        # self.context_size = 10
        feat_queue_size_default = 2
        if model_name == "musetalk":
            feat_queue_size_default = 8 if self.audio_overflow_mode == "block" else 2
            feat_queue_size_raw = os.getenv("LT_MUSETALK_FEAT_QUEUE_SIZE", str(feat_queue_size_default)).strip()
        else:
            feat_queue_size_raw = os.getenv("LT_FEAT_QUEUE_SIZE", str(feat_queue_size_default)).strip()
        try:
            feat_queue_size = int(feat_queue_size_raw)
        except ValueError:
            feat_queue_size = feat_queue_size_default
        feat_queue_size = max(1, min(64, feat_queue_size))
        self.feat_queue_maxsize = feat_queue_size
        self.feat_queue = mp.Queue(self.feat_queue_maxsize)
        logger.info("asr feat queue max=%d", self.feat_queue_maxsize)

        #self.warm_up()

    def _drain_queue_nowait(self, q):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break

    def flush_talk(self):
        self._drain_queue_nowait(self.queue)
        self._drain_queue_nowait(self.output_queue)
        self._drain_queue_nowait(self.feat_queue)

    def put_audio_frame(self,audio_chunk,datainfo:dict): #16khz 20ms pcm
        if self.audio_overflow_mode == "block":
            # Preserve full speech content: back-pressure the TTS producer.
            while True:
                try:
                    self.queue.put((audio_chunk, datainfo), block=True, timeout=self.chunk / self.sample_rate)
                    return
                except queue.Full:
                    # Let consumer catch up.
                    continue

        dropped_now = 0
        keep_recent = max(1, self.fps // 2)

        # In realtime drop mode, proactively trim backlog even before queue reaches maxsize.
        if self.audio_overflow_mode == "drop":
            while self.queue.qsize() > keep_recent:
                try:
                    self.queue.get_nowait()
                    dropped_now += 1
                except queue.Empty:
                    break
                except Exception:
                    break

        try:
            self.queue.put_nowait((audio_chunk, datainfo))
            return
        except queue.Full:
            pass

        # Hard real-time mode: when overflow happens, aggressively discard stale audio
        # and keep only the most recent ~0.5s backlog to avoid multi-second lip delay.
        while self.queue.qsize() > keep_recent:
            try:
                self.queue.get_nowait()
                dropped_now += 1
            except queue.Empty:
                break
            except Exception:
                break
        try:
            self.queue.put_nowait((audio_chunk, datainfo))
        except queue.Full:
            # Still full in extreme overload; drop current chunk.
            dropped_now += 1

        if dropped_now > 0:
            self._dropped_audio_chunks += dropped_now
            now = time.time()
            if now - self._last_drop_log_ts >= 2:
                backlog_ms = int(self.queue.qsize() * 1000 / self.fps)
                logger.warning(
                    "audio queue overloaded; dropped=%d chunks total, qsize=%d (~%dms lag).",
                    self._dropped_audio_chunks,
                    self.queue.qsize(),
                    backlog_ms,
                )
                self._last_drop_log_ts = now

    #return frame:audio pcm; type: 0-normal speak, 1-silence; eventpoint:custom event sync with audio
    def get_audio_frame(self):        
        try:
            frame,eventpoint = self.queue.get(block=True, timeout=self.chunk / self.sample_rate)
            type = 0
            #print(f'[INFO] get frame {frame.shape}')
        except queue.Empty:
            if self.parent and self.parent.curr_state>1: #播放自定义音频
                frame = self.parent.get_audio_stream(self.parent.curr_state)
                type = self.parent.curr_state
            else:
                frame = np.zeros(self.chunk, dtype=np.float32)
                type = 1
            eventpoint = None

        return frame,type,eventpoint 

    #return frame:audio pcm; type: 0-normal speak, 1-silence; eventpoint:custom event sync with audio
    def get_audio_out(self): 
        return self.output_queue.get()
    
    def warm_up(self):
        for _ in range(self.stride_left_size + self.stride_right_size):
            audio_frame,type,eventpoint=self.get_audio_frame()
            self.frames.append(audio_frame)
            self.output_queue.put((audio_frame,type,eventpoint))
        for _ in range(self.stride_left_size):
            self.output_queue.get()

    def run_step(self):
        pass

    def get_next_feat(self,block,timeout):        
        return self.feat_queue.get(block,timeout)
