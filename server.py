#!/usr/bin/env python3.8

import os
import logging
import requests
import re
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from api import MessageApiClient
from event import MessageReceiveEvent, UrlVerificationEvent, EventManager
from flask import Flask, jsonify, json
from dotenv import load_dotenv, find_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from twitter_rss import TwitterRssFetcher
from voice_manager import VoiceManager
from image_manager import ImageManager
from config_manager import ConfigManager
from alias_manager import AliasManager
from utils import get_audio_duration

# load env parameters form file named .env
load_dotenv(find_dotenv())

app = Flask(__name__)

# load from env
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
VERIFICATION_TOKEN = os.getenv("VERIFICATION_TOKEN")
ENCRYPT_KEY = os.getenv("ENCRYPT_KEY")
LARK_HOST = os.getenv("LARK_HOST")
SHEET_TOKEN = os.getenv("SHEET_TOKEN")
SHEET_RANGE = os.getenv("SHEET_RANGE")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

# init service
message_api_client = MessageApiClient(APP_ID, APP_SECRET, LARK_HOST)
event_manager = EventManager()
voice_manager = VoiceManager()
image_manager = ImageManager()
config_manager = ConfigManager()
alias_manager = AliasManager()

# 初始化线程池，处理耗时业务逻辑
message_executor = ThreadPoolExecutor(max_workers=10)

# 要监控的推特用户列表
MONITOR_USERS = ["aoki__hina"]

# 用于消息去重的事件 ID 队列
PROCESSED_EVENT_IDS = deque(maxlen=1000)


def get_monitor_users():
    """
    从飞书电子表格获取需要监控的用户列表。
    如果获取失败，则返回硬编码的兜底列表。
    返回格式: [{"name": "显示名称", "twitter_id": "推特账号"}, ...]
    """
    # 默认兜底数据格式化
    default_users = [{"name": user, "twitter_id": user} for user in MONITOR_USERS]
    
    if not SHEET_TOKEN or not SHEET_RANGE or "YCEpsvFQPhsptmtcZrocDlrpnMd" not in SHEET_TOKEN:
        if "XXXXXX" in SHEET_TOKEN:
            logging.warning("未配置有效的 Sheet Token，使用默认监控列表")
            return default_users
    
    try:
        rows = message_api_client.get_spreadsheet_values(SHEET_TOKEN, SHEET_RANGE)
        
        # 1. 严格预过滤：只有当“名字”和“推特账号”两列都有实际值时才保留，任何一个为 None 或空字符串都筛掉
        valid_rows = [
            r for r in rows 
            if len(r) >= 2 and r[0] and str(r[0]).strip() and r[1] and str(r[1]).strip()
        ]
        
        users = []
        seen_ids = set()
        for row in valid_rows:
            # 2. 清洗账号 ID
            twitter_id = str(row[1]).strip().replace('@', '')
            display_name = str(row[0]).strip()
            
            # 3. 过滤表头文字和重复项
            if twitter_id.lower() in ["twitter_id", "twitter"] or twitter_id in seen_ids:
                continue
            
            users.append({
                "name": display_name,
                "twitter_id": twitter_id
            })
            seen_ids.add(twitter_id)
        
        if not users:
            logging.warning("电子表格中未找到有效的监控配置（需要姓名和账号均不为空）")
            return default_users
        
        return users
    except Exception as e:
        logging.error(f"获取电子表格监控列表时发生异常: {e}")
        return default_users


def check_twitter_updates():
    logging.info("开始检查推特更新...")
    users_to_monitor = get_monitor_users()
    logging.info(f"本次监控用户列表: {[u['twitter_id'] for u in users_to_monitor]}")
    
    # 获取机器人所在的所有群聊
    try:
        chats = message_api_client.get_bot_chats()
        target_chat_ids = [chat['chat_id'] for chat in chats]
        if not target_chat_ids:
            logging.warning("未发现机器人所在的群聊，跳过推送")
            return
    except Exception as e:
        logging.error(f"获取群聊列表失败: {e}")
        return

    for user_info in users_to_monitor:
        username = user_info['twitter_id']
        display_name = user_info['name']
        try:
            fetcher = TwitterRssFetcher(username)
            new_tweets = fetcher.get_new_tweets()
            if new_tweets:
                for tweet in new_tweets:
                    logging.info(f"【定时任务】发现新推文: @{username} ({display_name}) - {tweet['id']}，准备推送卡片...")
                    
                    # 1. 上传图片获取 image_keys
                    image_keys = []
                    for img_url in tweet.get('images', []):
                        try:
                            key = message_api_client.upload_image(img_url)
                            if key:
                                image_keys.append(key)
                        except Exception as img_err:
                            logging.error(f"图片上传失败: {img_url}, error: {img_err}")
                    
                    # 2. 组装卡片 JSON
                    card_json = fetcher.build_tweet_card(tweet, image_keys, display_name=display_name)
                    
                    # 3. 广播发送卡片到所有群聊
                    for chat_id in target_chat_ids:
                        try:
                            message_api_client.send_card("chat_id", chat_id, card_json)
                            logging.info(f"推文 {tweet['id']} 推送到群聊 {chat_id} 成功。")
                        except Exception as send_err:
                            logging.error(f"推文 {tweet['id']} 推送到群聊 {chat_id} 失败: {send_err}")
            else:
                logging.info(f"用户 @{username} 无新推文")
        except Exception as e:
            logging.error(f"检查用户 @{username} 更新时出错: {e}")
    logging.info("推特更新检查完成。")


def refresh_voice_index():
    logging.info("开始定时刷新语音库索引...")
    voice_manager.refresh_index()
    logging.info(f"语音库索引刷新完成，当前共有 {len(voice_manager.voice_pool)} 条语音素材")


def refresh_image_index():
    logging.info("开始定时刷新图片库索引...")
    image_manager.refresh_index()
    logging.info(f"图片库索引刷新完成，当前共有 {len(image_manager.image_pool)} 条图片素材")


def send_daily_push(text):
    """
    每日一图广播功能
    """
    logging.info(f"开始执行每日推送: {text}")
    
    # 1. 获取所有群聊
    try:
        chats = message_api_client.get_bot_chats()
        all_chat_ids = [chat['chat_id'] for chat in chats]
        
        # 过滤出开启了每日一图推送的群聊
        target_chat_ids = [cid for cid in all_chat_ids if config_manager.is_daily_push_enabled(cid)]
        
        if not target_chat_ids:
            logging.warning("未发现开启了每日一图推送的群聊，跳过推送")
            return
    except Exception as e:
        logging.error(f"每日推送获取群聊失败: {e}")
        return

    # 2. 随机抽取图片
    img_path = image_manager.get_random_image()
    if not img_path:
        logging.warning("图库为空，跳过每日推送")
        return

    try:
        # 3. 上传图片
        img_key = message_api_client.upload_image(img_path)
        
        # 4. 构造富文本
        post_content = {
            "zh_cn": {
                "title": "📅 每日一图",
                "content": [
                    [{"tag": "text", "text": text}],
                    [{"tag": "img", "image_key": img_key}]
                ]
            }
        }
        
        # 5. 广播
        for chat_id in target_chat_ids:
            try:
                message_api_client.send_post("chat_id", chat_id, post_content)
                logging.info(f"每日推送成功发送至: {chat_id}")
            except Exception as e:
                logging.error(f"每日推送发送至 {chat_id} 失败: {e}")
                
    except Exception as e:
        logging.error(f"每日推送准备过程出错: {e}")


# 初始化定时任务
scheduler = BackgroundScheduler()
scheduler.add_job(check_twitter_updates, "interval", minutes=1)
scheduler.add_job(refresh_voice_index, "interval", minutes=5)
scheduler.add_job(refresh_image_index, "interval", minutes=5)

# 每日一图定时任务（北京时间）
# 工作日早上 10:00
scheduler.add_job(send_daily_push, 'cron', day_of_week='mon-fri', hour=10, minute=0, args=["今天工作加油 💪"])
# 工作日晚上 20:30
scheduler.add_job(send_daily_push, 'cron', day_of_week='mon-fri', hour=20, minute=30, args=["今天辛苦啦 🌟"])
# 休息日早上 10:30
scheduler.add_job(send_daily_push, 'cron', day_of_week='sat-sun', hour=10, minute=30, args=["休息日快乐 🎈"])


@event_manager.register("url_verification")
def request_url_verify_handler(req_data: UrlVerificationEvent):
    # url verification, just need return challenge
    if req_data.event.token != VERIFICATION_TOKEN:
        raise Exception("VERIFICATION_TOKEN is invalid")
    return jsonify({"challenge": req_data.event.challenge})


@event_manager.register("im.message.receive_v1")
def message_receive_event_handler(req_data: MessageReceiveEvent):
    # 1. 消息去重逻辑
    event_id = req_data.header.event_id
    if event_id in PROCESSED_EVENT_IDS:
        logging.info(f"忽略重复事件: {event_id}")
        return jsonify()
    
    # 记录事件 ID
    PROCESSED_EVENT_IDS.append(event_id)

    logging.info(f"Received message: {req_data}")
    # 异步处理业务逻辑
    message_executor.submit(handle_message_logic, req_data)
    
    # 立即向飞书返回响应，避免重试
    return jsonify()


def handle_message_logic(req_data: MessageReceiveEvent):
    """
    具体的业务逻辑处理函数，在独立线程中运行
    """
    message = req_data.event.message
    sender_id = req_data.event.sender.sender_id
    
    if message.message_type not in ["text", "post"]:
        logging.warn(f"Message type {message.message_type} has not been processed yet")
        return

    chat_id = message.chat_id
    chat_type = message.chat_type
    
    # 解析消息内容
    text_content = ""
    image_keys = []
    
    try:
        content_dict = json.loads(message.content)
        if message.message_type == "text":
            text_content = content_dict.get("text", "")
        elif message.message_type == "post":
            # 提取 post 中的所有文本和图片 key
            text_parts = []
            for row in content_dict.get("content", []):
                for item in row:
                    if item.get("tag") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("tag") == "img":
                        image_keys.append(item.get("image_key"))
            text_content = "".join(text_parts)
    except Exception as e:
        logging.error(f"解析消息内容失败: {e}")
        text_content = message.content

    # 只有在私聊，或者群聊被 @ 时才响应指令
    should_process_command = (chat_type == "p2p") or (chat_type == "group" and message.mentions)
    
    if should_process_command:
        # 逻辑 1：上传图片指令（支持指定多个或多个人物名，支持别名）
        if "上传图片" in text_content and image_keys:
            # 提取关键词后面的所有内容并按空格分割
            names_part = text_content.partition("上传图片")[2].strip()
            input_names = names_part.split() if names_part else ["未分类"]
            
            # 别名解析转换
            person_names = alias_manager.resolve_list(input_names)
            
            logging.info(f"触发上传图片逻辑。原始输入: {input_names}, 转换后: {person_names}, ChatID: {chat_id}")
            try:
                data_root = os.getenv("DATA_ROOT", "data")
                date_str = datetime.now().strftime("%Y-%m-%d")
                
                saved_count = 0
                for img_key in image_keys:
                    try:
                        # 1. 下载资源（只需下载一次）
                        img_data = message_api_client.get_message_resource(message.message_id, img_key, "image")
                        
                        # 2. 分别保存到所有指定的人物文件夹中
                        for person_name in person_names:
                            # 存储路径：data/image/人物名/日期/
                            save_dir = os.path.join(data_root, "image", person_name, date_str)
                            os.makedirs(save_dir, exist_ok=True)
                            save_path = os.path.join(save_dir, f"{img_key}.jpg")
                            with open(save_path, "wb") as f:
                                f.write(img_data)
                        
                        saved_count += 1
                    except Exception as dl_err:
                        logging.error(f"保存图片 {img_key} 失败: {dl_err}")

                feedback_names = "】、【".join(person_names)
                feedback_msg = f"成功保存了 {saved_count} 张图片到 【{feedback_names}】 的图库。"
                message_api_client.send_text("chat_id", chat_id, feedback_msg)
                
                # 如果有新图片保存成功，立即刷新图库索引
                if saved_count > 0:
                    image_manager.refresh_index()
            except Exception as e:
                logging.error(f"上传图片逻辑出错: {e}")
                message_api_client.send_text("chat_id", chat_id, "保存图片时出了点问题...")
            return

        # 逻辑 2：随机语音指令
        VOICE_KEYWORDS = ["随机语音", "抽一个", "试音", "来一句", "试听"]
        if any(kw in text_content for kw in VOICE_KEYWORDS):
            logging.info(f"触发语音回复逻辑。类型: {chat_type}, ChatID: {chat_id}, 内容: {text_content}")
            voice_info = voice_manager.get_random_voice()
            if voice_info:
                try:
                    post_content = {
                        "zh_cn": {
                            "title": "✨ 语音掉落",
                            "content": [
                                [{"tag": "text", "text": f"叮咚！为您捕捉到了 【{voice_info['cv_name']}】 的一段语音，请查收~ 🎧"}]
                            ]
                        }
                    }
                    if voice_info.get('avatar_path'):
                        try:
                            img_key = message_api_client.upload_image(voice_info['avatar_path'])
                            if img_key:
                                post_content["zh_cn"]["content"].append([{"tag": "img", "image_key": img_key}])
                        except Exception as img_err:
                            logging.error(f"头像上传失败: {img_err}")
                    message_api_client.send_post("chat_id", chat_id, post_content)
                    duration = get_audio_duration(voice_info['path'])
                    file_key = message_api_client.upload_file(voice_info['path'], "opus", voice_info['file_name'], duration)
                    message_api_client.send_audio("chat_id", chat_id, file_key)
                except Exception as e:
                    logging.error(f"随机语音回复失败: {e}")
                    message_api_client.send_text("chat_id", chat_id, "抱歉，语音抽取失败了...")
            else:
                message_api_client.send_text("chat_id", chat_id, "语音库目前是空的哦。")
            return

        # 逻辑 3：随机图片指令（支持指定一个或多个人物名，支持别名）
        if "随机图片" in text_content:
            # 提取关键词后面的所有内容并按空格分割
            names_part = text_content.partition("随机图片")[2].strip()
            input_names = names_part.split() if names_part else None
            
            # 别名解析转换
            person_names = alias_manager.resolve_list(input_names) if input_names else None
            
            logging.info(f"触发随机图片逻辑。目标人物列表: {person_names}, ChatID: {chat_id}")
            img_path = image_manager.get_random_image(person_names)
            if img_path:
                try:
                    # 1. 上传图片获取 image_key
                    img_key = message_api_client.upload_image(img_path)
                    
                    # 2. 构造富文本内容
                    post_content = {
                        "zh_cn": {
                            "title": "🖼️ 随机美图",
                            "content": [
                                [{"tag": "text", "text": "您要的今日份女声优 ✨"}]
                            ]
                        }
                    }
                    post_content["zh_cn"]["content"].append([{"tag": "img", "image_key": img_key}])
                    
                    # 3. 发送富文本
                    message_api_client.send_post("chat_id", chat_id, post_content)
                except Exception as e:
                    logging.error(f"随机图片发送失败: {e}")
                    message_api_client.send_text("chat_id", chat_id, "抱歉，图片抽取失败了...")
            else:
                if not person_names:
                    msg = "图库目前是空的哦。"
                else:
                    feedback_names = "】、【".join(person_names)
                    msg = f"还没有搜集到 【{feedback_names}】 的图片哦。"
                message_api_client.send_text("chat_id", chat_id, msg)
            return

        # 逻辑 4：推送控制指令
        if "开启每日一图" in text_content:
            if config_manager.enable_daily_push(chat_id):
                message_api_client.send_text("chat_id", chat_id, "✅ 已为您开启本群的【每日一图】推送功能。")
            else:
                message_api_client.send_text("chat_id", chat_id, "ℹ️ 本群已经开启过推送功能啦。")
            return

        if "关闭每日一图" in text_content:
            if config_manager.disable_daily_push(chat_id):
                message_api_client.send_text("chat_id", chat_id, "❌ 已为您关闭本群的【每日一图】推送功能。")
            else:
                message_api_client.send_text("chat_id", chat_id, "ℹ️ 本群之前就没有开启推送功能哦。")
            return

        if "推送状态" in text_content:
            status = "✅ 已开启" if config_manager.is_daily_push_enabled(chat_id) else "❌ 未开启"
            message_api_client.send_text("chat_id", chat_id, f"📊 【每日一图】推送状态：{status}")
            return

        # 逻辑 5：别名管理指令
        if "添加别名" in text_content:
            # 格式：添加别名 [别名1;别名2] [规范名]
            raw_part = text_content.partition("添加别名")[2].strip()
            parts = raw_part.split()
            if len(parts) >= 2:
                canonical_name = parts[-1]  # 最后一个词作为本名
                alias_raw = " ".join(parts[:-1]) # 前面部分作为别名
                
                # 支持多种分隔符：分号、逗号、竖线、空格
                aliases = [a.strip() for a in re.split(r'[;；,，|\t]+', alias_raw) if a.strip()]
                
                if not aliases:
                    message_api_client.send_text("chat_id", chat_id, "❌ 未识别到有效的别名，请检查格式。")
                    return

                # 强校验：规范名必须存在于图库中
                if image_manager.is_person_exists(canonical_name):
                    success_list = []
                    for alias in aliases:
                        if alias_manager.add_alias(alias, canonical_name):
                            success_list.append(alias)
                    
                    if success_list:
                        aliases_str = "】、【".join(success_list)
                        message_api_client.send_text("chat_id", chat_id, f"✅ 绑定成功！现在可以用 【{aliases_str}】 来指代 【{canonical_name}】 啦。")
                    else:
                        message_api_client.send_text("chat_id", chat_id, "❌ 别名保存失败，请稍后再试。")
                else:
                    message_api_client.send_text("chat_id", chat_id, f"❌ 图库中还没有 【{canonical_name}】 的文件夹噢，请先上传照片或手动创建文件夹。")
            else:
                message_api_client.send_text("chat_id", chat_id, "ℹ️ 用法提示：添加别名 [别名1;别名2] [本名]\n例如：添加别名 圣青木;猪咪 青木阳菜")
            return

        if "别名列表" in text_content or "查看别名" in text_content:
            # 格式：别名列表 [本名]
            keyword = "别名列表" if "别名列表" in text_content else "查看别名"
            target_name = text_content.partition(keyword)[2].strip()
            if target_name:
                aliases = alias_manager.get_aliases_by_name(target_name)
                if aliases:
                    msg = f"🔍 【{target_name}】 的已知别名有：\n" + "、".join(aliases)
                else:
                    msg = f"ℹ️ 暂时还没有人为 【{target_name}】 添加过别名哦。"
                message_api_client.send_text("chat_id", chat_id, msg)
            else:
                message_api_client.send_text("chat_id", chat_id, "ℹ️ 用法提示：别名列表 [本名]")
            return

        if "谁是" in text_content:
            alias = text_content.partition("谁是")[2].strip()
            if alias:
                canonical_name = alias_manager.get_canonical_name(alias)
                if canonical_name:
                    message_api_client.send_text("chat_id", chat_id, f"💡 【{alias}】 就是 【{canonical_name}】 哒！")
                else:
                    message_api_client.send_text("chat_id", chat_id, f"❓ 机器人还不认识 【{alias}】 呢，你可以用“添加别名”指令教教我。")
            return

        # 逻辑 6：图库查询指令
        if any(kw in text_content for kw in ["查人", "查看人物", "图库列表"]):
            persons = image_manager.get_all_persons()
            if persons:
                lines = [f"📊 当前图库已有 {len(persons)} 位女声优："]
                for p in persons:
                    # 尝试获取别名预览
                    aliases = alias_manager.get_aliases_by_name(p['name'])
                    alias_str = f" ({'、'.join(aliases[:2])}...)" if aliases else ""
                    lines.append(f"• 【{p['name']}】: {p['count']} 张{alias_str}")
                message_api_client.send_text("chat_id", chat_id, "\n".join(lines))
            else:
                message_api_client.send_text("chat_id", chat_id, "📁 图库目前是空的哦。")
            return

    # 逻辑 7：对于群聊中未被 @ 的消息，直接返回（不回复）
    if chat_type == "group":
        return

    # 逻辑 8：私聊中的兜底回复（Echo）
    message_api_client.send_text("chat_id", chat_id, f"Echo: {text_content}")



@app.errorhandler
def msg_error_handler(ex):
    logging.error(ex)
    response = jsonify(message=str(ex))
    response.status_code = (
        ex.response.status_code if isinstance(ex, requests.HTTPError) else 500
    )
    return response


@app.route("/", methods=["POST"])
def callback_event_handler():
    # init callback instance and handle
    event_handler, event = event_manager.get_handler_with_event(VERIFICATION_TOKEN, ENCRYPT_KEY)

    return event_handler(event)


if __name__ == "__main__":
    # init()
    # 启动定时任务（如果是 debug 模式，需要防止 reloader 启动两次）
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        scheduler.start()
        logging.info("APScheduler 已启动")
    
    app.run(host="0.0.0.0", port=3000, debug=True)
