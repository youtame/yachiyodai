import os
import json
import boto3
import requests
from urllib.parse import quote
from datetime import datetime, timezone, timedelta

# Environment variables configuration
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

def get_japan_date_str():
    """Get today's date string in Japan Standard Time (JST)."""
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime('%Y-%m-%d')

def get_target_staff_ids():
    """Parse and return target staff IDs from environment variable string."""
    if not TARGET_STAFF_IDS_STR:
        return []
    return [staff_id.strip() for staff_id in TARGET_STAFF_IDS_STR.split(',') if staff_id.strip()]

def get_fm_session():
    """Authenticate with FileMaker Data API and return the access token."""
    encoded_db = quote(FM_DB)
    url = f"https://{FM_SERVER}/fmi/data/v1/databases/{encoded_db}/sessions"
    try:
        response = requests.post(
            url, 
            auth=(FM_USER, FM_PASSWORD), 
            headers={"Content-Type": "application/json"}, 
            json={}, 
            verify=True,
            timeout=10
        )
        if response.status_code == 200:
            return response.headers.get('X-FM-Data-Access-Token')
        else:
            print(f"FileMaker Login Failed: {response.status_code} {response.text}")
    except Exception as e:
        print(f"FileMaker Login Error: {e}")
    return None

def logout_fm_session(token):
    """Terminate the FileMaker Data API session."""
    if not token:
        return
    encoded_db = quote(FM_DB)
    url = f"https://{FM_SERVER}/fmi/data/v1/databases/{encoded_db}/sessions/{token}"
    try:
        requests.delete(url, verify=True, timeout=5)
    except Exception as e:
        print(f"FileMaker Logout Error: {e}")

def fetch_staff_data(token, target_ids):
    """Fetch staff attendance data from FileMaker using the _find endpoint."""
    encoded_db = quote(FM_DB)
    encoded_layout = quote("web用_教職員情報")
    url = f"https://{FM_SERVER}/fmi/data/v1/databases/{encoded_db}/layouts/{encoded_layout}/_find"
    
    headers = {
        "Content-Type": "application/json",
        "X-FM-Data-Access-Token": token,
        "Authorization": f"Bearer {token}"
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
        response = requests.post(url, headers=headers, json=body, verify=True, timeout=10)
        if response.status_code in [404, 401]:
            return []
        if response.status_code == 200:
            return response.json().get("response", {}).get("data", [])
        else:
            print(f"FileMaker Fetch Failed: {response.status_code} {response.text}")
    except Exception as e:
        print(f"FileMaker Fetch Error: {e}")
    return []

def translate_status(sts_code):
    """Translate FileMaker status code into human-readable text."""
    if sts_code == "01":
        return "At Work"
    elif sts_code == "02":
        return "Left Work"
    elif sts_code is None or sts_code == "":
        return "Training / Off / Other"
    else:
        return f"Unknown Status ({sts_code})"
    
def send_ntfy_notification(name, status_text):
    """Send push notification via ntfy.sh."""
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    message = f"[Attendance Update]\nStaff: {name}\nStatus changed to: \"{status_text}\"."
    try:
        requests.post(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title": f"Attendance Alert [{status_text}]",
                "Priority": "urgent",
            },
            timeout=10
        )
    except Exception as e:
        print(f"ntfy Notification Error: {e}")

def lambda_handler(event, context):
    """Main Lambda handler function."""
    print("EventBridge trigger received. Starting targeted polling process...")
    
    target_ids = get_target_staff_ids()
    today_str = get_japan_date_str() # JST
    print(f"Target Staff IDs: {target_ids} | Today (JST): {today_str}")

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
            staff_name = field_data.get("氏名", "Unnamed Staff")
            
            if not staff_id:
                continue

            timedata_list = portal_data.get("tog001_教職員情報db_timedata", [])
            current_sts_code = None
            if timedata_list and len(timedata_list) > 0:
                first_timedata = timedata_list[0]
                if isinstance(first_timedata, dict):
                    current_sts_code = first_timedata.get("tog001_教職員情報db_timedata::sts")

            current_status_str = translate_status(current_sts_code)

            # Get previous station from DynamoDB
            response = table.get_item(Key={"staff_id": staff_id})
            item = response.get("Item", {})
            
            previous_status_str = item.get("status", None)
            saved_date = item.get("attendance_date", None)

            if saved_date != today_str:
                print(f"New day detected for {staff_name} ({staff_id}). Resetting context from {saved_date} to {today_str}.")
                previous_status_str = None

            if previous_status_str == "At Work" and current_status_str == "Training / Off / Other":
                print(f"[Guard Active] Ignored unexpected drop to Off for {staff_name} ({staff_id}) while At Work. Keeping 'At Work'.")
                current_status_str = "At Work"

            if previous_status_str == "Left Work" and current_status_str == "Training / Off / Other":
                print(f"[Guard Active] Ignored unexpected drop to Off for {staff_name} ({staff_id}) after Left Work. Keeping 'Left Work'.")
                current_status_str = "Left Work"

            if previous_status_str != current_status_str:
                print(f"Status change confirmed for {staff_name} ({staff_id}): {previous_status_str} -> {current_status_str}")
                
                if previous_status_str is not None:
                    send_ntfy_notification(staff_name, current_status_str)
                
                table.put_item(
                    Item={
                        "staff_id": staff_id,
                        "name": staff_name,
                        "status": current_status_str,
                        "attendance_date": today_str,
                        "updated_at": context.aws_request_id
                    }
                )

    finally:
        logout_fm_session(token)

    return {
        "statusCode": 200,
        "body": json.dumps("Targeted polling executed successfully with bug guards.")
    }