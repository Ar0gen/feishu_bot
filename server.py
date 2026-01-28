#!/usr/bin/env python3.8

import os
import logging
import requests
from collections import deque
from api import MessageApiClient
from event import MessageReceiveEvent, UrlVerificationEvent, EventManager
from flask import Flask, jsonify
from dotenv import load_dotenv, find_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from twitter_rss import TwitterRssFetcher
from voice_manager import VoiceManager
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
                    
                    # 3. 发送卡片到指定群聊
                    message_api_client.send_card("chat_id", TARGET_CHAT_ID, card_json)
                    logging.info(f"推文 {tweet['id']} 推送成功。")
            else:
                logging.info(f"用户 @{username} 无新推文")
        except Exception as e:
            logging.error(f"检查用户 @{username} 更新时出错: {e}")
    logging.info("推特更新检查完成。")


# 初始化定时任务
scheduler = BackgroundScheduler()
scheduler.add_job(check_twitter_updates, "interval", minutes=1)


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
    sender_id = req_data.event.sender.sender_id
    message = req_data.event.message
    
    if message.message_type != "text":
        logging.warn("Other types of messages have not been processed yet")
        return jsonify()

    chat_id = message.chat_id
    chat_type = message.chat_type
    # 解析消息内容，获取纯文本
    try:
        content_dict = json.loads(message.content)
        text_content = content_dict.get("text", "")
    except Exception as e:
        logging.error(f"解析消息内容失败: {e}")
        text_content = message.content

    # 逻辑 1：判断是否需要回复语音（包含关键词，且在私聊中或群聊中被 @）
    VOICE_KEYWORDS = ["随机语音", "抽一个", "试音", "来一句", "试听"]
    has_voice_keyword = any(kw in text_content for kw in VOICE_KEYWORDS)
    
    should_reply_voice = False
    
    if has_voice_keyword:
        if chat_type == "p2p":
            # 私聊中直接说关键词即可
            should_reply_voice = True
        elif chat_type == "group":
            # 群聊中需要关键词 + @ 机器人
            if message.mentions:
                should_reply_voice = True
            
    if should_reply_voice:
        logging.info(f"触发语音回复逻辑。类型: {chat_type}, ChatID: {chat_id}, 内容: {text_content}")
        voice_info = voice_manager.get_random_voice()
        if voice_info:
            try:
                # 1. 准备富文本内容
                post_content = {
                    "zh_cn": {
                        "title": "语音试听",
                        "content": [
                            [
                                {
                                    "tag": "text",
                                    "text": f"为您随机抽取了 【{voice_info['cv_name']}】 的语音试听："
                                }
                            ]
                        ]
                    }
                }

                # 2. 如果有头像，上传并加入富文本
                if voice_info.get('avatar_path'):
                    try:
                        img_key = message_api_client.upload_image(voice_info['avatar_path'])
                        if img_key:
                            post_content["zh_cn"]["content"].append([
                                {
                                    "tag": "img",
                                    "image_key": img_key
                                }
                            ])
                    except Exception as img_err:
                        logging.error(f"头像上传失败: {img_err}")

                # 3. 发送富文本提示
                message_api_client.send_post("chat_id", chat_id, post_content)
                
                # 4. 上传并发送语音
                duration = get_audio_duration(voice_info['path'])
                file_key = message_api_client.upload_file(voice_info['path'], "opus", voice_info['file_name'], duration)
                message_api_client.send_audio("chat_id", chat_id, file_key)
            except Exception as e:
                logging.error(f"随机语音回复失败: {e}")
                message_api_client.send_text("chat_id", chat_id, "抱歉，语音抽取失败了...")
        else:
            message_api_client.send_text("chat_id", chat_id, "语音库目前是空的哦。")
        return jsonify()

    # 逻辑 2：对于非 @ 的群聊消息，通常不作处理（避免刷屏）
    # 如果是群聊且没被 @，直接返回
    if chat_type == "group":
        return jsonify()

    # 逻辑 3：默认兜底（通常私聊非文本消息会走到这里，但前面已经过滤了非 text）
    message_api_client.send_text("chat_id", chat_id, f"Echo: {text_content}")
    return jsonify()


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
