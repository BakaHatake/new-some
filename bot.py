import os
import tempfile
import asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
import enka
from enkacard.encbanner import ENC
from enkanetwork import EnkaNetworkAPI
from pymongo import MongoClient

# --- MongoDB setup ---
MONGO_URI = "mongodb+srv://bakahatake:anush%40123@baka.f3g4xlx.mongodb.net/"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client['genshin']
profiles_col = db['profiles']
templates_col = db['templates']

def save_user_profile(user_id: int, uid: str):
    profiles_col.update_one({"user_id": user_id}, {"$set": {"uid": uid}}, upsert=True)
def delete_user_profile(user_id: int):
    profiles_col.delete_one({"user_id": user_id})
def get_user_profile(user_id: int):
    doc = profiles_col.find_one({"user_id": user_id})
    return doc["uid"] if doc else None
def save_user_template(user_id: int, category: str, choice: int):
    templates_col.update_one({"user_id": user_id}, {"$set": {category: choice}}, upsert=True)
def get_user_template(user_id: int):
    doc = templates_col.find_one({"user_id": user_id})
    if doc: return {k: v for k, v in doc.items() if k not in ("_id", "user_id")}
    return {}

def mark_owner(context, msg_id, user_id):
    context.application.bot_data.setdefault("msg_owner", {})[msg_id] = user_id
def get_owner(context, msg_id):
    return context.application.bot_data.get("msg_owner", {}).get(msg_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use /genshinlogin <UID> to login your Genshin Impact account.\n"
        "Then use /myc to view your profile card."
    )
import akasha
from akasha.enums import Language

async def fetch_akasha_rankings(uid: int) -> dict:
    async with akasha.AkashaAPI(lang=Language.ENGLISH) as api:
        results = {}
        user_calcs = await api.get_calculations_for_user(uid)
        for character in user_calcs:
            if not character.calculations:
                continue
            calc = character.calculations[0]
            # Fetch total number of users for this rank
            out_of = getattr(calc, 'out_of', None)
            if out_of is None:
                try:
                    out_of = await api.get_leaderboard_total_size(calc.id)
                except Exception:
                    out_of = "?"
            results[character.name] = {
                "top_percent": calc.top_percent,
                "ranking": calc.ranking,
                "out_of": out_of,
                "id": getattr(character, "id", None)
            }
        return results

async def genshinlogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /genshinlogin <numeric_uid>")
        return
    uid = context.args[0].strip()
    if not (8 <= len(uid) <= 10):
        await update.message.reply_text("UID length should be 8 to 10 digits.")
        return
    msg = await update.message.reply_text("🔄 Fetching profile for UID...", reply_to_message_id=update.message.message_id)
    mark_owner(context, msg.message_id, user_id)
    try:
        async with enka.GenshinClient(enka.gi.Language.ENGLISH) as client:
            response = await client.fetch_showcase(int(uid))
        characters = response.characters
        if not characters:
            await msg.edit_text("No characters found or profile is private.")
            return
        templates = get_user_template(user_id)
        profile_tplt = templates.get("profile", 1)
        async with ENC(uid=uid, lang="en") as encard:
            profile = await encard.profile(card=True, teamplate=profile_tplt)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image_path = tmp.name
            profile.card.save(image_path)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("💾 Save UID", callback_data=f"save_uid|{user_id}"),
            InlineKeyboardButton("🗑️ Delete UID", callback_data=f"delete_uid|{user_id}")
        ]])
        with open(image_path, "rb") as f:
            await msg.edit_media(
                media=InputMediaPhoto(f, caption=f"📋 UID {uid} Profile\nDo you want to save this UID?"),
                reply_markup=keyboard
            )
        os.remove(image_path)
        context.user_data['temp_uid'] = uid
    except Exception as e:
        await msg.edit_text(f"Failed to fetch profile or generate card: {e}")

async def save_or_delete_uid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, orig_user_id = query.data.split('|')
    orig_user_id = int(orig_user_id)
    if query.from_user.id != orig_user_id:
        await query.answer("Only the original user can use these buttons!", show_alert=True)
        return
    temp_uid = context.user_data.get('temp_uid')
    if not temp_uid:
        await query.message.edit_caption("Session expired or invalid. Please try /genshinlogin again.")
        return
    if action == "save_uid":
        save_user_profile(orig_user_id, temp_uid)
        await query.message.edit_caption(f"✅ UID {temp_uid} saved successfully.")
    elif action == "delete_uid":
        delete_user_profile(orig_user_id)
        await query.message.edit_caption(f"🗑️ UID {temp_uid} was not saved / deleted.")
    else:
        await query.message.edit_caption("Unknown action.")
    context.user_data.pop('temp_uid', None)

async def myc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    uid = get_user_profile(user_id)
    if not uid:
        await update.message.reply_text("You have not set your UID. Use /genshinlogin <uid>.")
        return
    orig_msg = update.message
    msg = await orig_msg.reply_text("🔄 Fetching your profile card...", reply_to_message_id=orig_msg.message_id)
    mark_owner(context, msg.message_id, user_id)
    try:
        await send_profile_card(uid, msg, user_id, context)
    except Exception as e:
        await msg.edit_text(f"Failed to fetch profile or generate card: {e}")

async def send_profile_card(uid, msg, user_id, context):
    import akasha
    from akasha.enums import Language

    # Fetch Enka profile/character info
    async with enka.GenshinClient(enka.gi.Language.ENGLISH) as client:
        response = await client.fetch_showcase(int(uid))
    characters = response.characters
    if not characters:
        await msg.edit_text("No characters found or profile is private.")
        return

    # Fetch Akasha Top % and Rankings info
    async with akasha.AkashaAPI(lang=Language.ENGLISH) as api:
        akasha_rankings = {}
        user_calcs = await api.get_calculations_for_user(uid)
        for character in user_calcs:
            if not character.calculations:
                continue
            calc = character.calculations[0]
            out_of = getattr(calc, 'out_of', None)
            if out_of is None:
                try:
                    out_of = await api.get_leaderboard_total_size(calc.id)
                except Exception:
                    out_of = "?"
            akasha_rankings[character.name] = {
                "top_percent": calc.top_percent,
                "ranking": calc.ranking,
                "out_of": out_of,
                "id": getattr(character, "id", None)
            }

    user_templates = get_user_template(user_id)
    profile_tplt = user_templates.get("profile", 1)

    async with ENC(uid=uid, lang="en") as encard:
        profile = await encard.profile(card=True, teamplate=profile_tplt)


    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        image_path = tmp.name
        profile.card.save(image_path)

    # ONLY Top X% (Rank/Total) for every character!
    caption = f"📋 UID {uid} Profile\n"

    keyboard, row = [], []
    for idx, char in enumerate(characters[:12]):
        row.append(InlineKeyboardButton(char.name, callback_data=f"char_{char.id}|{user_id}"))
        if (idx + 1) % 4 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard = InlineKeyboardMarkup(keyboard)

    with open(image_path, "rb") as f:
        await msg.edit_media(
            media=InputMediaPhoto(f, caption=caption, parse_mode="HTML"),
            reply_markup=keyboard
        )
    os.remove(image_path)


async def character_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cbdata, orig_user_id = query.data.split('|')
    orig_user_id = int(orig_user_id)
    if query.from_user.id != orig_user_id:
        await query.answer("Only the original user can use these buttons!", show_alert=True)
        return

    user_id = query.from_user.id
    uid = get_user_profile(user_id)
    if not uid:
        await query.message.edit_text("UID not set. Please use /genshinlogin first.")
        return

    char_id = int(cbdata.split("_")[1])
    await query.message.edit_caption("🔄 Fetching character build card...")

    # Fetch Enka character list for mapping char_id to char_name
    async with enka.GenshinClient(enka.gi.Language.ENGLISH) as client:
        response = await client.fetch_showcase(int(uid))
    characters = response.characters
    char_name = next((c.name for c in characters if c.id == char_id), None)

    # Fetch Akasha per-character ranking
    akasha_rankings = await fetch_akasha_rankings(uid)
    a = akasha_rankings.get(char_name) if char_name else None

    # Compose caption: ONLY Top X% (Rank/Total)
    caption = f"🔧 Build: {char_name}"
    if a:
        percent = f"{a['top_percent']:.2f}"                  
        ranking = f"{a['ranking']:,}"                          
        out_of = f"{a['out_of']:,}" if isinstance(a['out_of'], int) else a['out_of']
        caption += f"\nTop {percent}% ({ranking}/{out_of})"  
    else:
        caption += "\nAkasha ranking: —"
    user_templates = get_user_template(user_id)
    card_tplt = user_templates.get("card", 1)
    async with ENC(uid=uid, lang="en") as encard:
        result = await encard.creat(template=card_tplt, akasha=a)

    # Find the card image for this character
    found = False
    for card_obj in result.card:
        if card_obj.id == char_id:
            found = True
            img = card_obj.card


            if img is None:
                await query.message.edit_caption(f"⚠️ No image found for {char_name}.")
                return
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image_path = tmp.name
                img.save(image_path)
            go_back_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Go Back", callback_data=f"go_back_profile|{user_id}")]
            ])
            with open(image_path, "rb") as f:
                await query.message.edit_media(
                    media=InputMediaPhoto(f, caption=caption, parse_mode="HTML"),
                    reply_markup=go_back_keyboard
                )
            os.remove(image_path)
            break
    if not found:
        await query.message.edit_caption("⚠️ Character not found in your profile.")



async def go_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cbdata, orig_user_id = query.data.split('|')
    orig_user_id = int(orig_user_id)
    if query.from_user.id != orig_user_id:
        await query.answer("Only the original user can use these buttons!", show_alert=True)
        return
    user_id = query.from_user.id
    uid = get_user_profile(user_id)
    if not uid:
        await query.message.edit_text("UID not set. Use /genshinlogin.")
        return
    await query.message.edit_caption("🔄 Regenerating profile card...")
    await send_profile_card(uid, query.message, user_id, context)

async def template_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Profile Template", callback_data="choose_profile_template")],
        [InlineKeyboardButton("🃏 Card Template", callback_data="choose_card_template")]
    ])
    await update.message.reply_text("⚙️ Choose what to customize:", reply_markup=keyboard)

async def profile_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Template 1", callback_data="profile_1")],
        [InlineKeyboardButton("Template 2", callback_data="profile_2")]
    ])
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Select Profile Template:", reply_markup=keyboard)

async def card_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Card Template {i}", callback_data=f"card_{i}") for i in range(1, 6)]
    ])
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Select Card Template:", reply_markup=keyboard)

async def store_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    parts = data.split("_")
    if len(parts) < 2:
        await query.answer("Invalid selection")
        return
    category = parts[0]
    try:
        choice = int(parts[1])
    except:
        await query.answer("Invalid selection")
        return
    save_user_template(user_id, category, choice)
    await query.answer()
    await query.message.reply_text(f"✅ {category.capitalize()} template set to {choice}")

def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("genshinlogin", genshinlogin))
    app.add_handler(CommandHandler("myc", myc))
    app.add_handler(CommandHandler("template", template_menu))
    app.add_handler(CallbackQueryHandler(save_or_delete_uid_callback, pattern=r"^(save_uid|delete_uid)\|\d+$"))
    app.add_handler(CallbackQueryHandler(profile_selector, pattern="choose_profile_template"))
    app.add_handler(CallbackQueryHandler(card_selector, pattern="choose_card_template"))
    app.add_handler(CallbackQueryHandler(store_choice, pattern=r"^(profile|card)_\d+$"))
    app.add_handler(CallbackQueryHandler(character_callback, pattern=r"^char_\d+\|\d+$"))
    app.add_handler(CallbackQueryHandler(go_back_callback, pattern=r"^go_back_profile\|\d+$"))
    print("✅ Handlers registered.")

async def update_assets():
    try:
        from enkanetwork import EnkaNetworkAPI
        print("🔄 Updating assets...")
        async with EnkaNetworkAPI() as client:
            await client.update_assets(lang=["EN"])
        print("✅ Assets update complete.")
    except Exception as e:
        print(f"❌ Asset update failed: {e}")

async def main():
    TOKEN = "7610705253:AAGVc7Yy-uhBRAq3IESkbDxh4rdhVzZ6OHo"  # Replace with your bot token
    application = Application.builder().token(TOKEN).build()
    register_handlers(application)
    update_assets_env = os.getenv("UPDATE_ASSETS", "false").strip().lower()
    print(f"[DEBUG] UPDATE_ASSETS raw value: {repr(os.getenv('UPDATE_ASSETS'))}")
    if update_assets_env == "true":
        await update_assets()
    else:
        print("⚠️ Skipping asset update (set UPDATE_ASSETS=true to enable)")
    print("🚀 Bot starting...")
    await application.run_polling()

import nest_asyncio
if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        nest_asyncio.apply()
        loop.run_until_complete(main())
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
