# Diddy 2.0 — DarkHero AI bot

Telegram bot, `gpt-6.1-sol` (OpenAI Responses API) bilan ishlaydi.

## Nima qila oladi

- **Guruhda:** "Diddy" deb yozsangiz yoki botning xabariga reply qilsangiz javob beradi. Faqat "Diddy" yoki "Diddy nima gap" desangiz pulsiz hazil javob qaytaradi.
- **Guruh tarixi:** oxirgi 24 soat xabarlarini eslab turadi. "Diddy, bugun nima gaplashildi?" yoki `/xulosa 6` (oxirgi 6 soat) ishlaydi.
- **Reply:** kimningdir xabariga reply qilib "Diddy, bu to'g'rimi?" desangiz, o'sha xabarni ham hisobga oladi.
- **Rasm:** rasmga "Diddy" deb caption yozing yoki rasmga reply qilib "Diddy" deng.
- **Internet:** narx, yangilik, yangi GPU kabi savollarda o'zi qidiradi va manbalarni ko'rsatadi. `/qidir <savol>` — chuqur qidiruv.
- **Video:** videoga reply qilib "Diddy ..." deb yozing yoki botning xabariga reply qilib video tashlang. Bot 8 ta kadr va ovozni (matnga aylantirib) ko'radi, keyin reaksiya beradi. 10 daqiqada bitta video; bir vaqtda bir nechta kelsa, birinchisidan boshlab navbat bilan. 20 MB gacha (Telegram cheklovi). Shaxsiy chatda videoni to'g'ridan-to'g'ri tashlasa ham bo'ladi.
- **Shaxsiy chat:** oxirgi 6 ta savol-javobni eslaydi. `/reset` xotirani tozalaydi.
- **Byudjet:** oylik limit (`MONTHLY_BUDGET_USD`) va har bir odam uchun kunlik limit (`DAILY_USER_LIMIT`). Egasi (`OWNER_IDS`) limitsiz, `/usage` bilan sarfni ko'radi.

## O'rnatish (Windows)

Papkani ko'chirganingiz uchun eski `.venv` buzilgan bo'lishi mumkin, uni qaytadan yarating:

```
cd C:\Users\inGame\Documents\darkherobot
rmdir /s /q .venv
py -3.14 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Sozlash

1. `.env` faylingizda `TELEGRAM_BOT_TOKEN` va `OPENAI_API_KEY` allaqachon bor, ular o'zgarmaydi.
2. Oddiy sozlamalar `sozlamalar.env` faylida (namuna: `sozlamalar.env.example`). Kalitlar faqat `.env` da.
3. Botni ishga tushiring, unga shaxsiyda `/id` yozing va chiqqan raqamni `.env` ga `OWNER_IDS=...` qilib yozing. Guruh ID'sini ham shunday bilib, `ALLOWED_GROUP_IDS` ga qo'yishingiz mumkin.
4. **Muhim:** @BotFather → `/setprivacy` → botingiz → **Disable**. Aks holda bot guruhdagi oddiy xabarlarni ko'rmaydi: "Diddy" so'zini ham, guruh tarixini ham. O'zgartirgach botni guruhdan chiqarib qayta qo'shing.

## Ishga tushirish

```
.venv\Scripts\activate
python bot.py
```

Testlar: `python -m unittest discover -s tests`

## Boshqarish (egasi uchun, bot bilan shaxsiy chatda)

- `/pauza` — shaxsiy (Business) chatlarda avtomatik javobni to'xtatadi, `/davom` — yoqadi, `/holat` — holat
- `/usage` — oylik sarf, `/id` — chat va user ID

## Fleshkada olib yurish (USB / portable)

1. Fleshkani kompyuterga qo'ying va papkadagi `make_usb.bat` ni bosing (1-3 daqiqa).
   Python, kutubxonalar, kod, `.env`, `sozlamalar.env` va bot xotirasi fleshkadagi `DiddyBot` papkasiga yoziladi.
   Uydagi bot avtomatik to'xtatiladi (bir token bilan ikki joyda ishlab bo'lmaydi).
2. Istalgan Windows kompyuterda: fleshkadagi `DiddyBot\START_DIDDY.bat` ni bosing. Hech narsa o'rnatish shart emas.
3. Fleshkani chiqarsangiz, bot 2 soniya ichida o'chadi.

Eslatmalar:
- Fleshkada API kalitlar bor (`.env`). Fleshka yo'qolsa — @BotFather'da `/revoke`, OpenAI'da kalitni o'chiring.
- Ish joyidagi tarmoq Telegram yoki OpenAI'ni bloklagan bo'lsa, bot ulana olmaydi.
- Uyga qaytganda: fleshkadagi botni yoping, keyin uyda `start_diddy.bat`. Fleshkadagi xotira uydagidan alohida bo'ladi;
  yangisini uyga qaytarish uchun fleshkadagi `diddy.db` ni uydagi papkaga nusxalang (bot o'chiq paytda).

## Xarajat haqida

`gpt-6.1-sol`: kirish $2 / 1M token, keshlangan kirish $0.10, chiqish $10, web qidiruv $0.01 / chaqiruv.
Video: 1 daqiqalik video taxminan $0.02–0.04 (kadrlar + ovozni matnga aylantirish $0.0045/daq).
Oddiy guruh savoli (tarix bilan) taxminan $0.01–0.03 turadi, `/qidir` $0.10–0.25. $20 oylik byudjet taxminan 700–1500 ta savolga yetadi.
`/usage` dagi summa taxminiy, aniq hisob: platform.openai.com → Usage.
Arzonroq qilish uchun: `HISTORY_CONTEXT_MESSAGES` ni kamaytiring yoki `WEB_SEARCH=false` qiling.

## Maxfiylik

Bot guruh xabarlarini faqat `HISTORY_HOURS` (24 soat) davomida `diddy.db` faylida saqlaydi, keyin o'chiradi.
OpenAI'ga `store=false` bilan yuboriladi. Guruh a'zolariga bot tarixni o'qishini aytib qo'ying.
