import os
import json
import logging

class ConfigManager:
    def __init__(self):
        self.data_root = os.getenv("DATA_ROOT", "data")
        self.config_path = os.path.join(self.data_root, "config.json")
        self.config = {
            "daily_push_chats": []  # 存储开启了每日一图的 chat_id 列表
        }
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
            except Exception as e:
                logging.error(f"加载配置文件失败: {e}")
        else:
            self.save_config()

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"保存配置文件失败: {e}")

    def is_daily_push_enabled(self, chat_id):
        return chat_id in self.config.get("daily_push_chats", [])

    def enable_daily_push(self, chat_id):
        if chat_id not in self.config["daily_push_chats"]:
            self.config["daily_push_chats"].append(chat_id)
            self.save_config()
            return True
        return False

    def disable_daily_push(self, chat_id):
        if chat_id in self.config["daily_push_chats"]:
            self.config["daily_push_chats"].remove(chat_id)
            self.save_config()
            return True
        return False
