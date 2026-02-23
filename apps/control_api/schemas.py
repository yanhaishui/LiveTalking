"""API 请求与响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


class ApiMessage(BaseModel):
    """通用消息响应。"""

    message: str


class JobCreate(BaseModel):
    """创建任务请求。"""

    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class JobCancelRequest(BaseModel):
    """取消任务请求。"""

    reason: str | None = None


class AvatarCreate(BaseModel):
    """创建 Avatar 的请求。"""

    name: str = Field(..., min_length=1, max_length=120)
    avatar_path: str = Field(..., min_length=1)
    cover_image: str | None = None
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class AvatarUpdate(BaseModel):
    """更新 Avatar 的请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    cover_image: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    meta: dict[str, Any] | None = None


class AvatarCloneRequest(BaseModel):
    """创建 wav2lip 视频克隆任务。"""

    name: str = Field(..., min_length=1, max_length=120)
    video_path: str = Field(..., min_length=1)
    avatar_id: str | None = None
    img_size: int = Field(default=256, ge=96, le=512)
    face_det_batch_size: int = Field(default=16, ge=1, le=256)
    pads: list[int] = Field(default_factory=lambda: [0, 10, 0, 0])
    overwrite: bool = False
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_sec: int = Field(default=5, ge=1, le=120)


class VoiceCreate(BaseModel):
    """创建声音模型请求。"""

    name: str = Field(..., min_length=1, max_length=120)
    engine: str = Field(default="xtts", min_length=1)
    ref_wav_path: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)


class VoiceUpdate(BaseModel):
    """更新声音模型请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    engine: str | None = None
    ref_wav_path: str | None = None
    profile: dict[str, Any] | None = None
    status: str | None = None


class VoiceCloneRequest(BaseModel):
    """创建声音克隆任务。"""

    name: str = Field(..., min_length=1, max_length=120)
    engine: str = Field(default="xtts")
    ref_wav_path: str = Field(..., min_length=1)
    tts_server: str = Field(default="http://127.0.0.1:9000")
    voice_id: str | None = None
    # 一键试听能力（克隆完成后自动生成试听音频）。
    generate_preview: bool = True
    preview_text: str = Field(default="你好，欢迎来到直播间。")
    preview_language: str = Field(default="zh-cn")
    preview_stream_chunk_size: int = Field(default=20, ge=1, le=500)
    # XTTS 参数化（透传到 tts_stream，可按引擎实际支持情况生效）。
    temperature: float | None = Field(default=None, ge=0.01, le=2.0)
    speed: float | None = Field(default=None, ge=0.1, le=3.0)
    top_k: int | None = Field(default=None, ge=1, le=1000)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    repetition_penalty: float | None = Field(default=None, ge=0.1, le=5.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_sec: int = Field(default=5, ge=1, le=120)


class ScriptCreate(BaseModel):
    """新建脚本请求。"""

    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    category: str | None = None
    priority: int = 0
    enabled: bool = True


class ScriptUpdate(BaseModel):
    """更新脚本请求。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    category: str | None = None
    priority: int | None = None
    enabled: bool | None = None


class PlaylistCreate(BaseModel):
    """新建轮播计划请求。"""

    name: str = Field(..., min_length=1, max_length=120)
    mode: str = Field(default="sequential")
    interval_sec: int = Field(default=30, ge=1, le=3600)
    enabled: bool = True


class PlaylistUpdate(BaseModel):
    """更新轮播计划请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    mode: str | None = None
    interval_sec: int | None = Field(default=None, ge=1, le=3600)
    enabled: bool | None = None


class PlaylistItemCreate(BaseModel):
    """向轮播计划添加脚本项。"""

    script_id: str
    sort_order: int = 0
    weight: int = Field(default=1, ge=1, le=100)


class PresetCreate(BaseModel):
    """创建直播预设请求。"""

    name: str = Field(..., min_length=1, max_length=120)
    avatar_id: str
    voice_id: str | None = None
    model: str = Field(default="wav2lip")
    transport: str = Field(default="virtualcam")
    listen_port: int = Field(default=8010, ge=1, le=65535)
    tts: str | None = None
    tts_server: str | None = None
    ref_file: str | None = None
    ref_text: str | None = None
    extra_args: list[str] = Field(default_factory=list)


class PresetUpdate(BaseModel):
    """更新直播预设请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_id: str | None = None
    voice_id: str | None = None
    model: str | None = None
    transport: str | None = None
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    tts: str | None = None
    tts_server: str | None = None
    ref_file: str | None = None
    ref_text: str | None = None
    extra_args: list[str] | None = None


class LiveStartRequest(BaseModel):
    """启动直播请求。"""

    preset_id: str | None = None

    # 当不传 preset_id 时，使用以下字段直接启动
    avatar_id: str | None = None
    voice_id: str | None = None
    model: str = Field(default="wav2lip")
    transport: str = Field(default="virtualcam")
    listen_port: int = Field(default=8010, ge=1, le=65535)
    tts: str | None = None
    tts_server: str | None = None
    ref_file: str | None = None
    ref_text: str | None = None
    extra_args: list[str] = Field(default_factory=list)


class LiveStopRequest(BaseModel):
    """停止直播请求。"""

    force: bool = False


class RoomMessageIngest(BaseModel):
    """直播间消息。"""

    platform: str | None = None
    room_id: str | None = None
    source_msg_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    content: str = Field(..., min_length=1)
    msg_time: str | None = None
    priority: int = Field(default=50, ge=0, le=100)


class RoomMessagesIngestRequest(BaseModel):
    """批量写入直播间消息请求。"""

    messages: list[RoomMessageIngest] = Field(default_factory=list)


class PlatformMessageIngest(BaseModel):
    """平台侧标准化消息（用于真实弹幕接入）。"""

    platform: str = Field(..., min_length=1)
    room_id: str = Field(..., min_length=1)
    source_msg_id: str = Field(..., min_length=1)
    user_id: str | None = None
    user_name: str | None = None
    content: str = Field(..., min_length=1)
    msg_time: str | None = None
    priority: int = Field(default=50, ge=0, le=100)
    auto_generate_reply: bool = True
    auto_speak: bool = True
    strategy: str = Field(default="rule")
    fallback_text: str | None = None
    interrupt: bool | None = None


class PlatformMessagesIngestRequest(BaseModel):
    """平台消息批量接入请求。"""

    messages: list[PlatformMessageIngest] = Field(default_factory=list)


class ReplyGenerateRequest(BaseModel):
    """生成智能回复请求。"""

    message_id: str
    strategy: str = Field(default="rule")
    fallback_text: str | None = None


class ReplySpeakRequest(BaseModel):
    """请求将回复送入播报链路。"""

    auto_mark_handled: bool = True
    interrupt: bool = True
    priority: int = Field(default=80, ge=0, le=100)


class ManualSpeakRequest(BaseModel):
    """手工播报请求（用于联调或运营干预）。"""

    text: str = Field(..., min_length=1)
    interrupt: bool = False
    priority: int = Field(default=60, ge=0, le=100)


class VoicePreviewRequest(BaseModel):
    """声音试听请求。"""

    text: str = Field(..., min_length=1)
    tts_server: str = Field(default="http://127.0.0.1:9000")
    language: str = Field(default="zh-cn")
    stream_chunk_size: int = Field(default=20, ge=1, le=500)
    temperature: float | None = Field(default=None, ge=0.01, le=2.0)
    speed: float | None = Field(default=None, ge=0.1, le=3.0)
    top_k: int | None = Field(default=None, ge=1, le=1000)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    repetition_penalty: float | None = Field(default=None, ge=0.1, le=5.0)
