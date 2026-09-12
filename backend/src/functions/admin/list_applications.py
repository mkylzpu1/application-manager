"""GET /admin/applications 用 Lambda ハンドラー。

対象フロントページ:
- frontend/src/pages/Admin.jsx
"""

import base64
import json

from boto3.dynamodb.conditions import Key
from common.auth import get_claims, get_user_sub, is_admin_event
from common.db import get_table_by_env
from common.response import create_response

table = get_table_by_env("APPLICATIONS_TABLE_NAME")

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


def _get_single(application_id: str, owner_user_id: str) -> dict:
    """id を指定して単一アイテムを取得し、所有者が自分のものだけ返す。"""
    resp = table.get_item(Key={"id": application_id})
    item = resp.get("Item")
    if item is None:
        return None
    if item.get("ownerUserId") != owner_user_id:
        return None
    return item


def _list_items(
    owner_user_id: str,
    limit: int,
    next_token: str | None,
    status: str | None,
) -> tuple[list, str | None]:
    """ownerUserId が自分のものだけ一覧取得する。

    ownerUserId-createdAt-index を使って降順取得する。
    status 指定時は取得後にフィルタする。

    nextToken は LastEvaluatedKey を base64(JSON) 化した値。
    """
    start_key = _decode_next_token(next_token)
    if next_token and start_key is None:
        raise ValueError("Invalid nextToken")

    query_kwargs: dict = {
        "IndexName": "ownerUserId-createdAt-index",
        "KeyConditionExpression": Key("ownerUserId").eq(owner_user_id),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if start_key:
        query_kwargs["ExclusiveStartKey"] = start_key

try:
    resp = table.query(**query_kwargs)
except Exception as exc:
    print(f"ERROR list_applications query: {exc}")
    raise
    
    items = resp.get("Items", [])
    items = [item for item in items if item.get("ownerUserId") == owner_user_id]
    if status:
        items = [item for item in items if item.get("status") == status]

    last_key = resp.get("LastEvaluatedKey")
    out_token = _encode_next_token(last_key)
    return items, out_token


def handler(event, context):
    # 1) 管理者権限チェック
    if not is_admin_event(event):
        return create_response(
            403,
            {"message": "Forbidden: 管理者権限が必要です。"},
        )

    claims = get_claims(event)
    owner_user_id = get_user_sub(claims, local_fallback="local-admin")
    if not owner_user_id:
        return create_response(401, {"message": "認証情報からユーザーIDを取得できませんでした。"})

    params = event.get("queryStringParameters") or {}

    # 2) id の有無で単一取得 / 一覧取得を分岐
    application_id = params.get("id")

    # --- 単一取得 ---
    if application_id:
        item = _get_single(application_id, owner_user_id)
        if item is None:
            return create_response(
                404,
                {"message": f"Application '{application_id}' が見つかりません。"},
            )
        return create_response(200, {"item": item})

    # --- 一覧取得 ---
    try:
        raw_limit = int(params.get("limit", DEFAULT_LIMIT))
        limit = max(1, min(raw_limit, MAX_LIMIT))
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT

    next_token = params.get("nextToken") or None
    status = params.get("status") or None

    try:
        items, out_token = _list_items(owner_user_id, limit, next_token, status)
    except Exception as exc:
        print(f"ERROR list_applications: {exc}")
        return create_response(
            500,
            {"message": "内部エラーが発生しました。しばらくしてから再試行してください。"},
        )

    body: dict = {"items": items}
    if out_token:
        body["nextToken"] = out_token

    return create_response(200, body)
