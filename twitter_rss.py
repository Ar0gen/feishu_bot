import feedparser
import logging
import re
import requests
import os
import json
from typing import List, Dict, Optional, Any
from urllib.parse import unquote
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from dotenv import load_dotenv, find_dotenv

from api import MessageApiClient

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TwitterRssFetcher:
    """
    Twitter RSS 抓取工具类
    支持多 Nitter 实例轮询、图片链接还原、内容清洗以及本地历史记录持久化。
    """

    # Nitter 实例列表（按优先级排序）
    NITTER_INSTANCES = [
        "https://nitter.kuuro.net",           # 主要使用的稳定实例
        "https://nitter.net",                 # 官方实例
        "https://nitter.poast.org",           # 备用实例1
        "https://nitter.privacyredirect.com", # 备用实例2
        "https://lightbrd.com",               # 备用实例3
        "https://nitter.space",               # 备用实例4
        "https://nitter.tiekoetter.com",      # 备用实例5
        "https://nuku.trabun.org"             # 备用实例6
    ]
    
    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    def __init__(self, twitter_username: str):
        """
        初始化抓取器
        :param twitter_username: 目标推特用户名（带或不带 @ 均可）
        """
        # 从环境变量获取数据根目录，默认为项目下的 data 目录
        self.data_root = os.getenv("DATA_ROOT", "data")
        self.history_dir = os.path.join(self.data_root, "twitter_history")
        
        self.twitter_username = twitter_username.strip().replace('@', '')
        self.history_file = os.path.join(self.history_dir, f"{self.twitter_username}.json")
        self.allowed_tweet_types = self._parse_allowed_tweet_types(os.getenv("TWEET_TYPES", "original"))
        # 确保目录存在
        os.makedirs(self.history_dir, exist_ok=True)

    def _parse_allowed_tweet_types(self, value: Optional[str]) -> set:
        if not value:
            return {"original"}
        normalized = {part.strip().lower() for part in re.split(r"[\s,;]+", value) if part.strip()}
        if "all" in normalized:
            return {"original", "retweet"}
        allowed = {t for t in normalized if t in {"original", "retweet"}}
        return allowed or {"original"}

    def _load_history(self) -> List[str]:
        """加载历史已发送推文 ID 列表"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"加载历史记录失败 [{self.twitter_username}]: {e}")
        return []

    def _save_history(self, history_ids: List[str]):
        """保存历史已发送推文 ID，限制最多 30 条"""
        try:
            # 只保留最近的 30 条
            history_ids = history_ids[-30:]
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history_ids, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"保存历史记录失败 [{self.twitter_username}]: {e}")

    def _convert_nitter_image_url(self, url: str) -> str:
        """
        将 Nitter 代理的图片链接转换回 Twitter 原始链接 (pbs.twimg.com)
        """
        if '/pic/' not in url:
            return url

        try:
            decoded_url = unquote(url)
            # 处理媒体图片 (media/...)
            if 'media/' in decoded_url:
                media_id = decoded_url.split('media/')[-1].split('?')[0]
                media_id = re.sub(r'\.(jpg|png|jpeg|gif)$', '', media_id)
                return f"https://pbs.twimg.com/media/{media_id}?format=jpg&name=large"
            
            # 处理个人资料图片
            if 'pbs.twimg.com/' in decoded_url:
                path = decoded_url.split('pbs.twimg.com/')[-1]
                return f"https://pbs.twimg.com/{path}"
        except Exception as e:
            logging.warning(f"转换图片链接失败: {url}, error: {e}")
        
        return url

    def _clean_html_tags(self, text: Optional[str]) -> str:
        """
        清洗 HTML 标签及无意义字符，优化排版
        """
        if not text:
            return ""
        
        # 1. 换行符处理
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<br\s+[^>]*>', '\n', text, flags=re.IGNORECASE)
        
        # 2. 剔除所有 HTML 标签
        text = re.sub(r'<[^>]*>', '', text)
        
        # 3. 实体字符解码
        entities = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"'}
        for entity, char in entities.items():
            text = text.replace(entity, char)
        
        # 4. 移除 CDATA 标记和推文链接
        text = re.sub(r'\]\]>$', '', text)
        link_pattern = r'(?:https?://)?(?:(?:x\.com|twitter\.com)|(?:[A-Za-z0-9-]+\.)*nitter[^\s/]*|(?:[A-Za-z0-9-]+\.)*kuuro\.net)/[A-Za-z0-9_]+/status/\d+[^\s]*'
        text = re.sub(link_pattern, '', text)
        
        # 5. 空白规范化
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()

    def _extract_images(self, entry: Any, description: str) -> List[str]:
        """
        从 RSS 条目中多渠道提取图片 URL
        """
        images = []
        
        def add_image(img_url: str):
            if not img_url: return
            converted = self._convert_nitter_image_url(img_url)
            if converted not in images:
                images.append(converted)

        # 1. 扫描 description 中的 <img> 标签
        img_tags = re.findall(r'<img [^>]*src="([^"]+)"', description)
        for url in img_tags:
            add_image(url)

        # 2. 扫描媒体扩展标签 (media:content, media:thumbnail, enclosures)
        for field in ['media_content', 'media_thumbnail']:
            for item in entry.get(field, []):
                add_image(item.get('url'))
        
        for enc in entry.get('enclosures', []):
            if enc.get('type', '').startswith('image/'):
                add_image(enc.get('href'))

        # 3. 兜底：从文本中提取 media ID
        if not images:
            match = re.search(r'media/([a-zA-Z0-9_]+)', description)
            if match:
                add_image(f"https://pbs.twimg.com/media/{match.group(1)}?format=jpg&name=large")

        return images

    def _parse_tweet_entry(self, entry: Any) -> Optional[Dict[str, Any]]:
        """
        将单条 RSS entry 解析为统一的推文字典格式
        """
        description = entry.get('description', '')
        
        # 提取 ID (第一级：链接/GUID，第二级：内容搜索)
        link_to_check = entry.get('link') or entry.get('id') or ""
        tweet_id = None
        id_match = re.search(r'status/(\d+)', link_to_check)
        if id_match:
            tweet_id = id_match.group(1)
        else:
            search_text = f"{entry.get('title', '')} {description}"
            id_match = re.search(r'status/(\d+)', search_text)
            if id_match:
                tweet_id = id_match.group(1)

        if not tweet_id:
            return None

        # 处理链接转换
        nitter_link = entry.get('link') or entry.get('id') or ""
        original_link = re.sub(r'https?://[^/]+', 'https://x.com', nitter_link).split('#')[0] if nitter_link else ""
        
        # 处理发布时间 (GMT -> 北京时间)
        published_str = entry.get('published', "")
        beijing_time = published_str
        if published_str:
            try:
                dt = parsedate_to_datetime(published_str)
                beijing_time = dt.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            except: pass

        # 视频检测：检查是否有直链、Video 文字或者视频缩略图模式
        has_video = bool(re.search(r'<source [^>]*src="([^"]+)"', description)) or \
                    "<br>Video<br>" in description or \
                    "video_thumb" in description

        return {
            "id": tweet_id,
            "title": entry.get('title', ""),
            "author": entry.get('author', entry.get('dc_creator', 'Unknown')),
            "link": nitter_link,
            "original_link": original_link,
            "published": beijing_time,
            "content": self._clean_html_tags(description),
            "images": self._extract_images(entry, description),
            "videos": re.findall(r'<source [^>]*src="([^"]+)"', description),
            "has_video": has_video,
            "is_retweet": entry.title.startswith("RT by") if 'title' in entry else False
        }

    def fetch_latest_tweets(self) -> List[Dict[str, Any]]:
        """
        轮询 Nitter 实例获取最新的推文列表
        """
        for instance in self.NITTER_INSTANCES:
            rss_url = f"{instance}/{self.twitter_username}/rss"
            try:
                logging.info(f"正在抓取 [{self.twitter_username}] 实例: {instance}")
                resp = requests.get(rss_url, headers=self.DEFAULT_HEADERS, timeout=10)
                resp.raise_for_status()
                
                feed = feedparser.parse(resp.text)
                if feed.bozo and not feed.entries:
                    continue
                
                tweets = []
                for entry in feed.entries:
                    tweet = self._parse_tweet_entry(entry)
                    if tweet:
                        tweets.append(tweet)
                
                if tweets:
                    return tweets
            except Exception as e:
                logging.error(f"实例请求失败 {instance}: {e}")
                continue
        
        return []

    def get_new_tweets(self) -> List[Dict[str, Any]]:
        """
        获取未发送过的新推文。
        如果本地无历史记录（首次运行），则初始化记录并返回空列表，避免冷启动爆破。
        """
        all_tweets = self.fetch_latest_tweets()
        if not all_tweets:
            return []

        # 检查是否为首次运行
        is_first_run = not os.path.exists(self.history_file)
        history_ids = self._load_history()
        new_tweets = []
        
        # RSS 为降序，反转为升序处理以保持历史记录顺序
        for tweet in reversed(all_tweets):
            if tweet['id'] in history_ids:
                continue
            history_ids.append(tweet['id'])
            tweet_type = "retweet" if tweet.get("is_retweet") else "original"
            if tweet_type not in self.allowed_tweet_types:
                continue
            new_tweets.append(tweet)
        
        if new_tweets:
            self._save_history(history_ids)
            
            if is_first_run:
                logging.info(f"用户 @{self.twitter_username} 首次运行，已初始化 {len(new_tweets)} 条历史记录，本次不推送。")
                return []
            
            logging.info(f"用户 @{self.twitter_username} 发现 {len(new_tweets)} 条新内容")
        
        return new_tweets

    def build_tweet_card(self, tweet: Dict[str, Any], image_keys: List[str], display_name: Optional[str] = None) -> Dict[str, Any]:
        """
        组装飞书 2.0 消息卡片
        :param tweet: 推文数据字典
        :param image_keys: 已上传到飞书的图片 key 列表
        :param display_name: 可选的显示名称（如：青木阳菜）
        """
        display_name = display_name or self.twitter_username
        verb = "转发了" if tweet['is_retweet'] else "更新了"
        
        # 标题增加视频标识
        video_prefix = "📹 " if tweet.get('has_video') else ""
        video_suffix = "视频" if tweet.get('has_video') else ""
        
        # 构建正文内容
        text = f"**作者：** {tweet['author']}\n**时间：** {tweet['published']}\n\n{tweet['content']}"
        
        elements = [{"tag": "markdown", "content": text, "element_id": "main_text"}]
        
        # 构建宫格图
        if image_keys:
            img_count = len(image_keys)
            if img_count == 1:
                elements.append({"tag": "img", "img_key": image_keys[0], "mode": "fit_horizontal", "element_id": "img_0"})
            elif img_count == 4:
                # 2x2 排列
                for i in [0, 2]:
                    elements.append(self._build_img_row(image_keys[i:i+2], i))
            else:
                # 3 列九宫格逻辑
                for i in range(0, img_count, 3):
                    row = image_keys[i:i+3]
                    if len(row) == 3:
                        elements.append(self._build_img_row(row, i, mode="trisect"))
                    elif len(row) == 2:
                        elements.append(self._build_img_row(row, i, mode="bisect", ratio="16:9"))
                    else:
                        elements.append({"tag": "img", "img_key": row[0], "mode": "fit_horizontal", "element_id": f"img_{i}"})

        elements.append({
            "tag": "markdown", 
            "content": f"---\n🔗 [查看推特原文]({tweet['original_link']})", 
            "element_id": "footer"
        })

        return {
            "schema": "2.0",
            "header": {
                "title": {
                    "content": f"{video_prefix}{display_name} {verb}推特", 
                    "tag": "plain_text"
                }, 
                "template": "blue"
            },
            "config": {"streaming_mode": False, "summary": {"content": f"{video_prefix}{display_name} 推特{video_suffix}"}},
            "body": {"elements": elements}
        }

    def _build_img_row(self, keys: List[str], start_idx: int, mode: str = "bisect", ratio: str = "1:1") -> Dict[str, Any]:
        """构建分栏图片行"""
        columns = []
        for j, key in enumerate(keys):
            columns.append({
                "tag": "column", "width": "weighted", "weight": 1,
                "elements": [{"tag": "img", "img_key": key, "mode": "crop_center", "aspect_ratio": ratio, "element_id": f"img_{start_idx+j}"}]
            })
        return {"tag": "column_set", "flex_mode": mode, "columns": columns}

if __name__ == "__main__":
    load_dotenv(find_dotenv())
    
    # --- 测试电子表格读取逻辑 ---
    print("\n=== 开始测试电子表格读取逻辑 ===")
    SHEET_TOKEN = os.getenv("SHEET_TOKEN")
    SHEET_RANGE = os.getenv("SHEET_RANGE")
    APP_ID = os.getenv("APP_ID")
    APP_SECRET = os.getenv("APP_SECRET")
    LARK_HOST = os.getenv("LARK_HOST")

    if not SHEET_TOKEN or "XXXXXX" in SHEET_TOKEN:
        print("警告: 未在 .env 中配置有效的 SHEET_TOKEN")
        test_users = []
    else:
        try:
            client = MessageApiClient(APP_ID, APP_SECRET, LARK_HOST)
            print(f"正在读取表格: {SHEET_TOKEN}, 范围: {SHEET_RANGE}")
            rows = client.get_spreadsheet_values(SHEET_TOKEN, SHEET_RANGE)
            
            # 1. 严格过滤：只有当姓名和 ID 都不为空时才保留
            valid_rows = [
                r for r in rows 
                if len(r) >= 2 and r[0] and str(r[0]).strip() and r[1] and str(r[1]).strip()
            ]
            
            test_users = []
            seen_ids = set()
            for row in valid_rows:
                tid = str(row[1]).strip().replace('@', '')
                name = str(row[0]).strip()
                
                # 过滤表头和重复
                if tid.lower() in ["twitter_id", "twitter"] or tid in seen_ids:
                    continue
                
                test_users.append({"name": name, "twitter_id": tid})
                seen_ids.add(tid)
            
            print(f"成功获取到 {len(test_users)} 个监控对象:")
            for u in test_users:
                print(f"  - 姓名: {u['name']}, 推特ID: {u['twitter_id']}")
        except Exception as e:
            print(f"读取表格测试失败: {e}")
            test_users = []
    print("=== 电子表格测试结束 ===\n")

    # --- 2. 联动测试：使用表格中的第一个用户进行推文抓取测试 ---
    if test_users:
        target_user = test_users[0]
        print(f"=== 开始联动测试：使用用户 @{target_user['twitter_id']} ({target_user['name']}) ===")
        
        fetcher = TwitterRssFetcher(target_user['twitter_id'])
        client = MessageApiClient(APP_ID, APP_SECRET, LARK_HOST)
        
        # 强制获取最新推文进行卡片生成测试
        tweets = fetcher.fetch_latest_tweets()
        if tweets:
            tweet = tweets[0]
            print(f"成功抓取到推文: {tweet['id']}")
            
            # 模拟上传图片（仅第一张）
            keys = []
            if tweet['images']:
                img_url = tweet['images'][0]
                try:
                    print(f"尝试上传测试图片: {img_url}")
                    # key = client.upload_image(img_url) # 实际上传可取消注释
                    # keys.append(key)
                    print("图片上传逻辑已跳过（仅做打印测试）")
                except Exception as e:
                    print(f"图片上传测试失败: {e}")
            
            # 生成卡片
            card = fetcher.build_tweet_card(tweet, keys, display_name=target_user['name'])
            print("\n生成的卡片预览:")
            print(f"  [标题]: {card['header']['title']['content']}")
            print(f"  [摘要]: {card['config']['summary']['content']}")
            print(f"  [正文前50字]: {tweet['content'][:50]}...")
            
            client.send_card("chat_id", "oc_3a213fac055fc4a98bdd00b6fa97d414", card) # 实际发送可取消注释
        else:
            print(f"未能获取到 @{target_user['twitter_id']} 的推文，请检查 Nitter 实例是否可用。")
        print(f"=== 联动测试结束 ===\n")
    else:
        print("没有可用的测试用户，跳过联动测试。")
