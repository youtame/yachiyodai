import os
import json
import boto3
import requests

TABLE_NAME = os.environ.get('TABLE_NAME', 'FileMakerStaffState')
FM_DB = os.environ.get('FM_DB', '教職員情報db')
TARGET_STAFF_IDS_STR = os.environ.get('TARGET_STAFF_IDS', '')

FM_SERVER = os.environ.get('FM_SERVER')
NTFY_TOPIC = os.environ.get('NTFY_TOPIC')

FM_USER = os.environ.get('FM_USER')
FM_PASSWORD = os.environ.get('FM_PASSWORD')

if not all([FM_SERVER, FM_USER, FM_PASSWORD, NTFY_TOPIC]):
    print("WARNING: Some required environment variables are missing.")

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)

def get_target_staff_ids():
    if not TARGET_STAFF_IDS_STR:
        return []
    return [staff_id.strip() for staff_id in TARGET_STAFF_IDS_STR.split(',') if staff_id.strip()]

def get_fm_session():
    url = f"https://{FM_SERVER}/fmi/data/v1/databases/{FM_DB}/sessions"
    try:
        response = requests.post(url, auth=(FM_USER, FM_PASSWORD), headers={"Content-Type": "application/json"}, verify=True)
        if response.status_code == 200:
            return response.headers.get('X-FM-Data-Access-Token')
    except Exception as e:
        print(f"FileMaker Login Error: {e}")
    return None

def logout_fm_session(token):
    if not token:
        return
    url = f"https://{FM_SERVER}/fmi/data/v1/databases/{FM_DB}/sessions/{token}"
    try:
        requests.delete(url, verify=True)
    except Exception as e:
        print(f"FileMaker Logout Error: {e}")

def fetch_staff_data(token, target_ids):
    url = f"https://{FM_SERVER}/fmi/data/v1/databases/{FM_DB}/layouts/web用_教職員情報/_find"
    headers = {
        "Content-Type": "application/json",
        "X-FM-Data-Access-Token": token
    }
    
    if target_ids:
        query_list = [{"教職員番号": staff_id} for staff_id in target_ids]
    else:
        query_list = [{}]

    body = {
        "query": query_list,
        "limit": str(max(len(query_list), 1))
    }
    
    try:
        response = requests.post(url, headers=headers, json=body, verify=True)
        if response.status_code == 404:
            return []
        if response.status_code == 200:
            return response.json().get("response", {}).get("data", [])
    except Exception as e:
        print(f"FileMaker Fetch Error: {e}")
    return []

def translate_status(sts_code):
    if sts_code == "01":
        return "出勤"
    elif sts_code == "02":
        return "退勤"
    elif sts_code is None or sts_code == "":
        return "研修・休み・その他"
    else:
        return f"不明な状態({sts_code})"

def send_ntfy_notification(name, status_text):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    message = f"【出退勤通知】\n{name} さんが「{status_text}」になりました。"
    try:
        requests.post(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title": "教職員 出退勤ステータス変更",
                "Priority": "default",
                "Tags": "bell"
            }
        )
    except Exception as e:
        print(f"ntfy Notification Error: {e}")

def lambda_handler(event, context):
    print("EventBridge trigger received. Starting targeted polling process...")
    
    target_ids = get_target_staff_ids()
    print(f"Target Staff IDs: {target_ids}")

    token = get_fm_session()
    if not token:
        print("Failed to authenticate with FileMaker Data API.")
        return {"statusCode": 500, "body": "FM Authentication Failed"}

    try:
        records = fetch_staff_data(token, target_ids)
        
        for record in records:
            field_data = record.get("fieldData", {})
            portal_data = record.get("portalData", {})
            
            staff_id = str(field_data.get("教職員番号", ""))
            staff_name = field_data.get("氏名", "名前未設定")
            
            if not staff_id:
                continue

            timedata_list = portal_data.get("tog001_教職員情報db_timedata", [])
            current_sts_code = None
            if timedata_list and len(timedata_list) > 0:
                first_timedata = timedata_list[0]
                if isinstance(first_timedata, dict):
                    current_sts_code = first_timedata.get("tog001_教職員情報db_timedata::sts")

            current_status_str = translate_status(current_sts_code)

            response = table.get_item(Key={"staff_id": staff_id})
            previous_status_str = response.get("Item", {}).get("status", None)

            if previous_status_str != current_status_str:
                print(f"Status changed for {staff_name} ({staff_id}): {previous_status_str} -> {current_status_str}")
                
                if previous_status_str is not None:
                    send_ntfy_notification(staff_name, current_status_str)
                
                table.put_item(
                    Item={
                        "staff_id": staff_id,
                        "name": staff_name,
                        "status": current_status_str,
                        "updated_at": context.aws_request_id
                    }
                )

    finally:
        logout_fm_session(token)

    return {
        "statusCode": 200,
        "body": json.dumps("Targeted polling executed successfully.")
    }