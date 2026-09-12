"""GET /admin/jobs Lambda handler.

Target frontend page:
- frontend/src/pages/AdminJobList.jsx
"""

import base64
import json

from boto3.dynamodb.conditions import Key
from common.auth import get_claims, get_user_sub, is_admin_event
from common.db import get_table_by_env
from common.response import create_response

table = get_table_by_env("ADMIN_JOBS_TABLE_NAME")

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _encode_next_token(last_key: dict | None) -> str | None:
    if not last_key:
        return None
    return base64.urlsafe_b64encode(json.dumps(last_key).encode("utf-8")).decode("utf-8")


def _decode_next_token(next_token: str | None) -> dict | None:
    if not next_token:
        return None
    try:
        raw = base64.urlsafe_b64decode(next_token.encode("utf-8")).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


# ---------- クエリ ----------

def _list_jobs(owner_user_id: str, limit: int, next_token: str | None, status: str | None) -> tuple[list, str | None]:
    """ownerUserId-createdAt-index GSI で自分の案件を降順取得する。

    GSI 設定例:
      - IndexName: ownerUserId-createdAt-index
      - Partition key: ownerUserId (S)
      - Sort key: createdAt (S)
    """
    key_condition = Key("ownerUserId").eq(owner_user_id)

    kwargs: dict = {
        "IndexName": "ownerUserId-createdAt-index",
        "KeyConditionExpression": key_condition,
        "ScanIndexForward": False,
        "Limit": limit,
    }

    # status フィルター
    if status:
        from boto3.dynamodb.conditions import Attr
        kwargs["FilterExpression"] = Attr("status").eq(status)

    start_key = _decode_next_token(next_token)
    if next_token and start_key is None:
        raise ValueError("Invalid nextToken")
    if start_key:
        kwargs["ExclusiveStartKey"] = start_key

    try:
        resp = table.query(**kwargs)
except Exception as exc:
    print(f"ERROR list_my_jobs query: {exc}")
    raise
    
    items = resp.get("Items", [])
    last_key = resp.get("LastEvaluatedKey")
    out_token = _encode_next_token(last_key)
    return items, out_token


def _format_item(item: dict) -> dict:
    """フロントエンド向けに型を整形する。"""
    return {
        **item,
        "budget": float(item["budget"]) if item.get("budget") is not None else None,
    }


# ---------- ハンドラー ----------

def handler(event, context):
    # 1) 認証情報取得
    claims = get_claims(event)

    # 2) Admin 権限確認
    if not is_admin_event(event):
        return create_response(403, {"message": "Forbidden: admin privileges required."})

    # 3) ユーザーID取得
    owner_user_id = get_user_sub(claims, local_fallback="local-admin") or "unknown"

    params = event.get("queryStringParameters") or {}

    # 4) ページネーション・フィルターパラメータ
    try:
        limit = max(1, min(int(params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT))
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT

    next_token = params.get("nextToken") or None
    status = params.get("status") or None

    # 5) DynamoDB クエリ（createdAt 降順）
    try:
        items, out_token = _list_jobs(owner_user_id, limit, next_token, status)
    except Exception as exc:
        print(f"ERROR list_my_jobs: {exc}")
        return create_response(500, {"message": "An unexpected error occurred."})

    # 7) 型整形
    formatted = [_format_item(item) for item in items]

    # 6) レスポンス
    body: dict = {"items": formatted}
    if out_token:
        body["nextToken"] = out_token

    return create_response(200, body)
