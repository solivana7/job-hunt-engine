import os

# Unit tests must never call live APIs. Set to "" (not pop): dotenv re-loads
# removed vars at engine import; empty strings stay falsy for llm_available().
for var in ("KIMI_API_KEY", "MOONSHOT_API_KEY", "SERPAPI_KEY", "SERPER_API_KEY",
            "RESEND_API_KEY"):
    os.environ[var] = ""
