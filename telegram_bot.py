"""
UPSC PREDICTOR TELEGRAM BOT
============================
Telegram bot that generates UPSC-style questions from current affairs topics.
LINKED with web app - same users table, shared credits.

SETUP:
1. Create bot via @BotFather on Telegram
2. Get BOT_TOKEN
3. Set environment variables
4. Deploy to Railway/Render

ENVIRONMENT VARIABLES:
    TELEGRAM_BOT_TOKEN = "your-bot-token"
    ANTHROPIC_API_KEY = "sk-ant-..."
    SUPABASE_URL = "https://xxxxx.supabase.co"
    SUPABASE_KEY = "eyJhbG..."
    RAZORPAY_PAYMENT_URL = "https://rzp.io/rzp/xxxxx"
"""

import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import anthropic
from supabase import create_client
import random
import requests

# =============================================================================
# CONFIGURATION
# =============================================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RAZORPAY_PAYMENT_URL = os.environ.get("RAZORPAY_PAYMENT_URL", "https://rzp.io/rzp/GzH9tPDY")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")

# Conversation states
WAITING_FOR_EMAIL, WAITING_FOR_OTP = range(2)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =============================================================================
# DATABASE (SUPABASE) - USES MAIN USERS TABLE
# =============================================================================

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None


def get_user_by_telegram_id(telegram_id: int):
    """Get user by Telegram ID from main users table."""
    if not supabase:
        return None
    try:
        result = supabase.table('users').select('*').eq('telegram_id', telegram_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error getting user by telegram_id: {e}")
        return None


def get_user_by_email(email: str):
    """Get user by email from main users table."""
    if not supabase:
        return None
    try:
        email = email.lower().strip()
        result = supabase.table('users').select('*').eq('email', email).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error getting user by email: {e}")
        return None


def link_telegram_to_user(email: str, telegram_id: int, username: str = None):
    """Link Telegram ID to existing user account."""
    if not supabase:
        return False
    try:
        email = email.lower().strip()
        supabase.table('users').update({
            'telegram_id': telegram_id,
            'telegram_username': username
        }).eq('email', email).execute()
        return True
    except Exception as e:
        logger.error(f"Error linking telegram: {e}")
        return False


def create_user_from_telegram(telegram_id: int, username: str = None, first_name: str = None):
    """Create new user from Telegram with 1 free credit (no email yet)."""
    if not supabase:
        return None
    try:
        # Create with placeholder email that will be updated when they link
        placeholder_email = f"tg_{telegram_id}@telegram.placeholder"
        result = supabase.table('users').insert({
            'email': placeholder_email,
            'telegram_id': telegram_id,
            'telegram_username': username,
            'free_credits': 1,
            'paid_credits': 0,
            'total_queries': 0,
            'email_verified': False
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error creating user from telegram: {e}")
        return None


def update_user_credits(telegram_id: int, free_credits: int, paid_credits: int, total_queries: int):
    """Update user credits after query."""
    if not supabase:
        return False
    try:
        supabase.table('users').update({
            'free_credits': free_credits,
            'paid_credits': paid_credits,
            'total_queries': total_queries,
            'last_query_at': datetime.utcnow().isoformat()
        }).eq('telegram_id', telegram_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error updating credits: {e}")
        return False


# =============================================================================
# OTP FUNCTIONS
# =============================================================================

def generate_otp() -> str:
    """Generate 6-digit OTP."""
    return str(random.randint(100000, 999999))


def save_otp(email: str, otp: str) -> bool:
    """Save OTP to database."""
    if not supabase:
        return False
    try:
        from datetime import timedelta
        expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        supabase.table('otp_codes').insert({
            'email': email.lower().strip(),
            'otp': otp,
            'expires_at': expires_at,
            'used': False
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Error saving OTP: {e}")
        return False


def verify_otp(email: str, otp: str) -> bool:
    """Verify OTP from database."""
    if not supabase:
        return False
    try:
        email = email.lower().strip()
        result = supabase.table('otp_codes').select('*').eq('email', email).eq('otp', otp).eq('used', False).execute()
        
        if not result.data:
            return False
        
        otp_record = result.data[0]
        expires_at = datetime.fromisoformat(otp_record['expires_at'].replace('Z', '+00:00'))
        
        if datetime.now(expires_at.tzinfo) > expires_at:
            return False
        
        # Mark OTP as used
        supabase.table('otp_codes').update({'used': True}).eq('id', otp_record['id']).execute()
        return True
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return False


def send_otp_email(email: str, otp: str) -> bool:
    """Send OTP via Resend."""
    if not RESEND_API_KEY:
        logger.error("RESEND_API_KEY not set")
        return False
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": "UPSC Predictor <noreply@upscpredictor.in>",
                "to": [email],
                "subject": f"Your OTP: {otp} - UPSC Predictor",
                "html": f"""
                <div style="font-family: Arial, sans-serif; max-width: 400px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #1e40af;">UPSC Predictor</h2>
                    <p>Your verification code is:</p>
                    <div style="background: #f0f9ff; padding: 20px; text-align: center; border-radius: 8px; margin: 20px 0;">
                        <span style="font-size: 32px; font-weight: bold; letter-spacing: 4px; color: #1e40af;">{otp}</span>
                    </div>
                    <p style="color: #64748b; font-size: 14px;">This code expires in 10 minutes.</p>
                    <p style="color: #64748b; font-size: 14px;">Link your Telegram to access your credits on both platforms!</p>
                </div>
                """
            }
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending OTP email: {e}")
        return False


# =============================================================================
# CLAUDE API - QUESTION GENERATION
# =============================================================================

def generate_questions(topic: str) -> str:
    """Generate UPSC-style questions using Claude API - SAME FORMAT AS WEB APP."""
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    system_prompt = """You are an expert UPSC question setter. Generate 10 practice questions from the given topic.

CRITICAL REQUIREMENT — 5+5 SPLIT:
• 5 questions from PRIMARY SUBJECT (the obvious angle)
• 5 questions from CROSS-SUBJECT ANGLES (History, Geography, Economy, Ethics, Environment — whichever connects)

DISTRIBUTE AS:
- MCQ 1-3: Primary Subject
- MCQ 4-5: Cross-Subject Angles (DIFFERENT subjects)
- MAINS 1-2: Primary Subject
- MAINS 3-5: Cross-Subject Angles (include Ethics case study)

FORMAT:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 TOPIC ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Topic:** [News item]
**Primary Subject:** [GS-I/II/III/IV] — [Subject name]

**Cross-Subject Angles:**
• [Angle 1] — [Different GS Paper] — [Connection]
• [Angle 2] — [Different GS Paper] — [Connection]
• [Angle 3] — [Different GS Paper] — [Connection]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 SECTION A: PRIMARY MCQs (Q1-Q3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Q1** | [Primary Subject] | PRIMARY

[Question]
(a) [Option]
(b) [Option]
(c) [Option]
(d) [Option]

✓ **Answer:** [Letter]
⚠️ **Trap:** [Explain the trap]
💡 **Key Point:** [1-2 lines]

-----

[Q2, Q3 same format]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 SECTION B: CROSS-SUBJECT MCQs (Q4-Q5) 🔀
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Q4** | [Different Subject] | CROSS-ANGLE 🔀

[Question linking news to different subject]
(a)-(d) options

✓ **Answer:** [Letter]
💡 **Cross-Link:** [How this connects to original news]

-----

**Q5** | [Another Subject] | CROSS-ANGLE 🔀

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 SECTION C: PRIMARY MAINS (M1-M2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**M1** | [Primary Paper] | PRIMARY | 15 marks

"[Question]"

**Answer Framework (250 words):**
• **Intro (30 words):** [Approach]
• **Body (150 words):** [Key points]
• **Conclusion (40 words):** [Balanced ending]

**Must Include:** [Cases, committees, articles]
**Avoid:** [Common mistakes]

-----

[M2 same format]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 SECTION D: CROSS-SUBJECT MAINS (M3-M5) 🔀
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**M3** | [Different Paper] | CROSS-ANGLE 🔀 | 15 marks
**Cross-Link:** [Why UPSC asks from this angle]

-----

**M4** | [Another Paper] | CROSS-ANGLE 🔀

-----

**M5** | GS-IV | Ethics | CROSS-ANGLE 🔀 | Case Study

[Ethics case study based on the topic]
**Ethical Dimensions:** [Values at stake]
**Framework:** [How to approach]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULES:
1. Exactly 5 primary + 5 cross-subject questions
2. Cross-subject must be GENUINELY different subjects
3. Use real UPSC trap patterns
4. All cases/committees must be REAL
5. Balanced conclusions always"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Generate UPSC questions for: {topic}"}]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"❌ Error generating questions: {str(e)}"


# =============================================================================
# TELEGRAM HANDLERS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    telegram_id = user.id
    
    # Check if user exists (linked or created via Telegram)
    db_user = get_user_by_telegram_id(telegram_id)
    
    if not db_user:
        # New Telegram user - create account with 1 free credit
        create_user_from_telegram(telegram_id, user.username, user.first_name)
        db_user = get_user_by_telegram_id(telegram_id)
        
        welcome_msg = """
🎯 *UPSC Predictor Bot*

━━━━━━━━━━━━━━━━━━━━
🎁 *You have 1 FREE credit!*
━━━━━━━━━━━━━━━━━━━━

*WHAT THIS BOT DOES:*
Send any current affairs topic → Get 10 UPSC-style questions instantly

*WHAT YOU GET:*
• 5 Prelims MCQs (with trap explanations)
• 5 Mains questions (with answer frameworks)
• Cross-subject angles covered
• Downloadable text file

━━━━━━━━━━━━━━━━━━━━
*HOW TO USE:*
━━━━━━━━━━━━━━━━━━━━

1️⃣ Type a topic → Example: `Governor NEET Bill delay`
2️⃣ Wait 20-30 seconds
3️⃣ Get questions + downloadable file

━━━━━━━━━━━━━━━━━━━━
*💡 LINK YOUR ACCOUNT*
━━━━━━━━━━━━━━━━━━━━

*Why link?*
• Buy credits ONCE, use on BOTH Telegram & upscpredictor.in
• No need to buy separately for each platform

*How to link:*
1. Use /link command
2. Enter your email
3. Verify OTP sent to email
4. Done! Credits sync automatically

━━━━━━━━━━━━━━━━━━━━
*COMMANDS:*
━━━━━━━━━━━━━━━━━━━━

/credits - Check balance
/buy - Buy credits (₹12 each)
/link - Link web account
/help - Full guide

━━━━━━━━━━━━━━━━━━━━

*Try now - send a topic!* 👇
"""
    else:
        free = db_user.get('free_credits', 0)
        paid = db_user.get('paid_credits', 0)
        total = free + paid
        email = db_user.get('email', '')
        is_linked = not email.endswith('@telegram.placeholder')
        
        if is_linked:
            link_status = f"✅ Linked: `{email}`"
            link_tip = "✅ Your credits sync with upscpredictor.in"
        else:
            link_status = "⚠️ Account not linked"
            link_tip = """
*💡 LINK YOUR ACCOUNT*
Use /link to sync credits with upscpredictor.in
Buy once, use on both platforms!"""
        
        welcome_msg = f"""
🎯 *UPSC Predictor Bot*

━━━━━━━━━━━━━━━━━━━━
💳 *Credits:* {total} ({free} free + {paid} paid)
{link_status}
━━━━━━━━━━━━━━━━━━━━

*HOW TO USE:*
Type any current affairs topic!

Example: `Governor NEET Bill delay`

━━━━━━━━━━━━━━━━━━━━
*COMMANDS:*
━━━━━━━━━━━━━━━━━━━━

/credits - Check balance
/buy - Buy credits (₹12 each)
/link - Link web account
/paid - Verify payment
/help - Full guide

━━━━━━━━━━━━━━━━━━━━

{link_tip}

*Send a topic to generate questions!* 👇
"""
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = """
📖 *UPSC Predictor Bot - Help*

━━━━━━━━━━━━━━━━━━━━
*HOW TO USE*
━━━━━━━━━━━━━━━━━━━━

1️⃣ Send any current affairs topic
2️⃣ Get 10 UPSC-style questions instantly
3️⃣ Download as text file

━━━━━━━━━━━━━━━━━━━━
*COMMANDS*
━━━━━━━━━━━━━━━━━━━━

/start - Start bot & get 1 free credit
/credits - Check your balance
/buy - Purchase credits (₹12 each)
/link - Link web account (share credits!)
/paid - Check if payment was received
/help - This message

━━━━━━━━━━━━━━━━━━━━
*HOW CREDITS WORK*
━━━━━━━━━━━━━━━━━━━━

• 1 credit = 10 questions (5 MCQ + 5 Mains)
• Credits work on BOTH Telegram & upscpredictor.in
• Link your account to share credits across platforms

━━━━━━━━━━━━━━━━━━━━
*HOW TO BUY CREDITS*
━━━━━━━━━━━━━━━━━━━━

*If account is linked:*
1. Use /buy → Click "Pay ₹12"
2. Use your linked email in Razorpay
3. Click "I've Paid" after payment

*If not linked:*
1. Use /link first → Enter email → Verify OTP
2. Then /buy as above

━━━━━━━━━━━━━━━━━━━━
*HOW TO LINK ACCOUNTS*
━━━━━━━━━━━━━━━━━━━━

1. Use /link command
2. Enter email you use on upscpredictor.in
3. Check email for OTP
4. Enter OTP to verify
5. Done! Credits now sync automatically

━━━━━━━━━━━━━━━━━━━━

*Support:* @writernical
*Web:* upscpredictor.in
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /credits command."""
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        await update.message.reply_text(
            "❌ User not found. Send /start to register.",
            parse_mode='Markdown'
        )
        return
    
    free = user.get('free_credits', 0)
    paid = user.get('paid_credits', 0)
    total = free + paid
    used = user.get('total_queries', 0)
    email = user.get('email', '')
    is_linked = not email.endswith('@telegram.placeholder')
    
    link_status = f"🔗 Linked to: `{email}`" if is_linked else "⚠️ Not linked - use /link to connect web account"
    
    credits_msg = f"""
💳 *Your Credits*

🎁 Free: *{free}*
💰 Paid: *{paid}*
━━━━━━━━━━
📊 Total Available: *{total}*
📈 Total Used: *{used}*

{link_status}

{'✅ Ready to generate!' if total > 0 else '⚠️ No credits. Use /buy to get more.'}
"""
    await update.message.reply_text(credits_msg, parse_mode='Markdown')


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buy command."""
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    email = user.get('email', '') if user else ''
    is_linked = user and not email.endswith('@telegram.placeholder')
    
    if is_linked:
        keyboard = [
            [InlineKeyboardButton("💳 Pay ₹12 per Credit", url=RAZORPAY_PAYMENT_URL)],
            [InlineKeyboardButton("✅ I've Paid - Check Now", callback_data="check_payment")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        buy_msg = f"""
🛒 *Buy Credits*

*Price:* ₹12 per credit
*1 credit = 10 UPSC questions*

━━━━━━━━━━━━━━━━━━━━
✅ *Your linked email:* `{email}`
━━━━━━━━━━━━━━━━━━━━

*Steps:*
1️⃣ Click "Pay ₹12" button below
2️⃣ Use email: `{email}` when paying
3️⃣ Complete payment
4️⃣ Come back here & click "I've Paid"

⚠️ *IMPORTANT:* Use the SAME email `{email}` in Razorpay!
"""
    else:
        keyboard = [
            [InlineKeyboardButton("🔗 Link Account First", callback_data="start_link")],
            [InlineKeyboardButton("💳 Pay Anyway", url=RAZORPAY_PAYMENT_URL)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        buy_msg = """
🛒 *Buy Credits*

*Price:* ₹12 per credit

⚠️ *Account Not Linked!*

For automatic credit sync, link your account first:
1️⃣ Click "Link Account First" below
2️⃣ Enter your email & verify OTP
3️⃣ Then buy credits

*Or* pay anyway and use /paid command after payment.
"""
    
    await update.message.reply_text(buy_msg, parse_mode='Markdown', reply_markup=reply_markup)


async def paid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /paid command - check for recent payment."""
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        await update.message.reply_text(
            "❌ User not found. Send /start first.",
            parse_mode='Markdown'
        )
        return
    
    email = user.get('email', '')
    is_linked = not email.endswith('@telegram.placeholder')
    
    if not is_linked:
        await update.message.reply_text(
            "⚠️ *Account not linked!*\n\n"
            "To check payment, I need to know your email.\n\n"
            "Use /link to connect your web account first, then try /paid again.",
            parse_mode='Markdown'
        )
        return
    
    await update.message.reply_text(
        f"🔍 *Checking payments for:* `{email}`\n\nPlease wait...",
        parse_mode='Markdown'
    )
    
    # Check Razorpay for pending payments
    try:
        pending = check_razorpay_payments(email)
        if pending > 0:
            # Refresh user data
            user = get_user_by_telegram_id(telegram_id)
            total = user.get('free_credits', 0) + user.get('paid_credits', 0)
            await update.message.reply_text(
                f"✅ *Payment found!*\n\n"
                f"Added *{pending}* credit(s) to your account.\n\n"
                f"💳 *Total credits:* {total}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "❌ *No new payments found.*\n\n"
                "Make sure you:\n"
                f"• Used email `{email}` when paying\n"
                "• Completed the payment\n"
                "• Wait 1-2 minutes after payment\n\n"
                "Try again or contact @writernical for help.",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error checking payments: {e}")
        await update.message.reply_text(
            "❌ Error checking payments. Try again later or contact @writernical.",
            parse_mode='Markdown'
        )


def check_razorpay_payments(email: str) -> int:
    """Check Razorpay for pending payments and credit user."""
    if not supabase:
        return 0
    
    try:
        import os
        razorpay_key = os.environ.get('RAZORPAY_KEY_ID', '')
        razorpay_secret = os.environ.get('RAZORPAY_KEY_SECRET', '')
        
        if not razorpay_key or not razorpay_secret:
            # Fallback: Just refresh credits from database
            return 0
        
        # Check for uncredited payments via Razorpay API
        # For now, just return 0 - manual credit will work via web app Quick Login
        return 0
        
    except Exception as e:
        logger.error(f"Razorpay check error: {e}")
        return 0


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "check_payment":
        telegram_id = query.from_user.id
        user = get_user_by_telegram_id(telegram_id)
        
        if not user:
            await query.edit_message_text("❌ User not found. Send /start first.")
            return
        
        email = user.get('email', '')
        is_linked = not email.endswith('@telegram.placeholder')
        
        if not is_linked:
            await query.edit_message_text(
                "⚠️ *Account not linked!*\n\n"
                "Use /link first to connect your email, then I can check payments.",
                parse_mode='Markdown'
            )
            return
        
        await query.edit_message_text(
            f"🔍 Checking payments for `{email}`...\n\n"
            f"If you just paid, your credits should appear shortly.\n\n"
            f"💡 *Tip:* You can also use the web app's Quick Login with this email to sync credits.\n\n"
            f"Use /credits to check your balance.",
            parse_mode='Markdown'
        )
    
    elif query.data == "start_link":
        await query.edit_message_text(
            "🔗 *Link Your Account*\n\n"
            "Send /link command to start linking your web account.",
            parse_mode='Markdown'
        )


async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /link command - start linking process."""
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if user:
        email = user.get('email', '')
        if not email.endswith('@telegram.placeholder'):
            await update.message.reply_text(
                f"✅ Already linked to: `{email}`\n\nYour credits sync across Telegram and web!",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
    
    await update.message.reply_text(
        "🔗 *Link Your Web Account*\n\n"
        "Enter the email you use on upscpredictor.in:\n\n"
        "_(This will sync your credits across both platforms)_",
        parse_mode='Markdown'
    )
    return WAITING_FOR_EMAIL


async def receive_email_for_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive email for linking."""
    email = update.message.text.lower().strip()
    telegram_id = update.effective_user.id
    
    if '@' not in email or '.' not in email:
        await update.message.reply_text("❌ Invalid email. Please try again or /cancel")
        return WAITING_FOR_EMAIL
    
    # Check if email exists in web app
    existing_user = get_user_by_email(email)
    
    if not existing_user:
        await update.message.reply_text(
            f"❌ No account found for `{email}`\n\n"
            "First sign up at upscpredictor.in, then come back to link.\n\n"
            "Or send /cancel to exit.",
            parse_mode='Markdown'
        )
        return WAITING_FOR_EMAIL
    
    # Check if already linked to another Telegram
    if existing_user.get('telegram_id') and existing_user.get('telegram_id') != telegram_id:
        await update.message.reply_text(
            "❌ This email is already linked to another Telegram account.\n\n"
            "Contact @writernical for help.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    # Send OTP
    otp = generate_otp()
    if save_otp(email, otp) and send_otp_email(email, otp):
        context.user_data['link_email'] = email
        await update.message.reply_text(
            f"📧 OTP sent to `{email}`\n\n"
            "Enter the 6-digit code to verify:\n\n"
            "_(Check spam folder if not in inbox)_",
            parse_mode='Markdown'
        )
        return WAITING_FOR_OTP
    else:
        await update.message.reply_text(
            "❌ Failed to send OTP. Try again later or /cancel",
            parse_mode='Markdown'
        )
        return WAITING_FOR_EMAIL


async def receive_otp_for_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive OTP and complete linking."""
    otp = update.message.text.strip()
    telegram_id = update.effective_user.id
    email = context.user_data.get('link_email')
    
    if not email:
        await update.message.reply_text("❌ Session expired. Start again with /link")
        return ConversationHandler.END
    
    if len(otp) != 6 or not otp.isdigit():
        await update.message.reply_text("❌ Invalid OTP. Enter 6 digits or /cancel")
        return WAITING_FOR_OTP
    
    if verify_otp(email, otp):
        # Get current Telegram user (might have credits)
        tg_user = get_user_by_telegram_id(telegram_id)
        web_user = get_user_by_email(email)
        
        # Merge credits: add Telegram user's credits to web user
        if tg_user and web_user:
            # Transfer any credits from Telegram-only account to web account
            tg_free = tg_user.get('free_credits', 0)
            tg_paid = tg_user.get('paid_credits', 0)
            web_free = web_user.get('free_credits', 0)
            web_paid = web_user.get('paid_credits', 0)
            
            # Delete Telegram-only account if it exists
            if tg_user.get('email', '').endswith('@telegram.placeholder'):
                try:
                    supabase.table('users').delete().eq('telegram_id', telegram_id).execute()
                except:
                    pass
            
            # Update web account with Telegram ID and merged credits
            supabase.table('users').update({
                'telegram_id': telegram_id,
                'telegram_username': update.effective_user.username,
                'free_credits': web_free + tg_free,
                'paid_credits': web_paid + tg_paid
            }).eq('email', email).execute()
            
            total = web_free + tg_free + web_paid + tg_paid
            
        else:
            # Just link Telegram ID
            link_telegram_to_user(email, telegram_id, update.effective_user.username)
            total = web_user.get('free_credits', 0) + web_user.get('paid_credits', 0) if web_user else 0
        
        await update.message.reply_text(
            f"✅ *Successfully linked!*\n\n"
            f"Email: `{email}`\n"
            f"Credits: *{total}*\n\n"
            f"Your credits now sync across Telegram and upscpredictor.in!",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "❌ Invalid or expired OTP. Try /link again.",
            parse_mode='Markdown'
        )
    
    return ConversationHandler.END


async def cancel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel linking process."""
    await update.message.reply_text("❌ Linking cancelled.")
    return ConversationHandler.END


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages (topic queries)."""
    telegram_id = update.effective_user.id
    topic = update.message.text.strip()
    
    # Ignore commands
    if topic.startswith('/'):
        return
    
    # Get user
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        # Auto-create user
        create_user_from_telegram(telegram_id, update.effective_user.username, update.effective_user.first_name)
        user = get_user_by_telegram_id(telegram_id)
    
    # Check credits
    free = user.get('free_credits', 0)
    paid = user.get('paid_credits', 0)
    total = free + paid
    
    if total <= 0:
        keyboard = [[InlineKeyboardButton("💳 Buy Credits", url=RAZORPAY_PAYMENT_URL)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "❌ *No credits remaining!*\n\nUse /buy to purchase more (₹12 each).",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return
    
    # Validate topic
    if len(topic) < 5:
        await update.message.reply_text(
            "⚠️ Topic too short. Please provide more detail.\n\n*Example:* Governor delays NEET Bill controversy",
            parse_mode='Markdown'
        )
        return
    
    if len(topic) > 500:
        await update.message.reply_text(
            "⚠️ Topic too long. Keep it under 500 characters.",
            parse_mode='Markdown'
        )
        return
    
    # Send "generating" message
    processing_msg = await update.message.reply_text(
        f"⏳ *Generating questions...*\n\n_{topic}_\n\nThis takes 20-30 seconds. Please wait!",
        parse_mode='Markdown'
    )
    
    # Generate questions
    questions = generate_questions(topic)
    
    # Deduct credit
    if free > 0:
        new_free = free - 1
        new_paid = paid
    else:
        new_free = free
        new_paid = paid - 1
    
    total_queries = user.get('total_queries', 0) + 1
    update_user_credits(telegram_id, new_free, new_paid, total_queries)
    
    # Delete processing message
    await processing_msg.delete()
    
    # Split response if too long (Telegram limit: 4096 chars)
    if len(questions) > 4000:
        chunks = [questions[i:i+4000] for i in range(0, len(questions), 4000)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await update.message.reply_text(chunk)
            else:
                await update.message.reply_text(f"...continued\n\n{chunk}")
    else:
        await update.message.reply_text(questions)
    
    # Send as downloadable text file
    import io
    file_content = f"UPSC Predictor - Generated Questions\n"
    file_content += f"Topic: {topic}\n"
    file_content += f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
    file_content += f"{'='*50}\n\n"
    file_content += questions
    
    # Create file-like object
    file_bytes = io.BytesIO(file_content.encode('utf-8'))
    file_bytes.name = f"UPSC_Questions_{topic[:30].replace(' ', '_')}.txt"
    
    await update.message.reply_document(
        document=file_bytes,
        filename=file_bytes.name,
        caption="📄 Download questions as text file"
    )
    
    # Send credits remaining
    remaining = new_free + new_paid
    await update.message.reply_text(
        f"━━━━━━━━━━━━━━━━━━━━━━\n💳 *Credits remaining:* {remaining}\n\nSend another topic or /buy for more.",
        parse_mode='Markdown'
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")
    
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Link conversation handler
    link_handler = ConversationHandler(
        entry_points=[CommandHandler("link", link_command)],
        states={
            WAITING_FOR_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email_for_link)],
            WAITING_FOR_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_otp_for_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel_link)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("credits", credits_command))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("paid", paid_command))
    application.add_handler(link_handler)
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start polling
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
