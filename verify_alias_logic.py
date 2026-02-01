import os
import logging
from alias_manager import AliasManager
from image_manager import ImageManager
from dotenv import load_dotenv, find_dotenv

# 加载环境变量
load_dotenv(find_dotenv())

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def test_alias_logic():
    print("=== 测试 AliasManager 核心逻辑 ===")
    am = AliasManager("test_alias.json")
    
    # 1. 测试添加别名
    print("\n1. 测试添加别名:")
    am.add_alias("圣青木", "青木阳菜")
    am.add_alias("ao-chan", "青木阳菜")
    am.add_alias("羊姐", "羊宫妃那")
    print(f"  - 正向索引: {am.alias_to_name}")
    print(f"  - 反向索引: {am.name_to_aliases}")

    # 2. 测试解析逻辑
    print("\n2. 测试名称解析:")
    print(f"  - '圣青木' -> {am.resolve('圣青木')}")
    print(f"  - '羊姐' -> {am.resolve('羊姐')}")
    print(f"  - '普通路人' -> {am.resolve('普通路人')}")
    print(f"  - 批量解析 ['圣青木', '羊姐', '未知'] -> {am.resolve_list(['圣青木', '羊姐', '未知'])}")

    # 3. 测试覆盖更新
    print("\n3. 测试覆盖更新 (将 '圣青木' 改为指向 '羊宫妃那'，虽然不合常理但用于测试逻辑):")
    am.add_alias("圣青木", "羊宫妃那")
    print(f"  - '圣青木' 现在的归属: {am.alias_to_name.get('圣青木')}")
    print(f"  - '青木阳菜' 的别名列表: {am.get_aliases_by_name('青木阳菜')}")
    print(f"  - '羊宫妃那' 的别名列表: {am.get_aliases_by_name('羊宫妃那')}")

    # 4. 测试删除
    print("\n4. 测试删除别名:")
    am.delete_alias("ao-chan")
    print(f"  - 'ao-chan' 是否还在: {am.get_canonical_name('ao-chan')}")
    print(f"  - '青木阳菜' 剩余别名: {am.get_aliases_by_name('青木阳菜')}")

    # 清理测试文件
    if os.path.exists("test_alias.json"):
        os.remove("test_alias.json")

def test_integration_logic():
    print("\n=== 测试与图库集成逻辑 ===")
    im = ImageManager()
    am = AliasManager("temp_alias.json")
    
    persons = im.get_all_persons()
    if not persons:
        print("图库为空，无法进行集成测试。")
        return

    # 拿第一个声优做测试
    canonical_name = persons[0]['name']
    alias = "测试小名"
    
    print(f"目标声优: {canonical_name}")
    
    # 模拟指令: 添加别名
    if im.is_person_exists(canonical_name):
        am.add_alias(alias, canonical_name)
        print(f"成功绑定别名: {alias} -> {canonical_name}")
    
    # 模拟指令: 随机图片 [别名]
    resolved_name = am.resolve(alias)
    print(f"指令解析: '随机图片 {alias}' -> 解析为人物: {resolved_name}")
    
    img = im.get_random_image(resolved_name)
    print(f"抽取结果: {img}")

    # 清理
    if os.path.exists("temp_alias.json"):
        os.remove("temp_alias.json")

def test_batch_alias_parsing():
    print("\n=== 测试批量别名解析逻辑 (模拟 server.py) ===")
    import re
    test_cases = [
        "添加别名 圣青木;猪咪 青木阳菜",
        "添加别名 圣青木；猪咪，ao-chan 青木阳菜",
        "添加别名 圣青木 | 猪咪 | 哈密瓜派 青木阳菜",
        "添加别名 圣青木 青木阳菜",  # 单个
    ]
    
    for text in test_cases:
        raw_part = text.partition("添加别名")[2].strip()
        parts = raw_part.split()
        if len(parts) >= 2:
            canonical_name = parts[-1]
            alias_raw = " ".join(parts[:-1])
            aliases = [a.strip() for a in re.split(r'[;；,，|\t]+', alias_raw) if a.strip()]
            print(f"输入: '{text}'")
            print(f"  -> 本名: {canonical_name}")
            print(f"  -> 解析别名列表: {aliases}")

if __name__ == "__main__":
    test_alias_logic()
    test_batch_alias_parsing()
    test_integration_logic()
