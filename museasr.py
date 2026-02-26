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

import time
import os
import numpy as np

import queue
from queue import Queue
#import multiprocessing as mp
from baseasr import BaseASR
from musetalk.whisper.audio2feature import Audio2Feature
from logger import logger

class MuseASR(BaseASR):
    def __init__(self, opt, parent,audio_processor:Audio2Feature):
        super().__init__(opt,parent)
        self.audio_processor = audio_processor
        self._last_feat_drop_log_ts = 0.0
        self._dropped_feat_chunks = 0
        feat_mode = os.getenv("LT_MUSETALK_FEAT_OVERFLOW_MODE", "auto").strip().lower()
        if feat_mode not in {"auto", "drop", "block"}:
            feat_mode = "auto"
        if feat_mode == "auto":
            prefer_reliable = (
                os.getenv("LT_MUSETALK_PREFER_RELIABLE_SPEECH", "1").strip().lower() in {"1", "true", "yes"}
            )
            feat_mode = "block" if prefer_reliable else "drop"
        self._feat_overflow_mode = feat_mode
        logger.info("musetalk feat queue overflow mode=%s", self._feat_overflow_mode)

    def run_step(self):
        ############################################## extract audio feature ##############################################
        start_time = time.time()
        for _ in range(self.batch_size*2):
            audio_frame,type,eventpoint = self.get_audio_frame()
            self.frames.append(audio_frame)
            self.output_queue.put((audio_frame,type,eventpoint))
        
        if len(self.frames) <= self.stride_left_size + self.stride_right_size:
            return
        
        inputs = np.concatenate(self.frames) # [N * chunk]
        whisper_feature = self.audio_processor.audio2feat(inputs)
        # for feature in whisper_feature:
        #     self.audio_feats.append(feature)        
        #print(f"processing audio costs {(time.time() - start_time) * 1000}ms, inputs shape:{inputs.shape} whisper_feature len:{len(whisper_feature)}")
        whisper_chunks = self.audio_processor.feature2chunks(feature_array=whisper_feature,fps=self.fps/2,batch_size=self.batch_size,start=self.stride_left_size/2 )
        #print(f"whisper_chunks len:{len(whisper_chunks)},self.audio_feats len:{len(self.audio_feats)},self.output_queue len:{self.output_queue.qsize()}")
        #self.audio_feats = self.audio_feats[-(self.stride_left_size + self.stride_right_size):]
        if not self._push_feat_with_backpressure(whisper_chunks):
            # Drop current chunk when queue remains saturated; keep render thread alive.
            self._dropped_feat_chunks += 1
            now = time.time()
            if now - self._last_feat_drop_log_ts >= 2:
                logger.warning(
                    "asr feat queue saturated; dropped latest chunk total=%d to keep realtime.",
                    self._dropped_feat_chunks,
                )
                self._last_feat_drop_log_ts = now
        # discard the old part to save memory
        self.frames = self.frames[-(self.stride_left_size + self.stride_right_size):]

    def _push_feat_with_backpressure(self, whisper_chunks) -> bool:
        if self._feat_overflow_mode == "block":
            try:
                # Preserve full speech content when inference is slower than real-time.
                self.feat_queue.put(whisper_chunks, block=True, timeout=0.2)
                return True
            except queue.Full:
                # Keep trying with short sleeps instead of dropping aligned audio.
                for _ in range(5):
                    time.sleep(0.02)
                    try:
                        self.feat_queue.put(whisper_chunks, block=True, timeout=0.2)
                        return True
                    except queue.Full:
                        continue
                return False

        try:
            self.feat_queue.put_nowait(whisper_chunks)
            return True
        except queue.Full:
            pass

        # In low-FPS scenarios inference cannot keep up. Drop stale feature chunks and
        # aligned audio frames, then retry a few times without throwing.
        dropped_audio = 0
        dropped_feat = 0
        for _ in range(3):
            try:
                self.feat_queue.get_nowait()
                dropped_feat += 1
                for _ in range(self.batch_size * 2):
                    try:
                        self.output_queue.get_nowait()
                        dropped_audio += 1
                    except queue.Empty:
                        break
            except queue.Empty:
                pass
            except Exception:
                pass

            try:
                self.feat_queue.put_nowait(whisper_chunks)
                if dropped_feat > 0:
                    now = time.time()
                    if now - self._last_feat_drop_log_ts >= 1.5:
                        logger.warning(
                            "asr feat queue full; dropped %d stale chunk(s) and %d audio frames to keep realtime.",
                            dropped_feat,
                            dropped_audio,
                        )
                        self._last_feat_drop_log_ts = now
                return True
            except queue.Full:
                # Give the consumer thread a tiny chance to dequeue.
                time.sleep(0.003)
                continue

        try:
            self.feat_queue.put(whisper_chunks, block=True, timeout=0.01)
            return True
        except queue.Full:
            return False
