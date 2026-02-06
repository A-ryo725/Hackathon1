import os
import json
import calendar
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
import firebase_admin
from firebase_admin import credentials, firestore
from ai_logic import get_scheduler_system_prompt, format_user_query

# 環境変数の読み込み
load_dotenv()

app = Flask(__name__)
app.secret_key = "ai_scheduler_secret_key"
CORS(app)

# OpenAIクライアント初期化
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Firebase初期化
KEY_PATH = "firebase_key.json"
if os.path.exists(KEY_PATH):
    if not firebase_admin._apps:
        cred = credentials.Certificate(KEY_PATH)
        firebase_admin.initialize_app(cred)
db = firestore.client()

# Index.htmlにアクセスした時
@app.route('/')
def index():
    now = datetime.now()
    today_display = f"{now.month}月{now.day}日"
    today_str = now.strftime('%Y-%m-%d')
    schedule_data = []
    if db:
        doc = db.collection('history').document(today_str).get()
        if doc.exists:
            schedule_data = doc.to_dict().get('schedule', [])
    
    return render_template('index.html', schedule=schedule_data, today=today_display)

# routines.htmlにアクセスした時 
@app.route('/routine') # AI使用 平日と休日でルーティンを分けるために使用した。また、ユーザーの性格を考慮するため。
def routine_page():
    data = {"weekday": [], "weekend": [], "personality": ""}
    if db:
        doc = db.collection('settings').document('routines').get()
        if doc.exists:
            data = doc.to_dict()
    return render_template('routines.html', routines=data)

# calendar.htmlにアクセスした時
@app.route("/calendar")
def calendar_page():
    nowtime = datetime.now()
    year = int(request.args.get('year', nowtime.year))
    month = int(request.args.get('month', nowtime.month))
    today = 1
    if year == nowtime.year and month == nowtime.month:
        today = nowtime.day
    cal = calendar.Calendar(firstweekday=6)
#AI使用 カレンダーの月をめくる際の処理を行うため
    month_days = [day if day != 0 else '' for week in cal.monthdayscalendar(year, month) for day in week]
    prev_date = datetime(year, month, 1) - timedelta(days=1)
    next_date = datetime(year, month, 28) + timedelta(days=5) # 確実に翌月へ
    return render_template('calendar.html', 
        year=year, month=month, today=today, 
        cal_days=month_days,
        prev_year=prev_date.year, prev_month=prev_date.month,
        next_year=next_date.year, next_month=next_date.month)

# achievement.htmlにアクセスした時
@app.route('/achievement')
def achievement_page():
    if not db: return "DB Error", 500
    
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    
    today_doc = db.collection('history').document(today_str).get()
    schedule_data = []
    today_fruit = False
    if today_doc.exists:
        data = today_doc.to_dict()
        schedule_data = data.get('schedule', [])
        today_fruit = data.get('fruit_earned', False)
    
    tree_doc = db.collection('user_data').document('tree').get()
    my_fruits = tree_doc.to_dict().get('items', []) if tree_doc.exists else []

    _, num_days = calendar.monthrange(now.year, now.month)
    refs = []
    for d in range(1, num_days + 1):
        d_str = f"{now.year}-{now.month:02d}-{d:02d}"
        refs.append(db.collection('history').document(d_str))
    
    month_docs = db.get_all(refs)
    month_fruits_count = sum(1 for doc in month_docs if doc.exists and doc.to_dict().get('fruit_earned'))

    return render_template(
        'achievement.html',
        today=today_str,
        schedule=schedule_data,
        my_fruits=my_fruits,
        today_fruit_earned=today_fruit,
        month_fruits_count=month_fruits_count,
        days_in_month=num_days
    )

# 上書き保存
@app.route('/api/save_routines', methods=['POST'])
def save_routines():
    data = request.json
    if db:
        db.collection('settings').document('routines').set(data)
        return jsonify({"status": "success"})
    return jsonify({"error": "DB connection failed"}), 500

@app.route('/api/generate', methods=['POST'])
def generate_schedule():
    user_tasks = request.json.get('tasks', [])
    
    now = datetime.now()
    is_weekend = now.weekday() >= 5
    routine_key = "weekend" if is_weekend else "weekday"
    
    routines = []
    personality = ""
    
    if db:
        doc = db.collection('settings').document('routines').get()
        if doc.exists:
            data = doc.to_dict()
            routines = data.get(routine_key, [])
            personality = data.get('personality', "")
    
    fixed_text = "\n".join([f"{r['time']}: {r['name']}" for r in routines])
    task_text = "\n".join([f"{t['name']} ({t['duration']}分)" for t in user_tasks])
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": get_scheduler_system_prompt()},
                {"role": "user", "content": format_user_query(fixed_text, task_text, personality)}
            ],
            response_format={ "type": "json_object" }
        )
        result = json.loads(response.choices[0].message.content)
        
        today_str = now.strftime('%Y-%m-%d')
        if db:
            doc_ref = db.collection('history').document(today_str)
            if doc_ref.get().exists:
                doc_ref.update({'schedule': result['schedule']})
            else:
                doc_ref.set(result)
            
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_plan/<date>', methods=['GET'])
def get_plan(date):
    if not db: return jsonify({"error": "No DB"}), 500
    doc = db.collection('history').document(date).get()
    if doc.exists:
        return jsonify(doc.to_dict())
    return jsonify({"schedule": []})

@app.route('/api/toggle_task', methods=['POST'])
def toggle_task():
    data = request.json
    index = data.get('index')
    is_done = data.get('done')
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    doc_ref = db.collection('history').document(today_str)
    
    doc = doc_ref.get()
    if doc.exists:
        current_data = doc.to_dict()
        schedule = current_data.get('schedule', [])
        if 0 <= index < len(schedule):
            schedule[index]['done'] = is_done
            doc_ref.update({'schedule': schedule})
            return jsonify({"status": "success"})
            
    return jsonify({"error": "Data not found"}), 404

#AI使用 タスクの達成度に応じてフルーツを獲得できるようにするために使用した
@app.route('/api/claim_fruit', methods=['POST']) 
def claim_fruit():
    fruit_types = ['🍎', '🍊', '🍇','🍒', '🍑']
    new_fruit = random.choice(fruit_types)
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    hist_ref = db.collection('history').document(today_str)
    hist_ref.update({'fruit_earned': True})
    
    tree_ref = db.collection('user_data').document('tree')
    tree_doc = tree_ref.get()
    current_fruits = tree_doc.to_dict().get('items', []) if tree_doc.exists else []
    current_fruits.append(new_fruit)
    tree_ref.set({'items': current_fruits})
    
    return jsonify({"fruit": new_fruit})

if __name__ == '__main__':
    app.run(debug=True, port=5000)