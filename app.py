import os
import json
import datetime
import gspread
import pytz
import json
from flask import Flask, request, jsonify, render_template
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- 設定區 ---
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# --- 🔥 新增：短期對話記憶 (只存在記憶體中，重啟會清空) ---
# 格式: [{"role": "user", "text": "..."}, {"role": "model", "text": "..."}]
CHAT_HISTORY = []
MAX_HISTORY = 10  # 只保留最近 10 輪對話，避免 Token 爆炸

def update_history(role, text):
    """更新對話紀錄，並保持在限制長度內"""
    global CHAT_HISTORY
    CHAT_HISTORY.append({"role": role, "text": text})
    if len(CHAT_HISTORY) > MAX_HISTORY * 2: # *2 因為一問一答算兩句
        CHAT_HISTORY = CHAT_HISTORY[-MAX_HISTORY*2:]

def get_history_text():
    """將對話紀錄轉為文字字串供 Prompt 使用"""
    history_str = "\n【近期對話歷史 (Context)】：\n"
    if not CHAT_HISTORY:
        return history_str + "(無)"
    
    for msg in CHAT_HISTORY:
        role_name = "學員" if msg["role"] == "user" else "教練AI"
        history_str += f"{role_name}: {msg['text']}\n"
    return history_str

# --- 核心：取得台灣時間字串 ---
def get_tw_time_str():
    tw = pytz.timezone('Asia/Taipei')
    now = datetime.datetime.now(tw)
    return now.strftime("%Y-%m-%d %H:%M")

# --- 連線 Google Sheets (修改版：支援 Zeabur 環境變數) ---
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # 1. 嘗試從環境變數讀取 (Zeabur 部署時會用到這個)
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    if creds_json_str:
        # 如果環境變數有值，將字串轉為 JSON 字典
        creds_dict = json.loads(creds_json_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        # 2. 如果沒環境變數，就找本地檔案 (你在電腦測試時用這個)
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        
    client = gspread.authorize(creds)
    sheet = client.open_by_url(GOOGLE_SHEET_URL).sheet1
    return sheet



# --- System Prompt ---
def get_system_prompt():
    current_time = get_tw_time_str()
    return f"""
你現在是我的專業肌力體能教練。
【現在時間 (台灣)】：{current_time}

【學員資料】
身份：士博，28歲 男性 。174cm / 62kg。
限制：睡眠不足 (6hr)，每週練 3-4 次，每次 50 分鐘。
弱點：無氧耐力差，後鍊 (Posterior Chain) 弱。
目標：增重至 66kg，建立負重久站結構。

【任務】
1. 若我回報訓練數據，請記錄並給予「下一組建議」。
2. 若我是一般對話 (問問題、閒聊)，請以教練身份專業回答。
3. 語氣：簡潔、專業、鼓勵性，像個好夥伴。
"""

def get_db_history(sheet, exercise_name):
    # (保留原本的 Google Sheet 讀取功能)
    try:
        all_records = sheet.get_all_records()
        related_logs = [r for r in all_records if r['Exercise'] == exercise_name]
        recent_logs = related_logs[-3:] 
        context_str = f"\n【{exercise_name} 的歷史數據 (資料庫)】：\n"
        if not recent_logs:
            context_str += "無過去紀錄。"
        else:
            for log in recent_logs:
                context_str += f"- {log['Date']}: {log['Weight']}kg x {log['Reps']}次 ({log['Note']})\n"
        return context_str
    except Exception as e:
        return ""

@app.route('/')
def index():
    return render_template('index.html')

# --- API 1: 記錄訓練 ---
@app.route('/api/log', methods=['POST'])
def log_workout():
    data = request.json
    tw_time = get_tw_time_str() # 取得當下時間
    
    # 決定使用的日期：如果有前端傳來的 date 就用，否則用當下日期
    user_date = data.get('date')
    if user_date:
        record_date = user_date
    else:
        record_date = tw_time.split(' ')[0]

    record_time = tw_time.split(' ')[1] # 時間仍記錄當下操作時間，方便追蹤 Log 順序
    
    try:
        sheet = get_sheet()
        db_context = get_db_history(sheet, data['exercise'])
        
        # 存檔 (使用 record_date)
        row = [
            record_date, 
            record_time, 
            data['exercise'], 
            data['weight'], 
            data['reps'], 
            data['sets'], 
            data['note'],
            data['location']
        ]
        sheet.append_row(row)
        
        # 組合 User 訊息
        user_msg = (
            f"我剛在場地 {data['location']} 完成了：{data['exercise']}，"
            f"日期：{record_date}，"
            f"重量 {data['weight']} kg，做 {data['reps']} 下，第 {data['sets']} 組。"
            f"狀態備註：{data['note']}。"
        )
        
        # 🔥 更新記憶
        update_history("user", user_msg)
        
        # 組合 Prompt
        full_prompt = get_system_prompt() + get_history_text() + db_context + "\n【請針對此回報給予建議】"
        
        response = model.generate_content(full_prompt)
        ai_reply = response.text
        
        # 🔥 更新記憶 (AI 回覆)
        update_history("model", ai_reply)
        
        return jsonify({"status": "success", "ai_response": ai_reply})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- API 2: 純對話 ---
@app.route('/api/chat', methods=['POST'])
def chat_only():
    data = request.json
    user_msg = data.get('message', '')
    
    try:
        # 🔥 更新記憶
        update_history("user", user_msg)
        
        # 組合 Prompt
        full_prompt = get_system_prompt() + get_history_text() + "\n(請回應最後一句話)"
        
        response = model.generate_content(full_prompt)
        ai_reply = response.text
        
        # 🔥 更新記憶
        update_history("model", ai_reply)
        
        return jsonify({"status": "success", "ai_response": ai_reply})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)