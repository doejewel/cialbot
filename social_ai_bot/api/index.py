"""
api/index.py
Flask backend for the Social AI Bot demo
Vercel-compatible entry point
"""

import os
import time
import json
import requests
import tweepy
from datetime import datetime
from urllib.parse import quote
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google import genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="../", static_url_path="")
CORS(app)

GEMINI_API_KEY              = os.environ.get("GEMINI_API_KEY", "")
INSTAGRAM_ACCESS_TOKEN      = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID        = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
FACEBOOK_APP_ID             = os.environ.get("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET         = os.environ.get("FACEBOOK_APP_SECRET", "")
TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")
LINKEDIN_ACCESS_TOKEN        = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")


# ── Caption generation ────────────────────────────────────────────────────────

def generate_caption(topic, platform, brand_voice="professional yet approachable"):
    client = genai.Client(api_key=GEMINI_API_KEY)

    platform_rules = {
        "instagram": "Up to 1500 characters. Use emojis. End with a call-to-action. 5-12 hashtags.",
        "twitter":   "Max 280 characters total including hashtags. Be punchy and engaging. 2-3 hashtags only.",
        "linkedin":  "Professional tone. 1000 characters max. No emojis. 3-5 industry hashtags.",
    }
    rules = platform_rules.get(platform, platform_rules["instagram"])

    prompt = f"""
You are a world-class social media copywriter with a {brand_voice} brand voice.

Topic: {topic}
Platform: {platform.upper()}
Platform rules: {rules}

Respond ONLY with a valid JSON object — no markdown fences, no preamble:
{{
  "caption": "<full post caption>",
  "hashtags": ["#tag1", "#tag2"],
  "alt_text": "<one-sentence image description>",
  "image_prompt": "<detailed image generation prompt that visually represents this post>"
}}
""".strip()

    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("```").strip()

    return json.loads(raw)


# ── Image URL ─────────────────────────────────────────────────────────────────

def get_image_url(image_prompt):
    """Get a working public image URL for the given prompt."""
    encoded = quote(image_prompt)

    # Try Pollinations variants
    candidates = [
        f"https://image.pollinations.ai/prompt/{encoded}?width=512&height=512&model=flux&seed=42",
        f"https://image.pollinations.ai/prompt/{encoded}?width=512&height=512&seed=42",
    ]

    for url in candidates:
        try:
            res = requests.get(url, timeout=60)
            ct = res.headers.get("content-type", "")
            if res.status_code == 200 and "image" in ct:
                return url
        except Exception:
            pass

    # Reliable fallback — Picsum serves real JPEGs at stable URLs
    seed = abs(hash(image_prompt)) % 1000
    return f"https://picsum.photos/seed/{seed}/1024/1024"


# ── Instagram posting ─────────────────────────────────────────────────────────

def post_to_instagram(image_url, caption, hashtags, access_token, account_id):
    full_caption = f"{caption}\n\n{' '.join(hashtags)}"
    GRAPH = "https://graph.facebook.com/v19.0"

    # Step 1 — create media container
    res = requests.post(f"{GRAPH}/{account_id}/media", json={
        "image_url": image_url,
        "caption": full_caption,
        "access_token": access_token,
    })

    raw = res.text
    try:
        data = res.json()
    except Exception:
        raise Exception(f"Meta raw error: {raw[:300]}")

    if not res.ok or "error" in data:
        raise Exception(data.get("error", {}).get("message", f"Container failed: {raw[:200]}"))

    container_id = data["id"]

    # Step 2 — wait for container to be ready
    time.sleep(10)

    # Step 3 — publish
    pub = requests.post(f"{GRAPH}/{account_id}/media_publish", json={
        "creation_id": container_id,
        "access_token": access_token,
    })

    pub_raw = pub.text
    try:
        pub_data = pub.json()
    except Exception:
        raise Exception(f"Meta publish raw error: {pub_raw[:300]}")

    if not pub.ok or "error" in pub_data:
        raise Exception(pub_data.get("error", {}).get("message", f"Publish failed: {pub_raw[:200]}"))

    return pub_data["id"]


# ── X (Twitter) posting ───────────────────────────────────────────────────────

def post_to_twitter(caption, hashtags):
    full_tweet = f"{caption} {' '.join(hashtags)}"
    if len(full_tweet) > 280:
        full_tweet = full_tweet[:277] + "..."

    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )
    response = client.create_tweet(text=full_tweet)
    return response.data["id"]


# ── LinkedIn posting ─────────────────────────────────────────────────────────────

def get_linkedin_person_urn(access_token):
    res = requests.get("https://api.linkedin.com/v2/userinfo", headers={
        "Authorization": f"Bearer {access_token}"
    })
    data = res.json()
    sub = data.get("sub")
    if not sub:
        raise Exception(f"Could not get LinkedIn person URN: {data}")
    return f"urn:li:person:{sub}"


def post_to_linkedin(caption, hashtags, access_token):
    person_urn = get_linkedin_person_urn(access_token)
    full_text = f"{caption}\n\n{' '.join(hashtags)}"[:3000]

    res = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "author": person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": full_text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        },
    )

    raw = res.text
    try:
        data = res.json()
    except Exception:
        raise Exception(f"LinkedIn raw error: {raw[:300]}")

    if not res.ok or "serviceErrorCode" in data:
        raise Exception(data.get("message", f"LinkedIn post failed: {raw[:200]}"))

    return data.get("id", "posted")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("../../", "index.html")


@app.route("/callback")
def linkedin_callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"<h2>LinkedIn Error</h2><p>{error}</p>", 400
    if not code:
        return "<h2>No code received</h2>", 400

    LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
    LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
    REDIRECT_URI = request.base_url

    res = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})

    data = res.json()
    if "access_token" in data:
        token = data["access_token"]
        expires = data.get("expires_in", 0)
        days = round(expires / 86400)
        return f"""
        <html><body style="font-family:monospace;padding:40px;background:#000;color:#00ff41">
        <h2>✅ LinkedIn Token Generated!</h2>
        <p>Expires in: {days} days</p>
        <p>Copy this token into your .env as <strong>LINKEDIN_ACCESS_TOKEN</strong>:</p>
        <textarea style="width:100%;height:120px;background:#0a0a0a;color:#00ff41;border:1px solid #00ff41;padding:10px;font-family:monospace">{token}</textarea>
        <br><br>
        <p>Then add it to Vercel environment variables too.</p>
        </body></html>
        """
    else:
        return f"<h2>Error</h2><pre>{data}</pre>", 400


@app.route("/api/exchange-token")
def exchange_token():
    short_token = request.args.get("token")
    if not short_token:
        return jsonify({"error": "Pass ?token=YOUR_SHORT_TOKEN in the URL"}), 400
    if not FACEBOOK_APP_ID or not FACEBOOK_APP_SECRET:
        return jsonify({"error": "FACEBOOK_APP_ID or FACEBOOK_APP_SECRET not set in .env"}), 500

    res = requests.get("https://graph.facebook.com/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": FACEBOOK_APP_ID,
        "client_secret": FACEBOOK_APP_SECRET,
        "fb_exchange_token": short_token,
    })
    data = res.json()
    if "error" in data:
        return jsonify({"error": data["error"]}), 400

    return jsonify({
        "long_lived_token": data.get("access_token"),
        "expires_in_days": round(data.get("expires_in", 0) / 86400),
        "instructions": "Copy long_lived_token into your .env as INSTAGRAM_ACCESS_TOKEN, then restart Flask.",
    })


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json()
    topic       = data.get("topic", "").strip()
    platforms   = data.get("platforms", ["instagram", "twitter", "linkedin"])
    brand_voice = data.get("brand_voice", "professional yet approachable")
    post_to_ig  = data.get("post_to_instagram", False)
    post_to_tw  = data.get("post_to_twitter", False)

    if not topic:
        return jsonify({"error": "Topic is required"}), 400
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500

    try:
        posts = {}
        image_prompt = None

        for platform in platforms:
            result = generate_caption(topic, platform, brand_voice)
            posts[platform] = result
            if image_prompt is None:
                image_prompt = result["image_prompt"]

        # Get image URL — waits for Pollinations to be ready
        image_url = get_image_url(image_prompt)

        for platform in posts:
            posts[platform]["image_url"] = image_url

        # Instagram auto-post
        instagram_result = {"status": "not_requested"}
        if post_to_ig and "instagram" in posts:
            if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_ACCOUNT_ID:
                instagram_result = {"status": "error", "error": "Instagram credentials not set"}
            else:
                try:
                    media_id = post_to_instagram(
                        image_url,
                        posts["instagram"]["caption"],
                        posts["instagram"]["hashtags"],
                        INSTAGRAM_ACCESS_TOKEN,
                        INSTAGRAM_ACCOUNT_ID,
                    )
                    instagram_result = {"status": "posted", "media_id": media_id}
                except Exception as e:
                    instagram_result = {"status": "error", "error": str(e)}

        # Twitter auto-post
        twitter_result = {"status": "not_requested"}
        if post_to_tw and "twitter" in posts:
            if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
                twitter_result = {"status": "error", "error": "Twitter credentials not set"}
            else:
                try:
                    tweet_id = post_to_twitter(
                        posts["twitter"]["caption"],
                        posts["twitter"]["hashtags"],
                    )
                    twitter_result = {"status": "posted", "tweet_id": tweet_id}
                except Exception as e:
                    twitter_result = {"status": "error", "error": str(e)}

        return jsonify({
            "success": True,
            "topic": topic,
            "posts": posts,
            "image_url": image_url,
            "instagram": instagram_result,
            "twitter": twitter_result,
            "linkedin": linkedin_result,
            "timestamp": datetime.now().isoformat(),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "gemini": bool(GEMINI_API_KEY),
        "instagram": bool(INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID),
        "twitter": bool(TWITTER_API_KEY and TWITTER_ACCESS_TOKEN),
        "facebook_app": bool(FACEBOOK_APP_ID and FACEBOOK_APP_SECRET),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
