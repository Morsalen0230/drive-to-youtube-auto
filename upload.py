import base64
import html
import io
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageStat
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"].strip()
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"].strip()
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"].strip()
FALLBACK_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# ==============================================================================
# GOOGLE DRIVE FOLDERS & ATTRIBUTION SETTINGS
# নিচে কমা (,) দিয়ে ২, ৩, ৪... ১০ বা যত খুশি ফোল্ডার যোগ করতে পারবেন।
# - "folder_id": আপনার Google Drive ফোল্ডারের ID (বা ফোল্ডারের লিংক) বসান
# - "attribution": YouTube ডেসক্রিপশনের একদম শেষে যে লেখাটি যোগ হবে (না চাইলে "" রাখুন)
# ==============================================================================
DRIVE_FOLDERS = [
    {
        "folder_id": "1GmFpEtNuRaJK3yQiJK3XdG1fpW_3JNO5",
        "attribution": """Original Video: Ad Astra Muse - Nozima Qadamova (4K) | Early Bloomer (E65)
Creator: Ad Astra
Source: https://www.youtube.com/watch?v=IN6RBu-j6JQ
License: Creative Commons Attribution (CC BY).""",
    },
    {
        "folder_id": "1DskbS2pUpDFQeWsPjSl0ATvLpDrWWM0s",
        "attribution": """Original Video: IELTS Speaking Test- Perfect Band 9
Creator: IELTS Advantage
Source: https://www.youtube.com/watch?v=IevmdO16GuE
License: Creative Commons Attribution (CC BY).""",
    },
    {
        "folder_id": "1omq1SmE2TCpeD36wNVwbcSbNnkXhd1rn",
        "attribution": """Original Video: IELTS Speaking- Perfect Pronunciation and Fluency
Creator: IELTS Advantage
Source: https://www.youtube.com/watch?v=8pnGARTYMpg
License: Creative Commons Attribution (CC BY).""",
    },
]

STATE_FILE = "uploaded_videos.json"

VIRAL_THUMB_THEMES = [
    {
        "name": "mrbeast_yellow",
        "badge_bg": "linear-gradient(135deg, #FF0844 0%, #FF5500 100%)",
        "badge_text": "#FFFFFF",
        "badge_border": "#FFFFFF",
        "badge_glow": "rgba(255, 20, 68, 0.75)",
        "tape_bg": "linear-gradient(135deg, #FFE600 0%, #FFB800 100%)",
        "tape_text": "#080808",
        "tape_stroke": "0px transparent",
        "tape_border": "#000000",
        "accent_glow": "rgba(255, 196, 0, 0.40)",
        "frame_border": "#FFD600",
    },
    {
        "name": "crimson_shock",
        "badge_bg": "linear-gradient(135deg, #101218 0%, #1E2230 100%)",
        "badge_text": "#FFE600",
        "badge_border": "#FFE600",
        "badge_glow": "rgba(255, 214, 0, 0.55)",
        "tape_bg": "linear-gradient(135deg, #FF0F39 0%, #D6002A 100%)",
        "tape_text": "#FFFFFF",
        "tape_stroke": "5px #000000",
        "tape_border": "#FFFFFF",
        "accent_glow": "rgba(255, 15, 57, 0.42)",
        "frame_border": "#FF1744",
    },
    {
        "name": "neon_emerald",
        "badge_bg": "linear-gradient(135deg, #FF1744 0%, #FF6D00 100%)",
        "badge_text": "#FFFFFF",
        "badge_border": "#FFFFFF",
        "badge_glow": "rgba(255, 23, 68, 0.68)",
        "tape_bg": "linear-gradient(135deg, #69F0AE 0%, #00E676 100%)",
        "tape_text": "#05140B",
        "tape_stroke": "0px transparent",
        "tape_border": "#000000",
        "accent_glow": "rgba(0, 230, 118, 0.34)",
        "frame_border": "#00E676",
    },
    {
        "name": "electric_cyan",
        "badge_bg": "linear-gradient(135deg, #7C4DFF 0%, #D500F9 100%)",
        "badge_text": "#FFFFFF",
        "badge_border": "#FFFFFF",
        "badge_glow": "rgba(213, 0, 249, 0.68)",
        "tape_bg": "linear-gradient(135deg, #18FFFF 0%, #00E5FF 100%)",
        "tape_text": "#041016",
        "tape_stroke": "0px transparent",
        "tape_border": "#000000",
        "accent_glow": "rgba(0, 229, 255, 0.36)",
        "frame_border": "#00E5FF",
    },
]


def get_google_services():
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    drive = build("drive", "v3", credentials=creds)
    youtube = build("youtube", "v3", credentials=creds)
    return drive, youtube


def load_uploaded_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return []
            data = json.loads(content)
            return data if isinstance(data, list) else []
    return []


def save_uploaded_ids(ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=2)


def clean_title(title, fallback_title):
    raw = str(title or fallback_title).replace("<", "").replace(">", "")
    title = re.sub(r"\s+", " ", raw).strip().strip('"')
    if not title:
        title = re.sub(r"\s+", " ", str(fallback_title)).replace("<", "").replace(">", "").strip()[:100] or "শিক্ষামূলক ভিডিও #shorts"
    if len(title) <= 100:
        return title

    parts = title.split(" ")
    hashtags = []
    while parts and parts[-1].startswith("#"):
        hashtags.insert(0, parts.pop())

    main_text = " ".join(parts).strip(" |-–—")
    tag_str = " ".join(hashtags)

    if tag_str and len(tag_str) < 40:
        max_main = 100 - len(tag_str) - 1
        trimmed_main = main_text[:max_main].rsplit(" ", 1)[0].strip(" |-–—")
        return f"{trimmed_main} {tag_str}".strip()[:100]

    return title[:100].rsplit(" ", 1)[0].strip()


def clean_tags(tags):
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = ["শিক্ষামূলক", "বাংলা টিউটোরিয়াল", "bangla education", "shorts"]

    cleaned = []
    total_len = 0
    for t in tags:
        t = str(t).replace("#", "").replace("<", "").replace(">", "").strip()
        if not t or t in cleaned:
            continue
        if total_len + len(t) + 2 > 450:
            break
        cleaned.append(t)
        total_len += len(t) + 2
    return cleaned or ["শিক্ষামূলক", "বাংলা টিউটোরিয়াল", "education"]


def clean_description(description, fallback_title):
    text = str(description or fallback_title)
    text = text.replace("\\n", "\n").replace("\r\n", "\n")
    text = text.replace("<", "").replace(">", "")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def clean_hook_phrase(text, max_words=4, max_chars=24):
    raw = re.sub(r"#\S+", "", str(text or "")).replace("<", "").replace(">", "")
    raw = re.sub(r"\s+", " ", raw).strip(' "\'“”|-–—')
    if not raw:
        return ""
    words = raw.split(" ")
    if len(words) > max_words:
        raw = " ".join(words[:max_words])
    if len(raw) > max_chars:
        cut = raw[:max_chars].rsplit(" ", 1)[0].strip()
        raw = cut if len(cut) >= 6 else raw[:max_chars].strip()
    return raw


def build_hook_data(data_dict, clean_video_title):
    default_badges = [
        "🔥 ৯৯% মানুষ জানে না!",
        "😱 অবিশ্বাস্য সত্য!",
        "🚨 মারাত্মক ভুল!",
        "🎯 গোপন কৌশল!",
    ]
    badge = clean_hook_phrase(data_dict.get("hook_badge", ""), max_words=4, max_chars=24)
    if not badge:
        badge = random.choice(default_badges)
    elif not any(ord(ch) > 0x2600 for ch in badge):
        badge = f"🔥 {badge}"

    line1 = clean_hook_phrase(data_dict.get("hook_line1", ""), max_words=4, max_chars=22)
    line2 = clean_hook_phrase(data_dict.get("hook_line2", ""), max_words=4, max_chars=22)

    if not line1 or not line2:
        stripped_title = re.sub(r"#\S+", "", clean_video_title)
        stripped_title = re.sub(r"\s+", " ", stripped_title).strip(" |-–—!?")
        parts = [p.strip() for p in re.split(r"[|!?।:—–-]+", stripped_title) if p.strip()]
        if len(parts) >= 2:
            line1 = line1 or clean_hook_phrase(parts[0], max_words=3, max_chars=20)
            line2 = line2 or (clean_hook_phrase(parts[1], max_words=4, max_chars=22) + "!")
        else:
            words = stripped_title.split()
            mid = max(1, min(3, len(words) // 2 or 1))
            line1 = line1 or clean_hook_phrase(" ".join(words[:mid]), max_words=3, max_chars=20)
            rem = " ".join(words[mid:]) if len(words) > mid else "এখনই জেনে নিন!"
            line2 = line2 or clean_hook_phrase(rem, max_words=4, max_chars=22)

    if not line1:
        line1 = "সফল হতে চাইলে"
    if not line2:
        line2 = "এই ১টি কাজ করুন!"
    if not line2.endswith(("!", "?", "।")):
        line2 = f"{line2}!"

    return {"badge": badge, "line1": line1, "line2": line2}


def parse_metadata_json(raw_text, fallback_title):
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()

    try:
        data = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0), strict=False)

    title = clean_title(data.get("title", fallback_title), fallback_title)
    description = clean_description(data.get("description", fallback_title), fallback_title)
    tags = clean_tags(data.get("tags"))
    hooks = build_hook_data(data, title)
    return title, description, tags, hooks


def get_available_gemini_models(headers):
    fallback_models = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
    ]
    try:
        res = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers=headers,
            timeout=20,
        )
        if res.status_code != 200:
            print(f"[Gemini Models List] Error {res.status_code}: {res.text[:200]}")
            return fallback_models

        models_data = res.json().get("models", [])
        blocked_keywords = (
            "tts", "image", "audio", "live", "embed", "vision",
            "computer-use", "robotics", "1.5", "2.0", "8b", "exp"
        )

        candidates = []
        for m in models_data:
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            mid = m.get("name", "").replace("models/", "").strip()
            low = mid.lower()
            if not low.startswith("gemini-") or "flash" not in low:
                continue
            if any(b in low for b in blocked_keywords):
                continue
            candidates.append(mid)

        if not candidates:
            return fallback_models

        def gemini_priority(mid):
            low = mid.lower()
            ver_match = re.match(r"^gemini-(\d+(?:\.\d+)?)-", low)
            version = float(ver_match.group(1)) if ver_match else (2.9 if "latest" in low else 0.0)
            is_preview = 1 if "preview" in low else 0
            is_lite = 1 if "lite" in low else 0
            is_dated = 1 if re.search(r"-(\d{3}|\d{2}-\d{4}|\d{4}-\d{2})$", low) else 0
            return (-version, is_preview, is_lite, is_dated, low)

        modern = [c for c in candidates if not c.startswith("gemini-2.5")]
        pool = modern if modern else candidates
        pool.sort(key=gemini_priority)

        base_set = set(pool)
        deduped = [
            m for m in pool
            if not (
                re.search(r"-(\d{3}|\d{2}-\d{4}|\d{4}-\d{2})$", m)
                and re.sub(r"-(\d{3}|\d{2}-\d{4}|\d{4}-\d{2})$", "", m) in base_set
            )
        ]

        selected = (deduped or pool)[:3]
        print(f"Discovered active Gemini models: {selected}")
        return selected
    except Exception as e:
        print(f"[Gemini Models List] Exception: {e}")
        return fallback_models


def generate_with_gemini(prompt, fallback_title):
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set in GitHub Secrets. Skipping Gemini.")
        return None

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    models = get_available_gemini_models(headers)
    max_rounds = 3

    for attempt in range(1, max_rounds + 1):
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                res = requests.post(url, headers=headers, json=payload, timeout=35)
                if res.status_code != 200:
                    print(f"[Gemini {attempt}/{max_rounds} | {model}] API Error {res.status_code}: {res.text[:200]}")
                    continue

                candidates = res.json().get("candidates", [])
                if not candidates:
                    print(f"[Gemini {attempt}/{max_rounds} | {model}] Empty candidates: {res.text[:200]}")
                    continue

                parts = candidates[0].get("content", {}).get("parts", [])
                text_parts = [p["text"] for p in parts if "text" in p and not p.get("thought")]
                if not text_parts:
                    text_parts = [p["text"] for p in parts if "text" in p]
                raw_text = "\n".join(text_parts).strip()

                result = parse_metadata_json(raw_text, fallback_title)
                print(f"Metadata generated successfully using Gemini ({model}, attempt {attempt}).")
                return result
            except Exception as e:
                print(f"[Gemini {attempt}/{max_rounds} | {model}] Exception: {e}")

        if attempt < max_rounds:
            wait_seconds = attempt * 10
            print(f"Gemini busy. Waiting {wait_seconds} seconds before retry...")
            time.sleep(wait_seconds)

    return None


def get_available_groq_models(headers):
    try:
        res = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=20)
        if res.status_code != 200:
            print(f"[Groq Models List] Error {res.status_code}: {res.text[:200]}")
            return []

        models_data = res.json().get("data", [])
        blocked_keywords = (
            "whisper", "tts", "guard", "safeguard", "embed",
            "audio", "speech", "playai", "orpheus", "moderation", "allam"
        )

        candidates = []
        for m in models_data:
            mid = m.get("id", "")
            if not mid or m.get("active") is False:
                continue
            low = mid.lower()
            if any(b in low for b in blocked_keywords):
                continue
            candidates.append(mid)

        def model_priority(mid):
            low = mid.lower()
            if "llama-4" in low or "maverick" in low or "scout" in low:
                return (0, mid)
            if "qwen" in low:
                return (1, mid)
            if "gpt-oss" in low:
                return (2, mid)
            if "llama" in low and ("70b" in low or "versatile" in low):
                return (3, mid)
            if "kimi" in low or "deepseek" in low:
                return (4, mid)
            if "gemma" in low or "mistral" in low:
                return (5, mid)
            if "llama" in low:
                return (6, mid)
            if "compound" in low:
                return (8, mid)
            return (7, mid)

        candidates.sort(key=model_priority)
        print(f"Discovered active Groq models: {candidates[:8]}")
        return candidates[:6]
    except Exception as e:
        print(f"[Groq Models List] Exception: {e}")
        return []


def generate_with_groq(prompt, fallback_title):
    if not GROQ_API_KEY:
        print("GROQ_API_KEY is not set in GitHub Secrets. Skipping Groq fallback.")
        return None

    print("Switching to Groq API fallback...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }

    groq_models = get_available_groq_models(headers)
    if not groq_models:
        print("No active Groq chat models found.")
        return None

    for model in groq_models:
        base_payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert Bengali YouTube SEO strategist and viral thumbnail hook copywriter. "
                        "Always respond strictly with valid JSON containing keys: title, description, tags, hook_badge, hook_line1, hook_line2."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.7,
        }

        for use_json_mode in (True, False):
            payload = dict(base_payload)
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}

            try:
                res = requests.post(url, headers=headers, json=payload, timeout=35)
                if res.status_code == 400 and use_json_mode:
                    continue
                if res.status_code != 200:
                    print(f"[Groq | {model}] API Error {res.status_code}: {res.text[:200]}")
                    break

                raw_text = res.json()["choices"][0]["message"]["content"]
                result = parse_metadata_json(raw_text, fallback_title)
                print(f"Metadata generated successfully using Groq ({model}).")
                return result
            except Exception as e:
                print(f"[Groq | {model}] Exception: {e}")
                break

    return None


def generate_seo_metadata(filename):
    fallback_title = os.path.splitext(filename)[0].strip()
    prompt = (
        "You are a top Bengali YouTube SEO expert and viral thumbnail hook copywriter for an educational channel. "
        "Based on the video filename below, generate highly optimized YouTube metadata AND scroll-stopping thumbnail hook text:\n\n"
        "1. title:\n"
        "   - Write a highly clickable (high-CTR), search-optimized Bengali YouTube title that sparks intense curiosity.\n"
        "   - Place the primary Bengali search keyword right within the first 3 to 5 words.\n"
        "   - MUST include 2 to 3 short, relevant hashtags right at the end of the title (e.g., #shorts #learn #bangla).\n"
        "   - Keep the ENTIRE title between 55 and 92 characters (strictly under 95 characters).\n\n"
        "2. description:\n"
        "   - Write a rich, comprehensive, visually attractive Bengali YouTube description (around 220 to 350 words) using blank lines (\\n\\n) and thin dividers (━━━━━━━━━━━━━━━━━━━━):\n"
        "     * আকর্ষণীয় ওপেনিং হুক (3-4 lines, no heading) containing primary keywords.\n"
        "     ━━━━━━━━━━━━━━━━━━━━\n"
        "     📖 ভিডিওর বিস্তারিত আলোচনা: (4 to 6 lines in engaging Bengali)\n"
        "     📌 এই ভিডিও থেকে যা যা শিখবেন: (5 to 6 bullet points starting with ✅)\n"
        "     💡 কেন এই বিষয়টি জানা জরুরি: (2 to 3 lines)\n"
        "     ━━━━━━━━━━━━━━━━━━━━\n"
        "     🔔 আমাদের সাথে যুক্ত থাকুন: (2 to 3 lines warm Bengali CTA)\n"
        "     * Hashtags Line at the bottom: 8 to 10 relevant hashtags (#shorts #learn #bangla #শিক্ষামূলক etc.).\n"
        "   - Never use angle brackets (< or >).\n\n"
        "3. tags:\n"
        "   - A JSON list of 15 to 18 high-search-volume YouTube tags (without # symbol), mixing Bengali, Banglish, and English.\n\n"
        "4. VIRAL THUMBNAIL HOOK TEXT (CRITICAL - Must make every viewer freeze and click instantly!):\n"
        "   - hook_badge: 2 to 3 ultra-punchy Bengali curiosity/shock words starting with ONE emoji from [🔥, 😱, 🚨, 🎯] (e.g., '🔥 ৯৯% মানুষ জানে না!', '😱 মারাত্মক ভুল!', '🎯 গোপন কৌশল!'). Max 20 characters.\n"
        "   - hook_line1: 2 to 3 short, bold Bengali words creating tension, question, or problem (e.g., 'আত্মবিশ্বাসের ভয়?', 'ইংরেজিতে কথা বলুন', 'স্বপ্ন পূরণের পথে'). Max 18 characters, NO hashtags.\n"
        "   - hook_line2: 2 to 4 explosive Bengali payoff/climax words ending with '!' or '?' (e.g., '১ মিনিটে জয় করুন!', 'কোনো ভয় ছাড়াই!', 'এই ১টি কাজ করুন!'). Max 20 characters, NO hashtags.\n\n"
        "Strict Language Rule: ALWAYS write title, description, and hook fields strictly in natural, punchy Bengali.\n"
        "Return ONLY valid JSON with exact keys: title, description, tags, hook_badge, hook_line1, hook_line2.\n\n"
        f"Video Filename: {filename}"
    )

    result = generate_with_gemini(prompt, fallback_title)
    if result is not None:
        return result

    result = generate_with_groq(prompt, fallback_title)
    if result is not None:
        return result

    return None


def inspect_video_specs(video_path):
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json",
            video_path,
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return 1080, 1920, 30.0
        info = json.loads(out.stdout)
        streams = info.get("streams", [])
        width = int(streams[0].get("width", 1080)) if streams else 1080
        height = int(streams[0].get("height", 1920)) if streams else 1920
        duration = float(info.get("format", {}).get("duration", 30.0))
        return width, height, max(duration, 1.0)
    except Exception:
        return 1080, 1920, 30.0


def score_candidate_frame(img):
    small = img.convert("RGB")
    small.thumbnail((480, 480))
    w, h = small.size

    subject_box = (int(w * 0.12), int(h * 0.12), int(w * 0.88), int(h * 0.55))
    subject = small.crop(subject_box)

    gray = subject.convert("L")
    stat = ImageStat.Stat(gray)
    mean_lum = stat.mean[0]
    contrast = stat.stddev[0]

    if mean_lum < 32 or mean_lum > 235:
        exposure_weight = 0.25
    elif 60 <= mean_lum <= 195:
        exposure_weight = 1.0
    else:
        exposure_weight = 0.65

    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = ImageStat.Stat(edges).mean[0]

    hsv = subject.convert("HSV")
    saturation = ImageStat.Stat(hsv).mean[1]

    return (sharpness * 2.4 + contrast * 0.9 + saturation * 0.35) * exposure_weight


def extract_best_video_frame(video_path, duration, work_dir):
    ratios = [0.14, 0.22, 0.30, 0.38, 0.48, 0.58, 0.68, 0.78]
    best_path = None
    best_score = -1.0

    for idx, r in enumerate(ratios):
        ts = max(0.2, min(duration - 0.2, duration * r))
        frame_path = os.path.join(work_dir, f"cand_{idx}.jpg")
        cmd = [
            "ffmpeg", "-y", "-ss", f"{ts:.2f}",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            frame_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
            if os.path.exists(frame_path) and os.path.getsize(frame_path) > 1000:
                with Image.open(frame_path) as im:
                    sc = score_candidate_frame(im)
                if sc > best_score:
                    best_score = sc
                    best_path = frame_path
        except Exception:
            continue

    return best_path


def extract_clean_subject_for_youtube(img):
    """
    Prepares the sharpest speaker subject region for a full-bleed 1280x720 YouTube thumbnail:
    - If vertical frame is a framed educational card (bright top header & bottom subtitle boxes
      around an inner video window), automatically extracts the clean inner video window!
    - If vertical frame is a full-bleed 9:16 video, crops the upper portrait half (face & shoulders).
    - If already horizontal (16:9), returns full frame.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    if w >= h:
        return rgb, False  # Native landscape video

    gray = rgb.convert("L")
    top_lum = ImageStat.Stat(gray.crop((0, 0, w, int(h * 0.13)))).mean[0]
    mid_lum = ImageStat.Stat(gray.crop((int(w * 0.08), int(h * 0.17), int(w * 0.92), int(h * 0.43)))).mean[0]
    bot_lum = ImageStat.Stat(gray.crop((0, int(h * 0.50), w, int(h * 0.92)))).mean[0]

    if top_lum > 185 and bot_lum > 185 and mid_lum < (min(top_lum, bot_lum) - 30):
        inner = rgb.crop((int(w * 0.045), int(h * 0.158), int(w * 0.955), int(h * 0.442)))
        return inner, True

    portrait_upper = rgb.crop((0, int(h * 0.04), w, int(h * 0.65)))
    return portrait_upper, True


def render_badge_with_twemoji(badge_text):
    parts = []
    for ch in badge_text:
        cp = ord(ch)
        if cp in (0xFE0F, 0x200D):
            continue
        if cp > 0x2000 and not (0x0980 <= cp <= 0x09FF):
            hex_cp = f"{cp:x}"
            parts.append(
                f'<img class="emoji-icon" src="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/{hex_cp}.svg" '
                f'alt="{html.escape(ch)}" onerror="this.replaceWith(document.createTextNode(this.alt))" />'
            )
        else:
            parts.append(html.escape(ch))
    return "".join(parts)


def find_headless_browser():
    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
        "microsoft-edge",
        "microsoft-edge-stable",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.isabs(c) and os.path.exists(c):
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def build_youtube_16x9_html(bg_data_uri, hook_data, use_split_hero, theme):
    """
    Always builds a crisp 1280x720 (16:9) YouTube thumbnail so YouTube never adds ugly sidebars!
    """
    badge_html = render_badge_with_twemoji(hook_data["badge"])
    line1_esc = html.escape(hook_data["line1"])
    line2_esc = html.escape(hook_data["line2"])

    len1 = len(hook_data["line1"])
    len2 = len(hook_data["line2"])
    l1_size = 80 if len1 <= 13 else (68 if len1 <= 17 else (58 if len1 <= 22 else 48))
    l2_size = 74 if len2 <= 14 else (64 if len2 <= 18 else (54 if len2 <= 23 else 46))

    if use_split_hero:
        media_layers = f"""
        <img class="bg-ambient" src="{bg_data_uri}" alt="" />
        <div class="subject-panel"><img class="subject-img" src="{bg_data_uri}" alt="" /></div>
        <div class="split-scrim"></div>
        """
        media_css = f"""
        .bg-ambient {{
            position: absolute; inset: -40px; width: calc(100% + 80px); height: calc(100% + 80px);
            object-fit: cover; filter: blur(26px) brightness(0.32) saturate(1.60); z-index: 1;
        }}
        .subject-panel {{
            position: absolute; top: 0; right: 0; width: 62%; height: 100%; overflow: hidden; z-index: 2;
            -webkit-mask-image: linear-gradient(to right, transparent 0%, rgba(0,0,0,0.72) 18%, #000000 34%, #000000 100%);
            mask-image: linear-gradient(to right, transparent 0%, rgba(0,0,0,0.72) 18%, #000000 34%, #000000 100%);
        }}
        .subject-img {{
            width: 100%; height: 100%; object-fit: cover; object-position: center 22%;
            transform: scale(1.06); filter: contrast(1.16) saturate(1.26) brightness(1.05);
        }}
        .split-scrim {{
            position: absolute; inset: 0;
            background:
                radial-gradient(circle at 26% 54%, {theme['accent_glow']} 0%, transparent 52%),
                linear-gradient(90deg, rgba(4,6,15,0.97) 0%, rgba(4,6,15,0.90) 40%, rgba(4,6,15,0.32) 64%, rgba(4,6,15,0.04) 100%);
            z-index: 3;
        }}
        .hook-wrap {{
            position: absolute; left: 50px; top: 50%; transform: translateY(-50%);
            width: 60%; display: flex; flex-direction: column; align-items: flex-start; text-align: left; z-index: 10;
        }}
        """
    else:
        media_layers = f"""
        <img class="bg-full" src="{bg_data_uri}" alt="" />
        <div class="full-scrim"></div>
        """
        media_css = f"""
        .bg-full {{
            position: absolute; inset: 0; width: 100%; height: 100%;
            object-fit: cover; object-position: center 22%; transform: scale(1.04);
            filter: contrast(1.15) saturate(1.25) brightness(1.04); z-index: 1;
        }}
        .full-scrim {{
            position: absolute; inset: 0;
            background:
                radial-gradient(ellipse at 26% 78%, {theme['accent_glow']} 0%, transparent 50%),
                linear-gradient(90deg, rgba(3,5,12,0.92) 0%, rgba(3,5,12,0.68) 48%, rgba(3,5,12,0.10) 100%),
                linear-gradient(180deg, rgba(0,0,0,0.08) 0%, rgba(2,3,8,0.88) 100%);
            z-index: 2;
        }}
        .hook-wrap {{
            position: absolute; left: 52px; bottom: 48px; width: 66%;
            display: flex; flex-direction: column; align-items: flex-start; text-align: left; z-index: 10;
        }}
        """

    return f"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anek+Bangla:wght@800&family=Baloo+Da+2:wght@800&family=Hind+Siliguri:wght@700&family=Noto+Sans+Bengali:wght@800;900&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1280px; height: 720px; overflow: hidden; background: #04060f;
    font-family: 'Baloo Da 2', 'Anek Bangla', 'Hind Siliguri', 'Noto Sans Bengali', 'Noto Color Emoji', sans-serif;
  }}
  .canvas {{ position: relative; width: 1280px; height: 720px; overflow: hidden; }}
  {media_css}
  .frame {{
    position: absolute; inset: 0;
    box-shadow: inset 0 0 85px rgba(0,0,0,0.55);
    border: 7px solid {theme['frame_border']}; z-index: 5; pointer-events: none;
  }}
  .badge {{
    display: inline-flex; align-items: center; gap: 12px;
    background: {theme['badge_bg']}; color: {theme['badge_text']};
    font-size: 35px; font-weight: 800; line-height: 1.15;
    padding: 10px 30px 8px 30px; border-radius: 999px;
    border: 3.5px solid {theme['badge_border']};
    box-shadow: 0 10px 28px {theme['badge_glow']}, 0 5px 0 #000000;
    transform: rotate(-1.8deg); margin-bottom: 20px;
  }}
  .emoji-icon {{ width: 38px; height: 38px; flex-shrink: 0; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.45)); }}
  .line1 {{
    color: #FFFFFF; font-size: {l1_size}px; font-weight: 800; line-height: 1.14;
    paint-order: stroke fill; -webkit-text-stroke: 12px #000000;
    text-shadow: 0 6px 0 #000000, 0 12px 24px rgba(0,0,0,0.95);
    margin-bottom: 18px; max-width: 100%;
  }}
  .tape {{
    display: inline-block; background: {theme['tape_bg']};
    border: 4.5px solid {theme['tape_border']}; border-radius: 20px;
    padding: 10px 30px 6px 30px;
    box-shadow: 0 10px 0 #000000, 0 18px 36px rgba(0,0,0,0.92);
    transform: rotate(-1.2deg); max-width: 100%;
  }}
  .line2 {{
    color: {theme['tape_text']}; font-size: {l2_size}px; font-weight: 800; line-height: 1.14;
    paint-order: stroke fill; -webkit-text-stroke: {theme['tape_stroke']};
  }}
</style>
</head>
<body>
  <div class="canvas">
    {media_layers}
    <div class="frame"></div>
    <div class="hook-wrap">
      <div class="badge">{badge_html}</div>
      <div class="line1">{line1_esc}</div>
      <div class="tape"><div class="line2">{line2_esc}</div></div>
    </div>
  </div>
</body>
</html>"""


def save_optimized_jpeg(img, dest_path, max_bytes=1900000):
    rgb = img.convert("RGB")
    for quality in (93, 88, 82, 75):
        rgb.save(dest_path, "JPEG", quality=quality, subsampling=0, optimize=True)
        if os.path.getsize(dest_path) <= max_bytes:
            return
    rgb.save(dest_path, "JPEG", quality=70, optimize=True)


def generate_viral_thumbnail(video_path, dest_thumb_path, hook_data):
    _, _, duration = inspect_video_specs(video_path)
    target_w, target_h = 1280, 720  # YouTube thumbnails.set always expects 16:9 (1280x720)
    theme = random.choice(VIRAL_THUMB_THEMES)

    work_dir = tempfile.mkdtemp(prefix="yt_thumb_")
    try:
        best_frame = extract_best_video_frame(video_path, duration, work_dir)
        if not best_frame:
            fallback_img = Image.new("RGB", (target_w, target_h), (18, 24, 42))
            best_frame = os.path.join(work_dir, "fallback.jpg")
            fallback_img.save(best_frame, "JPEG", quality=92)

        with Image.open(best_frame) as raw_im:
            subject_im, use_split_hero = extract_clean_subject_for_youtube(raw_im)
            subject_path = os.path.join(work_dir, "subject.jpg")
            subject_im.save(subject_path, "JPEG", quality=95)

        with open(subject_path, "rb") as sf:
            data_uri = "data:image/jpeg;base64," + base64.b64encode(sf.read()).decode("ascii")

        browser = find_headless_browser()
        if browser:
            html_path = os.path.join(work_dir, "thumb.html")
            png_path = os.path.join(work_dir, "thumb.png")
            html_content = build_youtube_16x9_html(data_uri, hook_data, use_split_hero, theme)
            with open(html_path, "w", encoding="utf-8") as hf:
                hf.write(html_content)

            file_url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
            cmd = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--window-size={target_w},{target_h}",
                "--virtual-time-budget=4000",
                f"--screenshot={png_path}",
                file_url,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=40)
            if os.path.exists(png_path) and os.path.getsize(png_path) > 5000:
                with Image.open(png_path) as rendered:
                    cropped = rendered.convert("RGB").crop((0, 0, target_w, target_h))
                    save_optimized_jpeg(cropped, dest_thumb_path)
                print(
                    f"Viral 16:9 YouTube thumbnail generated ({target_w}x{target_h}, "
                    f"theme={theme['name']}, size={os.path.getsize(dest_thumb_path) // 1024}KB)."
                )
                return True

        with Image.open(subject_path) as im:
            im = im.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            im = ImageEnhance.Contrast(im).enhance(1.18)
            im = ImageEnhance.Color(im).enhance(1.25)
            save_optimized_jpeg(im, dest_thumb_path)
        print("Fallback enhanced frame thumbnail generated.")
        return True
    except Exception as e:
        print(f"[Warning] Thumbnail generation failed: {e}")
        return False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def download_video(drive, file_id, dest_path):
    request = drive.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Downloading: {int(status.progress() * 100)}%")
    if not os.path.exists(dest_path) or os.path.getsize(dest_path) <= 0:
        raise RuntimeError("Downloaded video file is empty (0 bytes).")


def upload_to_youtube(youtube, video_path, title, description, tags):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,
        },
        "paidProductPlacementDetails": {
            "hasPaidProductPlacement": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=5 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status,paidProductPlacementDetails",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploading to YouTube: {int(status.progress() * 100)}%")
    video_id = response.get("id")
    print(f"Published! Video ID: {video_id}")
    return video_id


def set_youtube_thumbnail(youtube, video_id, thumb_path):
    if not video_id or not os.path.exists(thumb_path):
        return
    # youtube.upload টোকেনেই কাজ করার জন্য videos.list এর বদলে সরাসরি ৪৫ সেকেন্ড প্রসেসিং ওয়েট
    print("Waiting 45 seconds for YouTube video processing before applying custom thumbnail...")
    time.sleep(45)
    try:
        media = MediaFileUpload(thumb_path, mimetype="image/jpeg", resumable=False)
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print(f"Custom 16:9 viral thumbnail applied to YouTube video {video_id}!")
    except Exception as e:
        print(f"[Warning] Could not apply custom YouTube thumbnail (check if channel is phone-verified): {e}")


def list_drive_videos(drive, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false"
    files = []
    page_token = None

    while True:
        results = drive.files().list(
            q=query,
            pageSize=1000,
            fields="nextPageToken, files(id, name)",
            orderBy="createdTime asc",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return files


def extract_folder_id(raw_value):
    val = str(raw_value or "").strip()
    low = val.lower()
    if not val or low.startswith("drive_folder_id") or low.startswith("your_"):
        return ""
    for pattern in (r"/folders/([a-zA-Z0-9_-]+)", r"[?&]id=([a-zA-Z0-9_-]+)"):
        match = re.search(pattern, val)
        if match:
            return match.group(1)
    return val if re.fullmatch(r"[a-zA-Z0-9_-]+", val) else ""


def get_configured_folders():
    active = []
    for idx, entry in enumerate(DRIVE_FOLDERS, start=1):
        folder_id = extract_folder_id(entry.get("folder_id", ""))
        if not folder_id:
            continue
        attribution = str(entry.get("attribution", "") or "").strip()
        active.append({
            "index": idx,
            "folder_id": folder_id,
            "attribution": attribution,
        })

    if not active and FALLBACK_FOLDER_ID:
        fallback_id = extract_folder_id(FALLBACK_FOLDER_ID)
        if fallback_id:
            active.append({
                "index": 1,
                "folder_id": fallback_id,
                "attribution": "",
            })

    return active


def append_attribution(description, attribution):
    base_desc = str(description or "").strip()
    attr_text = str(attribution or "").replace("<", "").replace(">", "").strip()
    if not attr_text:
        return base_desc[:5000]
    max_base_len = max(0, 5000 - len(attr_text) - 2)
    trimmed_base = base_desc[:max_base_len].rstrip()
    return f"{trimmed_base}\n\n{attr_text}" if trimmed_base else attr_text[:5000]


def main():
    configured_folders = get_configured_folders()
    if not configured_folders:
        print("No valid Google Drive folder IDs configured in DRIVE_FOLDERS.")
        return

    drive, youtube = get_google_services()
    uploaded_ids = load_uploaded_ids()
    uploaded_set = set(uploaded_ids)

    shuffled_folders = list(configured_folders)
    random.shuffle(shuffled_folders)

    selected_folder = None
    selected_video = None

    for folder in shuffled_folders:
        folder_id = folder["folder_id"]
        try:
            files = list_drive_videos(drive, folder_id)
        except Exception as e:
            print(f"[Warning] Failed to read Folder #{folder['index']} ({folder_id}): {e}")
            continue
        new_files = [f for f in files if f["id"] not in uploaded_set]
        if new_files:
            selected_folder = folder
            selected_video = new_files[0]
            break
        print(f"No new videos in Folder #{folder['index']} ({folder_id}). Checking next folder...")

    if not selected_video:
        print("No new videos found in any configured Drive folder.")
        return

    file_id = selected_video["id"]
    file_name = selected_video["name"]
    attribution = selected_folder["attribution"]
    temp_path = os.path.join(tempfile.gettempdir(), "temp_video.mp4")
    thumb_path = os.path.join(tempfile.gettempdir(), "temp_thumb.jpg")

    print(f"\nSelected Folder #{selected_folder['index']} ({selected_folder['folder_id']})")
    print(f"Processing: {file_name}")
    metadata = generate_seo_metadata(file_name)

    if metadata is None:
        print("Both Gemini and Groq APIs failed. Skipping upload for this run so it retries cleanly next time.")
        return

    title, description, tags, hook_data = metadata
    description = append_attribution(description, attribution)
    print(f"Generated Title: {title}")
    print(f"Generated Thumbnail Hook: [{hook_data['badge']}] {hook_data['line1']} -> {hook_data['line2']}")
    if attribution:
        print(f"Appended Attribution: {attribution}")

    try:
        download_video(drive, file_id, temp_path)
        thumb_ok = generate_viral_thumbnail(temp_path, thumb_path, hook_data)
        video_id = upload_to_youtube(youtube, temp_path, title, description, tags)
        if thumb_ok and video_id:
            set_youtube_thumbnail(youtube, video_id, thumb_path)
    finally:
        for p in (temp_path, thumb_path):
            if os.path.exists(p):
                os.remove(p)

    uploaded_ids.append(file_id)
    save_uploaded_ids(uploaded_ids)


if __name__ == "__main__":
    main()
