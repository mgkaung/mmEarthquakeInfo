import aiohttp
import asyncio
import feedparser
import reverse_geocoder as rg
from telegram import Bot
from telegram.utils.helpers import escape_markdown
from telegram.error import TelegramError
from googletrans import Translator
from pathlib import Path
from datetime import datetime, timedelta
import logging

# Configuration
BOT_TOKEN = 'Your_Telegram_Bot_Token'
CHANNEL_ID = 'Your_Telegram_channel'
FEED_URL = 'https://earthquake.tmd.go.th/feed/rss_tmd.xml'
STORAGE_PATH = Path('processed_ids.txt')
RATE_LIMIT_DELAY = 2  # seconds between messages
CHECK_INTERVAL = 10    # Check every 10 seconds

# Initialize services
translator = Translator()
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def get_rss_feed():
    """Fetch RSS feed asynchronously [[1]]."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(FEED_URL) as response:
                return feedparser.parse(await response.text())
        except Exception as e:
            logging.error(f"RSS fetch failed: {e}")
            return None

async def async_translate(text, dest='en'):
    """Handle translation with input sanitization [[2]][[6]]."""
    text = str(text) if text else ""
    try:
        return await asyncio.to_thread(translator.translate, text, dest=dest)
    except Exception as e:
        logging.warning(f"Translation failed: {e}")
        return type('Dummy', (), {'text': text})

async def parse_entry(entry):
    """Process earthquake data with timezone conversion [[3]][[7]]."""
    # Time conversion UTC -> MMT (UTC+6:30)
    utc_time_str = entry.get('tmd_time', 'N/A')
    mmt_time = 'N/A'
    
    if utc_time_str != 'N/A':
        try:
            utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S UTC")
            mmt_time = utc_time + timedelta(hours=6, minutes=30)
            mmt_time = mmt_time.strftime("%Y-%m-%d %H:%M:%S MMT")
        except ValueError:
            mmt_time = 'N/A'

    # Geolocation processing
    coordinates = (float(entry.get('geo_lat', 0)), 
                   float(entry.get('geo_long', 0)))
    
    geo_data = rg.search(coordinates)[0] if coordinates != (0,0) else {}
    
    return {
        'id': entry.get('id', entry.link),
        'magnitude': entry.get('tmd_magnitude', 'N/A'),
        'time_utc': utc_time_str,
        'time_mmt': mmt_time,
        'latitude': entry.get('geo_lat', 'N/A'),
        'longitude': entry.get('geo_long', 'N/A'),
        'depth_km': entry.get('tmd_depth', 'N/A'),
        'location': (await async_translate(entry.get('title', 'Unknown'))).text,
        'details': (await async_translate(entry.get('comments', ''))).text,
        'nearest_city': geo_data.get('name', 'N/A'),
        'country_code': geo_data.get('cc', 'N/A'),
        'link': entry.get('link', '')
    }

async def send_telegram_message(bot, message):
    """Send message with retry logic [[8]][[10]]."""
    for attempt in range(3):
        try:
            await asyncio.to_thread(
                bot.send_message,
                chat_id=CHANNEL_ID,
                text=message,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            return True
        except TelegramError as e:
            if "blocked" in str(e).lower():
                logging.error("Bot blocked by user")
                return False
            await asyncio.sleep(5)
    return False

def format_message(entry):
    """Format message with MarkdownV2 escaping [[5]][[10]]."""
    return (
        f"⚠️ *မြေငလျင် သတိပေးချက်* ⚠️\n\n"
        f"*ပြင်းအား :* {escape_markdown(entry['magnitude'], 2)}\n"
        f"*အနီးဆုံးမြို့ :* {escape_markdown(entry['nearest_city'], 2)}\n"
        f"*အချိန် :* {escape_markdown(entry['time_mmt'], 2)}\n"
        f"*ဗဟိုချက် တည်နေရာ :* {escape_markdown(entry['latitude'], 2)}, "
        f"{escape_markdown(entry['longitude'], 2)}\n"
        f"*အနက် :* {escape_markdown(entry['depth_km'], 2)} km\n\n"
        f"[အပြည့်စုံဖတ်ရှုရန်]({entry['link']})"
    )

async def main():
    bot = Bot(token=BOT_TOKEN)
    processed_ids = set()
    
    # Load existing IDs
    if STORAGE_PATH.exists():
        with open(STORAGE_PATH, 'r') as f:
            processed_ids.update(f.read().splitlines())

    while True:
        feed = await get_rss_feed()
        if not feed:
            await asyncio.sleep(CHECK_INTERVAL)
            continue
        
        # Process entries from newest to oldest [[4]]
        for entry in feed.entries:
            current_id = entry.get('id', entry.link)
            
            if current_id in processed_ids:
                continue  # Skip already processed entries [[6]]
                
            # Preliminary filter
            if "Myanmar" not in entry.get('title', ''):
                processed_ids.add(current_id)
                continue
                
            # Full processing
            quake_data = await parse_entry(entry)
            
            # Final country check
            if quake_data['country_code'] != 'MM':
                logging.info(f"Filtered non-Myanmar: {current_id}")
                processed_ids.add(current_id)
                continue
                
            # Message preparation
            message = format_message(quake_data)
            
            # Send and track
            if await send_telegram_message(bot, message):
                processed_ids.add(current_id)
                with open(STORAGE_PATH, 'a') as f:
                    f.write(f"{current_id}\n")
                logging.info(f"Sent: {current_id} | Mag: {quake_data['magnitude']}")
                await asyncio.sleep(RATE_LIMIT_DELAY)

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
