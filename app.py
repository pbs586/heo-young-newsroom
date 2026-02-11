import os
import csv
import requests
import re
import html
import threading
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from flask_apscheduler import APScheduler
from datetime import datetime
from email.utils import parsedate_to_datetime

# Flask 앱 설정
app = Flask(__name__)
CORS(app)

# APScheduler 설정
scheduler = APScheduler()
app.config['SCHEDULER_API_ENABLED'] = True

# 설정 정보 (환경 변수 사용)
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "GPX0rI_YjHBslAr9z8PL")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "KoSkn2naT5")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8029820176:AAHyhTsInoorQJXzje7QQYi15Y9RTMf9tkI")
CHAT_ID = os.environ.get("CHAT_ID", "-1003892633377")
CSV_PATH = "naver_news_results.csv"

def clean_text(text):
    if not text: return ""
    text = re.sub('<.*?>', '', text)
    return html.unescape(text)

def format_date(pubDate):
    try:
        dt = parsedate_to_datetime(pubDate)
        am_pm = "오전" if dt.hour < 12 else "오후"
        hour = dt.hour if 1 <= dt.hour <= 12 else abs(dt.hour - 12) if dt.hour > 12 else 12
        return f"{dt.year}.{dt.month:02}.{dt.day:02}. {am_pm} {hour:02}:{dt.minute:02}"
    except:
        return pubDate

def send_telegram(news_item):
    """새 뉴스 발견 시 텔레그램 발송"""
    title = news_item['title']
    link = news_item['link']
    date = news_item['pubDate']
    
    text = f"<b>[신규 뉴스 발견]</b>\n\n📰 {title}\n📅 {date}\n\n<a href='{link}'>🔗 원문 보기</a>"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

def init_csv():
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['검색일시', '제목', '링크', '설명', '언론사', '발행일'])

def get_existing_links():
    links = set()
    if os.path.exists(CSV_PATH):
        try:
            with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    links.add(row.get('링크'))
        except:
            pass
    return links

# 💡 Background Job: 10분마다 실행되는 뉴스 동기화
@scheduler.task('interval', id='sync_news_job', minutes=10, misfire_grace_time=900)
def background_news_sync():
    print(f"[{datetime.now()}] News Sync Started...")
    init_csv()
    existing_links = get_existing_links()
    
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    params = {"query": "허영 의원", "display": 15, "sort": "date"}
    
    try:
        res = requests.get(url, headers=headers, params=params)
        items = res.json().get('items', [])
        
        new_items = []
        for item in items:
            link = item['link']
            if link not in existing_links:
                processed = {
                    "title": clean_text(item['title']),
                    "link": link,
                    "description": clean_text(item['description']),
                    "pubDate": format_date(item['pubDate'])
                }
                new_items.append(processed)
        
        # 새로운 뉴스 저장 및 텔레그램 발송
        if new_items:
            with open(CSV_PATH, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                for item in new_items[::-1]: # 오래된 것부터 저장
                    writer.writerow([
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        item['title'],
                        item['link'],
                        item['description'],
                        "네이버 뉴스",
                        item['pubDate']
                    ])
                    # 텔레그램 발송
                    send_telegram(item)
            print(f"Saved {len(new_items)} new news items and sent alerts.")
        else:
            print("No new news found.")
            
    except Exception as e:
        print(f"Error during news sync: {e}")

@app.route('/')
def index():
    return render_template('index.html')

# 1. 뉴스 기능 (접속 시 로딩 속도를 위해 CSV에서 최신 6개만 가져옴)
@app.route('/api/news')
def get_recent_news():
    if not os.path.exists(CSV_PATH):
        # CSV가 없으면 최초 1회만 직접 호출하여 생성 시도
        background_news_sync()
        
    recent_items = []
    try:
        with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            data = list(reader)
            # 가장 뒤에 있는(최신) 뉴스 6개 추출
            recent_items = data[-6:][::-1]
            return jsonify([{
                "title": i.get('제목'),
                "link": i.get('링크'),
                "description": i.get('설명'),
                "pubDate": i.get('발행일')
            } for i in recent_items])
    except:
        return jsonify([])

# 2. 뉴스 아카이브 (CSV 검색 및 페이징)
@app.route('/api/all-news')
def get_archive_news():
    page = int(request.args.get('page', 1))
    keyword = request.args.get('keyword', '').strip().lower()
    page_size = 10
    
    if not os.path.exists(CSV_PATH):
        return jsonify({"message": "현재 데이터를 수집 중입니다", "items": [], "total_pages": 0})
    
    archive = []
    try:
        with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                item = {
                    "title": row.get('제목', ''),
                    "link": row.get('링크', ''),
                    "description": row.get('설명', ''),
                    "pubDate": row.get('발행일', '')
                }
                if keyword:
                    if keyword in item['title'].lower() or keyword in item['description'].lower():
                        archive.append(item)
                else:
                    archive.append(item)
        
        archive = archive[::-1]
        total_items = len(archive)
        total_pages = (total_items + page_size - 1) // page_size
        start_idx = (page - 1) * page_size
        paged_items = archive[start_idx:start_idx + page_size]
        
        if not archive:
            return jsonify({"message": "수집된 데이터가 없습니다.", "items": [], "total_pages": 0})
            
        return jsonify({
            "items": paged_items,
            "total_pages": total_pages,
            "current_page": page,
            "total_items": total_items
        })
    except:
        return jsonify({"message": "오류 발생", "items": [], "total_pages": 0})

# 3. 소통 창구 (텔레그램 전송)
@app.route('/api/contact', methods=['POST'])
def send_contact():
    data = request.json
    text = f"<b>[홈페이지 민원 접수]</b>\n\n👤 성함: {data.get('name')}\n📞 연락처: {data.get('phone')}\n\n📝 내용:\n{data.get('message')}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
        return jsonify({"success": True})
    except:
        return jsonify({"success": False}), 500

if __name__ == '__main__':
    scheduler.init_app(app)
    scheduler.start()
    
    # 서버 기동 시 최초 1회 즉시 실행 (데이터 확보)
    threading.Thread(target=background_news_sync).start()
    
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
