<h1>Ma Alert</h1>

### Repository name: Yachiyodai

<p style="display: inline">
    <a href="https://www.python.org/" target="_blank"><img src="https://img.shields.io/badge/-Python-ffff00.svg?logo=python&style=for-the-badge"></a>
</p>

![Logo](/src/image/photo.png)

## 1. Overview

- **Objective:** Periodically poll (fetch at regular intervals) the attendance information of faculty members managed in FileMaker, detect status changes, and send push notifications to smartphones via `ntfy.sh`.
- **Problem Solved:** **Completely prevents false notifications** caused by system-level or temporary bugs on the FileMaker side (e.g., a momentary glitch where everyone's status briefly drops to empty or "Holiday" and immediately reverts) using a dual-guard logic and date management system.

## 2. Tech Stack

- **AWS Lambda**
- Executes Python code in a serverless environment, reducing operational costs and improving maintenance efficiency.

- **AWS SAM (Serverless Application Model)**
- Manages infrastructure configurations (AWS Lambda, DynamoDB, EventBridge, etc.) as code (IaC), ensuring environmental consistency and efficiency in team development.

- **Amazon DynamoDB**
- Stores the previous attendance status and current date to compare against the current status and determine if any changes occurred.

- **Amazon EventBridge**
- Scheduled execution using Cron expressions. Suppresses Lambda execution during nights, holidays, and lunch breaks, balancing cost-efficiency with the elimination of redundant processing.

- **FileMaker Data API**
- The official REST API used to retrieve attendance data from the existing faculty database.

- **Python (v3.12)**
- The implementation language for the Lambda function.

- **ntfy.sh**
- A simple and rapid web notification service for receiving push notifications.

## 3. System Architecture

1. **Trigger:** Amazon EventBridge schedule (Cron) periodically invokes AWS Lambda during specific time slots on weekdays.
2. **Data Acquisition:** AWS Lambda authenticates with the FileMaker Data API (obtains a session token) and fetches the target faculty members' attendance data.
3. **Status Judgment & Guard Processing:**

- Compares the retrieved status with the previous state stored in Amazon DynamoDB.
- Automatically resets the context if the date has changed.
- Detects and neutralizes FileMaker bugs causing "sudden status drops (reverting to holidays)" via guard logic.

4. **Notification & Storage:** Sends a push notification via `ntfy.sh` and updates the DynamoDB record **only** when a valid status change is confirmed.

## 4. Conditions & Operational Policies

- **Version Control via GitHub:** Designed for collaborative multi-user development, utilizing AWS SAM (IaC) to manage infrastructure and application code holistically.
- **Optimization of Operating Hours:** Cron settings in EventBridge halt polling during late nights, weekends, and unnecessary time slots to prevent wasteful API and Lambda executions.
- **Robust Bug Mitigation (Guard Features):**
- **Date Change Reset:** Prevents carrying over the previous day's status upon the first execution after the date (JST) changes.
- **At-Work Guard:** Once a faculty member is marked as `At Work`, their status will not be falsely dropped to `Training / Off / Other` (Holiday) even if data temporarily disappears during the day.
- **Left-Work Guard:** Once marked as `Left Work`, their status is maintained and prevented from dropping to `Training / Off / Other` due to nighttime data glitches.

## 5. API Specification (FileMaker Data API)

The response structure returned from the FileMaker Data API is as follows:

```json
{
    "response": {
        "dataInfo": {
            "database": "Faculty Info DB",
            "layout": "web_Faculty Info",
            "table": "tog001_Faculty Info TB",
            "totalRecordCount": "{Total records in DB}",
            "foundCount": 1,
            "returnedCount": 1
        },
        "data": [
            {
                "fieldData": {
                    "教職員番号": "{Staff ID}",
                    "氏": "{Last Name}",
                    "名": "{First Name}",
                    "氏名": "{Full Name}",
                    "ふりがな＿氏": "{Last Name Ruby}",
                    "ふりがな＿名": "{First Name Ruby}",
                    "ふりがな＿氏名": "{Full Name Ruby}",
                    "状態": "勤務",
                    "tog001_教職員情報tb_教職員写真tb::サムネール": "{Staff Photo URL}",
                    "教科": "{Subject}",
                    "表示用所属部署": "{Subject Display Name}",
                    "tog001_教職員情報tb_ソート順tb::ソート順": "{Display Order}",
                    "tog001_教職員情報tb_職種情報tb::職種区分": "{Staff Category}"
                },
                "portalData": {
                    "tog001_教職員情報db_timedata": [
                        {
                            "recordId": "{Record ID}",
                            "tog001_教職員情報db_timedata::day": "{Date: DD/MM/YYYY}",
                            "tog001_教職員情報db_timedata::sts": "{Status}",
                            "modId": "0"
                        }
                    ]
                },
                "recordId": "{Record ID}",
                "modId": "150"
            }
        ]
    },
    "messages": [
        {
            "code": "0",
            "message": "OK"
        }
    ]
}
```

### Status Translation Logic (`translate_status`)

Based on the value of `tog001_教職員情報db_timedata::sts` within the portal data, status values are translated into internal Japanese/English states as follows:

| Status Code (`sts`)    | Internal Status String        | Notes                                                 |
| ---------------------- | ----------------------------- | ----------------------------------------------------- |
| `"01"`                 | `At Work`                     | Currently working                                     |
| `"02"`                 | `Left Work`                   | Left work / Off duty                                  |
| `null` or `""` (Empty) | `Training / Off / Other`      | Training day, holiday, or other states                |
| Other (Unknown value)  | `Unknown Status ({sts_code})` | Exceptions / Unknown states (Requires log monitoring) |

---

## 6. DynamoDB Data Structure (Schema Design)

Table Name: `FileMakerStaffState` (Partition Key: `staff_id` (String))

| Attribute Name    | Type        | Description                                                                 |
| ----------------- | ----------- | --------------------------------------------------------------------------- |
| `staff_id`        | String (PK) | Staff ID (e.g., `"10000"`)                                                  |
| `name`            | String      | Faculty member's full name (e.g., `"Hoge Hoge"`)                            |
| `status`          | String      | Current confirmed status (`At Work`, `Left Work`, `Training / Off / Other`) |
| `attendance_date` | String      | Japanese Standard Time (JST) date when recorded (e.g., `"2026-11-06"`)      |
| `updated_at`      | String      | Request ID or timestamp upon update                                         |

---

## 7. State Transitions and Bug Mitigation Guard Logic

To absorb FileMaker-side specifications and bugs, the Lambda function evaluates states in the following order:

1. **Automatic Date Reset:**

- If the `attendance_date` in the DB differs from the current date (JST), the previous status carry-over is reset (`previous_status_str = None`) to prevent false notifications at the turn of a new day.

2. **At-Work Guard:**

- If the previous state was `At Work` and temporarily drops to `Training / Off / Other` (treated as holiday) during the poll, it is treated as a FileMaker bug, and `At Work` is forcefully maintained.

3. **Left-Work Guard:**

- If the previous state was `Left Work` and drops to `Training / Off / Other` during nighttime or later, `Left Work` is similarly maintained.

4. **Notification Condition:**

- If the status passes the guards above and actually changes (excluding initial registrations where `None`), an emergency notification is sent via `ntfy.sh`.

## 8. Operational Examples

Timeline examples demonstrating how the system processes events and how the guards function in actual operation. _(Note: Times in the examples are for illustrative purposes)._

### Date Change & Morning Arrival Flow

- **00:01 (Late Night):** Post-deployment manual test execution. DB date is updated, and status is cleaned up to `Training / Off / Other` (No notification sent).
- **08:20 (Morning):** Periodic polling executed.
- **Status Change:** `Training / Off / Other` $\rightarrow$ `At Work`
- **Judgment:** Detected as a normal clock-in.
- **Action:** **"Clock-in notification" is sent.** DB is updated to `At Work` with today's date.

### Daytime FileMaker Bug (Sudden Drop to Holiday) Flow

- **10:05 (Daytime):** Periodic polling executed.
- **Status Change:** `At Work` $\rightarrow$ `Training / Off / Other` (Data loss due to bug).
- **Judgment:** [At-Work Guard] triggers. Since the previous state was `At Work`, the drop to holiday is invalidated, and `At Work` is forcefully maintained.
- **Action:** **No notifications are sent.** DB status remains `At Work`.
- **10:35 (Daytime):** Periodic polling executed.
- **Status Change:** `At Work` $\rightarrow$ `At Work` (Bug recovered).
- **Judgment:** No change.
- **Action:** **No notifications are sent.**

### Normal Departure Flow

- **16:53 (Evening):** Periodic polling executed.
- **Status Change:** `At Work` $\rightarrow$ `Left Work`
- **Judgment:** Detected as a normal clock-out.
- **Action:** **"Clock-out notification" is sent.** DB is updated to `Left Work`.

### Post-Departure Nighttime Bug Flow

- **19:00 (Night):** Periodic polling executed.
- **Status Change:** `Left Work` $\rightarrow$ `Training / Off / Other` (Nighttime bug).
- **Judgment:** [Left-Work Guard] triggers. Since the previous state was `Left Work`, the drop to holiday is invalidated, and `Left Work` is forcefully maintained.
- **Action:** **No notifications are sent.** DB remains `Left Work`.

## 9. Why is the repository named "Yachiyodai"?

Do you know Yachiyodai Station?

> Yachiyodai Station (八千代台駅, Yachiyodai-eki) is a passenger railway station in the city of Yachiyo, Chiba, Japan, operated by the private railway operator Keisei Electric Railway.
> _— from Wikipedia_

I came up with the idea for Ma Alert when I visited Yachiyodai Station.
