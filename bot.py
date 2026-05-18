"""
Ponsse CRM Bot — Telegram бот для добавления цен в журнал CRM
Деплой на Render.com как Web Service
"""

import os
import re
import json
import httpx
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ── КОНФИГ ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPA_URL  = os.getenv("SUPA_URL", "https://emgpqyxcrqueuewhubxm.supabase.co")
SUPA_KEY  = os.getenv("SUPA_KEY")
CLAUDE_KEY = os.getenv("CLAUDE_KEY")  # опционально — для умного парсинга

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()

# ── SUPABASE ──────────────────────────────────────────────────────────────────
async def save_price(data: dict):
    """Сохраняет цену в Supabase таблицу prices"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPA_URL}/rest/v1/prices",
            headers={
                "apikey": SUPA_KEY,
                "Authorization": f"Bearer {SUPA_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            json={"data": data}
        )
        return r.status_code == 201

async def save_wishlist(art: str, note: str = "") -> bool:
    """Сохраняет артикул в список нет в наличии"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPA_URL}/rest/v1/wishlist",
            headers={
                "apikey": SUPA_KEY,
                "Authorization": f"Bearer {SUPA_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            json={"data": {
                "art": normalize(art),
                "art_raw": art,
                "note": note,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "localId": f"wl_{int(datetime.now().timestamp())}"
            }}
        )
        return r.status_code == 201

async def get_wishlist() -> list:
    """Получает все артикулы из вишлиста"""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPA_URL}/rest/v1/wishlist?select=*&limit=1000",
            headers={
                "apikey": SUPA_KEY,
                "Authorization": f"Bearer {SUPA_KEY}",
            }
        )
        if r.status_code != 200:
            return []
        return [row.get("data", {}) for row in r.json()]

def get_wishlist_stats(items: list, period: str = "month") -> str:
    """Статистика по артикулам нет в наличии"""
    from collections import Counter
    from datetime import timedelta

    now = datetime.now()

    if period == "month":
        label = now.strftime("%B %Y")
        cutoff = now.strftime("%Y-%m")
        filtered = [p for p in items if p.get("date", "").startswith(cutoff)]
    elif period == "week":
        label = "последние 7 дней"
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        filtered = [p for p in items if p.get("date", "") >= cutoff]
    elif period == "all":
        label = "всё время"
        filtered = items
    else:
        label = period
        filtered = [p for p in items if p.get("date", "").startswith(period)]

    if not filtered:
        return f"📋 За {label} запросов не найдено."

    counter = Counter(p.get("art", "—") for p in filtered)

    lines = [f"📋 <b>Нет в наличии — {label}</b>\n"]
    lines.append(f"Всего запросов: <b>{len(filtered)}</b>")
    lines.append(f"Уникальных артикулов: <b>{len(counter)}</b>\n")
    lines.append("🔩 <b>Топ артикулов:</b>")

    for i, (art, cnt) in enumerate(counter.most_common(15), 1):
        # Найдём последнюю заметку
        notes = [p.get("note", "") for p in filtered if p.get("art") == art and p.get("note")]
        note_str = f" — {notes[-1]}" if notes else ""
        times = "раз" if cnt == 1 else "раза" if cnt < 5 else "раз"
        lines.append(f"{i}. <code>{art}</code> — {cnt} {times}{note_str}")

    return "\n".join(lines)

async def search_prices(article: str) -> list:
    """Ищет цены по артикулу в Supabase"""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPA_URL}/rest/v1/prices?select=*",
            headers={
                "apikey": SUPA_KEY,
                "Authorization": f"Bearer {SUPA_KEY}",
            }
        )
        if r.status_code != 200:
            return []
        rows = r.json()
        art_norm = normalize(article)
        results = []
        for row in rows:
            d = row.get("data", {})
            if art_norm in normalize(d.get("art", "")):
                results.append(d)
        return results

async def get_all_prices() -> list:
    """Получает все цены из Supabase"""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPA_URL}/rest/v1/prices?select=*&limit=1000",
            headers={
                "apikey": SUPA_KEY,
                "Authorization": f"Bearer {SUPA_KEY}",
            }
        )
        if r.status_code != 200:
            return []
        return [row.get("data", {}) for row in r.json()]

def get_stats_text(prices: list, period: str = "month") -> str:
    """Формирует текст статистики"""
    from collections import Counter, defaultdict

    now = datetime.now()

    # Фильтр по периоду
    if period == "month":
        label = f"{now.strftime('%B %Y')}"
        cutoff = now.strftime("%Y-%m")
        filtered = [p for p in prices if p.get("date", "").startswith(cutoff)]
    elif period == "week":
        from datetime import timedelta
        label = "последние 7 дней"
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        filtered = [p for p in prices if p.get("date", "") >= cutoff]
    elif period == "all":
        label = "всё время"
        filtered = prices
    else:
        label = period
        filtered = [p for p in prices if p.get("date", "").startswith(period)]

    if not filtered:
        return f"📊 За {label} записей не найдено."

    # Топ артикулов
    art_counter = Counter()
    art_prices = defaultdict(list)
    client_counter = Counter()

    for p in filtered:
        art = p.get("art", "—")
        price = p.get("price")
        who = p.get("who", "").strip()
        art_counter[art] += 1
        if price:
            art_prices[art].append(float(price))
        if who:
            client_counter[who] += 1

    lines = [f"📊 <b>Статистика за {label}</b>\n"]
    lines.append(f"Всего записей: <b>{len(filtered)}</b>")
    lines.append(f"Уникальных артикулов: <b>{len(art_counter)}</b>")
    if client_counter:
        lines.append(f"Уникальных клиентов: <b>{len(client_counter)}</b>")

    # Топ 7 артикулов
    lines.append("\n🔩 <b>Топ артикулов:</b>")
    for i, (art, cnt) in enumerate(art_counter.most_common(7), 1):
        prices_list = art_prices.get(art, [])
        if prices_list:
            mn = int(min(prices_list))
            mx = int(max(prices_list))
            avg = int(sum(prices_list) / len(prices_list))
            if mn == mx:
                price_str = f"{mn:,} ₽".replace(",", " ")
            else:
                price_str = f"{mn:,}–{mx:,} ₽ (ср. {avg:,})".replace(",", " ")
        else:
            price_str = "цена не указана"
        lines.append(f"{i}. <code>{art}</code> — {cnt} раз · {price_str}")

    # Топ клиентов
    if client_counter:
        lines.append("\n👤 <b>Топ клиентов:</b>")
        for i, (who, cnt) in enumerate(client_counter.most_common(5), 1):
            lines.append(f"{i}. {who} — {cnt} запросов")

    return "\n".join(lines)

def normalize(s: str) -> str:
    cyr = {"Р":"P","С":"C","В":"B","А":"A","Е":"E","О":"O","Х":"X","К":"K"}
    s = s.strip().upper()
    return re.sub(r"[\s\-]+", "", "".join(cyr.get(c, c) for c in s))

# ── ПАРСИНГ СООБЩЕНИЯ ─────────────────────────────────────────────────────────
def parse_message(text: str) -> dict | None:
    """
    Парсит сообщение и извлекает артикул, цену, описание, кому.
    Поддерживает форматы:
      0079980 2350
      0079980 2350 руб Северный лес
      0080050 - 279 784,64 с ндс
      артикул 0079980 цена 2350
      P45399 - 1800р - ИП Иванов
    """
    text = text.strip()

    # Слова которые не являются клиентом
    IGNORE_WORDS = {
        'с', 'ндс', 'без', 'руб', 'рублей', 'цена', 'артикул',
        'шт', 'штук', 'за', 'по', 'от', 'до', 'и', 'в', 'на',
        'включая', 'включено', 'налог', 'всего', 'итого'
    }

    # Ищем артикул — буквенно-цифровая последовательность 5-12 символов
    art_match = re.search(r'\b([A-ZА-ЯЁa-zа-яё0-9]{5,12})\b', text)
    if not art_match:
        return None
    art = art_match.group(1).strip()

    # Ищем цену с учётом пробелов как разделителей тысяч
    # Форматы: 279 784,64 / 279784.64 / 2350 / 2 350 руб
    price_patterns = [
        # Число с пробелами как разделитель тысяч + запятая/точка для копеек
        r'(\d{1,3}(?:\s\d{3})+(?:[.,]\d{1,2})?)\s*(?:руб|р\b|₽)?',
        # Обычное число с копейками
        r'(\d+[.,]\d{1,2})\s*(?:руб|р\b|₽)?',
        # Просто число
        r'\b(\d{3,7})\b',
    ]

    price = None
    price_match_end = 0
    for pattern in price_patterns:
        m = re.search(pattern, text[art_match.end():], re.IGNORECASE)
        if m:
            raw = m.group(1).replace(" ", "").replace(",", ".")
            try:
                val = float(raw)
                if 1 < val < 50_000_000:
                    price = val
                    price_match_end = art_match.end() + m.end()
                    break
            except ValueError:
                continue

    if not price:
        return None

    # Ищем клиента — текст после цены, исключая стоп-слова
    who = ""
    remainder = text[price_match_end:].strip()
    # Убираем стоп-слова и знаки
    remainder = re.sub(r'(?i)\b(' + '|'.join(IGNORE_WORDS) + r')\b', ' ', remainder)
    remainder = re.sub(r'[:\-–—,.]', ' ', remainder)
    remainder = re.sub(r'\s+', ' ', remainder).strip()

    # Оставляем только если что-то содержательное осталось
    if len(remainder) > 2 and not remainder.isdigit():
        who = remainder[:60]

    return {
        "art": normalize(art),
        "desc": "",
        "who": who,
        "price": round(price, 2),
        "src": "client",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "localId": f"tg_{int(datetime.now().timestamp())}"
    }

# ── CLAUDE ПАРСИНГ (умный) ────────────────────────────────────────────────────
async def parse_with_claude(text: str) -> dict | None:
    """Использует Claude API для умного парсинга если есть ключ"""
    if not CLAUDE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": CLAUDE_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 200,
                    "messages": [{
                        "role": "user",
                        "content": f"""Извлеки из сообщения: артикул запчасти, цену в рублях, кому называлась цена.
Ответь ТОЛЬКО JSON без пояснений:
{{"art": "артикул", "price": число, "who": "кому или пусто", "desc": "описание или пусто"}}
Если не можешь извлечь артикул или цену — ответь: null

Сообщение: {text}"""
                    }]
                },
                timeout=10.0
            )
            if r.status_code != 200:
                return None
            content = r.json()["content"][0]["text"].strip()
            if content == "null":
                return None
            data = json.loads(content)
            data["src"] = "client"
            data["date"] = datetime.now().strftime("%Y-%m-%d")
            data["localId"] = f"tg_{int(datetime.now().timestamp())}"
            data["price"] = float(data["price"])
            return data
    except Exception:
        return None

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
async def send_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        })

async def set_webhook(url: str):
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{TG_API}/setWebhook", json={"url": url})
        print(f"Webhook: {r.json()}")

# ── ОБРАБОТКА КОМАНД ──────────────────────────────────────────────────────────
async def handle_update(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if not text:
        return

    # /start
    if text == "/start":
        await send_message(chat_id, """👋 <b>Ponsse CRM Бот</b>

Я помогаю вести учёт цен и спроса на запчасти.

<b>Добавить цену:</b>
<code>0079980 2350 руб Северный лес</code>

<b>Нет в наличии — записать запрос:</b>
<code>/want 0079980</code>
<code>/want 0079980 срочно нужен клиенту</code>

<b>Найти цены:</b>
<code>/find 0079980</code>

<b>Статистика цен:</b>
<code>/stats</code> — текущий месяц
<code>/stats week</code> — 7 дней
<code>/stats all</code> — всё время

<b>Статистика спроса (нет в наличии):</b>
<code>/missing</code> — текущий месяц
<code>/missing week</code> — 7 дней
<code>/missing all</code> — всё время

/help — подробная помощь""")
        return

    if text == "/help":
        await send_message(chat_id, """📖 <b>Помощь</b>

<b>Добавить цену:</b>
<code>0079980 2350 руб Северный лес</code>
<code>279 784,64 с ндс артикул 0080050</code>

<b>Записать артикул которого нет в наличии:</b>
<code>/want 0079980</code>
<code>/want 0079980 клиент спрашивал срочно</code>

<b>Найти цены по артикулу:</b>
<code>/find 0079980</code>

<b>Статистика цен:</b>
<code>/stats</code> — текущий месяц
<code>/stats week</code> — 7 дней
<code>/stats all</code> — всё время
<code>/stats 2026-04</code> — апрель 2026

<b>Статистика спроса:</b>
<code>/missing</code> — текущий месяц
<code>/missing week</code> — 7 дней
<code>/missing all</code> — всё время""")
        return

    # /want АРТИКУЛ [заметка] — добавить в список нет в наличии
    if text.startswith("/want") or text.startswith("/нет"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await send_message(chat_id, "Укажи артикул: <code>/want 0079980</code>\nИли с заметкой: <code>/want 0079980 срочно нужен</code>")
            return
        rest = parts[1].strip()
        # Первое слово — артикул, остальное — заметка
        tokens = rest.split(maxsplit=1)
        art = tokens[0]
        note = tokens[1] if len(tokens) > 1 else ""
        ok = await save_wishlist(art, note)
        if ok:
            note_str = f"\n📝 Заметка: {note}" if note else ""
            await send_message(chat_id, f"📋 <b>Добавлено в список нет в наличии</b>\n\n🔩 Артикул: <code>{normalize(art)}</code>{note_str}\n📅 Дата: {datetime.now().strftime('%Y-%m-%d')}")
        else:
            await send_message(chat_id, "⚠️ Ошибка сохранения.")
        return

    # /missing [month|week|all|YYYY-MM] — статистика нет в наличии
    if text.startswith("/missing") or text.startswith("/спрос"):
        parts = text.split(maxsplit=1)
        period = parts[1].strip() if len(parts) > 1 else "month"
        await send_message(chat_id, "⏳ Считаю...")
        items = await get_wishlist()
        stats = get_wishlist_stats(items, period)
        await send_message(chat_id, stats)
        return

    # /stats [month|week|all|YYYY-MM]
    if text.startswith("/stats"):
        parts = text.split(maxsplit=1)
        period = parts[1].strip() if len(parts) > 1 else "month"
        await send_message(chat_id, "⏳ Считаю статистику...")
        all_prices = await get_all_prices()
        stats = get_stats_text(all_prices, period)
        await send_message(chat_id, stats)
        return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await send_message(chat_id, "Укажи артикул: <code>/find 0079980</code>")
            return
        article = parts[1].strip()
        results = await search_prices(article)
        if not results:
            await send_message(chat_id, f"❌ По артикулу <code>{article}</code> ничего не найдено в журнале цен.")
            return
        lines = [f"🔍 <b>Цены по артикулу {article}:</b>\n"]
        for r in results[:10]:
            lines.append(f"• <b>{int(float(r['price'])):,} ₽</b> — {r.get('who','—')} ({r.get('date','')})".replace(",", " "))
        await send_message(chat_id, "\n".join(lines))
        return

    # Парсинг сообщения — сначала пробуем Claude, потом regex
    parsed = await parse_with_claude(text)
    if not parsed:
        parsed = parse_message(text)

    if not parsed:
        # Проверяем — может это просто артикул без цены?
        art_only = re.fullmatch(r'[A-ZА-ЯЁa-zа-яё0-9]{5,12}', text.strip())
        if art_only:
            art = text.strip()
            ok = await save_wishlist(art)
            if ok:
                await send_message(chat_id, f"📋 <b>Записано — нет в наличии</b>\n\n🔩 Артикул: <code>{normalize(art)}</code>\n📅 {datetime.now().strftime('%Y-%m-%d')}\n\nЧтобы добавить заметку: <code>/want {art} текст заметки</code>")
            else:
                await send_message(chat_id, "⚠️ Ошибка сохранения.")
            return

        await send_message(chat_id, """❓ Не смог распознать.

Чтобы <b>добавить цену</b>:
<code>0079980 2350 руб Северный лес</code>

Чтобы записать <b>нет в наличии</b> — просто артикул:
<code>0079980</code>
<code>P45399</code>

или /help""")
        return

    # Сохраняем в Supabase
    ok = await save_price(parsed)
    if ok:
        who_str = f"\n👤 Кому: {parsed['who']}" if parsed.get('who','').strip() else ""
        price_fmt = f"{parsed['price']:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
        await send_message(chat_id, f"""✅ <b>Цена добавлена в CRM!</b>

🔩 Артикул: <code>{parsed['art']}</code>
💰 Цена: <b>{price_fmt} ₽</b>{who_str}
📅 Дата: {parsed['date']}""")
    else:
        await send_message(chat_id, "⚠️ Ошибка сохранения. Проверь соединение с базой данных.")

# ── WEBHOOK ENDPOINT ──────────────────────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    asyncio.create_task(handle_update(update))
    return JSONResponse({"ok": True})

@app.get("/")
async def root():
    return {"status": "Ponsse CRM Bot running"}

@app.get("/setup")
async def setup(request: Request):
    """Вызови один раз чтобы зарегистрировать webhook"""
    host = str(request.base_url).rstrip("/")
    await set_webhook(f"{host}/webhook")
    return {"status": "webhook set", "url": f"{host}/webhook"}

@app.get("/health")
async def health():
    return {"status": "ok", "bot_token_set": bool(BOT_TOKEN), "supa_key_set": bool(SUPA_KEY)}
