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

#from .utils import *
import subprocess
import os
import time
import torch.nn.functional as F
import cv2
import glob
import pickle
import copy

import queue
from queue import Queue
from threading import Thread, Event
import torch.multiprocessing as mp

from musetalk.utils.utils import get_file_type,get_video_fps,datagen
#from musetalk.utils.preprocessing import get_landmark_and_bbox,read_imgs,coord_placeholder
from musetalk.myutil import get_image_blending
from musetalk.utils.utils import load_all_model
from musetalk.whisper.audio2feature import Audio2Feature

from museasr import MuseASR
import asyncio
from av import AudioFrame, VideoFrame
from basereal import BaseReal

from tqdm import tqdm
from logger import logger

def load_model():
    # load model weights
    vae, unet, pe = load_all_model()
    device = torch.device("cuda" if torch.cuda.is_available() else ("mps" if (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()) else "cpu"))
    if device.type == "cuda":
        # Prefer fast SDPA kernels by default; allow forcing math-only via env for stability.
        sdpa_mode = os.getenv("MUSETALK_SDPA_MODE", "auto").strip().lower()
        try:
            if sdpa_mode in {"math", "safe"}:
                torch.backends.cuda.enable_cudnn_sdp(False)
                torch.backends.cuda.enable_flash_sdp(False)
                torch.backends.cuda.enable_mem_efficient_sdp(False)
                torch.backends.cuda.enable_math_sdp(True)
                logger.info("SDPA backend configured: math-only (cudnn/flash/mem-efficient disabled)")
            else:
                torch.backends.cuda.enable_math_sdp(True)
                torch.backends.cuda.enable_mem_efficient_sdp(True)
                torch.backends.cuda.enable_flash_sdp(True)
                # cuDNN SDPA plan search is unstable on some GPU/driver combos.
                torch.backends.cuda.enable_cudnn_sdp(False)
                logger.info("SDPA backend configured: auto-fast (flash/mem-efficient enabled, cudnn disabled)")
        except Exception as e:
            logger.warning(f"failed to configure SDPA backend, fallback to defaults: {e}")
    timesteps = torch.tensor([0], device=device)
    pe = pe.half().to(device)
    vae.vae = vae.vae.half().to(device)
    #vae.vae.share_memory().to(device)
    unet.model = unet.model.half().to(device)
    #unet.model.share_memory()
    # Initialize audio processor and Whisper model
    audio_processor = Audio2Feature(model_path="./models/whisper")
    return vae, unet, pe, timesteps, audio_processor

def load_avatar(avatar_id):
    #self.video_path = '' #video_path
    #self.bbox_shift = opt.bbox_shift
    avatar_path = f"./data/avatars/{avatar_id}"
    full_imgs_path = f"{avatar_path}/full_imgs" 
    coords_path = f"{avatar_path}/coords.pkl"
    latents_out_path= f"{avatar_path}/latents.pt"
    video_out_path = f"{avatar_path}/vid_output/"
    mask_out_path =f"{avatar_path}/mask"
    mask_coords_path =f"{avatar_path}/mask_coords.pkl"
    avatar_info_path = f"{avatar_path}/avator_info.json"
    # self.avatar_info = {
    #     "avatar_id":self.avatar_id,
    #     "video_path":self.video_path,
    #     "bbox_shift":self.bbox_shift   
    # }

    try:
        input_latent_list_cycle = torch.load(latents_out_path, weights_only=True)
    except TypeError:
        # Backward compatibility for older torch without weights_only.
        input_latent_list_cycle = torch.load(latents_out_path)
    with open(coords_path, 'rb') as f:
        coord_list_cycle = pickle.load(f)
    input_img_list = glob.glob(os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]'))
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = read_imgs(input_img_list)
    with open(mask_coords_path, 'rb') as f:
        mask_coords_list_cycle = pickle.load(f)
    input_mask_list = glob.glob(os.path.join(mask_out_path, '*.[jpJP][pnPN]*[gG]'))
    input_mask_list = sorted(input_mask_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    mask_list_cycle = read_imgs(input_mask_list)
    return frame_list_cycle,mask_list_cycle,coord_list_cycle,mask_coords_list_cycle,input_latent_list_cycle

@torch.no_grad()
def warm_up(batch_size,model):
    # 预热函数
    logger.info('warmup model...')
    vae, unet, pe, timesteps, audio_processor = model
    #batch_size = 16
    #timesteps = torch.tensor([0], device=unet.device)
    whisper_batch = np.ones((batch_size, 50, 384), dtype=np.uint8)
    latent_batch = torch.ones(batch_size, 8, 32, 32).to(unet.device)

    audio_feature_batch = torch.from_numpy(whisper_batch)
    audio_feature_batch = audio_feature_batch.to(device=unet.device, dtype=unet.model.dtype)
    audio_feature_batch = pe(audio_feature_batch)
    latent_batch = latent_batch.to(dtype=unet.model.dtype)
    try:
        pred_latents = unet.model(
            latent_batch,
            timesteps,
            encoder_hidden_states=audio_feature_batch,
        ).sample
    except RuntimeError as e:
        err = str(e)
        if "No execution plans support the graph" in err or "No available kernel" in err:
            logger.warning("warmup SDPA fast kernel failed; fallback to math-only and retry once.")
            try:
                torch.backends.cuda.enable_cudnn_sdp(False)
                torch.backends.cuda.enable_flash_sdp(False)
                torch.backends.cuda.enable_mem_efficient_sdp(False)
                torch.backends.cuda.enable_math_sdp(True)
            except Exception:
                pass
            pred_latents = unet.model(
                latent_batch,
                timesteps,
                encoder_hidden_states=audio_feature_batch,
            ).sample
        else:
            raise
    vae.decode_latents(pred_latents)

def read_imgs(img_list):
    frames = []
    logger.info('reading images...')
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames

def __mirror_index(size, index):
    #size = len(self.coord_list_cycle)
    turn = index // size
    res = index % size
    if turn % 2 == 0:
        return res
    else:
        return size - res - 1 

def _push_realtime_audio_frames(realtime_audio_queue, audio_frames):
    if realtime_audio_queue is None:
        return
    for audio_frame in audio_frames:
        pushed = False
        for _ in range(2):
            try:
                realtime_audio_queue.put_nowait(audio_frame)
                pushed = True
                break
            except queue.Full:
                try:
                    realtime_audio_queue.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
            except Exception:
                break
        if not pushed:
            # Drop the newest frame in extreme overload.
            continue

@torch.no_grad()
def inference(quit_event,batch_size,input_latent_list_cycle,audio_feat_queue,audio_out_queue,res_frame_queue,
              vae, unet, pe,timesteps,realtime_audio_queue=None): #vae, unet, pe,timesteps
    
    # vae, unet, pe = load_diffusion_model()
    # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # timesteps = torch.tensor([0], device=device)
    # pe = pe.half()
    # vae.vae = vae.vae.half()
    # unet.model = unet.model.half()
    
    length = len(input_latent_list_cycle)
    index = 0
    count=0
    counttime=0
    logger.info('start inference')
    while not quit_event.is_set():
        starttime=time.perf_counter()
        try:
            whisper_chunks = audio_feat_queue.get(block=True, timeout=1)
        except queue.Empty:
            continue
        is_all_silence=True
        audio_frames = []
        for _ in range(batch_size*2):
            frame,type,eventpoint = audio_out_queue.get()
            audio_frames.append((frame,type,eventpoint))
            if type==0:
                is_all_silence=False
        _push_realtime_audio_frames(realtime_audio_queue, audio_frames)
        if is_all_silence:
            for i in range(batch_size):
                res_frame_queue.put((None,__mirror_index(length,index),audio_frames[i*2:i*2+2]))
                index = index + 1
        else:
            # print('infer=======')
            t=time.perf_counter()
            whisper_batch = np.stack(whisper_chunks)
            latent_batch = []
            for i in range(batch_size):
                idx = __mirror_index(length,index+i)
                latent = input_latent_list_cycle[idx]
                latent_batch.append(latent)
            latent_batch = torch.cat(latent_batch, dim=0)
            
            # for i, (whisper_batch,latent_batch) in enumerate(gen):
            audio_feature_batch = torch.from_numpy(whisper_batch)
            audio_feature_batch = audio_feature_batch.to(device=unet.device,
                                                            dtype=unet.model.dtype)
            audio_feature_batch = pe(audio_feature_batch)
            latent_batch = latent_batch.to(dtype=unet.model.dtype)
            # print('prepare time:',time.perf_counter()-t)
            # t=time.perf_counter()

            try:
                pred_latents = unet.model(
                    latent_batch,
                    timesteps,
                    encoder_hidden_states=audio_feature_batch,
                ).sample
            except RuntimeError as e:
                err = str(e)
                if "No execution plans support the graph" in err or "No available kernel" in err:
                    logger.warning("SDPA fast kernel failed; fallback to math-only and retry once.")
                    try:
                        torch.backends.cuda.enable_cudnn_sdp(False)
                        torch.backends.cuda.enable_flash_sdp(False)
                        torch.backends.cuda.enable_mem_efficient_sdp(False)
                        torch.backends.cuda.enable_math_sdp(True)
                    except Exception:
                        pass
                    pred_latents = unet.model(
                        latent_batch,
                        timesteps,
                        encoder_hidden_states=audio_feature_batch,
                    ).sample
                else:
                    raise
            # print('unet time:',time.perf_counter()-t)
            # t=time.perf_counter()
            recon = vae.decode_latents(pred_latents)
            # infer_inqueue.put((whisper_batch,latent_batch,sessionid))
            # recon,outsessionid = infer_outqueue.get()
            # if outsessionid != sessionid:
            #     print('outsessionid:',outsessionid,' mysessionid:',sessionid)

            # print('vae time:',time.perf_counter()-t)
            #print('diffusion len=',len(recon))
            counttime += (time.perf_counter() - t)
            count += batch_size
            #_totalframe += 1
            if count>=40:
                avg_fps = count / counttime
                if torch.cuda.is_available() and str(unet.device).startswith("cuda"):
                    alloc_mb = torch.cuda.memory_allocated(unet.device) / 1024 / 1024
                    reserved_mb = torch.cuda.memory_reserved(unet.device) / 1024 / 1024
                    logger.info(
                        "------actual avg infer fps:%.4f | cuda alloc %.1f MiB, reserved %.1f MiB",
                        avg_fps,
                        alloc_mb,
                        reserved_mb,
                    )
                else:
                    logger.info(f"------actual avg infer fps:{avg_fps:.4f}")
                count=0
                counttime=0
            for i,res_frame in enumerate(recon):
                #self.__pushmedia(res_frame,loop,audio_track,video_track)
                res_frame_queue.put((res_frame,__mirror_index(length,index),audio_frames[i*2:i*2+2]))
                index = index + 1
            #print('total batch time:',time.perf_counter()-starttime)            
    logger.info('musereal inference processor stop')

class MuseReal(BaseReal):
    @torch.no_grad()
    def __init__(self, opt, model, avatar):
        super().__init__(opt)
        #self.opt = opt # shared with the trainer's opt to support in-place modification of rendering parameters.
        # self.W = opt.W
        # self.H = opt.H

        self.fps = opt.fps # 20 ms per frame

        self.batch_size = opt.batch_size
        self.idx = 0
        self.res_frame_queue = mp.Queue(self.batch_size*2)

        self.vae, self.unet, self.pe, self.timesteps, self.audio_processor = model
        self.frame_list_cycle,self.mask_list_cycle,self.coord_list_cycle,self.mask_coords_list_cycle, self.input_latent_list_cycle = avatar
        #self.__loadavatar()

        self.asr = MuseASR(opt,self,self.audio_processor)
        self.asr.warm_up()
        self._last_asr_err_log_ts = 0.0
        self._asr_err_count = 0
        self._last_backpressure_log_ts = 0.0
        pacing_mode = os.getenv("LT_MUSETALK_ENABLE_PACING", "auto").strip().lower()
        if pacing_mode in {"1", "true", "yes", "on"}:
            self._enable_pacing = True
        elif pacing_mode in {"0", "false", "no", "off"}:
            self._enable_pacing = False
        else:
            # Auto mode: enable by default for stability on consumer GPUs.
            self._enable_pacing = True
        self.realtime_audio_queue = Queue(maxsize=max(self.fps * 2, 100))
        logger.info(
            "musetalk realtime-audio bridge enabled, qmax=%d, pacing=%s",
            self.realtime_audio_queue.maxsize,
            self._enable_pacing,
        )
        
        self.render_event = mp.Event()

    # def __del__(self):
    #     logger.info(f'musereal({self.sessionid}) delete')
    

    def __mirror_index(self, index):
        size = len(self.coord_list_cycle)
        turn = index // size
        res = index % size
        if turn % 2 == 0:
            return res
        else:
            return size - res - 1  

    def __warm_up(self): 
        self.asr.run_step()
        whisper_chunks = self.asr.get_next_feat()
        whisper_batch = np.stack(whisper_chunks)
        latent_batch = []
        for i in range(self.batch_size):
            idx = self.__mirror_index(self.idx+i)
            latent = self.input_latent_list_cycle[idx]
            latent_batch.append(latent)
        latent_batch = torch.cat(latent_batch, dim=0)
        logger.info('infer=======')
        # for i, (whisper_batch,latent_batch) in enumerate(gen):
        audio_feature_batch = torch.from_numpy(whisper_batch)
        audio_feature_batch = audio_feature_batch.to(device=self.unet.device,
                                                        dtype=self.unet.model.dtype)
        audio_feature_batch = self.pe(audio_feature_batch)
        latent_batch = latent_batch.to(dtype=self.unet.model.dtype)

        pred_latents = self.unet.model(latent_batch, 
                                    self.timesteps, 
                                    encoder_hidden_states=audio_feature_batch).sample
        recon = self.vae.decode_latents(pred_latents)
      

    def paste_back_frame(self,pred_frame,idx:int):
        bbox = self.coord_list_cycle[idx]
        ori_frame = copy.deepcopy(self.frame_list_cycle[idx])
        x1, y1, x2, y2 = bbox

        res_frame = cv2.resize(pred_frame.astype(np.uint8),(x2-x1,y2-y1))
        mask = self.mask_list_cycle[idx]
        mask_crop_box = self.mask_coords_list_cycle[idx]

        combine_frame = get_image_blending(ori_frame,res_frame,bbox,mask,mask_crop_box)
        return combine_frame
            
    def render(self,quit_event,loop=None,audio_track=None,video_track=None):
        #if self.opt.asr:
        #     self.asr.warm_up()

        self.init_customindex()
        self.tts.render(quit_event)
        
        #self.render_event.set() #start infer process render
        infer_quit_event = Event()
        infer_thread = Thread(target=inference, args=(infer_quit_event,self.batch_size,self.input_latent_list_cycle,
                                           self.asr.feat_queue,self.asr.output_queue,self.res_frame_queue,
                                           self.vae, self.unet, self.pe,self.timesteps,self.realtime_audio_queue)) #mp.Process
        infer_thread.start()
        
        process_quit_event = Event()
        process_thread = Thread(target=self.process_frames, args=(process_quit_event,loop,audio_track,video_track))
        process_thread.start()

        
        count=0
        totaltime=0
        _starttime=time.perf_counter()
        target_step_sec = (self.batch_size * 2) / float(self.fps)
        #_totalframe=0
        while not quit_event.is_set(): #todo
            # update texture every frame
            # audio stream thread...
            step_t = time.perf_counter()
            try:
                self.asr.run_step()
            except Exception:
                self._asr_err_count += 1
                now = time.time()
                if now - self._last_asr_err_log_ts >= 2:
                    logger.exception(
                        "asr.run_step failed; continue render loop (err_count=%d).",
                        self._asr_err_count,
                    )
                    self._last_asr_err_log_ts = now
                time.sleep(min(target_step_sec, 0.05))
                continue
            step_elapsed = time.perf_counter() - step_t
            if step_elapsed < target_step_sec:
                time.sleep(target_step_sec - step_elapsed)

            if self._enable_pacing:
                # If feature queue is frequently full, inference is behind.
                # Add a small adaptive pause to reduce producer pressure.
                extra_sleep = 0.0
                feat_backlog = 0
                feat_qmax = max(1, int(getattr(self.asr, "feat_queue_maxsize", 2)))
                try:
                    feat_backlog = self.asr.feat_queue.qsize()
                except Exception:
                    feat_backlog = 0
                backlog_ratio = float(feat_backlog) / float(feat_qmax)
                if backlog_ratio >= 0.5:
                    # Increase producer sleep as backlog grows.
                    extra_sleep = target_step_sec * min(1.2, max(0.2, backlog_ratio))
                if extra_sleep > 0:
                    now = time.time()
                    if now - self._last_backpressure_log_ts >= 2:
                        logger.warning(
                            "musetalk backpressure: feat_q=%d/%d, ratio=%.2f, add %.3fs pacing sleep.",
                            feat_backlog,
                            feat_qmax,
                            backlog_ratio,
                            extra_sleep,
                        )
                        self._last_backpressure_log_ts = now
                    time.sleep(extra_sleep)
            #self.test_step(loop,audio_track,video_track)
            # totaltime += (time.perf_counter() - t)
            # count += self.opt.batch_size
            # if count>=100:
            #     print(f"------actual avg infer fps:{count/totaltime:.4f}")
            #     count=0
            #     totaltime=0
            if video_track and video_track._queue.qsize()>=1.5*self.opt.batch_size:
                logger.debug('sleep qsize=%d',video_track._queue.qsize())
                time.sleep(0.04*video_track._queue.qsize()*0.8)
            # if video_track._queue.qsize()>=5:
            #     print('sleep qsize=',video_track._queue.qsize())
            #     time.sleep(0.04*video_track._queue.qsize()*0.8)
                
            # delay = _starttime+_totalframe*0.04-time.perf_counter() #40ms
            # if delay > 0:
            #     time.sleep(delay)
        logger.info('musereal thread stop')

        infer_quit_event.set()
        infer_thread.join()

        process_quit_event.set()
        process_thread.join()
            
