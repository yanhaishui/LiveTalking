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

import math
import torch
import numpy as np

import subprocess
import os
import time
import cv2
import glob
import resampy

import queue
from queue import Queue
from threading import Thread, Event
from io import BytesIO
import soundfile as sf

import asyncio
from av import AudioFrame, VideoFrame

import av
from fractions import Fraction

from ttsreal import EdgeTTS,SovitsTTS,XTTS,CosyVoiceTTS,FishTTS,TencentTTS,DoubaoTTS,IndexTTS2,AzureTTS,ElevenLabsTTS
from logger import logger

from tqdm import tqdm
def read_imgs(img_list):
    frames = []
    logger.info('reading images...')
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames

def play_audio(quit_event,audio_queue):        
    import pyaudio
    p = pyaudio.PyAudio()
    sample_rate = 16000
    channels = 1
    frames_per_buffer = sample_rate // 50  # 20ms
    try:
        frames_per_buffer = int(os.getenv("LT_AUDIO_FRAMES_PER_BUFFER", str(frames_per_buffer)).strip())
    except Exception:
        frames_per_buffer = sample_rate // 50
    frames_per_buffer = max(160, min(4096, frames_per_buffer))
    prefer_non_hdmi = os.getenv("LT_AUDIO_PREFER_NON_HDMI", "1").strip().lower() in {"1", "true", "yes"}
    selected_device = None
    selected_reason = "none"

    def _collect_output_devices():
        devices = []
        for idx in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(idx)
            except Exception:
                continue
            max_channels = int(info.get("maxOutputChannels", 0))
            if max_channels >= channels:
                devices.append(
                    {
                        "index": idx,
                        "name": str(info.get("name", "")),
                        "max_channels": max_channels,
                    }
                )
        return devices

    def _pick_output_device():
        devices = _collect_output_devices()
        if not devices:
            return None, "no_output_devices", devices

        env_idx = os.getenv("LT_AUDIO_OUTPUT_INDEX", "").strip()
        if env_idx:
            try:
                wanted = int(env_idx)
                for d in devices:
                    if d["index"] == wanted:
                        return d, "env_index", devices
                logger.warning("LT_AUDIO_OUTPUT_INDEX=%s not found in output devices.", env_idx)
            except ValueError:
                logger.warning("LT_AUDIO_OUTPUT_INDEX is not an integer: %s", env_idx)

        env_name = os.getenv("LT_AUDIO_OUTPUT_DEVICE", "").strip().lower()
        if env_name:
            for d in devices:
                if env_name in d["name"].lower():
                    return d, "env_name", devices
            logger.warning("LT_AUDIO_OUTPUT_DEVICE='%s' not matched in output devices.", env_name)

        default_device = None
        try:
            default_info = p.get_default_output_device_info()
            default_idx = int(default_info.get("index"))
            for d in devices:
                if d["index"] == default_idx:
                    default_device = d
                    break
        except Exception:
            default_device = None

        if default_device is not None:
            if prefer_non_hdmi and "hdmi" in default_device["name"].lower():
                non_hdmi = [d for d in devices if "hdmi" not in d["name"].lower()]
                if non_hdmi:
                    score_words = ["speaker", "headphone", "realtek", "cable input", "cable in", "vb-audio"]

                    def _score(item):
                        name = item["name"].lower()
                        score = 0
                        for pos, word in enumerate(score_words):
                            if word in name:
                                score += (100 - pos * 10)
                        score += item["max_channels"]
                        return score

                    non_hdmi.sort(key=_score, reverse=True)
                    return non_hdmi[0], "default_hdmi_fallback_non_hdmi", devices
            return default_device, "default", devices

        score_words = ["speaker", "headphone", "realtek", "cable input", "cable in", "vb-audio"]

        def _score(item):
            name = item["name"].lower()
            score = 0
            for pos, word in enumerate(score_words):
                if word in name:
                    score += (100 - pos * 10)
            if prefer_non_hdmi and "hdmi" in name:
                score -= 50
            score += item["max_channels"]
            return score

        devices.sort(key=_score, reverse=True)
        return devices[0], "best_available", devices

    def _open_output_stream(device):
        open_kwargs = {
            "rate": sample_rate,
            "channels": channels,
            "format": pyaudio.paInt16,
            "output": True,
            "frames_per_buffer": frames_per_buffer,
        }
        if device is not None:
            open_kwargs["output_device_index"] = device["index"]
        try:
            return p.open(**open_kwargs), device
        except OSError as e:
            if "output_device_index" in open_kwargs:
                logger.warning(
                    "open audio output failed for idx=%d (%s): %s; fallback to PortAudio default.",
                    device["index"],
                    device["name"],
                    e,
                )
                open_kwargs.pop("output_device_index", None)
                return p.open(**open_kwargs), None
            raise

    selected_device, selected_reason, available_devices = _pick_output_device()
    if selected_device is not None:
        logger.info(
            "audio output selected: idx=%d name=%s channels=%d reason=%s (available=%d)",
            selected_device["index"],
            selected_device["name"],
            selected_device["max_channels"],
            selected_reason,
            len(available_devices),
        )
    else:
        logger.warning("no explicit output device selected; fallback to PortAudio default.")

    stream, opened_device = _open_output_stream(selected_device)
    stream.start_stream()
    try:
        while not quit_event.is_set():
            try:
                audio_bytes = audio_queue.get(block=True, timeout=0.1)
            except queue.Empty:
                continue
            try:
                stream.write(audio_bytes)
            except Exception as e:
                logger.warning("audio stream write failed: %s; trying to reopen stream.", e)
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
                stream, opened_device = _open_output_stream(selected_device if opened_device is not None else None)
                stream.start_stream()
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        p.terminate()

class BaseReal:
    def __init__(self, opt):
        self.opt = opt
        self.sample_rate = 16000
        self.chunk = self.sample_rate // opt.fps # 320 samples per chunk (20ms * 16000 / 1000)
        self.sessionid = self.opt.sessionid

        if opt.tts == "edgetts":
            self.tts = EdgeTTS(opt,self)
        elif opt.tts == "gpt-sovits":
            self.tts = SovitsTTS(opt,self)
        elif opt.tts == "xtts":
            self.tts = XTTS(opt,self)
        elif opt.tts == "cosyvoice":
            self.tts = CosyVoiceTTS(opt,self)
        elif opt.tts == "fishtts":
            self.tts = FishTTS(opt,self)
        elif opt.tts == "tencent":
            self.tts = TencentTTS(opt,self)
        elif opt.tts == "doubao":
            self.tts = DoubaoTTS(opt,self)
        elif opt.tts == "indextts2":
            self.tts = IndexTTS2(opt,self)
        elif opt.tts == "azuretts":
            self.tts = AzureTTS(opt,self)
        elif opt.tts == "elevenlabs":
            self.tts = ElevenLabsTTS(opt,self)

        self.speaking = False

        self.recording = False
        self._record_video_pipe = None
        self._record_audio_pipe = None
        self.width = self.height = 0

        self.curr_state=0
        self.custom_img_cycle = {}
        self.custom_audio_cycle = {}
        self.custom_audio_index = {}
        self.custom_index = {}
        self.custom_opt = {}
        self.__loadcustom()

    def put_msg_txt(self,msg,datainfo:dict={}):
        self.tts.put_msg_txt(msg,datainfo)
    
    def put_audio_frame(self,audio_chunk,datainfo:dict={}): #16khz 20ms pcm
        self.asr.put_audio_frame(audio_chunk,datainfo)

    def put_audio_file(self,filebyte,datainfo:dict={}): 
        input_stream = BytesIO(filebyte)
        stream = self.__create_bytes_stream(input_stream)
        streamlen = stream.shape[0]
        idx=0
        while streamlen >= self.chunk:  #and self.state==State.RUNNING
            self.put_audio_frame(stream[idx:idx+self.chunk],datainfo)
            streamlen -= self.chunk
            idx += self.chunk
    
    def __create_bytes_stream(self,byte_stream):
        #byte_stream=BytesIO(buffer)
        stream, sample_rate = sf.read(byte_stream) # [T*sample_rate,] float64
        logger.info(f'[INFO]put audio stream {sample_rate}: {stream.shape}')
        stream = stream.astype(np.float32)

        if stream.ndim > 1:
            logger.info(f'[WARN] audio has {stream.shape[1]} channels, only use the first.')
            stream = stream[:, 0]
    
        if sample_rate != self.sample_rate and stream.shape[0]>0:
            logger.info(f'[WARN] audio sample rate is {sample_rate}, resampling into {self.sample_rate}.')
            stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

        return stream

    def flush_talk(self):
        self.tts.flush_talk()
        self.asr.flush_talk()

    def is_speaking(self)->bool:
        return self.speaking
    
    def __loadcustom(self):
        for item in self.opt.customopt:
            logger.info(item)
            input_img_list = glob.glob(os.path.join(item['imgpath'], '*.[jpJP][pnPN]*[gG]'))
            input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
            self.custom_img_cycle[item['audiotype']] = read_imgs(input_img_list)
            self.custom_audio_cycle[item['audiotype']], sample_rate = sf.read(item['audiopath'], dtype='float32')
            self.custom_audio_index[item['audiotype']] = 0
            self.custom_index[item['audiotype']] = 0
            self.custom_opt[item['audiotype']] = item

    def init_customindex(self):
        self.curr_state=0
        for key in self.custom_audio_index:
            self.custom_audio_index[key]=0
        for key in self.custom_index:
            self.custom_index[key]=0

    def notify(self,eventpoint):
        logger.info("notify:%s",eventpoint)

    def start_recording(self):
        """Start recording output."""
        if self.recording:
            return

        command = ['ffmpeg',
                    '-y', '-an',
                    '-f', 'rawvideo',
                    '-vcodec','rawvideo',
                    '-pix_fmt', 'bgr24', # pixel format
                    '-s', "{}x{}".format(self.width, self.height),
                    '-r', str(25),
                    '-i', '-',
                    '-pix_fmt', 'yuv420p', 
                    '-vcodec', "h264",
                    #'-f' , 'flv',                  
                    f'temp{self.opt.sessionid}.mp4']
        self._record_video_pipe = subprocess.Popen(command, shell=False, stdin=subprocess.PIPE)

        acommand = ['ffmpeg',
                    '-y', '-vn',
                    '-f', 's16le',
                    #'-acodec','pcm_s16le',
                    '-ac', '1',
                    '-ar', '16000',
                    '-i', '-',
                    '-acodec', 'aac',
                    #'-f' , 'wav',                  
                    f'temp{self.opt.sessionid}.aac']
        self._record_audio_pipe = subprocess.Popen(acommand, shell=False, stdin=subprocess.PIPE)

        self.recording = True
        # self.recordq_video.queue.clear()
        # self.recordq_audio.queue.clear()
        # self.container = av.open(path, mode="w")
    
        # process_thread = Thread(target=self.record_frame, args=())
        # process_thread.start()
    
    def record_video_data(self,image):
        if self.width == 0:
            print("image.shape:",image.shape)
            self.height,self.width,_ = image.shape
        if self.recording:
            self._record_video_pipe.stdin.write(image.tostring())

    def record_audio_data(self,frame):
        if self.recording:
            self._record_audio_pipe.stdin.write(frame.tostring())
    
    # def record_frame(self): 
    #     videostream = self.container.add_stream("libx264", rate=25)
    #     videostream.codec_context.time_base = Fraction(1, 25)
    #     audiostream = self.container.add_stream("aac")
    #     audiostream.codec_context.time_base = Fraction(1, 16000)
    #     init = True
    #     framenum = 0       
    #     while self.recording:
    #         try:
    #             videoframe = self.recordq_video.get(block=True, timeout=1)
    #             videoframe.pts = framenum #int(round(framenum*0.04 / videostream.codec_context.time_base))
    #             videoframe.dts = videoframe.pts
    #             if init:
    #                 videostream.width = videoframe.width
    #                 videostream.height = videoframe.height
    #                 init = False
    #             for packet in videostream.encode(videoframe):
    #                 self.container.mux(packet)
    #             for k in range(2):
    #                 audioframe = self.recordq_audio.get(block=True, timeout=1)
    #                 audioframe.pts = int(round((framenum*2+k)*0.02 / audiostream.codec_context.time_base))
    #                 audioframe.dts = audioframe.pts
    #                 for packet in audiostream.encode(audioframe):
    #                     self.container.mux(packet)
    #             framenum += 1
    #         except queue.Empty:
    #             print('record queue empty,')
    #             continue
    #         except Exception as e:
    #             print(e)
    #             #break
    #     for packet in videostream.encode(None):
    #         self.container.mux(packet)
    #     for packet in audiostream.encode(None):
    #         self.container.mux(packet)
    #     self.container.close()
    #     self.recordq_video.queue.clear()
    #     self.recordq_audio.queue.clear()
    #     print('record thread stop')
		
    def stop_recording(self):
        """Stop recording output."""
        if not self.recording:
            return
        self.recording = False 
        self._record_video_pipe.stdin.close()  #wait() 
        self._record_video_pipe.wait()
        self._record_audio_pipe.stdin.close()
        self._record_audio_pipe.wait()
        cmd_combine_audio = f"ffmpeg -y -i temp{self.opt.sessionid}.aac -i temp{self.opt.sessionid}.mp4 -c:v copy -c:a copy data/record.mp4"
        os.system(cmd_combine_audio) 
        #os.remove(output_path)

    def mirror_index(self,size, index):
        #size = len(self.coord_list_cycle)
        turn = index // size
        res = index % size
        if turn % 2 == 0:
            return res
        else:
            return size - res - 1 
    
    def get_audio_stream(self,audiotype):
        idx = self.custom_audio_index[audiotype]
        stream = self.custom_audio_cycle[audiotype][idx:idx+self.chunk]
        self.custom_audio_index[audiotype] += self.chunk
        if self.custom_audio_index[audiotype]>=self.custom_audio_cycle[audiotype].shape[0]:
            self.curr_state = 1  # current clip finished, switch back to silent state
        return stream
    
    def set_custom_state(self,audiotype, reinit=True):
        print('set_custom_state:',audiotype)
        if self.custom_audio_index.get(audiotype) is None:
            return
        self.curr_state = audiotype
        if reinit:
            self.custom_audio_index[audiotype] = 0
            self.custom_index[audiotype] = 0

    def process_frames(self,quit_event,loop=None,audio_track=None,video_track=None):
        enable_transition = os.getenv("LT_ENABLE_TRANSITION", "1").strip().lower() not in {"0", "false", "no"}
        transition_duration = 0.16
        speaking_hangover_sec = 0.35
        state_log_cooldown_sec = 0.4
        try:
            transition_duration = float(os.getenv("LT_TRANSITION_SEC", str(transition_duration)).strip())
        except Exception:
            pass
        try:
            speaking_hangover_sec = float(os.getenv("LT_SPEAKING_HANGOVER_SEC", str(speaking_hangover_sec)).strip())
        except Exception:
            pass
        try:
            state_log_cooldown_sec = float(
                os.getenv("LT_STATE_SWITCH_LOG_COOLDOWN_SEC", str(state_log_cooldown_sec)).strip()
            )
        except Exception:
            pass
        transition_duration = max(0.0, min(2.0, transition_duration))
        speaking_hangover_sec = max(0.0, min(2.0, speaking_hangover_sec))
        state_log_cooldown_sec = max(0.0, min(5.0, state_log_cooldown_sec))

        realtime_audio_queue = getattr(self, "realtime_audio_queue", None)
        use_realtime_audio = realtime_audio_queue is not None
        if use_realtime_audio:
            logger.info("process_frames realtime-audio mode enabled.")

        _last_speaking = False
        _transition_start = time.time()
        _last_switch_log_ts = 0.0
        _last_silent_frame = None
        _last_speaking_frame = None
        _last_voice_ts = 0.0

        if self.opt.transport=='virtualcam':
            import pyvirtualcam
            vircam = None
            vircam_open_failed_at = 0.0

            vcam_audio_queue_seconds = 0.6
            vcam_audio_keep_seconds = 0.2
            try:
                vcam_audio_queue_seconds = float(
                    os.getenv("LT_VCAM_AUDIO_QUEUE_SECONDS", str(vcam_audio_queue_seconds)).strip()
                )
            except Exception:
                pass
            try:
                vcam_audio_keep_seconds = float(
                    os.getenv("LT_VCAM_AUDIO_KEEP_SECONDS", str(vcam_audio_keep_seconds)).strip()
                )
            except Exception:
                pass
            vcam_audio_queue_seconds = max(0.1, min(10.0, vcam_audio_queue_seconds))
            vcam_audio_keep_seconds = max(0.04, min(vcam_audio_queue_seconds, vcam_audio_keep_seconds))
            vcam_audio_qmax_chunks = max(8, int(round(vcam_audio_queue_seconds * self.fps)))
            vcam_audio_keep_chunks = max(2, min(vcam_audio_qmax_chunks - 1, int(round(vcam_audio_keep_seconds * self.fps))))
            audio_tmp = queue.Queue(maxsize=vcam_audio_qmax_chunks)
            _audio_drop_total = 0
            _last_audio_drop_log_ts = 0.0
            logger.info(
                "virtualcam audio queue max=%d chunks (~%dms), keep=%d chunks (~%dms)",
                vcam_audio_qmax_chunks,
                int(vcam_audio_qmax_chunks * 1000 / self.fps),
                vcam_audio_keep_chunks,
                int(vcam_audio_keep_chunks * 1000 / self.fps),
            )
            audio_thread = Thread(target=play_audio, args=(quit_event,audio_tmp,), daemon=True, name="pyaudio_stream")
            audio_thread.start()

            def _push_virtualcam_audio(audio_bytes):
                nonlocal _audio_drop_total, _last_audio_drop_log_ts
                dropped_now = 0

                # Keep realtime: proactively trim stale audio backlog.
                while audio_tmp.qsize() > vcam_audio_keep_chunks:
                    try:
                        audio_tmp.get_nowait()
                        dropped_now += 1
                    except queue.Empty:
                        break
                    except Exception:
                        break

                try:
                    audio_tmp.put_nowait(audio_bytes)
                except queue.Full:
                    while audio_tmp.qsize() > vcam_audio_keep_chunks:
                        try:
                            audio_tmp.get_nowait()
                            dropped_now += 1
                        except queue.Empty:
                            break
                        except Exception:
                            break
                    try:
                        audio_tmp.put_nowait(audio_bytes)
                    except queue.Full:
                        dropped_now += 1

                if dropped_now > 0:
                    _audio_drop_total += dropped_now
                    now_ts = time.time()
                    if now_ts - _last_audio_drop_log_ts >= 2:
                        backlog_ms = int(audio_tmp.qsize() * 1000 / self.fps)
                        logger.warning(
                            "virtualcam audio backlog; dropped=%d total, qsize=%d (~%dms).",
                            _audio_drop_total,
                            audio_tmp.qsize(),
                            backlog_ms,
                        )
                        _last_audio_drop_log_ts = now_ts
        
        def _emit_audio_frame(audio_frame):
            frame,type,eventpoint = audio_frame
            frame = (frame * 32767).astype(np.int16)

            if self.opt.transport=='virtualcam':
                _push_virtualcam_audio(frame.tobytes())
            else: #webrtc
                new_frame = AudioFrame(format='s16', layout='mono', samples=frame.shape[0])
                new_frame.planes[0].update(frame.tobytes())
                new_frame.sample_rate=16000
                asyncio.run_coroutine_threadsafe(audio_track._queue.put((new_frame,eventpoint)), loop)
            self.record_audio_data(frame)

        get_timeout = 0.02 if use_realtime_audio else 1

        while not quit_event.is_set():
            if use_realtime_audio:
                while True:
                    try:
                        _emit_audio_frame(realtime_audio_queue.get_nowait())
                    except queue.Empty:
                        break
                    except Exception:
                        break
                while True:
                    try:
                        if self.res_frame_queue.qsize() <= 1:
                            break
                        self.res_frame_queue.get_nowait()
                    except queue.Empty:
                        break
                    except Exception:
                        break
            try:
                res_frame,idx,audio_frames = self.res_frame_queue.get(block=True, timeout=get_timeout)
            except queue.Empty:
                continue

            now = time.time()
            raw_silence = (audio_frames[0][1] != 0 and audio_frames[1][1] != 0)
            if not raw_silence:
                _last_voice_ts = now
            current_speaking = (not raw_silence) or ((now - _last_voice_ts) <= speaking_hangover_sec)
            self.speaking = current_speaking

            if enable_transition and current_speaking != _last_speaking:
                if now - _last_switch_log_ts >= state_log_cooldown_sec:
                    logger.info(
                        "state switch: %s -> %s",
                        "speaking" if _last_speaking else "silent",
                        "speaking" if current_speaking else "silent",
                    )
                    _last_switch_log_ts = now
                _transition_start = now
            _last_speaking = current_speaking

            if raw_silence and not current_speaking:
                audiotype = audio_frames[0][1]
                if self.custom_index.get(audiotype) is not None:
                    mirindex = self.mirror_index(len(self.custom_img_cycle[audiotype]), self.custom_index[audiotype])
                    target_frame = self.custom_img_cycle[audiotype][mirindex]
                    self.custom_index[audiotype] += 1
                else:
                    target_frame = self.frame_list_cycle[idx]

                if enable_transition and transition_duration > 0 and (time.time() - _transition_start) < transition_duration and _last_speaking_frame is not None:
                    alpha = min(1.0, (time.time() - _transition_start) / transition_duration)
                    combine_frame = cv2.addWeighted(_last_speaking_frame, 1 - alpha, target_frame, alpha, 0)
                else:
                    combine_frame = target_frame
                if enable_transition:
                    _last_silent_frame = combine_frame.copy()
            elif raw_silence and current_speaking:
                # In short inter-segment gaps, hold speaking frame to avoid abrupt freeze.
                if enable_transition and _last_speaking_frame is not None:
                    combine_frame = _last_speaking_frame.copy()
                else:
                    combine_frame = self.frame_list_cycle[idx]
            else:
                try:
                    current_frame = self.paste_back_frame(res_frame,idx)
                except Exception as e:
                    logger.warning(f"paste_back_frame error: {e}")
                    continue
                if enable_transition and transition_duration > 0 and (time.time() - _transition_start) < transition_duration and _last_silent_frame is not None:
                    alpha = min(1.0, (time.time() - _transition_start) / transition_duration)
                    combine_frame = cv2.addWeighted(_last_silent_frame, 1 - alpha, current_frame, alpha, 0)
                else:
                    combine_frame = current_frame
                if enable_transition:
                    _last_speaking_frame = combine_frame.copy()

            cv2.putText(combine_frame, "LiveTalking", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (128,128,128), 1)
            if self.opt.transport=='virtualcam':
                if vircam is None:
                    height, width, _ = combine_frame.shape
                    try:
                        vircam = pyvirtualcam.Camera(
                            width=width,
                            height=height,
                            fps=25,
                            fmt=pyvirtualcam.PixelFormat.BGR,
                            print_fps=True,
                        )
                        logger.info(f"virtual camera started: {width}x{height}@25")
                    except Exception as e:
                        now = time.time()
                        # Keep the worker alive and retry later instead of killing the whole frame loop.
                        if now - vircam_open_failed_at > 3:
                            logger.error(f"virtual camera open failed, will retry: {e}")
                            vircam_open_failed_at = now
                if vircam is not None:
                    vircam.send(combine_frame)
            else: #webrtc
                image = combine_frame
                new_frame = VideoFrame.from_ndarray(image, format="bgr24")
                asyncio.run_coroutine_threadsafe(video_track._queue.put((new_frame,None)), loop)
            self.record_video_data(combine_frame)

            if not use_realtime_audio:
                for audio_frame in audio_frames:
                    _emit_audio_frame(audio_frame)
            if self.opt.transport=='virtualcam' and vircam is not None:
                vircam.sleep_until_next_frame()
        if self.opt.transport=='virtualcam':
            audio_thread.join()
            if vircam is not None:
                vircam.close()
        logger.info('basereal process_frames thread stop')
    
    # def process_custom(self,audiotype:int,idx:int):
    #     if self.curr_state!=audiotype: #娴犲孩甯归悶鍡楀瀼閸掓澘褰涢幘?
    #         if idx in self.switch_pos:  #閸︺劌宕遍悙閫涚秴缂冾喖褰叉禒銉ュ瀼閹?
    #             self.curr_state=audiotype
    #             self.custom_index=0
    #     else:
    #         self.custom_index+=1
