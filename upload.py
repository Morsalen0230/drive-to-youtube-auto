import os
import io
import json
import random
import re
import time
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

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
        "folder_id": "drive_folder_id_1",
        "attribution": "folder 1 attribution",
    },
    {
        "folder_id": "drive_folder_id_2",
        "attribution": "folder 2 attribution",
    },
    {
        "folder_id": "drive_folder_id_3",
        "attribution": "folder 3 attribution",
    },
    # নতুন ফোল্ডার যোগ করতে উপরের ব্লকের শেষে কমা (,) রেখে নিচে একইভাবে লিখুন:
    # {
    #     "folder_id": "drive_folder_id_4",
    #     "attribution": "folder 4 attribution",
    # },
    # {
    #     "folder_id": "drive_folder_id_5",
    #     "attribution": "folder 5 attribution",
    # },
]

STATE_FILE = "uploaded_videos.json"

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
            return json.load(f)
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

    # টাইটেল ১০০ ক্যারেক্টারের বেশি হলে শেষের #হ্যাশট্যাগ ঠিক রেখে মূল অংশ ছোট করবে
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

def parse_metadata_json(raw_text, fallback_title):
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```json\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

    title = clean_title(data.get("title", fallback_title), fallback_title)
    description = clean_description(data.get("description", fallback_title), fallback_title)
    tags = clean_tags(data.get("tags"))
    return title, description, tags

def get_available_gemini_models(headers):
    fallback_models = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"]
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
        "x-goog-api-key": GEMINI_API_KEY
    }
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    models = get_available_gemini_models(headers)
    max_rounds = 3  # Gemini-তে মোট ৩ বার চেষ্টা করবে

    for attempt in range(1, max_rounds + 1):
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                res = requests.post(url, headers=headers, json=payload, timeout=30)
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
        "Authorization": f"Bearer {GROQ_API_KEY}"
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
                        "You are an expert Bengali YouTube SEO strategist and content writer. "
                        "Always respond strictly with valid JSON containing keys: title, description, tags. "
                        "Write a rich, detailed, visually well-formatted Bengali YouTube description following every section, divider, bullet, and hashtag rule."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7
        }

        for use_json_mode in (True, False):
            payload = dict(base_payload)
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}

            try:
                res = requests.post(url, headers=headers, json=payload, timeout=30)
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
        "You are a top Bengali YouTube SEO expert and professional content writer for an educational channel. "
        "Based on the video filename below, generate highly optimized, engaging YouTube metadata:\n\n"
        "1. title:\n"
        "   - Write a highly clickable (high-CTR), search-optimized Bengali YouTube title that sparks curiosity while staying clear and accurate.\n"
        "   - Place the primary Bengali search keyword right within the first 3 to 5 words so it ranks strongly in YouTube Search.\n"
        "   - Use a clean, natural structure when appropriate (e.g., 'মূল কিওয়ার্ড | আকর্ষণীয় প্রশ্ন বা চমকপ্রদ তথ্য!' or a crisp, punchy single phrase in natural Bengali—never robotic literal translation).\n"
        "   - MUST include 2 to 3 short, relevant hashtags right at the end of the title (e.g., #shorts #learn #bangla or #shorts #বাংলা + 1 short topic hashtag).\n"
        "   - Keep the ENTIRE title (Bengali text + hashtags combined) between 55 and 92 characters (strictly under 95 characters) so nothing gets cut off.\n\n"
        "2. description:\n"
        "   - Write a rich, comprehensive, visually attractive, and deeply SEO-optimized YouTube description (around 220 to 350 words) strictly in natural, fluent Bengali.\n"
        "   - Follow this exact clean visual layout using blank lines (\\n\\n) and thin dividers (━━━━━━━━━━━━━━━━━━━━) so it looks polished and easy to read on YouTube:\n\n"
        "     * আকর্ষণীয় ওপেনিং হুক (3-4 lines, no heading): Start immediately with an engaging hook containing the primary Bengali search keywords in the first 2 lines (visible above 'More'), explaining clearly what the topic is and why watching this video matters.\n\n"
        "     ━━━━━━━━━━━━━━━━━━━━\n"
        "     📖 ভিডিওর বিস্তারিত আলোচনা:\n"
        "     Write a clear, informative 4 to 6 line paragraph explaining the concept in simple, engaging Bengali, naturally weaving in secondary and long-tail Bengali search phrases.\n\n"
        "     📌 এই ভিডিও থেকে যা যা শিখবেন:\n"
        "     Write 5 to 6 well-explained bullet points (each starting with ✅ on a new line) highlighting the key lessons, facts, or practical takeaways.\n\n"
        "     💡 কেন এই বিষয়টি জানা জরুরি:\n"
        "     Write 2 to 3 lines explaining how this knowledge helps students, learners, or curious viewers in real life or studies.\n\n"
        "     ━━━━━━━━━━━━━━━━━━━━\n"
        "     🔔 আমাদের সাথে যুক্ত থাকুন:\n"
        "     Write a warm, friendly 2 to 3 line Bengali Call-to-Action inviting viewers to Like (👍) the video, share their thoughts or questions in the Comments (💬), Share with friends, and Subscribe to the channel for regular educational videos.\n\n"
        "     * Hashtags Line (at the bottom): Include 8 to 10 relevant hashtags on the final line (must include #shorts #learn #bangla #শিক্ষামূলক plus topic-specific Bangla & English hashtags).\n"
        "   - Never use angle brackets (< or >). Avoid spammy comma-separated raw keyword dumps inside the description.\n\n"
        "3. tags:\n"
        "   - A JSON list of 15 to 18 high-search-volume YouTube tags (without # symbol).\n"
        "   - Mix natural Bengali search terms, popular Banglish search queries (used by Bangladeshi viewers), and a few relevant English educational keywords.\n\n"
        "Strict Language Rule: Regardless of whether the video filename is in English, Bangla, or Banglish, ALWAYS write the title text and the entire description prose strictly in natural, fluent, standard Bengali.\n"
        "Return ONLY valid JSON with exact keys: title, description, tags.\n\n"
        f"Video Filename: {filename}"
    )

    # ১. প্রথমে Gemini API দিয়ে ৩ বার চেষ্টা করবে
    result = generate_with_gemini(prompt, fallback_title)
    if result is not None:
        return result

    # ২. Gemini ৩ বার ফেইল করলে Groq-এর অ্যাক্টিভ মডেল ব্যবহার করবে
    result = generate_with_groq(prompt, fallback_title)
    if result is not None:
        return result

    return None

def download_video(drive, file_id, dest_path):
    request = drive.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Downloading: {int(status.progress() * 100)}%")

def upload_to_youtube(youtube, video_path, title, description, tags):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "27"  # 27 = Education
        },
        "status": {
            "privacyStatus": "public",          # সরাসরি পাবলিক হিসেবে আপলোড হবে
            "selfDeclaredMadeForKids": False,   # No, it's not made for kids
            "containsSyntheticMedia": False     # AI use: No
        },
        "paidProductPlacementDetails": {
            "hasPaidProductPlacement": False    # Paid promotion: No
        }
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
    print(f"Published! Video ID: {response.get('id')}")

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
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", val)
    if match:
        return match.group(1)
    return val

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
    attr_text = str(attribution or "").strip()
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

    # ফোল্ডারগুলো র‍্যান্ডম অর্ডারে সাজিয়ে প্রথম যে ফোল্ডারে নতুন ভিডিও পাবে সেটি নেবে
    shuffled_folders = list(configured_folders)
    random.shuffle(shuffled_folders)

    selected_folder = None
    selected_video = None

    for folder in shuffled_folders:
        folder_id = folder["folder_id"]
        files = list_drive_videos(drive, folder_id)
        new_files = [f for f in files if f["id"] not in uploaded_set]
        if new_files:
            selected_folder = folder
            selected_video = new_files[0]  # পুরনো থেকে নতুন ক্রমে প্রথম আন-আপলোডেড ভিডিও
            break
        print(f"No new videos in Folder #{folder['index']} ({folder_id}). Checking next folder...")

    if not selected_video:
        print("No new videos found in any configured Drive folder.")
        return

    file_id = selected_video["id"]
    file_name = selected_video["name"]
    attribution = selected_folder["attribution"]
    temp_path = "/tmp/temp_video.mp4"

    print(f"\nSelected Folder #{selected_folder['index']} ({selected_folder['folder_id']})")
    print(f"Processing: {file_name}")
    metadata = generate_seo_metadata(file_name)

    if metadata is None:
        print("Both Gemini and Groq APIs failed. Skipping upload for this run so it retries cleanly next time.")
        return

    title, description, tags = metadata
    description = append_attribution(description, attribution)
    print(f"Generated Title: {title}")
    if attribution:
        print(f"Appended Attribution: {attribution}")

    download_video(drive, file_id, temp_path)
    upload_to_youtube(youtube, temp_path, title, description, tags)

    if os.path.exists(temp_path):
        os.remove(temp_path)

    uploaded_ids.append(file_id)
    save_uploaded_ids(uploaded_ids)

if __name__ == "__main__":
    main()
