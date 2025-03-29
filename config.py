import os
from dotenv import load_dotenv

load_dotenv()

# --- Branch Configuration ---
KNOWN_BRANCHES = [
    "السلالم",
    "المدينة",
    "الصويفية",
    "المركز الرئيسي",
    # Add all your branch names here exactly as expected
]

# --- WhatsApp Configuration ---
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN')
WHATSAPP_API_TOKEN = os.getenv('WHATSAPP_API_TOKEN')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
WHATSAPP_API_VERSION = 'v18.0' # Specify API Version

# --- OpenAI Configuration ---
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL_NLU = os.getenv('OPENAI_MODEL_NLU', 'gpt-4o-mini')
OPENAI_MODEL_STT = os.getenv('OPENAI_MODEL_STT', 'whisper-1')

# --- Database Configuration ---
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set.")


# --- Other Settings ---
DEFAULT_CURRENCY = "JOD"

# --- Error Messages (Arabic) ---
ERROR_MSG_GENERIC = "عذراً، حدث خطأ فني. يرجى المحاولة لاحقاً أو إبلاغ المسؤول."
ERROR_MSG_UNDERSTAND = "عذراً، لم أتمكن من فهم طلبك بوضوح. هل يمكنك إعادة الصياغة أو تقديم تفاصيل أكثر؟"
ERROR_MSG_BRANCH_NOT_FOUND = "عذراً، لم أتعرف على اسم الفرع '{}'. الفروع المعروفة هي: {}"
ERROR_MSG_MISSING_INFO_TRANSFER = "لم أتمكن من تحديد المبلغ أو فرع المصدر أو فرع الوجهة بوضوح. يرجى ذكرها في طلبك."
ERROR_MSG_MISSING_INFO_QUERY = "لم أتمكن من تحديد الفرع للاستعلام عنه بوضوح."
ERROR_MSG_STT_FAILED = "عذراً، لم أتمكن من فهم الرسالة الصوتية. يرجى المحاولة مرة أخرى بصوت أوضح."
ERROR_MSG_API_DOWN = "عذراً، هناك مشكلة في الوصول إلى الخدمات الخارجية. يرجى المحاولة لاحقاً."

CONFIRM_TRANSFER_MSG = "✅ تم تسجيل تحويل {} {} من فرع {} إلى فرع {} بنجاح."
QUERY_RESULT_MSG = "إجمالي التحويلات من فرع {} لهذا اليوم هو: {} {}."
QUERY_NO_RESULT_MSG = "لم يتم العثور على أي تحويلات مسجلة من فرع {} لهذا اليوم."
BRANCH_SELF_TRANSFER_MSG = "لا يمكن التحويل من فرع إلى نفسه."