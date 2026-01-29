#! /usr/bin/env python3.8
import os
import logging
import requests
import json
from utils import convert_to_opus

APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")

# const
TENANT_ACCESS_TOKEN_URI = "/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URI = "/open-apis/im/v1/messages"
MESSAGE_RESOURCE_URI = "/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
IMAGE_URI = "/open-apis/im/v1/images"
FILE_URI = "/open-apis/im/v1/files"
CHATS_URI = "/open-apis/im/v1/chats"
CARD_CREATE_URI = "/open-apis/cardkit/v1/cards"
BITABLE_RECORD_URI = "/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
SHEET_VALUES_URI = "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range}"


class MessageApiClient(object):
    def __init__(self, app_id, app_secret, lark_host):
        self._app_id = app_id
        self._app_secret = app_secret
        self._lark_host = lark_host
        self._tenant_access_token = ""

    @property
    def tenant_access_token(self):
        return self._tenant_access_token

    def send_text_with_open_id(self, open_id, content):
        # 兼容旧逻辑，如果 content 已经是 JSON 字符串（比如从事件原样转发的）
        if content.startswith('{') and '"text"' in content:
            self.send("open_id", open_id, "text", content)
        else:
            self.send_text("open_id", open_id, content)

    def send_text(self, receive_id_type, receive_id, text):
        content = json.dumps({"text": text})
        self.send(receive_id_type, receive_id, "text", content)

    def send_image(self, receive_id_type, receive_id, image_key):
        content = json.dumps({"image_key": image_key})
        self.send(receive_id_type, receive_id, "image", content)

    def send_audio(self, receive_id_type, receive_id, file_key):
        content = json.dumps({"file_key": file_key})
        self.send(receive_id_type, receive_id, "audio", content)

    def send_post(self, receive_id_type, receive_id, post_content):
        """
        发送富文本消息。
        post_content 结构参考: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/im-v1/message/create_json#7111df05
        """
        content = json.dumps(post_content)
        self.send(receive_id_type, receive_id, "post", content)

    def send_card(self, receive_id_type, receive_id, card_dict):
        # 1. 创建卡片实体获取 card_id
        card_id = self.create_card(card_dict)
        
        # 2. 发送卡片消息
        content = json.dumps({
            "type": "card",
            "data": {
                "card_id": card_id
            }
        })
        self.send(receive_id_type, receive_id, "interactive", content)

    def create_card(self, card_dict):
        # 调用 /open-apis/cardkit/v1/cards 创建卡片实体
        logging.info("正在创建卡片实体...")
        self._authorize_tenant_access_token()
        url = "{}{}".format(self._lark_host, CARD_CREATE_URI)
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.tenant_access_token,
        }
        # 按照要求，将 card_dict 序列化为 JSON 字符串放入 data 字段
        req_body = {
            "type": "card_json",
            "data": json.dumps(card_dict)
        }
        resp = requests.post(url=url, headers=headers, json=req_body)
        MessageApiClient._check_error_response(resp)
        card_id = resp.json().get("data", {}).get("card_id")
        if not card_id:
            raise LarkException(msg="创建卡片成功但未获取到 card_id")
        logging.info(f"卡片实体创建成功，card_id: {card_id}")
        return card_id

    def upload_image(self, image_source):
        """
        上传图片。image_source 可以是 URL 或本地文件路径。
        """
        if image_source.startswith('http'):
            # 1. 从 URL 下载图片内容
            logging.info(f"正在从 URL 下载图片: {image_source}")
            resp = requests.get(image_source)
            resp.raise_for_status()
            image_content = resp.content
        else:
            # 2. 从本地路径读取图片内容
            logging.info(f"正在从本地读取图片: {image_source}")
            with open(image_source, 'rb') as f:
                image_content = f.read()

        # 3. 调用飞书上传接口
        logging.info("正在上传图片到飞书...")
        self._authorize_tenant_access_token()
        url = "{}{}".format(self._lark_host, IMAGE_URI)
        headers = {
            "Authorization": "Bearer " + self.tenant_access_token,
        }
        # 飞书接口要求 form-data 格式，包含 image_type 和 image
        files = {
            "image_type": (None, "message"),
            "image": ("daily_image.jpg", image_content, "image/jpeg"),
        }
        resp = requests.post(url=url, headers=headers, files=files)
        MessageApiClient._check_error_response(resp)
        image_key = resp.json().get("data", {}).get("image_key")
        if not image_key:
            raise LarkException(msg="图片上传成功但未获取到 image_key")
        logging.info(f"图片上传成功，image_key: {image_key}")
        return image_key

    def upload_file(self, file_source, file_type, file_name, duration=None):
        """
        上传文件。file_source 可以是 URL 或本地文件路径。
        针对语音消息，file_type 必须为 "opus"。
        """
        temp_file_to_clean = None
        
        # 1. 针对 opus 类型进行格式转换逻辑
        if file_type == "opus" and not file_source.startswith('http'):
            # 如果是本地文件且要求 opus 格式，尝试转换
            if not file_source.endswith('.opus'):
                logging.info(f"检测到非 opus 格式音频，尝试转换: {file_source}")
                converted_path = convert_to_opus(file_source)
                if converted_path:
                    file_source = converted_path
                    temp_file_to_clean = converted_path
                    file_name = os.path.basename(converted_path)

        # 2. 获取文件内容
        if file_source.startswith('http'):
            logging.info(f"正在从 URL 下载文件 ({file_type}): {file_source}")
            resp = requests.get(file_source)
            resp.raise_for_status()
            file_content = resp.content
        else:
            logging.info(f"正在从本地读取文件: {file_source}")
            with open(file_source, 'rb') as f:
                file_content = f.read()

        # 3. 调用飞书文件上传接口
        logging.info(f"正在上传文件到飞书, 类型: {file_type}...")
        self._authorize_tenant_access_token()
        url = "{}{}".format(self._lark_host, FILE_URI)
        headers = {
            "Authorization": "Bearer " + self.tenant_access_token,
        }
        
        # 飞书接口要求 form-data 格式
        # 注意: file 字段需要包含文件名和 MIME 类型
        mime_type = 'application/octet-stream'
        if file_type == 'opus':
            mime_type = 'audio/opus'
            
        files = {
            "file_type": (None, file_type),
            "file_name": (None, file_name),
            "file": (file_name, file_content, mime_type),
        }
        
        # 如果提供了时长，加入表单数据
        if duration:
            files["duration"] = (None, str(duration))
        
        try:
            resp = requests.post(url=url, headers=headers, files=files)
            MessageApiClient._check_error_response(resp)
            file_key = resp.json().get("data", {}).get("file_key")
            if not file_key:
                raise LarkException(msg=f"文件上传成功但未获取到 file_key")
            logging.info(f"文件上传成功，file_key: {file_key}")
            return file_key
        finally:
            # 清理转换生成的临时文件
            if temp_file_to_clean and os.path.exists(temp_file_to_clean):
                try:
                    os.remove(temp_file_to_clean)
                    logging.info(f"清理临时文件: {temp_file_to_clean}")
                except: pass

    def get_spreadsheet_values(self, spreadsheet_token, range_name):
        logging.info(f"正在读取电子表格数据: spreadsheet_token={spreadsheet_token}, range={range_name}")
        self._authorize_tenant_access_token()
        url = "{}{}".format(self._lark_host, SHEET_VALUES_URI.format(spreadsheet_token=spreadsheet_token, range=range_name))
        headers = {
            "Authorization": "Bearer " + self.tenant_access_token,
        }
        resp = requests.get(url=url, headers=headers)
        MessageApiClient._check_error_response(resp)
        value_range = resp.json().get("data", {}).get("valueRange", {})
        values = value_range.get("values", [])
        logging.info(f"成功读取 {len(values)} 行电子表格数据")
        return values

    def get_bot_chats(self):
        """
        获取机器人所在的群聊列表（支持自动分页获取全部）
        """
        logging.info("正在获取机器人所在的群聊列表...")
        self._authorize_tenant_access_token()
        url = "{}{}".format(self._lark_host, CHATS_URI)
        headers = {
            "Authorization": "Bearer " + self.tenant_access_token,
        }
        
        all_items = []
        page_token = ""
        has_more = True
        
        while has_more:
            params = {
                "page_size": 100 # 尽量一次获取更多
            }
            if page_token:
                params["page_token"] = page_token
            
            resp = requests.get(url=url, headers=headers, params=params)
            MessageApiClient._check_error_response(resp)
            
            data = resp.json().get("data", {})
            items = data.get("items", [])
            all_items.extend(items)
            
            has_more = data.get("has_more", False)
            page_token = data.get("page_token", "")
            
            if not has_more:
                break
                
        logging.info(f"成功获取到全部 {len(all_items)} 个群聊")
        return all_items

    def get_message_resource(self, message_id, file_key, resource_type):
        """
        获取消息中的资源文件（图片或文件）。
        resource_type: "image" 或 "file"
        """
        logging.info(f"正在下载消息资源: message_id={message_id}, file_key={file_key}, type={resource_type}")
        self._authorize_tenant_access_token()
        url = "{}{}".format(self._lark_host, MESSAGE_RESOURCE_URI.format(message_id=message_id, file_key=file_key))
        headers = {
            "Authorization": "Bearer " + self.tenant_access_token,
        }
        params = {
            "type": resource_type
        }
        resp = requests.get(url=url, headers=headers, params=params)
        # 资源下载接口返回的是二进制流，不能直接调用 _check_error_response
        if resp.status_code != 200:
            logging.error(f"下载资源失败: {resp.text}")
            resp.raise_for_status()
        return resp.content

    def send(self, receive_id_type, receive_id, msg_type, content):
        # send message to user, implemented based on Feishu open api capability. doc link: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create
        self._authorize_tenant_access_token()
        url = "{}{}?receive_id_type={}".format(
            self._lark_host, MESSAGE_URI, receive_id_type
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.tenant_access_token,
        }

        req_body = {
            "receive_id": receive_id,
            "content": content,
            "msg_type": msg_type,
        }
        resp = requests.post(url=url, headers=headers, json=req_body)
        MessageApiClient._check_error_response(resp)

    def _authorize_tenant_access_token(self):
        # get tenant_access_token and set, implemented based on Feishu open api capability. doc link: https://open.feishu.cn/document/ukTMukTMukTM/ukDNz4SO0MjL5QzM/auth-v3/auth/tenant_access_token_internal
        url = "{}{}".format(self._lark_host, TENANT_ACCESS_TOKEN_URI)
        req_body = {"app_id": self._app_id, "app_secret": self._app_secret}
        response = requests.post(url, req_body)
        MessageApiClient._check_error_response(response)
        self._tenant_access_token = response.json().get("tenant_access_token")

    @staticmethod
    def _check_error_response(resp):
        # check if the response contains error information
        if resp.status_code != 200:
            print(f"Error response from Feishu: {resp.text}")
            resp.raise_for_status()
        response_dict = resp.json()
        code = response_dict.get("code", -1)
        if code != 0:
            logging.error(response_dict)
            raise LarkException(code=code, msg=response_dict.get("msg"))


class LarkException(Exception):
    def __init__(self, code=0, msg=None):
        self.code = code
        self.msg = msg

    def __str__(self) -> str:
        return "{}:{}".format(self.code, self.msg)

    __repr__ = __str__
