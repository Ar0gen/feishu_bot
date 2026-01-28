#!/usr/bin/env python3.8
import os
import subprocess
import logging
import tempfile

class Obj(dict):
    def __init__(self, d):
        for a, b in d.items():
            if isinstance(b, (list, tuple)):
                setattr(self, a, [Obj(x) if isinstance(x, dict) else x for x in b])
            else:
                setattr(self, a, Obj(b) if isinstance(b, dict) else b)


def dict_2_obj(d: dict):
    return Obj(d)


def convert_to_opus(input_path: str) -> str:
    """
    将音频文件转换为 opus 格式，这是飞书语音消息要求的格式。
    返回转换后的临时文件路径。
    """
    if not os.path.exists(input_path):
        logging.error(f"转换文件不存在: {input_path}")
        return None
    
    # 创建临时文件
    temp_dir = tempfile.gettempdir()
    output_filename = os.path.splitext(os.path.basename(input_path))[0] + ".opus"
    output_path = os.path.join(temp_dir, output_filename)
    
    try:
        # 使用 ffmpeg 进行转换
        # 飞书建议的参数: opus 编码, 16000 采样率, 单声道
        command = [
            'ffmpeg', '-y', 
            '-i', input_path, 
            '-acodec', 'libopus', 
            '-ar', '16000', 
            '-ac', '1', 
            output_path
        ]
        logging.info(f"正在转换音频: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode == 0:
            logging.info(f"音频转换成功: {output_path}")
            return output_path
        else:
            logging.error(f"音频转换失败: {result.stderr}")
            return None
    except Exception as e:
        logging.error(f"音频转换异常: {e}")
        return None


def get_audio_duration(file_path: str) -> int:
    """
    获取音频文件的时长，单位毫秒。
    """
    if not os.path.exists(file_path):
        return 0
    
    try:
        command = [
            'ffprobe', '-v', 'error', 
            '-show_entries', 'format=duration', 
            '-of', 'default=noprint_wrappers=1:nokey=1', 
            file_path
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            duration_sec = float(result.stdout.strip())
            return int(duration_sec * 1000)
        else:
            logging.error(f"获取时长失败: {result.stderr}")
            return 0
    except Exception as e:
        logging.error(f"获取时长异常: {e}")
        return 0
