"""GET /jobs Lambda handler.

Returns public OPEN job postings from admin-jobs table.
"""

import base64
import json

from boto3.dynamodb.conditions import Attr, Key
from common.db import get_table_by_env
from common.response import create_response

table = get_table_by_env("ADMIN_JOBS_TABLE_NAME")

DEFAULT_LIMIT = 10
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


def _to_home_item(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("jobTitle"),
        "company": item.get("company"),
        "location": item.get("location"),
        "type": item.get("employmentType"),
        "budget": item.get("budget"),
        "description": item.get("description"),
        "createdAt": item.get("createdAt"),
    }


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    try:
        limit = max(1, min(int(params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT))
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT

    q = (params.get("q") or "").strip()

    next_token = params.get("nextToken") or None
    start_key = _decode_next_token(next_token)
    if next_token and start_key is None:
        return create_response(400, {"message": "Invalid nextToken"})

    try:
        items = []
        last_key = start_key

        query_kwargs = {
            "IndexName": "status-createdAt-index",
            "KeyConditionExpression": Key("status").eq("OPEN"),
            "ScanIndexForward": False,
            
        }

        if q:
            query_kwargs["FilterExpression"] = (
                Attr("jobTitle").contains(q)
                | Attr("company").contains(q)
                | Attr("employmentType").contains(q)
            )

        while len(items) < limit:
            query_kwargs["Limit"] = limit - len(items)

            if last_key:
                query_kwargs["ExclusiveStartKey"] = last_key
            else:
                query_kwargs.pop("ExclusiveStartKey", None)

            response = table.query(**query_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")

            if not last_key:
                break

    except Exception as exc:
        print(f"ERROR list_jobs: {exc}")
        return create_response(500, {"message": "Failed to fetch jobs"})

    out_token = _encode_next_token(last_key)

    body: dict = {"items": [_to_home_item(item) for item in items]}
    if out_token:
        body["nextToken"] = out_token

    return create_response(200, body)
