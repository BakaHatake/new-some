import os
import tempfile
import asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import enka
from enkacard.encbanner import ENC
from enkanetwork import EnkaNetworkAPI
from pymongo import MongoClient

# --- MongoDB setup ---
MONGO_URI = "mongodb+srv://bakahatake:anush%40123@baka.f3g4xlx.mongodb.net/"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client['genshin']
profiles_col = db['profiles']  # stores user_id -> uid
templates_col = db['templates']  # stores user_id -> {"profile": X, "card": Y}

# --- DB helpers ---

def save_user_profile(user_id: int, uid: str):
    profiles_col.update_one({"user_id": user_id}, {"$set": {"uid": uid}}, upsert=True)

def delete_user_profile(user_id: int):
    profiles_col.delete_one({"user_id": user_id})

def get_user_profile(user_id: int):
    doc = profiles_col.find_one({"user_id": user_id})
    return doc["uid"] if doc else None

def save_user_template(user_id: int, category: str, choice: int):
    templates_col.update_one(
        {"user_id": user_id},
        {"$set": {category: choice}},
        upsert=True
    )

def get_user_template(user_id: int):
    doc = templates_col.find_one({"user_id": user_id})
    if doc:
        # Remove the MongoDB _id, user_id keys
        return {k: v for k, v in doc.items() if k not in ("_id", "user_id")}
    return {}

# === Telegram Bot Handlers ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use /genshinlogin <UID> to login your Genshin Impact account.\n"
        "Then use /myc to view your profile card."
    )

# --- /genshinlogin handler: fetch & preview with Save/Delete buttons ---

async def genshinlogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /genshinlogin <numeric_uid>")
        return
    uid = context.args[0].strip()
    if not (8 <= len(uid) <= 10):
        await update.message.reply_text("UID length should be 8 to 10 digits.")
        return

    # Send fetching message
    msg = await update.message.reply_text(f"🔄 Fetching profile for UID {uid}...")

    try:
        async with enka.GenshinClient(enka.gi.Language.ENGLISH) as client:
            response = await client.fetch_showcase(int(uid))
        characters = response.characters
        if not characters:
            await msg.edit_text("No characters found or profile is private.")
            return

        # Get user's saved templates if any
        user_templates = get_user_template(user_id)
        profile_tplt = user_templates.get("profile", 1)

        async with ENC(uid=uid, lang="en") as encard:
            profile = await encard.profile(card=True, teamplate=profile_tplt)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image_path = tmp.name
            profile.card.save(image_path)

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💾 Save UID", callback_data="save_uid"),
                 InlineKeyboardButton("🗑️ Delete UID", callback_data="delete_uid")]
            ]
        )

        with open(image_path, "rb") as f:
            await msg.delete()
            sent_msg = await update.message.reply_photo(
                f,
                caption=f"📋 Preview Profile Card for UID {uid}\n"
                        "Do you want to save this UID?",
                reply_markup=keyboard
            )
        os.remove(image_path)

        # Store temp data in user_data for save/delete callbacks
        context.user_data['temp_uid'] = uid
        context.user_data['preview_message_id'] = sent_msg.message_id

    except Exception as e:
        await msg.edit_text(f"Failed to fetch profile or generate card: {e}")

# Callbacks for Save/Delete buttons after /genshinlogin preview
async def save_or_delete_uid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    temp_uid = context.user_data.get('temp_uid')
    if not temp_uid:
        await query.message.edit_caption("Session expired or invalid. Please try /genshinlogin again.")
        return

    if data == "save_uid":
        save_user_profile(user_id, temp_uid)
        await query.message.edit_caption(f"✅ UID {temp_uid} saved successfully.")
    elif data == "delete_uid":
        delete_user_profile(user_id)
        await query.message.edit_caption(f"🗑️ UID {temp_uid} was not saved / deleted.")
    else:
        await query.message.edit_caption("Unknown action.")

    # Clean up temp data
    context.user_data.pop('temp_uid', None)
    context.user_data.pop('preview_message_id', None)

# --- /myc command — show profile card with inline character buttons ---

async def myc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    uid = get_user_profile(user_id)
    if not uid:
        await update.message.reply_text("🔢 You have not set your UID. Use /genshinlogin <uid>.")
        return

    # Send fetching message
    msg = await update.message.reply_text("🔄 Fetching your profile card...")

    try:
        await send_profile_card(uid, msg, user_id, context)

    except Exception as e:
        await msg.edit_text(f"Failed to fetch profile or generate card: {e}")

async def send_profile_card(uid: str, msg, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Fetch profile and edit `msg` with profile card and inline character buttons."""

    async with enka.GenshinClient(enka.gi.Language.ENGLISH) as client:
        response = await client.fetch_showcase(int(uid))

    characters = response.characters
    if not characters:
        await msg.edit_text("No characters found or profile is private.")
        return

    user_templates = get_user_template(user_id)
    profile_tplt = user_templates.get("profile", 1)

    async with ENC(uid=uid, lang="en") as encard:
        profile = await encard.profile(card=True, teamplate=profile_tplt)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        image_path = tmp.name
        profile.card.save(image_path)

    keyboard, row = [], []
    for idx, char in enumerate(characters[:12]):
        row.append(InlineKeyboardButton(char.name, callback_data=f"char_{char.id}"))
        if (idx + 1) % 4 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)

    with open(image_path, "rb") as f:
        media = InputMediaPhoto(f, caption=f"📋 UID {uid} Profile")
        await msg.delete()
        sent_msg = await context.bot.send_photo(
            chat_id=msg.chat_id,
            photo=f,
            caption=f"📋 UID {uid} Profile",
            reply_markup=reply_markup
        )
    os.remove(image_path)

    # Save message_id and uid in user_data for character card navigation
    context.user_data['last_profile_message_id'] = sent_msg.message_id
    context.user_data['uid'] = uid

# --- Character card callback: show character card with "Go Back" option ---

async def character_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    uid = get_user_profile(user_id)
    if not uid:
        await query.message.edit_text("UID not set. Please use /genshinlogin first.")
        return

    char_id = int(query.data.split("_")[1])

    # Retrieve message to edit
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    # Send "Fetching character card..." edit
    try:
        await query.message.edit_caption("🔄 Fetching character build card...")

        user_templates = get_user_template(user_id)
        card_tplt = user_templates.get("card", 1)

        async with ENC(uid=uid, lang="en") as encard:
            result = await encard.creat(template=card_tplt)

        found = False
        for card_obj in result.card:
            if card_obj.id == char_id:
                found = True
                char_name = card_obj.name
                img = card_obj.card
                if img is None:
                    await query.message.edit_caption(f"⚠️ No image found for {char_name}.")
                    return

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    image_path = tmp.name
                    img.save(image_path)

                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Go Back", callback_data="go_back_profile")]
                ])

                with open(image_path, "rb") as f:
                    # Edit media (photo + caption + buttons)
                    await query.message.edit_media(
                        media=InputMediaPhoto(f, caption=f"🔧 Build: {char_name}"),
                        reply_markup=keyboard
                    )
                os.remove(image_path)
                break
        if not found:
            await query.message.edit_caption("⚠️ Character not found in your profile.")

    except Exception as e:
        await query.message.edit_caption(f"Failed to generate character build: {e}")

# --- Go Back button callback ---

async def go_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    uid = get_user_profile(user_id)
    if not uid:
        await query.message.edit_text("UID not set. Please use /genshinlogin first.")
        return

    msg = query.message
    # Show "Fetching profile card..." message then re-render profile card in-place.
    try:
        await query.message.edit_caption("🔄 Regenerating profile card...")
        await send_profile_card(uid, msg, user_id, context)
    except Exception as e:
        await query.message.edit_caption(f"Failed to regenerate profile card: {e}")

# --- Templates customization handlers ---

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
    category = parts[0]  # 'profile' or 'card'
    try:
        choice = int(parts[1])
    except:
        await query.answer("Invalid selection")
        return

    save_user_template(user_id, category, choice)
    await query.answer()
    await query.message.reply_text(f"✅ {category.capitalize()} template set to {choice}")
# --- Register handlers ---
def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("genshinlogin", genshinlogin))
    app.add_handler(CommandHandler("myc", myc))
    app.add_handler(CommandHandler("template", template_menu))

    app.add_handler(CallbackQueryHandler(save_or_delete_uid_callback, pattern="^(save_uid|delete_uid)$"))
    app.add_handler(CallbackQueryHandler(profile_selector, pattern="choose_profile_template"))
    app.add_handler(CallbackQueryHandler(card_selector, pattern="choose_card_template"))
    app.add_handler(CallbackQueryHandler(store_choice, pattern=r"^(profile|card)_\d+$"))
    app.add_handler(CallbackQueryHandler(character_callback, pattern=r"^char_\d+$"))
    app.add_handler(CallbackQueryHandler(go_back_callback, pattern="go_back_profile"))

    print("✅ Handlers registered.")

async def update_assets():
    from enkanetwork import EnkaNetworkAPI
    print("Updating assets...")
    async with EnkaNetworkAPI() as client:
        await client.update_assets(lang=["EN"])
    print("Assets update complete.")

async def main():
    TOKEN = "7610705253:AAGVc7Yy-uhBRAq3IESkbDxh4rdhVzZ6OHo"   # Set your bot token here!
    application = Application.builder().token(TOKEN).build()
    register_handlers(application)
    await update_assets()    # If you don't need asset updates, you can skip or comment this line
    print("Bot starting...")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
