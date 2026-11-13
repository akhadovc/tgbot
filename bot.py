import os
import feedparser
import requests
from openai import OpenAI

# API kalitlar va kanal ID (GitHub Secrets dan olinadi)
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # Masalan: @kanaling_nomi yoki -100123456789

client = OpenAI(api_key=OPENAI_API_KEY)

# Barcha 13 ta manbaning rasmiy RSS havolalari
RSS_FEEDS = [
    "https://videocardz.com/feed",
    "https://www.techpowerup.com/rss/news",
    "https://wccftech.com/feed/",
    "https://www.tomshardware.com/feeds/all",
    "https://www.techradar.com/rss",
    "https://www.pcmag.com/feeds/rss/latest",
    "https://www.engadget.com/rss.xml",
    "https://www.cnet.com/rss/news/",
    "https://techcrunch.com/feed/",
    "https://www.wired.com/feed/rss",
    "https://blogs.nvidia.com/feed/",
    "https://community.amd.com/t5/blogs/bg-p/amd-blogs/rss/board",
    "https://rog.asus.com/rss/news"
]

POSTED_FILE = "posted.txt"

def load_posted():
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(url):
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def analyze_news(title, summary, link):
    prompt = f"""
Sen kompyuter texnologiyalari va hardware bo‘yicha tajribali muhandis-ekspertsan.
Quyidagi yangilikni o‘qib, o‘zbek tilida Telegram kanal uchun tayyor, qiziqarli va lo‘nda post yozib ber.

Qoidalar:
1. Faqat hardware, GPU, CPU, operativ xotira, sovutish tizimlari, o‘yin texnologiyalari yoki muhim AI/IT yangiliklariga e'tibor ber. Agar yangilik oddiy maishiy texnika, chegirma (deals) yoki mavzuga aloqasiz reklama bo‘lsa, faqat "SKIP" deb javob ber.
2. Post formati (HTML teglari bilan):
   <b>[Diqqatni tortuvchi o‘zbekcha sarlavha]</b>

   [Asosiy yangilik mazmuni: 2-3 ta qisqa va aniq gap]

   <b>Texnik xulosa:</b> [Muhandis sifatida 1-2 gaplik qisqa tahlil — bu foydalanuvchilarga nima beradi]

Sarlavha: {title}
Matn: {summary}
"""
    response = client.chat.completions.create(
        model="gpt-6.1-terra",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=450
    )
    return response.choices[0].message.content.strip()

def send_to_telegram(text, link):
    full_text = f"{text}\n\n🔗 <a href='{link}'>Manba</a>"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": full_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    res = requests.post(url, json=payload)
    return res.status_code == 200

def main():
    posted_urls = load_posted()
    new_posts_count = 0
    MAX_POSTS_PER_RUN = 5  # Har tekshiruvda ko'pi bilan 5 ta post tashlaydi

    for feed_url in RSS_FEEDS:
        if new_posts_count >= MAX_POSTS_PER_RUN:
            break
        try:
            feed = feedparser.parse(feed_url)
            # Har bir manbadan faqat eng oxirgi 2 ta yangilikni tekshiramiz
            for entry in feed.entries[:2]:
                link = entry.get("link", "")
                if not link or link in posted_urls:
                    continue

                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))[:1500]

                # Avval ro'yxatga qo'shamiz (qayta aylanib qolmasligi uchun)
                save_posted(link)
                posted_urls.add(link)

                analyzed_text = analyze_news(title, summary, link)
                if analyzed_text == "SKIP" or "SKIP" in analyzed_text[:10]:
                    print(f"O'tkazib yuborildi (keraksiz mavzu): {title}")
                    continue

                if send_to_telegram(analyzed_text, link):
                    print(f"Kanalga joylandi: {title}")
                    new_posts_count += 1
                    if new_posts_count >= MAX_POSTS_PER_RUN:
                        break
        except Exception as e:
            print(f"Xatolik ({feed_url}): {e}")

if __name__ == "__main__":
    main()

