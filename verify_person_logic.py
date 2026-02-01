import re
import os
import logging
from dotenv import load_dotenv, find_dotenv
from image_manager import ImageManager

# 加载环境变量
load_dotenv(find_dotenv())

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def test_parsing_logic():
    print("=== 测试指令解析 (模拟 server.py 逻辑) ===")
    test_cases = [
        "上传图片 羊宫妃那 青木阳菜",
        "上传图片",
        "随机图片 羊宫妃那 青木阳菜",
        "随机图片",
        "随机图片 长谷川育美",
    ]
    
    for text in test_cases:
        if "上传图片" in text:
            names_part = text.partition("上传图片")[2].strip()
            person_names = names_part.split() if names_part else ["未分类"]
            print(f"输入: '{text}' -> 解析人物列表: {person_names}")
        elif "随机图片" in text:
            names_part = text.partition("随机图片")[2].strip()
            person_names = names_part.split() if names_part else None
            print(f"输入: '{text}' -> 解析人物列表: {person_names}")

def test_image_manager_logic():
    print("\n=== 测试 ImageManager 多人合并逻辑 ===")
    print(f"当前 DATA_ROOT: {os.getenv('DATA_ROOT')}")
    
    im = ImageManager()
    
    print(f"总图片数: {len(im.image_pool)}")
    print(f"分类列表: {list(im.person_pool.keys())}")
    
    # 找两个存在的分类进行测试
    keys = list(im.person_pool.keys())
    if len(keys) >= 2:
        test_names = keys[:2]
        print(f"测试多人合并抽取: {test_names}")
        img = im.get_random_image(test_names)
        res = img if img else "无图"
        print(f"  -> 结果: {res}")
    
    # 测试单个名字
    if keys:
        test_name = keys[0]
        print(f"测试单人抽取: {test_name}")
        img = im.get_random_image(test_name)
        print(f"  -> 结果: {img}")

    # 测试不存在的人物组合
    print(f"测试不存在的人物组合: ['虚构1', '虚构2']")
    img_none = im.get_random_image(['虚构1', '虚构2'])
    print(f"  -> 结果: {img_none} (预期从全量池抽取)")


if __name__ == "__main__":
    test_parsing_logic()
    test_image_manager_logic()
