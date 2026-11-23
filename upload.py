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

def parse_metadata_json(raw_text, fallback_title):
    cleaned = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip(), flags=re.IGNORECASE)
    data = json.loads(cleaned)

    title = str(data.get("title", fallback_title)).strip()[:100]
    description = str(data.get("description", fallback_title)).strip()
    tags = data.get("tags", ["শিক্ষামূলক", "বাংলা টিউটোরিয়াল", "education"])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
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
            wait_seconds = attempt * 10  # ১ম বারের পর ১০ সেকেন্ড, ২য় বারের পর ২০ সেকেন্ড অপেক্ষা
            print(f"Gemini busy. Waiting {wait_seconds} seconds before retry...")
            time.sleep(wait_seconds)

    return None

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

    groq_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

    for model in groq_models:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a Bengali YouTube SEO expert. Always respond strictly with valid JSON containing keys: title, description, tags. Write title and description in natural Bengali."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=30)
            if res.status_code != 200:
                print(f"[Groq | {model}] API Error {res.status_code}: {res.text[:200]}")
                continue

            raw_text = res.json()["choices"][0]["message"]["content"]
            result = parse_metadata_json(raw_text, fallback_title)
            print(f"Metadata generated successfully using Groq ({model}).")
            return result
        except Exception as e:
            print(f"[Groq | {model}] Exception: {e}")

    return None

def generate_seo_metadata(filename):
    fallback_title = os.path.splitext(filename)[0].strip()
    prompt = (
        "You are a Bengali YouTube SEO expert for an educational channel. Based on the video filename below, generate:\n"
        "1. title: A catchy, high-CTR educational YouTube title in natural Bengali (max 90 characters).\n"
        "2. description: An engaging SEO-friendly YouTube description in natural Bengali (2-3 short paragraphs explaining the topic + call-to-action in Bengali to like, comment & subscribe + 4-5 relevant hashtags at the bottom).\n"
        "3. tags: A list of 12-15 high-search-volume YouTube tags in Bengali (you may include a few commonly searched educational terms).\n"
        "Strict Language Rule: Regardless of whether the video filename is in English, Bangla, or Banglish, ALWAYS write the title and description strictly in natural Bengali.\n"
        "Return ONLY valid JSON with exact keys: title, description, tags.\n\n"
        f"Video Filename: {filename}"
    )

    # ১. প্রথমে Gemini API দিয়ে ৩ বার চেষ্টা করবে
    result = generate_with_gemini(prompt, fallback_title)
    if result is not None:
        return result

    # ২. Gemini ৩ বার ফেইল করলে Groq API ব্যবহার করবে
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
