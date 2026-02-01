import os
import json
import logging

class AliasManager:
    def __init__(self, config_filename="alias.json"):
        self.data_root = os.getenv("DATA_ROOT", "data")
        self.config_path = os.path.join(self.data_root, config_filename)
        self.alias_to_name = {}  # 别名 -> 规范名 (e.g. {"圣青木": "青木阳菜"})
        self.name_to_aliases = {} # 规范名 -> [别名列表] (e.g. {"青木阳菜": ["圣青木"]})
        self.load()

    def load(self):
        """从 JSON 加载别名配置并构建双向索引"""
        if not os.path.exists(self.config_path):
            self.alias_to_name = {}
            self.name_to_aliases = {}
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.alias_to_name = json.load(f)
            self._rebuild_reverse_index()
            logging.info(f"别名配置加载成功，共 {len(self.alias_to_name)} 条别名映射")
        except Exception as e:
            logging.error(f"加载别名配置失败: {e}")
            self.alias_to_name = {}
            self.name_to_aliases = {}

    def _rebuild_reverse_index(self):
        """重新构建反向索引"""
        self.name_to_aliases = {}
        for alias, name in self.alias_to_name.items():
            if name not in self.name_to_aliases:
                self.name_to_aliases[name] = []
            if alias not in self.name_to_aliases[name]:
                self.name_to_aliases[name].append(alias)

    def save(self):
        """保存别名配置到 JSON"""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.alias_to_name, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            logging.error(f"保存别名配置失败: {e}")
            return False

    def add_alias(self, alias, name):
        """添加或更新别名"""
        # 如果该别名原本属于另一个人，先从旧人的反向索引中移除
        old_name = self.alias_to_name.get(alias)
        if old_name and old_name in self.name_to_aliases:
            if alias in self.name_to_aliases[old_name]:
                self.name_to_aliases[old_name].remove(alias)

        # 更新正向索引
        self.alias_to_name[alias] = name
        
        # 更新反向索引
        if name not in self.name_to_aliases:
            self.name_to_aliases[name] = []
        if alias not in self.name_to_aliases[name]:
            self.name_to_aliases[name].append(alias)
            
        return self.save()

    def delete_alias(self, alias):
        """删除别名"""
        if alias in self.alias_to_name:
            name = self.alias_to_name[alias]
            del self.alias_to_name[alias]
            if name in self.name_to_aliases and alias in self.name_to_aliases[name]:
                self.name_to_aliases[name].remove(alias)
            return self.save()
        return False

    def resolve(self, input_name):
        """解析名称：如果是别名则返回规范名，否则原样返回"""
        return self.alias_to_name.get(input_name, input_name)

    def resolve_list(self, names):
        """批量解析名称列表"""
        if not names:
            return names
        return [self.resolve(n) for n in names]

    def get_aliases_by_name(self, name):
        """获取某人的所有别名"""
        return self.name_to_aliases.get(name, [])

    def get_canonical_name(self, alias):
        """获取某个别名对应的规范名"""
        return self.alias_to_name.get(alias)
