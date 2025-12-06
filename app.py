import os
import json
import datetime
import gspread
import pytz
from flask import Flask, request, jsonify
from flask_cors import CORS  # 新增：處理跨域
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# 允許所有來源存取 (或是你可以指定只允許你的 GitHub Pages 網址)
CORS(app) 

# --- 設定區 ---
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

# --- 核心：取得台灣時間字串 ---
def get_tw_time_str():
    tw = pytz.timezone('Asia/Taipei')
    now = datetime.datetime.now(tw)
    return now.strftime("%Y-%m-%d %H:%M")

# --- 連線 Google Sheets ---
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    if creds_json_str:
        creds_dict = json.loads(creds_json_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        
    client = gspread.authorize(creds)
    sheet = client.open_by_url(GOOGLE_SHEET_URL).sheet1
    return sheet

@app.route('/')
def index():
    return "Dr. Muscle Backend is Running!"

# --- API: 記錄訓練 ---
@app.route('/api/log', methods=['POST'])
def log_workout():
    data = request.json
    tw_time = get_tw_time_str()
    
    user_date = data.get('date')
    record_date = user_date if user_date else tw_time.split(' ')[0]
    record_time = tw_time.split(' ')[1]
    
    try:
        sheet = get_sheet()
        
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
        
        return jsonify({"status": "success", "message": "紀錄已儲存"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)