import os
import random
import logging
from typing import List, Dict

class VoiceManager:
    """
    语音资源管理器
    负责扫描目录、建立索引并提供随机抽取功能
    """
    def __init__(self):
        self.data_root = os.getenv("DATA_ROOT", "data")
        self.voice_base_dir = os.path.join(self.data_root, "voice")
        self.voice_pool: List[Dict[str, str]] = []
        
        # 支持的音频格式
        self.supported_extensions = {".mp3", ".wav", ".opus", ".m4a"}
        
        # 初始化扫描
        self.refresh_index()

    def refresh_index(self):
        """扫描目录并建立索引"""
        if not os.path.exists(self.voice_base_dir):
            logging.warning(f"语音目录不存在: {self.voice_base_dir}")
            return

        new_pool = []
        # 遍历 data_root/voice 下的所有文件夹
        for cv_name in os.listdir(self.voice_base_dir):
            cv_dir = os.path.join(self.voice_base_dir, cv_name)
            if not os.path.isdir(cv_dir):
                continue
            
            # 查找该声优文件夹下的头像文件
            avatar_path = None
            for f in os.listdir(cv_dir):
                if f.lower().startswith('avatar') and os.path.splitext(f)[1].lower() in {'.jpg', '.jpeg', '.png'}:
                    avatar_path = os.path.join(cv_dir, f)
                    break

            # 遍历声优文件夹下的所有文件
            for root, _, files in os.walk(cv_dir):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in self.supported_extensions:
                        full_path = os.path.join(root, file)
                        new_pool.append({
                            "path": full_path,
                            "file_name": file,
                            "cv_name": cv_name,
                            "avatar_path": avatar_path
                        })
        
        self.voice_pool = new_pool
        logging.info(f"语音索引已更新，共加载 {len(self.voice_pool)} 条试音素材")

    def get_random_voice(self) -> Dict[str, str]:
        """随机获取一条语音信息"""
        if not self.voice_pool:
            return None
        return random.choice(self.voice_pool)
