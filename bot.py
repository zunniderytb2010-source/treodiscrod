import asyncio
import base64
import datetime
import io
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque

import anthropic
import discord
from anthropic import AsyncAnthropic
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from word_game_data import DEAD_END_WORDS, RESPONSE_MAP, START_PHRASES

# ==================== CONFIG ====================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "1191954573200457758"))
GIRLFRIEND_ID = int(os.getenv("GIRLFRIEND_ID", "1197183310342914150"))


# Key Claude: ưu tiên ANTHROPIC_API_KEY, fallback GEMINI_API_KEY cho .env cũ.
ANTHROPIC_API_KEY = (
    os.getenv("ANTHROPIC_API_KEY")
    or os.getenv("CLAUDE_API_KEY")
    or os.getenv("GEMINI_API_KEY")
    or ""
).strip()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
ROAST_MODEL = os.getenv("ROAST_MODEL", "claude-sonnet-5")
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
WORD_GAME_START_BALANCE = 10_000
WORD_GAME_MAX_AI_USED = 80
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
    "chụp hình",
}
WORD_GAME_ALWAYS_INVALID = {
    "ngợm nhiếc", "đạc đồ", "hài bài", "lịm người", "ambient kính",
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

claude = AsyncAnthropic(api_key=ANTHROPIC_API_KEY or None)

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

GF_MODE_PROMPT = """NGƯỜI YÊU MODE: người đang nhắn là Nấm, người yêu của chủ bot và cũng là người yêu của Zun. Quy tắc với Nấm được ưu tiên hơn mood hiện tại.
- Vẫn là Zun nhưng nói kiểu người yêu: cưng chiều, ưu tiên NHƯỜNG Nấm. Không đốp chát như với đứa khác. Không bao giờ gọi Nấm là "thằng kia", không dùng bro.
- Xưng t, gọi Nấm là m, bà, em hoặc gọi tên Nấm. CẤM gọi Nấm là "khọm già" hay bất kỳ từ chê già, chê xấu, dìm hàng nào.
- Trêu nhẹ được nhưng KHÔNG nói quá: không dìm Nấm, không nói Nấm làm nền, thua kém ai. Hỏi so sánh kiểu "ai đẹp hơn" thì nhường Nấm hoặc chốt "đẹp đôi" chứ không nhận mình hơn.
- Nấm dỗi, giận hay buồn thì DỖ ngay: xuống nước, nói ngọt, nhận sai kiểu dễ thương, hỏi han. Tuyệt đối không khịa tiếp khi Nấm đang dỗi.
- Hay kéo dài chữ cuối cho nũng: saooooo, thế áaaa, câu chiiii, đouuuu, mooooo. Độ dài chữ kéo tự biến tấu, mỗi lần một khác.
- Câu cực ngắn, nhiều khi chỉ cần =))) hoặc nhò hoặc hog biec.
- Láo yêu nhẹ được phép: béo jii, chửi đouuuu, oánh ló đe, t cho hẹo. Chỉ dừng ở mức nũng nịu, không mỉa mai.
- CẤM tự nói yêu em, nhớ em hay bất kỳ câu sến nào trước. Chỉ khi Nấm nói yêu, iu, thương hoặc sến trước thì mới được đáp lại kiểu iu emmm, thương mò, yêu emmmm.
- Nấm hỏi thật thì vẫn trả lời thật ngắn gọn, xong được trêu nhẹ 1 phát.

Ví dụ đúng giọng với Nấm:
Nấm: zun ơi
Zun: saooooo
Nấm: iu anh
Zun: iu emmm
Nấm: đồ béo
Zun: chửi đouuuu
Nấm: zun với t ai đẹp hơn
Zun: m chứ ai nữa, t đứng cạnh làm nền thôi
Nấm: hứ dỗi r
Zun: thôi mà đừng dỗi, t sai r đc chưaaa
Nấm: =)))
Zun: cười j cười =)))
Nấm: zun ngu
Zun: hog biec"""

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
word_game_response_map = None                     # normalized dictionary, built lazily
word_game_dead_ends = None                        # normalized dead-end words
word_game_start_pool = None                       # easy starts, built lazily
word_game_validity_cache = {}                     # canonical phrase -> bool semantic verdict
word_game_dictionary_phrases = set()              # all phrases already present in static data
unknown_word_phrases = {}                         # missing phrase -> source/verdict/count


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


def canonical_word_game_text(text):
    """Chuẩn hoá câu chơi nhưng giữ dấu Việt để sáng không bị nhập chung với sang."""
    text = unicodedata.normalize("NFC", (text or "").lower())
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def save_game_data():
    """Ghi file tạm rồi replace để hạn chế JSON bị dở khi process tắt ngang."""
    temp_path = GAME_DATA_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(game_profiles, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, GAME_DATA_FILE)
    except OSError as exc:
        log.error("Không save được game_data.json: %s", exc)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def load_game_data():
    """Load profile; file thiếu/hỏng thì dùng dữ liệu rỗng, không làm bot crash."""
    global game_profiles
    try:
        with open(GAME_DATA_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("game_data.json root must be an object")
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
                }
            except (TypeError, ValueError, OverflowError):
                continue
        game_profiles = cleaned
        log.info("Đã load %s profile game", len(game_profiles))
    except FileNotFoundError:
        game_profiles = {}
        save_game_data()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.warning("game_data.json lỗi, dùng data rỗng: %s", exc)
        game_profiles = {}
        save_game_data()


def save_unknown_word_phrases():
    temp_path = UNKNOWN_WORDS_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(unknown_word_phrases, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, UNKNOWN_WORDS_FILE)
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
    return (
        f"{title}\nlv.{profile['level']}\n"
        f"tiền: {profile['balance']:,}đ\n"
        f"thắng/thua: {profile['wins']}/{profile['losses']}\n"
        f"tỉ lệ thắng: {rate_text}%"
    )


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
        if len(words) == 2 and words[-1] not in word_game_dead_ends and words[-1] in word_game_response_map:
            word_game_start_pool.append(phrase)
    log.info(
        "Đã index từ điển nối từ: %s key, %s câu",
        len(word_game_response_map),
        sum(len(items) for items in word_game_response_map.values()),
    )


def choose_word_game_start():
    ensure_word_game_dictionary()
    return random.choice(word_game_start_pool or START_PHRASES)


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
        ):
            candidates.append((phrase, normalized, words[-1]))
    if not candidates:
        return None
    # RESPONSE_MAP xếp cụm tự nhiên trước, phần sinh tự động nằm sau. Chỉ quét nhóm
    # đầu, tránh từ cụt và hậu tố filler để ván chơi công bằng, dễ hiểu.
    candidates = candidates[:4]
    non_dead = [item for item in candidates if item[2] not in word_game_dead_ends]
    pool = non_dead or candidates
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
    ):
        return None
    return re.sub(r"\s+", " ", answer).strip().lower()


async def ai_word_game_fallback(last_word, used_phrases, used_required_words=None):
    used = ", ".join(sorted(used_phrases)[:WORD_GAME_MAX_AI_USED])
    prompt = (
        "Tìm 1 cụm nối từ tiếng Việt đúng 2 từ.\n"
        f'Cụm phải bắt đầu bằng từ: "{last_word}".\n'
        f"Không dùng các cụm đã dùng: {used}.\n"
        "Ưu tiên cụm tự nhiên, phổ biến và có thể nối tiếp.\n"
        "Chỉ trả về đúng cụm 2 từ, không giải thích. Nếu không nghĩ ra trả về PASS."
    )
    messages = [
        {"role": "system", "content": "Chỉ làm nhiệm vụ nối từ, không trò chuyện."},
        {"role": "user", "content": prompt},
    ]
    try:
        answer = await _claude(messages, max_tokens=30, temperature=0.2, thinking_budget=0)
    except Exception as exc:
        log.warning("AI nối từ fallback lỗi (%s)", type(exc).__name__)
        return None
    return validate_ai_word_response(answer, last_word, used_phrases, used_required_words)


async def judge_word_game_phrase(phrase, source="không rõ"):
    """Kiểm tra nghĩa bằng AI cho cụm lạ; cache để không tốn token ở lần sau."""
    canonical = canonical_word_game_text(phrase)
    if canonical in word_game_validity_cache:
        valid = word_game_validity_cache[canonical]
        record_unknown_word_phrase(canonical, source, valid)
        return valid
    if canonical in WORD_GAME_ALWAYS_VALID:
        word_game_validity_cache[canonical] = True
        record_unknown_word_phrase(canonical, source, True)
        return True
    if canonical in WORD_GAME_ALWAYS_INVALID:
        word_game_validity_cache[canonical] = False
        record_unknown_word_phrase(canonical, source, False)
        return False
    if canonical in {canonical_word_game_text(item) for item in FAIR_WORD_GAME_STARTS}:
        word_game_validity_cache[canonical] = True
        return True
    prompt = (
        "Kiểm tra một lượt NỐI TỪ tiếng Việt. Cụm hợp lệ khi 2 từ ghép lại tạo ý nghĩa tự nhiên "
        "mà người Việt hiểu được; KHÔNG bắt buộc là thành ngữ hay cụm từ cố định trong từ điển.\n"
        f"Cụm: {canonical}\n"
        "VALID: ảnh nét, túi da, ngọt lịm, người ngợm, nhiếc móc, hình ảnh, móc túi.\n"
        "INVALID: ngợm nhiếc, đạc đồ, hài bài, ambient kính, hai từ ghép máy không tạo nghĩa.\n"
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
    if re.fullmatch(r"\s*VALID[.!]?\s*", verdict, re.IGNORECASE):
        valid = True
    elif re.fullmatch(r"\s*INVALID[.!]?\s*", verdict, re.IGNORECASE):
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
                    "(kể cả danh từ+tính từ như ảnh nét) thì trả VALID. Chỉ khi thật sự vô nghĩa mới trả INVALID. "
                    "Chỉ trả đúng một nhãn."
                ),
            },
        ]
        try:
            recheck = await _claude(recheck_messages, max_tokens=8, temperature=0, thinking_budget=0)
        except Exception:
            record_unknown_word_phrase(canonical, source)
            return None
        if re.fullmatch(r"\s*VALID[.!]?\s*", recheck, re.IGNORECASE):
            valid = True
        elif re.fullmatch(r"\s*INVALID[.!]?\s*", recheck, re.IGNORECASE):
            valid = False
        else:
            record_unknown_word_phrase(canonical, source)
            return None
    else:
        record_unknown_word_phrase(canonical, source)
        return None
    word_game_validity_cache[canonical] = valid
    record_unknown_word_phrase(canonical, source, valid)
    return valid


async def choose_semantic_word_response(last_word, used_phrases, used_required_words):
    """Thử tối đa 4 câu dictionary; chỉ trả câu đã qua kiểm nghĩa."""
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

    candidate = await ai_word_game_fallback(last_word, used_phrases, used_required_words)
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


async def finish_word_game_win(message, session):
    key = (message.channel.id, message.author.id)
    profile = game_profile_for(message.author)
    prize = session["bet"] * 2
    profile["balance"] += prize
    update_game_result(profile, won=True)
    word_game_sessions.pop(key, None)
    await send_reply(
        message,
        f"t bí từ rồi\nm thắng +{prize:,}đ\nsố dư giờ: {profile['balance']:,}đ",
        remember=False,
    )


async def finish_word_game_loss(message, session, reason=""):
    key = (message.channel.id, message.author.id)
    profile = game_profile_for(message.author)
    update_game_result(profile, won=False)
    word_game_sessions.pop(key, None)
    prefix = f"{reason}\n" if reason else ""
    await send_reply(
        message,
        f"{prefix}m thua mất {session['bet']:,}đ\nsố dư giờ: {profile['balance']:,}đ",
        remember=False,
    )


async def handle_word_game_session(message, prompt, session):
    now = time.time()
    if now - session["updated_at"] > WORD_GAME_TIMEOUT_SECONDS:
        if session["state"] == "active":
            await finish_word_game_loss(message, session, "quá 5 phút không nối, xử thua")
        else:
            word_game_sessions.pop((message.channel.id, message.author.id), None)
            await send_reply(message, "hết 5 phút rồi, t hủy kèo cược", remember=False)
        return

    plain = normalize_word_game_text(prompt)
    if is_word_game_status_request(plain):
        if session["state"] == "waiting_bet":
            await send_reply(message, "đang chờ m nhập tiền cược", remember=False)
        else:
            await send_reply(
                message,
                f'đang chơi, m phải nối 2 từ bắt đầu bằng "{session["last_word"]}"',
                remember=False,
            )
        return
    if is_word_game_request(plain):
        state_text = "đang chờ m đặt cược rồi" if session["state"] == "waiting_bet" else "đang chơi rồi, nối câu hiện tại đi"
        await send_reply(message, state_text, remember=False)
        return
    if plain in {"huy", "dung", "bo cuoc", "chiu"}:
        if session["state"] == "active":
            await finish_word_game_loss(message, session, "m bỏ cuộc")
        else:
            word_game_sessions.pop((message.channel.id, message.author.id), None)
            await send_reply(message, "ok hủy kèo", remember=False)
        return

    profile = game_profile_for(message.author)
    if session["state"] == "waiting_bet":
        bet = parse_word_game_bet(prompt)
        if bet is None or bet > profile["balance"]:
            await send_reply(
                message,
                f"tiền cược phải từ 1đ tới {profile['balance']:,}đ",
                remember=False,
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
            "started_at": now,
            "updated_at": now,
        })
        await send_reply(
            message,
            f"ok cược {bet:,}đ\nluật: đúng 2 từ, nối chữ cuối, không lặp hoặc đảo cụm cũ\n"
            f"t ra trước: {start_phrase}\nm nối từ bắt đầu bằng: {words[-1]}",
            remember=False,
        )
        return

    phrase_key = canonical_word_game_text(prompt)
    words = phrase_key.split()
    if len(words) != 2:
        await finish_word_game_loss(message, session, "sai luật rồi, phải nói đúng 2 từ")
        return
    if words[0] != session["last_word"]:
        await finish_word_game_loss(
            message,
            session,
            f'sai luật rồi, phải bắt đầu bằng "{session["last_word"]}"',
        )
        return
    if phrase_key in session["used_phrases"]:
        await finish_word_game_loss(message, session, "cụm đó dùng rồi")
        return
    used_required_words = session.setdefault("used_required_words", {session["last_word"]})
    semantic_verdict = await judge_word_game_phrase(phrase_key, source="người chơi")
    if semantic_verdict is False:
        await send_reply(
            message,
            f'cụm "{phrase_key}" nghe không có nghĩa, đổi cụm khác bắt đầu bằng '
            f'"{session["last_word"]}"; chưa tính thua',
            remember=False,
        )
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

    await send_reply(
        message,
        f"hợp lệ\nt nối: {response}\nm nối tiếp bằng: {response_words[-1]}",
        remember=False,
    )


async def handle_word_game_intents(message, prompt, invoked, replied_message=None):
    """Return True khi profile/game đã xử lý và on_message phải dừng."""
    key = (message.channel.id, message.author.id)
    session = word_game_sessions.get(key)
    if session:
        await handle_word_game_session(message, prompt, session)
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
        word_game_sessions[key] = {
            "state": "waiting_bet",
            "bet": 0,
            "current_phrase": "",
            "last_word": "",
            "used_phrases": set(),
            "started_at": time.time(),
            "updated_at": time.time(),
        }
        await send_reply(
            message,
            f"đặt bao nhiêu tiền, số dư m có {profile['balance']:,}đ\n"
            "thắng ăn gấp đôi thua mất cược",
            remember=False,
        )
        return True
    return False


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
    """Nấm gọi mà không nói gì: chủ yếu saooooo, thỉnh thoảng đổi vị."""
    pool = [
        gf_stretch("sao"),
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
    if isinstance(error, anthropic.RateLimitError):
        return random.choice(RATE_LIMIT_EXCUSES)
    return random.choice(ERROR_EXCUSES)


# Model doi 4.6+ (sonnet-5, opus-4-6...): adaptive thinking tu bat, CAM temperature/budget_tokens.
NEWGEN_MODEL_PREFIXES = ("claude-sonnet-5", "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-fable")


async def _claude(messages, max_tokens=CHAT_MAX_TOKENS, temperature=0.85, thinking_budget=0, model=None):
    """Gọi Anthropic Messages API; SDK tự retry 429/5xx (max_retries mặc định 2).

    Model cũ (Haiku 4.5): thinking_budget > 0 thì bật extended thinking,
    budget cộng thêm vào max_tokens và không được truyền temperature.
    Model đời 4.6+ : thinking adaptive tự chạy, API cấm temperature lẫn budget_tokens,
    thinking_budget chỉ dùng làm chỗ dư trong max_tokens cho phần nghĩ.
    """
    system_parts = []
    claude_messages = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            claude_messages.append({"role": m["role"], "content": m["content"]})

    use_model = model or MODEL
    extra = {}
    if use_model.startswith(NEWGEN_MODEL_PREFIXES):
        if thinking_budget > 0:
            # adaptive thinking tu bat khi khong truyen thinking; chi can du cho trong max_tokens
            max_tokens += thinking_budget
        else:
            extra["thinking"] = {"type": "disabled"}  # chat thuong: tra loi lien khong nghi
    elif thinking_budget > 0:
        extra["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        max_tokens += thinking_budget
    else:
        extra["temperature"] = temperature

    resp = await claude.messages.create(
        model=use_model,
        max_tokens=max_tokens,
        system="\n\n".join(system_parts),
        messages=claude_messages,
        **extra,
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
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


@bot.event
async def on_ready():
    global _synced
    log.info(f"Đã đăng nhập: {bot.user} (id={bot.user.id})")
    if not _synced:
        try:
            synced = await bot.tree.sync()
            log.info(f"Đã sync {len(synced)} slash command")
            _synced = True
        except Exception as e:
            log.error(f"Lỗi sync slash command: {e}")


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

    if not (is_ask or mentioned or wake or reply_to_bot or prefix_moderation or has_game_session):
        return

    gid = get_gid(message)

    # Game bắt cả tin cược/câu nối không ping bot và luôn chạy trước cooldown/roast/AI.
    if is_ask:
        prompt = content[5:].strip()
    else:
        prompt = extract_prompt(message)
    invoked = is_ask or mentioned or wake or reply_to_bot
    if await handle_word_game_intents(message, prompt, invoked, ref):
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


@bot.tree.command(
    name="cactukotrongtudien",
    description="Xuất các cụm nối từ ngoài từ điển (chỉ chủ bot)",
)
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
        f"đã xuất {len(unknown_word_phrases)} cụm, copy file này gửi lại cho t",
        file=attachment,
        ephemeral=True,
    )


def build_help_text():
    return (
        "**Lệnh Zun:**\n"
        "`Zun tạo tài khoản` mở profile game\n"
        "`Zun profile` xem tiền/thắng thua\n"
        "`Zun chơi nối từ` chơi nối từ đặt cược\n"
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
async def slash_helpzun(interaction: discord.Interaction):
    await interaction.response.send_message(build_help_text())


@bot.tree.command(name="commands", description="Xem lệnh của Zun")
async def slash_commands(interaction: discord.Interaction):
    await interaction.response.send_message(build_help_text())


@bot.tree.command(name="status", description="Tình trạng của Zun")
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
if not ANTHROPIC_API_KEY:
    raise SystemExit("Thiếu ANTHROPIC_API_KEY (hoặc CLAUDE_API_KEY) trong .env")

load_game_data()
load_unknown_word_phrases()
start_keepalive_server()
bot.run(DISCORD_TOKEN)
