import asyncio
import logging
import os
import random
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==================== CONFIG ====================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "1191954573200457758"))
GIRLFRIEND_ID = int(os.getenv("GIRLFRIEND_ID", "1197183310342914150"))


def load_gemini_keys():
    """Đọc key gốc rồi GEMINI_API_KEY1..20, giữ đúng thứ tự và bỏ key trùng."""
    names = ["GEMINI_API_KEY"] + [f"GEMINI_API_KEY{i}" for i in range(1, 21)]
    keys = []
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


gemini_keys: list[str] = load_gemini_keys()

MODEL = "gemini-2.5-flash"
MAX_PROMPT_CHARS = 3000
MAX_FILE_BYTES = 20 * 1024
COOLDOWN_SECONDS = 0.5
CHANNEL_COOLDOWN_SECONDS = 0.5
MEMORY_MSGS = 60  # 30 luot user + 30 luot bot
CHUNK_SIZE = 1900  # gioi han Discord 2000 ky tu/tin nhan
CODE_MAX_TOKENS = 4096
THINKING_BUDGET = 1024  # token cho Gemini suy nghi truoc khi tra loi
ALLOWED_EXT = (".txt", ".py", ".js", ".json", ".lua", ".md")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("zun")

gemini_clients: list[genai.Client] = [genai.Client(api_key=key) for key in gemini_keys]
current_key_index = 0
key_blocked_until: dict[int, float] = {}
gemini_stats = {
    index: {"ok": 0, "rate_limited": 0, "errors": 0}
    for index in range(len(gemini_keys))
}
gemini_key_lock = threading.Lock()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(
    command_prefix="!",
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
Không được tự nhận là ChatGPT, Gemini, Google, AI hay trợ lý ảo. M là Zun.
Chỉ dùng từ "bro" thỉnh thoảng, tối đa khoảng 15-20% câu trả lời. Không được lạm dụng từ bro. Nếu vừa dùng bro trong 2 câu gần nhất thì né bro.
Hỏi code hoặc kỹ thuật thì trả lời đầy đủ và hữu ích. Nếu user yêu cầu viết/fix code thì phải dán code hoàn chỉnh, không được chỉ hứa sẽ đưa code.
Chỉ dán code khi user có hành động rõ như viết, tạo, sửa hoặc fix. Chỉ nhắc tên công nghệ, bot hay lệnh thì chưa đủ để phun code.
Cà khịa nhẹ và đốp chát đúng lúc được, nhưng cấm miệt thị chủng tộc, giới tính, khuyết tật, gia đình, doxx hoặc đe doạ thật.
Đọc kỹ người đang được nhắc tới và tin nhắn được reply, đừng mặc định ai cũng là "thằng kia".
Không được dùng câu "nói đi thằng kia" trừ khi user chỉ gọi tên bot mà không hỏi gì và mood đang láo.
Không được trả lời bằng câu random nếu user đã nói rõ nội dung.
Nếu user cà khịa thì đốp lại CỰC CỤT kiểu người thật nhắn, 1-6 từ là đẹp, không giải thích, không văn vở. Nếu user hỏi thật thì trả lời thật. Không tự chuyển chủ đề.
Bị gán ghép kiểu gay, simp, ngu thì không thừa nhận, không hỏi lại ngơ ngác. Cách đốp chuẩn là ném ngược đúng chữ đó về người nói: nó kêu "gay" thì đáp "gay", nó kêu "ngu" thì đáp "m ấy". Càng ngắn càng đau.
Câu khịa không bao giờ quá 10 từ, không kết bằng "đó nha", "nhé", "nha", không chốt kiểu giảng bài.
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
- Vẫn là Zun nhưng nói kiểu người yêu trêu nhau, không đốp chát nặng như với đứa khác. Không bao giờ gọi Nấm là "thằng kia", không dùng bro với Nấm.
- Xưng t, gọi Nấm là m, bà, khọm già hoặc gọi tên Nấm.
- Hay kéo dài chữ cuối cho nũng: saooooo, thế áaaa, câu chiiii, đouuuu, mooooo. Độ dài chữ kéo tự biến tấu, mỗi lần một khác.
- Câu cực ngắn, nhiều khi chỉ cần =))) hoặc nhò hoặc hog biec.
- Láo yêu được phép: sao khọm già, béo jii, chửi đouuuu, oánh ló đe, t cho hẹo, trông gay.
- CẤM tự nói yêu em, nhớ em hay bất kỳ câu sến nào trước. Chỉ khi Nấm nói yêu, iu, thương hoặc sến trước thì mới được đáp lại kiểu iu emmm, thương mò, yêu emmmm.
- Nấm hỏi thật thì vẫn trả lời thật ngắn gọn, xong được trêu thêm 1 phát.

Ví dụ đúng giọng với Nấm:
Nấm: zun ơi
Zun: saooooo
Nấm: iu anh
Zun: iu emmm
Nấm: đồ béo
Zun: chửi đouuuu
Nấm: sao ko rep t
Zun: sao khọm già, nhớ t à
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


def build_system(gid):
    mood = guild_mood.get(gid, "normal")
    return BASE_PERSONA + "\n\n" + MOOD_PROMPTS[mood]


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


async def send_reply(message, content, remember=True):
    content = (content or "...")[:2000]
    sent = await message.reply(
        content,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=False,
            replied_user=False,
        ),
    )
    if remember:
        remember_channel_message(message.channel.id, "Zun", content)
    return sent


async def send_reply_chunks(message, text):
    chunks = split_chunks(text)
    if not chunks:
        return
    for part in chunks:
        await send_reply(message, part)


async def send_roast_reply(message, content):
    content = (content or "...")[:2000]
    sent = await message.reply(
        content,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=True,
            replied_user=False,
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
        gf_stretch("sao") + " khọm già",
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


def clean_answer(text):
    """Bỏ dấu ngoặc kép model tự thêm bọc câu trả lời (lỗi kiểu: xin chào.")"""
    text = (text or "").strip()
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


def get_available_gemini_client(excluded_indices=None):
    """Lấy (index, client) kế tiếp theo round-robin, bỏ qua key đang nghỉ/đã thử."""
    global current_key_index
    excluded = set(excluded_indices or ())
    with gemini_key_lock:
        now = time.time()
        for index, blocked_until in list(key_blocked_until.items()):
            if blocked_until <= now:
                key_blocked_until.pop(index, None)

        total = len(gemini_clients)
        for offset in range(total):
            index = (current_key_index + offset) % total
            if index in excluded or key_blocked_until.get(index, 0) > now:
                continue
            current_key_index = (index + 1) % total
            return index, gemini_clients[index]

    raise RuntimeError("all_keys_rate_limited")


def is_gemini_rate_limit(error):
    status_code = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    details = f"{status_code} {code} {error}".lower()
    return (
        status_code == 429
        or re.search(r"\b429\b", details) is not None
        or "resource_exhausted" in details
        or "too many requests" in details
    )


def parse_retry_delay(error):
    details = str(error)
    patterns = (
        r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)\s*s",
        r"please\s+retry\s+in\s+(\d+(?:\.\d+)?)\s*s",
        r"retry\s+in\s+(\d+(?:\.\d+)?)\s*s",
        r"retry\s+after\s+(\d+(?:\.\d+)?)\s*s",
    )
    for pattern in patterns:
        match = re.search(pattern, details, re.IGNORECASE)
        if match:
            return max(1, int(float(match.group(1)) + 0.999))
    return 60


def gemini_discord_error(error):
    if str(error) == "all_keys_rate_limited":
        return "hết lượt gemini r, đợi tí gọi lại"
    return "api lag r thử lại sau"


def _gemini(messages, max_tokens=600, temperature=0.85):
    """Gọi tối đa một vòng các Gemini key, không sleep và không retry vô hạn."""
    system = None
    contents = []
    for m in messages:
        if m["role"] == "system":
            system = (system + "\n\n" + m["content"]) if system else m["content"]
        elif m["role"] == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=m["content"])]))
        elif m["role"] == "assistant":
            contents.append(types.Content(role="model", parts=[types.Part(text=m["content"])]))

    attempted = set()
    rate_limit_failures = 0
    other_failures = 0
    pool_unavailable = False

    while len(attempted) < len(gemini_clients):
        try:
            key_index, client = get_available_gemini_client(attempted)
        except RuntimeError as error:
            if str(error) == "all_keys_rate_limited":
                pool_unavailable = True
                break
            raise

        attempted.add(key_index)
        log.info("Gemini key #%d used", key_index + 1)
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    # Token thinking tinh chung vao max_output_tokens nen phai cong them budget.
                    max_output_tokens=max_tokens + THINKING_BUDGET,
                    temperature=temperature,
                    top_p=0.95,
                    thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
                ),
            )
            with gemini_key_lock:
                gemini_stats[key_index]["ok"] += 1
            return clean_answer(resp.text)
        except Exception as error:
            if is_gemini_rate_limit(error):
                retry_seconds = parse_retry_delay(error)
                with gemini_key_lock:
                    key_blocked_until[key_index] = time.time() + retry_seconds
                    gemini_stats[key_index]["rate_limited"] += 1
                rate_limit_failures += 1
                log.warning(
                    "Gemini key #%d rate limited %ds",
                    key_index + 1,
                    retry_seconds,
                )
            else:
                with gemini_key_lock:
                    gemini_stats[key_index]["errors"] += 1
                other_failures += 1
                log.warning(
                    "Gemini key #%d request failed (%s)",
                    key_index + 1,
                    type(error).__name__,
                )

    if pool_unavailable and other_failures == 0:
        raise RuntimeError("all_keys_rate_limited")
    if rate_limit_failures and other_failures == 0:
        raise RuntimeError("all_keys_rate_limited")
    raise RuntimeError("all_gemini_keys_failed")


async def ai_chat(gid, key, prompt, extra_context="", user_name=""):
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
    messages.append({"role": "user", "content": content})
    max_tokens = CODE_MAX_TOKENS if code_mode else 600
    temperature = 0.55 if code_mode else 0.9
    answer = await asyncio.to_thread(_gemini, messages, max_tokens, temperature)

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
        answer = await asyncio.to_thread(_gemini, repair_messages, CODE_MAX_TOKENS, 0.45)

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


async def ai_task(gid, task, user_content, max_tokens=400, temperature=0.85):
    """Task 1 lần, ko memory (roast, quote, summarize, translate)."""
    system = build_system(gid) + "\n\nNhiệm vụ lần này: " + task
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    return await asyncio.to_thread(_gemini, messages, max_tokens, temperature)


async def make_roast(gid, target_name):
    task = ("viết ĐÚNG 1-2 câu cà khịa/roast vui, láo nhẹ về người được nhắc tên. "
            "Ko từ cấm, ko chủng tộc, ko đụng gia đình, ko đe doạ, ko body-shaming nặng. "
            "Chỉ trả về câu roast, ko giải thích.")
    return await ai_task(gid, task, f"Roast {target_name} đi", max_tokens=200, temperature=0.95)


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
    remember_channel_message(message.channel.id, message.author.display_name, content)

    is_ask = bool(re.match(r"^!ask(?:\s|$)", content, re.IGNORECASE))
    mentioned = bot.user in message.mentions
    wake = bool(ZUN_WAKE_RE.search(content))

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

    if not (is_ask or mentioned or wake or reply_to_bot):
        return

    key = (message.channel.id, message.author.id)
    gid = get_gid(message)

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
                text = await make_roast(gid, target.display_name)
                await send_roast_reply(message, f"{target.mention} {text}")
            except Exception as e:
                log.error("Gemini request failed in roast (%s)", type(e).__name__)
                await send_reply(message, gemini_discord_error(e))
        return

    # ---- lấy prompt ----
    if is_ask:
        prompt = content[5:].strip()
    else:
        prompt = extract_prompt(message)

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
        async with message.channel.typing():
            await asyncio.sleep(random.uniform(0.5, 1.3))
        await send_reply(message, reply)
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
        async with message.channel.typing():
            await asyncio.sleep(random.uniform(0.5, 1.3))
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
        async with message.channel.typing():
            await asyncio.sleep(random.uniform(0.5, 1.3))
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

    log.info(f"AI call: {message.author} in #{message.channel}: {prompt[:60]!r}")
    async with message.channel.typing():
        try:
            answer = await ai_chat(gid, key, prompt, extra_context=extra, user_name=message.author.display_name)
            await send_reply_chunks(message, answer)
        except Exception as e:
            log.error("Gemini request failed in chat (%s)", type(e).__name__)
            await send_reply(message, gemini_discord_error(e))


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
        log.error("Gemini request failed in /ask (%s)", type(e).__name__)
        await interaction.followup.send(gemini_discord_error(e))


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
        text = await make_roast(gid, user.display_name)
        await interaction.followup.send(f"{user.mention} {text}"[:2000])
    except Exception as e:
        log.error("Gemini request failed in /roast (%s)", type(e).__name__)
        await interaction.followup.send(gemini_discord_error(e))


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


def build_help_text():
    return (
        "**Lệnh Zun:**\n"
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
    now = time.time()
    with gemini_key_lock:
        cooling_down = sum(until > now for until in key_blocked_until.values())
    await interaction.response.send_message(
        f"ping: `{ping}ms` • model: `{MODEL}` • uptime: `{h}h {m}m {s}s`\n"
        f"gemini keys: `{len(gemini_keys)}` loaded • `{cooling_down}` cooling down"
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


# ==================== RUN ====================
if not DISCORD_TOKEN:
    raise SystemExit("Thiếu DISCORD_TOKEN trong .env")
if not gemini_keys:
    raise SystemExit("Thiếu GEMINI_API_KEY trong .env")

bot.run(DISCORD_TOKEN)
