# -*- coding: utf-8 -*-
"""
视频转码与压缩核心模块
支持微信/抖音已下载视频扫描、外部视频拖入、ffprobe 信息检测以及多参数 FFmpeg 转码队列
"""

import os
import sys
import json
import queue
import time
import uuid
import shutil
import subprocess
import threading
from pathlib import Path
from flask import Blueprint, request, jsonify

from backend.config import DATA_DIR, OUTPUT_DIR, get_settings

transcode_bp = Blueprint("transcode", __name__, url_prefix="/api/transcode")

# ── 路径初始化 ────────────────────────────────────────────
DOUYIN_DIR = DATA_DIR / "douyin_downloads"
TEMP_DIR = DATA_DIR / "temp_uploads"
TRANSCODE_OUTPUT_DIR = DATA_DIR / "transcoded"

def ensure_dirs():
    DOUYIN_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCODE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 全局队列与状态 ──────────────────────────────────────────
job_queue = queue.Queue()
jobs = {}
jobs_lock = threading.Lock()
worker_thread = None

# ── ffprobe 视频信息提取 ─────────────────────────────────────
def get_video_metadata(file_path: str) -> dict:
    """利用 ffprobe 提取视频/音频流元数据"""
    if not os.path.exists(file_path):
        return {"error": "文件不存在"}

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if result.returncode != 0:
            return {"error": f"ffprobe 执行失败: {result.stderr}"}
        
        data = json.loads(result.stdout)
        format_info = data.get("format", {})
        streams = data.get("streams", [])
        
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
        
        duration = float(format_info.get("duration", 0))
        size = int(format_info.get("size", 0))
        bitrate = int(format_info.get("bit_rate", 0)) if format_info.get("bit_rate") else 0
        
        width = int(video_stream.get("width", 0)) if video_stream.get("width") else 0
        height = int(video_stream.get("height", 0)) if video_stream.get("height") else 0
        video_codec = video_stream.get("codec_name", "unknown")
        
        framerate_str = video_stream.get("r_frame_rate", "0/0")
        if "/" in framerate_str:
            try:
                num, den = map(int, framerate_str.split("/"))
                fps = num / den if den > 0 else 0
            except ValueError:
                fps = 0
        else:
            try:
                fps = float(framerate_str) if framerate_str else 0
            except ValueError:
                fps = 0
                
        audio_codec = audio_stream.get("codec_name", "none")
        
        return {
            "duration": duration,
            "size_bytes": size,
            "bitrate": bitrate,
            "width": width,
            "height": height,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "fps": round(fps, 2),
            "format_name": format_info.get("format_long_name", "unknown")
        }
    except Exception as e:
        return {"error": f"提取失败: {str(e)}"}


def _should_copy_audio(audio_codec: str, out_format: str) -> bool:
    """判断源音频流是否可以直接复制（无重编码），以防音频重编码带来冗余体积和质量损耗"""
    if not audio_codec or audio_codec == "none":
        return False
    out_format = out_format.lower()
    audio_codec = audio_codec.lower()
    
    # MP4 & MOV 容器兼容的常见音频编码
    if out_format in ("mp4", "mov"):
        return audio_codec in ("aac", "mp3", "ac3", "eac3")
    # MKV 支持几乎任何格式的音频复制
    elif out_format == "mkv":
        return audio_codec in ("aac", "mp3", "ac3", "eac3", "flac", "vorbis", "opus", "pcm_s16le", "pcm_s24le")
    # WebM 只兼容 Vorbis 或 Opus，通常不进行音频复制，强制重编码
    return False


def _get_target_pixels(resolution: str, width: int, height: int) -> int:
    """根据目标分辨率计算像素点总数"""
    if resolution == "1080p":
        return 1920 * 1080
    elif resolution == "720p":
        return 1280 * 720
    elif resolution == "480p":
        return 854 * 480
    return width * height


def _pixel_based_max_bitrate(pixels: int, codec: str) -> int:
    """根据像素点数和编码格式，给出合理的码率上限限制，防止码率虚高体积过大"""
    # HEVC 压缩率极高，同等画质下可以设置更低码率
    if codec == "hevc":
        bpp = 0.04
        val = int(pixels * 24 * bpp)
        return max(val, 200000)  # 最低限制 200kbps
    else:
        bpp = 0.08
        val = int(pixels * 24 * bpp)
        return max(val, 400000)  # 最低限制 400kbps


def _build_software_fallback_cmd(input_path: str, output_path: str, video_codec: str, quality: str, resolution: str, audio_mode: str, meta: dict) -> list:
    """构建用于 fallback 的高压缩率软件编码 FFmpeg 命令"""
    cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-i", input_path]
    
    # 获取比特率和大小信息
    input_bitrate = meta.get("bitrate", 0)
    duration = meta.get("duration", 0)
    if not input_bitrate:
        if duration > 0 and meta.get("size_bytes", 0) > 0:
            input_bitrate = int(meta["size_bytes"] * 8 / duration)
        else:
            input_bitrate = 1500000

    if video_codec == "hevc":
        cmd.extend(["-c:v", "libx265", "-tag:v", "hvc1"])
        # CRF 黄金档位：low: 32, medium: 28, high: 24 (Fallback 稍微激进一点防止体积膨胀)
        crf_map = {"high": "25", "medium": "29", "low": "34"}
        cmd.extend(["-crf", crf_map.get(quality, "29"), "-preset", "slow"])
        
        # 强制设置 VBV 上限限制，使得最高码率强制被卡死
        target_max = input_bitrate * 0.60
        if quality == "high":
            target_max = input_bitrate * 0.75
        elif quality == "low":
            target_max = input_bitrate * 0.40
        cmd.extend(["-maxrate", f"{int(target_max)}", "-bufsize", f"{int(target_max * 1.5)}"])
    else:
        cmd.extend(["-c:v", "libx264"])
        crf_map = {"high": "22", "medium": "25", "low": "30"}
        cmd.extend(["-crf", crf_map.get(quality, "25"), "-preset", "slow"])
        
        target_max = input_bitrate * 0.70
        if quality == "high":
            target_max = input_bitrate * 0.80
        elif quality == "low":
            target_max = input_bitrate * 0.45
        cmd.extend(["-maxrate", f"{int(target_max)}", "-bufsize", f"{int(target_max * 1.5)}"])
        
    cmd.extend(["-pix_fmt", "yuv420p"])
    
    # 视频分辨率缩放
    if resolution == "1080p":
        cmd.extend(["-vf", "scale=1920:-2"])
    elif resolution == "720p":
        cmd.extend(["-vf", "scale=1280:-2"])
    elif resolution == "480p":
        cmd.extend(["-vf", "scale=854:-2"])
        
    # 音频处理
    if audio_mode == "mute":
        cmd.append("-an")
    elif audio_mode == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
    else:  # keep
        # 智能检测源音频码率，如果本来就很低，直接用 copy，省时省力省空间
        source_audio_bitrate = 0
        if "streams" in meta:
            # 找到 audio stream
            try:
                streams_list = meta.get("streams", [])
                audio_stream = next((s for s in streams_list if s.get("codec_type") == "audio"), {})
                source_audio_bitrate = int(audio_stream.get("bit_rate", 0))
            except Exception:
                pass
                
        if source_audio_bitrate and source_audio_bitrate < 128000:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        
    cmd.append(output_path)
    return cmd


_supported_encoders = None

def get_supported_encoders() -> set:
    """探测 FFmpeg 支持的编码器，结果进行全局缓存"""
    global _supported_encoders
    if _supported_encoders is not None:
        return _supported_encoders
        
    _supported_encoders = set()
    try:
        # 运行 ffmpeg -encoders
        result = subprocess.run(
            ["ffmpeg", "-encoders"], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True, 
            timeout=3
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    _supported_encoders.add(parts[1])
    except Exception as e:
        print(f"探测 FFmpeg 编码器失败: {e}")
    return _supported_encoders


def _get_win_gpu_encoder(video_codec: str, quality: str) -> tuple:
    """Windows 下自动探测并选择最优的显卡硬件编码器及质量参数"""
    encoders = get_supported_encoders()
    
    # 质量控制档位映射
    if video_codec == "hevc":
        qp_map = {"high": "22", "medium": "27", "low": "32"}
        # 1. NVIDIA (优先)
        if "hevc_nvenc" in encoders:
            return "hevc_nvenc", ["-rc", "constqp", "-qp", qp_map.get(quality, "27")]
        # 2. Intel QSV
        if "hevc_qsv" in encoders:
            return "hevc_qsv", ["-global_quality", qp_map.get(quality, "27")]
        # 3. AMD AMF
        if "hevc_amf" in encoders:
            qp_val = qp_map.get(quality, "27")
            return "hevc_amf", ["-rc", "cqp", "-qp_i", qp_val, "-qp_p", qp_val]
    else:  # h264
        qp_map = {"high": "20", "medium": "24", "low": "29"}
        # 1. NVIDIA
        if "h264_nvenc" in encoders:
            return "h264_nvenc", ["-rc", "constqp", "-qp", qp_map.get(quality, "24")]
        # 2. Intel QSV
        if "h264_qsv" in encoders:
            return "h264_qsv", ["-global_quality", qp_map.get(quality, "24")]
        # 3. AMD AMF
        if "h264_amf" in encoders:
            qp_val = qp_map.get(quality, "24")
            return "h264_amf", ["-rc", "cqp", "-qp_i", qp_val, "-qp_p", qp_val]
            
    return None, []


# ── 视频转码核心工作流 ─────────────────────────────────────────
def _transcode_worker():
    """后台单线程串口任务执行器，防止并发转码导致系统卡死"""
    while True:
        try:
            job_id = job_queue.get()
            if job_id is None:
                break
                
            with jobs_lock:
                job = jobs.get(job_id)
                if not job or job["status"] != "pending":
                    job_queue.task_done()
                    continue
                job["status"] = "running"
                job["started_at"] = time.time()
            
            _execute_transcode(job)
            job_queue.task_done()
        except Exception as e:
            print(f"转码队列异常: {e}")
            time.sleep(1)

def _execute_transcode(job):
    """构建命令并执行转码，精确解析 ffmpeg 进度"""
    input_path = job["input_path"]
    
    # 提取视频总时长
    meta = get_video_metadata(input_path)
    if "error" in meta:
        with jobs_lock:
            job["status"] = "failed"
            job["error"] = f"读取源视频元数据失败: {meta['error']}"
            job["completed_at"] = time.time()
        return

    duration = meta.get("duration", 0)
    job["input_metadata"] = meta
    
    # 解析输出文件名与后缀
    ensure_dirs()
    input_filename = Path(input_path).stem
    out_format = job["params"]["output_format"]
    ext = f".{out_format.lower()}"
    params = job["params"]
    video_codec = params.get("video_codec", "h264")
    quality = params.get("quality", "medium")
    resolution = params.get("resolution", "keep")
    
    # 注意：输出文件名会在智能编码策略判断之后生成（见下方），以反映实际使用的编码器

    params = job["params"]

    # 1. 基础参数解析
    video_codec = params.get("video_codec", "h264")
    hw_accel = params.get("hw_accel", True)
    quality = params.get("quality", "medium")
    resolution = params.get("resolution", "keep")
    audio_mode = params.get("audio_mode", "keep")
    is_mac = sys.platform == "darwin"
    is_win = sys.platform == "win32"
    is_audio_only = out_format.lower() == "mp3"

    # 构建 FFmpeg 命令
    cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-i", input_path]
    
    input_bitrate = meta.get("bitrate", 0)
    if not input_bitrate:
        # 无 bitrate 信息时通过文件大小和时长粗算
        if duration > 0 and meta.get("size_bytes", 0) > 0:
            input_bitrate = int(meta["size_bytes"] * 8 / duration)
        else:
            input_bitrate = 1500000
    
    source_codec = meta.get("video_codec", "").lower()
    width = meta.get("width", 0) or 1280
    height = meta.get("height", 0) or 720
    
    # ═══ 智能编码策略判断 ═══
    # 核心逻辑：绝大多数从微信/抖音/网站下载的视频都是已经高度压缩的低码率视频，
    # 对这类视频使用硬件编码器（VideoToolbox/NVENC）反而会导致：
    #   1. 体积膨胀（硬件编码器压缩效率低于 CRF 软件编码器）
    #   2. 画质劣化（重新编码引入累积误差 generation loss）
    # HEVC 阈值更高：硬件 HEVC 编码器压缩效率明显弱于 libx265，对中等码率视频也应走软件
    # H.264 阈值较低：硬件 H.264 编码器（VideoToolbox/NVENC）质量已足够接近软件 x264
    if video_codec == "hevc":
        LOW_BITRATE_THRESHOLD = 8000000  # 8 Mbps — HEVC 几乎总是走软件编码以获得最佳压缩
    else:
        LOW_BITRATE_THRESHOLD = 3000000  # 3 Mbps — H.264 仅对高码率原视频启用硬件加速
    force_software = (not is_audio_only and video_codec != "copy"
                      and input_bitrate < LOW_BITRATE_THRESHOLD)
    
    # 同编码器检测：如果源视频已经是目标编码格式，再次转码只会引入 generation loss
    # 除非用户明确要求了分辨率变更或极高压缩
    same_codec = False
    if video_codec == "hevc" and source_codec in ("hevc", "h265"):
        same_codec = True
    elif video_codec == "h264" and source_codec in ("h264", "avc"):
        same_codec = True
    
    if same_codec and resolution == "keep" and quality != "low":
        # 源已经是目标编码，且不需要缩放和极高压缩 → 直接 copy，避免画质损失
        print(f"⚡ 源视频已经是 {source_codec.upper()} 编码，且未要求缩放/极高压缩，自动切换为 Direct Copy 防止画质劣化")
        video_codec = "copy"
        job["params"]["video_codec"] = "copy"
        job["_auto_copy"] = True
    
    win_gpu_encoder = None
    win_gpu_args = []
    if is_win and hw_accel and not is_audio_only and video_codec != "copy":
        win_gpu_encoder, win_gpu_args = _get_win_gpu_encoder(video_codec, quality)

    use_hw = False
    if not force_software:
        if is_mac and hw_accel:
            use_hw = True
        elif is_win and hw_accel and win_gpu_encoder is not None:
            use_hw = True
    
    if force_software and video_codec != "copy":
        print(f"⚡ 源视频码率较低 ({input_bitrate//1000} kbps)，自动切换为 CRF 软件编码以确保压缩效果")
    
    # 记录实际使用的编码策略到 job 中，便于 fallback 判断
    job["_use_hw"] = use_hw
    
    # ── 构造输出文件名（在智能策略判断之后，确保反映实际编码器）──
    codec_tag = "h265" if video_codec == "hevc" else "h264"
    if video_codec == "copy":
        codec_tag = "copy"
    if out_format.lower() == "mp3":
        codec_tag = "audio"
        
    output_filename = f"{input_filename}_{codec_tag}_{quality}_{resolution}{ext}"
    output_path = str(TRANSCODE_OUTPUT_DIR / output_filename)
    job["output_path"] = output_path
    
    # 2. 视频编码器选择
    if is_audio_only:
        cmd.append("-vn")
    elif video_codec == "copy":
        cmd.extend(["-c:v", "copy"])
    elif use_hw:
        if is_mac:
            # macOS 硬件加速 (VideoToolbox)
            if video_codec == "hevc":
                cmd.extend(["-c:v", "hevc_videotoolbox", "-tag:v", "hvc1"])
            else:
                cmd.extend(["-c:v", "h264_videotoolbox"])
            cmd.extend(["-pix_fmt", "yuv420p"])
        elif is_win and win_gpu_encoder:
            # Windows 硬件加速 (NVIDIA / Intel / AMD)
            cmd.extend(["-c:v", win_gpu_encoder])
            if video_codec == "hevc":
                cmd.extend(["-tag:v", "hvc1"])
            cmd.extend(["-pix_fmt", "yuv420p"])
    else:
        # 软件编码 (CRF 模式 = 最佳质量/体积比)
        if video_codec == "hevc":
            cmd.extend(["-c:v", "libx265", "-tag:v", "hvc1"])
        else:
            cmd.extend(["-c:v", "libx264"])
        cmd.extend(["-pix_fmt", "yuv420p"])
    
    # 3. 视频分辨率缩放
    if not is_audio_only and video_codec != "copy":
        if resolution == "1080p":
            cmd.extend(["-vf", "scale=1920:-2"])
        elif resolution == "720p":
            cmd.extend(["-vf", "scale=1280:-2"])
        elif resolution == "480p":
            cmd.extend(["-vf", "scale=854:-2"])
            
    # 4. 质量/码率控制
    if not is_audio_only and video_codec != "copy":
        if use_hw:
            if is_mac:
                # ── macOS 硬件编码（仅高码率原始视频才会走到这里）──
                # VideoToolbox -q:v 值域: 1=最低质量, 100=无损
                # 与软件编码一致的策略：medium/high 不设 maxrate 让编码器保画质，仅 low 启用 bitrate 模式压缩
                if video_codec == "hevc":
                    if quality == "high":
                        cmd.extend(["-q:v", "65"])  # 视觉无损
                    elif quality == "low":
                        target_bitrate = max(500000, int(input_bitrate * 0.35))
                        cmd.extend([
                            "-b:v", f"{target_bitrate}",
                            "-maxrate", f"{int(target_bitrate * 1.2)}",
                            "-bufsize", f"{int(target_bitrate * 2)}"
                        ])
                    else:  # medium
                        cmd.extend(["-q:v", "55"])  # 高画质
                else:  # h264
                    if quality == "high":
                        cmd.extend(["-q:v", "70"])  # 视觉无损
                    elif quality == "low":
                        target_bitrate = max(600000, int(input_bitrate * 0.45))
                        cmd.extend([
                            "-b:v", f"{target_bitrate}",
                            "-maxrate", f"{int(target_bitrate * 1.2)}",
                            "-bufsize", f"{int(target_bitrate * 2)}"
                        ])
                    else:  # medium
                        cmd.extend(["-q:v", "60"])  # 高画质
            elif is_win and win_gpu_args:
                # ── Windows 硬件编码 ──
                cmd.extend(win_gpu_args)
        else:
            # ── 软件编码模式：CRF + slow preset，质量优先 ──
            # 限制 maxrate 兜底防止转码后文件体积比原视频还大，同时设置合理下限防止过度压缩导致画面模糊
            if video_codec == "hevc":
                # libx265 CRF 档位：越低画质越好体积越大
                # high=20 视觉无损, medium=23 肉眼几乎难辨, low=27 明显压缩
                crf_map = {"high": "20", "medium": "23", "low": "27"}
                cmd.extend(["-crf", crf_map.get(quality, "23"), "-preset", "slow"])

                # x265 保锐度参数集：
                #   no-sao=1        关闭 SAO 滤镜，避免边缘被过度平滑（最关键的锐度提升）
                #   psy-rd=2.0      感知失真权重（默认值，显式声明）
                #   psy-rdoq=2.0    开启感知量化优化（默认 0），强力保留高频细节/纹理
                #   aq-mode=2       变方差 AQ（默认，比 mode=3 不那么激进，避免平坦区域过度软化）
                #   aq-strength=0.8 微微降低 AQ 强度，保留更多细节码率
                #   bframes=8/ref=4 提升压缩效率
                cmd.extend(["-x265-params",
                            "no-sao=1:psy-rd=2.0:psy-rdoq=2.0:aq-mode=2:aq-strength=0.8:bframes=8:ref=4"])

                if quality == "high":
                    target_max = int(input_bitrate * 0.85)
                    target_max = max(target_max, min(600000, int(input_bitrate * 0.95)))
                elif quality == "low":
                    target_max = int(input_bitrate * 0.50)
                    target_max = max(target_max, min(300000, int(input_bitrate * 0.95)))
                else:  # medium
                    target_max = int(input_bitrate * 0.70)
                    target_max = max(target_max, min(450000, int(input_bitrate * 0.95)))
                
                cmd.extend(["-maxrate", f"{target_max}", "-bufsize", f"{int(target_max * 2)}"])
            else:  # h264
                # libx264 CRF 档位（H.264 CRF 比 HEVC 高 3 档对应相近画质）
                crf_map = {"high": "18", "medium": "21", "low": "26"}
                cmd.extend(["-crf", crf_map.get(quality, "21"), "-preset", "slow"])

                if quality == "high":
                    target_max = int(input_bitrate * 0.90)
                    target_max = max(target_max, min(800000, int(input_bitrate * 0.95)))
                elif quality == "low":
                    target_max = int(input_bitrate * 0.55)
                    target_max = max(target_max, min(400000, int(input_bitrate * 0.95)))
                else:  # medium
                    target_max = int(input_bitrate * 0.80)
                    target_max = max(target_max, min(600000, int(input_bitrate * 0.95)))
                
                cmd.extend(["-maxrate", f"{target_max}", "-bufsize", f"{int(target_max * 2)}"])
                
    # 5. 音频处理
    if is_audio_only:
        cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
    elif audio_mode == "mute":
        cmd.append("-an")
    elif audio_mode == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
    else:  # keep
        source_audio_codec = meta.get("audio_codec", "none")
        if video_codec == "copy" or _should_copy_audio(source_audio_codec, out_format):
            cmd.extend(["-c:a", "copy"])
        else:
            if out_format.lower() == "webm":
                cmd.extend(["-c:a", "libopus", "-b:a", "96k"])
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "96k"])
        
    cmd.append(output_path)
    
    # 辅助子进程运行器（整合多次转码尝试的控制代码，防止重复）
    def run_ffmpeg_cmd(ffmpeg_cmd):
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
        proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
            startupinfo=startupinfo
        )
        
        def check_stdout():
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=")[1])
                        current_sec = us / 1000000.0
                        if duration > 0:
                            pct = min(99.9, round((current_sec / duration) * 100, 1))
                            with jobs_lock:
                                job["progress"] = pct
                    except Exception:
                        pass
                elif line.startswith("speed="):
                    speed = line.split("=")[1].strip()
                    with jobs_lock:
                        job["speed"] = speed
                elif line.startswith("fps="):
                    try:
                        fps = float(line.split("=")[1].strip())
                        with jobs_lock:
                            job["fps"] = fps
                    except Exception:
                        pass
                        
        stdout_thread = threading.Thread(target=check_stdout, daemon=True)
        stdout_thread.start()
        
        _, stderr_content = proc.communicate()
        return_code = proc.wait()
        return return_code, stderr_content

    # 启动三阶段智能体积压缩兜底逻辑
    try:
        # ── 阶段 1：首轮主编码转码 ──
        print(f"🎬 启动阶段 1 转码 (HW Acceleration={use_hw})...")
        return_code, stderr_content = run_ffmpeg_cmd(cmd)
        
        if return_code == 0:
            out_meta = get_video_metadata(output_path)
            output_size = out_meta.get("size_bytes", 0)
            input_size = meta.get("size_bytes", 0)
            
            is_copy = video_codec == "copy"
            is_audio = out_format.lower() == "mp3" or audio_mode == "mp3"
            
            # 兜底触发条件：
            #   - quality=low（用户明确要压）：输出 >= 95% 源 → 触发严苛压缩
            #   - quality=medium/high（保画质优先）：输出 > 100% 源 → 触发兜底，防止"体积反而变大"
            need_fallback = False
            if not is_copy and not is_audio:
                if quality == "low" and output_size >= input_size * 0.95:
                    need_fallback = True
                elif quality in ("medium", "high") and output_size > input_size:
                    need_fallback = True

            if need_fallback:
                print(f"⚠️ 阶段 1 转码体积膨胀 ({output_size} > {input_size})，自动触发阶段 2 兜底压缩...")
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except Exception:
                    pass
                
                # ── 阶段 2：CRF 升档 + 较松 maxrate 兜底 ──
                # 对 low 用严苛压缩；medium/high 触发时（仅当 stage1 体积反而>源）只需略压保证缩小
                soft_cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-i", input_path]
                if video_codec == "hevc":
                    soft_cmd.extend(["-c:v", "libx265", "-tag:v", "hvc1"])
                    crf_val = "30" if quality == "low" else ("26" if quality == "medium" else "23")
                    soft_cmd.extend(["-crf", crf_val, "-preset", "medium"])
                    # 同样应用保锐度参数
                    soft_cmd.extend(["-x265-params",
                                     "no-sao=1:psy-rd=2.0:psy-rdoq=2.0:aq-mode=2:aq-strength=0.8"])
                    target_max = input_bitrate * (0.50 if quality == "low" else 0.85)
                    soft_cmd.extend(["-maxrate", f"{int(target_max)}", "-bufsize", f"{int(target_max * 1.5)}"])
                else:
                    soft_cmd.extend(["-c:v", "libx264"])
                    crf_val = "28" if quality == "low" else ("25" if quality == "medium" else "22")
                    soft_cmd.extend(["-crf", crf_val, "-preset", "medium"])
                    target_max = input_bitrate * (0.65 if quality == "low" else 0.90)
                    soft_cmd.extend(["-maxrate", f"{int(target_max)}", "-bufsize", f"{int(target_max * 1.5)}"])
                    
                soft_cmd.extend(["-pix_fmt", "yuv420p"])

                if resolution == "1080p":
                    soft_cmd.extend(["-vf", "scale=1920:-2"])
                elif resolution == "720p":
                    soft_cmd.extend(["-vf", "scale=1280:-2"])
                elif resolution == "480p":
                    soft_cmd.extend(["-vf", "scale=854:-2"])
                    
                # 阶段 2 音频也更加紧凑，使用 smart copy 或 aac 64k
                source_audio_codec = meta.get("audio_codec", "none")
                if _should_copy_audio(source_audio_codec, out_format):
                    soft_cmd.extend(["-c:a", "copy"])
                else:
                    soft_cmd.extend(["-c:a", "aac", "-b:a", "64k"])
                    
                soft_cmd.append(output_path)
                
                return_code, stderr_content = run_ffmpeg_cmd(soft_cmd)
                
                if return_code == 0:
                    out_meta = get_video_metadata(output_path)
                    output_size = out_meta.get("size_bytes", 0)
                    
                    # ── 阶段 3：自适应分辨率缩小 1.5 倍 + 极限压缩 CRF（仅 low 档触发）──
                    # medium/high 重画质，不应降分辨率；阶段 2 没压下来就接受现状
                    if quality == "low" and output_size >= input_size * 0.98:
                        print(f"⚠️ 阶段 2 压缩效果仍不佳，触发阶段 3：自适应分辨率缩放 + 极限 CRF 压缩...")
                        try:
                            if os.path.exists(output_path):
                                os.remove(output_path)
                        except Exception:
                            pass
                            
                        stage3_cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-i", input_path]
                        
                        # 长宽降级
                        new_width = 640
                        if width > 0 and width < 640:
                            new_width = int(width * 0.7) // 2 * 2
                        elif width > 0:
                            new_width = int(width / 1.5) // 2 * 2
                            
                        if video_codec == "hevc":
                            stage3_cmd.extend(["-c:v", "libx265", "-tag:v", "hvc1", "-crf", "33", "-preset", "medium"])
                            target_max = input_bitrate * 0.35
                            stage3_cmd.extend(["-maxrate", f"{int(target_max)}", "-bufsize", f"{int(target_max * 1.5)}"])
                        else:
                            stage3_cmd.extend(["-c:v", "libx264", "-crf", "29", "-preset", "medium"])
                            target_max = input_bitrate * 0.45
                            stage3_cmd.extend(["-maxrate", f"{int(target_max)}", "-bufsize", f"{int(target_max * 1.5)}"])
                            
                        stage3_cmd.extend(["-vf", f"scale={new_width}:-2", "-pix_fmt", "yuv420p"])
                        stage3_cmd.extend(["-c:a", "aac", "-b:a", "48k"])
                        stage3_cmd.append(output_path)
                        
                        return_code, stderr_content = run_ffmpeg_cmd(stage3_cmd)
                        if return_code == 0:
                            out_meta = get_video_metadata(output_path)
            
            with jobs_lock:
                job["status"] = "completed"
                job["progress"] = 100
                job["output_metadata"] = out_meta
                job["output_size"] = out_meta.get("size_bytes", 0)
                job["completed_at"] = time.time()
        else:
            # 读取最后的 stderr 信息作为错误描述
            err_msg = stderr_content[-500:] if stderr_content else f"FFmpeg 返回码 {return_code}"
            with jobs_lock:
                job["status"] = "failed"
                job["error"] = err_msg
                job["completed_at"] = time.time()
                
    except Exception as exc:
        with jobs_lock:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["completed_at"] = time.time()

# ── 启动后台服务 ──────────────────────────────────────────
def start_worker():
    global worker_thread
    if worker_thread is None or not worker_thread.is_alive():
        ensure_dirs()
        worker_thread = threading.Thread(target=_transcode_worker, daemon=True)
        worker_thread.start()

# ── API 接口定义 ──────────────────────────────────────────

@transcode_bp.route("/scan-downloads", methods=["GET"])
def scan_downloads():
    """扫描 WeChat 和 Douyin 文件夹下的所有视频文件"""
    ensure_dirs()
    video_list = []
    
    # 1. 递归扫描微信及视频号视频 (wechat)
    if OUTPUT_DIR.exists():
        for root, dirs, files in os.walk(str(OUTPUT_DIR)):
            for file in files:
                if file.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
                    f_path = Path(root) / file
                    
                    # 排除临时上传及转码输出目录，防自身循环干扰
                    if "temp_uploads" in f_path.parts or "transcoded" in f_path.parts:
                        continue
                        
                    # 仅限视频号已下载的视频，排除公众号文章目录中的视频（公众号下载不能转码）
                    try:
                        rel_parts = f_path.relative_to(OUTPUT_DIR).parts
                        if "channels" not in rel_parts:
                            continue
                        parent_name = "视频号已下载"
                    except Exception:
                        continue
                        
                    video_list.append({
                        "name": file,
                        "parent_name": parent_name,
                        "path": str(f_path.resolve()),
                        "size_bytes": f_path.stat().st_size,
                        "source": "wechat",
                        "created_at": f_path.stat().st_mtime
                    })
                            
    # 2. 扫描抖音视频 (douyin)
    # 路径为 data/douyin_downloads/*.mp4
    if DOUYIN_DIR.exists():
        for f in DOUYIN_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
                video_list.append({
                    "name": f.name,
                    "parent_name": "抖音已下载",
                    "path": str(f.resolve()),
                    "size_bytes": f.stat().st_size,
                    "source": "douyin",
                    "created_at": f.stat().st_mtime
                })
                
    # 降序排列 (按创建时间)
    video_list.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"success": True, "videos": video_list})

@transcode_bp.route("/video-info", methods=["POST"])
def video_info():
    """获取单个视频的详细元数据信息"""
    data = request.get_json() or {}
    file_path = data.get("path", "")
    if not file_path:
        return jsonify({"error": "路径不能为空"}), 400
        
    meta = get_video_metadata(file_path)
    if "error" in meta:
        return jsonify({"error": meta["error"]}), 500
    return jsonify(meta)

@transcode_bp.route("/upload", methods=["POST"])
def upload_file():
    """fallback 文件上传路由 (用于外部浏览器)"""
    ensure_dirs()
    if "file" not in request.files:
        return jsonify({"error": "未发现上传文件"}), 400
        
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "文件名为空"}), 400
        
    file_id = uuid.uuid4().hex[:8]
    ext = Path(f.filename).suffix
    save_name = f"upload_{file_id}{ext}"
    save_path = TEMP_DIR / save_name
    
    f.save(save_path)
    
    return jsonify({
        "success": True,
        "name": f.filename,
        "path": str(save_path.resolve()),
        "size_bytes": save_path.stat().st_size
    })

@transcode_bp.route("/start", methods=["POST"])
def start_transcode():
    """启动/排队转码任务"""
    start_worker()
    
    data = request.get_json() or {}
    input_path = data.get("input_path", "")
    params = data.get("params", {})
    
    if not input_path or not os.path.exists(input_path):
        return jsonify({"error": "找不到输入的视频文件"}), 400
        
    # 参数预校验与默认值
    output_format = params.get("output_format", "mp4").lower()
    if output_format not in ("mp4", "mkv", "mov", "webm", "mp3"):
        return jsonify({"error": "不支持的输出格式"}), 400
        
    job_id = uuid.uuid4().hex[:12]
    
    job = {
        "id": job_id,
        "input_name": Path(input_path).name,
        "input_path": input_path,
        "input_size": os.path.getsize(input_path),
        "output_path": None,
        "params": params,
        "status": "pending",
        "progress": 0,
        "speed": "0x",
        "fps": 0,
        "error": None,
        "created_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "input_metadata": None,
        "output_metadata": None,
        "output_size": 0
    }
    
    with jobs_lock:
        jobs[job_id] = job
        
    # 加入串口转码队列
    job_queue.put(job_id)
    
    return jsonify({"success": True, "job_id": job_id})

@transcode_bp.route("/status", methods=["GET"])
def get_status():
    """获取所有任务的运行状态列表"""
    with jobs_lock:
        return jsonify({
            "success": True,
            "jobs": list(jobs.values())
        })

@transcode_bp.route("/clear-completed", methods=["POST"])
def clear_completed():
    """清空已完成或失败的历史任务"""
    global jobs
    with jobs_lock:
        active_jobs = {}
        for jid, job in jobs.items():
            if job["status"] in ("pending", "running"):
                active_jobs[jid] = job
        jobs = active_jobs
    return jsonify({"success": True})

@transcode_bp.route("/open-parent", methods=["POST"])
def open_parent():
    """在资源管理器中打开目标文件并选中当前文件"""
    import subprocess
    import sys
    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400
        
    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件不存在"}), 404
            
        if path.is_file():
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(path)])
            elif sys.platform == "win32":
                subprocess.run(["explorer", f"/select,{path}"])
            else:
                subprocess.run(["xdg-open", str(path.parent)])
        else:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)])
            elif sys.platform == "win32":
                subprocess.run(["explorer", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        return jsonify({"message": "已打开"})
    except Exception as e:
        return jsonify({"error": f"打开失败: {str(e)}"}), 500

@transcode_bp.route("/resolve-path", methods=["POST"])
def resolve_path():
    """解析任意路径，如果是文件夹则寻找其中的第一个视频，如果是视频文件则直接返回"""
    data = request.get_json() or {}
    path_str = data.get("path", "")
    if not path_str:
        return jsonify({"error": "路径不能为空"}), 400
        
    try:
        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": "文件或文件夹不存在"}), 404
            
        # 安全检查：公众号下载的 HTML 或其目录下文件均不允许导入转码
        try:
            resolved_path = str(path.resolve())
            resolved_output_dir = str(OUTPUT_DIR.resolve())
            resolved_channels_dir = str((OUTPUT_DIR / "channels").resolve())
            if resolved_path.startswith(resolved_output_dir) and not resolved_path.startswith(resolved_channels_dir):
                return jsonify({"error": "公众号下载的文章不能导入转码"}), 400
        except Exception as e:
            print(f"检查路径归属失败: {e}")
            
        if path.is_file():
            if path.suffix.lower() in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
                return jsonify({
                    "success": True,
                    "path": str(path.resolve()),
                    "name": path.name,
                    "is_video": True
                })
            else:
                return jsonify({"error": "该文件不是支持的视频格式"}), 400
                
        # 如果是文件夹，递归寻找第一个视频
        for root, dirs, files in os.walk(str(path)):
            for file in files:
                if file.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
                    f_path = Path(root) / file
                    return jsonify({
                        "success": True,
                        "path": str(f_path.resolve()),
                        "name": file,
                        "is_video": True
                    })
                    
        return jsonify({"error": "该目录下未找到任何支持的视频文件"}), 400
    except Exception as e:
        return jsonify({"error": f"解析路径异常: {str(e)}"}), 500

@transcode_bp.route("/check-ffmpeg", methods=["GET"])
def check_ffmpeg():
    """检查系统是否安装了 ffmpeg/ffprobe"""
    import shutil
    has_ffmpeg = shutil.which("ffmpeg") is not None
    has_ffprobe = shutil.which("ffprobe") is not None
    return jsonify({
        "success": True,
        "available": has_ffmpeg and has_ffprobe
    })

# ── 垃圾清理 (临时上传文件) ───────────────────────────────────
def cleanup_temp_uploads():
    """启动时或定时清理临时上传文件夹"""
    try:
        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

cleanup_temp_uploads()
