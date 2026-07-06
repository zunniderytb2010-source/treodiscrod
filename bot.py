import asyncio
import base64
import datetime
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


# AI local: chạy Gemma qua Ollama trên máy chủ bot, không dùng API trả phí nữa.
# Cài Ollama rồi `ollama pull gemma3n:e4b`, để Ollama chạy nền ở cổng 11434.
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))  # giây, lần đầu load model chậm

MODEL = os.getenv("GEMMA_MODEL", "gemma3n:e4b")
ROAST_MODEL = os.getenv("ROAST_MODEL", MODEL)
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
MAINTENANCE_RESTORE_DELAY_SECONDS = 20
WORD_GAME_MAX_STRIKES = 4
# 0️⃣ 1️⃣ ... 9️⃣ 🔟, index = số giây còn lại
WORD_GAME_START_BALANCE = 10_000
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
    "ăn cơm", "đi học", "chơi game", "làm việc", "uống nước", "đọc sách",
    "nghe nhạc", "xem phim", "nấu ăn", "mua đồ", "học bài", "vẽ tranh",
    "chạy bộ", "ngủ trưa", "nói chuyện", "mở cửa", "trồng cây", "nuôi mèo",
    "ăn sáng", "đọc truyện", "xây nhà", "bán hàng", "làm bánh", "uống sữa",
    "đi chơi", "vào lớp", "học nhóm", "chơi bóng", "trồng rau", "nuôi chó",
    "đổi tên", "cầm bút", "bắt đầu", "thả tim", "pha màu", "đi ngủ",
    "chơi nhạc", "uống trà", "đọc báo", "nghe tin", "vẽ hình", "nói thật",
    "trồng hoa", "nuôi cá", "ra đường", "thắng trận", "vui vẻ",
)
# Các hậu tố này xuất hiện hàng loạt trong phần dữ liệu sinh tự động; chỉ dùng khi
# một key không còn lựa chọn tự nhiên nào khác.
WORD_GAME_FILLER_WORDS = {
    "bot", "bug", "code", "data", "file", "fix", "game", "key", "lag", "loi",
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
}
# Cụm nghe gượng: người chơi nói thì tha, nhưng bot không được tự ra.
WORD_GAME_BOT_AVOID_PHRASES = {
    "chì lưới", "thép nguội", "nát đời",
}
# Từ đuôi gần như không có đường nối chuẩn: nước gài chết, bot ƯU TIÊN ra để ép thua.
WORD_GAME_KILL_WORDS = {
    "ngoằng", "lự",
}
# Cụm chứa từ tục/nhạy cảm không được tính lượt, cả phía người chơi lẫn bot.
WORD_GAME_BANNED_WORDS = {
    "lồn", "loz", "cặc", "cak", "buồi", "đụ", "địt", "đéo", "đĩ", "điếm",
    "cứt", "cức", "sex", "porn",
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
    if not game_profiles and not unknown_word_phrases and not _pending_refunds:
        # Không bao giờ đè backup đang có dữ liệu bằng trạng thái rỗng (vd instance
        # mới boot chưa kịp khôi phục) - đây là cách profile bị mất vĩnh viễn.
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
    if not need_profiles and not need_unknown and not need_sessions:
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


def is_loan_request(plain):
    return bool(re.match(r"(?:vay|muon tien|vay tien)\b", plain))


def is_repay_request(plain):
    return bool(re.match(r"(?:tra no|tra tien|tra nợ|gop no)\b", plain))


def is_debt_request(plain):
    return bool(re.fullmatch(r"(?:xem )?no|no cua (?:t|toi|tao|minh)|so no", plain))


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


def ensure_word_game_dictionary():
    global word_game_response_map, word_game_dead_ends, word_game_start_pool, word_game_dictionary_phrases
    if word_game_response_map is not None:
        return
    normalized_map = defaultdict(list)
    for key, phrases in RESPONSE_MAP.items():
        normalized_key = canonical_word_game_text(key)
        for phrase in phrases:
            if len(canonical_word_game_text(phrase).split()) == 2:
                normalized_map[normalized_key].append(phrase)
    word_game_response_map = dict(normalized_map)
    word_game_dictionary_phrases = {
        canonical_word_game_text(phrase)
        for phrase in START_PHRASES
    }
    word_game_dictionary_phrases.update(
        canonical_word_game_text(phrase)
        for phrases in RESPONSE_MAP.values()
        for phrase in phrases
    )
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


def reverses_used_phrase(phrase, used_phrases):
    words = canonical_word_game_text(phrase).split()
    return len(words) == 2 and f"{words[1]} {words[0]}" in used_phrases


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
            and words[0] != words[1]  # cấm bịa kiểu "nhàng nhàng", "queo queo"
        ):
            candidates.append((phrase, normalized, words[-1]))
    if not candidates:
        return None
    # RESPONSE_MAP xếp cụm tự nhiên trước, phần sinh tự động nằm sau nên chỉ quét
    # nhóm đầu. Câu mở màn đã lọc dễ riêng; còn trong ván bot chơi độ khó max:
    # ưu tiên nước gài chết (từ cuối tuyệt đường), rồi tới từ cuối cụt thường.
    candidates = candidates[:4]
    killers = [item for item in candidates if item[2] in WORD_GAME_KILL_WORDS]
    deadly = killers or [item for item in candidates if item[2] in word_game_dead_ends]
    pool = deadly or candidates
    natural = [item for item in pool if item[2] not in WORD_GAME_FILLER_WORDS]
    return random.choice(natural or pool)[0]


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
    ):
        return None
    return re.sub(r"\s+", " ", answer).strip().lower()


async def ai_word_game_fallback(last_word, used_phrases, used_required_words=None, temperature=0.2):
    used = ", ".join(sorted(used_phrases)[:WORD_GAME_MAX_AI_USED])
    prompt = (
        "Tìm 1 cụm nối từ tiếng Việt đúng 2 từ.\n"
        f'Cụm phải bắt đầu bằng từ: "{last_word}".\n'
        f"Không dùng các cụm đã dùng: {used}.\n"
        "Cụm phải là từ ghép chuẩn, phổ biến với người Việt. KHÔNG dùng tên riêng/địa danh, "
        "KHÔNG ghép gượng kiểu ty con, chì lưới, rãi rác.\n"
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


async def judge_word_game_phrase(phrase, source="không rõ"):
    """Kiểm tra nghĩa bằng AI cho cụm lạ; cache để không tốn token ở lần sau."""
    canonical = canonical_word_game_text(phrase)
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
        verdict = await _claude(messages, max_tokens=8, temperature=0, thinking_budget=0)
    except Exception as exc:
        log.warning("AI kiểm nghĩa nối từ lỗi (%s)", type(exc).__name__)
        record_unknown_word_phrase(canonical, source)
        return None
    parsed = _parse_word_game_verdict(verdict)
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
            recheck = await _claude(recheck_messages, max_tokens=8, temperature=0, thinking_budget=0)
        except Exception:
            record_unknown_word_phrase(canonical, source)
            return None
        rechecked = _parse_word_game_verdict(recheck)
        if rechecked is None:
            record_unknown_word_phrase(canonical, source)
            return None
        valid = rechecked
    else:
        record_unknown_word_phrase(canonical, source)
        return None
    word_game_validity_cache[canonical] = valid
    record_unknown_word_phrase(canonical, source, valid)
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


async def archive_and_cleanup_word_game(session, source_channel, won):
    """Gửi biên bản trước; chỉ dọn tin khi bản lưu đã lên kênh thành công."""
    log_channel = find_word_game_log_channel(source_channel)
    if log_channel is None:
        log.warning(
            "Không tìm thấy kênh lưu nối từ %r trong guild %s; giữ nguyên tin nhắn ván %s",
            WORD_GAME_LOG_CHANNEL_NAME,
            getattr(getattr(source_channel, "guild", None), "id", None),
            session.get("game_id"),
        )
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

    # Xóa từ mới tới cũ để reply không còn treo vào tin đã bị xóa.
    failed = 0
    for item in reversed(session.get("game_messages", [])):
        try:
            await item.delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed += 1
            log.warning("Không xóa được tin %s của ván %s: %s", item.id, game_id, exc)
    if failed:
        log.warning("Ván %s còn %s tin không xóa được; kiểm tra quyền Manage Messages", game_id, failed)
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
            await archive_and_cleanup_word_game(session, bot_message.channel, won=False)
    except asyncio.CancelledError:
        raise
    except discord.HTTPException as exc:
        log.warning("Đếm giờ nối từ lỗi: %s", exc)


async def register_word_game_strike(message, session, phrase_key, profane=False):
    """Từ sai/vô nghĩa không thua ngay: khịa tăng dần, đủ 4 lần trong ván mới xử thua."""
    session["strikes"] = session.get("strikes", 0) + 1
    strikes = session["strikes"]
    if strikes >= WORD_GAME_MAX_STRIKES:
        await finish_word_game_loss(message, session, "sai lần 4 rồi, hết cứu")
        return
    if profane:
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
    start_word_game_timer((message.channel.id, message.author.id), session, sent)


async def finish_word_game_win(message, session):
    key = (message.channel.id, message.author.id)
    profile = game_profile_for(message.author)
    prize = session["bet"] * 2
    profile["balance"] += prize
    update_game_result(profile, won=True)
    cancel_word_game_timer(session)
    word_game_sessions.pop(key, None)
    await send_word_game_reply(
        message,
        session,
        f"t bí từ rồi\nm thắng +{prize:,}đ\nsố dư giờ: {profile['balance']:,}đ",
    )
    await archive_and_cleanup_word_game(session, message.channel, won=True)


async def finish_word_game_loss(message, session, reason=""):
    key = (message.channel.id, message.author.id)
    profile = game_profile_for(message.author)
    update_game_result(profile, won=False)
    cancel_word_game_timer(session)
    word_game_sessions.pop(key, None)
    prefix = f"{reason}\n" if reason else ""
    await send_word_game_reply(
        message,
        session,
        f"{prefix}m thua mất {session['bet']:,}đ\nsố dư giờ: {profile['balance']:,}đ",
    )
    await archive_and_cleanup_word_game(session, message.channel, won=False)


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
        if bet is None or bet > profile["balance"]:
            await send_word_game_reply(
                message,
                session,
                f"tiền cược phải từ 1đ tới {profile['balance']:,}đ",
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
    if (
        len(words) != 2
        or words[0] != session["last_word"]
        or phrase_key in session["used_phrases"]
        or await judge_word_game_phrase(phrase_key, source="người chơi") is False
    ):
        # Sai chính tả, sai luật hay vô nghĩa đều tính strike, không thua ngay.
        await register_word_game_strike(message, session, phrase_key)
        return

    session["used_phrases"].add(phrase_key)
    used_required_words.add(words[-1])
    session["current_phrase"] = phrase_key
    session["last_word"] = words[-1]
    session["updated_at"] = now

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

    sent = await send_word_game_reply(
        message,
        session,
        f"**{response}**... {WORD_GAME_TURN_SECONDS}",
    )
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
        if is_loan_request(plain):
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
        if is_debt_request(plain):
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
        r"(?i)\b(DISCORD_TOKEN|ANTHROPIC_API_KEY|CLAUDE_API_KEY|GEMINI_API_KEY)\s*([:=])\s*([^\s`]+)",
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


def clean_answer(text):
    """Bỏ dấu ngoặc kép model tự thêm bọc câu trả lời (lỗi kiểu: xin chào.")"""
    text = sanitize_ai_output(text).strip()
    quotes = '"\u201c\u201d'
    # bọc nguyên câu trong ngoặc kép -> bỏ cả 2 đầu
    if len(text) >= 2 and text[0] in quotes and text[-1] in quotes:
        text = text[1:-1].strip()
    # dấu " lẻ ở cuối câu -> cắt
    elif text and text[-1] in quotes and sum(text.count(q) for q in quotes) % 2 == 1:
        text = text[:-1].rstrip()
    return text


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


def claude_discord_error(error):
    # Ollama tắt/chưa pull model thì báo kiểu người thật, không lộ lỗi kỹ thuật.
    if isinstance(error, (aiohttp.ClientConnectorError, ConnectionError)):
        return random.choice(RATE_LIMIT_EXCUSES)
    return random.choice(ERROR_EXCUSES)


def _to_ollama_messages(messages):
    """Đổi list {role, content} (content có thể là block ảnh kiểu Anthropic) sang format Ollama."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        # content là list block: gom text + ảnh base64 cho message đa phương tiện.
        texts, images = [], []
        for block in content:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "image":
                data = block.get("source", {}).get("data")
                if data:
                    images.append(data)
        entry = {"role": m["role"], "content": "\n".join(texts)}
        if images:
            entry["images"] = images
        out.append(entry)
    return out


async def _claude(messages, max_tokens=CHAT_MAX_TOKENS, temperature=0.85, thinking_budget=0, model=None):
    """Gọi model Gemma local qua Ollama (/api/chat). Tên hàm giữ nguyên cho khỏi sửa nơi gọi.

    thinking_budget không dùng nữa (Gemma không có extended thinking); chỉ map
    temperature + num_predict. Ollama gộp system vào messages luôn.
    """
    payload = {
        "model": model or MODEL,
        "messages": _to_ollama_messages(messages),
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    timeout = aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{OLLAMA_HOST}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
    text = (data.get("message") or {}).get("content", "")
    return clean_answer(text)


async def ai_chat(gid, key, prompt, extra_context="", user_name="", image_blocks=None, force_thinking=False):
    """Chat có memory theo (channel, user)."""
    channel_id = key[0]
    is_girlfriend = key[1] == GIRLFRIEND_ID
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
        "Viết thường như đang nhắn tin, giọng tự nhiên đúng persona."
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
    temperature = 0.55 if code_mode else 0.9
    thinking_budget = CODE_THINKING_BUDGET if code_mode else (OWNER_THINKING_BUDGET if force_thinking else 0)
    answer = await _claude(messages, max_tokens, temperature, thinking_budget)

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
        answer = await _claude(repair_messages, CODE_MAX_TOKENS, 0.45, CODE_THINKING_BUDGET)

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
        "- Trong lúc suy nghĩ hãy nghĩ ra 3-4 hướng roast khác nhau rồi CHỌN câu đau và gọn nhất mới trả lời.\n"
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
    return await ai_task(
        gid, task, user_content,
        max_tokens=300, thinking_budget=2048, model=ROAST_MODEL,
    )


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

    # Chỉ chặn câu chửi ngắn thuần tuý; có yêu cầu thật thì vẫn gọi AI.
    if is_short_insult(prompt):
        if on_quick_cooldown(message.channel.id, message.author.id):
            try:
                await message.add_reaction("⏳")
            except Exception:
                pass
            return
        await handle_short_insult(message, prompt)
        return

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


# ==================== SLASH COMMANDS ====================
@bot.tree.command(name="ask", description="Hỏi Zun")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(prompt="Câu hỏi của m")
async def slash_ask(interaction: discord.Interaction, prompt: str):
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

log.info("AI local: Ollama %s, model %s (nhớ chạy Ollama + `ollama pull %s`)", OLLAMA_HOST, MODEL, MODEL)
load_game_data()
load_unknown_word_phrases()
start_keepalive_server()
bot.run(DISCORD_TOKEN)
