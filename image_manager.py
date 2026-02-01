import os
import random
import logging

class ImageManager:
    def __init__(self):
        self.data_root = os.getenv("DATA_ROOT", "data")
        self.image_dir = os.path.join(self.data_root, "image")
        self.image_pool = []  # 全量图片池
        self.person_pool = {} # 按人物分类的图片池 {"长谷川育美": [路径1, 路径2]}
        self.refresh_index()

    def refresh_index(self):
        """
        递归扫描 image 目录，按照一级目录名作为人物名建立索引
        """
        new_all_pool = []
        new_person_pool = {}
        
        if not os.path.exists(self.image_dir):
            os.makedirs(self.image_dir, exist_ok=True)
            self.image_pool = []
            self.person_pool = {}
            return

        valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
        
        # 获取 image 目录下的所有一级项目
        for item in os.listdir(self.image_dir):
            item_path = os.path.join(self.image_dir, item)
            
            if os.path.isdir(item_path):
                # 如果是目录，则该目录名就是人物名（如 "长谷川育美" 或 "未分类"）
                person_name = item
                if person_name not in new_person_pool:
                    new_person_pool[person_name] = []
                
                # 递归扫描该人物目录下的所有图片
                for root, _, files in os.walk(item_path):
                    for file in files:
                        if file.lower().endswith(valid_extensions):
                            full_path = os.path.join(root, file)
                            new_person_pool[person_name].append(full_path)
                            new_all_pool.append(full_path)
            else:
                # 如果直接在 image 目录下有图片文件，归类到 "未分类"
                if item.lower().endswith(valid_extensions):
                    if "未分类" not in new_person_pool:
                        new_person_pool["未分类"] = []
                    new_person_pool["未分类"].append(item_path)
                    new_all_pool.append(item_path)
        
        self.image_pool = new_all_pool
        self.person_pool = new_person_pool
        logging.info(f"图片库索引已刷新：共发现 {len(self.image_pool)} 张图片，涉及 {len(self.person_pool)} 个分类/人物")

    def get_random_image(self, person_names=None):
        """
        从索引池中随机抽取一张图片
        :param person_names: 指定人物名或名字列表。如果为 None 或找不到对应人物，则按逻辑回退。
        """
        # 统一转为列表处理
        if isinstance(person_names, str):
            person_names = [person_names]
        
        if person_names:
            combined_pool = []
            valid_names = []
            for name in person_names:
                if name in self.person_pool and self.person_pool[name]:
                    combined_pool.extend(self.person_pool[name])
                    valid_names.append(name)
            
            if combined_pool:
                # 随机去重（如果有重复路径）
                combined_pool = list(set(combined_pool))
                logging.info(f"正在从人物 【{'、'.join(valid_names)}】 的合并图库中随机抽取")
                return random.choice(combined_pool)
            else:
                logging.info(f"未找到人物 【{'、'.join(person_names)}】 的任何图片，将从全量图库中随机抽取")
        
        # 兜底：从全量池中抽取
        if not self.image_pool:
            return None
            
        return random.choice(self.image_pool)

    def get_all_persons(self):
        """获取当前图库中所有合法的人物名及图片统计"""
        # 返回格式: [{"name": "羊宫妃那", "count": 10}, ...]
        result = []
        for name, pool in self.person_pool.items():
            result.append({
                "name": name,
                "count": len(pool)
            })
        # 按图片数量排序
        return sorted(result, key=lambda x: x["count"], reverse=True)

    def is_person_exists(self, name):
        """检查某个人物文件夹是否存在"""
        return name in self.person_pool
