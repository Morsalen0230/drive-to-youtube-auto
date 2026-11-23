import os
import io
import json
import re
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

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

    endpoints = [
        (
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}
            }
        ),
        (
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}
            }
        )
    ]

    last_error = None
    for url, payload in endpoints:
        try:
            res = requests.post(url, json=payload, timeout=30)
            res.raise_for_status()
            raw_text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip(), flags=re.IGNORECASE)
            data = json.loads(raw_text)

            title = str(data.get("title", fallback_title)).strip()[:100]
            description = str(data.get("description", fallback_title)).strip()
            tags = data.get("tags", ["শিক্ষামূলক", "বাংলা টিউটোরিয়াল", "education"])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            return title, description, tags
        except Exception as e:
            last_error = e

    print(f"AI generation fallback triggered: {last_error}")
    return (
        fallback_title[:100],
        f"{fallback_title}\n\nনতুন শিক্ষামূলক ভিডিও পেতে আমাদের চ্যানেলটি সাবস্ক্রাইব করে রাখুন!",
        ["শিক্ষামূলক", "বাংলা ভিডিও", "education"]
    )

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
            "privacyStatus": "private"  # টেস্ট সফল হলে এটিকে "public" করে দেবেন
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
        title, description, tags = generate_seo_metadata(file_name)
        print(f"Generated Title: {title}")

        download_video(drive, file_id, temp_path)
        upload_to_youtube(youtube, temp_path, title, description, tags)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        uploaded_ids.append(file_id)
        save_uploaded_ids(uploaded_ids)

if __name__ == "__main__":
    main()
