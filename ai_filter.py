import os
import json
import logging
from typing import Dict, Any, Optional, List

from openai import OpenAI


class AITweetFilter:
    def __init__(self):
        self.enabled = os.getenv("AI_FILTER_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        self.strict = os.getenv("AI_FILTER_STRICT", "false").lower() in {"1", "true", "yes", "on"}
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.timeout = float(os.getenv("AI_FILTER_TIMEOUT", "10"))
        self.image_limit = self._parse_image_limit(os.getenv("AI_FILTER_IMAGE_LIMIT", ""))
        self.prompt = os.getenv(
            "AI_FILTER_PROMPT",
            "仅包含女声优的生活分享，过滤掉商务合作等内容。只回答 YES 或 NO，YES 表示符合要求。",
        )
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def is_allowed(self, tweet: Dict[str, Any]) -> bool:
        if not self.enabled:
            return True
        if not self.api_key:
            logging.warning("AI 过滤已开启但未配置 OPENAI_API_KEY")
            return False if self.strict else True
        try:
            result = self._classify(tweet)
            if result is None:
                return False if self.strict else True
            return result
        except Exception as e:
            logging.error(f"AI 过滤异常: {e}")
            return False if self.strict else True

    def _classify(self, tweet: Dict[str, Any]) -> Optional[bool]:
        content = tweet.get("content") or tweet.get("title") or ""
        author = tweet.get("author") or ""
        link = tweet.get("original_link") or tweet.get("link") or ""
        system_prompt = "You are a strict binary classifier."
        user_prompt = f"标准: {self.prompt}\n\n作者: {author}\n内容: {content}\n链接: {link}"

        content_items = [{"type": "input_text", "text": user_prompt}]
        if self.image_limit > 0:
            images = tweet.get("images") or []
            for image_url in images[: self.image_limit]:
                content_items.append({"type": "input_image", "image_url": image_url})

        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": content_items},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "tweet_filter",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "allow": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["allow", "reason"],
                        "additionalProperties": False,
                    },
                }
            },
            timeout=self.timeout,
        )

        raw_text = self._extract_response_text(response)
        if not raw_text:
            return None
        try:
            data = json.loads(raw_text)
        except Exception:
            return None
        return bool(data.get("allow"))

    def _extract_response_text(self, response: Any) -> Optional[str]:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        if hasattr(response, "text") and getattr(response, "text", None):
            text_obj = response.text
            if isinstance(text_obj, dict) and text_obj.get("value"):
                return text_obj.get("value")
            if not isinstance(text_obj, dict) and hasattr(text_obj, "value"):
                return text_obj.value
        output = getattr(response, "output", None) or []
        for item in output:
            contents = getattr(item, "content", None) or []
            for part in contents:
                part_type = getattr(part, "type", None)
                if part_type in {"output_text", "text"}:
                    return getattr(part, "text", None) or getattr(part, "value", None)
        return None

    def _parse_image_limit(self, value: Optional[str]) -> int:
        if not value:
            return 0
        try:
            limit = int(str(value).strip())
        except Exception:
            return 0
        return limit if limit > 0 else 0
