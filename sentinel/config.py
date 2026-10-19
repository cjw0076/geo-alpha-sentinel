"""Runtime configuration (environment variables only; no secrets in the repo)."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

VERSION = "2.0.0"

# SEC requires a descriptive User-Agent with a contact address.
# Format required by SEC: "<org or name> <contact email>". Deployers should set their own.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "GeoAlphaSentinel admin@geoalphasentinel.org")

# Writable cache directory. On Vercel only /tmp is writable.
CACHE_DIR = Path(os.getenv("SENTINEL_CACHE_DIR", "/tmp/sentinel-cache"))

# LLM: Vercel AI Gateway (OpenAI-compatible). On Vercel the OIDC token is injected
# automatically; locally set AI_GATEWAY_API_KEY. Without either the agent runs its
# deterministic planner and template synthesizer.
AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "https://ai-gateway.vercel.sh/v1")
LLM_MODEL = os.getenv("SENTINEL_LLM_MODEL", "anthropic/claude-sonnet-5.5")
LLM_FAST_MODEL = os.getenv("SENTINEL_LLM_FAST_MODEL", "anthropic/claude-haiku-5.5")
LLM_DISABLED = os.getenv("SENTINEL_LLM_DISABLED", "0") == "1"
LLM_DAILY_RUN_CAP = int(os.getenv("SENTINEL_LLM_DAILY_RUN_CAP", "300"))

# Monte Carlo defaults
MC_PATHS = int(os.getenv("SENTINEL_MC_PATHS", "20000"))
MC_SEED = int(os.getenv("SENTINEL_MC_SEED", "42"))
