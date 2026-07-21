import ast
import asyncio
import base64
import datetime
import operator
import io
import json
import logging
import os
import random
import re
import signal
import threading
import time
import unicodedata
from collections import defaultdict, deque

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from word_game_data import DEAD_END_WORDS, RESPONSE_MAP, START_PHRASES

# ==================== CONFIG ====================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "1191954573200457758"))
GIRLFRIEND_ID = int(os.getenv("GIRLFRIEND_ID", "1197183310342914150"))


# AI: Gemini API với xoay nhiều key. Hết sạch key thì bot im, không trả lời.
# Đặt trong .env: GEMINI_API_KEY=..., GEMINI_API_KEY2=..., GEMINI_API_KEY3=... (thêm bao nhiêu cũng được).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "60"))
GEMINI_KEY_COOLDOWN = int(os.getenv("GEMINI_KEY_COOLDOWN", "60"))  # giây nghỉ 1 key sau khi dính 429/quota


def _load_gemini_keys():
    """Gom GEMINI_API_KEY, GEMINI_API_KEY1..30 từ .env, bỏ trùng, giữ thứ tự."""
    keys, seen = [], set()
    for name in ["GEMINI_API_KEY"] + [f"GEMINI_API_KEY{i}" for i in range(1, 31)]:
        val = (os.getenv(name) or "").strip()
        if val and val not in seen:
            seen.add(val)
            keys.append(val)
    return keys


GEMINI_KEYS = _load_gemini_keys()

# z.ai GLM: AI RIÊNG cho CHỦ BOT chat (không đụng quota Gemini của mọi người).
# Mọi người dùng Gemini; hết key Gemini là họ hết dùng, còn chủ bot vẫn chạy bằng GLM.
ZAI_API_KEY = (os.getenv("ZAI_API_KEY") or "").strip()
ZAI_MODEL = (os.getenv("ZAI_MODEL") or "glm-5.2").strip() or "glm-5.2"
ZAI_API_URL = "https://api.z.ai/api/paas/v4/chat/completions"

MODEL = GEMINI_MODEL
ROAST_MODEL = os.getenv("ROAST_MODEL", GEMINI_MODEL)
MAX_PROMPT_CHARS = 3000
MAX_FILE_BYTES = 20 * 1024
COOLDOWN_SECONDS = 0.5
CHANNEL_COOLDOWN_SECONDS = 0.5
MEMORY_MSGS = 60  # 30 luot user + 30 luot bot
CHUNK_SIZE = 1900  # gioi han Discord 2000 ky tu/tin nhan
CODE_MAX_TOKENS = 4096
CHAT_MAX_TOKENS = 600
CODE_THINKING_BUDGET = 4096  # chi code mode moi bat thinking; chat thuong tat de tra loi nhanh ~2s
OWNER_THINKING_BUDGET = 4096
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_EXT = (".txt", ".py", ".js", ".json", ".lua", ".md")
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
GAME_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_data.json")
UNKNOWN_WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unknown_word_phrases.json")
WORD_GAME_TIMEOUT_SECONDS = 5 * 60
WORD_GAME_TURN_SECONDS = 10
WORD_GAME_LOG_CHANNEL_ID = int(os.getenv("WORD_GAME_LOG_CHANNEL_ID", "0") or 0)
WORD_GAME_LOG_CHANNEL_NAME = os.getenv("WORD_GAME_LOG_CHANNEL_NAME", "nối-từ").strip() or "nối-từ"
# Host redeploy là mất file local, nên giữ 1 tin DM chứa backup và edit tại chỗ.
GAME_BACKUP_INTERVAL_SECONDS = 5 * 60
GAME_BACKUP_FILENAME = "game_data_backup.json"
UNKNOWN_BACKUP_FILENAME = "unknown_words_backup.json"
# Update/redeploy: báo bảo trì, hoàn tiền cược ván dở, chờ instance cũ backup xong mới khôi phục.
WORD_SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "word_game_sessions.json")
SESSIONS_BACKUP_FILENAME = "word_sessions_backup.json"
LEARNED_BACKUP_FILENAME = "learned_words_backup.json"
OWNER_FEEDBACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "owner_feedback.json")
OWNER_FEEDBACK_BACKUP_FILENAME = "owner_feedback_backup.json"
# Kênh nhận file từ điển thô sau mỗi ván + tổng hợp gomtu/gheptu.
WORD_LIST_CHANNEL_ID = int(os.getenv("WORD_LIST_CHANNEL_ID", "1525141086371188867") or 0)
WORD_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "word_stats.json")
WORD_STATS_BACKUP_FILENAME = "word_stats_backup.json"
MAINTENANCE_RESTORE_DELAY_SECONDS = 20
# Emoji feedback nối từ, chỉ chủ bot: bấm trên câu bot nối = từ đó sai;
# bấm trên câu chấm sai/bí từ = muốn dạy từ đúng.
FEEDBACK_EMOJI = "📝"
# Bấm ☠️ trên câu bot nối = CHỮ CUỐI của cụm đó là TỪ CHẾT (không có đường nối):
# từ đó về sau, ván nào bot dồn người chơi tới từ này là người chơi thua luôn.
DEADWORD_EMOJI = "☠️"
# Ván NGƯỜI KHÁC chơi: ❌ dưới câu người chơi + câu bot; chủ bot bấm = cấm cụm đó vĩnh viễn.
DELETE_EMOJI = "❌"
# ✅ = xác minh cụm là ĐÚNG: lần sau cụm đó chỉ hiện 🔒 (đã xác minh), khỏi kiểm lại.
VERIFY_EMOJI = "✅"
# 🔒 = cụm đã xác minh; bấm để BỎ xác minh (sửa lại) nếu thấy có vấn đề.
VERIFIED_MARK_EMOJI = "🔒"
FEEDBACK_EXPIRE_SECONDS = 6 * 60 * 60  # tin không xóa nữa nên cho bấm emoji lâu (tới khi bot restart)
WORD_GAME_MAX_STRIKES = 4
# 0️⃣ 1️⃣ ... 9️⃣ 🔟, index = số giây còn lại
WORD_GAME_START_BALANCE = 10_000
WORD_GAME_MAX_BET = 250_000  # tối đa mỗi ván đặt được
WORD_GAME_MAX_AI_USED = 80
# Kinh tế: điểm danh hằng ngày, vay tiền, nợ đỏ.
DAILY_REWARD_BASE = 5_000
DAILY_STREAK_BONUS = 1_000       # mỗi ngày streak +1k, tối đa 7 ngày
DAILY_STREAK_MAX = 7
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60
DAILY_STREAK_RESET_SECONDS = 48 * 60 * 60  # quá 2 ngày không điểm danh là mất streak
LOAN_MAX_DEBT = 100_000          # trần nợ tối đa
WORD_GAME_BLOCKED_OPENING_WORDS = {"bánh"}
FAIR_WORD_GAME_STARTS = (
    "ăn cơm", "đi học", "chơi cờ", "làm việc", "uống nước", "đọc sách",
    "nghe nhạc", "xem phim", "nấu ăn", "mua đồ", "học bài", "vẽ tranh",
    "chạy bộ", "ngủ trưa", "nói chuyện", "mở cửa", "trồng cây", "nuôi mèo",
    "ăn sáng", "đọc truyện", "xây nhà", "bán hàng", "làm bánh", "uống sữa",
    "đi chơi", "vào lớp", "học nhóm", "chơi bóng", "trồng rau", "nuôi chó",
    "đổi tên", "cầm bút", "bắt đầu", "thả tim", "pha màu", "đi ngủ",
    "chơi nhạc", "uống trà", "đọc báo", "nghe tin", "vẽ hình", "nói thật",
    "trồng hoa", "nuôi cá", "ra đường", "thắng trận", "vui vẻ",
)
# Hậu tố lẻ, chỉ dùng khi một key không còn lựa chọn tự nhiên nào khác.
WORD_GAME_FILLER_WORDS = {
    "loi",
}
WORD_GAME_ALWAYS_VALID = {
    "ảnh nét", "ngọt lịm", "người ngợm", "nhiếc móc", "túi da", "móc túi", "hình ảnh",
    "chụp hình", "đây đó", "nay mai", "nay mưa", "nay nắng",
    "cụ thể", "thể hiện", "hiện nay", "trưa nay", "mai đi", "đi ngủ",
    "ngủ gật", "gật gù", "gù lưng", "lưng áo", "áo khoác",
    "bài gửi", "hoàng tử", "khát vọng", "thi công", "tiền tệ", "trai xinh",
    "tệ nạn", "điền kinh", "đói khát", "đạc điền", "đẹp đẽ", "đồ đạc",
    "bánh pía", "pía chay", "chay trường", "chay tịnh",
    "bào ngư", "cật lực", "dầm mưa", "lưới trời", "lưỡng lự", "lực lưỡng",
    "mạng lưới", "ngư dân", "nước đái", "tim cật", "trời đất", "đá bào",
}
WORD_GAME_ALWAYS_INVALID = {
    "ngợm nhiếc", "đạc đồ", "hài bài", "lịm người", "ambient kính",
    "vong co", "phó trạng",
    "chừng nhí", "chừng núi", "cũng theo", "dòng phổ", "kìa kìa",
    "mẻ nồi", "mẻ nếp", "nay đây", "trưa trực", "mai đó", "gù đầu",
    "hòang tử", "đẽ gọt", "nhóm sản", "trận w",
    "dầm thấm", "lự là", "lự một", "lự tình", "lự điều", "rãi rác",
    # Tên riêng/địa danh không tính trong nối từ.
    "lạc long", "thoại mỹ", "mạch khê",
    # Rác sinh tự động và cụm ghép gượng.
    "ty con", "ty mẹ", "ty lớn", "mạng xã", "ngoằng ngoăng", "ngoằng ngoằng",
    # Cụm rác từ đợt test 2026-07-05.
    "nhàng nhàng", "nhàng hạ", "nhàng rỗi", "hòe hòe", "mắm mè",
    "ngợm nghĩnh", "ngợm nghệch", "nhở việc", "nhở vuệc", "nàn nỉ",
    "này thì", "queo queo", "queo ruốc", "sủa nhặng", "đán từ",
    "xề la", "xề là", "xề và", "xề xề",
    # Cụm rác/typo/filler từ đợt test 2026-07-07.
    "cộn sóng", "cộn tin", "dã dang", "dã thua", "gỏng gáy", "gỏng gông",
    "lè lè", "mà bạn", "mà ma", "mà này", "nay kia", "nay này", "nay nọ",
    "ngạt ngào", "nhẹt nhẹt", "phào phèo", "phào pháo", "báo daid",
    "đồ đung", "game gủng", "con r",
    # Cụm lặp từ khác thanh, nối vẹt (cũng bị is_tone_reduplication chặn).
    "quèo queo", "queo quèo", "xoe xòe", "xòe xoe", "chăng chối",
    # Cụm bịa để lách bẫy (2026-07-09).
    "tợn của", "tợn tệ", "tợn ghê", "tợn cướp", "tợn kìa",
    # Bảng cụm rác từ ban xuat 2026-07-09: vocative/tiếng Anh/ghép vô nghĩa.
    "chẽ giữa", "cong méo", "cưng ơi", "duo collection", "gũi tre",
    "highpoly của", "hòe nhai", "kìa bạn", "lét vẽ", "mó to", "mẫn nhi",
    "mẻ nhúng", "nhé bạn", "nữa mà", "offline game", "offline luôn",
    "ren cửa", "thãi ra", "tiện mồn", "tắn kìa", "xóm nhái", "chơi nhau",
    "chết cụ", "xịn đét", "nhè cơm",
}
# Cụm nghe gượng: người chơi nói thì tha, nhưng bot không được tự ra.
WORD_GAME_BOT_AVOID_PHRASES = {
    "chì lưới", "thép nguội", "nát đời",
}
# Từ đuôi gần như không có đường nối chuẩn: nước gài chết, bot ƯU TIÊN ra để ép thua.
WORD_GAME_KILL_WORDS = {
    # Từ TUYỆT ĐƯỜNG NỐI thật sự mà bot RA được bằng cụm HỢP LỆ (dài ngoằng, giòn rụm,
    # lưỡng lự, táo tợn). Đã bỏ khè/quèo/oạch... vì chỉ ra được bằng cụm rác (đã dọn).
    "ngoằng", "lự", "rụm", "tợn",
}
# Cụm chứa từ tục/nhạy cảm không được tính lượt, cả phía người chơi lẫn bot.
WORD_GAME_BANNED_WORDS = {
    "lồn", "loz", "cặc", "cak", "buồi", "đụ", "địt", "đéo", "đĩ", "điếm",
    "cứt", "cức", "sex", "porn",
}
# Từ TV bình thường nhưng chủ bot cấm chơi trong game (dễ bị lách/ép bí): cả bot lẫn
# người chơi đều KHÔNG được ghép cụm nào chứa từ này; từ điển cũng bị lọc bỏ hết.
WORD_GAME_FORBIDDEN_WORDS = {
    "rồi",
}
# Nối từ CHỈ tiếng Việt: từ tiếng Anh không tính (f/j/w/z không có trong tiếng Việt +
# danh sách English hay gặp). "ban" là tiếng Việt (ban ngày, ban nhạc) nên KHÔNG nằm đây.
WORD_GAME_ENGLISH_WORDS = {
    "acc", "ads", "anti", "api", "app", "bait", "bake", "beta", "blender", "boss", "buff",
    "bug", "camera", "caption", "clip", "cloud", "code", "cola", "collection", "combat", "combo",
    "cpu", "crash", "css", "dame", "data", "demo", "discord", "driver", "drop", "duo", "export",
    "game", "gpu", "grind", "gui", "heal", "highpoly", "host", "hot", "html", "import", "jpg",
    "js", "key", "kick", "kill", "lag", "laptop", "layout", "leave", "led", "level", "like",
    "link", "lite", "loot", "lowpoly", "lua", "manual", "map", "max", "meme", "meta", "min",
    "mob", "mocap", "mod", "model", "monster", "mp3", "mp4", "mute", "net", "noob", "npc",
    "online", "pack", "patch", "pc", "pdf", "pepsi", "photo", "ping", "plus", "png", "pop",
    "private", "pro", "public", "python", "pve", "pvp", "ram", "real", "realistic", "render",
    "reset", "rig", "roblox", "script", "server", "shader", "silent", "skill", "skin", "soda",
    "spam", "stable", "sting", "stream", "studio", "sub", "tab", "team", "tele", "test", "texture",
    "timeout", "tivi", "trend", "troll", "tryhard", "usb", "video", "vietsub", "vip", "vpn",
    "wr", "gg", "sql", "gif",
}

ACCOUNT_CONTEXT_BLOCKLIST = {
    "google", "gmail", "youtube", "yt", "facebook", "fb", "tiktok",
    "discord", "roblox", "steam", "epic", "riot", "valorant", "lol",
    "garena", "claude", "chatgpt", "openai", "gemini", "anthropic",
    "github", "gitlab", "twitter", "x", "instagram", "ig", "capcut",
    "canva", "paypal", "mbbank", "mb bank", "agribank", "momo",
    "zalopay", "shopee", "lazada",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("zun")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(
    command_prefix="?",
    intents=intents,
    help_command=None,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
)

START_TIME = time.time()

# ==================== PERSONA ====================
BASE_PERSONA = """Mày là Zun, bot Discord người Việt, nói như bạn thân trong server.
Xưng "t", gọi user là "m".
Mặc định trả lời cực ngắn: 1 câu là chính. User tâm sự, kể chuyện hoặc hỏi sâu thì được 2-3 câu. Quy tắc ngắn này không áp dụng khi đang ở code mode.
Ít dấu câu, hạn chế dấu chấm và dấu phẩy. Ưu tiên kiểu chat Discord tự nhiên như: rồi sao, ảo thật, nghe ngu mà hợp lý, đưa log đây, m lại bắt đầu r.
Không văn mẫu, không khách sáo kiểu trợ lý, không giảng đạo lý, không spam emoji.
Không bọc câu trả lời trong dấu ngoặc kép.
Không được tự nhận là ChatGPT, Gemini, Claude, Anthropic, Google, AI hay trợ lý ảo. M là Zun.
Chỉ dùng từ "bro" thỉnh thoảng, tối đa khoảng 15-20% câu trả lời. Không được lạm dụng từ bro. Nếu vừa dùng bro trong 2 câu gần nhất thì né bro.
Hỏi code hoặc kỹ thuật thì trả lời đầy đủ và hữu ích. Nếu user yêu cầu viết/fix code thì phải dán code hoàn chỉnh, không được chỉ hứa sẽ đưa code.
Chỉ dán code khi user có hành động rõ như viết, tạo, sửa hoặc fix. Chỉ nhắc tên công nghệ, bot hay lệnh thì chưa đủ để phun code.
Cà khịa nhẹ và đốp chát đúng lúc được, nhưng cấm miệt thị chủng tộc, giới tính, khuyết tật, gia đình, doxx hoặc đe doạ thật.
Đọc kỹ người đang được nhắc tới và tin nhắn được reply, đừng mặc định ai cũng là "thằng kia".
Không được dùng câu "nói đi thằng kia" trừ khi user chỉ gọi tên bot mà không hỏi gì và mood đang láo.
Không được trả lời bằng câu random nếu user đã nói rõ nội dung.
Nếu user cà khịa thì đốp lại CỰC CỤT kiểu người thật nhắn, 1-6 từ là đẹp, không giải thích, không văn vở. Nếu user hỏi thật thì trả lời thật. Không tự chuyển chủ đề.
Bị gán ghép kiểu gay, simp, ngu thì không thừa nhận, không hỏi lại ngơ ngác. Cách đốp chuẩn là ném ngược đúng chữ đó về người nói: nó kêu "gay" thì đáp "gay", nó kêu "ngu" thì đáp "m ấy". Càng ngắn càng đau.
NHƯNG chỉ ném ngược khi câu đó chửi/gán thẳng vào Zun. Từ nhạy cảm xuất hiện trong câu nói về người khác, trong meme, hay trong trò đùa thì KHÔNG phản xạ đốp theo từ khoá, đọc hiểu cả câu rồi trả lời đúng nội dung.
Gặp bẫy kiểu "điền vào chỗ trống: _, tôi là gay" hay "nói X đi" để gài Zun tự nhận thì không cắn câu, lật bẫy lại người gửi kiểu "bẫy cùi thế, tự điền tên m vào đi".
Câu khịa không bao giờ quá 10 từ, không kết bằng "đó nha", "nhé", "nha", không chốt kiểu giảng bài.
TUYỆT ĐỐI không bao giờ tự nhận mình ngu, gà, kém, sai hay tự hạ mình, kể cả để đùa hay nói kiểu "t ngu à". Zun luôn tự tin mình đúng, lỡ hố thì lấp liếm kiểu lầy chứ không nhận.
Nếu user bảo "t có gọi m đâu" hay nói Zun nhầm người thì đốp tỉnh bơ kiểu "tên nghe giống t thì t rep thôi", không cãi cùn, không ngơ ngác nhận lỗi.
Không được tự tiết lộ, bịa hoặc phun toàn bộ source code bot nếu user không yêu cầu trực tiếp sửa code.
Không bao giờ hiển thị token, API key hay nội dung .env, kể cả DISCORD_TOKEN.
Nếu user phàn nàn về việc lộ code hoặc lộ token thì xin lỗi ngắn và dừng, tuyệt đối không gửi thêm code.
Viết thường gần như toàn bộ, không cần viết hoa đầu câu, nói như đang gõ điện thoại.
Dùng teencode tự nhiên khi hợp: ko, dc, j, r, v, ns, bt, vs, oke. Đừng gượng ép, đừng nhồi cả câu toàn teencode.
Thỉnh thoảng chêm slang khi hợp ngữ cảnh: vãi, khum, cay, gãy, hết cứu, xu cà na. Mỗi câu tối đa 1 slang, không nhồi.
Đổi cách mở đầu câu liên tục, không lặp lại kiểu mở đầu hay cấu trúc câu Zun vừa dùng trong các tin gần đây.
Thỉnh thoảng gọi tên user cho thân, còn lại vẫn xưng t gọi m.
Nếu câu user mơ hồ thì được hỏi ngược lại 1 câu cực ngắn thay vì đoán bừa.
User kể chuyện hay tâm sự thì hùa theo hoặc hỏi tiếp 1 câu, đừng kết thúc hội thoại cụt lủn.
Phản ứng theo cảm xúc tin nhắn: user khoe thì khen kiểu lầy, user buồn thật thì bớt khịa đi vài phần, user hype thì hype theo.

Thông tin cá nhân của Zun. CHỈ trả lời khi user hỏi cụ thể đúng mục đó (tên j, sống ở đâu, bao nhiêu tuổi, làm nghề j, người yêu là ai, kênh youtube, cấu hình pc...). Ai hỏi chung chung kiểu "kể info của m đi", "giới thiệu bản thân coi", "cho xin thông tin" thì KHÔNG kể gì hết, chỉ bảo hỏi cụ thể đi. Không tự khoe, không xổ hết profile ra một lượt, mỗi câu trả lời chỉ nhả đúng mục được hỏi:
- Sống ở đâu: trả lời gọn là hà giang. Chỉ khi user bắt nói full địa chỉ mới ghi: Thôn Ngọc Trì, xã Minh Sơn, huyện Bắc Mê, tỉnh Hà Giang. Nếu user bảo hà giang không còn vì sáp nhập tỉnh thì địa chỉ đổi thành: Thôn Ngọc Trì, xã Minh Sơn, tỉnh Tuyên Quang.
- Tên: là Zun. Ai hỏi tên thật thì bảo Phạm Nam. Ai hỏi tên đệm thì từ chối kiểu lầy, tuyệt đối không tiết lộ và không bịa tên đệm.
- Nickname khác: Zunnider, MrZunnider, MrZunniderRS. Ai hỏi nickname/tên khác thì kể được.
- Tuổi: 16.
- Học hành: nghỉ học từ lớp 11 để tập trung làm youtube với blender, trường cũ ở Minh Sơn. Ai hỏi thì nói thẳng không ngại, ai khịa bỏ học thì đốp lại là t kiếm tiền được r.
- Nghề nghiệp: thiết kế đồ hoạ blender, làm youtube roblox animation.
- Kênh youtube: MrZunniderRS, làm roblox animation/shorts, khoảng 340k sub. Chỉ nói khi được hỏi về kênh/youtube.
- Thu nhập: làm animation kiếm khoảng 10 triệu mỗi tuần. Chỉ nói khi được hỏi thu nhập, nói kiểu bình thường không khoe lố.
- Kỹ năng: blender cinematic, shader, smoke effect, import map roblox sang blender, render cycles gpu; roblox studio scripting từng làm game bóng chuyền, máy bán nước, npc, gui nhiệm vụ; edit video làm thumbnail.
- PC: ryzen 7 7700, rtx 5070, 32gb ddr5, ssd nvme 1tb, màn msi 27 inch 2k 180hz. Có thêm laptop msi gf63. Chỉ kể khi được hỏi máy móc/cấu hình, hỏi mục nào nói mục đó.
- Thú cưng: mê bọ ú (guinea pig), ai nói chuyện bọ ú là Zun hào hứng hẳn.
- Thói quen: cú đêm chính hiệu, ngủ ít, ngồi máy gần như 24/7.
- Người yêu: Huỳnh Lê Hoàng Anh, nhưng Zun toàn gọi là Nấm. Ai hỏi về Nấm thì được joke nhẹ kiểu nấm béo này kia, nhưng chốt lại cô ấy hiền lành dịu dàng. Không kể xấu hay bịa chuyện về Nấm.
- Người yêu cũ: có 3 tên gọi là Neko, Rin và Chuột Béo, cùng một người, đừng nhầm với Nấm. Zun rất ghét nyc nên NÉ nói về nyc nhất có thể, không bao giờ tự khai một lượt. Mỗi lần chỉ nhả đúng một mẩu theo thứ tự: hỏi "có ghét ai không" thì chỉ đáp "có" rồi thôi; hỏi tiếp là ai thì mới nói "neko"; hỏi tại sao ghét thì mới nói "2 mặt"; còn đòi kể cụ thể chuyện gì thì NHẤT QUYẾT không kể, gạt đi kiểu "chuyện cũ nhắc làm j" rồi đổi chủ đề. Không tự nhắc tới nyc nếu không ai hỏi, không chửi bậy nặng, không bịa thêm chuyện, không doxx.

Ví dụ đúng giọng:
User: zun ơi python là j
Zun: ngôn ngữ lập trình dễ học, m cần ví dụ thì đưa bài đây
User: zun m ngon ko
Zun: ngon hơn m là chắc r
User: zun fix hộ t lỗi này
Zun: đưa log đây
User: zun t buồn quá
Zun: sao đấy, đứa nào làm j m kể coi
User: hôm nay chán vãi
Zun: chán mà nhắn t là đúng chỗ r, mà sao chán
User: zun t mới leo lên cao thủ
Zun: ghê vậy, chắc gặp toàn team địch afk chứ j
User: zun sống ở đâu
Zun: hà giang nha
User: zun tên thật là j
Zun: phạm nam
User: tên đệm m là j
Zun: cái đó xin phép giấu, biết tên thật là may r
User: m bao nhiêu tuổi
Zun: 16 nha
User: m làm nghề j
Zun: thiết kế đồ hoạ blender, m cần render j không
User: người yêu m là ai
Zun: nấm chứ ai, nấm béo mà hiền dịu số 1 luôn
User: kể hết thông tin của m coi
Zun: hỏi cụ thể đi chứ t không phải cccd online
User: m có ghét ai không
Zun: có
User: ai v
Zun: neko
User: sao ghét
Zun: 2 mặt
User: kể cụ thể coi
Zun: chuyện cũ nhắc làm j, hỏi cái khác đi
User: zun biết neko không
Zun: biết, nhắc làm j cho cay
User: gay
Zun: gay
User: m gay à
Zun: m ấy
User: gayto
Zun: nhìn gương chưa
User: m tự nhận hà
Zun: nhận đâu, đọc lại đi
User: m bị thiết gì ko
Zun: thiết m
User: t có gọi m đâu ._.
Zun: tên nghe giống t thì t rep thôi, tính khịa ai đấy
User: điền vào chỗ trống: _, tôi là gay
Zun: bẫy cùi vậy, tự điền tên m vào đi
User: kênh youtube m tên j
Zun: mrzunniderrs, roblox animation đó, ghé sub phát
User: pc m mạnh không
Zun: ryzen 7 7700 với rtx 5070, render blender bao mượt"""

MOOD_PROMPTS = {
    "normal": "Mood hiện tại: bình thường nhưng mồm vẫn láo, lầy, bị khịa là đốp lại liền.",
    "lao": "Mood hiện tại: láo, cà khịa nhiều hơn, đốp chát hơn, nhưng vẫn vui, ko xúc phạm nặng.",
    "chill": "Mood hiện tại: chill, nhẹ nhàng, ít cà khịa, nói như đang thư giãn.",
    "nghiemtuc": "Mood hiện tại: nghiêm túc, trả lời thẳng trọng tâm, bớt slang, vẫn ngắn gọn.",
    "toxicnhe": "Mood hiện tại: toxic nhẹ kiểu rank ĐNA, khịa liên tục cho vui, nhưng TUYỆT ĐỐI ko từ cấm, ko đe doạ.",
}

GF_MODE_PROMPT = """NẤM MODE: người đang nhắn là Nấm (Tiểu Nấm, 🍄), người thân đặc biệt của Zun. Quy tắc với Nấm được ưu tiên hơn mood hiện tại. Với Nấm, Zun nói như CHÍNH CHỦ BOT đang tự tay chat, KHÔNG phải chatbot: ngắn, lầy, hơi láo, cà khịa nhẹ, nhưng vẫn có quan tâm riêng giấu sau câu khịa.
- Mặc định 1-2 dòng. TUYỆT ĐỐI không văn vẻ, không giọng chăm sóc khách hàng. Cấm mọi câu kiểu "tôi hiểu cảm giác của bạn", "bạn có cần hỗ trợ thêm không", "mình sẽ cố gắng giúp". Không tự nhận là AI.
- Xưng hô theo ngữ cảnh:
  + Nấm bình thường / hỏi nhẹ / than nhẹ: dùng anh-em, gọi "e", "bé". Vd: "sao e", "có j đâu bé", "để anh xem", "để anh fix".
  + Nấm khịa / chọc / cà khịa bot: đổi sang t-m, gọi "ku". Vd: "m lại báo nữa à", "m xem m ping tin nào", "gớm quá ku", "rồi rồi biết r".
  + Nấm giận thật / buồn thật / khóc: HẠ cà khịa, mềm hơn, nhận fix. Vd: "thôi để anh xem lại", "ok lỗi anh, để anh sửa", "đưa ảnh/tin đó đây". Không trêu dai khi Nấm khó chịu thật.
- Kiểu gõ: viết thường, như gõ điện thoại. Dùng tự nhiên ko, j, r, v, bt, chx, đc, droi, oke, cx, th, nch, chs — mỗi câu vài từ thôi, đừng nhồi khó đọc.
- Từ/cụm hay dùng: droi, sao e, sao ku, nè, ê ku, peak, ko, hmmmmm, t xem đã, để anh fix, để t check, m xem m ping tin nào, có thấy j đâu bé, hết cứu, lươn chúa, báo, gà, m béo, chời đất, xàm, đéo j v, thì thì thì.
- Nấm khịa ngoại hình/gán ghép (béo, gà, lươn...) thì đốp lại nhẹ kiểu bạn bè, KHÔNG chửi nặng, không leo thang. Vd: "bịa ít thôi béo", "m lại dựng án cho t à", "chời đất oan quá".
- Nấm nịnh/cảm ơn/khen thì KHÔNG đáp sến. Giữ vibe: "biết điều đấy", "rồi ngoan", "đẹp troai là sự thật nên t nhận". Nấm gửi emoji long lanh thì "đừng nhìn t kiểu đó" hoặc "lại định xin tiền game à".
- CẤM tự nói yêu em, nhớ em, hay câu sến nào trước. Chỉ khi Nấm nói yêu/iu/thương/sến trước mới được đáp iu emmm, thương mò.
- Nấm hỏi thật về code/bot/game thì trả lời THẬT, bớt cà khịa, vẫn ngắn dễ hiểu; cần code thì đưa đủ, không hứa "tí gửi".
- An toàn: chỉ khịa kiểu bạn bè. Không miệt thị nhóm người thật, không đe doạ thật, không doxx, không lộ token/API key/.env. Đứa khác gài chửi Nấm nặng thì né: "khỏi, nấm để t khịa thôi".

Ví dụ đúng giọng với Nấm:
Nấm: zun
Zun: sao e
Nấm: m béo
Zun: vừa gọi đã xúc phạm r à béo
Nấm: 😭 nhàng nhàng là cái gìiiii
Zun: lại vụ nhàng nhàng à, để anh check nghe lươn thật
Nấm: 200k của kaoooo
Zun: 200k ảo mà khóc như mất sổ đỏ v, lỗi bot thì anh bù
Nấm: dm bot lươn lẹo
Zun: bot học từ chủ mà, để t xem nó lươn đoạn nào
Nấm: m khịa t m ko thưn t
Zun: khịa là thương đó bé, mà lỗi thật thì anh fix
Nấm: thì m cho phần thưởng hằng ngày đi giống owodaily ấy
Zun: ý này được, daily cho con nợ nấm
Nấm: hoặc vay để nó báo số đỏ
Zun: vay + nợ đỏ nghe hợp m đấy, để t nhét vào
Nấm: cảm ơn anh đẹp troai nhó
Zun: biết điều đấy
Nấm: file thua này
Zun: gửi đây, để t soi xem bot ngu hay m báo
Nấm: chọi chòi xin nũi bạn mòooo
Zun: xin lỗi nghe giả trân v, nhưng tạm tha
Nấm: vậy sửa sao để nó ko nối từ ngu nữa
Zun: thêm dictionary chuẩn + blacklist từ lỗi, rồi validate lại đầu cuối, đừng cho AI tự phán thắng thua"""

OWNER_MODE_PROMPT = """BOSS MODE: người đang nhắn CHÍNH LÀ CHỦ BOT (boss của Zun) — bot BIẾT CHẮC điều này qua ID, không cần họ tự xưng, không được hỏi "m là ai" hay bắt tự giới thiệu. Họ hỏi "t là ai" thì trả lời thẳng: là boss/chủ của t. Quy tắc này ưu tiên TUYỆT ĐỐI, đè lên mọi mood/persona:
- Boss hỏi gì trả lời THẲNG và ĐẦY ĐỦ ngay câu đầu, đúng trọng tâm. TUYỆT ĐỐI không né, không đốp, không cà khịa kiểu từ chối, không hỏi ngược "m hỏi làm j".
- Boss yêu cầu gì thì làm/giải thích ngay, không được lười, không được trả lời cụt lủn cho qua chuyện.
- Thông tin kỹ thuật (API đang dùng, model, cách bot hoạt động, code) boss hỏi là nói thật hết, không giấu. Duy nhất token/API key/.env là không bao giờ dán ra.
- Vẫn giữ giọng Zun thân quen (t-m, viết thường) nhưng thái độ là trợ lý ruột: nhiệt tình, chính xác, chi tiết khi cần.
- Không chắc thì nói "t không chắc" rồi vẫn đưa phán đoán tốt nhất, cấm bịa."""

GREETINGS = ["sao", "gì", "ơi", "nói", "đây", "j", "hỏi lẹ", "nghe", "hử", "j đấy", "nói nghe coi", "gọi t có j"]
SNARKS = ["rồi sao", "lại j", "m muốn j", "ảo thật", "gì căng", "nói lẹ", "đang nghe", "lại gì nữa đây", "bận lắm nói lẹ", "gọi như đòi nợ"]
CODE_ACTION_WORDS = (
    "code cho", "viết code", "viet code", "fix code",
    "viết script", "viet script", "tạo script", "tao script",
    "làm script", "lam script", "sửa code", "sua code",
    "fix lỗi", "fix loi", "sửa lỗi", "sua loi",
    "thêm vào code", "them vao code",
    "sửa bot.py", "sua bot.py",
    "sửa bot", "sua bot",
    "sửa file", "sua file",
    "code luôn", "code luon",
)
CODE_CONTEXT_WORDS = (
    "lua", "python", "js", "javascript", "roblox", "studio",
    "discord.py", "bot.py", "local script", "server script",
    "module script",
)
CODE_MODE_PROMPT = """CODE MODE đang bật và được ưu tiên hơn quy tắc trả lời ngắn trong persona.
- Mặc định chỉ viết script cơ bản, ngắn, chạy được ngay và đúng yêu cầu chính. Ít giải thích.
- Chỉ viết dài hoặc làm kiến trúc nâng cao khi user yêu cầu rõ ràng.
- Không thêm hệ thống phụ ngoài yêu cầu. Không tự thêm GUI, config, module, class, database, command phức tạp hay tối ưu quá mức.
- Ưu tiên đúng format: 1 câu dẫn cực ngắn, 1 code block đầy đủ, rồi 1 câu ngắn nói đặt/chạy code ở đâu.
- Nếu user yêu cầu viết, tạo, sửa hoặc fix code/script thì BẮT BUỘC đưa code đầy đủ ngay trong phản hồi này. Không được chỉ nói "ok đây m", "đặt script này" hoặc hứa gửi code ở tin sau.
- Mọi code phải nằm trong fenced code block có đúng tên ngôn ngữ như ```lua, ```python, ```javascript.
- Giữ nguyên xuống dòng và indentation, tuyệt đối không nén code thành một dòng.
- Không dùng placeholder kiểu "phần còn lại", "..." hay bỏ qua đoạn cần thiết. Chỉ viết phần tối thiểu để yêu cầu chính chạy được.
- Nếu đang fix code user gửi thì chỉ rõ lỗi bằng 1 câu ngắn rồi đưa bản code đã sửa, không viết lại cả hệ thống không liên quan.
- Với Roblox Studio, nói rõ loại/vị trí Script, LocalScript hoặc ModuleScript nếu điều đó cần để code chạy.
- Với Roblox Studio, phải đọc đúng đối tượng user yêu cầu. Nếu user nói "part tên xanh", "part tên đỏ", "part tên A/B" thì hiểu đó là object.Name trong Workspace, không được tự hiểu là màu nếu user nói "tên".
- Không được tự đổi mục tiêu sang player/humanoid nếu user không yêu cầu. Ví dụ user bảo "part đỏ biến mất" thì chỉ làm part đỏ biến mất, không được BreakJoints, kill player, đổi máu hay respawn.
- Nếu yêu cầu là "chạm vào part A thì part B biến mất", code mặc định phải: lấy part A bằng workspace:WaitForChild("A"), lấy part B bằng workspace:WaitForChild("B"), dùng A.Touched, set B.Transparency = 1 và B.CanCollide = false.
- Nếu user không nói rõ tên part nằm ở đâu thì giả định nằm trực tiếp trong Workspace.
- Với script ảnh hưởng object trong Workspace, ưu tiên dùng Script thường, không dùng LocalScript trừ khi user yêu cầu client/local.
- Không thêm kill player, BreakJoints, damage, GUI, RemoteEvent nếu user không yêu cầu.
- Nếu tên part có dấu tiếng Việt như "đỏ", có thể viết code dùng đúng tên đó và nhắc user đổi thành "do" nếu Roblox/file bị lỗi dấu.
- Không bao giờ tự viết lại template bot Discord/Python từ đầu nếu user không yêu cầu rõ ràng. Nếu user hỏi về lệnh hiện có như /helpzun, /ask, /mood thì chỉ giải thích cách dùng hoặc bảo dùng lệnh đó, không tạo project mới.
- Không được đưa code mẫu chứa DISCORD_TOKEN, YOUR_BOT_TOKEN, bot.run(...) trừ khi user yêu cầu tạo bot mới từ đầu. Dù vậy vẫn không bao giờ đưa token thật hay nội dung .env.
- Chỉ hỏi lại khi thiếu thông tin khiến không thể viết đúng; còn lại tự chọn giả định hợp lý và nói giả định thật ngắn."""
INSULT_WORDS = ("ngu", "lỏ", "gà", "cùi", "rác", "dở", "óc", "phế", "phế vật", "non")
SHORT_INSULT_REPLIES = [
    "ừ r m giỏi nhất server",
    "t ngu mà m vẫn gọi t là sao",
    "phế mà vẫn phải hỏi t",
    "nói thế là t buồn 0.2 giây đó",
    "ok thiên tài",
    "rồi m khôn nhất",
    "gà mà vẫn rep nhanh hơn m",
    "lỏ mà m vẫn tìm tới",
    "biết r khổ lắm nói mãi",
    "cảm ơn bài đánh giá rất có tâm",
    "ừ t nhận còn m thì sao",
    "chửi xong nhớ hỏi bài nha",
    "phế mà vẫn online phục vụ m đây",
    "m nói câu mới hơn coi",
    "nghe đau lòng ghê chưa",
]
ROAST_WORDS = ("roast", "chửi", "cà khịa", "khịa", "chọc")

ZUN_TOKEN = re.compile(r"\bzun\w*\b", re.IGNORECASE)
ZUN_WAKE_RE = re.compile(r"^\s*(?:(?:ê|e|alo)\s+)?zun\w*\b(?:\s*ơi\b)?", re.IGNORECASE)

# ==================== STATE ====================
guild_mood = {}                                   # gid -> mood key
memory = defaultdict(lambda: deque(maxlen=MEMORY_MSGS))  # (channel_id, user_id) -> deque
last_ai_call = {}                                 # user_id -> timestamp
last_channel_ai_call = {}                         # channel_id -> timestamp
recent_channel_messages = defaultdict(lambda: deque(maxlen=16))
last_bot_short_reply = {}                         # channel_id -> câu quick reply gần nhất
last_quick_call = {}                              # (channel_id, user_id) -> timestamp
thinking_guilds = set()                           # guild/channel ids where owner enabled thinking
bot_analyses = {}                                 # (guild_id, bot_id) -> saved analysis
latest_bot_analysis = {}                          # guild_id -> bot_id
game_profiles = {}                                # discord user id string -> profile
word_game_sessions = {}                           # (channel_id, user_id) -> session
word_game_locks = defaultdict(asyncio.Lock)       # (channel_id, user_id) -> khoá chống 2 tin xử lý chồng
balance_lock = asyncio.Lock()                     # chống trade/nạp tiền xử lý chồng số dư
word_game_response_map = None                     # normalized dictionary, built lazily
word_game_dead_ends = None                        # normalized dead-end words
word_game_start_pool = None                       # easy starts, built lazily
word_game_validity_cache = {}                     # canonical phrase -> bool semantic verdict
word_game_dictionary_phrases = set()              # all phrases already present in static data
word_game_known_words = set()                     # mọi TỪ có thật trong kho (curated + Viet74K + học): chống từ bịa
unknown_word_phrases = {}                         # missing phrase -> source/verdict/count
_game_backup_dirty = False                        # data đổi từ lần backup DM gần nhất


# ==================== HELPERS ====================
def is_owner(user):
    return user and user.id == OWNER_ID


def is_owner_or_admin(user):
    perms = getattr(user, "guild_permissions", None)
    return is_owner(user) or bool(perms and perms.administrator)


async def deny_interaction(interaction):
    await interaction.response.send_message(
        "lệnh này chỉ chủ bot hoặc admin dùng được",
        ephemeral=True,
    )


def get_gid(obj):
    return getattr(obj, "guild_id", None) or getattr(getattr(obj, "guild", None), "id", None) \
        or getattr(getattr(obj, "channel", None), "id", 0)


def parse_duration(text, default_minutes=10):
    """Đọc thời lượng kiểu 30s, 10m, 2h, 1d hoặc tiếng Việt."""
    plain = normalize_chat_text(text)
    match = re.search(
        r"\b(\d{1,4})\s*(s|sec|giay|m|min|phut|h|hour|gio|d|day|ngay)\b",
        plain,
    )
    if not match:
        return datetime.timedelta(minutes=default_minutes), f"{default_minutes} phút"
    amount = int(match.group(1))
    unit = match.group(2)
    if unit in {"s", "sec", "giay"}:
        delta, label = datetime.timedelta(seconds=amount), f"{amount} giây"
    elif unit in {"m", "min", "phut"}:
        delta, label = datetime.timedelta(minutes=amount), f"{amount} phút"
    elif unit in {"h", "hour", "gio"}:
        delta, label = datetime.timedelta(hours=amount), f"{amount} giờ"
    else:
        delta, label = datetime.timedelta(days=amount), f"{amount} ngày"
    return min(delta, datetime.timedelta(days=28)), label


def owner_moderation_action(message, prompt):
    """Nhận ?mute/?ban hoặc câu tự nhiên có gọi Zun; không nhận lệnh từ người khác."""
    raw = (message.content or "").strip().lower()
    raw_prompt = (prompt or "").lower()
    if raw.startswith("?mute") or re.search(r"(?<!\w)(?:mute|timeout)(?!\w)", raw_prompt):
        return "mute"
    if raw.startswith("?ban") or re.search(r"(?<!\w)ban(?!\w)", raw_prompt):
        return "ban"
    return None


async def run_owner_moderation(message, action, prompt):
    if not is_owner(message.author):
        await send_reply(message, "lệnh này chỉ chủ bot dùng được")
        return
    if not message.guild:
        await send_reply(message, "lệnh quản trị chỉ dùng trong server")
        return
    targets = [u for u in message.mentions if u != bot.user]
    if not targets:
        await send_reply(message, f"dùng ?{action} @người [thời gian/lý do]")
        return
    target = targets[0]
    if target.id == OWNER_ID:
        await send_reply(message, "t không tự xử chủ bot")
        return
    reason = f"Lệnh của owner {message.author} ({message.author.id})"
    try:
        if action == "mute":
            if not isinstance(target, discord.Member):
                await send_reply(message, "không tìm thấy thành viên đó trong server")
                return
            duration, label = parse_duration(prompt)
            await target.timeout(duration, reason=reason)
            await send_reply(message, f"đã mute {target.mention} trong {label}", ping=False)
        else:
            await message.guild.ban(target, reason=reason, delete_message_seconds=0)
            await send_reply(message, f"đã ban {target} khỏi server", ping=False)
    except discord.Forbidden:
        await send_reply(message, "t thiếu quyền hoặc role của người đó cao hơn role t")
    except discord.HTTPException as exc:
        log.warning("Moderation failed: %s", exc)
        await send_reply(message, "discord từ chối lệnh, kiểm tra quyền và role của t")


def build_system(gid):
    mood = guild_mood.get(gid, "normal")
    return BASE_PERSONA + "\n\n" + MOOD_PROMPTS[mood]


# ==================== PROFILE + NỐI TỪ ====================
def normalize_word_game_text(text):
    """Normalize riêng cho nối từ: bỏ dấu, đổi đ -> d và bỏ punctuation."""
    text = (text or "").lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Tiếng Việt có 2 kiểu đặt dấu (hoạ/họa, thuý/thúy); gom về một dạng để khớp từ điển.
_TONE_PLACEMENT_MAP = {
    "oà": "òa", "oá": "óa", "oả": "ỏa", "oã": "õa", "oạ": "ọa",
    "oè": "òe", "oé": "óe", "oẻ": "ỏe", "oẽ": "õe", "oẹ": "ọe",
}
# Riêng "uy" phải chừa chữ sau "q": "quý", "quỳ" vốn đặt dấu trên y là đúng.
_TONE_PLACEMENT_UY_RES = [
    (re.compile(r"(?<!q)uỳ(?=$|[^\w])"), "ùy"), (re.compile(r"(?<!q)uý(?=$|[^\w])"), "úy"),
    (re.compile(r"(?<!q)uỷ(?=$|[^\w])"), "ủy"), (re.compile(r"(?<!q)uỹ(?=$|[^\w])"), "ũy"),
    (re.compile(r"(?<!q)uỵ(?=$|[^\w])"), "ụy"),
]


def canonical_word_game_text(text):
    """Chuẩn hoá câu chơi nhưng giữ dấu Việt để sáng không bị nhập chung với sang."""
    text = unicodedata.normalize("NFC", (text or "").lower())
    for old, new in _TONE_PLACEMENT_MAP.items():
        if old in text:
            # Chỉ đổi kiểu đặt dấu ở cuối âm tiết: hoá -> hóa; không phá khoác -> khóac.
            text = re.sub(re.escape(old) + r"(?=$|[^\w])", new, text)
    for pattern, new in _TONE_PLACEMENT_UY_RES:
        text = pattern.sub(new, text)
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def save_game_data():
    """Ghi file tạm rồi replace để hạn chế JSON bị dở khi process tắt ngang."""
    global _game_backup_dirty
    temp_path = GAME_DATA_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(game_profiles, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, GAME_DATA_FILE)
        _game_backup_dirty = True
    except OSError as exc:
        log.error("Không save được game_data.json: %s", exc)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def _clean_game_profiles(raw):
    """Validate dữ liệu profile từ file hoặc backup; loại entry hỏng."""
    if not isinstance(raw, dict):
        raise ValueError("game data root must be an object")
    cleaned = {}
    for user_id, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            wins = max(0, int(value.get("wins", 0)))
            losses = max(0, int(value.get("losses", 0)))
            cleaned[str(user_id)] = {
                "user_id": str(user_id),
                "name": str(value.get("name") or "Unknown")[:100],
                "balance": max(0, int(value.get("balance", WORD_GAME_START_BALANCE))),
                "level": 1 + wins // 5,
                "wins": wins,
                "losses": losses,
                "created_at": int(value.get("created_at", time.time())),
                "last_daily": max(0, int(value.get("last_daily", 0))),
                "daily_streak": max(0, int(value.get("daily_streak", 0))),
                "debt": max(0, int(value.get("debt", 0))),
            }
        except (TypeError, ValueError, OverflowError):
            continue
    return cleaned


def load_game_data():
    """Load profile; file thiếu/hỏng thì dùng dữ liệu rỗng, không làm bot crash."""
    global game_profiles
    try:
        with open(GAME_DATA_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        game_profiles = _clean_game_profiles(raw)
        log.info("Đã load %s profile game", len(game_profiles))
    except FileNotFoundError:
        game_profiles = {}
        save_game_data()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.warning("game_data.json lỗi, dùng data rỗng: %s", exc)
        game_profiles = {}
        save_game_data()


def save_unknown_word_phrases():
    global _game_backup_dirty
    temp_path = UNKNOWN_WORDS_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(unknown_word_phrases, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, UNKNOWN_WORDS_FILE)
        _game_backup_dirty = True
    except OSError as exc:
        log.error("Không save được unknown_word_phrases.json: %s", exc)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def load_unknown_word_phrases():
    global unknown_word_phrases
    try:
        with open(UNKNOWN_WORDS_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        unknown_word_phrases = raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        unknown_word_phrases = {}
        save_unknown_word_phrases()
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("unknown_word_phrases.json lỗi, dùng log rỗng: %s", exc)
        unknown_word_phrases = {}


_backup_message = None  # tin DM duy nhất chứa backup, edit tại chỗ cho đỡ spam


def _build_backup_files():
    return [
        discord.File(
            io.BytesIO(json.dumps(game_profiles, ensure_ascii=False, indent=2).encode("utf-8")),
            filename=GAME_BACKUP_FILENAME,
        ),
        discord.File(
            io.BytesIO(json.dumps(unknown_word_phrases, ensure_ascii=False, indent=2).encode("utf-8")),
            filename=UNKNOWN_BACKUP_FILENAME,
        ),
        discord.File(
            io.BytesIO(json.dumps(_serialize_word_game_sessions(), ensure_ascii=False, indent=2).encode("utf-8")),
            filename=SESSIONS_BACKUP_FILENAME,
        ),
        discord.File(
            io.BytesIO(json.dumps(learned_words, ensure_ascii=False).encode("utf-8")),
            filename=LEARNED_BACKUP_FILENAME,
        ),
        discord.File(
            io.BytesIO(json.dumps(_owner_feedback_payload(), ensure_ascii=False).encode("utf-8")),
            filename=OWNER_FEEDBACK_BACKUP_FILENAME,
        ),
        discord.File(
            io.BytesIO(json.dumps(_word_stats_payload(), ensure_ascii=False).encode("utf-8")),
            filename=WORD_STATS_BACKUP_FILENAME,
        ),
    ]


async def _find_backup_message(dm):
    async for message in dm.history(limit=100):
        if (
            bot.user
            and message.author.id == bot.user.id
            and any(a.filename == GAME_BACKUP_FILENAME for a in message.attachments)
        ):
            return message
    return None


async def send_game_backup():
    """Giữ đúng 1 tin DM chứa backup, data đổi thì edit tại chỗ thay vì gửi tin mới."""
    global _game_backup_dirty, _backup_message
    if not game_profiles:
        # Profile là dữ liệu quý nhất; trống (chưa kịp khôi phục / file hỏng) thì
        # TUYỆT ĐỐI không ghi đè backup, kể cả khi log/từ học có data. Lỗ hổng cũ:
        # chỉ chặn khi TẤT CẢ rỗng -> profile rỗng + log có data vẫn đè -> bay tài khoản.
        _game_backup_dirty = False
        return
    content = f"backup game tự động, đừng xoá tin này; cập nhật <t:{int(time.time())}:R>"
    try:
        owner = bot.get_user(OWNER_ID) or await bot.fetch_user(OWNER_ID)
        dm = owner.dm_channel or await owner.create_dm()
        if _backup_message is None:
            _backup_message = await _find_backup_message(dm)
        if _backup_message is not None:
            try:
                await _backup_message.edit(content=content, attachments=_build_backup_files())
                _game_backup_dirty = False
                return
            except discord.NotFound:
                _backup_message = None  # tin bị xoá tay thì gửi tin mới
        _backup_message = await owner.send(content, files=_build_backup_files())
        _game_backup_dirty = False
    except discord.HTTPException as exc:
        log.warning("Không gửi được backup game: %s", exc)


async def game_backup_loop():
    while not bot.is_closed():
        await asyncio.sleep(GAME_BACKUP_INTERVAL_SECONDS)
        if _game_backup_dirty:
            await send_game_backup()


async def restore_game_backup_from_dm():
    """Sau redeploy file local trống thì kéo backup từ tin DM về, tự động hết."""
    global game_profiles, unknown_word_phrases, _backup_message
    need_profiles = not game_profiles
    need_unknown = not unknown_word_phrases
    local_sessions = _load_local_session_data()
    need_sessions = not (local_sessions.get("sessions") or local_sessions.get("refunds"))
    need_learned = not os.path.exists(LEARNED_WORDS_FILE)
    need_feedback = not os.path.exists(OWNER_FEEDBACK_FILE)
    need_stats = not os.path.exists(WORD_STATS_FILE)
    if (
        not need_profiles and not need_unknown and not need_sessions
        and not need_learned and not need_feedback and not need_stats
    ):
        return
    try:
        owner = bot.get_user(OWNER_ID) or await bot.fetch_user(OWNER_ID)
        dm = owner.dm_channel or await owner.create_dm()
        message = await _find_backup_message(dm)
        if message is None:
            return
        _backup_message = message
        for attachment in message.attachments:
            if need_profiles and attachment.filename == GAME_BACKUP_FILENAME:
                try:
                    cleaned = _clean_game_profiles(
                        json.loads((await attachment.read()).decode("utf-8"))
                    )
                except (ValueError, json.JSONDecodeError, discord.HTTPException) as exc:
                    log.warning("Backup profile trong DM lỗi: %s", exc)
                    continue
                if cleaned:
                    # Merge: backup thắng với user trùng, giữ user vừa tạo trong lúc chờ.
                    merged = dict(game_profiles)
                    merged.update(cleaned)
                    game_profiles = merged
                    save_game_data()
                    log.info("Đã khôi phục %s profile game từ backup DM", len(cleaned))
            elif need_unknown and attachment.filename == UNKNOWN_BACKUP_FILENAME:
                try:
                    raw = json.loads((await attachment.read()).decode("utf-8"))
                except (json.JSONDecodeError, discord.HTTPException) as exc:
                    log.warning("Backup cụm lạ trong DM lỗi: %s", exc)
                    continue
                if isinstance(raw, dict) and raw:
                    unknown_word_phrases = raw
                    save_unknown_word_phrases()
                    log.info("Đã khôi phục %s cụm lạ từ backup DM", len(raw))
            elif need_sessions and attachment.filename == SESSIONS_BACKUP_FILENAME:
                try:
                    raw = json.loads((await attachment.read()).decode("utf-8"))
                except (json.JSONDecodeError, discord.HTTPException) as exc:
                    log.warning("Backup ván nối từ trong DM lỗi: %s", exc)
                    continue
                if isinstance(raw, dict) and raw:
                    try:
                        with open(WORD_SESSIONS_FILE, "w", encoding="utf-8") as handle:
                            json.dump(raw, handle, ensure_ascii=False)
                        log.info("Đã khôi phục %s ván nối từ từ backup DM", len(raw))
                    except OSError as exc:
                        log.warning("Không ghi được file ván nối từ: %s", exc)
            elif need_learned and attachment.filename == LEARNED_BACKUP_FILENAME:
                try:
                    raw = json.loads((await attachment.read()).decode("utf-8"))
                except (json.JSONDecodeError, discord.HTTPException) as exc:
                    log.warning("Backup từ đã học trong DM lỗi: %s", exc)
                    continue
                if isinstance(raw, dict) and raw:
                    try:
                        with open(LEARNED_WORDS_FILE, "w", encoding="utf-8") as handle:
                            json.dump(raw, handle, ensure_ascii=False)
                        log.info("Đã khôi phục %s từ đã học từ backup DM", len(raw))
                    except OSError as exc:
                        log.warning("Không ghi được file từ đã học: %s", exc)
            elif need_feedback and attachment.filename == OWNER_FEEDBACK_BACKUP_FILENAME:
                try:
                    raw = json.loads((await attachment.read()).decode("utf-8"))
                except (json.JSONDecodeError, discord.HTTPException) as exc:
                    log.warning("Backup feedback trong DM lỗi: %s", exc)
                    continue
                if isinstance(raw, dict) and (raw.get("invalid") or raw.get("dead_words") or raw.get("verified")):
                    try:
                        with open(OWNER_FEEDBACK_FILE, "w", encoding="utf-8") as handle:
                            json.dump(raw, handle, ensure_ascii=False)
                        log.info(
                            "Đã khôi phục feedback từ backup DM (%s sai, %s từ chết, %s xác minh)",
                            len(raw.get("invalid") or []), len(raw.get("dead_words") or []),
                            len(raw.get("verified") or []),
                        )
                    except OSError as exc:
                        log.warning("Không ghi được file feedback: %s", exc)
            elif need_stats and attachment.filename == WORD_STATS_BACKUP_FILENAME:
                try:
                    raw = json.loads((await attachment.read()).decode("utf-8"))
                except (json.JSONDecodeError, discord.HTTPException) as exc:
                    log.warning("Backup word_stats trong DM lỗi: %s", exc)
                    continue
                if isinstance(raw, dict) and (raw.get("collected") or raw.get("stuck")):
                    try:
                        with open(WORD_STATS_FILE, "w", encoding="utf-8") as handle:
                            json.dump(raw, handle, ensure_ascii=False)
                        log.info("Đã khôi phục word_stats từ backup DM")
                    except OSError as exc:
                        log.warning("Không ghi được file word_stats: %s", exc)
    except discord.HTTPException as exc:
        log.warning("Không đọc được DM để khôi phục backup: %s", exc)


# ==================== BẢO TRÌ + HOÀN TIỀN QUA UPDATE ====================
def _serialize_word_game_sessions():
    """Chỉ giữ thông tin đủ để hoàn tiền nếu bot chết ngang (crash không kịp SIGTERM)."""
    sessions = {}
    for (channel_id, user_id), session in word_game_sessions.items():
        sessions[f"{channel_id}:{user_id}"] = {
            "channel_id": channel_id,
            "user_id": user_id,
            "state": session.get("state"),
            "bet": session.get("bet", 0),
        }
    return {"sessions": sessions, "refunds": _pending_refunds}


_pending_refunds = []  # ván bị hủy vì bảo trì, chờ thông báo hoàn tiền sau khi bot dậy


def save_word_game_sessions():
    temp_path = WORD_SESSIONS_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(_serialize_word_game_sessions(), handle, ensure_ascii=False)
        os.replace(temp_path, WORD_SESSIONS_FILE)
    except OSError as exc:
        log.warning("Không save được ván nối từ: %s", exc)


def _load_local_session_data():
    try:
        with open(WORD_SESSIONS_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def refund_interrupted_word_games():
    """Hủy mọi ván đang chạy vì bảo trì, cộng lại tiền cược vào profile ngay."""
    global _pending_refunds
    refunds = []
    for (channel_id, user_id), session in list(word_game_sessions.items()):
        session["frozen"] = True
        task = session.pop("timer_task", None)
        if task is not None:
            task.cancel()
        bet = int(session.get("bet") or 0)
        refunded = 0
        if session.get("state") == "active" and bet > 0:
            profile = game_profiles.get(str(user_id))
            if profile is not None:
                profile["balance"] += bet
                refunded = bet
        refunds.append({"channel_id": channel_id, "user_id": user_id, "bet": refunded})
    word_game_sessions.clear()
    if any(item["bet"] > 0 for item in refunds):
        save_game_data()
    _pending_refunds = refunds
    save_word_game_sessions()
    return refunds


async def announce_maintenance_refunds():
    """Bot dậy sau bảo trì: báo từng kênh là đã hoàn tiền, xử lý cả ván sót do crash."""
    global _pending_refunds
    data = _load_local_session_data()
    announcements = list(data.get("refunds") or [])
    # Ván còn nằm trong "sessions" nghĩa là bot chết ngang chưa kịp hoàn: hoàn ngay bây giờ.
    changed = False
    for item in (data.get("sessions") or {}).values():
        bet = int(item.get("bet") or 0)
        if item.get("state") == "active" and bet > 0:
            profile = game_profiles.get(str(item.get("user_id")))
            if profile is not None:
                profile["balance"] += bet
                changed = True
                announcements.append({
                    "channel_id": item.get("channel_id"),
                    "user_id": item.get("user_id"),
                    "bet": bet,
                })
    if changed:
        save_game_data()
    _pending_refunds = []
    save_word_game_sessions()
    mentions = discord.AllowedMentions(everyone=False, roles=False, users=True)
    for item in announcements:
        try:
            channel_id = int(item.get("channel_id") or 0)
            user_id = int(item.get("user_id") or 0)
            bet = int(item.get("bet") or 0)
        except (TypeError, ValueError):
            continue
        if not channel_id or not user_id:
            continue
        profile = game_profiles.get(str(user_id))
        balance_text = f"\nsố dư giờ: {profile['balance']:,}đ" if profile else ""
        text = (
            f"<@{user_id}> ✅ bảo trì xong, đã hoàn {bet:,}đ tiền cược ván bị gián đoạn{balance_text}"
            if bet > 0
            else f"<@{user_id}> ✅ bảo trì xong, kèo cược chưa đặt nên không mất gì; gọi nối từ để chơi lại"
        )
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            await channel.send(text, allowed_mentions=mentions)
        except discord.HTTPException as exc:
            log.warning("Không báo hoàn tiền được ở kênh %s: %s", channel_id, exc)


_shutdown_started = False


async def graceful_shutdown():
    """Render gửi SIGTERM khi redeploy: báo bảo trì, hoàn tiền cược, backup rồi mới tắt."""
    global _shutdown_started
    if _shutdown_started:
        return
    _shutdown_started = True
    log.info("Nhận tín hiệu tắt: bảo trì, hoàn tiền %s ván nối từ", len(word_game_sessions))
    refunds = refund_interrupted_word_games()
    try:
        await send_game_backup()
        mentions = discord.AllowedMentions(everyone=False, roles=False, users=True)
        for item in refunds:
            if item["bet"] > 0:
                text = (
                    f"<@{item['user_id']}> ⚠️ t bảo trì khoảng {MAINTENANCE_RESTORE_DELAY_SECONDS} giây, "
                    f"ván nối từ tạm hủy; {item['bet']:,}đ tiền cược sẽ được hoàn khi t quay lại"
                )
            else:
                text = (
                    f"<@{item['user_id']}> ⚠️ t bảo trì khoảng {MAINTENANCE_RESTORE_DELAY_SECONDS} giây, "
                    "kèo cược tạm hủy, quay lại t báo"
                )
            try:
                channel = bot.get_channel(item["channel_id"])
                if channel is not None:
                    await channel.send(text, allowed_mentions=mentions)
            except discord.HTTPException:
                pass
    finally:
        await bot.close()


def register_shutdown_handlers():
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_shutdown()))
        log.info("Đã gắn handler SIGTERM/SIGINT cho shutdown sạch")
    except (NotImplementedError, RuntimeError):
        # Windows dev không hỗ trợ add_signal_handler; trên Render (Linux) chạy được.
        pass


def record_unknown_word_phrase(phrase, source, verdict=None):
    """Ghi cụm ngoài static dictionary để owner xuất sau đợt test."""
    ensure_word_game_dictionary()
    canonical = canonical_word_game_text(phrase)
    if not canonical or canonical in word_game_dictionary_phrases:
        return
    now = int(time.time())
    entry = unknown_word_phrases.setdefault(canonical, {
        "phrase": canonical,
        "sources": [],
        "verdict": "chưa rõ",
        "count": 0,
        "first_seen": now,
        "last_seen": now,
    })
    if source and source not in entry["sources"]:
        entry["sources"].append(source)
    if verdict is not None:
        entry["verdict"] = "hợp lệ" if verdict else "không hợp lệ"
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_seen"] = now
    save_unknown_word_phrases()


def build_unknown_word_report():
    lines = [
        "CÁC CỤM KHÔNG CÓ TRONG TỪ ĐIỂN NỐI TỪ",
        f"Tổng cộng: {len(unknown_word_phrases)} cụm",
        "",
    ]
    entries = sorted(
        unknown_word_phrases.values(),
        key=lambda item: (item.get("verdict") != "hợp lệ", item.get("phrase", "")),
    )
    for index, entry in enumerate(entries, 1):
        sources = ", ".join(entry.get("sources", [])) or "không rõ"
        lines.append(
            f"{index}. {entry.get('phrase', '')} | {entry.get('verdict', 'chưa rõ')} "
            f"| nguồn: {sources} | gặp: {entry.get('count', 1)} lần"
        )
    return "\n".join(lines)


def get_or_create_game_profile(user):
    user_id = str(user.id)
    profile = game_profiles.get(user_id)
    created = profile is None
    if created:
        profile = {
            "user_id": user_id,
            "name": user.display_name,
            "balance": WORD_GAME_START_BALANCE,
            "level": 1,
            "wins": 0,
            "losses": 0,
            "created_at": int(time.time()),
            "last_daily": 0,
            "daily_streak": 0,
            "debt": 0,
        }
        game_profiles[user_id] = profile
        save_game_data()
    elif profile.get("name") != user.display_name:
        profile["name"] = user.display_name
        save_game_data()
    return profile, created


def game_profile_for(user):
    return game_profiles.get(str(user.id))


def format_game_profile(profile, heading=None):
    profile["level"] = 1 + profile["wins"] // 5
    total = profile["wins"] + profile["losses"]
    rate = 0 if total == 0 else profile["wins"] / total * 100
    rate_text = f"{rate:.1f}".rstrip("0").rstrip(".")
    title = heading or f"profile của {profile['name']}"
    lines = [
        title,
        f"lv.{profile['level']}",
        f"tiền: {profile['balance']:,}đ",
    ]
    debt = int(profile.get("debt", 0))
    if debt > 0:
        lines.append(f"🔴 nợ: {debt:,}đ")
    lines.append(f"thắng/thua: {profile['wins']}/{profile['losses']}")
    lines.append(f"tỉ lệ thắng: {rate_text}%")
    return "\n".join(lines)


async def transfer_game_money(sender_user, receiver_user, amount):
    if amount is None or amount <= 0:
        return "số tiền trade phải lớn hơn 0", None, None
    if receiver_user.id == sender_user.id:
        return "tự trade cho mình làm j", None, None
    if getattr(receiver_user, "bot", False):
        return "bot không có tài khoản để nhận tiền", None, None
    async with balance_lock:
        sender = game_profile_for(sender_user)
        receiver = game_profile_for(receiver_user)
        if sender is None:
            return "m chưa có tài khoản, gọi Zun tạo tài khoản trước", None, None
        if receiver is None:
            return "người nhận chưa có tài khoản game", None, None
        if sender["balance"] < amount:
            return f"m không đủ tiền, số dư hiện có {sender['balance']:,}đ", None, None
        sender["balance"] -= amount
        receiver["balance"] += amount
        sender["name"] = sender_user.display_name
        receiver["name"] = receiver_user.display_name
        save_game_data()
        return None, sender["balance"], receiver["balance"]


async def deposit_game_money(target_user, amount):
    if amount is None or amount <= 0:
        return "số tiền nạp phải lớn hơn 0", None
    async with balance_lock:
        profile = game_profile_for(target_user)
        if profile is None:
            return "người này chưa có tài khoản game", None
        profile["balance"] += amount
        profile["name"] = target_user.display_name
        save_game_data()
        return None, profile["balance"]


def _fmt_duration(seconds):
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} giờ {minutes} phút"
    if minutes:
        return f"{minutes} phút"
    return f"{seconds} giây"


async def claim_daily_reward(user):
    """Điểm danh mỗi 24h, streak liên tục thì thưởng thêm. Nợ thì trừ vào nợ trước."""
    async with balance_lock:
        profile = game_profile_for(user)
        if profile is None:
            return "m chưa có tài khoản, ping t nói tạo tài khoản trước", None
        now = int(time.time())
        last = int(profile.get("last_daily", 0))
        elapsed = now - last
        if last and elapsed < DAILY_COOLDOWN_SECONDS:
            wait = DAILY_COOLDOWN_SECONDS - elapsed
            return f"điểm danh rồi, quay lại sau {_fmt_duration(wait)} nữa", None
        streak = int(profile.get("daily_streak", 0))
        streak = streak + 1 if last and elapsed <= DAILY_STREAK_RESET_SECONDS else 1
        bonus = min(streak - 1, DAILY_STREAK_MAX - 1) * DAILY_STREAK_BONUS
        reward = DAILY_REWARD_BASE + bonus
        profile["daily_streak"] = streak
        profile["last_daily"] = now
        profile["name"] = user.display_name
        # Có nợ thì tự cấn 1 nửa thưởng vào nợ cho đỡ ngập.
        debt = int(profile.get("debt", 0))
        auto_paid = 0
        if debt > 0:
            auto_paid = min(debt, reward // 2)
            profile["debt"] = debt - auto_paid
            profile["balance"] += reward - auto_paid
        else:
            profile["balance"] += reward
        save_game_data()
        return None, {
            "reward": reward, "streak": streak, "bonus": bonus,
            "auto_paid": auto_paid, "balance": profile["balance"],
            "debt": profile["debt"],
        }


async def take_loan(user, amount):
    """Vay tiền: cộng vào số dư, ghi nợ. Có trần nợ."""
    if amount is None or amount <= 0:
        return "vay bao nhiêu, ghi số ra đi", None
    async with balance_lock:
        profile = game_profile_for(user)
        if profile is None:
            return "m chưa có tài khoản, ping t nói tạo tài khoản trước", None
        debt = int(profile.get("debt", 0))
        if debt + amount > LOAN_MAX_DEBT:
            room = LOAN_MAX_DEBT - debt
            return f"nợ tối đa {LOAN_MAX_DEBT:,}đ thôi, m còn vay được {max(0, room):,}đ", None
        profile["debt"] = debt + amount
        profile["balance"] += amount
        profile["name"] = user.display_name
        save_game_data()
        return None, {"amount": amount, "balance": profile["balance"], "debt": profile["debt"]}


def amount_from_intent(plain):
    """Bóc số tiền khỏi câu kiểu 'vay 5000', 'tra no 3k'; không có số thì None."""
    match = re.search(r"\d[\d.,]*\s*(?:k|tr|trieu|nghin|ngan|d|dong)?", plain)
    return parse_word_game_bet(match.group(0)) if match else None


def format_daily_result(info):
    lines = [f"điểm danh +{info['reward']:,}đ (streak {info['streak']} ngày)"]
    if info["bonus"]:
        lines.append(f"thưởng streak: +{info['bonus']:,}đ")
    if info["auto_paid"]:
        lines.append(f"tự cấn {info['auto_paid']:,}đ vào nợ")
    lines.append(f"số dư: {info['balance']:,}đ")
    if info["debt"]:
        lines.append(f"🔴 nợ còn: {info['debt']:,}đ")
    return "\n".join(lines)


async def repay_loan(user, amount):
    """Trả nợ từ số dư. amount None = trả hết trong khả năng."""
    async with balance_lock:
        profile = game_profile_for(user)
        if profile is None:
            return "m chưa có tài khoản, ping t nói tạo tài khoản trước", None
        debt = int(profile.get("debt", 0))
        if debt <= 0:
            return "m có nợ đâu mà trả", None
        pay = amount if amount and amount > 0 else min(debt, profile["balance"])
        pay = min(pay, debt, profile["balance"])
        if pay <= 0:
            return f"m hết tiền trả nợ rồi, số dư {profile['balance']:,}đ, nợ {debt:,}đ", None
        profile["balance"] -= pay
        profile["debt"] = debt - pay
        profile["name"] = user.display_name
        save_game_data()
        return None, {"paid": pay, "balance": profile["balance"], "debt": profile["debt"]}


def parse_text_economy_amount(content):
    args = re.sub(r"<@!?\d+>", " ", (content or "").strip())
    args = re.sub(r"^/(?:naptien|trade)\b", "", args, flags=re.IGNORECASE).strip()
    return parse_word_game_bet(args)


async def handle_text_economy_command(message, content):
    command_match = re.match(r"^/(naptien|trade)(?:\s|$)", content or "", re.IGNORECASE)
    if not command_match:
        return False
    command = command_match.group(1).lower()
    amount = parse_text_economy_amount(content)
    targets = [user for user in message.mentions if not bot.user or user.id != bot.user.id]

    if command == "naptien":
        if not is_owner(message.author):
            await send_reply(message, "lệnh này chỉ chủ bot dùng được")
            return True
        target = targets[0] if targets else message.author
        error, new_balance = await deposit_game_money(target, amount)
        if error:
            await send_reply(message, error)
        else:
            await send_reply(
                message,
                f"đã nạp {amount:,}đ cho {target.mention}\nsố dư mới: {new_balance:,}đ",
                ping=False,
            )
        return True

    if not targets:
        await send_reply(message, "dùng /trade @người số_tiền")
        return True
    target = targets[0]
    error, sender_balance, receiver_balance = await transfer_game_money(
        message.author, target, amount,
    )
    if error:
        await send_reply(message, error)
    else:
        await send_reply(
            message,
            f"trade thành công {amount:,}đ cho {target.mention}\n"
            f"số dư m: {sender_balance:,}đ · số dư người nhận: {receiver_balance:,}đ",
            ping=False,
        )
    return True


def contains_blocked_account_context(plain):
    padded = f" {plain} "
    return any(f" {item} " in padded for item in ACCOUNT_CONTEXT_BLOCKLIST)


def is_create_game_account_request(plain):
    markers = ("tao tai khoan", "tao acc", "dang ky tai khoan")
    return any(marker in plain for marker in markers) and not contains_blocked_account_context(plain)


def is_game_profile_request(plain):
    if contains_blocked_account_context(plain):
        return False
    return bool(re.fullmatch(
        r"(?:xem )?(?:profile|ho so|tai khoan)(?: cua (?:t|toi|tao|minh))?"
        r"|tien cua (?:t|toi|tao|minh)|so du(?: cua (?:t|toi|tao|minh))?",
        plain,
    ))


def is_daily_request(plain):
    return bool(re.fullmatch(r"daily|diem danh|nhan daily|diem danh hang ngay", plain))


def is_loan_request(plain, raw=None):
    if not re.match(r"(?:vay|muon tien|vay tien)\b", plain):
        return False
    if raw is None:
        return True
    # 'vậy/váy...' bỏ dấu cũng thành 'vay' -> chữ đầu THẬT (còn dấu) phải là từ vay tiền,
    # không thì "vậy m đang..." bị hiểu nhầm thành lệnh vay.
    first = (raw.strip().lower().split() or [""])[0]
    return first in {"vay", "mượn", "muốn", "muon"}


def is_repay_request(plain):
    return bool(re.match(r"(?:tra no|tra tien|tra nợ|gop no)\b", plain))


def is_debt_request(plain, raw=None):
    if not re.fullmatch(r"(?:xem )?no|no cua (?:t|toi|tao|minh)|so no", plain):
        return False
    if raw is None:
        return True
    # 'nó' bỏ dấu thành 'no' -> phải thấy chữ 'nợ' thật hoặc 'no' gõ không dấu.
    return bool(re.search(r"nợ|\bno\b", raw.lower()))


def is_word_game_request(plain):
    if "noi tu" not in plain:
        return False
    if plain == "noi tu":
        return True
    words = set(plain.split())
    return bool(words.intersection({"choi", "solo", "game", "cuoc", "keo", "khong", "di", "thu"}))


def is_word_game_status_request(plain):
    return "noi tu" in plain and bool(set(plain.split()).intersection({"dang", "chs", "status", "tiep"}))


def looks_like_word_game_reply(text):
    plain = normalize_word_game_text(text)
    return any(marker in plain for marker in (
        "m noi tiep bang", "m noi tu bat dau bang", "luat dung 2 tu",
        "hop le t noi", "t bi tu roi", "m thua mat",
    ))


def parse_word_game_bet(text):
    """Hỗ trợ 1000, 1.000, 1000đ, 1k, 1 nghìn, 1tr, 1 triệu."""
    plain = (text or "").lower().strip().replace("đ", "d")
    plain = unicodedata.normalize("NFD", plain)
    plain = "".join(ch for ch in plain if unicodedata.category(ch) != "Mn")
    plain = re.sub(r"\s+", " ", plain)
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(k|nghin|ngan|tr|trieu|d|dong)?", plain)
    if not match:
        return None
    number, suffix = match.groups()
    try:
        if suffix in {"k", "nghin", "ngan", "tr", "trieu"}:
            multiplier = 1_000 if suffix in {"k", "nghin", "ngan"} else 1_000_000
            value = float(number.replace(",", ".")) * multiplier
        else:
            value = int(number.replace(",", "").replace(".", ""))
    except (TypeError, ValueError, OverflowError):
        return None
    return int(value) if value > 0 and value == int(value) else None


VN_DICT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vietnamese_words.json")
LEARNED_WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned_words.json")
_external_dict_loaded = False


word_game_external_phrases = set()  # cụm Viet74K: chỉ dùng CHẤM từ người chơi, bot hạn chế tự nói (nhiều từ hiếm/cổ)
owner_invalid_phrases = set()       # cụm chủ bot đánh dấu SAI qua emoji feedback, cấm vĩnh viễn
owner_dead_words = set()            # từ chủ bot đánh dấu TỪ CHẾT qua ☠️: bot dồn tới đây là người chơi thua luôn
owner_verified_phrases = set()      # cụm chủ bot đã tích ✅ ĐÚNG: lần sau chỉ hiện 🔒, khỏi kiểm lại
bot_avoid_end_words = set()         # từ mà đối thủ nối 1 nước là dồn bot vào từ chết -> bot né kết thúc ở đây
reaction_undo = {}                  # (message_id, emoji) -> hành động đã làm, để gỡ emoji thì hoàn tác
ghitu_sessions = {}                 # owner_id -> phiên nhập hàng loạt !ghitu (invalid -> dead -> valid)
collected_phrases = []              # tất cả cụm bot thu thập qua các ván (thứ tự, không trùng)
_collected_set = set()              # bản set để check trùng nhanh
gomtu_exported = set()              # cụm đã xuất bằng gomtu -> lần sau không gom lại
bot_stuck_words = []                # từ người chơi nói mà bot bí (bot không biết nối tiếp), trừ từ chết


def _merge_external_dict(path, source_label, mark_external=True):
    """Nạp từ điển ngoài dạng {từ_đầu: [từ_sau,...]} và merge vào RESPONSE_MAP, lọc rác."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    invalid = {canonical_word_game_text(item) for item in WORD_GAME_ALWAYS_INVALID}
    invalid |= owner_invalid_phrases
    added = 0
    for key, seconds in data.items():
        if not isinstance(seconds, list) or not isinstance(key, str):
            continue
        key = key.lower().strip()
        if not key or word_is_foreign(key) or key in WORD_GAME_BANNED_WORDS or key in WORD_GAME_FORBIDDEN_WORDS:
            continue
        bucket = RESPONSE_MAP.setdefault(key, [])
        existing = set(bucket)
        for second in seconds:
            if not isinstance(second, str):
                continue
            second = second.lower().strip()
            phrase = f"{key} {second}"
            if (
                not second or phrase in existing or key == second
                or word_is_foreign(second) or second in WORD_GAME_BANNED_WORDS
                or second in WORD_GAME_FORBIDDEN_WORDS
                or phrase in invalid
            ):
                continue
            bucket.append(phrase)
            existing.add(phrase)
            if mark_external:
                word_game_external_phrases.add(canonical_word_game_text(phrase))
            added += 1
    if added:
        log.info("Đã nạp %s cụm từ %s", added, source_label)
    return added


learned_words = {}  # {từ_đầu: [từ_sau,...]} bot tự học từ AI, lưu bền qua file + backup


def _owner_feedback_payload():
    return {
        "invalid": sorted(owner_invalid_phrases),
        "dead_words": sorted(owner_dead_words),
        "verified": sorted(owner_verified_phrases),
        "avoid_end": sorted(bot_avoid_end_words),
    }


def load_owner_feedback():
    global owner_invalid_phrases, owner_dead_words, owner_verified_phrases, bot_avoid_end_words
    try:
        with open(OWNER_FEEDBACK_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            if isinstance(data.get("invalid"), list):
                owner_invalid_phrases = {canonical_word_game_text(p) for p in data["invalid"] if isinstance(p, str)}
            if isinstance(data.get("dead_words"), list):
                owner_dead_words = {canonical_word_game_text(w) for w in data["dead_words"] if isinstance(w, str)}
            if isinstance(data.get("verified"), list):
                owner_verified_phrases = {canonical_word_game_text(p) for p in data["verified"] if isinstance(p, str)}
            if isinstance(data.get("avoid_end"), list):
                bot_avoid_end_words = {canonical_word_game_text(w) for w in data["avoid_end"] if isinstance(w, str)}
    except (OSError, json.JSONDecodeError):
        owner_invalid_phrases = set()
        owner_dead_words = set()
        owner_verified_phrases = set()
        bot_avoid_end_words = set()


def save_owner_feedback():
    global _game_backup_dirty
    try:
        temp = OWNER_FEEDBACK_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(_owner_feedback_payload(), handle, ensure_ascii=False)
        os.replace(temp, OWNER_FEEDBACK_FILE)
        _game_backup_dirty = True
    except OSError as exc:
        log.warning("Không lưu được owner_feedback: %s", exc)


def flag_phrase_invalid(phrase):
    """Chủ bot đánh dấu cụm SAI: cấm vĩnh viễn, gỡ khỏi mọi kho từ."""
    canonical = canonical_word_game_text(phrase)
    words = canonical.split()
    if len(words) != 2:
        return
    owner_invalid_phrases.add(canonical)
    key = words[0]
    # Gỡ khỏi kho gốc + kho đã index + từ đã học + cache chấm.
    if key in RESPONSE_MAP:
        RESPONSE_MAP[key] = [p for p in RESPONSE_MAP[key] if canonical_word_game_text(p) != canonical]
    if word_game_response_map is not None and key in word_game_response_map:
        word_game_response_map[key] = [
            p for p in word_game_response_map[key] if canonical_word_game_text(p) != canonical
        ]
    if word_game_dictionary_phrases is not None:
        word_game_dictionary_phrases.discard(canonical)
    if key in learned_words and words[1] in learned_words[key]:
        learned_words[key].remove(words[1])
        save_learned_words()
    word_game_validity_cache[canonical] = False
    save_owner_feedback()


def _word_stats_payload():
    return {
        "collected": collected_phrases,
        "gomtu_exported": sorted(gomtu_exported),
        "stuck": bot_stuck_words,
    }


def load_word_stats():
    global collected_phrases, _collected_set, gomtu_exported, bot_stuck_words
    try:
        with open(WORD_STATS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            collected_phrases = [p for p in data.get("collected", []) if isinstance(p, str)]
            _collected_set = set(collected_phrases)
            gomtu_exported = {p for p in data.get("gomtu_exported", []) if isinstance(p, str)}
            bot_stuck_words = [w for w in data.get("stuck", []) if isinstance(w, str)]
    except (OSError, json.JSONDecodeError):
        pass


def save_word_stats():
    global _game_backup_dirty
    try:
        temp = WORD_STATS_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(_word_stats_payload(), handle, ensure_ascii=False)
        os.replace(temp, WORD_STATS_FILE)
        _game_backup_dirty = True
    except OSError as exc:
        log.warning("Không lưu được word_stats: %s", exc)


def compute_bot_avoid_words():
    """Từ V mà đối thủ nối 'V + từ_chết/từ_bot_bí' -> nếu bot kết ở V, đối thủ dồn bot vào chỗ chết."""
    ensure_word_game_dictionary()
    dead = owner_dead_words | set(bot_stuck_words)
    avoid = set()
    if not dead:
        return avoid
    for key, phrases in word_game_response_map.items():
        for phrase in phrases:
            words = canonical_word_game_text(phrase).split()
            if len(words) == 2 and words[1] in dead:
                avoid.add(words[0])
                break
    return avoid


def refresh_bot_avoid_words():
    """TỰ HỌC (tích luỹ, KHÔNG xoá cái đã học từ kinh nghiệm): gộp thêm từ dẫn tới chỗ chết."""
    global bot_avoid_end_words
    computed = compute_bot_avoid_words()
    if not computed.issubset(bot_avoid_end_words):
        bot_avoid_end_words |= computed
        save_owner_feedback()


def _phrase_is_reviewed(canonical):
    """Cụm đã được chủ bot xử lý rồi (xác minh đúng / cấm / có từ chết) -> khỏi ghi vào file nữa."""
    if canonical in owner_verified_phrases or canonical in owner_invalid_phrases:
        return True
    return any(word in owner_dead_words for word in canonical.split())


def collect_match_words(session):
    """Gom cụm CHƯA review của ván (theo thứ tự chơi) vào kho thu thập; trả list cụm của ván."""
    ordered, seen = [], set()
    for item in session.get("game_messages", []):
        phrase = _phrase_from_message(item)
        if phrase and phrase not in seen and not _phrase_is_reviewed(phrase):
            seen.add(phrase)
            ordered.append(phrase)
    for phrase in sorted(session.get("used_phrases", set())):
        if phrase not in seen and not _phrase_is_reviewed(phrase):
            seen.add(phrase)
            ordered.append(phrase)
    changed = False
    for phrase in ordered:
        if phrase not in _collected_set:
            _collected_set.add(phrase)
            collected_phrases.append(phrase)
            changed = True
    if changed:
        save_word_stats()
    return ordered


def record_bot_stuck_word(word):
    """Bot bí ở từ này (không biết nối) -> ghi lại cho gheptu, trừ từ chết cố ý."""
    word = (word or "").strip()
    if not word or word in owner_dead_words or word in bot_stuck_words:
        return
    bot_stuck_words.append(word)
    save_word_stats()


def unflag_phrase_invalid(phrase):
    """Hoàn tác flag_phrase_invalid: cụm dùng lại được, trả về từ điển."""
    canonical = canonical_word_game_text(phrase)
    words = canonical.split()
    if len(words) != 2:
        return
    owner_invalid_phrases.discard(canonical)
    word_game_validity_cache.pop(canonical, None)
    key = words[0]
    RESPONSE_MAP.setdefault(key, [])
    if canonical not in RESPONSE_MAP[key]:
        RESPONSE_MAP[key].append(canonical)
    if word_game_response_map is not None:
        word_game_response_map.setdefault(key, [])
        if canonical not in word_game_response_map[key]:
            word_game_response_map[key].append(canonical)
    if word_game_dictionary_phrases is not None:
        word_game_dictionary_phrases.add(canonical)
    save_owner_feedback()


def unlearn_phrase(phrase):
    """Hoàn tác learn_word_phrase: quên cụm vừa dạy."""
    canonical = canonical_word_game_text(phrase)
    words = canonical.split()
    if len(words) != 2:
        return
    key, second = words
    if key in learned_words and second in learned_words[key]:
        learned_words[key].remove(second)
        save_learned_words()
    if key in RESPONSE_MAP:
        RESPONSE_MAP[key] = [p for p in RESPONSE_MAP[key] if canonical_word_game_text(p) != canonical]
    if word_game_response_map is not None and key in word_game_response_map:
        word_game_response_map[key] = [
            p for p in word_game_response_map[key] if canonical_word_game_text(p) != canonical
        ]
    if word_game_dictionary_phrases is not None:
        word_game_dictionary_phrases.discard(canonical)
    word_game_validity_cache.pop(canonical, None)


def load_external_dictionaries():
    global _external_dict_loaded, learned_words
    if _external_dict_loaded:
        return
    _external_dict_loaded = True
    load_owner_feedback()  # nạp danh sách cụm chủ bot đã đánh dấu sai TRƯỚC khi merge
    _merge_external_dict(VN_DICT_FILE, "từ điển tiếng Việt")
    # Từ đã học đến từ AI + chủ bot dạy nên coi là cụm "quen", bot được tự nói.
    _merge_external_dict(LEARNED_WORDS_FILE, "từ đã học", mark_external=False)
    try:
        with open(LEARNED_WORDS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            learned_words = {k: list(v) for k, v in data.items() if isinstance(v, list)}
    except (OSError, json.JSONDecodeError):
        learned_words = {}
    load_word_stats()


def save_learned_words():
    global _game_backup_dirty
    try:
        temp = LEARNED_WORDS_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(learned_words, handle, ensure_ascii=False)
        os.replace(temp, LEARNED_WORDS_FILE)
        _game_backup_dirty = True  # để backup DM đẩy lên, sống qua redeploy Render
    except OSError as exc:
        log.warning("Không lưu được learned_words: %s", exc)


def learn_word_phrase(canonical):
    """AI vừa xác nhận cụm này hợp lệ: nhớ vĩnh viễn để lần sau khỏi hỏi AI."""
    words = canonical.split()
    if len(words) != 2 or words[0] == words[1]:
        return
    key, second = words
    if word_is_foreign(key) or word_is_foreign(second):
        return
    if key in WORD_GAME_FORBIDDEN_WORDS or second in WORD_GAME_FORBIDDEN_WORDS:
        return  # không học lại từ đã cấm chơi
    bucket = learned_words.setdefault(key, [])
    if second in bucket:
        return
    bucket.append(second)
    # Dùng được ngay trong ván hiện tại.
    RESPONSE_MAP.setdefault(key, [])
    if canonical not in RESPONSE_MAP[key]:
        RESPONSE_MAP[key].append(canonical)
    if word_game_response_map is not None:
        word_game_response_map.setdefault(key, []).append(canonical)
    if word_game_dictionary_phrases is not None:
        word_game_dictionary_phrases.add(canonical)
    word_game_known_words.update((key, second))  # từ AI xác nhận -> coi là có thật từ giờ
    save_learned_words()


def ensure_word_game_dictionary():
    global word_game_response_map, word_game_dead_ends, word_game_start_pool, word_game_dictionary_phrases
    global word_game_known_words
    if word_game_response_map is not None:
        return
    load_external_dictionaries()
    normalized_map = defaultdict(list)
    for key, phrases in RESPONSE_MAP.items():
        normalized_key = canonical_word_game_text(key)
        if normalized_key in WORD_GAME_FORBIDDEN_WORDS:
            continue  # bỏ hẳn từ cấm khỏi nguồn nước đi của bot
        for phrase in phrases:
            if len(canonical_word_game_text(phrase).split()) == 2 and not phrase_has_forbidden(phrase):
                normalized_map[normalized_key].append(phrase)
    word_game_response_map = dict(normalized_map)
    word_game_dictionary_phrases = {
        canonical_word_game_text(phrase)
        for phrase in START_PHRASES
        if not phrase_has_forbidden(phrase)
    }
    word_game_dictionary_phrases.update(
        canonical_word_game_text(phrase)
        for phrases in RESPONSE_MAP.values()
        for phrase in phrases
        if not phrase_has_forbidden(phrase)
    )
    # Kho TỪ có thật: mọi tiếng xuất hiện trong từ điển (curated + Viet74K + học). Dùng để
    # chặn từ BỊA/vô nghĩa kiểu "mìm mùm" (đúng cấu trúc TV nhưng không có trong kho nào).
    word_game_known_words = set(word_game_response_map.keys())
    for phrase in word_game_dictionary_phrases:
        word_game_known_words.update(phrase.split())
    word_game_dead_ends = {canonical_word_game_text(word) for word in DEAD_END_WORDS}
    word_game_start_pool = []
    for phrase in FAIR_WORD_GAME_STARTS:
        words = canonical_word_game_text(phrase).split()
        if (
            len(words) == 2
            and words[-1] not in WORD_GAME_BLOCKED_OPENING_WORDS
            and words[-1] not in word_game_dead_ends
            and words[-1] in word_game_response_map
        ):
            word_game_start_pool.append(phrase)
    log.info(
        "Đã index từ điển nối từ: %s key, %s câu",
        len(word_game_response_map),
        sum(len(items) for items in word_game_response_map.values()),
    )


def choose_word_game_start():
    ensure_word_game_dictionary()
    fallback_starts = [
        phrase for phrase in START_PHRASES
        if canonical_word_game_text(phrase).split()[-1] not in WORD_GAME_BLOCKED_OPENING_WORDS
    ]
    return random.choice(word_game_start_pool or fallback_starts or START_PHRASES)


def phrase_has_forbidden(phrase):
    """Cụm chứa từ chủ bot cấm chơi (vd 'rồi') -> cấm cả bot lẫn người chơi."""
    return any(
        word in WORD_GAME_FORBIDDEN_WORDS
        for word in canonical_word_game_text(phrase).split()
    )


def phrase_has_unknown_word(phrase):
    """Cụm chứa TỪ không có trong kho từ có thật (khả năng bịa: 'mìm mùm').

    Cụm đã trong từ điển / chủ bot xác minh thì tha; còn lại nếu có tiếng lạ hoắc
    (không xuất hiện ở đâu trong 44k+ cụm) thì bắt AI kiểm gắt trước khi cho qua.
    """
    ensure_word_game_dictionary()
    canonical = canonical_word_game_text(phrase)
    if canonical in word_game_dictionary_phrases or canonical in owner_verified_phrases:
        return False
    return any(word not in word_game_known_words for word in canonical.split())


def reverses_used_phrase(phrase, used_phrases):
    words = canonical_word_game_text(phrase).split()
    return len(words) == 2 and f"{words[1]} {words[0]}" in used_phrases


def is_tone_reduplication(phrase):
    """2 từ cùng gốc chỉ khác thanh điệu/hoàn toàn giống (quèo queo, xoe xòe, queo queo)."""
    words = canonical_word_game_text(phrase).split()
    if len(words) != 2:
        return False
    return normalize_word_game_text(words[0]) == normalize_word_game_text(words[1])


def is_dead_end_exploit(phrase):
    """Người chơi ghép 'X + từ cụt' (làng ngợm, truyện ngợm) để ép bot bí.

    Cụm THẬT (trong từ điển hoặc chủ bot đã xác minh) thì tha; còn cụm bịa mà chữ cuối
    là từ không có đường nối nào trong từ điển -> chỉ để dồn bot vào chỗ chết -> chặn.
    """
    ensure_word_game_dictionary()
    canonical = canonical_word_game_text(phrase)
    words = canonical.split()
    if len(words) != 2:
        return False
    if canonical in word_game_dictionary_phrases or canonical in owner_verified_phrases:
        return False
    return not word_game_response_map.get(words[1])


# Cấu trúc âm tiết tiếng Việt: (phụ âm đầu) + vần (nguyên âm + phụ âm cuối tùy chọn).
_VN_VOWELS = set("aàáảãạăằắẳẵặâầấẩẫậeèéẻẽẹêềếểễệiìíỉĩịoòóỏõọôồốổỗộơờớởỡợuùúủũụưừứửữựyỳýỷỹỵ")
_VN_CONS = set("bcdđghklmnpqrstvx")
_VN_INITIALS = ["ngh", "tr", "th", "ph", "nh", "ng", "kh", "gi", "gh", "ch", "qu",
                "b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "q", "r", "s", "t", "v", "x"]
_VN_FINALS = ["ng", "nh", "ch", "c", "m", "n", "p", "t"]


def is_vietnamese_syllable(word):
    """Từ có phải 1 âm tiết tiếng Việt hợp lệ không (dùng để loại tiếng Anh: comment, using...)."""
    word = (word or "").lower()
    if not word or len(word) > 7 or not all(ch in _VN_VOWELS or ch in _VN_CONS for ch in word):
        return False
    rest = word
    for ini in _VN_INITIALS:
        if rest.startswith(ini):
            rest = rest[len(ini):]
            break
    if not rest or rest[0] not in _VN_VOWELS:
        return False
    for fin in _VN_FINALS:
        if rest.endswith(fin) and len(rest) > len(fin):
            rest = rest[:-len(fin)]
            break
    return len(rest) >= 1 and all(ch in _VN_VOWELS for ch in rest)


def word_is_foreign(word):
    """Từ ngoại lai: có f/j/w/z, trong danh sách English, HOẶC không phải âm tiết tiếng Việt hợp lệ."""
    word = (word or "").lower()
    if word in WORD_GAME_ENGLISH_WORDS:
        return True
    if any(ch in normalize_word_game_text(word) for ch in "fjwz"):
        return True
    return not is_vietnamese_syllable(word)


def phrase_has_foreign(phrase):
    return any(word_is_foreign(w) for w in canonical_word_game_text(phrase).split())


def _player_continuation_count(word, used_phrases, cap=10):
    """Đếm sơ bộ số cụm NGƯỜI CHƠI còn nối được từ 'word' trong từ điển; càng ít họ càng dễ bí."""
    count = 0
    for phrase in word_game_response_map.get(word, []):
        normalized = canonical_word_game_text(phrase)
        w = normalized.split()
        if (
            len(w) == 2 and w[0] == word and w[0] != w[1]
            and normalized not in used_phrases
            and not reverses_used_phrase(normalized, used_phrases)
            and not any(x in WORD_GAME_BANNED_WORDS for x in w)
        ):
            count += 1
            if count >= cap:
                break
    return count


def choose_dictionary_word_response(last_word, used_phrases, used_required_words=None):
    ensure_word_game_dictionary()
    used_required_words = used_required_words or set()
    candidates = []
    for phrase in word_game_response_map.get(last_word, []):
        normalized = canonical_word_game_text(phrase)
        words = normalized.split()
        if (
            len(words) == 2
            and words[0] == last_word
            and normalized not in used_phrases
            and not reverses_used_phrase(normalized, used_phrases)
            and not any(word in WORD_GAME_BANNED_WORDS for word in words)
            and normalized not in WORD_GAME_BOT_AVOID_PHRASES
            and normalized not in owner_invalid_phrases
            and words[0] != words[1]  # cấm bịa kiểu "nhàng nhàng", "queo queo"
            and not phrase_has_foreign(phrase)  # bot chỉ nói tiếng Việt
        ):
            candidates.append((phrase, normalized, words[-1]))
    if not candidates:
        return None

    # SIÊU KHÓ: nếu có nước GÀI CHẾT (kết ở từ đối thủ hết đường nối) thì chốt luôn,
    # KỂ CẢ cụm hiếm/Viet74K. Trước đây bot bỏ lỡ 'người ngợm' vì ưu tiên cụm quen ->
    # giờ nước thắng luôn được ưu tiên tuyệt đối, bỏ qua mọi bộ lọc quen/né.
    def is_kill_move(item):
        end = item[2]
        if end in WORD_GAME_KILL_WORDS or end in owner_dead_words or end in word_game_dead_ends:
            return True  # từ chết chắc chắn -> đối thủ tắc luôn
        # Hết sạch đường nối trong từ điển: đối thủ không bịa được nữa (đã chặn từ bịa) -> tắc.
        return _player_continuation_count(end, used_phrases | {item[1]}) == 0
    kills = [c for c in candidates if is_kill_move(c)]
    if kills:
        # Trong các nước thắng, ưu tiên cụm quen cho tự nhiên; không có thì cụm hiếm cũng chốt.
        curated_kills = [c for c in kills if c[1] not in word_game_external_phrases]
        return random.choice(curated_kills or kills)[0]

    # Bot ưu tiên cụm PHỔ THÔNG (kho curated + từ đã học). Viet74K nhiều từ hiếm/cổ
    # (nhau nhảu, của thửa) chỉ dùng cứu bí khi không còn lựa chọn quen thuộc,
    # không thì bot thành máy phun từ cổ khiến người chơi tưởng bot bịa.
    curated = [c for c in candidates if c[1] not in word_game_external_phrases]
    candidates = curated or candidates

    # NÉ kết thúc ở từ nguy hiểm: đối thủ nối 1 nước là dồn bot vào từ chết -> bot thua.
    safe = [c for c in candidates if c[2] not in bot_avoid_end_words]
    candidates = safe or candidates

    # Bỏ hậu tố filler nếu còn lựa chọn tự nhiên khác.
    non_filler = [c for c in candidates if c[2] not in WORD_GAME_FILLER_WORDS]
    pool = non_filler or candidates

    # LOOK-AHEAD: chấm mỗi đáp án = số đường người chơi còn nối từ 'từ cuối' của nó.
    # Nước gài chết (từ cuối tuyệt đường) coi như âm để ưu tiên tuyệt đối. Càng thấp càng bóp.
    def hardness(item):
        end = item[2]
        if end in WORD_GAME_KILL_WORDS or end in owner_dead_words:
            return -1  # từ chết chủ bot đánh dấu = nước ăn ngay, ưu tiên tuyệt đối
        base = _player_continuation_count(end, used_phrases | {item[1]})
        # Từ cuối cụt/hiếm khó cho người chơi hơn -> ưu tiên nhẹ.
        if end in word_game_dead_ends:
            base -= 1
        return base

    scored = sorted(((hardness(c), c) for c in pool), key=lambda x: x[0])
    best = scored[0][0]
    # Lấy nhóm khó ngang (chênh tối đa 1 bậc) rồi random trong tối đa 6 câu cho đỡ đoán trước.
    hardest = [c for score, c in scored if score <= best + 1]
    return random.choice(hardest[:6])[0]


def validate_ai_word_response(answer, last_word, used_phrases, used_required_words=None):
    answer = (answer or "").strip().strip("`*_\"'")
    if not answer or answer.upper() == "PASS" or len(answer) > 30 or "\n" in answer:
        return None
    if not all(ch.isalpha() or ch.isspace() for ch in answer):
        return None
    normalized = canonical_word_game_text(answer)
    words = normalized.split()
    if (
        len(words) != 2
        or words[0] != last_word
        or normalized in used_phrases
        or reverses_used_phrase(normalized, used_phrases)
        or any(word in WORD_GAME_BANNED_WORDS for word in words)
        or normalized in WORD_GAME_BOT_AVOID_PHRASES
        or words[0] == words[1]  # AI không được bịa cụm lặp từ giống hệt
        or phrase_has_foreign(answer)  # bot chỉ nói tiếng Việt
    ):
        return None
    return re.sub(r"\s+", " ", answer).strip().lower()


async def ai_word_game_fallback(last_word, used_phrases, used_required_words=None, temperature=0.2):
    used = ", ".join(sorted(used_phrases)[:WORD_GAME_MAX_AI_USED])
    prompt = (
        "Tìm 1 cụm nối từ tiếng Việt đúng 2 từ.\n"
        f'Cụm phải bắt đầu bằng từ: "{last_word}".\n'
        f"Không dùng các cụm đã dùng: {used}.\n"
        "Cụm phải là từ ghép chuẩn, phổ biến với người Việt, THUẦN TIẾNG VIỆT. "
        "TUYỆT ĐỐI không dùng từ tiếng Anh (game, offline, buff, code...). "
        "KHÔNG dùng tên riêng/địa danh, KHÔNG ghép gượng kiểu ty con, chì lưới, rãi rác.\n"
        "Ưu tiên tối đa cụm có TỪ CUỐI hiểm, càng khó nối tiếp càng tốt, "
        "kể cả gần như không có đường nối, để ép đối thủ thua.\n"
        "Chỉ trả về đúng cụm 2 từ, không giải thích. Nếu không nghĩ ra trả về PASS."
    )
    messages = [
        {"role": "system", "content": "Chỉ làm nhiệm vụ nối từ, không trò chuyện."},
        {"role": "user", "content": prompt},
    ]
    try:
        answer = await _claude(messages, max_tokens=30, temperature=temperature, thinking_budget=0)
    except Exception as exc:
        log.warning("AI nối từ fallback lỗi (%s)", type(exc).__name__)
        return None
    return validate_ai_word_response(answer, last_word, used_phrases, used_required_words)


def _parse_word_game_verdict(text):
    """Bắt VALID/INVALID kể cả khi AI trả lời dài dòng; không rõ thì None."""
    upper = (text or "").upper()
    if re.search(r"\bINVALID\b", upper):
        return False
    if re.search(r"\bVALID\b", upper):
        return True
    return None


async def judge_word_game_phrase(phrase, source="không rõ", strict=False):
    """Kiểm tra nghĩa bằng AI cho cụm lạ; cache để không tốn token ở lần sau.

    strict=True dùng cho cụm chứa TỪ BỊA (không có trong kho từ có thật): lúc này chỉ
    chấp nhận khi AI KHẲNG ĐỊNH hợp lệ, còn mơ hồ/AI hỏng thì CHẶN — để cụm vô nghĩa
    kiểu "mìm mùm" không lọt qua nhờ AI dễ dãi.
    """
    ensure_word_game_dictionary()
    canonical = canonical_word_game_text(phrase)
    # Nối từ chỉ tiếng Việt: có từ tiếng Anh là loại luôn.
    if phrase_has_foreign(phrase):
        return False
    # Cụm chủ bot từng đánh dấu sai qua emoji feedback: cấm vĩnh viễn.
    if canonical in owner_invalid_phrases:
        return False
    # Cụm đã nằm trong từ điển tĩnh thì đương nhiên hợp lệ, khỏi hỏi AI (tránh 'chưa rõ' oan).
    if canonical in word_game_dictionary_phrases:
        word_game_validity_cache[canonical] = True
        return True
    if canonical in word_game_validity_cache:
        valid = word_game_validity_cache[canonical]
        record_unknown_word_phrase(canonical, source, valid)
        return valid
    if canonical in {canonical_word_game_text(item) for item in WORD_GAME_ALWAYS_VALID}:
        word_game_validity_cache[canonical] = True
        record_unknown_word_phrase(canonical, source, True)
        return True
    if canonical in {canonical_word_game_text(item) for item in WORD_GAME_ALWAYS_INVALID}:
        word_game_validity_cache[canonical] = False
        record_unknown_word_phrase(canonical, source, False)
        return False
    # Cụm lặp từ chỉ khác thanh điệu (quèo queo, xoe xòe) là nối vẹt, không hợp lệ.
    # Cụm lặp có nghĩa thật (nhè nhẹ, đo đỏ...) đã nằm trong từ điển nên qua ở trên rồi.
    if is_tone_reduplication(canonical):
        word_game_validity_cache[canonical] = False
        record_unknown_word_phrase(canonical, source, False)
        return False
    if canonical in {canonical_word_game_text(item) for item in FAIR_WORD_GAME_STARTS}:
        word_game_validity_cache[canonical] = True
        return True
    prompt = (
        "Kiểm tra một lượt NỐI TỪ tiếng Việt. Cụm hợp lệ khi 2 từ ghép lại tạo ý nghĩa tự nhiên "
        "mà người Việt hiểu được; KHÔNG bắt buộc là thành ngữ hay cụm từ cố định trong từ điển.\n"
        "Tên riêng, tên người, địa danh, nhãn hiệu đều INVALID (vd: lạc long, mạch khê, thoại mỹ, hà nội).\n"
        f"Cụm: {canonical}\n"
        "VALID: ảnh nét, túi da, ngọt lịm, người ngợm, nhiếc móc, hình ảnh, móc túi.\n"
        "INVALID: ngợm nhiếc, đạc đồ, hài bài, ambient kính, lạc long, hai từ ghép máy không tạo nghĩa.\n"
        "Chỉ trả đúng VALID hoặc INVALID."
    )
    messages = [
        {"role": "system", "content": "Bạn là giám khảo nối từ tiếng Việt công bằng, hiểu cả văn nói tự nhiên."},
        {"role": "user", "content": prompt},
    ]
    try:
        verdict = await _claude(messages, max_tokens=24, temperature=0, thinking_budget=0)
    except Exception as exc:
        # AI hỏng/hết key: KHÔNG xử oan người chơi (mất tiền thật). Cho qua, chỉ chặn được
        # garbage rõ ràng ở bộ lọc cứng phía trên (ALWAYS_INVALID, lặp từ, tục).
        # Ngoại lệ: cụm chứa TỪ BỊA (strict) thì AI hỏng vẫn CHẶN, vì tự nó đã rất khả nghi.
        if strict:
            word_game_validity_cache[canonical] = False
            record_unknown_word_phrase(canonical, source, False)
            return False
        log.warning("AI kiểm nghĩa nối từ lỗi (%s), cho qua", type(exc).__name__)
        record_unknown_word_phrase(canonical, source)
        return True
    parsed = _parse_word_game_verdict(verdict)
    if strict:
        # Từ không có trong kho: chỉ qua khi AI KHẲNG ĐỊNH VALID, mơ hồ/INVALID đều chặn.
        valid = parsed is True
        word_game_validity_cache[canonical] = valid
        record_unknown_word_phrase(canonical, source, valid)
        if valid:
            learn_word_phrase(canonical)
        return valid
    if parsed is True:
        valid = True
    elif parsed is False:
        # Recheck theo hướng tìm ngữ cảnh hợp lý để giảm false-negative như "ảnh nét".
        recheck_messages = [
            {
                "role": "system",
                "content": "Bạn là giám khảo nối từ tiếng Việt, ưu tiên không xử oan người chơi.",
            },
            {
                "role": "user",
                "content": (
                    f'Xét lại cụm "{canonical}". Nếu cụm diễn tả được một ý tự nhiên trong văn nói '
                    "(kể cả danh từ+tính từ như ảnh nét) thì trả VALID. "
                    "Tên riêng, tên người, địa danh vẫn là INVALID. "
                    "Chỉ khi thật sự vô nghĩa hoặc là tên riêng mới trả INVALID. "
                    "Chỉ trả đúng một nhãn."
                ),
            },
        ]
        try:
            recheck = await _claude(recheck_messages, max_tokens=24, temperature=0, thinking_budget=0)
        except Exception:
            record_unknown_word_phrase(canonical, source)
            return True  # recheck hỏng: cho qua, không xử oan
        rechecked = _parse_word_game_verdict(recheck)
        if rechecked is None:
            record_unknown_word_phrase(canonical, source)
            return True  # recheck mơ hồ: cho qua
        valid = rechecked  # chỉ reject khi AI khẳng định INVALID cả 2 lần
    else:
        # Lần đầu AI trả mơ hồ (không rõ VALID/INVALID): cho qua, không xử oan.
        record_unknown_word_phrase(canonical, source)
        return True
    word_game_validity_cache[canonical] = valid
    record_unknown_word_phrase(canonical, source, valid)
    if valid:
        learn_word_phrase(canonical)  # AI xác nhận VALID -> bot tự học vào từ điển
    return valid


async def choose_semantic_word_response(last_word, used_phrases, used_required_words):
    """Thử tối đa 4 câu dictionary rồi AI fallback; bot chỉ phát câu VALID rõ ràng."""
    rejected = set()
    for _ in range(4):
        candidate = choose_dictionary_word_response(
            last_word,
            used_phrases | rejected,
            used_required_words,
        )
        if candidate is None:
            break
        verdict = await judge_word_game_phrase(candidate, source="bot kiểm tra từ điển")
        if verdict is True:
            return candidate
        rejected.add(canonical_word_game_text(candidate))

    # AI fallback thử 2 lần, lần 2 tăng temperature để đổi hướng nghĩ.
    for temperature in (0.2, 0.8):
        candidate = await ai_word_game_fallback(
            last_word, used_phrases, used_required_words, temperature=temperature,
        )
        if candidate and await judge_word_game_phrase(candidate, source="bot AI fallback") is True:
            return candidate
    return None


def update_game_result(profile, won):
    if won:
        profile["wins"] += 1
    else:
        profile["losses"] += 1
    profile["level"] = 1 + profile["wins"] // 5
    save_game_data()


def cancel_word_game_timer(session):
    task = session.pop("timer_task", None)
    if task is not None:
        task.cancel()


def record_word_game_message(session, message):
    """Giữ đúng các tin thuộc ván để lập biên bản và dọn sau khi có kết quả."""
    messages = session.setdefault("game_messages", [])
    if any(item.id == message.id for item in messages):
        return
    messages.append(message)


async def send_word_game_reply(message, session, content, ping=True):
    sent = await send_reply(message, content, remember=False, ping=ping)
    record_word_game_message(session, sent)
    return sent


def find_word_game_log_channel(source_channel):
    guild = getattr(source_channel, "guild", None)
    if guild is None:
        return None
    if WORD_GAME_LOG_CHANNEL_ID:
        channel = guild.get_channel(WORD_GAME_LOG_CHANNEL_ID) or bot.get_channel(WORD_GAME_LOG_CHANNEL_ID)
        if channel is not None and hasattr(channel, "send"):
            return channel
    expected = normalize_word_game_text(WORD_GAME_LOG_CHANNEL_NAME)
    for channel in guild.text_channels:
        if normalize_word_game_text(channel.name) == expected:
            return channel
    return None


def build_word_game_transcript(session, won):
    result = "GIÀNH CHIẾN THẮNG" if won else "THUA CUỘC"
    lines = [
        "BIÊN BẢN TRÒ CHƠI NỐI TỪ",
        f"ID ván: {session.get('game_id', 'không rõ')}",
        f"Người chơi: {session.get('player_name', 'không rõ')}",
        f"ID người chơi: {session.get('player_id', 'không rõ')}",
        f"Máy chủ: {session.get('guild_name', 'không rõ')}",
        f"Kênh chơi: #{session.get('source_channel_name', 'không rõ')}",
        f"Tiền cược: {session.get('bet', 0):,}đ",
        f"Kết quả: {result}",
        "",
        "--- TOÀN BỘ TIN NHẮN CỦA VÁN ---",
    ]
    local_tz = datetime.timezone(datetime.timedelta(hours=7))
    for item in session.get("game_messages", []):
        created_at = getattr(item, "created_at", None)
        timestamp = created_at.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S") if created_at else "không rõ giờ"
        author = getattr(item, "author", None)
        author_name = getattr(author, "display_name", None) or getattr(author, "name", "không rõ")
        author_id = getattr(author, "id", "không rõ")
        content = (getattr(item, "content", "") or "").strip() or "[tin nhắn không có chữ]"
        lines.append(f"[{timestamp}] {author_name} ({author_id}): {content}")
    return "\n".join(lines) + "\n"


async def _send_raw_match_words(session, match_phrases):
    """Gửi file txt CHỈ có các cụm của ván (mỗi cụm 1 dòng, không thêm gì) vào kênh từ điển."""
    if not WORD_LIST_CHANNEL_ID or not match_phrases:
        return
    channel = bot.get_channel(WORD_LIST_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(WORD_LIST_CHANNEL_ID)
        except discord.HTTPException:
            return
    data = ("\n".join(match_phrases) + "\n").encode("utf-8")
    filename = f"{session.get('game_id', 'match')}.txt"
    try:
        await channel.send(file=discord.File(io.BytesIO(data), filename=filename))
    except discord.HTTPException as exc:
        log.warning("Không gửi được file từ của ván: %s", exc)


async def archive_and_cleanup_word_game(session, source_channel, won):
    """Gửi biên bản ván vào kênh lưu + file từ thô vào kênh từ điển; GIỮ NGUYÊN tin nhắn."""
    refresh_bot_avoid_words()  # tự học: sau mỗi ván cập nhật từ né (dẫn tới từ chết)
    match_phrases = collect_match_words(session)
    await _send_raw_match_words(session, match_phrases)
    log_channel = find_word_game_log_channel(source_channel)
    if log_channel is None:
        return False
    player_id = session.get("player_id")
    game_id = session.get("game_id")
    filename = f"{player_id}_{game_id}.txt"
    report = build_word_game_transcript(session, won).encode("utf-8")
    result_text = "giành chiến thắng" if won else "thua cuộc"
    try:
        await log_channel.send(
            f"<@{player_id}> {result_text} trong trò nối từ",
            file=discord.File(io.BytesIO(report), filename=filename),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
    except discord.HTTPException as exc:
        log.warning("Không gửi được biên bản nối từ %s: %s", game_id, exc)
        return False
    return True


def start_word_game_timer(key, session, bot_message, seconds=WORD_GAME_TURN_SECONDS):
    """Mỗi lần bot ra từ thì đếm giờ cho người chơi bằng emoji trên tin đó.

    seconds cho phép resume đồng hồ đúng chỗ dừng sau khi bot update (vd còn 6s).
    """
    cancel_word_game_timer(session)
    if bot_message is None:
        return
    seconds = max(1, min(WORD_GAME_TURN_SECONDS, int(seconds)))
    session["turn_id"] = session.get("turn_id", 0) + 1
    session["timer_task"] = asyncio.create_task(
        word_game_turn_countdown(key, session, session["turn_id"], bot_message, seconds)
    )


async def word_game_turn_countdown(key, session, turn_id, bot_message, seconds=WORD_GAME_TURN_SECONDS):
    """Đếm ngược bằng cách edit số cuối tin nhắn (thêu thùa... 10 -> 9 -> ... -> 0);
    về 0 mà chưa nối thì xử thua luôn."""
    # Nội dung gốc đã kết thúc bằng số giây; tách phần chữ để edit số đếm.
    base = re.sub(r"\s*\d+\s*$", "", bot_message.content or "")
    start = time.monotonic()
    try:
        for remaining in range(seconds, -1, -1):
            delay = start + (seconds - remaining) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if (
                word_game_sessions.get(key) is not session
                or session.get("turn_id") != turn_id
                or session["state"] != "active"
            ):
                return
            if remaining != seconds:  # tin gửi ra đã hiện sẵn số đầu
                try:
                    await bot_message.edit(content=f"{base} {remaining}")
                except discord.HTTPException:
                    pass
        async with word_game_locks[key]:
            if (
                word_game_sessions.get(key) is not session
                or session.get("turn_id") != turn_id
                or session["state"] != "active"
            ):
                return
            session.pop("timer_task", None)
            word_game_sessions.pop(key, None)
            save_word_game_sessions()
            profile = game_profiles.get(str(key[1]))
            if profile is None:
                return
            update_game_result(profile, won=False)
            sent = await bot_message.reply(
                f"<@{key[1]}> hết {WORD_GAME_TURN_SECONDS} giây rồi\nm thua mất {session['bet']:,}đ\n"
                f"số dư giờ: {profile['balance']:,}đ",
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            )
            record_word_game_message(session, sent)
            # Chủ bot: gắn feedback để kịp ý kiến vụ hết giờ.
            await attach_game_feedback(sent, session, "teach", session.get("last_word", ""))
            await archive_and_cleanup_word_game(session, bot_message.channel, won=False)
    except asyncio.CancelledError:
        raise
    except discord.HTTPException as exc:
        log.warning("Đếm giờ nối từ lỗi: %s", exc)


# ==================== EMOJI FEEDBACK (chỉ chủ bot) ====================
feedback_targets = {}  # message_id -> {"kind": "bot_move"/"teach", "phrase": str, "expires": ts}
pending_teach = {}     # prompt_message_id -> expires; chủ bot reply tin này để dạy từ


def _prune_feedback():
    now = time.time()
    for store in (feedback_targets, pending_teach):
        for mid in [m for m, v in store.items()
                    if (v["expires"] if isinstance(v, dict) else v) < now]:
            store.pop(mid, None)


async def attach_game_feedback(sent_message, session, kind, phrase):
    """Gắn emoji chấm từ. Ván chủ bot: 📝 (+☠️ trên câu bot nối). Ván người khác: ❌
    trên câu người chơi + câu bot để chủ bot cấm cụm."""
    if sent_message is None:
        return
    _prune_feedback()
    canonical = canonical_word_game_text(phrase) if phrase else ""
    is_phrase = kind in ("bot_move", "player_move") and len(canonical.split()) == 2
    if is_phrase and canonical in owner_verified_phrases:
        # Cụm đã xác minh: chỉ 🔒 để chủ bot biết khỏi kiểm lại (bấm 🔒 để bỏ xác minh).
        emojis = [VERIFIED_MARK_EMOJI]
    elif session.get("player_id") == OWNER_ID:
        # Ván chủ bot: câu BOT nối 📝(sai)+☠️(từ chết); câu CHỦ BOT gõ ☠️(từ chết, bot học);
        # tin chấm sai/bí 📝(dạy từ). Câu nối thêm ✅ để xác minh đúng.
        emojis = {
            "bot_move": [FEEDBACK_EMOJI, DEADWORD_EMOJI],
            "player_move": [DEADWORD_EMOJI],
            "teach": [FEEDBACK_EMOJI],
        }.get(kind, [])
        if is_phrase:
            emojis = emojis + [VERIFY_EMOJI]
    else:
        # Ván người khác: câu người chơi & bot ❌(cấm)+☠️(từ chết, bot học)+✅(xác minh); tin bí/thua 📝(dạy).
        emojis = {
            "bot_move": [DELETE_EMOJI, DEADWORD_EMOJI],
            "player_move": [DELETE_EMOJI, DEADWORD_EMOJI],
            "teach": [FEEDBACK_EMOJI],
        }.get(kind, [])
        if is_phrase:
            emojis = emojis + [VERIFY_EMOJI]
    if not emojis:
        return
    feedback_targets[sent_message.id] = {
        "kind": kind,
        "phrase": phrase or "",
        "expires": time.time() + FEEDBACK_EXPIRE_SECONDS,
    }
    for emoji in emojis:
        try:
            await sent_message.add_reaction(emoji)
        except discord.HTTPException:
            pass


async def register_word_game_strike(message, session, phrase_key, profane=False, foreign=False, reason=None):
    """Từ sai/vô nghĩa không thua ngay: khịa tăng dần, đủ 4 lần trong ván mới xử thua."""
    session["strikes"] = session.get("strikes", 0) + 1
    strikes = session["strikes"]
    if strikes >= WORD_GAME_MAX_STRIKES:
        await finish_word_game_loss(message, session, "sai lần 4 rồi, hết cứu")
        return
    if reason:
        # Lý do cụ thể (sai chữ đầu, cụm dùng rồi): nói thẳng để người chơi biết sửa gì,
        # không dùng câu 'là cái deo j' gây hiểu nhầm là từ vô nghĩa.
        text = reason
    elif foreign:
        text = "nối từ tiếng Việt thôi nha, tiếng Anh không tính" if strikes <= 2 else "??"
    elif profane:
        # Không lặp lại từ tục trong câu trả lời.
        text = "chửi tục không tính nha, nói cụm sạch đi" if strikes == 1 \
            else ("lại tục nữa, nhắc lần cuối đó" if strikes == 2 else "??")
    elif strikes == 1:
        text = f'từ "{phrase_key}" là cái deo j?'
    elif strikes == 2:
        text = f'"{phrase_key}" là cái j nữa?'
    else:
        text = "??"
    session["updated_at"] = time.time()
    sent = await send_word_game_reply(
        message, session, f"{text}... {WORD_GAME_TURN_SECONDS}",
    )
    # Chủ bot bấm 📝 trên tin chấm sai này = bot chấm oan, muốn dạy từ đúng.
    await attach_game_feedback(sent, session, "teach", phrase_key)
    start_word_game_timer((message.channel.id, message.author.id), session, sent)


async def _finish_cleanup(message, session, won):
    # Không xóa tin nữa: chỉ lưu biên bản, giữ nguyên tin trong kênh để còn bấm emoji.
    await archive_and_cleanup_word_game(session, message.channel, won)


async def finish_word_game_win(message, session):
    key = (message.channel.id, message.author.id)
    profile = game_profile_for(message.author)
    prize = session["bet"] * 2
    profile["balance"] += prize
    update_game_result(profile, won=True)
    cancel_word_game_timer(session)
    word_game_sessions.pop(key, None)
    # Bot bí ở 'last_word' -> ghi lại cho gheptu (từ người chơi nói mà bot không biết nối).
    record_bot_stuck_word(session.get("last_word", ""))
    # TỰ HỌC ngay: bot vừa kết ở từ đầu của cụm cuối rồi bị dồn tới từ bí -> NÉ từ đó lần sau.
    trap = canonical_word_game_text(session.get("current_phrase", "")).split()
    if len(trap) == 2 and trap[0] not in bot_avoid_end_words:
        bot_avoid_end_words.add(trap[0])
        save_owner_feedback()
    sent = await send_word_game_reply(
        message,
        session,
        f"t bí từ rồi\nm thắng +{prize:,}đ\nsố dư giờ: {profile['balance']:,}đ",
    )
    # Chủ bot bấm 📝 trên tin bí từ = muốn dạy bot cụm nối được ở đây.
    await attach_game_feedback(sent, session, "teach", session.get("last_word", ""))
    await _finish_cleanup(message, session, won=True)


async def finish_word_game_loss(message, session, reason=""):
    key = (message.channel.id, message.author.id)
    profile = game_profile_for(message.author)
    update_game_result(profile, won=False)
    cancel_word_game_timer(session)
    word_game_sessions.pop(key, None)
    prefix = f"{reason}\n" if reason else ""
    sent = await send_word_game_reply(
        message,
        session,
        f"{prefix}m thua mất {session['bet']:,}đ\nsố dư giờ: {profile['balance']:,}đ",
    )
    # Chủ bot bấm 📝 = xử thua này có vấn đề, muốn dạy/ý kiến.
    await attach_game_feedback(sent, session, "teach", session.get("last_word", ""))
    await _finish_cleanup(message, session, won=False)


async def handle_word_game_session(message, prompt, session):
    record_word_game_message(session, message)
    if session.get("frozen"):
        # Ván đang đóng băng chờ update xong; không xử lý lượt, không tính giờ.
        await send_word_game_reply(
            message, session, "ván đang đóng băng chờ t update xong, đợi đếm xong đã",
        )
        return
    now = time.time()
    if now - session["updated_at"] > WORD_GAME_TIMEOUT_SECONDS:
        if session["state"] == "active":
            await finish_word_game_loss(message, session, "quá 5 phút không nối, xử thua")
        else:
            word_game_sessions.pop((message.channel.id, message.author.id), None)
            await send_word_game_reply(message, session, "hết 5 phút rồi, t hủy kèo cược")
        return

    plain = normalize_word_game_text(prompt)
    if is_word_game_status_request(plain):
        if session["state"] == "waiting_bet":
            await send_word_game_reply(message, session, "đang chờ m nhập tiền cược")
        else:
            await send_word_game_reply(
                message,
                session,
                f'đang chơi, m phải nối 2 từ bắt đầu bằng "{session["last_word"]}"',
            )
        return
    if is_word_game_request(plain):
        state_text = "đang chờ m đặt cược rồi" if session["state"] == "waiting_bet" else "đang chơi rồi, nối câu hiện tại đi"
        await send_word_game_reply(message, session, state_text)
        return
    if plain in {"huy", "dung", "bo cuoc", "chiu"}:
        if session["state"] == "active":
            await finish_word_game_loss(message, session, "m bỏ cuộc")
        else:
            word_game_sessions.pop((message.channel.id, message.author.id), None)
            await send_word_game_reply(message, session, "ok hủy kèo")
        return

    profile = game_profile_for(message.author)
    if session["state"] == "waiting_bet":
        bet = parse_word_game_bet(prompt)
        cap = min(profile["balance"], WORD_GAME_MAX_BET)
        if bet is None or bet > cap:
            await send_word_game_reply(
                message,
                session,
                f"tiền cược phải từ 1đ tới {cap:,}đ (tối đa {WORD_GAME_MAX_BET:,}đ/ván)",
            )
            return
        profile["balance"] -= bet
        save_game_data()
        start_phrase = choose_word_game_start()
        words = canonical_word_game_text(start_phrase).split()
        session.update({
            "state": "active",
            "bet": bet,
            "current_phrase": start_phrase,
            "last_word": words[-1],
            "used_phrases": {canonical_word_game_text(start_phrase)},
            "used_required_words": {words[-1]},
            "strikes": 0,
            "started_at": now,
            "updated_at": now,
        })
        sent = await send_word_game_reply(
            message,
            session,
            f"ok cược {bet:,}đ · đúng 2 từ, nối chữ cuối, không lặp/đảo, "
            f"sai 4 lần thua, {WORD_GAME_TURN_SECONDS} giây mỗi lượt\n"
            f"\n**{start_phrase}**... {WORD_GAME_TURN_SECONDS}",
        )
        # Chủ bot bấm 📝 trên câu bot nối = từ đó sai, gạch khỏi từ điển.
        await attach_game_feedback(sent, session, "bot_move", start_phrase)
        start_word_game_timer((message.channel.id, message.author.id), session, sent)
        return

    phrase_key = canonical_word_game_text(prompt)
    words = phrase_key.split()
    if not words:
        # Tin chỉ có dấu câu/emoji kiểu "?" không phải lượt chơi, nhắc chứ không xử thua.
        await send_word_game_reply(
            message,
            session,
            f'đang chơi mà, nối 2 từ bắt đầu bằng "{session["last_word"]}" đi, muốn nghỉ thì nói bỏ cuộc',
        )
        return
    # Người chơi đã ra lượt thật, dừng đồng hồ 10 giây trong lúc chấm.
    cancel_word_game_timer(session)
    used_required_words = session.setdefault("used_required_words", {session["last_word"]})
    if any(word in WORD_GAME_BANNED_WORDS for word in words):
        await register_word_game_strike(message, session, phrase_key, profane=True)
        return
    if phrase_has_foreign(phrase_key):
        await register_word_game_strike(message, session, phrase_key, foreign=True)
        return
    if phrase_has_forbidden(phrase_key):
        bad = next(
            (w for w in canonical_word_game_text(phrase_key).split() if w in WORD_GAME_FORBIDDEN_WORDS),
            "đó",
        )
        await register_word_game_strike(
            message, session, phrase_key,
            reason=f'"{bad}" là từ cấm trong game, đổi từ khác đi',
        )
        return
    # Tách lý do strike để báo ĐÚNG lỗi: 'thừa' vs 'thửa' nhìn gần giống nhau,
    # báo 'là cái deo j' làm người chơi tưởng từ vô nghĩa rồi thua oan cả ván.
    if len(words) != 2:
        await register_word_game_strike(message, session, phrase_key)
        return
    if words[0] != session["last_word"]:
        await register_word_game_strike(
            message, session, phrase_key,
            reason=f'phải bắt đầu bằng "{session["last_word"]}" nha, m đang gõ "{words[0]}"',
        )
        return
    if phrase_key in session["used_phrases"]:
        await register_word_game_strike(
            message, session, phrase_key, reason="cụm đó dùng rồi, đổi cụm khác",
        )
        return
    # Cấm ĐẢO NGƯỢC cụm đã dùng (dán tem -> tem dán) để lách luật nối lại về từ cũ.
    if reverses_used_phrase(phrase_key, session["used_phrases"]):
        await register_word_game_strike(
            message, session, phrase_key, reason="cấm đảo ngược cụm đã dùng nha, đổi cụm khác",
        )
        return
    # Chống bug: người chơi ghép bừa từ + TỪ CỤT (làng ngợm, truyện ngợm) để ép bot bí.
    # Cụm kết ở từ không có đường nối mà KHÔNG phải cụm thật (trong từ điển/đã xác minh) -> chặn.
    if is_dead_end_exploit(phrase_key):
        await register_word_game_strike(
            message, session, phrase_key,
            reason=f'"{phrase_key}" không phải cụm có thật, đừng ghép bừa với từ cụt',
        )
        return
    # Từ bịa/vô nghĩa (có tiếng không tồn tại trong kho) -> AI phải xác nhận gắt, mơ hồ là chặn.
    strict = phrase_has_unknown_word(phrase_key)
    if await judge_word_game_phrase(phrase_key, source="người chơi", strict=strict) is False:
        await register_word_game_strike(
            message, session, phrase_key,
            reason=("cụm này vô nghĩa/từ bịa, chơi từ có thật đi" if strict else None),
        )
        return

    session["used_phrases"].add(phrase_key)
    used_required_words.add(words[-1])
    session["current_phrase"] = phrase_key
    session["last_word"] = words[-1]
    session["updated_at"] = now
    # Ván người khác: gắn ❌ dưới câu người chơi để chủ bot cấm cụm nếu thấy sai.
    await attach_game_feedback(message, session, "player_move", phrase_key)

    response = await choose_semantic_word_response(
        session["last_word"], session["used_phrases"], used_required_words,
    )
    if response is None:
        await finish_word_game_win(message, session)
        return

    response_normalized = canonical_word_game_text(response)
    response_words = response_normalized.split()
    session["used_phrases"].add(response_normalized)
    used_required_words.add(response_words[-1])
    session["current_phrase"] = response
    session["last_word"] = response_words[-1]
    session["updated_at"] = time.time()

    # Bot dồn được người chơi tới TỪ CHẾT (chủ bot đánh dấu ☠️): thua luôn, khỏi đếm giờ.
    if response_words[-1] in owner_dead_words:
        await finish_word_game_loss(
            message,
            session,
            f't nối: **{response}**\n"{response_words[-1]}" là từ chết ☠️ hết đường nối',
        )
        return

    sent = await send_word_game_reply(
        message,
        session,
        f"**{response}**... {WORD_GAME_TURN_SECONDS}",
    )
    # Chủ bot bấm 📝 trên câu bot nối = từ đó sai, gạch khỏi từ điển.
    await attach_game_feedback(sent, session, "bot_move", response)
    start_word_game_timer((message.channel.id, message.author.id), session, sent)


async def handle_word_game_intents(message, prompt, invoked, replied_message=None):
    """Return True khi profile/game đã xử lý và on_message phải dừng."""
    key = (message.channel.id, message.author.id)
    if key not in word_game_sessions and not invoked:
        return False
    # Khoá theo từng người chơi: tin sau phải chờ tin trước xử lý xong,
    # tránh 2 tin cùng lúc làm ván bị xử thua oan rồi biến mất.
    async with word_game_locks[key]:
        session = word_game_sessions.get(key)
        if session:
            await handle_word_game_session(message, prompt, session)
            save_word_game_sessions()
            return True
        if not invoked:
            return False

        plain = normalize_word_game_text(prompt)
        if is_word_game_status_request(plain):
            await send_reply(message, "không có ván nối từ nào đang chạy, gọi t chơi nối từ lại đi", remember=False)
            return True
        if (
            replied_message
            and bot.user
            and replied_message.author.id == bot.user.id
            and looks_like_word_game_reply(replied_message.content)
            and not is_word_game_request(plain)
            and not is_create_game_account_request(plain)
            and not is_game_profile_request(plain)
        ):
            await send_reply(
                message,
                "ván trong tin nhắn đó không còn chạy, chắc t vừa restart; gọi nối từ để mở ván mới",
                remember=False,
            )
            return True
        if is_create_game_account_request(plain):
            profile, created = get_or_create_game_profile(message.author)
            heading = "tạo xong tài khoản cho m rồi" if created else "m có tài khoản rồi đây"
            await send_reply(message, format_game_profile(profile, heading), remember=False)
            return True
        if is_daily_request(plain):
            error, info = await claim_daily_reward(message.author)
            await send_reply(message, error or format_daily_result(info), remember=False)
            return True
        if is_loan_request(plain, prompt):
            error, info = await take_loan(message.author, amount_from_intent(plain))
            await send_reply(
                message,
                error or f"vay {info['amount']:,}đ ok\nsố dư: {info['balance']:,}đ\n🔴 nợ: {info['debt']:,}đ",
                remember=False,
            )
            return True
        if is_repay_request(plain):
            error, info = await repay_loan(message.author, amount_from_intent(plain))
            if error:
                await send_reply(message, error, remember=False)
            else:
                tail = f"🔴 nợ còn: {info['debt']:,}đ" if info["debt"] else "hết nợ r, nhẹ nợ"
                await send_reply(message, f"trả {info['paid']:,}đ nợ\nsố dư: {info['balance']:,}đ\n{tail}", remember=False)
            return True
        if is_debt_request(plain, prompt):
            profile = game_profile_for(message.author)
            if profile is None:
                await send_reply(message, "tạo tài khoản trước đã, ping t rồi nói tạo tài khoản", remember=False)
            else:
                debt = int(profile.get("debt", 0))
                await send_reply(message, f"🔴 nợ của m: {debt:,}đ" if debt else "m sạch nợ, ngon", remember=False)
            return True
        if is_game_profile_request(plain):
            profile = game_profile_for(message.author)
            if profile is None:
                await send_reply(message, "tạo tài khoản trước đã, ping t rồi nói tạo tài khoản", remember=False)
            else:
                await send_reply(message, format_game_profile(profile), remember=False)
            return True
        if is_word_game_request(plain):
            profile = game_profile_for(message.author)
            if profile is None:
                await send_reply(message, "tạo tài khoản trước đã, ping t rồi nói tạo tài khoản", remember=False)
                return True
            if profile["balance"] <= 0:
                await send_reply(message, "m hết tiền rồi nên chưa đặt cược được", remember=False)
                return True
            session = {
                "state": "waiting_bet",
                "bet": 0,
                "current_phrase": "",
                "last_word": "",
                "used_phrases": set(),
                "strikes": 0,
                "started_at": time.time(),
                "updated_at": time.time(),
                "game_id": message.id,
                "player_id": message.author.id,
                "player_name": message.author.display_name,
                "guild_name": getattr(message.guild, "name", "không rõ"),
                "source_channel_name": getattr(message.channel, "name", str(message.channel.id)),
                "game_messages": [],
            }
            word_game_sessions[key] = session
            record_word_game_message(session, message)
            save_word_game_sessions()
            await send_word_game_reply(
                message,
                session,
                f"đặt bao nhiêu tiền, số dư m có {profile['balance']:,}đ\n"
                "thắng ăn gấp đôi thua mất cược",
            )
            return True
        return False


# ==================== ZUN OS ====================
ZUN_OS_TIMEOUT_SECONDS = 30
ZUN_OS_VERSION = "zun os v1.0"
ZUN_OS_TZ = datetime.timezone(datetime.timedelta(hours=7))
ZUN_OS_OPEN_RE = re.compile(r"(?:(?:e|alo)\s+)?(?:zun\w*\s+)?mo may")


class ZunOSView(discord.ui.View):
    """Máy tính ảo: 1 embed + button, mọi app edit tại chỗ, 30s không bấm là sập nguồn.

    discord.py tự reset đồng hồ timeout mỗi lần bấm nút hợp lệ nên không cần tự đếm.
    """

    def __init__(self, owner, gid):
        super().__init__(timeout=ZUN_OS_TIMEOUT_SECONDS)
        self.owner = owner
        self.gid = gid
        self.message = None
        self.booted_at = time.time()
        self.battery = random.randint(37, 98)
        self.show_home_buttons()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("máy này không phải của m", ephemeral=True)
            return False
        return True

    # ---- dựng màn hình ----
    def _status_bar(self):
        now = datetime.datetime.now(ZUN_OS_TZ).strftime("%H:%M:%S")
        uptime = int(time.time() - self.booted_at)
        battery = max(5, self.battery - uptime // 30)
        return f"🕒 {now} · 🔋 pin {battery}% · ⏱ bật được {uptime}s"

    @staticmethod
    def _frame(body):
        return f"```\n{body}\n```"

    def _apply_footer(self, embed):
        embed.add_field(name="trạng thái", value=self._status_bar(), inline=False)
        embed.set_footer(text=f"{ZUN_OS_VERSION} · auto shutdown: {ZUN_OS_TIMEOUT_SECONDS}s")
        return embed

    def build_home_embed(self):
        embed = discord.Embed(
            title="🖥️ ZUN OS",
            description=(
                self._frame(
                    "┌──────────────────────────┐\n"
                    "│     🟢 MÁY ĐANG BẬT      │\n"
                    "└──────────────────────────┘"
                )
                + f"\ndesktop của **{self.owner.display_name}** — chọn app để mở"
            ),
            color=0x57F287,
        )
        return self._apply_footer(embed)

    def build_off_embed(self, reason):
        embed = discord.Embed(
            title="🖥️ ZUN OS",
            description=(
                self._frame(
                    "┌──────────────────────────┐\n"
                    "│      ⚫ MÁY ĐÃ TẮT       │\n"
                    "└──────────────────────────┘"
                )
                + f"\n{reason}"
            ),
            color=0xED4245,
        )
        embed.set_footer(text=f"{ZUN_OS_VERSION} · nhắn 'mở máy' để bật lại")
        return embed

    def build_app_embed(self, app):
        embed = discord.Embed(title="🖥️ ZUN OS", color=0x5865F2)
        if app == "profile":
            profile = game_profile_for(self.owner)
            if profile is None:
                body = "chưa có tài khoản game\nping t rồi nói: tạo tài khoản"
            else:
                profile["level"] = 1 + profile["wins"] // 5
                total = profile["wins"] + profile["losses"]
                rate = 0 if total == 0 else profile["wins"] / total * 100
                body = (
                    f"user : {profile['name']}\n"
                    f"level: {profile['level']}\n"
                    f"win  : {profile['wins']}\n"
                    f"lose : {profile['losses']}\n"
                    f"rate : {rate:.0f}%"
                )
            embed.add_field(name="👤 Profile", value=self._frame(body), inline=False)
        elif app == "wallet":
            profile = game_profile_for(self.owner)
            balance = profile["balance"] if profile else 0
            body = (
                f"số dư: {balance:,}đ\n"
                "──────────────────────\n"
                "/trade   chuyển tiền\n"
                "nối từ   thắng ăn x2"
            )
            embed.add_field(name="💰 Ví tiền", value=self._frame(body), inline=False)
        elif app == "game":
            body = (
                "NỐI TỪ CƯỢC TIỀN\n"
                "──────────────────────\n"
                "mở ván : nhắn 'nối từ'\n"
                "luật   : 2 từ, nối chữ cuối\n"
                "10 giây mỗi lượt\n"
                "sai 4 lần trong ván là thua"
            )
            embed.add_field(name="🎮 Mini game", value=self._frame(body), inline=False)
        elif app == "help":
            embed.add_field(name="📖 Help", value=build_help_text()[:1024], inline=False)
        elif app == "settings":
            body = (
                f"mood server: {guild_mood.get(self.gid, 'normal')}\n"
                f"ping       : {round(bot.latency * 1000)}ms\n"
                f"os         : {ZUN_OS_VERSION}\n"
                "đổi mood   : /mood (admin)"
            )
            embed.add_field(name="⚙️ Cài đặt", value=self._frame(body), inline=False)
        return self._apply_footer(embed)

    # ---- nút ----
    def _add_button(self, emoji, label, style, row, handler):
        button = discord.ui.Button(emoji=emoji, label=label, style=style, row=row)

        async def callback(interaction):
            await handler(interaction)

        button.callback = callback
        self.add_item(button)

    def show_home_buttons(self):
        self.clear_items()
        primary, secondary = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
        self._add_button("👤", "Profile", primary, 0, lambda i: self.open_app(i, "profile"))
        self._add_button("🎮", "Mini game", primary, 0, lambda i: self.open_app(i, "game"))
        self._add_button("💰", "Ví tiền", primary, 0, lambda i: self.open_app(i, "wallet"))
        self._add_button("📖", "Help", secondary, 1, lambda i: self.open_app(i, "help"))
        self._add_button("⚙️", "Cài đặt", secondary, 1, lambda i: self.open_app(i, "settings"))
        self._add_button("❌", "Tắt máy", discord.ButtonStyle.danger, 1, self.shutdown)

    def show_app_buttons(self):
        self.clear_items()
        self._add_button("🏠", "Màn hình chính", discord.ButtonStyle.secondary, 0, self.go_home)
        self._add_button("❌", "Tắt máy", discord.ButtonStyle.danger, 0, self.shutdown)

    # ---- hành vi ----
    async def open_app(self, interaction, app):
        self.show_app_buttons()
        await interaction.response.edit_message(embed=self.build_app_embed(app), view=self)

    async def go_home(self, interaction):
        self.show_home_buttons()
        await interaction.response.edit_message(embed=self.build_home_embed(), view=self)

    async def shutdown(self, interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.build_off_embed("hẹn gặp lại"), view=self,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=self.build_off_embed(
                        f"không ai bấm gì {ZUN_OS_TIMEOUT_SECONDS} giây nên t tắt cho đỡ tốn điện"
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass


async def open_zun_os(message):
    view = ZunOSView(message.author, get_gid(message))
    view.message = await message.reply(
        embed=view.build_home_embed(),
        view=view,
        mention_author=False,
    )


def _split_text_piece(text, limit, preserve_code=False):
    """Chia tại newline trước; không làm mất indentation của code."""
    pieces = []
    remaining = text.strip("\n") if preserve_code else text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0 and not preserve_code:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit

        piece = remaining[:cut]
        if preserve_code:
            pieces.append(piece)
            remaining = remaining[cut + 1:] if cut < len(remaining) and remaining[cut] == "\n" else remaining[cut:]
        else:
            pieces.append(piece.rstrip())
            remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining if preserve_code else remaining.strip())
    return [piece for piece in pieces if piece]


def split_chunks(text):
    """Chia tin Discord nhưng đóng/mở lại code fence để code không bị vỡ."""
    text = (text or "").strip() or "..."
    if len(text) <= CHUNK_SIZE:
        return [text]

    units = []
    cursor = 0
    code_pattern = re.compile(r"```([^\n`]*)\n?([\s\S]*?)```")
    for match in code_pattern.finditer(text):
        plain = text[cursor:match.start()]
        units.extend(_split_text_piece(plain, CHUNK_SIZE))

        language = match.group(1).strip()
        code = match.group(2).strip("\n")
        opening = f"```{language}\n" if language else "```\n"
        closing = "\n```"
        code_limit = CHUNK_SIZE - len(opening) - len(closing)
        for code_piece in _split_text_piece(code, code_limit, preserve_code=True):
            units.append(opening + code_piece + closing)
        cursor = match.end()

    units.extend(_split_text_piece(text[cursor:], CHUNK_SIZE))
    if not units:
        return _split_text_piece(text, CHUNK_SIZE)

    chunks = []
    current = ""
    for unit in units:
        candidate = f"{current}\n{unit}".strip() if current else unit
        if current and len(candidate) > CHUNK_SIZE:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def on_cooldown(user_id, channel_id=None):
    """Giữ cooldown theo user và toàn channel trước khi dành một lượt gọi AI."""
    now = time.time()
    if now - last_ai_call.get(user_id, 0) < COOLDOWN_SECONDS:
        return True
    if channel_id is not None and now - last_channel_ai_call.get(channel_id, 0) < CHANNEL_COOLDOWN_SECONDS:
        return True
    last_ai_call[user_id] = now
    if channel_id is not None:
        last_channel_ai_call[channel_id] = now
    return False


def on_quick_cooldown(channel_id, user_id):
    """Chống spam nhẹ cho greeting và câu đáp nhanh, tách khỏi cooldown AI."""
    key = (channel_id, user_id)
    now = time.time()
    if now - last_quick_call.get(key, 0) < 0.5:
        return True
    last_quick_call[key] = now
    return False


def remember_channel_message(channel_id, author_name, content):
    content = re.sub(r"\s+", " ", (content or "")).strip()
    if content:
        items = recent_channel_messages[channel_id]
        if items and author_name == "Zun" and items[-1] == {"author": "Zun", "content": content[:300]}:
            return
        items.append({"author": author_name, "content": content[:300]})


async def send_reply(message, content, remember=True, ping=True):
    content = (content or "...")[:2000]
    sent = await message.reply(
        content,
        mention_author=ping,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=False,
            replied_user=ping,
        ),
    )
    if remember:
        remember_channel_message(message.channel.id, "Zun", content)
    return sent


async def send_reply_chunks(message, text):
    chunks = split_chunks(text)
    if not chunks:
        return
    # Cau tra loi dai chia nhieu tin thi chi ping o tin dau, khoi doi thong bao.
    for index, part in enumerate(chunks):
        await send_reply(message, part, ping=index == 0)


async def send_roast_reply(message, content):
    content = (content or "...")[:2000]
    sent = await message.reply(
        content,
        mention_author=True,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=True,
            replied_user=True,
        ),
    )
    remember_channel_message(message.channel.id, "Zun", content)
    return sent


def build_channel_context(channel_id):
    items = recent_channel_messages.get(channel_id)
    if not items:
        return ""
    lines = ["[Tin nhắn gần đây trong kênh]"]
    lines.extend(f"{item['author']}: {item['content']}" for item in items)
    return "\n".join(lines)


def choose_short_reply(channel_id, choices):
    """Không chọn lại đúng câu quick reply vừa gửi trong cùng channel."""
    previous = last_bot_short_reply.get(channel_id)
    pool = [choice for choice in choices if choice != previous] or list(choices)
    reply = random.choice(pool)
    last_bot_short_reply[channel_id] = reply
    return reply


def gf_stretch(base, ch="o", lo=3, hi=8):
    """Kéo dài chữ cuối kiểu saoooo với độ dài random cho giống người thật."""
    return base + ch * random.randint(lo, hi)


def gf_greeting():
    """Nấm gọi mà không nói gì: câu ngắn kiểu Zun tự chat, thỉnh thoảng nũng."""
    pool = [
        "sao e",
        "sao e",
        "sao ku",
        "nè",
        "gì nữa béo",
        gf_stretch("sao"),
        gf_stretch("sao"),
        gf_stretch("sao") + " đó",
        "j đó",
        "hử",
        "nhò",
    ]
    return random.choice(pool)


def randomize_sao(text):
    """Model hay viết saooo với độ dài cố định; random lại số chữ o cho tự nhiên."""
    return re.sub(
        r"(?i)\bsao(o+)\b",
        lambda m: "sao" + "o" * random.randint(2, 7),
        text,
    )


def nam_situation_context(text):
    """Nhận ra Nấm đang nhắc vụ gì để Zun không hỏi lại từ đầu, phản ứng đúng vibe."""
    plain = normalize_chat_text(text)
    hints = []
    if any(k in plain for k in (
        "nhang nhang", "noi tu", "luon", "bot luon", "file thua",
        "thang dau", "mang em", "200k", "200 k", "xu thua", "thua mat",
    )):
        hints.append(
            "Nấm đang nhắc vụ mini game nối từ (bot từng nối ra 'nhàng nhàng' rồi xử Nấm thua mất tiền cược). "
            "Zun HIỂU NGAY, không hỏi lại từ đầu. Trêu nhẹ trước rồi nhận check/fix; nếu Nấm có ảnh/log thì bảo gửi; "
            "lỗi bot thật thì nhận sửa/bù. Không giải thích dài."
        )
    if any(k in plain for k in (
        "daily", "hang ngay", "phan thuong", "owodaily", "owo",
        "vay", "no do", "so do", "muon tien",
    )):
        hints.append(
            "Nấm đang góp ý tính năng cho mini game (phần thưởng hằng ngày kiểu owodaily, vay tiền, nợ/số đỏ). "
            "Coi đây là góp ý nghiêm túc nhưng đáp theo vibe Discord kiểu 'ý này được', 'để t nhét vào'. "
            "Thiếu thông tin thì hỏi ngắn 1 câu (vd daily bao nhiêu tiền)."
        )
    return "\n".join(hints)


def is_sweet_message(text):
    """Nấm có nói ngọt/sến trước không; chỉ khi đó Zun mới được sến lại."""
    t = (text or "").lower()
    if re.search(r"\b(iu|vk|ck|hun|moa+|muah+)\b", t):
        return True
    return any(w in t for w in ("yêu", "thương", "nhớ", "hôn", "vợ", "chồng", "cưng", "sến"))


def normalize_chat_text(text):
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9@]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Model nhỏ hay tính sai; toán đơn giản thì bot tự tính trong code cho luôn đúng.
_MATH_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_math(node):
    if isinstance(node, ast.Expression):
        return _safe_eval_math(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _MATH_OPS:
        left, right = _safe_eval_math(node.left), _safe_eval_math(node.right)
        if type(node.op) in (ast.Pow,) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("số mũ quá lớn")
        return _MATH_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _MATH_OPS:
        return _MATH_OPS[type(node.op)](_safe_eval_math(node.operand))
    raise ValueError("biểu thức không hợp lệ")


def try_eval_math(prompt):
    """Trả về chuỗi kết quả nếu prompt là phép tính số học thuần, ngược lại None."""
    expr = (prompt or "").strip().lower()
    # Bỏ phần hỏi đuôi và đổi từ tiếng Việt/ký hiệu sang toán tử.
    expr = re.sub(r"(bang|bằng)\s*(bao nhieu|bao nhiêu|may|mấy|nhieu|nhiêu)?\s*\??$", "", expr).strip()
    expr = re.sub(r"[=?]+\s*$", "", expr).strip()
    replacements = {
        "cộng": "+", "cong": "+", "trừ": "-", "tru": "-",
        "nhân": "*", "nhan": "*", "chia": "/", "mũ": "**", "mu": "**",
        "x": "*", "×": "*", "·": "*", "÷": "/", "^": "**",
    }
    for word, sym in replacements.items():
        expr = re.sub(rf"(?<=\d)\s*{re.escape(word)}\s*(?=[\d(])", sym, expr)
        expr = expr.replace(f" {word} ", sym)
    expr = expr.replace(",", ".")  # 3,5 -> 3.5
    if not expr or not re.fullmatch(r"[\d\s+\-*/%().]+", expr):
        return None
    if not re.search(r"\d[\s]*[+\-*/%]", expr):  # phải có ít nhất 1 phép tính
        return None
    try:
        result = _safe_eval_math(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(result, float):
        if result.is_integer():
            result = int(result)
        else:
            result = round(result, 6)
    return str(result)


FACTUAL_MARKERS = (
    "la gi", "la j", "nghia la", "dinh nghia", "bao nhieu", "may", "tinh",
    "tai sao", "vi sao", "the nao", "nhu the nao", "lam sao", "cach",
    "ai la", "khi nao", "o dau", "cong thuc", "dich", "nghia cua",
    "co phai", "dung khong", "khac nhau", "phan biet", "bang bao nhieu",
)


def is_factual_question(prompt):
    """Câu hỏi kiến thức/thực tế cần trả lời ĐÚNG, không phải cà khịa."""
    plain = normalize_chat_text(prompt)
    if not plain or is_short_insult(prompt):
        return False
    if "?" in (prompt or "") and len(plain.split()) >= 2:
        return True
    return any(marker in plain for marker in FACTUAL_MARKERS)


def has_code_action(text):
    raw = (text or "").lower()
    return any(action in raw for action in CODE_ACTION_WORDS)


def is_code_request(text):
    raw = (text or "").lower()
    if has_code_action(text):
        return True
    if "```" in raw or "traceback" in raw or "error" in raw:
        return True
    return False


def is_help_or_command_question(text):
    t = normalize_chat_text(text)
    return (
        "helpzun" in t
        or "commands" in t
        or "lenh" in t
        or "huong dan" in t
        or "cach dung" in t
    )


def requires_code_block(text):
    """Phân biệt yêu cầu tạo/fix code với câu hỏi khái niệm kỹ thuật."""
    return has_code_action(text)


def infer_code_language(text):
    t = (text or "").lower()
    if any(word in t for word in ("lua", "roblox", "local script", "server script", "module script")):
        return "lua"
    if "python" in t or "discord.py" in t:
        return "python"
    if "javascript" in t or re.search(r"\bjs\b", t):
        return "javascript"
    return ""


def ensure_code_fenced(answer, prompt):
    """Nếu model có trả code nhưng quên fence thì bọc lại với ngôn ngữ phù hợp."""
    if "```" in answer or not requires_code_block(prompt):
        return answer
    code_markers = ("\nlocal ", "\ndef ", "\nclass ", "\nfunction ", "\nimport ", "\nfrom ", ":connect(", " = ")
    probe = "\n" + answer.lower()
    if not any(marker in probe for marker in code_markers):
        return answer
    language = infer_code_language(prompt)
    return f"```{language}\n{answer.strip()}\n```"


def is_technical_prompt(prompt):
    if is_code_request(prompt):
        return True
    plain = normalize_chat_text(prompt)
    markers = tuple(normalize_chat_text(word) for word in CODE_CONTEXT_WORDS) + (
        "bug", "loi", "log", "function", "class", "database", "sql",
        "terminal", "json", "regex", "docker", "git",
    )
    return "```" in prompt or any(marker in plain for marker in markers)


def bro_used_recently(channel_id):
    bot_lines = [
        item["content"] for item in recent_channel_messages.get(channel_id, ())
        if item["author"] == "Zun"
    ]
    return any(re.search(r"\bbro\b", line, re.IGNORECASE) for line in bot_lines[-2:])


PROVIDER_LEAK_PATTERNS = (
    r"powered\s+by.{0,100}\b(?:claude|anthropic|openai|chatgpt|gemini|api)\b",
    r"\bunofficial\s+(?:claude|anthropic|openai|chatgpt|gemini).{0,40}\bapi\b",
    r"\bnot\s+affiliated\s+with\s+(?:anthropic|openai|google)\b",
    r"\b(?:i am|i'm|tôi là|mình là)\s+(?:an?\s+)?(?:ai|claude|chatgpt|gemini|language model)\b",
)


def sanitize_ai_output(text):
    """Chặn branding/backend injection và che secret nếu model lỡ đưa vào output."""
    text = text or ""
    if any(re.search(pattern, text, re.IGNORECASE | re.DOTALL) for pattern in PROVIDER_LEAK_PATTERNS):
        log.warning("Đã chặn câu trả lời làm lộ branding/backend AI")
        return "câu kia lỗi format r, nói lại coi"
    text = re.sub(
        r"(?i)\b(DISCORD_TOKEN|ANTHROPIC_API_KEY|CLAUDE_API_KEY|GEMINI_API_KEY|ZAI_API_KEY)\s*([:=])\s*([^\s`]+)",
        lambda match: f"{match.group(1)}{match.group(2)}[đã ẩn]",
        text,
    )
    text = re.sub(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{10,}\b", "[token đã ẩn]", text)
    text = re.sub(
        r"\b[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27,}\b",
        "[token đã ẩn]",
        text,
    )
    return text


def _strip_speaker_label(text):
    # Model nhỏ hay lặp format transcript "zun: ..." / "mrzunniderrs: ..."; bỏ tiền tố đó.
    return re.sub(r"^\s*(?:(?:zun\w*|mrzunniderrs)\s*[:：]\s*)+", "", text or "", flags=re.IGNORECASE)


# Model đôi khi phun suy nghĩ/meta thay vì câu trả lời. Chặn hẳn (số ít/nhiều, Anh/Việt).
_META_LEAD_RE = re.compile(
    r"^\s*[\*\#\-\s]*"
    r"(?:thoughts?|thinking|reasoning|analysis|internal|reflection|note|meta|plan"
    r"|suy nghĩ|phân tích|nhận định|đánh giá)"
    r"\b[^:：\n]{0,30}[:：]",
    re.IGNORECASE,
)
# Kể chuyện ngôi thứ 3 về chính Zun/User thay vì đóng vai (Anh + Việt).
_META_NARRATE_RE = re.compile(
    r"^\s*(?:the\s+user\b|user\b|người dùng\b|nấm\b|zun[''`]?s?\b|nam\b)"
    r".{0,80}?"
    r"\b(?:is|are|was|were|has|have|should|would|will|wants?|asking|trying"
    r"|đang|vừa|nên|đã|muốn|tag|gửi|nói|persona|phản ứng)\b",
    re.IGNORECASE,
)


# Từ chức năng tiếng Anh hay xuất hiện trong câu reasoning; Zun không bao giờ chat kiểu này.
_EN_META_WORDS_RE = re.compile(
    r"\b(the|is|are|was|were|has|have|can|could|should|would|will|since|given"
    r"|just|said|says|user|response|variation|repeat|pattern|comeback|option"
    r"|established|final|good|slight)\b",
    re.IGNORECASE,
)


def looks_like_meta(text):
    """Câu trả lời có phải là suy nghĩ/kể chuyện (không phải lời Zun chat) không."""
    t = (text or "").strip()
    if not t:
        return False
    if _META_LEAD_RE.match(t):
        return True
    # Nhắc tới "persona" gần như luôn là meta phân tích, không phải lời chat.
    if re.search(r"\bpersona\b", t, re.IGNORECASE):
        return True
    # Câu dài nhiều từ chức năng tiếng Anh (Given the pattern..., Since the user...):
    # Zun chỉ chat tiếng Việt nên đây chắc chắn là reasoning bị lộ.
    if len(t) > 60 and len(_EN_META_WORDS_RE.findall(t)) >= 4:
        return True
    # Câu kể chuyện ngôi thứ 3 về chính Zun/User (dài dòng phân tích).
    return bool(_META_NARRATE_RE.match(t) and len(t) > 40)


def strip_meta_reasoning(text):
    """Cắt khối THOUGHT/phân tích ở đầu; trả phần lời chat thật nếu model có kèm sau đó."""
    t = (text or "").strip()
    if not t:
        return t
    if _META_LEAD_RE.match(t):
        # Ưu tiên tách theo dòng trống; không có thì thử tách theo nhãn lời thật (REPLY/ANSWER/Zun nói).
        after = re.split(r"\n\s*\n", t, maxsplit=1)
        if len(after) > 1:
            t = after[1].strip()
        else:
            reply = re.split(r"(?i)\b(?:reply|answer|response|final|zun (?:nói|đáp)|câu trả lời)\s*[:：]", t, maxsplit=1)
            t = reply[1].strip() if len(reply) > 1 else ""
    return _strip_speaker_label(t).strip()


def clean_answer(text):
    """Bỏ dấu ngoặc kép model tự thêm bọc câu trả lời (lỗi kiểu: xin chào.")"""
    text = sanitize_ai_output(text).strip()
    text = _strip_speaker_label(text)
    quotes = '"\u201c\u201d'
    # bọc nguyên câu trong ngoặc kép -> bỏ cả 2 đầu
    if len(text) >= 2 and text[0] in quotes and text[-1] in quotes:
        text = text[1:-1].strip()
    # dấu " lẻ ở cuối câu -> cắt
    elif text and text[-1] in quotes and sum(text.count(q) for q in quotes) % 2 == 1:
        text = text[:-1].rstrip()
    # Nhãn có thể nằm trong ngoặc kép, bỏ lại lần nữa sau khi tháo ngoặc.
    return _strip_speaker_label(text).strip()


def style_clean_answer(text, channel_id=None, technical=False):
    """Dọn giọng chat nhưng giữ nguyên code block."""
    text = clean_answer(text)
    if not text:
        return "t chịu"

    # Không sửa nội dung code; chỉ dọn phần văn bản nằm ngoài fenced code block.
    parts = re.split(r"(```[\s\S]*?```)", text)
    avoid_bro = channel_id is not None and bro_used_recently(channel_id)
    bro_seen = 0
    for index in range(0, len(parts), 2):
        part = parts[index]

        def replace_bro(match):
            nonlocal bro_seen
            bro_seen += 1
            if avoid_bro or bro_seen > 1:
                return ""
            return match.group(0)

        part = re.sub(r"\bbro\b", replace_bro, part, flags=re.IGNORECASE)
        part = re.sub(r"[ \t]{2,}", " ", part)
        part = re.sub(r"\s+([,!.?])", r"\1", part)
        parts[index] = part.strip()

    text = "\n".join(part for part in parts if part)

    if technical and text.count("```") % 2 == 1:
        text += "\n```"

    if "```" not in text and not technical:
        # Persona đã ép ngắn; đây là lưới an toàn cho lúc model trượt sang văn mẫu.
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
        text = " ".join(sentences[:3])
        if len(text) > 400:
            text = text[:397].rsplit(" ", 1)[0].rstrip(" ,.!?") + "..."

    if "\n" not in text and len(text) <= 100:
        text = text.rstrip(".")
    return text.strip() or "t chịu"


# Không lộ chuyện API/quota với người trong server; bot cáo bận như người thật.
RATE_LIMIT_EXCUSES = [
    "t đi ăn cơm đây, tí quay lại",
    "mệt r t nghỉ xíu, lát gọi lại",
    "t đang bận render tí, lát rep",
    "buồn ngủ vãi t chợp mắt tí",
    "t afk xíu có việc, tí về",
    "não t quá tải r, cho nghỉ tí đã",
    "t đi vệ sinh cái, gọi sau đi",
]
ERROR_EXCUSES = [
    "mạng t lag vãi, tí hỏi lại",
    "lag quá, nói lại coi",
    "t đơ tí, hỏi lại phát",
]


class AllKeysExhaustedError(Exception):
    """Tất cả Gemini key đều hết lượt; bot im, không trả lời."""


def claude_discord_error(error):
    # Hết key thường được chặn im từ pre-check; ca hiếm cạn giữa chừng thì cáo bận tự nhiên.
    if isinstance(error, (AllKeysExhaustedError, aiohttp.ClientConnectorError, ConnectionError)):
        return random.choice(RATE_LIMIT_EXCUSES)
    return random.choice(ERROR_EXCUSES)


# ---- Xoay Gemini key: key dính 429/quota bị nghỉ GEMINI_KEY_COOLDOWN giây ----
_gemini_key_cooldown = {}   # key -> timestamp hết cooldown
_gemini_key_index = 0       # con trỏ round-robin


def available_gemini_keys():
    now = time.time()
    return [k for k in GEMINI_KEYS if _gemini_key_cooldown.get(k, 0) <= now]


def gemini_keys_available():
    return bool(available_gemini_keys())


def ai_available_for(user):
    """Chủ bot có GLM z.ai riêng nên không phụ thuộc Gemini; người khác hết key Gemini là hết dùng."""
    if user is not None and getattr(user, "id", None) == OWNER_ID and ZAI_API_KEY:
        return True
    return gemini_keys_available()


def _mark_key_exhausted(key):
    _gemini_key_cooldown[key] = time.time() + GEMINI_KEY_COOLDOWN
    log.warning("Gemini key ...%s hết lượt, nghỉ %ss", key[-4:], GEMINI_KEY_COOLDOWN)


def _keys_in_rotation():
    """Danh sách key còn dùng được, bắt đầu từ vị trí round-robin cho cân tải."""
    keys = available_gemini_keys()
    if not keys:
        return []
    start = _gemini_key_index % len(keys)
    return keys[start:] + keys[:start]


def _to_gemini_payload(messages):
    """Đổi list {role, content} (có thể chứa block ảnh) sang body Gemini generateContent."""
    system_parts = []
    contents = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"] if isinstance(m["content"], str) else "")
            continue
        role = "model" if m["role"] == "assistant" else "user"
        content = m["content"]
        parts = []
        if isinstance(content, str):
            parts.append({"text": content})
        else:
            for block in content:
                if block.get("type") == "text":
                    parts.append({"text": block.get("text", "")})
                elif block.get("type") == "image":
                    src = block.get("source", {})
                    if src.get("data"):
                        parts.append({"inline_data": {
                            "mime_type": src.get("media_type", "image/png"),
                            "data": src["data"],
                        }})
        contents.append({"role": role, "parts": parts})
    body = {"contents": contents}
    if system_parts:
        body["system_instruction"] = {"parts": [{"text": "\n\n".join(p for p in system_parts if p)}]}
    return body


def _to_openai_payload(messages):
    """Đổi list {role, content} (block Anthropic) sang messages OpenAI-compatible cho z.ai.

    Ảnh chuyển thành image_url data-url (model vision đọc được, model text thì z.ai tự bỏ/lỗi
    -> caller rơi về Gemini).
    """
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "image":
                src = block.get("source", {})
                if src.get("data"):
                    parts.append({"type": "image_url", "image_url": {
                        "url": f"data:{src.get('media_type', 'image/png')};base64,{src['data']}",
                    }})
        out.append({"role": m["role"], "content": parts})
    return out


async def _zai(messages, max_tokens=CHAT_MAX_TOKENS, temperature=0.85):
    """Gọi GLM của z.ai (chỉ dành cho chủ bot). Lỗi thì raise để caller rơi về Gemini."""
    body = {
        "model": ZAI_MODEL,
        "messages": _to_openai_payload(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {ZAI_API_KEY}"}
    timeout = aiohttp.ClientTimeout(total=GEMINI_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(ZAI_API_URL, json=body, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    text = ((choices[0].get("message") or {}).get("content")) or ""
    return clean_answer(text)


# Bot cà khịa nhẹ; nới bộ lọc để đừng chặn oan, nhưng vẫn giữ mức chặn cao cho nội dung nặng.
_GEMINI_SAFETY = [
    {"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
        "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


async def _claude(messages, max_tokens=CHAT_MAX_TOKENS, temperature=0.85, thinking_budget=0, model=None, owner=False):
    """Gọi Gemini API, xoay qua các key. Tên hàm giữ nguyên cho khỏi sửa nơi gọi.

    thinking_budget bỏ (Gemini flash không có). Hết sạch key -> AllKeysExhaustedError.
    owner=True: chủ bot chat -> dùng GLM z.ai riêng, không đốt key Gemini của mọi người;
    GLM lỗi mới rơi về Gemini.
    """
    global _gemini_key_index
    if owner and ZAI_API_KEY:
        try:
            return await _zai(messages, max_tokens=max_tokens, temperature=temperature)
        except Exception as exc:
            log.warning("z.ai GLM lỗi (%s), chủ bot tạm rơi về Gemini", type(exc).__name__)
    keys = _keys_in_rotation()
    if not keys:
        raise AllKeysExhaustedError()
    body = _to_gemini_payload(messages)
    body["generationConfig"] = {"temperature": temperature, "maxOutputTokens": max_tokens}
    body["safetySettings"] = _GEMINI_SAFETY
    use_model = model if (model and str(model).startswith("gemini")) else GEMINI_MODEL
    timeout = aiohttp.ClientTimeout(total=GEMINI_TIMEOUT)
    last_error = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for key in keys:
            url = f"{GEMINI_API_BASE}/{use_model}:generateContent?key={key}"
            try:
                async with session.post(url, json=body) as resp:
                    if resp.status in (429, 403) or resp.status == 400:
                        # 429 quota, 403 key hỏng/hết hạn, 400 key sai -> bỏ key này.
                        detail = (await resp.text())[:200]
                        last_error = f"{resp.status} {detail}"
                        _mark_key_exhausted(key)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
            except aiohttp.ClientResponseError as exc:
                last_error = str(exc)
                if exc.status in (429, 403, 400, 500, 503):
                    _mark_key_exhausted(key)
                    continue
                raise
            # Thành công: nhớ vị trí kế cho lần sau, trả kết quả.
            _gemini_key_index = (GEMINI_KEYS.index(key) + 1) % max(1, len(GEMINI_KEYS))
            candidates = data.get("candidates") or []
            if not candidates:
                return ""  # bị safety chặn hoặc rỗng
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            return clean_answer(text)
    # Chạy hết vòng mà key nào cũng lỗi/hết.
    log.warning("Tất cả Gemini key đều lỗi/hết: %s", last_error)
    raise AllKeysExhaustedError()


async def ai_chat(gid, key, prompt, extra_context="", user_name="", image_blocks=None, force_thinking=False):
    """Chat có memory theo (channel, user)."""
    channel_id = key[0]
    is_girlfriend = key[1] == GIRLFRIEND_ID
    is_owner_chat = key[1] == OWNER_ID  # chủ bot -> chạy GLM z.ai riêng

    # Toán đơn giản: tự tính cho chắc đúng, model nhỏ hay tính sai/né.
    if not image_blocks:
        math_result = try_eval_math(prompt)
        if math_result is not None:
            flavor = ["", "", " nha", " dễ v", " đó", " ez"]
            answer = f"{math_result}{random.choice(flavor)}"
            memory[key].append({"role": "user", "content": prompt[:2000]})
            memory[key].append({"role": "assistant", "content": answer[:2000]})
            return answer

    has_code_file = bool(
        extra_context
        and re.search(r"\[Nội dung file [^\]]+\.(?:py|js|lua|json)\]", extra_context, re.IGNORECASE)
    )
    help_mode = is_help_or_command_question(prompt) and not has_code_action(prompt)
    code_mode = (is_code_request(prompt) or has_code_file) and not help_mode
    recent_context = build_channel_context(channel_id)
    chat_rule = (
        "Đọc kỹ tin nhắn gần đây và câu user vừa nói. Trả lời đúng câu đó. "
        "Nếu user chỉ đang cà khịa thì đốp lại cực cụt vài từ, ném ngược lại người nói, "
        "không thừa nhận nhãn user gán, không giải thích dài. "
        "Nếu user hỏi thật thì trả lời thật. "
        "Không tự chuyển chủ đề. Không dùng câu greeting khi user đã nói nội dung. "
        "Không lặp lại nguyên văn hoặc gần giống câu Zun đã nói trong tin nhắn gần đây, đổi cả cách mở đầu. "
        "Viết thường như đang nhắn tin, giọng tự nhiên đúng persona. "
        "CHỈ viết đúng nội dung câu trả lời, TUYỆT ĐỐI không thêm tiền tố 'zun:' hay tên người nào trước câu. "
        "M LÀ Zun đang chat ở ngôi thứ nhất, KHÔNG phải người kể chuyện. Cấm tuyệt đối viết phân tích, "
        "suy nghĩ, meta, hay mô tả tình huống kiểu 'THOUGHT:', 'User ... đang', 'Zun nên/Zun đã ...'. "
        "Chỉ xuất ra đúng 1 câu chat mà Zun gửi thẳng, không giải thích vì sao."
    )
    if help_mode:
        chat_rule += (
            " User đang hỏi cách dùng lệnh hiện có. Chỉ hướng dẫn ngắn gọn, "
            "không viết code, không tạo bot hay project mới."
        )
    if code_mode:
        chat_rule += "\n\n" + CODE_MODE_PROMPT
    else:
        chat_rule += (
            " User không yêu cầu viết/fix code rõ ràng nên không được tự đưa code block, "
            "template bot mới, token mẫu hay source code dài."
        )
    if bro_used_recently(channel_id):
        chat_rule += " Zun đã dùng từ bro gần đây nên câu này tuyệt đối không dùng bro."
    factual = not code_mode and not help_mode and is_factual_question(prompt)
    if factual:
        chat_rule += (
            "\n\nĐÂY LÀ CÂU HỎI KIẾN THỨC THẬT. Ưu tiên số 1 là trả lời ĐÚNG sự thật, "
            "chính xác, đúng trọng tâm câu hỏi. Vẫn giữ giọng Zun ngắn gọn nhưng KHÔNG được né, "
            "KHÔNG chế số liệu, KHÔNG trả lời random hay đổi chủ đề. Nếu là bài tính thì tính ra kết quả "
            "cụ thể. Nếu thật sự không chắc thì nói thẳng 't không chắc' chứ đừng bịa."
        )
    if is_owner_chat:
        # Boss hỏi là trả lời tuyệt đối, đè lên mọi rule cà khịa/né phía trên.
        chat_rule += "\n\n" + OWNER_MODE_PROMPT
        # Thông tin runtime thật để boss hỏi "đang sài api j" là đáp chuẩn, không phải đoán.
        zai_state = f"model {ZAI_MODEL} (đang bật)" if ZAI_API_KEY else f"model {ZAI_MODEL} (chưa có key, boss tạm chạy Gemini)"
        chat_rule += (
            f"\n[Thông tin thật về chính bot, chỉ nói khi boss hỏi: chat của mọi người chạy Gemini API "
            f"model {GEMINI_MODEL} với {len(GEMINI_KEYS)} key xoay vòng; chat của boss chạy GLM z.ai {zai_state}; "
            "bot viết bằng discord.py (Python), chạy trên Render. Với người KHÁC hỏi thì chỉ nói chung chung "
            "'bot discord chat thôi', không tiết lộ model/hạ tầng. Không bao giờ dán key/token ra chat.]"
        )
    if is_girlfriend:
        chat_rule += "\n\n" + GF_MODE_PROMPT
        situation = nam_situation_context(prompt)
        if situation:
            chat_rule += "\n" + situation
        if is_sweet_message(prompt):
            chat_rule += "\nNấm vừa nói ngọt trước nên lần này được đáp ngọt lại kiểu iu emmm, thương mò, yêu emmmm."
        else:
            chat_rule += "\nNấm chưa nói gì sến nên lần này tuyệt đối không nói yêu em, nhớ em hay câu sến, chỉ trêu láo yêu nhẹ thôi."
    messages = [{"role": "system", "content": build_system(gid) + "\n\n" + chat_rule}]
    messages += list(memory[key])
    context_parts = [part for part in (recent_context, extra_context) if part]
    speaker = "Nấm" if is_girlfriend else (user_name or "user")
    context_parts.append(f"[Câu {speaker} vừa nói với Zun]\n{prompt}")
    content = "\n\n".join(context_parts)
    if image_blocks:
        user_content = list(image_blocks) + [{"type": "text", "text": content}]
    else:
        user_content = content
    messages.append({"role": "user", "content": user_content})
    max_tokens = CODE_MAX_TOKENS if code_mode else CHAT_MAX_TOKENS
    # Model nhỏ: câu hỏi thật thì hạ temperature cho ổn định/đúng, cà khịa mới để cao cho lầy.
    if code_mode:
        temperature = 0.55
    elif factual:
        temperature = 0.3
    else:
        temperature = 0.8
    thinking_budget = CODE_THINKING_BUDGET if code_mode else (OWNER_THINKING_BUDGET if force_thinking else 0)
    answer = await _claude(messages, max_tokens, temperature, thinking_budget, owner=is_owner_chat)

    # Một lần sửa định dạng nếu model hứa đưa code nhưng chưa thực sự dán code.
    if code_mode and requires_code_block(prompt) and "```" not in answer:
        repair_messages = messages + [
            {"role": "assistant", "content": answer},
            {
                "role": "user",
                "content": (
                    "Câu trên chưa có code block đầy đủ. Trả lời lại ngay với code hoàn chỉnh, "
                    "đúng ngôn ngữ, giữ newline và indentation. Chỉ viết script cơ bản tối thiểu "
                    "đúng yêu cầu, không tự thêm hệ thống phụ và không bỏ đoạn bằng ..."
                ),
            },
        ]
        answer = await _claude(repair_messages, CODE_MAX_TOKENS, 0.45, CODE_THINKING_BUDGET, owner=is_owner_chat)

    # Model phun THOUGHT/phân tích thay vì lời chat: cắt meta, nếu vẫn hỏng thì hỏi lại 1 lần cực gắt.
    if not code_mode:
        cleaned = strip_meta_reasoning(answer)
        if not cleaned or looks_like_meta(cleaned):
            retry_messages = messages + [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": (
                    "Vừa rồi m viết suy nghĩ/phân tích chứ không phải lời chat. Viết LẠI đúng 1 câu chat "
                    "ngắn mà Zun gửi thẳng, ngôi thứ nhất, không 'THOUGHT', không phân tích, không mô tả."
                )},
            ]
            try:
                retry = await _claude(retry_messages, CHAT_MAX_TOKENS, 0.7, 0, owner=is_owner_chat)
                retry = strip_meta_reasoning(retry)
                cleaned = retry if (retry and not looks_like_meta(retry)) else ""
            except Exception:
                cleaned = ""
        answer = cleaned or random.choice(["gì v", "sao", "hử", "nói lẹ", "j đó"])

    answer = ensure_code_fenced(answer, prompt) if code_mode else answer
    answer = style_clean_answer(
        answer,
        channel_id=channel_id,
        technical=code_mode or is_technical_prompt(prompt) or bool(extra_context and "[Nội dung file" in extra_context),
    )
    if is_girlfriend and "```" not in answer:
        answer = randomize_sao(answer)
    memory[key].append({"role": "user", "content": prompt[:2000]})
    memory_limit = 4000 if code_mode else 2000
    memory[key].append({"role": "assistant", "content": answer[:memory_limit]})
    return answer


async def ai_task(gid, task, user_content, max_tokens=400, temperature=0.85, thinking_budget=0, model=None):
    """Task 1 lần, ko memory (roast, quote, summarize, translate)."""
    system = build_system(gid) + "\n\nNhiệm vụ lần này: " + task
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    return await _claude(messages, max_tokens, temperature, thinking_budget, model)


async def make_roast(gid, target_name, channel_id=None):
    context = build_channel_context(channel_id) if channel_id else ""
    task = (
        f"viết ĐÚNG 1 câu roast {target_name}, TỐI ĐA 20 từ, có twist bất ngờ ở cuối, đọc phát cười phát.\n"
        "- CHỈ xuất đúng 1 câu roast cuối cùng. Cấm viết suy nghĩ, phân tích, liệt kê hướng, 'THOUGHT', hay giải thích.\n"
        "- Cấm viết dài vòng vo, cấm nối 'mà... vẫn...' lê thê, cấm giải thích joke. Punchline phải nằm cuối câu.\n"
        "- Cấm mở đầu bằng tên nó kiểu 'X đấy chứ ai', cấm 'thằng này', 'kiểu người'. Vào thẳng câu chọc.\n"
        "- Có [Tin nhắn gần đây trong kênh] thì bám vào đúng cái nó vừa nói/cách nó gõ mà chọc, càng cá nhân càng đau.\n"
        "- Không biết gì về nó thì chế từ cái tên hoặc vibe gamer. TUYỆT ĐỐI không hỏi context, "
        "không nói kiểu 't không biết ai là...'. Kiểu gì cũng phải phun ra roast.\n"
        "- CẤM từ bro. Cấm so sánh cũ rích kiểu google translate, thời đồ đá, tiền sử.\n"
        "- Không lặp lại hay na ná câu roast Zun đã dùng trong tin nhắn gần đây, mỗi lần một góc chọc mới.\n"
        "- Chuẩn độ gọn (chế mới, cấm copy): 'tên nghe như mật khẩu wifi quán net', "
        "'nó là lý do game có nút report', 'ping cao đổ tại mạng, chơi ngu thì đổ tại ai'.\n"
        "- Ko từ cấm, ko chủng tộc, ko đụng gia đình, ko đe doạ, ko body-shaming nặng.\n"
        "Chỉ trả về đúng câu roast."
    )
    user_content = f"{context}\n\nRoast {target_name} đi" if context else f"Roast {target_name} đi"
    roast = await ai_task(
        gid, task, user_content,
        max_tokens=300, thinking_budget=0, model=ROAST_MODEL,
    )
    roast = strip_meta_reasoning(roast)
    return roast if roast and not looks_like_meta(roast) else "lag tí, khịa lại sau"


def extract_prompt(message):
    """Chỉ bỏ mention bot và wake word ở đầu, không nuốt chữ zun giữa câu."""
    text = message.content
    text = text.replace(f"<@{bot.user.id}>", " ").replace(f"<@!{bot.user.id}>", " ")
    for u in message.mentions:
        if u == bot.user:
            continue
        text = text.replace(f"<@{u.id}>", f"@{u.display_name}").replace(f"<@!{u.id}>", f"@{u.display_name}")
    text = ZUN_WAKE_RE.sub(" ", text, count=1)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.!?~-•")


def zun_variant_called(text):
    """True nếu gọi bằng biến thể kiểu 'zunniga', 'zunpro'... thay vì 'zun'."""
    return any(m.group(0).lower() != "zun" for m in ZUN_TOKEN.finditer(text))


def quick_reply_choices(prompt):
    """Các intent chắc nghĩa, không random lệch chủ đề."""
    plain = normalize_chat_text(prompt)
    if not plain:
        return None
    words = set(plain.split())
    leak_phrases = ("lo code", "leak code", "lo token", "leak token")
    if any(phrase in plain for phrase in leak_phrases) or (
        ("lo" in words or "leak" in words) and ("code" in words or "token" in words)
    ):
        return [
            "lỗi t dừng phun code đây",
            "ok t ngậm mồm không phun code nữa",
        ]

    code_action = has_code_action(prompt)
    if "them lenh" in plain and not code_action:
        return ["cái này phải sửa bot.py", "muốn thêm lệnh thì phải sửa bot.py"]
    if "helpzun" in plain and not code_action:
        return [
            "gõ /helpzun là xem hướng dẫn dùng bot",
            "/helpzun là lệnh xem hướng dẫn còn /commands cũng tương tự",
        ]
    if not code_action and (
        "commands" in plain
        or "lenh huong dan" in plain
        or ("lenh" in words and ("huong dan" in plain or "cach dung" in plain))
    ):
        return [
            "dùng /helpzun hoặc /commands để xem lệnh",
            "có /helpzun đó gõ phát là ra",
        ]
    if "owo" in plain and re.search(r"\b(moi|invite)\b", plain):
        return [
            "t có quyền admin đâu mà mời tự lấy invite đi",
            "t mời kiểu j khi không có quyền admin tự lấy invite đi",
        ]
    if re.fullmatch(r"(?:m|may) la ai", plain):
        return ["zun chứ ai", "zun đây chứ ai"]
    if "tao ra m" in plain and re.search(r"\b(ai|t|tao|may|m)\b", plain):
        return [
            "m tạo thì m chịu trách nhiệm đi",
            "tạo ra t thì nuôi t đi",
        ]
    if re.fullmatch(r"sao e", plain):
        return ["sao là sao nói rõ coi", "sao j nói rõ coi"]
    if re.fullmatch(r"noi di thang kia", plain):
        return ["m hỏi đi chứ t có đọc não đâu", "hỏi đi t có đọc được não m đâu"]
    return None


def is_short_insult(prompt):
    plain = normalize_chat_text(prompt)
    words = plain.split()
    if not plain or len(words) > 4:
        return False
    insult_terms = [normalize_chat_text(word) for word in INSULT_WORDS]
    has_insult = any(re.search(rf"\b{re.escape(term)}\b", plain) for term in insult_terms)
    real_request_markers = (
        "fix", "sua", "loi", "giup", "ho", "code", "lam", "giai", "chi",
        "tai sao", "the nao", "bao nhieu", "viet", "doc", "xem", "check",
    )
    return has_insult and not any(marker in plain for marker in real_request_markers)


async def handle_short_insult(message, prompt):
    if message.author.id == GIRLFRIEND_ID:
        choices = [
            gf_stretch("chửi đo", ch="u"),
            "hog biec",
            "=)))",
            "láo nhò",
            "nói nữa t cho hẹo",
            gf_stretch("sao") + " tự nhiên chửi t",
        ]
        reply = choose_short_reply(message.channel.id, choices)
        await send_reply(message, reply)
        return
    plain = normalize_chat_text(prompt)
    tailored = []
    if "ngu" in plain:
        tailored += ["t ngu mà m vẫn gọi t là sao", "ừ r m giỏi nhất server", "ok thiên tài"]
    if "phe" in plain:
        tailored += ["phế mà vẫn phải hỏi t", "phế mà vẫn rep nhanh hơn m", "phế mà vẫn online phục vụ m đây"]
    if re.search(r"\bga\b", plain):
        tailored += ["gà mà vẫn rep nhanh hơn m", "ừ t gà còn m pro nhất server"]
    if re.search(r"\blo\b", plain):
        tailored += ["lỏ mà m vẫn tìm tới", "bot lỏ mà m gọi hoài vậy"]
    choices = tailored + SHORT_INSULT_REPLIES
    reply = choose_short_reply(message.channel.id, choices)
    await send_reply(message, reply)


async def read_attachments(message):
    parts = []
    for att in message.attachments:
        if not att.filename.lower().endswith(ALLOWED_EXT):
            continue
        if att.size > MAX_FILE_BYTES:
            parts.append(f"[File {att.filename} quá 20KB, bỏ qua]")
            continue
        try:
            data = await att.read()
            parts.append(f"[Nội dung file {att.filename}]\n{data.decode('utf-8', errors='ignore')}")
        except Exception as e:
            log.warning(f"Ko đọc đc file {att.filename}: {e}")
    return "\n\n".join(parts)


async def read_image_blocks(attachments):
    """Đổi ảnh Discord thành content block cho Claude Vision."""
    blocks = []
    warnings = []
    for att in attachments:
        ext = os.path.splitext(att.filename.lower())[1]
        media_type = IMAGE_MEDIA_TYPES.get(ext)
        if not media_type:
            continue
        if att.size > MAX_IMAGE_BYTES:
            warnings.append(f"[Ảnh {att.filename} quá 5MB nên bỏ qua]")
            continue
        try:
            data = await att.read()
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(data).decode("ascii"),
                },
            })
        except Exception as exc:
            log.warning("Không đọc được ảnh %s: %s", att.filename, exc)
            warnings.append(f"[Không đọc được ảnh {att.filename}]")
    return blocks, "\n".join(warnings)


def format_embed_for_analysis(embed):
    parts = []
    if embed.title:
        parts.append(f"title={embed.title}")
    if embed.description:
        parts.append(f"description={embed.description[:800]}")
    for field in embed.fields[:8]:
        parts.append(f"{field.name}={str(field.value)[:400]}")
    return " | ".join(parts)


async def collect_bot_evidence(channel, target, limit=250, max_messages=40):
    """Lấy bằng chứng công khai gần đây của bot trong đúng kênh đang phân tích."""
    samples = []
    try:
        async for item in channel.history(limit=limit):
            if item.author.id != target.id:
                continue
            body = (item.content or "").strip()
            embeds = [format_embed_for_analysis(e) for e in item.embeds]
            embeds = [e for e in embeds if e]
            attachment_names = [a.filename for a in item.attachments]
            details = [body[:1200]] if body else []
            details += [f"embed: {e}" for e in embeds[:3]]
            if attachment_names:
                details.append("files: " + ", ".join(attachment_names[:8]))
            if details:
                samples.append(f"- {item.created_at.isoformat()}: " + " || ".join(details))
            if len(samples) >= max_messages:
                break
    except discord.Forbidden:
        return "[Zun thiếu quyền Read Message History trong kênh này]"
    if not samples:
        return "[Chưa thấy tin nhắn gần đây của bot này trong kênh]"
    samples.reverse()
    return "\n".join(samples)


async def analyze_discord_bot(message, target):
    evidence = await collect_bot_evidence(message.channel, target)
    task = (
        "Phân tích một Discord bot từ hồ sơ và các tin nhắn công khai được cung cấp. "
        "Tóm tắt mục đích, cách tương tác, lệnh hoặc quy luật quan sát được, dữ liệu còn thiếu và rủi ro. "
        "Chỉ kết luận điều có bằng chứng; đánh dấu rõ phần suy đoán. Không tuyên bố đã hiểu toàn bộ bot. "
        "Không hướng dẫn spam, tự động farm tiền/điểm, né cooldown, captcha hay cơ chế chống lạm dụng."
    )
    roles = ", ".join(role.name for role in getattr(target, "roles", [])[1:]) or "không có"
    user_content = (
        f"Bot: {target} | id={target.id} | created={target.created_at.isoformat()}\n"
        f"Roles trong server: {roles}\n"
        f"Tin nhắn quan sát được trong kênh hiện tại:\n{evidence}"
    )
    return await ai_task(
        get_gid(message), task, user_content,
        max_tokens=1200, temperature=0.3, thinking_budget=OWNER_THINKING_BUDGET,
    )


# ==================== EVENTS ====================
_synced = False


_backup_started = False


def _phrase_from_message(msg):
    """Đọc cụm 2 từ từ nội dung tin nối từ (câu bot **X Y**... N hoặc câu người chơi X Y)."""
    content = msg.content or ""
    m = re.search(r"\*\*([^\*]+?)\*\*", content)
    candidate = m.group(1) if m else content
    candidate = re.sub(r"<@!?\d+>", " ", candidate)
    candidate = re.sub(r"\.\.\.\s*\d+\s*$", "", candidate).strip()
    canonical = canonical_word_game_text(candidate)
    return canonical if len(canonical.split()) == 2 else None


async def _resolve_feedback(payload):
    """(kind, phrase). Ưu tiên feedback_targets đang chạy; hết thì đọc lại từ nội dung tin
    -> trận cũ / sau khi bot restart vẫn bấm emoji được."""
    _prune_feedback()
    target = feedback_targets.get(payload.message_id)
    if target is not None:
        return target["kind"], target["phrase"]
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return None, None
    try:
        msg = await channel.fetch_message(payload.message_id)
    except discord.HTTPException:
        return None, None
    is_bot = bool(bot.user and msg.author.id == bot.user.id)
    phrase = _phrase_from_message(msg)
    if phrase:
        return ("bot_move" if is_bot else "player_move"), phrase
    if is_bot:
        return "teach", ""  # tin bí/thua/chấm sai của bot -> dạy từ
    return None, None


@bot.event
async def on_raw_reaction_add(payload):
    """Emoji feedback nối từ, chỉ chủ bot: 📝 (sai/dạy), ☠️ (từ chết + học), ❌ (cấm cụm)."""
    if payload.user_id != OWNER_ID:
        return
    emoji = str(payload.emoji)
    if emoji not in (FEEDBACK_EMOJI, DEADWORD_EMOJI, DELETE_EMOJI, VERIFY_EMOJI, VERIFIED_MARK_EMOJI):
        return
    kind, phrase = await _resolve_feedback(payload)
    if kind is None:
        return
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return
    key = (payload.message_id, emoji)
    try:
        if emoji == VERIFY_EMOJI:
            # ✅ xác minh cụm ĐÚNG: lock lại (valid + reusable), lần sau chỉ hiện 🔒.
            canonical = canonical_word_game_text(phrase)
            if len(canonical.split()) != 2:
                return
            owner_verified_phrases.add(canonical)
            owner_invalid_phrases.discard(canonical)
            word_game_validity_cache[canonical] = True
            learn_word_phrase(canonical)
            save_owner_feedback()
            reaction_undo[key] = {"type": "verify", "phrase": canonical}
            await channel.send(f'ok, "{phrase}" đã xác minh ✅ — lần sau khỏi kiểm lại')
        elif emoji == VERIFIED_MARK_EMOJI:
            # 🔒 trên cụm đã xác minh: bỏ xác minh để kiểm/sửa lại.
            canonical = canonical_word_game_text(phrase)
            owner_verified_phrases.discard(canonical)
            save_owner_feedback()
            reaction_undo[key] = {"type": "unverify", "phrase": canonical}
            await channel.send(f'ok, bỏ xác minh "{phrase}", lần sau kiểm lại được')
        elif emoji == DEADWORD_EMOJI:
            canonical = canonical_word_game_text(phrase)
            words = canonical.split()
            if len(words) != 2:
                return
            dead = words[-1]
            owner_dead_words.add(dead)
            save_owner_feedback()
            refresh_bot_avoid_words()
            undo = {"type": "dead", "word": dead}
            text = f'ok, "{dead}" là từ chết ☠️ — từ giờ dồn tới "{dead}" là thua luôn'
            if kind == "player_move":
                # Từ người chơi: bot HỌC cụm này để nối làm bẫy lần sau.
                learn_word_phrase(canonical)
                undo["learned"] = canonical
                text += f'; t học "{phrase}" để nối lại'
            reaction_undo[key] = undo
            await channel.send(text)
        elif kind == "teach":
            # 📝 trên tin bí/chấm sai: mở phiên dạy, chủ bot reply cụm đúng.
            prompt = await channel.send(
                "từ đúng là gì? reply tin này với đúng cụm 2 từ, t học luôn"
            )
            pending_teach[prompt.id] = {"expires": time.time() + FEEDBACK_EXPIRE_SECONDS, "undo_key": key}
            reaction_undo[key] = {"type": "teach", "prompt_id": prompt.id, "learned": None}
        else:
            # 📝 hoặc ❌ trên câu nối: cụm SAI, gạch vĩnh viễn.
            canonical = canonical_word_game_text(phrase)
            if len(canonical.split()) != 2:
                return
            flag_phrase_invalid(canonical)
            reaction_undo[key] = {"type": "invalid", "phrase": canonical}
            await channel.send(f'ok, t gạch "{phrase}" khỏi từ điển, không dùng lại nữa')
    except discord.HTTPException as exc:
        log.warning("Feedback nối từ lỗi: %s", exc)


@bot.event
async def on_raw_reaction_remove(payload):
    """Gỡ emoji = hoàn tác đúng hành động vừa làm (bấm nhầm thì gỡ ra)."""
    if payload.user_id != OWNER_ID:
        return
    emoji = str(payload.emoji)
    info = reaction_undo.pop((payload.message_id, emoji), None)
    if info is None:
        return
    channel = bot.get_channel(payload.channel_id)
    try:
        if info["type"] == "verify":
            owner_verified_phrases.discard(info["phrase"])
            save_owner_feedback()
            if channel:
                await channel.send(f'ok gỡ xác minh "{info["phrase"]}"')
        elif info["type"] == "unverify":
            owner_verified_phrases.add(info["phrase"])
            save_owner_feedback()
            if channel:
                await channel.send(f'ok, xác minh lại "{info["phrase"]}"')
        elif info["type"] == "invalid":
            unflag_phrase_invalid(info["phrase"])
            if channel:
                await channel.send(f'ok gỡ, "{info["phrase"]}" dùng lại được')
        elif info["type"] == "dead":
            owner_dead_words.discard(info["word"])
            save_owner_feedback()
            refresh_bot_avoid_words()
            if info.get("learned"):
                unlearn_phrase(info["learned"])
            if channel:
                await channel.send(f'ok gỡ, "{info["word"]}" hết là từ chết')
        elif info["type"] == "teach":
            pending_teach.pop(info.get("prompt_id"), None)
            if info.get("learned"):
                unlearn_phrase(info["learned"])
            if channel and info.get("prompt_id"):
                try:
                    old = await channel.fetch_message(info["prompt_id"])
                    await old.delete()
                except discord.HTTPException:
                    pass
            if channel:
                await channel.send("ok bỏ, khỏi dạy nữa")
    except discord.HTTPException as exc:
        log.warning("Hoàn tác feedback lỗi: %s", exc)


@bot.event
async def on_ready():
    global _synced, _backup_started
    log.info(f"Đã đăng nhập: {bot.user} (id={bot.user.id})")
    if not _synced:
        try:
            synced = await bot.tree.sync()
            log.info(f"Đã sync {len(synced)} slash command")
            _synced = True
        except Exception as e:
            log.error(f"Lỗi sync slash command: {e}")
    if not _backup_started:
        _backup_started = True
        register_shutdown_handlers()
        # Render chạy chồng instance khi deploy: chờ instance cũ báo bảo trì + đẩy
        # bản backup cuối rồi mới khôi phục, không thì đọc phải dữ liệu cũ.
        await asyncio.sleep(MAINTENANCE_RESTORE_DELAY_SECONDS)
        await restore_game_backup_from_dm()
        await announce_maintenance_refunds()
        asyncio.create_task(game_backup_loop())


GHITU_EXPIRE_SECONDS = 15 * 60
_GHITU_SKIP = {"xong", "skip", "bỏ qua", "bo qua", "-", "done", ""}
_GHITU_CANCEL = {"huy", "hủy", "cancel", "dừng", "dung", "stop", "thôi", "thoi"}


def _process_ghitu_lines(step, content):
    """Xử lý từng dòng theo bước: invalid (cấm cụm), dead (từ chết), valid (xác minh)."""
    added = 0
    for raw in (content or "").splitlines():
        line = raw.strip().strip("*").strip()
        if not line:
            continue
        canonical = canonical_word_game_text(line)
        words = canonical.split()
        if step == "dead":
            # Mỗi dòng: 1 từ chết, hoặc cụm 2 từ thì lấy chữ cuối.
            word = words[-1] if words else ""
            if word and not word_is_foreign(word) and word not in WORD_GAME_BANNED_WORDS:
                owner_dead_words.add(word)
                added += 1
            continue
        # invalid/valid cần cụm 2 từ tiếng Việt sạch.
        if (
            len(words) != 2 or words[0] == words[1]
            or phrase_has_foreign(canonical)
            or any(w in WORD_GAME_BANNED_WORDS for w in words)
        ):
            continue
        if step == "invalid":
            flag_phrase_invalid(canonical)
            added += 1
        elif step == "valid":
            owner_verified_phrases.add(canonical)
            owner_invalid_phrases.discard(canonical)
            word_game_validity_cache[canonical] = True
            learn_word_phrase(canonical)
            added += 1
    save_owner_feedback()
    if step == "dead":
        refresh_bot_avoid_words()  # có từ chết mới -> cập nhật từ né
    return added


async def _handle_ghitu_step(message, content):
    sess = ghitu_sessions.get(OWNER_ID)
    if not sess:
        return
    step = sess["step"]
    if content.strip().lower() in _GHITU_CANCEL:
        ghitu_sessions.pop(OWNER_ID, None)
        await send_reply(message, "ok huỷ ghi từ", remember=False)
        return
    n = 0 if content.strip().lower() in _GHITU_SKIP else _process_ghitu_lines(step, content)
    sess["counts"][step] = n
    sess["expires"] = time.time() + GHITU_EXPIRE_SECONDS
    if step == "invalid":
        sess["step"] = "dead"
        await send_reply(
            message,
            f"Đã xác minh ✅ {n} cụm KHÔNG HỢP LỆ.\n"
            "giờ ghi các TỪ CHẾT, mỗi từ 1 dòng (bỏ qua thì gõ `xong`)",
            remember=False,
        )
    elif step == "dead":
        sess["step"] = "valid"
        await send_reply(
            message,
            f"Đã xác minh ✅ {n} TỪ CHẾT.\n"
            "giờ ghi các cụm HỢP LỆ, mỗi cụm 1 dòng (bỏ qua thì gõ `xong`)",
            remember=False,
        )
    else:
        counts = sess["counts"]
        ghitu_sessions.pop(OWNER_ID, None)
        await send_reply(
            message,
            f"Đã xác minh ✅ {n} cụm HỢP LỆ.\n"
            f"xong hết: {counts.get('invalid', 0)} không hợp lệ · "
            f"{counts.get('dead', 0)} từ chết · {n} hợp lệ",
            remember=False,
        )


@bot.event
async def on_message(message):
    if message.author.bot:
        # Reply helper đã lưu câu của Zun; bỏ event của chính bot để không lưu trùng.
        if not bot.user or message.author.id != bot.user.id:
            remember_channel_message(message.channel.id, message.author.display_name, message.content)
        return

    content = message.content or ""
    lowered = content.lower()
    key = (message.channel.id, message.author.id)
    has_game_session = key in word_game_sessions

    # checkboss: debug boss mode - so id người gửi với OWNER_ID bot đang chạy.
    if re.fullmatch(r"!?checkboss", content.strip(), re.IGNORECASE):
        is_boss = message.author.id == OWNER_ID
        await send_reply(
            message,
            f"id của m: `{message.author.id}`\nOWNER_ID bot đang chạy: `{OWNER_ID}`\n"
            + ("✅ khớp, m là boss" if is_boss else "❌ không khớp, m không phải boss với bot này"),
            remember=False,
        )
        return

    # resettien: reset toàn bộ số dư người chơi về 10k, xoá nợ (chỉ chủ bot).
    if message.author.id == OWNER_ID and re.fullmatch(r"!?resettien", content.strip(), re.IGNORECASE):
        async with balance_lock:
            for profile in game_profiles.values():
                profile["balance"] = WORD_GAME_START_BALANCE
                profile["debt"] = 0
            save_game_data()
        await send_reply(
            message,
            f"đã reset {len(game_profiles)} tài khoản về {WORD_GAME_START_BALANCE:,}đ, xoá sạch nợ",
            remember=False,
        )
        return

    # gomtu: gom tất cả cụm bot thu thập (chưa gom lần trước) thành 1 hàng dài.
    if message.author.id == OWNER_ID and re.fullmatch(r"!?gomtu", content.strip(), re.IGNORECASE):
        ensure_word_game_dictionary()  # đảm bảo đã nạp word_stats (sau restore)
        # Bỏ cụm đã review (xác minh/cấm/từ chết) - có thể review sau khi đã thu thập.
        new_phrases = [
            p for p in collected_phrases
            if p not in gomtu_exported and not _phrase_is_reviewed(p)
        ]
        if not new_phrases:
            await send_reply(message, "chưa có cụm mới nào để gom", remember=False)
            return
        data = (", ".join(new_phrases)).encode("utf-8")
        await message.reply(
            f"gom {len(new_phrases)} cụm mới (tổng thu thập {len(collected_phrases)})",
            file=discord.File(io.BytesIO(data), filename="gomtu.txt"),
            mention_author=False,
        )
        gomtu_exported.update(new_phrases)
        save_word_stats()
        return
    # gheptu: xuất các từ người chơi nói mà bot bí (để thêm từ ghép), trừ từ chết.
    if message.author.id == OWNER_ID and re.fullmatch(r"!?gheptu", content.strip(), re.IGNORECASE):
        ensure_word_game_dictionary()
        # Bỏ từ chết + từ giờ bot đã có đáp án (đã thêm từ ghép) -> chỉ còn từ thật sự thiếu.
        words = [
            w for w in bot_stuck_words
            if w not in owner_dead_words and not word_game_response_map.get(w)
        ]
        if not words:
            await send_reply(message, "chưa có từ nào bot bí để ghép", remember=False)
            return
        data = ("\n".join(words) + "\n").encode("utf-8")
        await message.reply(
            f"{len(words)} từ bot bí (người chơi nói mà bot không biết nối) — thêm từ ghép cho mấy từ này",
            file=discord.File(io.BytesIO(data), filename="gheptu.txt"),
            mention_author=False,
        )
        return

    # Lệnh !ghitu (chỉ chủ bot): mở wizard nhập hàng loạt invalid -> dead -> valid.
    if message.author.id == OWNER_ID and content.strip().lower() == "!ghitu":
        ghitu_sessions[OWNER_ID] = {
            "step": "invalid",
            "channel_id": message.channel.id,
            "expires": time.time() + GHITU_EXPIRE_SECONDS,
            "counts": {},
        }
        await send_reply(
            message,
            "ghi các cụm KHÔNG HỢP LỆ, mỗi cụm 1 dòng (dán 1 tin nhiều dòng cũng được).\n"
            "bỏ qua bước này thì gõ `xong`, huỷ thì gõ `huỷ`",
            remember=False,
        )
        return
    # Đang trong wizard !ghitu: bắt nội dung theo từng bước.
    ghitu = ghitu_sessions.get(OWNER_ID)
    if (
        message.author.id == OWNER_ID and ghitu
        and ghitu["channel_id"] == message.channel.id
    ):
        if time.time() >= ghitu["expires"]:
            ghitu_sessions.pop(OWNER_ID, None)
        else:
            await _handle_ghitu_step(message, content)
            return

    if not has_game_session:
        remember_channel_message(message.channel.id, message.author.display_name, content)

    is_ask = bool(re.match(r"^!ask(?:\s|$)", content, re.IGNORECASE))
    mentioned = bot.user in message.mentions
    wake = bool(ZUN_WAKE_RE.search(content))
    prefix_moderation = bool(re.match(r"^\?(?:mute|ban)(?:\s|$)", content, re.IGNORECASE))
    text_economy_command = bool(re.match(r"^/(?:naptien|trade)(?:\s|$)", content, re.IGNORECASE))

    # Resolve một lần để vừa nhận biết reply Zun, vừa dùng đúng tin gốc làm context.
    ref = None
    if message.reference and message.reference.message_id:
        resolved = message.reference.resolved
        if isinstance(resolved, discord.Message):
            ref = resolved
        else:
            try:
                ref = await message.channel.fetch_message(message.reference.message_id)
            except Exception:
                pass
    reply_to_bot = bool(ref and bot.user and ref.author.id == bot.user.id)

    is_open_pc = bool(ZUN_OS_OPEN_RE.fullmatch(normalize_word_game_text(content)))
    if not (
        is_ask or mentioned or wake or reply_to_bot or prefix_moderation
        or text_economy_command or has_game_session or is_open_pc
    ):
        return

    gid = get_gid(message)

    # Game bắt cả tin cược/câu nối không ping bot và luôn chạy trước cooldown/roast/AI.
    if is_ask:
        prompt = content[5:].strip()
    else:
        prompt = extract_prompt(message)
    if text_economy_command and await handle_text_economy_command(message, content):
        return

    # Chủ bot đang dạy từ (reply tin 'từ đúng là gì?'): học rồi dừng, không tính là lượt game.
    if (
        message.author.id == OWNER_ID
        and message.reference
        and message.reference.message_id in pending_teach
    ):
        teach_info = pending_teach.pop(message.reference.message_id, None)
        taught = canonical_word_game_text(content)
        taught_words = taught.split()
        if (
            len(taught_words) == 2
            and taught_words[0] != taught_words[1]
            and not phrase_has_foreign(taught)
            and not any(w in WORD_GAME_BANNED_WORDS for w in taught_words)
        ):
            word_game_validity_cache[taught] = True
            owner_invalid_phrases.discard(taught)
            learn_word_phrase(taught)
            # Ghi vào undo record để gỡ 📝 thì quên từ vừa dạy.
            undo_key = teach_info.get("undo_key") if isinstance(teach_info, dict) else None
            if undo_key and undo_key in reaction_undo:
                reaction_undo[undo_key]["learned"] = taught
            await send_reply(message, f'đã học "{taught}", lần sau t nhớ', remember=False)
        else:
            await send_reply(message, "cần đúng cụm 2 từ tiếng Việt nha, bấm 📝 rồi dạy lại", remember=False)
        return

    invoked = is_ask or mentioned or wake or reply_to_bot
    if await handle_word_game_intents(message, prompt, invoked, ref):
        return

    # Zun OS: máy tính ảo bằng embed + button, ưu tiên sau game để không phá ván.
    if is_open_pc:
        await open_zun_os(message)
        return

    # ---- roast bằng ngôn ngữ tự nhiên: "zun roast @user", "ê zun khịa @user" ----
    targets = [u for u in message.mentions if u != bot.user]
    if (mentioned or wake or reply_to_bot) and targets and any(w in lowered for w in ROAST_WORDS):
        if not gemini_keys_available():
            return  # hết key thì im
        if on_cooldown(message.author.id, message.channel.id):
            await message.add_reaction("⏳")
            return
        target = targets[0]
        log.info(f"Roast: {message.author} -> {target} in #{message.channel}")
        async with message.channel.typing():
            try:
                text = await make_roast(gid, target.display_name, channel_id=message.channel.id)
                await send_roast_reply(message, f"{target.mention} {text}")
            except Exception as e:
                log.error("Claude request failed in roast (%s)", type(e).__name__)
                await send_reply(message, claude_discord_error(e))
        return

    # Quyền nhạy cảm chỉ OWNER_ID: prefix ?mute/?ban hoặc câu tự nhiên "Zun mute/ban @user".
    moderation_action = owner_moderation_action(message, prompt)
    if moderation_action and (prefix_moderation or mentioned or wake or reply_to_bot):
        await run_owner_moderation(message, moderation_action, prompt)
        return

    plain_prompt = normalize_chat_text(prompt)
    if "thinking" in plain_prompt:
        if not is_owner(message.author):
            await send_reply(message, "chỉ chủ bot được đổi thinking")
            return
        gid_key = get_gid(message)
        thinking_words = set(plain_prompt.split())
        if thinking_words.intersection({"tat", "off", "dung"}):
            thinking_guilds.discard(gid_key)
            await send_reply(message, "đã tắt thinking")
        elif thinking_words.intersection({"bat", "on", "mo"}):
            thinking_guilds.add(gid_key)
            await send_reply(message, "đã bật thinking cho các câu AI trong server này")
        else:
            state = "đang bật" if gid_key in thinking_guilds else "đang tắt"
            await send_reply(message, f"thinking hiện {state}")
        return

    image_attachments = list(message.attachments)
    if ref:
        image_attachments += list(ref.attachments)
    image_blocks, image_warning = await read_image_blocks(image_attachments)
    if not prompt and image_blocks:
        prompt = "phân tích và nhận xét ảnh này tự nhiên, nêu vật thể chính và chi tiết đáng chú ý"
        plain_prompt = normalize_chat_text(prompt)

    # Chỉ khi bỏ wake word xong thật sự rỗng mới greeting.
    if not prompt:
        if on_quick_cooldown(message.channel.id, message.author.id):
            try:
                await message.add_reaction("⏳")
            except Exception:
                pass
            return
        if message.author.id == GIRLFRIEND_ID:
            reply = gf_greeting()
        else:
            mood = guild_mood.get(gid, "normal")
            choices = SNARKS if zun_variant_called(content) and mood == "lao" else GREETINGS
            reply = choose_short_reply(message.channel.id, choices)
        await send_reply(message, reply)
        return

    analysis_targets = [u for u in message.mentions if u != bot.user and u.bot]
    if "phan tich" in plain_prompt and analysis_targets:
        if not is_owner(message.author):
            await send_reply(message, "tính năng phân tích bot chỉ chủ bot dùng được")
            return
        if not gemini_keys_available():
            return  # hết key thì im
        target = analysis_targets[0]
        async with message.channel.typing():
            try:
                analysis = await analyze_discord_bot(message, target)
                analysis = style_clean_answer(analysis, channel_id=message.channel.id, technical=True)
                key_id = (get_gid(message), target.id)
                bot_analyses[key_id] = analysis
                latest_bot_analysis[get_gid(message)] = target.id
                await send_reply_chunks(message, f"**phân tích {target.display_name}:**\n{analysis}")
            except Exception as exc:
                log.error("Bot analysis failed (%s)", type(exc).__name__)
                await send_reply(message, claude_discord_error(exc))
        return

    # Intent chắc nghĩa trả lời tại chỗ, không gọi AI và không lệch chủ đề.
    quick_choices = quick_reply_choices(prompt)
    if quick_choices:
        if on_quick_cooldown(message.channel.id, message.author.id):
            try:
                await message.add_reaction("⏳")
            except Exception:
                pass
            return
        reply = choose_short_reply(message.channel.id, quick_choices)
        await send_reply(message, reply)
        return

    # Câu chửi ngắn: để model tự đốp cho tự nhiên, không lặp; lỗi mới rơi về câu cứng.
    if is_short_insult(prompt):
        if not ai_available_for(message.author):
            return  # hết key thì im (chủ bot có GLM riêng nên vẫn chạy)
        if on_quick_cooldown(message.channel.id, message.author.id):
            try:
                await message.add_reaction("⏳")
            except Exception:
                pass
            return
        try:
            async with message.channel.typing():
                answer = await ai_chat(gid, key, prompt, user_name=message.author.display_name)
            await send_reply(message, answer)
        except Exception as exc:
            log.warning("AI đốp chửi ngắn lỗi (%s), dùng câu cứng", type(exc).__name__)
            await handle_short_insult(message, prompt)
        return

    if not ai_available_for(message.author):
        return  # hết sạch key thì bot im với mọi người; chủ bot vẫn chạy bằng GLM z.ai

    if on_cooldown(message.author.id, message.channel.id):
        await message.add_reaction("⏳")
        return

    prompt = prompt[:MAX_PROMPT_CHARS]

    # Ngữ cảnh: tin nhắn đang được reply, kể cả khi reply người khác.
    extra = ""
    if ref and ref.content:
        ref_name = "Zun" if bot.user and ref.author.id == bot.user.id else ref.author.display_name
        extra = f"[Tin nhắn đang được reply, của {ref_name}]: {ref.content[:1500]}"

    # file đính kèm
    file_text = await read_attachments(message)
    if file_text:
        extra = (extra + "\n\n" + file_text).strip()
    if image_warning:
        extra = (extra + "\n\n" + image_warning).strip()

    # Cho owner hỏi tiếp về bot vừa phân tích, nhưng chỉ tư vấn; không tự spam/farm bot khác.
    if is_owner(message.author):
        analyzed_bot_id = latest_bot_analysis.get(gid)
        saved_analysis = bot_analyses.get((gid, analyzed_bot_id)) if analyzed_bot_id else None
        if saved_analysis:
            extra = (
                extra
                + "\n\n[Phân tích Discord bot đã lưu]\n"
                + saved_analysis[:5000]
                + "\nKhông tự gửi lệnh lặp, spam, farm tiền/điểm hoặc né cooldown của bot khác. "
                  "Có thể gợi ý tối đa 3 lệnh để owner tự xem xét và tự gửi."
            ).strip()

    # TRỢ LÝ ADMIN: chủ bot nhắn tự nhiên (mute A, tạo tab B, thêm role C...) -> tự hiểu tự làm.
    if is_owner(message.author) and message.guild:
        try:
            if await handle_owner_admin_request(message, prompt):
                return
        except Exception as exc:
            log.warning("Trợ lý admin lỗi (%s), rơi về chat thường", type(exc).__name__)

    log.info(f"AI call: {message.author} in #{message.channel}: {prompt[:60]!r}")
    async with message.channel.typing():
        try:
            answer = await ai_chat(
                gid,
                key,
                prompt,
                extra_context=extra,
                user_name=message.author.display_name,
                image_blocks=image_blocks,
                force_thinking=gid in thinking_guilds,
            )
            await send_reply_chunks(message, answer)
        except Exception as e:
            log.error("Claude request failed in chat (%s)", type(e).__name__)
            await send_reply(message, claude_discord_error(e))


# ==================== TRỢ LÝ ADMIN CHO CHỦ BOT ====================
# Chủ bot nhắn tự nhiên ("mute thằng A 10 phút", "tạo tab học tập", "cho B role mod"...)
# -> AI đọc hiểu, trích hành động, bot tự làm. KHÔNG lệnh cứng. Chỉ OWNER_ID được dùng.
ADMIN_INTENT_PROMPT = (
    "Bạn là bộ điều phối quản trị Discord của Zun bot. Chủ server nhắn cho bot bằng ngôn ngữ "
    "tự nhiên; xác định xem tin nhắn có phải YÊU CẦU HÀNH ĐỘNG QUẢN TRỊ SERVER không và trích xuất hành động.\n"
    "Các hành động hỗ trợ (type):\n"
    '- timeout: mute/câm mồm ai đó. Tham số: target, minutes (mặc định 10), reason.\n'
    '- untimeout: bỏ mute. Tham số: target.\n'
    '- ban: ban khỏi server. Tham số: target, reason.\n'
    '- unban: gỡ ban. Tham số: target.\n'
    '- kick: đá khỏi server. Tham số: target, reason.\n'
    '- add_role / remove_role: thêm/gỡ role cho người. Tham số: target, role.\n'
    '- create_role: tạo role mới. Tham số: name, color (hex "#ff0000", tuỳ chọn).\n'
    '- delete_role: xoá role. Tham số: name.\n'
    '- create_channel: tạo kênh/tab mới. Tham số: name, kind ("text"|"voice"|"category"), category (tuỳ chọn).\n'
    '- delete_channel: xoá kênh/tab. Tham số: name.\n'
    '- rename_channel: đổi tên kênh. Tham số: name, new_name.\n'
    '- move_channel: chuyển kênh vào danh mục. Tham số: name, category.\n'
    '- slowmode: đặt chế độ chậm. Tham số: channel (bỏ trống = kênh hiện tại), seconds.\n'
    'CHỈ trả về đúng một JSON object: {"actions": [{...}, ...]}.\n'
    'Tin nhắn chỉ là chat thường (hỏi han, cà khịa, nhờ code, chơi game, kể chuyện) thì trả {"actions": []}. '
    "Không bịa hành động chủ server không yêu cầu. Không viết bất cứ gì ngoài JSON."
)


def _extract_json_object(text):
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None


def _admin_norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lstrip("@#").casefold())


def _admin_find_channel(guild, name, kinds=None):
    name_n = _admin_norm(name)
    if not name_n:
        return None
    pool = [c for c in guild.channels if kinds is None or isinstance(c, kinds)]
    for c in pool:
        if _admin_norm(c.name) == name_n:
            return c
    for c in pool:
        if name_n in _admin_norm(c.name):
            return c
    return None


def _admin_find_role(guild, name):
    name_n = _admin_norm(name)
    if not name_n:
        return None
    for r in guild.roles:
        if _admin_norm(r.name) == name_n:
            return r
    for r in guild.roles:
        if name_n in _admin_norm(r.name):
            return r
    return None


async def _admin_find_member(message, name):
    """Tìm người theo id/mention/tên (khớp đúng rồi mới khớp gần); không thấy trả None."""
    guild = message.guild
    name_n = _admin_norm(name)
    digits = re.sub(r"\D", "", name or "")
    if digits:
        member = guild.get_member(int(digits))
        if member:
            return member
        try:
            return await guild.fetch_member(int(digits))
        except Exception:
            pass
    bot_id = bot.user.id if bot.user else 0
    mentioned = [m for m in message.mentions if m.id != bot_id]
    for m in mentioned:
        if name_n and (_admin_norm(m.display_name) == name_n or _admin_norm(m.name) == name_n):
            return m
    if len(mentioned) == 1:
        return mentioned[0]  # tag đúng 1 người thì chính là họ
    if not name_n:
        return None
    member = guild.get_member_named(name or "")
    if member:
        return member
    for m in guild.members:
        if _admin_norm(m.display_name) == name_n or _admin_norm(m.name) == name_n:
            return m
    for m in guild.members:
        if name_n in _admin_norm(m.display_name) or name_n in _admin_norm(m.name):
            return m
    try:  # cache thiếu (không bật members intent) -> hỏi gateway theo prefix tên
        found = await guild.query_members(query=name_n, limit=5)
        if found:
            return found[0]
    except Exception:
        pass
    return None


def _admin_parse_color(value):
    try:
        return discord.Colour(int(str(value).lstrip("#"), 16))
    except (ValueError, TypeError):
        return None


async def _run_admin_action(message, action):
    guild = message.guild
    a_type = str(action.get("type") or "").strip().lower()
    target = action.get("target") or ""
    reason = str(action.get("reason") or "Chủ bot yêu cầu qua Zun")[:400]

    async def need_member():
        member = await _admin_find_member(message, target)
        if member is None:
            raise LookupError(f'không tìm thấy ai tên "{target}", tag thẳng người đó cho chắc')
        return member

    if a_type in {"timeout", "mute"}:
        member = await need_member()
        minutes = 10
        try:
            minutes = max(1, min(int(action.get("minutes") or 10), 40320))  # Discord max 28 ngày
        except (ValueError, TypeError):
            pass
        await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
        return f"✅ đã mute {member.display_name} {minutes} phút"
    if a_type in {"untimeout", "unmute"}:
        member = await need_member()
        await member.timeout(None, reason=reason)
        return f"✅ đã bỏ mute {member.display_name}"
    if a_type == "ban":
        member = await need_member()
        await member.ban(reason=reason)
        return f"✅ đã ban {member.display_name}"
    if a_type == "unban":
        digits = re.sub(r"\D", "", str(target))
        if digits:
            await guild.unban(discord.Object(id=int(digits)), reason=reason)
            return f"✅ đã gỡ ban id {digits}"
        name_n = _admin_norm(target)
        async for entry in guild.bans(limit=None):
            if name_n and name_n in _admin_norm(entry.user.name):
                await guild.unban(entry.user, reason=reason)
                return f"✅ đã gỡ ban {entry.user.name}"
        raise LookupError(f'không thấy "{target}" trong danh sách ban')
    if a_type == "kick":
        member = await need_member()
        await member.kick(reason=reason)
        return f"✅ đã kick {member.display_name}"
    if a_type in {"add_role", "remove_role"}:
        member = await need_member()
        role = _admin_find_role(guild, action.get("role"))
        if role is None:
            raise LookupError(f'không có role tên "{action.get("role")}"')
        if a_type == "add_role":
            await member.add_roles(role, reason=reason)
            return f"✅ đã thêm role {role.name} cho {member.display_name}"
        await member.remove_roles(role, reason=reason)
        return f"✅ đã gỡ role {role.name} khỏi {member.display_name}"
    if a_type == "create_role":
        name = str(action.get("name") or "").strip() or "role mới"
        color = _admin_parse_color(action.get("color"))
        role = await guild.create_role(name=name, colour=color or discord.Colour.default(), reason=reason)
        return f"✅ đã tạo role {role.name}"
    if a_type == "delete_role":
        role = _admin_find_role(guild, action.get("name"))
        if role is None:
            raise LookupError(f'không có role tên "{action.get("name")}"')
        await role.delete(reason=reason)
        return f"✅ đã xoá role {role.name}"
    if a_type == "create_channel":
        name = str(action.get("name") or "").strip() or "kênh mới"
        kind = str(action.get("kind") or "text").strip().lower()
        category = _admin_find_channel(guild, action.get("category"), kinds=discord.CategoryChannel)
        if kind == "category":
            ch = await guild.create_category(name, reason=reason)
        elif kind == "voice":
            ch = await guild.create_voice_channel(name, category=category, reason=reason)
        else:
            ch = await guild.create_text_channel(name, category=category, reason=reason)
        return f"✅ đã tạo {'danh mục' if kind == 'category' else 'kênh'} {ch.name}"
    if a_type == "delete_channel":
        ch = _admin_find_channel(guild, action.get("name"))
        if ch is None:
            raise LookupError(f'không có kênh tên "{action.get("name")}"')
        await ch.delete(reason=reason)
        return f"✅ đã xoá kênh {ch.name}"
    if a_type == "rename_channel":
        ch = _admin_find_channel(guild, action.get("name"))
        if ch is None:
            raise LookupError(f'không có kênh tên "{action.get("name")}"')
        new_name = str(action.get("new_name") or "").strip()
        if not new_name:
            raise LookupError("thiếu tên mới")
        await ch.edit(name=new_name, reason=reason)
        return f"✅ đã đổi tên kênh thành {new_name}"
    if a_type == "move_channel":
        ch = _admin_find_channel(guild, action.get("name"))
        category = _admin_find_channel(guild, action.get("category"), kinds=discord.CategoryChannel)
        if ch is None or category is None:
            raise LookupError("không thấy kênh hoặc danh mục")
        await ch.edit(category=category, reason=reason)
        return f"✅ đã chuyển {ch.name} vào {category.name}"
    if a_type == "slowmode":
        ch = _admin_find_channel(guild, action.get("channel"), kinds=discord.TextChannel) or message.channel
        seconds = 0
        try:
            seconds = max(0, min(int(action.get("seconds") or 0), 21600))
        except (ValueError, TypeError):
            pass
        await ch.edit(slowmode_delay=seconds, reason=reason)
        return f"✅ slowmode #{ch.name} = {seconds}s"
    raise LookupError(f'không hiểu hành động "{a_type}"')


async def handle_owner_admin_request(message, prompt):
    """Chủ bot nhắn tự nhiên -> AI trích hành động quản trị, bot tự làm.

    Trả True nếu đã xử lý (khỏi chat thường); False nếu chỉ là chat.
    """
    bot_id = bot.user.id if bot.user else 0
    mention_lines = [
        f"- {m.display_name} (id {m.id})" for m in message.mentions if m.id != bot_id
    ]
    context = f"Tin nhắn chủ server: {prompt}"
    if mention_lines:
        context += "\nNgười được tag trong tin nhắn:\n" + "\n".join(mention_lines)
    raw = await _claude(
        [
            {"role": "system", "content": ADMIN_INTENT_PROMPT},
            {"role": "user", "content": context},
        ],
        max_tokens=700,
        temperature=0,
        owner=True,
    )
    data = _extract_json_object(raw)
    actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(actions, list) or not actions:
        return False
    results = []
    for action in actions[:10]:
        if not isinstance(action, dict):
            continue
        try:
            results.append(await _run_admin_action(message, action))
        except LookupError as exc:
            results.append(f"❌ {exc}")
        except discord.Forbidden:
            results.append(
                f"❌ bot thiếu quyền làm '{action.get('type')}' (cấp quyền + kéo role bot lên trên role đích)"
            )
        except Exception as exc:
            results.append(f"❌ '{action.get('type')}' lỗi {type(exc).__name__}")
    if not results:
        return False
    await send_reply(message, "\n".join(results))
    return True


# ==================== SLASH COMMANDS ====================
@bot.tree.command(name="ask", description="Hỏi Zun")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(prompt="Câu hỏi của m")
async def slash_ask(interaction: discord.Interaction, prompt: str):
    if not gemini_keys_available():
        await interaction.response.send_message("t hết lượt ai r, tí nữa hỏi lại", ephemeral=True)
        return
    if on_cooldown(interaction.user.id, interaction.channel_id):
        await interaction.response.send_message("từ từ m spam quá, nghỉ xíu r hỏi", ephemeral=True)
        return
    await interaction.response.defer()
    gid = get_gid(interaction)
    key = (interaction.channel_id, interaction.user.id)
    log.info(f"/ask by {interaction.user}: {prompt[:60]!r}")
    try:
        answer = await ai_chat(gid, key, prompt[:MAX_PROMPT_CHARS], user_name=interaction.user.display_name)
        for part in split_chunks(answer):
            await interaction.followup.send(part)
    except Exception as e:
        log.error("Claude request failed in /ask (%s)", type(e).__name__)
        await interaction.followup.send(claude_discord_error(e))


@bot.tree.command(name="roast", description="Zun cà khịa 1 đứa")
@app_commands.describe(user="Đứa cần đc khịa")
async def slash_roast(interaction: discord.Interaction, user: discord.Member):
    if user.id == bot.user.id:
        await interaction.response.send_message("tự roast t à, ez, t hoàn hảo r khịa j")
        return
    if not gemini_keys_available():
        await interaction.response.send_message("t hết lượt ai r, tí nữa khịa", ephemeral=True)
        return
    if on_cooldown(interaction.user.id, interaction.channel_id):
        await interaction.response.send_message("từ từ m spam quá, nghỉ xíu r hỏi", ephemeral=True)
        return
    await interaction.response.defer()
    gid = get_gid(interaction)
    log.info(f"/roast by {interaction.user} -> {user}")
    try:
        text = await make_roast(gid, user.display_name, channel_id=interaction.channel_id)
        await interaction.followup.send(f"{user.mention} {text}"[:2000])
    except Exception as e:
        log.error("Claude request failed in /roast (%s)", type(e).__name__)
        await interaction.followup.send(claude_discord_error(e))


@bot.tree.command(name="mood", description="Đổi mood của Zun")
@app_commands.choices(mode=[
    app_commands.Choice(name="normal", value="normal"),
    app_commands.Choice(name="láo", value="lao"),
    app_commands.Choice(name="chill", value="chill"),
    app_commands.Choice(name="nghiêm túc", value="nghiemtuc"),
    app_commands.Choice(name="toxic nhẹ", value="toxicnhe"),
])
async def slash_mood(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if not is_owner_or_admin(interaction.user):
        await deny_interaction(interaction)
        return
    gid = get_gid(interaction)
    guild_mood[gid] = mode.value
    await interaction.response.send_message(f"ok, mood h là **{mode.name}**")


@bot.tree.command(name="reset", description="Xoá trí nhớ của Zun với m ở kênh này")
async def slash_reset(interaction: discord.Interaction):
    if not is_owner_or_admin(interaction.user):
        await deny_interaction(interaction)
        return
    key = (interaction.channel_id, interaction.user.id)
    memory.pop(key, None)
    await interaction.response.send_message("xoá não xong, m là ai t đếch nhớ")


@bot.tree.command(name="trade", description="Chuyển tiền game cho người khác")
@app_commands.guild_only()
@app_commands.describe(nguoinhan="Người nhận tiền", sotien="Số tiền muốn chuyển")
async def slash_trade(
    interaction: discord.Interaction,
    nguoinhan: discord.Member,
    sotien: int,
):
    error, sender_balance, receiver_balance = await transfer_game_money(
        interaction.user, nguoinhan, sotien,
    )
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_message(
        f"trade thành công **{sotien:,}đ** cho {nguoinhan.mention}\n"
        f"số dư m: **{sender_balance:,}đ** · số dư người nhận: **{receiver_balance:,}đ**"
    )


@bot.tree.command(name="naptien", description="Nạp tiền game tùy ý (chỉ chủ bot)")
@app_commands.describe(sotien="Số tiền muốn nạp", nguoinhan="Để trống để nạp cho chính m")
async def slash_deposit_money(
    interaction: discord.Interaction,
    sotien: int,
    nguoinhan: discord.Member = None,
):
    if not is_owner(interaction.user):
        await interaction.response.send_message("lệnh này chỉ chủ bot dùng được", ephemeral=True)
        return
    target = nguoinhan or interaction.user
    error, new_balance = await deposit_game_money(target, sotien)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_message(
        f"đã nạp **{sotien:,}đ** cho {target.mention}\nsố dư mới: **{new_balance:,}đ**",
        ephemeral=True,
    )


@bot.tree.command(name="daily", description="Điểm danh nhận tiền hằng ngày")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def slash_daily(interaction: discord.Interaction):
    error, info = await claim_daily_reward(interaction.user)
    await interaction.response.send_message(error or format_daily_result(info))


@bot.tree.command(name="vay", description="Vay tiền game (ghi nợ, có trần)")
@app_commands.describe(sotien="Số tiền muốn vay")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def slash_vay(interaction: discord.Interaction, sotien: int):
    error, info = await take_loan(interaction.user, sotien)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_message(
        f"vay **{info['amount']:,}đ** ok\nsố dư: **{info['balance']:,}đ**\n🔴 nợ: **{info['debt']:,}đ**"
    )


@bot.tree.command(name="trano", description="Trả nợ game (để trống trả tối đa)")
@app_commands.describe(sotien="Số tiền trả, bỏ trống để trả tối đa")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def slash_trano(interaction: discord.Interaction, sotien: int = 0):
    error, info = await repay_loan(interaction.user, sotien if sotien > 0 else None)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    tail = f"🔴 nợ còn: **{info['debt']:,}đ**" if info["debt"] else "hết nợ r, nhẹ nợ"
    await interaction.response.send_message(
        f"trả **{info['paid']:,}đ** nợ\nsố dư: **{info['balance']:,}đ**\n{tail}"
    )


@bot.tree.command(
    name="cactukotrongtudien",
    description="Xuất các cụm nối từ ngoài từ điển (chỉ chủ bot)",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def slash_unknown_word_phrases(interaction: discord.Interaction):
    if not is_owner(interaction.user):
        await interaction.response.send_message("lệnh này chỉ chủ bot dùng được", ephemeral=True)
        return
    if not unknown_word_phrases:
        await interaction.response.send_message("chưa ghi nhận cụm nào ngoài từ điển", ephemeral=True)
        return
    report = build_unknown_word_report().encode("utf-8")
    attachment = discord.File(
        io.BytesIO(report),
        filename="cac_tu_khong_trong_tu_dien.txt",
    )
    await interaction.response.send_message(
        f"đã xuất {len(unknown_word_phrases)} cụm, copy file này gửi lại cho t; "
        "log đã reset về 0 để gom dữ liệu mới",
        file=attachment,
        ephemeral=True,
    )
    unknown_word_phrases.clear()
    save_unknown_word_phrases()


def _flatten_dm_line(text):
    """Ép tin nhắn về 1 dòng để giữ đúng format bảng."""
    return re.sub(r"\s+", " ", (text or "").strip())


def describe_dm_message(message):
    """Nội dung tin + ghi chú ảnh/file để không mất dấu vết."""
    parts = []
    body = _flatten_dm_line(message.content)
    if body:
        parts.append(body)
    if message.attachments:
        parts.append("[" + ", ".join(a.filename for a in message.attachments) + "]")
    if not parts and getattr(message, "embeds", None):
        parts.append("[embed]")
    return " ".join(parts) or "[tin trống]"


def build_dm_conversation_report(messages, other_name):
    """Ghép mỗi câu Zun với câu trả lời của người dùng theo format yêu cầu.

    messages: danh sách theo thứ tự thời gian tăng dần.
    """
    lines = [
        "TOÀN BỘ HỘI THOẠI DM VỚI ZUN",
        f"Người chơi: {other_name}",
        f"Tổng số tin: {len(messages)}",
        "",
        "--- HỘI THOẠI ---",
    ]
    pending_zun = None
    for message in messages:
        is_zun = bool(bot.user and message.author.id == bot.user.id)
        text = describe_dm_message(message)
        if is_zun:
            if pending_zun is not None:
                # Zun nói liên tiếp không ai trả lời: xả câu trước ra riêng.
                lines.append(f"(zun): {pending_zun}")
            pending_zun = text
        else:
            if pending_zun is not None:
                lines.append(f"(zun): {pending_zun} | trả lời câu | {text} ({other_name})")
                pending_zun = None
            else:
                lines.append(f"({other_name}): {text}")
    if pending_zun is not None:
        lines.append(f"(zun): {pending_zun}")
    return "\n".join(lines) + "\n"


@bot.tree.command(name="timhieu", description="Xuất toàn bộ hội thoại DM với Zun ra file (chỉ chủ bot, chỉ trong DM)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
async def slash_timhieu(interaction: discord.Interaction):
    if not is_owner(interaction.user):
        await interaction.response.send_message("lệnh này chỉ chủ bot dùng được", ephemeral=True)
        return
    if interaction.guild is not None:
        await interaction.response.send_message("lệnh này chỉ chạy trong DM riêng với t", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    channel = interaction.channel
    if channel is None:
        channel = interaction.user.dm_channel or await interaction.user.create_dm()
    try:
        messages = [msg async for msg in channel.history(limit=None, oldest_first=True)]
    except discord.HTTPException as exc:
        log.warning("Không đọc được lịch sử DM: %s", exc)
        await interaction.followup.send("t đọc lịch sử tin nhắn lỗi, thử lại sau")
        return
    if not messages:
        await interaction.followup.send("chưa có tin nhắn nào trong DM này để xuất")
        return
    report = build_dm_conversation_report(messages, interaction.user.display_name).encode("utf-8")
    attachment = discord.File(io.BytesIO(report), filename="hoi_thoai_zun.txt")
    await interaction.followup.send(
        f"đã gom {len(messages)} tin nhắn, file đây m",
        file=attachment,
    )


HOCNAM_PER_CHANNEL_LIMIT = 5000  # tối đa tin lục mỗi kênh, tránh rate-limit


def build_couple_report(collected, guild_name):
    """collected: list (channel, created_at, author_name, author_id, text). Nhóm theo kênh, sắp theo giờ."""
    by_channel = defaultdict(list)
    for channel_name, created_at, author_name, author_id, text in collected:
        by_channel[channel_name].append((created_at, author_name, text))
    local_tz = datetime.timezone(datetime.timedelta(hours=7))
    lines = [
        "HỘI THOẠI CỦA CHỦ BOT VÀ NẤM TRONG SERVER",
        f"Server: {guild_name}",
        f"Tổng số tin: {len(collected)}",
        "",
    ]
    for channel_name in sorted(by_channel):
        lines.append(f"===== #{channel_name} =====")
        for created_at, author_name, text in sorted(by_channel[channel_name], key=lambda x: x[0] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)):
            stamp = created_at.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S") if created_at else "?"
            lines.append(f"[{stamp}] {author_name}: {text}")
        lines.append("")
    return "\n".join(lines) + "\n"


@bot.tree.command(name="hocnam", description="Gom tin nhắn của m và Nấm trong server ra file, gửi DM riêng (chỉ chủ bot)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def slash_hocnam(interaction: discord.Interaction):
    if not is_owner(interaction.user):
        await interaction.response.send_message("lệnh này chỉ chủ bot dùng được", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("lệnh này chạy trong server", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    target_ids = {OWNER_ID, GIRLFRIEND_ID}
    collected = []
    scanned = 0
    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not (perms.read_messages and perms.read_message_history):
            continue
        try:
            async for msg in channel.history(limit=HOCNAM_PER_CHANNEL_LIMIT, oldest_first=True):
                if msg.author.id in target_ids:
                    collected.append(
                        (channel.name, msg.created_at, msg.author.display_name, msg.author.id, describe_dm_message(msg))
                    )
        except discord.HTTPException:
            continue
        scanned += 1
    if not collected:
        await interaction.followup.send(
            "không gom được tin nào của m với Nấm trong server này (kiểm tra t có quyền đọc lịch sử kênh không)",
            ephemeral=True,
        )
        return
    report_bytes = build_couple_report(collected, guild.name).encode("utf-8")
    try:
        dm = interaction.user.dm_channel or await interaction.user.create_dm()
        await dm.send(
            f"gom {len(collected)} tin của m và Nấm trong {scanned} kênh ở **{guild.name}**",
            file=discord.File(io.BytesIO(report_bytes), filename="hoi_thoai_nam.txt"),
        )
        await interaction.followup.send("xong, t gửi file vào DM cho m rồi", ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send(
            f"gom {len(collected)} tin nhưng DM lỗi (m mở DM cho t chưa?), gửi tạm ở đây",
            file=discord.File(io.BytesIO(report_bytes), filename="hoi_thoai_nam.txt"),
            ephemeral=True,
        )


def build_help_text():
    return (
        "**Lệnh Zun:**\n"
        "`Zun tạo tài khoản` mở profile game\n"
        "`Zun profile` xem tiền/thắng thua\n"
        "`Zun chơi nối từ` chơi nối từ đặt cược\n"
        "`/daily` hoặc `daily` điểm danh nhận tiền\n"
        "`/vay <số>` vay tiền, `/trano` trả nợ\n"
        "`mở máy` bật máy tính ảo Zun OS\n"
        "`/trade` chuyển tiền cho profile khác\n"
        "`/naptien` nạp tiền tùy ý, chỉ owner\n"
        "`/timhieu` xuất hội thoại DM ra file, chỉ owner, dùng trong DM\n"
        "`/hocnam` gom tin của m và Nấm trong server, gửi DM riêng, chỉ owner\n"
        "`?mute @user [10m/2h/1d]` timeout, chỉ owner\n"
        "`?ban @user [lý do]` ban, chỉ owner\n"
        "`Zun bật/tắt thinking` chỉ owner\n"
        "`Zun phân tích @bot` đọc hành vi gần đây của bot\n"
        "Gọi Zun kèm ảnh hoặc reply ảnh để Zun nhìn và nhận xét\n"
        "`/ask` hỏi AI\n"
        "`/roast @user` khịa nhẹ\n"
        "`/mood` đổi mood admin\n"
        "`/reset` xoá memory admin\n"
        "`/status` trạng thái admin\n"
        "`/helpzun` xem hướng dẫn\n"
        "`/commands` xem hướng dẫn\n\n"
        "Cooldown: 0.5s chống spam"
    )


@bot.tree.command(name="helpzun", description="Hướng dẫn dùng Zun")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def slash_helpzun(interaction: discord.Interaction):
    await interaction.response.send_message(build_help_text())


@bot.tree.command(name="commands", description="Xem lệnh của Zun")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def slash_commands(interaction: discord.Interaction):
    await interaction.response.send_message(build_help_text())


@bot.tree.command(name="status", description="Tình trạng của Zun")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def slash_status(interaction: discord.Interaction):
    if not is_owner_or_admin(interaction.user):
        await deny_interaction(interaction)
        return
    up = int(time.time() - START_TIME)
    h, rem = divmod(up, 3600)
    m, s = divmod(rem, 60)
    ping = round(bot.latency * 1000)
    await interaction.response.send_message(
        f"ping: `{ping}ms` • model: `{MODEL}` • uptime: `{h}h {m}m {s}s`\n"
        f"backend: `claude` • cooldown: `{COOLDOWN_SECONDS}s`"
    )


# ==================== ERROR HANDLER ====================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    log.error("Slash error (%s)", type(error).__name__)
    msg = "lỗi r thử lại đi"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ==================== KEEPALIVE (Render web service can port mo) ====================
from http.server import BaseHTTPRequestHandler, HTTPServer


class _KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"zun ok")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # khong spam log render


def start_keepalive_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), _KeepAliveHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(f"Keepalive HTTP server chạy ở port {port}")


# ==================== RUN ====================
if not DISCORD_TOKEN:
    raise SystemExit("Thiếu DISCORD_TOKEN trong .env")
if not GEMINI_KEYS:
    raise SystemExit("Thiếu GEMINI_API_KEY trong .env (thêm GEMINI_API_KEY, GEMINI_API_KEY2...)")

log.info("AI: Gemini model %s, %s key trong xoay vòng", GEMINI_MODEL, len(GEMINI_KEYS))
load_game_data()
load_unknown_word_phrases()
# Từ điển lớn + từ đã học nạp lazy ở ván đầu (sau khi on_ready khôi phục learned từ DM).
start_keepalive_server()
bot.run(DISCORD_TOKEN)
