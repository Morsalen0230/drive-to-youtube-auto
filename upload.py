import os
import io
import json
import re
import time
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"].strip()
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"].strip()
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"].strip()
FOLDER_ID = os.environ["DRIVE_FOLDER_ID"].strip()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

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
    title = re.sub(r"\s+", " ", str(title or fallback_title)).strip()
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
    description = str(data.get("description", fallback_title)).strip()
    tags = clean_tags(data.get("tags"))
    return title, description, tags

def generate_with_gemini(prompt, fallback_title):
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}
    }

    models = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    max_rounds = 3  # Gemini-তে মোট ৩ বার চেষ্টা করবে

    for attempt in range(1, max_rounds + 1):
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                res = requests.post(url, headers=headers, json=payload, timeout=30)
                if res.status_code != 200:
                    print(f"[Gemini {attempt}/{max_rounds} | {model}] API Error {res.status_code}: {res.text[:200]}")
                    continue

                raw_text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
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
                        "You are an expert Bengali YouTube SEO strategist. "
                        "Always respond strictly with valid JSON containing keys: title, description, tags. "
                        "Write title and description strictly in natural Bengali, following all length and hashtag rules."
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
        "You are a top Bengali YouTube SEO expert for an educational channel. Based on the video filename below, generate highly optimized YouTube metadata:\n\n"
        "1. title:\n"
        "   - Write a catchy, search-optimized, high-CTR educational title in natural Bengali with the primary keyword placed near the beginning.\n"
        "   - MUST include 2 to 3 short, relevant hashtags right at the end of the title (e.g., #shorts #learn #bangla or #shorts #learn + 1 short topic hashtag).\n"
        "   - Keep the ENTIRE title (Bengali text + hashtags combined) strictly under 95 characters so nothing gets cut off.\n\n"
        "2. description:\n"
        "   - Write a deeply SEO-friendly, well-structured YouTube description strictly in natural Bengali using this clean format:\n"
        "     * Opening Hook (2-3 lines): Naturally include the main Bengali search keywords and clearly explain what viewers will gain.\n"
        "     * Bullet Section ('এই ভিডিওতে যা জানবেন:'): 3 to 4 crisp bullet points (using ✅ or •) covering key takeaways with secondary search keywords.\n"
        "     * Engagement & CTA: A short friendly Bengali line encouraging viewers to Like, Comment their question, Share, and Subscribe for more educational videos.\n"
        "     * Hashtags at the bottom: 6 to 8 relevant hashtags on the last line (including #shorts #learn #bangla and topic-specific hashtags).\n"
        "   - Use clean line breaks (\\n) between sections for readability. Avoid spammy raw keyword lists.\n\n"
        "3. tags:\n"
        "   - A JSON list of 15 to 18 high-search-volume YouTube tags (without # symbol).\n"
        "   - Mix natural Bengali search terms, popular Banglish search queries (used by Bangladeshi viewers), and a few relevant English educational keywords.\n\n"
        "Strict Language Rule: Regardless of whether the video filename is in English, Bangla, or Banglish, ALWAYS write the title text and description strictly in natural, fluent Bengali.\n"
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
            "privacyStatus": "public"  # সরাসরি পাবলিক হিসেবে আপলোড হবে
        }
    }
    media = MediaFileUpload(video_path, chunksize=5 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploading to YouTube: {int(status.progress() * 100)}%")
    print(f"Published! Video ID: {response.get('id')}")

def main():
    drive, youtube = get_google_services()
    uploaded_ids = load_uploaded_ids()

    query = f"'{FOLDER_ID}' in parents and mimeType contains 'video/' and trashed = false"
    results = drive.files().list(q=query, fields="files(id, name)", orderBy="createdTime asc").execute()
    files = results.get("files", [])

    new_files = [f for f in files if f["id"] not in uploaded_ids]
    if not new_files:
        print("No new videos found.")
        return

    # প্রতি রানে মাত্র ১টি ভিডিও আপলোড করবে
    for video in new_files[:1]:
        file_id = video["id"]
        file_name = video["name"]
        temp_path = "/tmp/temp_video.mp4"

        print(f"\nProcessing: {file_name}")
        metadata = generate_seo_metadata(file_name)

        if metadata is None:
            print("Both Gemini and Groq APIs failed. Skipping upload for this run so it retries cleanly next time.")
            return

        title, description, tags = metadata
        print(f"Generated Title: {title}")

        download_video(drive, file_id, temp_path)
        upload_to_youtube(youtube, temp_path, title, description, tags)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        uploaded_ids.append(file_id)
        save_uploaded_ids(uploaded_ids)

if __name__ == "__main__":
    main()
