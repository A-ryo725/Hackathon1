# インポート
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
    if db:#接続確認
        doc = db.collection('history').document(today_str).get()#今日のデータを受け取る
        if doc.exists:#今日の部分にデータがあればTrue
            schedule_data = doc.to_dict().get('schedule', [])#辞書形式でタスクを受け取る
    
    return render_template('index.html', schedule=schedule_data, today=today_display)#今日の日付とタスクをindex.htmlに送る

# routines.htmlにアクセスした時 
@app.route('/routine') # AI使用 平日と休日でルーティンを分けるために使用した。また、ユーザーの性格を考慮するため。
def routine_page():
    data = {"weekday": [], "weekend": [], "personality": ""}#データがない時のため
    if db:#接続確認
        doc = db.collection('settings').document('routines').get()#settingsの中のroutinesの中の、性格、平日、休日を受け取る
        if doc.exists:#今日の部分にデータがあればTrue
            data = doc.to_dict() #性格、平日、休日のデータを受け取る。
    return render_template('routines.html', routines=data)#routines.htmlに上記のデータを返す。

# calendar.htmlにアクセスした時
@app.route("/calendar")
def calendar_page():
    now = datetime.now()#カレンダーを開いたときの時刻を受け取る。
    year = int(request.args.get('year', now.year))#URLのパラメータから年を獲得
    month = int(request.args.get('month', now.month))#URLのパラメータから月を獲得
    today = 1#日付の初期値
    if year == now.year and month == now.month:#表示されている月が現在の年と月だった場合
        today = now.day#一致した場合、今日の日付をtodayに代入
    cal = calendar.Calendar(firstweekday=6)#カレンダーを作成、firstweekday=6により、日曜日始まりに
#AI使用　カレンダーを切り替える矢印を押したときにスムーズに動くようにAIを利用した。
    month_days = [day if day != 0 else '' for week in cal.monthdayscalendar(year, month) for day in week]#月の全日をリスト化
    prev_date = datetime(year, month, 1) - timedelta(days=1)#選択月1日の一日前を計算
    next_date = datetime(year, month, 28) + timedelta(days=5) # 確実に翌月へ向かうために28に5を足す。翌月に行くように工夫。
#AI
    return render_template('calendar.html', 
        year=year, month=month, today=today, 
        cal_days=month_days, 
        prev_year=prev_date.year, prev_month=prev_date.month,
        next_year=next_date.year, next_month=next_date.month)#calendar.htmlに対し、年月日、月のリスト、切り替えの際の年月日を返す

# achievement.htmlにアクセスした時
@app.route('/achievement')
def achievement_page():
    if not db: return "DB Error", 500 #dbに接続していない場合、エラーを返す。
    
    now = datetime.now()#アチーブメントのサイトにとんだ際の時刻を取得
    today_str = now.strftime('%Y-%m-%d')#文字列形式で年月日を受けとる
    
    today_doc = db.collection('history').document(today_str).get()#データベース内のhistoryから今日の日付の部分をもってくる
    schedule_data = []
    today_fruit = False
    if today_doc.exists:#今日の日付部分にデータがあれば
        data = today_doc.to_dict()#今日の部分のデータを辞書に変更
        schedule_data = data.get('schedule', [])#scheduleから、今日のタスクをリストで取得
        today_fruit = data.get('fruit_earned', False)#ユーザが本日フルーツを獲得しているかどうか
    
    tree_doc = db.collection('user_data').document('tree').get()#ユーザが現在までにためてきたフルーツがなっている木を取得
    my_fruits = tree_doc.to_dict().get('items', []) if tree_doc.exists else []#これまでに獲得したフルーツのリストを取得

    _, num_days = calendar.monthrange(now.year, now.month)#今月の日数を計算
    refs = []
    for d in range(1, num_days + 1):#全日付を参照
        d_str = f"{now.year}-{now.month:02d}-{d:02d}"#日付をstr形式で取得
        refs.append(db.collection('history').document(d_str))#リストに各日をアペンド
    
    month_docs = db.get_all(refs)#今月１か月分のデータをすべて取得
    month_fruits_count = sum(1 for doc in month_docs if doc.exists and doc.to_dict().get('fruit_earned'))#フルーツを獲得した日を計算

    return render_template(
        'achievement.html',
        today=today_str,
        schedule=schedule_data,
        my_fruits=my_fruits,
        today_fruit_earned=today_fruit,
        month_fruits_count=month_fruits_count,
        days_in_month=num_days
    )#achievement.htmlに日付、タスク、フルーツ、本日のフルーツ、フルーツを獲得した日の合計、今月の日数を返す

# 上書き保存
@app.route('/api/save_routines', methods=['POST'])
def save_routines():
    data = request.json#フロントから送られてきた、ルーティーンや性格を代入
    if db:#データベース接続確認
        db.collection('settings').document('routines').set(data)#設定→ルーティーンの中に今受け取ったデータを上書きで保存する
        return jsonify({"status": "success"})#保存完了を返す
    return jsonify({"error": "DB connection failed"}), 500#接続されていない場合エラーを返す

#API
@app.route('/api/generate', methods=['POST'])
def generate_schedule():
    user_tasks = request.json.get('tasks', [])#ユーザが入力したタスクのリストを受け取る。
    
    now = datetime.now()#現在の時刻を取得
    is_weekend = now.weekday() >= 5 #週末かどうかを判定
    routine_key = "weekend" if is_weekend else "weekday"#週末なら週末用、平日なら平日用
    
    routines = []
    personality = ""
    
    if db:#接続確認
        doc = db.collection('settings').document('routines').get()#db内のルーティン、性格を取得
        if doc.exists:
            data = doc.to_dict()
            routines = data.get(routine_key, [])#週末用または平日用
            personality = data.get('personality', "")
    
    fixed_text = "\n".join([f"{r['time']}: {r['name']}" for r in routines])#ルーティンをAIが読みこみやすいように変更
    task_text = "\n".join([f"{t['name']} ({t['duration']}分)" for t in user_tasks])#ルーティンをAIが読みこみやすいように変更
    
    try:
        response = client.chat.completions.create(#OpenAI　API呼び出し
            model="gpt-4o",#モデルの選択
            messages=[#AIへの指示文
                {"role": "system", "content": get_scheduler_system_prompt()},#システム設定、AIの設定。
                {"role": "user", "content": format_user_query(fixed_text, task_text, personality)}#ユーザーの設定をAIに組み込み
            ],
            response_format={ "type": "json_object" }#Aiの回答をjson形式で変えてもらう
        )
        result = json.loads(response.choices[0].message.content)#AIから帰ってきたデータを辞書形式に
        
        today_str = now.strftime('%Y-%m-%d')
        if db:#接続確認
            doc_ref = db.collection('history').document(today_str)#historyから今日のデータを取得
            if doc_ref.get().exists:#本日のデータがあるかないか
                doc_ref.update({'schedule': result['schedule']})#すでにあれば、上書き
            else:
                doc_ref.set(result)#なければ保存
            
        return jsonify(result)#AIが作成したスケジュールを画面に返す
    except Exception as e:
        return jsonify({"error": str(e)}), 500

#カレンダーで日付選択をしたとき
@app.route('/api/get_plan/<date>', methods=['GET'])
def get_plan(date):
    if not db: return jsonify({"error": "No DB"}), 500#dbと接続がなければエラーを返す
    doc = db.collection('history').document(date).get()#historyからしてされた日付（data）のものを探す
    if doc.exists:#データが存在するか否か
        return jsonify(doc.to_dict())#あればその中身を返す（日程）
    return jsonify({"schedule": []})#なければからのリストを返す

#todoについて
@app.route('/api/toggle_task', methods=['POST'])
def toggle_task():
    data = request.json#タスクを読みこむ
    index = data.get('index')#何番目のタスクか
    is_done = data.get('done')#チェックが入っているか否か
    
    today_str = datetime.now().strftime('%Y-%m-%d')#現在の年月日を文字列形式で取得
    doc_ref = db.collection('history').document(today_str)#db内の今日のデータを参照
    
    doc = doc_ref.get()#今日のデータを読みこみ
    if doc.exists:#今日のデータがあるか否か
        current_data = doc.to_dict()#データを辞書形式に変換
        schedule = current_data.get('schedule', [])#スケジュールの予定を取り出す
        if 0 <= index < len(schedule):#インデックスが範囲内かどうか
            schedule[index]['done'] = is_done#checkの切り替え
            doc_ref.update({'schedule': schedule})#書き換えたタスクを更新
            return jsonify({"status": "success"})
            
    return jsonify({"error": "Data not found"}), 404

@app.route('/api/claim_fruit', methods=['POST']) #AI使用 タスクの達成度に応じてフルーツを獲得できるようにするために使用した
def claim_fruit():
    fruit_types = ['🍎', '🍊', '🍇','🍒', '🍑']
    new_fruit = random.choice(fruit_types) #リストの中からランダムにチョイス
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    hist_ref = db.collection('history').document(today_str)#今日のデータを参照
    hist_ref.update({'fruit_earned': True})#今日のデータにフルーツを獲得したと記録
    
    tree_ref = db.collection('user_data').document('tree')#獲得したフルーツを参照
    tree_doc = tree_ref.get()#treeのフルーツを取得
    current_fruits = tree_doc.to_dict().get('items', []) if tree_doc.exists else []#持っているフルーツをリストで取り出す
    current_fruits.append(new_fruit)#新しくゲットしたフルーツをアペンド
    tree_ref.set({'items': current_fruits})#上書き
    
    return jsonify({"fruit": new_fruit})

if __name__ == '__main__':
    app.run(debug=True, port=5000)