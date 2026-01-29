import os
import random
import logging

class ImageManager:
    def __init__(self):
        self.data_root = os.getenv("DATA_ROOT", "data")
        self.image_dir = os.path.join(self.data_root, "image")
        self.image_pool = []
        self.refresh_index()

    def refresh_index(self):
        """
        递归扫描 image 目录，索引所有图片文件
        """
        new_pool = []
        if not os.path.exists(self.image_dir):
            os.makedirs(self.image_dir, exist_ok=True)
            self.image_pool = []
            return

        valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
        
        for root, dirs, files in os.walk(self.image_dir):
            for file in files:
                if file.lower().endswith(valid_extensions):
                    full_path = os.path.join(root, file)
                    new_pool.append(full_path)
        
        self.image_pool = new_pool
        logging.info(f"图片库索引已刷新，共发现 {len(self.image_pool)} 张图片")

    def get_random_image(self):
        """
        从索引池中随机抽取一张图片
        """
        if not self.image_pool:
            return None
        return random.choice(self.image_pool)
