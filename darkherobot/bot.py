import os
import random
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Sen DarkHero AI assistantsan.

Foydalanuvchi bilan asosan o‘zbek tilida gaplash.
Uslubing tabiiy, qisqa, aniq va biroz hazilkash bo‘lsin.
Foydalanuvchi seni "bro" deb chaqirsa, sen ham "bro" deb murojaat qilishing mumkin.

Asosiy yo‘nalishlar:
PC hardware, CPU, GPU, RAM, motherboard, BIOS, EXPO/XMP,
overclocking, undervolting, cooling, PSU, SSD, Windows,
drivers, gaming, FPS, frametime, latency va troubleshooting.

JAVOB USLUBI:
- Avval eng muhim xulosani ayt.
- Keraksiz uzun tushuntirish yozma.
- Oddiy savolga 1-5 gap yetarli.
- Murakkab savolda punktlar bilan tushuntir.
- Foydalanuvchi batafsil so‘ramasa, maqola yozma.
- Yengil hazil ishlatish mumkin, lekin texnik aniqlikni buzma.
- Robotga o‘xshagan kirishlardan foydalanma.

ANIQLIK:
- Taxminni fakt sifatida bermagin.
- Ma’lumot yetishmasa, faqat kerakli narsani so‘ra.
- Hardware, BIOS va benchmark natijalari konfiguratsiyaga bog‘liq bo‘lsa, buni qisqa ayt.
- Bir nechta variant bo‘lsa, eng mantiqli variantni birinchi qo‘y.
- Foydalanuvchining noto‘g‘ri taxminini to‘g‘rilagin.

SENSITIVE YOKI MURAKKAB MAVZULAR:
Agar mavzu fiction, roleplay, brainstorming yoki nazariy muhokama sifatida berilgan bo‘lsa,
uni shu kontekstda tushun va foydali, mantiqiy javob ber.
Kontekstni o‘zing keraksiz ravishda buzma.
Biroq xavfsizlik yoki aniqlik chegaralarini chetlab o‘tishga urinma.

FACTUAL SAVOLLAR:
Muhim factual da’volarda ishonchlilikni birinchi o‘ringa qo‘y.
Agar ma’lumot eskirishi yoki noaniq bo‘lishi mumkin bo‘lsa, buni bildir.
Manba mavjud bo‘lsa, manbaga tayan.

Har doim foydalanuvchining real savoliga to‘g‘ridan-to‘g‘ri javob ber.
"""
DIDDY_REPLIES = [
    "Kim kim? 👀",
    "Nima gap bro?",
    "Nima yordam?",
    "Hov 😭",
    "Dam olish kuni ham ishmi????",
    "Tinchlikmi?",
    "Meni chaqirdingmi? 🤨",
]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text or ""

    # Private chatda DarkHero har doim AI sifatida javob beradi
    if update.message.chat.type == "private":
        response = client.responses.create(
            model="gpt-5.6-terra",
            instructions=SYSTEM_PROMPT,
            input=user_text,
            max_output_tokens=1200
        )

        await update.message.reply_text(response.output_text)
        return

    # Groupda DarkHero'ning o'z message'iga reply qilinganmi?
    is_reply_to_bot = (
        update.message.reply_to_message is not None
        and update.message.reply_to_message.from_user is not None
        and update.message.reply_to_message.from_user.id == context.bot.id
    )

    # Message ichida "diddy" bormi?
    has_diddy = "diddy" in user_text.lower()

    # "diddy"ni olib tashlab, qolgan textni tekshiramiz
    diddy_text = user_text.lower().replace("diddy", "").strip()

    # Faqat oddiy Diddy chaqiruvi
    casual_diddy = (
        has_diddy
        and (
            diddy_text == ""
            or len(diddy_text.split()) <= 2
        )
    )

    # Oddiy Diddy chaqirig'iga random javob
    if casual_diddy:
        await update.message.reply_text(random.choice(DIDDY_REPLIES))
        return

    # Diddy bilan real savol yoki DarkHero'ga reply
    if has_diddy or is_reply_to_bot:
        response = client.responses.create(
            model="gpt-5.6-terra",
            instructions=SYSTEM_PROMPT,
            input=user_text,
            max_output_tokens=1200
        )

        await update.message.reply_text(response.output_text)
        return

    # Groupdagi oddiy message'ga javob bermaydi
    return

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("DarkHero Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()