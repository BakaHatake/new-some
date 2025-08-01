import os
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import enka
from enkacard.encbanner import ENC
import asyncio
user_uid_map = {}
user_template_settings = {}

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Use /myc to view your Genshin Impact profile.")

# === /myc ===
async def myc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_uid_map:
        await update.message.reply_text("🔢 You have not set your UID. Use /genshinlogin <uid>.")
        return
    uid = user_uid_map[user_id]
    await generate_profile_card(update, context, uid)

async def handle_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_uid"):
        return
    uid_text = update.message.text.strip()
    if not uid_text.isdigit():
        await update.message.reply_text("❌ Invalid UID. Use digits only.")
        return
    user_uid_map[update.effective_user.id] = uid_text
    context.user_data["awaiting_uid"] = False
    await update.message.reply_text(f"✅ UID set to {uid_text}. Fetching...")
    await generate_profile_card(update, context, uid_text)

# === /genshinlogin ===
async def genshinlogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /genshinlogin <numeric_uid>")
        return
    uid = context.args[0].strip()
    if not (8 <= len(uid) <= 10):
        await update.message.reply_text("UID length should be 8 to 10 digits.")
        return
    user_uid_map[update.effective_user.id] = uid
    await update.message.reply_text(f"✅ UID set to {uid}. Fetching profile...")
    await generate_profile_card(update, context, uid)

# === /template ===
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

    if user_id not in user_template_settings:
        user_template_settings[user_id] = {}

    user_template_settings[user_id][category] = choice
    await query.answer()
    await query.message.reply_text(f"✅ {category.capitalize()} template set to {choice}")

# === Profile Card with enkacard ===
async def generate_profile_card(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str):
    message = await update.message.reply_text("Fetching Enka data & generating card...")
    try:
        async with enka.GenshinClient(enka.gi.Language.ENGLISH) as client:
            response = await client.fetch_showcase(int(uid))
        player = response.player
        characters = response.characters
        if not characters:
            await message.edit_text("No characters found or profile is private.")
            return

        profile_tplt = user_template_settings.get(update.effective_user.id, {}).get("profile", 1)
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
            await update.message.reply_photo(f, caption=f"📋 UID {uid} Profile", reply_markup=reply_markup)
        os.remove(image_path)
        await message.delete()
    except Exception as e:
        await message.edit_text(f"Failed to fetch profile or generate card: {e}")

# === Character build card with enkacard ===
async def character_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    uid = user_uid_map.get(user_id)
    if not uid:
        await query.message.reply_text("UID not set. Please use /genshinlogin first.")
        return

    try:
        card_tplt = user_template_settings.get(user_id, {}).get("card", 1)
        async with ENC(uid=uid, lang="en") as encard:
            result = await encard.creat(template=card_tplt)
        # Get the character ID from the inline button callback
        char_id = int(query.data.split("_")[1])
        found = False
        for card_obj in result.card:
            if card_obj.id == char_id:
                found = True
                char_name = card_obj.name
                img = card_obj.card
                if img is None:
                    await query.message.reply_text(f"⚠️ No image found for {char_name}.")
                    return
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    image_path = tmp.name
                    img.save(image_path)
                with open(image_path, "rb") as f:
                    await query.message.reply_photo(f, caption=f"🔧 Build: {char_name}")
                os.remove(image_path)
                break
        if not found:
            await query.message.reply_text("⚠️ Character not found in your profile.")
    except Exception as e:
        await query.message.reply_text(f"Failed to generate character build: {e}")
from enkanetwork import EnkaNetworkAPI
async def update_assets() -> None:
    async with EnkaNetworkAPI() as client:
        await client.update_assets(lang=["EN"])
async def myc_handlers(application):
    """Register MYC handlers and update assets for the bot."""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myc", myc))
    application.add_handler(CommandHandler("genshinlogin", genshinlogin))
    application.add_handler(CommandHandler("template", template_menu))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_uid))
    application.add_handler(CallbackQueryHandler(character_callback, pattern=r"char_\d+"))
    application.add_handler(CallbackQueryHandler(profile_selector, pattern="choose_profile_template"))
    application.add_handler(CallbackQueryHandler(card_selector, pattern="choose_card_template"))
    application.add_handler(CallbackQueryHandler(store_choice, pattern=r"(profile|card)_\d+"))

    print("✅ MYC handlers registered.")

    print("Starting assets update...")
    await update_assets()
    print("Assets update complete.")
